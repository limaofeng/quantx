from datetime import date, datetime
from types import SimpleNamespace

import pytest
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

  assert _effective_roe_quality(
    metric,
    quality,
    success,
    date(2026, 5, 2),
  )[0] == "VALID"
  assert _effective_roe_quality(
    metric,
    quality,
    None,
    date(2026, 5, 2),
  )[0] == "UNVERIFIED"
  assert _effective_roe_quality(
    metric,
    quality,
    SimpleNamespace(status="FAILED"),
    date(2026, 5, 2),
  )[0] == "UNVERIFIED"
  assert _effective_roe_quality(
    metric,
    quality,
    SimpleNamespace(status="EMPTY"),
    date(2026, 5, 2),
  )[0] == "INVALID"


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

  async def execute(self, statement):
    self.statements.append(statement)

  async def commit(self):
    self.commits += 1


class _ScreenResult:
  def scalar_one(self):
    return 0

  def all(self):
    return []


class _ScreenSession:
  def __init__(self) -> None:
    self.statements = []

  async def execute(self, statement):
    self.statements.append(statement)
    return _ScreenResult()


@pytest.mark.asyncio
async def test_snapshot_bulk_upsert_uses_bounded_multi_row_statements() -> None:
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

  saved = await repo.bulk_upsert(records)

  assert saved == count
  assert len(session.statements) == 3
  assert session.commits == 1
  assert all("ON CONFLICT" in str(statement) for statement in session.statements)


@pytest.mark.asyncio
async def test_roe_filter_sort_and_count_share_strict_quality_joins() -> None:
  session = _ScreenSession()
  repo = IndicatorSnapshotRepository(session)

  rows, total = await repo.screen_snapshots(
    snapshot_date=date(2026, 5, 20),
    min_roe=5.0,
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
