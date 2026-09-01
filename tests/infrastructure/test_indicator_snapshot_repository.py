from datetime import date, datetime
from types import SimpleNamespace

import pytest
import quantx_infrastructure.repositories.indicator_snapshot_repository as snapshot_repository_module
from quantx_infrastructure.core.financial_quality import (
  minimum_required_financial_report_date,
)
from quantx_infrastructure.models.enums import InstrumentType
from quantx_infrastructure.repositories.indicator_snapshot_repository import (
  MAX_BULK_UPSERT_RECORDS,
  IndicatorSnapshotRepository,
  _effective_roe_quality,
  _normalize_instrument_type,
)
from quantx_infrastructure.services.snapshot_fencing import SnapshotFenceLost
from sqlalchemy.dialects import postgresql


def test_instrument_type_normalization_unwraps_strawberry_enum_metadata() -> None:
  assert _normalize_instrument_type(InstrumentType.STOCK) == "stock"
  assert _normalize_instrument_type(InstrumentType.ETF) == "etf"


@pytest.mark.parametrize(
  ("as_of_date", "expected"),
  [
    (date(2026, 4, 30), date(2025, 9, 30)),
    (date(2026, 5, 1), date(2026, 3, 31)),
    (date(2026, 8, 31), date(2026, 3, 31)),
    (date(2026, 9, 1), date(2026, 6, 30)),
    (date(2026, 10, 31), date(2026, 6, 30)),
    (date(2026, 11, 1), date(2026, 9, 30)),
  ],
)
def test_minimum_financial_report_date_switches_after_deadlines(
  as_of_date,
  expected,
) -> None:
  assert minimum_required_financial_report_date(as_of_date) == expected


def test_effective_roe_quality_requires_latest_per_code_sync_audit() -> None:
  metric = SimpleNamespace(
    report_date=date(2026, 3, 31),
  )
  quality = SimpleNamespace(status="VALID", flags=[])
  success = SimpleNamespace(status="SUCCESS", verified_at=datetime(2026, 5, 2))

  assert (
    _effective_roe_quality(
      metric,
      quality,
      success,
      date(2026, 5, 2),
    )[0]
    == "VALID"
  )
  assert (
    _effective_roe_quality(
      metric,
      quality,
      None,
      date(2026, 5, 2),
    )[0]
    == "UNVERIFIED"
  )
  assert (
    _effective_roe_quality(
      metric,
      quality,
      SimpleNamespace(status="FAILED"),
      date(2026, 5, 2),
    )[0]
    == "UNVERIFIED"
  )
  assert (
    _effective_roe_quality(
      metric,
      quality,
      SimpleNamespace(status="EMPTY"),
      date(2026, 5, 2),
    )[0]
    == "INVALID"
  )


def test_effective_roe_quality_marks_report_stale_after_deadline() -> None:
  metric = SimpleNamespace(
    report_date=date(2025, 12, 31),
  )
  quality = SimpleNamespace(status="VALID", flags=[])
  audit = SimpleNamespace(status="SUCCESS")

  status, flags = _effective_roe_quality(
    metric,
    quality,
    audit,
    date(2026, 5, 1),
  )

  assert status == "STALE"
  assert flags == ["financial_report_stale"]


class _FakeSession:
  def __init__(self) -> None:
    self.statements = []
    self.commits = 0
    self.rollbacks = 0

  async def execute(self, statement):
    self.statements.append(statement)

  async def commit(self):
    self.commits += 1

  async def rollback(self):
    self.rollbacks += 1


class _ScreenResult:
  def scalar_one(self):
    return 0

  def all(self):
    return []

  def scalars(self):
    return self

  def scalar_one_or_none(self):
    return None


class _ScreenSession:
  def __init__(self) -> None:
    self.statements = []

  async def execute(self, statement):
    self.statements.append(statement)
    return _ScreenResult()


@pytest.mark.asyncio
async def test_factor_scope_invalidation_is_exact_and_committed_before_recalculation(
  monkeypatch,
):
  async def assert_owner(_db, snapshot_run_ids):
    assert snapshot_run_ids == {date(2026, 5, 20): 7}

  monkeypatch.setattr(
    snapshot_repository_module,
    "assert_snapshot_run_owner",
    assert_owner,
  )
  session = _FakeSession()
  repo = IndicatorSnapshotRepository(session)
  await repo.invalidate_factor_scope(
    ["000001.SZ"],
    [date(2026, 5, 20)],
    snapshot_run_ids={date(2026, 5, 20): 7},
  )
  assert session.commits == 1
  assert len(session.statements) == 1
  sql = str(
    session.statements[0].compile(
      dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
  )
  assert "UPDATE indicator_snapshots SET" in sql
  assert "calculation_version=NULL" in sql
  assert "indicator_snapshots.code IN ('000001.SZ')" in sql
  assert "indicator_snapshots.snapshot_date IN ('2026-05-20')" in sql
  assert "indicator_snapshots.calculation_version = 'daily-v1'" in sql


@pytest.mark.parametrize(
  "codes,dates", [([], [date(2026, 5, 20)]), (["000001.SZ"], [])]
)
@pytest.mark.asyncio
async def test_factor_scope_invalidation_never_expands_empty_scope(codes, dates):
  session = _FakeSession()
  await IndicatorSnapshotRepository(session).invalidate_factor_scope(
    codes,
    dates,
    snapshot_run_ids={},
  )
  assert session.statements == []
  assert session.commits == 0


@pytest.mark.asyncio
async def test_snapshot_bulk_upsert_uses_bounded_multi_row_statements(
  monkeypatch,
) -> None:
  guard_calls = []

  async def acquire_guard(_db, **kwargs):
    guard_calls.append(("acquire", kwargs))

  async def assert_publish_owner(_db, **kwargs):
    guard_calls.append(("assert", kwargs))

  async def assert_owner(_db, snapshot_run_ids):
    assert snapshot_run_ids == {date(2026, 7, 29): 9}

  monkeypatch.setattr(
    snapshot_repository_module,
    "acquire_snapshot_publish_guard",
    acquire_guard,
  )
  monkeypatch.setattr(
    snapshot_repository_module,
    "assert_snapshot_publish_owner",
    assert_publish_owner,
  )
  monkeypatch.setattr(
    snapshot_repository_module,
    "assert_snapshot_run_owner",
    assert_owner,
  )
  session = _FakeSession()
  repo = IndicatorSnapshotRepository(session)
  count = MAX_BULK_UPSERT_RECORDS * 2 + 1
  records = [
    {
      "code": f"{index:06d}.SZ",
      "snapshot_date": date(2026, 7, 29),
      "instrument_type": "stock",
      "name": f"股票{index}",
    }
    for index in range(count)
  ]

  saved = await repo.bulk_upsert(
    records,
    snapshot_run_ids={date(2026, 7, 29): 9},
    lock_backend_pid=101,
  )

  assert saved == count
  assert len(session.statements) == 3
  assert session.commits == 1
  assert all("ON CONFLICT" in str(statement) for statement in session.statements)
  assert guard_calls == [
    (
      "acquire",
      {
        "lock_backend_pid": 101,
        "snapshot_dates": (date(2026, 7, 29),),
      },
    ),
    (
      "assert",
      {
        "lock_backend_pid": 101,
        "snapshot_dates": (date(2026, 7, 29),),
      },
    ),
  ]


@pytest.mark.asyncio
async def test_snapshot_bulk_upsert_rolls_back_before_writing_when_owner_is_lost(
  monkeypatch,
) -> None:
  async def reject_guard(_db, **_kwargs):
    raise SnapshotFenceLost("simulated owner loss")

  monkeypatch.setattr(
    snapshot_repository_module,
    "acquire_snapshot_publish_guard",
    reject_guard,
  )
  session = _FakeSession()
  repo = IndicatorSnapshotRepository(session)

  with pytest.raises(SnapshotFenceLost, match="owner loss"):
    await repo.bulk_upsert(
      [
        {
          "code": "000001.SZ",
          "snapshot_date": date(2026, 7, 29),
          "instrument_type": "stock",
          "name": "平安银行",
        }
      ],
      snapshot_run_ids={date(2026, 7, 29): 9},
      lock_backend_pid=101,
    )

  assert session.statements == []
  assert session.commits == 0
  assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_roe_filter_sort_and_count_share_strict_quality_joins() -> None:
  session = _ScreenSession()
  repo = IndicatorSnapshotRepository(session)

  rows, total = await repo.screen_factor_snapshots(
    snapshot_date=date(2026, 5, 20),
    factor_conditions=[{"factor_id": "roe_ttm", "operator": "gte", "value": 5.0}],
    sort={"field": "roe_ttm", "direction": "desc"},
    limit=20,
    offset=40,
  )

  assert rows == []
  assert total == 0
  assert len(session.statements) == 2
  count_sql, page_sql = [
    str(statement.compile(dialect=postgresql.dialect()))
    for statement in session.statements
  ]
  for sql in (count_sql, page_sql):
    assert "financial_metric_roe_qualities" in sql
    assert "financial_sync_code_audits" in sql
    assert "financial_sync_runs" in sql
    assert "roe_ttm" in sql
    assert "status" in sql
  assert page_sql.count("LIMIT") == count_sql.count("LIMIT") + 1
  assert page_sql.count("OFFSET") == count_sql.count("OFFSET") + 1
  assert "NULLS LAST" in page_sql
  assert "LIMIT" in page_sql
  assert "OFFSET" in page_sql


@pytest.mark.asyncio
async def test_factor_filters_keep_zero_and_version_boundary_and_default_order():
  session = _ScreenSession()
  await IndicatorSnapshotRepository(session).screen_factor_snapshots(
    snapshot_date=date(2026, 5, 20),
    factor_conditions=[
      {"factor_id": "consecutive_down_days", "operator": "eq", "value": 0}
    ],
  )
  compiled = session.statements[-1].compile(dialect=postgresql.dialect())
  assert "calculation_version" in str(compiled)
  assert "daily-v1" in compiled.params.values()
  assert 0 in compiled.params.values()
  order = str(compiled).split("ORDER BY")[-1]
  assert "change_pct DESC NULLS LAST, indicator_snapshots.code ASC" in order
  assert "volume_ratio DESC" not in order


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "condition",
  [
    {"factor_id": "unknown", "operator": "gte", "value": 1},
    {"factor_id": "rsi12", "operator": "gt_or_eq", "value": 1},
    {"factor_id": "rsi12", "operator": "between", "value": 70, "value_to": 30},
  ],
)
async def test_factor_repository_rejects_invalid_conditions(condition):
  session = _ScreenSession()
  with pytest.raises(ValueError):
    await IndicatorSnapshotRepository(session).screen_factor_snapshots(
      snapshot_date=date(2026, 5, 20),
      factor_conditions=[condition],
    )
  assert session.statements == []


@pytest.mark.asyncio
async def test_radar_baseline_reads_are_independent_of_factor_version_readiness():
  session = _ScreenSession()
  repo = IndicatorSnapshotRepository(session)
  await repo.get_latest_snapshot_date()
  await repo.list_baseline_snapshots(date(2026, 5, 20))
  await repo.find_snapshot_dates(date(2026, 5, 19), date(2026, 5, 20))
  for statement in session.statements:
    # The selected model includes calculation_version, but no version WHERE predicate.
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "calculation_version =" not in sql
  session.statements.clear()
  await repo.get_latest_factor_snapshot_date()
  await repo.find_factor_snapshot_dates(date(2026, 5, 19), date(2026, 5, 20))
  for statement in session.statements:
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "calculation_version =" in sql


@pytest.mark.asyncio
async def test_completed_factor_runs_exclude_scoped_success_without_changing_radar_reads():
  from quantx_infrastructure.repositories.daily_signal_run_repository import (
    DailySignalRunRepository,
  )

  session = _ScreenSession()
  repo = DailySignalRunRepository(session)
  await repo.find_latest_completed(date(2026, 5, 20))
  await repo.find_completed_dates(date(2026, 5, 19), date(2026, 5, 20))
  for statement in session.statements:
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "success" in compiled.params.values()
    assert "scoped_success" not in compiled.params.values()
    assert "daily-v1" in compiled.params.values()
