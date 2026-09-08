import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_worker.prefector.flows.daily_indicator_snapshot_flow as indicator_flow
import quantx_worker.prefector.flows.daily_market_data_sync_flow as market_flow
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  build_sector_membership_audit,
)
from sqlalchemy.dialects import postgresql

from tests.worker.market_sync_helpers import completed_transfer


class FakeLogger:
  def warning(self, *args, **kwargs):
    return None

  def info(self, *args, **kwargs):
    return None


class FakeTradingTimeService:
  async def get_previous_trading_day(self, market, from_date):
    assert market == "SH"
    return date(2026, 7, 28)


class FakeTradingDates:
  def __init__(self):
    self.trading_time_service = FakeTradingTimeService()

  async def is_trading_date(self, market, check_date):
    return check_date == date(2026, 7, 29)

  async def get_trading_calendar(self, market, start_date, end_date):
    current = start_date
    result = []
    while current <= end_date:
      if current.weekday() < 5:
        result.append(current)
      current = date.fromordinal(current.toordinal() + 1)
    return result


@pytest.fixture(autouse=True)
def market_sync_calendar(monkeypatch):
  monkeypatch.setattr(market_flow, "TradingDateHelper", FakeTradingDates)
  monkeypatch.setattr(market_flow, "MARKET_DATA_RETRY_DELAY_SECONDS", 0)


def test_daily_market_sync_retries_durable_batches() -> None:
  assert market_flow.daily_market_data_sync_flow.retries == 0
  assert market_flow.MARKET_DATA_REQUEST_CONCURRENCY == 2




@pytest.mark.asyncio
async def test_expected_snapshot_date_changes_at_1535():
  helper = FakeTradingDates()

  before = await indicator_flow.expected_snapshot_date(
    datetime(2026, 7, 29, 15, 34),
    trading_dates=helper,
  )
  after = await indicator_flow.expected_snapshot_date(
    datetime(2026, 7, 29, 15, 35),
    trading_dates=helper,
  )

  assert before == date(2026, 7, 28)
  assert after == date(2026, 7, 29)


@pytest.mark.asyncio
async def test_explicit_snapshot_range_filters_weekend():
  dates = await indicator_flow.resolve_snapshot_dates(
    "20260723",
    "20260727",
    trading_dates=FakeTradingDates(),
  )

  assert dates == [
    date(2026, 7, 23),
    date(2026, 7, 24),
    date(2026, 7, 27),
  ]


@pytest.mark.parametrize(
  ("sectors", "stock_list", "expected"),
  [
    (None, None, True),
    (["沪深ETF", "沪深A股"], None, True),
    (["沪深A股"], None, False),
    (None, ["000001.SZ"], False),
  ],
)
def test_only_exact_full_snapshot_request_certifies_readiness(
  sectors, stock_list, expected
):
  assert indicator_flow._requests_full_snapshot_scope(sectors, stock_list) is expected


def _membership_payload(target: date) -> dict:
  return {
    "operation": "sector_instruments",
    "destination": "audit_only",
    "as_of_date": target.isoformat(),
    "sectors": ["沪深A股", "沪深ETF"],
  }


def test_membership_audit_must_exactly_match_each_typed_sector_and_union():
  target = date(2026, 9, 1)
  payload = _membership_payload(target)
  audit = build_sector_membership_audit(
    [
      {"sector": "沪深A股", "code": "600000.SH"},
      {"sector": "沪深ETF", "code": "510300.SH"},
    ],
    payload,
  )
  codes_by_sector = {
    "沪深A股": {"600000.SH"},
    "沪深ETF": {"510300.SH"},
  }

  assert indicator_flow._membership_audit_matches(
    payload=payload,
    audit=audit,
    target=target,
    codes_by_sector=codes_by_sector,
  )

  mismatched = {
    **audit,
    "sector_audits": {
      **audit["sector_audits"],
      "沪深ETF": {
        **audit["sector_audits"]["沪深ETF"],
        "code_count": 2,
      },
    },
  }
  assert not indicator_flow._membership_audit_matches(
    payload=payload,
    audit=mismatched,
    target=target,
    codes_by_sector=codes_by_sector,
  )


@pytest.mark.asyncio
async def test_historical_target_never_reuses_current_sector_relation(monkeypatch):
  monkeypatch.setattr(
    indicator_flow.time_utils,
    "today",
    lambda: date(2026, 9, 1),
  )
  monkeypatch.setattr(
    indicator_flow,
    "AsyncSessionLocal",
    lambda: (_ for _ in ()).throw(AssertionError("current relation was queried")),
  )

  result = await indicator_flow._certified_default_membership_generations(
    target_dates=[date(2026, 8, 31)],
    frozen_instruments=[],
  )

  assert result == {}


@pytest.mark.asyncio
async def test_current_membership_generation_requires_same_completed_request(
  monkeypatch,
):
  target = date(2026, 9, 1)
  payload = _membership_payload(target)
  audit = build_sector_membership_audit(
    [
      {"sector": "沪深A股", "code": "600000.SH"},
      {"sector": "沪深ETF", "code": "510300.SH"},
      {"sector": "沪深ETF", "code": "560000.SH"},
    ],
    payload,
  )

  class Result:
    def __init__(self, rows):
      self.rows = rows

    def mappings(self):
      return self

    def all(self):
      return self.rows

  class Session:
    def __init__(self):
      self.results = [
        Result(
          [
            {
              "sector_code": "沪深A股",
              "instrument_code": "600000.SH",
              "instrument_type": indicator_flow.InstrumentType.STOCK,
              "secu_category": None,
            },
            {
              "sector_code": "沪深ETF",
              "instrument_code": "510300.SH",
              "instrument_type": indicator_flow.InstrumentType.ETF,
              "secu_category": None,
            },
            {
              "sector_code": "沪深ETF",
              "instrument_code": "560000.SH",
              "instrument_type": indicator_flow.InstrumentType.ETF,
              "secu_category": 10608640,
            },
          ]
        ),
        Result(
          [
            {
              "request_id": "membership-request-1",
              "request_payload": payload,
              "ingestion_result": audit,
            }
          ]
        ),
      ]

    async def execute(self, _statement):
      return self.results.pop(0)

  class SessionContext:
    async def __aenter__(self):
      return Session()

    async def __aexit__(self, *_args):
      return False

  monkeypatch.setattr(indicator_flow.time_utils, "today", lambda: target)
  monkeypatch.setattr(indicator_flow, "AsyncSessionLocal", SessionContext)

  result = await indicator_flow._certified_default_membership_generations(
    target_dates=[target],
    frozen_instruments=[
      {"code": "600000.SH", "instrument_type": "stock"},
      {"code": "510300.SH", "instrument_type": "etf"},
    ],
  )

  assert result == {target: "membership-request-1"}


@pytest.mark.asyncio
async def test_snapshot_instrument_scope_is_active_on_target_date(monkeypatch):
  class Result:
    def all(self):
      return []

  class Session:
    statement = None

    async def execute(self, statement):
      self.statement = statement
      return Result()

  class SessionContext:
    def __init__(self, session):
      self.session = session

    async def __aenter__(self):
      return self.session

    async def __aexit__(self, exc_type, exc, traceback):
      return False

  session = Session()
  monkeypatch.setattr(
    indicator_flow,
    "AsyncSessionLocal",
    lambda: SessionContext(session),
  )

  await indicator_flow.resolve_instruments(
    indicator_flow.DEFAULT_SNAPSHOT_SECTORS,
    None,
    allowed_types={
      indicator_flow.InstrumentType.STOCK,
      indicator_flow.InstrumentType.ETF,
    },
    active_on=date(2026, 7, 29),
  )

  sql = " ".join(
    str(
      session.statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
      )
    ).split()
  )
  assert (
    "(instruments.open_date IS NULL OR instruments.open_date <= '2026-07-29')" in sql
  )
  assert (
    "(instruments.expire_date IS NULL OR instruments.expire_date >= '2026-07-29')"
    in sql
  )
  assert "SELECT DISTINCT" in sql
  assert "JOIN sector_stocks ON sector_stocks.stock_code = instruments.code" in sql
  assert "JOIN sectors ON sector_stocks.sector_id = sectors.id" in sql
  assert "sectors.code = '沪深A股'" in sql
  assert "sectors.code = '沪深ETF'" in sql
  assert "sectors.classification = 'MKT'" in sql
  assert "instruments.instrument_type = 'STOCK'" in sql
  assert "instruments.instrument_type = 'ETF'" in sql
  assert "instruments.secu_category NOT IN (10608640, 10608720, 10625024)" in sql


@pytest.mark.asyncio
async def test_custom_sector_uses_authoritative_relation_and_requested_types(
  monkeypatch,
):
  class Result:
    def all(self):
      return []

  class Session:
    statement = None

    async def execute(self, statement):
      self.statement = statement
      return Result()

  class SessionContext:
    def __init__(self, session):
      self.session = session

    async def __aenter__(self):
      return self.session

    async def __aexit__(self, exc_type, exc, traceback):
      return False

  session = Session()
  monkeypatch.setattr(
    indicator_flow,
    "AsyncSessionLocal",
    lambda: SessionContext(session),
  )

  await indicator_flow.resolve_instruments(
    ["银行", "TGN-001"],
    None,
    allowed_types={indicator_flow.InstrumentType.STOCK},
  )

  sql = " ".join(
    str(
      session.statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
      )
    ).split()
  )
  assert "JOIN sector_stocks ON sector_stocks.stock_code = instruments.code" in sql
  assert "JOIN sectors ON sector_stocks.sector_id = sectors.id" in sql
  assert "sectors.name = '银行' OR sectors.code = '银行'" in sql
  assert "sectors.name = 'TGN-001' OR sectors.code = 'TGN-001'" in sql
  assert "instruments.instrument_type IN ('STOCK')" in sql


@pytest.mark.parametrize(
  ("sectors", "stock_list"),
  [
    (None, ["000001.SZ", "000002.SZ"]),
    (["银行"], None),
  ],
)
@pytest.mark.asyncio
async def test_scoped_flow_batches_complete_scope_but_calculates_only_active_codes(
  monkeypatch, sectors, stock_list
):
  target = date(2026, 7, 29)
  complete = [
    {
      "code": "000001.SZ",
      "name": "活动标的",
      "instrument_type": "stock",
      "float_volume": None,
      "open_date": date(2020, 1, 1),
      "expire_date": None,
    },
    {
      "code": "000002.SZ",
      "name": "已退市标的",
      "instrument_type": "stock",
      "float_volume": None,
      "open_date": date(2020, 1, 1),
      "expire_date": date(2026, 7, 28),
    },
  ]
  resolve_calls = []

  async def resolve(scope_sectors, scope_stock_list, **kwargs):
    resolve_calls.append((scope_sectors, scope_stock_list, kwargs))
    return complete

  batch_calls = []
  cleanup_calls = []
  run_cleanup_calls = []

  class SnapshotService:
    async def compute_and_save_dates_batch(self, **kwargs):
      batch_calls.append(kwargs)
      active_count = len(kwargs["codes_by_snapshot_date"][target])
      day = {
        "total": active_count,
        "saved": active_count,
        "skipped": 0,
        "failed": 0,
        "missing_target": 0,
        "inactive_target": 0,
        "insufficient_history": 0,
        "errors": [],
      }
      return {
        "total": active_count,
        "saved": active_count,
        "skipped": 0,
        "failed": 0,
        "errors": [],
        "systemic_failure": False,
        "dates": {target.isoformat(): day},
      }

    async def cleanup_old_snapshots(self, retain_days):
      cleanup_calls.append(retain_days)
      raise AssertionError("快照计算 Flow 不应执行全局快照保留清理")

  class SessionContext:
    async def __aenter__(self):
      return object()

    async def __aexit__(self, exc_type, exc, traceback):
      return False

  class SignalRunRepository:
    def __init__(self, db):
      pass

    async def delete_older_than(self, cutoff):
      run_cleanup_calls.append(cutoff)
      raise AssertionError("快照计算 Flow 不应执行全局运行记录保留清理")

  monkeypatch.setattr(indicator_flow, "get_run_logger", FakeLogger)
  monkeypatch.setattr(
    indicator_flow, "resolve_snapshot_dates", AsyncMock(return_value=[target])
  )
  monkeypatch.setattr(indicator_flow, "resolve_instruments", resolve)
  monkeypatch.setattr(
    indicator_flow, "_acquire_snapshot_locks", AsyncMock(return_value={})
  )
  monkeypatch.setattr(indicator_flow, "_snapshot_lock_backend_pid", lambda _locks: 101)
  monkeypatch.setattr(indicator_flow, "_release_snapshot_locks", AsyncMock())
  monkeypatch.setattr(
    indicator_flow, "_create_signal_runs", AsyncMock(return_value={target: 1})
  )
  invalidate_generation = AsyncMock()
  monkeypatch.setattr(
    indicator_flow,
    "_invalidate_snapshot_generation",
    invalidate_generation,
  )
  monkeypatch.setattr(indicator_flow, "_finish_signal_run", AsyncMock())
  monkeypatch.setattr(indicator_flow, "DailyIndicatorSnapshotService", SnapshotService)
  monkeypatch.setattr(indicator_flow, "AsyncSessionLocal", SessionContext)
  monkeypatch.setattr(indicator_flow, "DailySignalRunRepository", SignalRunRepository)

  result = await indicator_flow.daily_indicator_snapshot_flow.fn(
    sectors=sectors,
    stock_list=stock_list,
    start_time="20260729",
    end_time="20260729",
    batch_size=1,
  )

  expected_sectors = sectors or indicator_flow.DEFAULT_SNAPSHOT_SECTORS
  assert resolve_calls[0][0:2] == (expected_sectors, stock_list)
  assert resolve_calls[0][2].get("active_on") is None
  assert len(resolve_calls) == 1
  assert [call["codes"] for call in batch_calls] == [
    ["000001.SZ"],
    ["000002.SZ"],
  ]
  assert batch_calls[0]["codes_by_snapshot_date"] == {target: ["000001.SZ"]}
  assert batch_calls[1]["codes_by_snapshot_date"] == {target: []}
  assert all(call["snapshot_run_ids"] == {target: 1} for call in batch_calls)
  assert all(call["lock_backend_pid"] == 101 for call in batch_calls)
  invalidate_generation.assert_awaited_once_with(
    run_ids={target: 1},
    target_codes={target: ["000001.SZ"]},
    replace_entire_date=False,
    locks={},
  )
  assert result["dates"][0]["total_codes"] == 1
  assert result["dates"][0]["saved"] == 1
  assert result["deleted_old_snapshots"] == 0
  assert result["deleted_old_runs"] == 0
  assert cleanup_calls == []
  assert run_cleanup_calls == []


@pytest.mark.parametrize(
  ("saved", "failed", "full_scope", "expected"),
  [
    (0, 0, True, "failed"),
    (1, 1, True, "partial_failure"),
    (1, 0, False, "scoped_success"),
    (1, 0, True, "success"),
  ],
)
def test_partial_universe_success_is_not_global_snapshot_success(
  saved, failed, full_scope, expected
):
  assert (
    indicator_flow._run_status(saved, failed, full_scope=full_scope, missing_target=0)
    == expected
  )


@pytest.mark.parametrize("full_scope", [True, False])
def test_unknown_missing_target_cannot_certify_complete_snapshot(full_scope):
  assert (
    indicator_flow._run_status(10, 0, full_scope=full_scope, missing_target=1)
    == "partial_failure"
  )


def test_inactive_only_invalidation_error_cannot_certify_complete_snapshot():
  assert (
    indicator_flow._run_status(
      10,
      0,
      full_scope=True,
      missing_target=0,
      has_errors=True,
    )
    == "partial_failure"
  )


def test_result_counter_mismatch_cannot_be_silently_accepted():
  result = {"saved": 1, "skipped": 0, "failed": 0}

  assert indicator_flow._result_conservation_error(result, 2) == (
    "快照结果计数不守恒: total=2 processed=1"
  )
  assert indicator_flow._result_conservation_error(result, 1) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_entire_date", [True, False])
async def test_generation_invalidation_precedes_batches_and_is_scope_exact(
  monkeypatch,
  replace_entire_date,
):
  target = date(2026, 9, 1)

  class Session:
    def __init__(self):
      self.statement = None
      self.committed = False

    async def execute(self, statement):
      self.statement = statement

    async def commit(self):
      self.committed = True

    async def rollback(self):
      raise AssertionError("successful invalidation must not roll back")

  session = Session()

  class SessionContext:
    async def __aenter__(self):
      return session

    async def __aexit__(self, *_args):
      return False

  monkeypatch.setattr(indicator_flow, "AsyncSessionLocal", SessionContext)
  monkeypatch.setattr(
    indicator_flow,
    "_snapshot_lock_backend_pid",
    lambda _locks: 701,
  )
  monkeypatch.setattr(indicator_flow, "_assert_snapshot_locks", AsyncMock())
  publish_guard = AsyncMock()
  run_owner = AsyncMock()
  publish_owner = AsyncMock()
  monkeypatch.setattr(
    indicator_flow,
    "acquire_snapshot_publish_guard",
    publish_guard,
  )
  monkeypatch.setattr(indicator_flow, "assert_snapshot_run_owner", run_owner)
  monkeypatch.setattr(
    indicator_flow,
    "assert_snapshot_publish_owner",
    publish_owner,
  )

  await indicator_flow._invalidate_snapshot_generation(
    run_ids={target: 91},
    target_codes={target: ["600000.SH"]},
    replace_entire_date=replace_entire_date,
    locks=object(),
  )

  sql = " ".join(
    str(
      session.statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
      )
    ).split()
  )
  assert "indicator_snapshots.snapshot_date = '2026-09-01'" in sql
  if replace_entire_date:
    assert "indicator_snapshots.code IN" not in sql
  else:
    assert "indicator_snapshots.code IN ('600000.SH')" in sql
  assert "calculation_version=NULL" in sql
  assert session.committed
  publish_guard.assert_awaited_once_with(
    session,
    lock_backend_pid=701,
    snapshot_dates=[target],
  )
  run_owner.assert_awaited_once_with(session, {target: 91})
  publish_owner.assert_awaited_once_with(
    session,
    lock_backend_pid=701,
    snapshot_dates=[target],
  )


@pytest.mark.asyncio
async def test_snapshot_lock_rejects_a_reconnected_database_session():
  class Result:
    def one(self):
      return (202, 1, 1)

  class Connection:
    closed = False

    def __init__(self):
      self.commit_count = 0
      self.close_called = False

    async def execute(self, statement, parameters):
      del statement, parameters
      return Result()

    async def commit(self):
      self.commit_count += 1

    async def close(self):
      self.close_called = True
      self.closed = True

  locks = indicator_flow.SnapshotDatabaseLocks([date(2026, 7, 29)])
  connection = Connection()
  locks.connection = connection
  locks.backend_pid = 101

  with pytest.raises(indicator_flow.SnapshotLockLost, match="所有权已丢失"):
    await locks.assert_held()
  assert connection.commit_count == 1
  assert connection.close_called is True
  assert locks.connection is None


@pytest.mark.asyncio
async def test_snapshot_locks_commit_without_releasing_session_locks(monkeypatch):
  snapshot_dates = [date(2026, 7, 28), date(2026, 7, 29)]

  class Result:
    def __init__(self, connection):
      self.connection = connection

    def one(self):
      return (
        7301,
        len(self.connection.date_locks),
        int(self.connection.factor_lock),
      )

  class Connection:
    closed = False
    invalidated = False

    def __init__(self):
      self.date_locks = set()
      self.factor_lock = False
      self.transaction_active = False
      self.commit_count = 0
      self.close_called = False

    async def scalar(self, statement, parameters=None):
      self.transaction_active = True
      sql = str(statement)
      if "pg_try_advisory_lock" in sql:
        self.date_locks.add(parameters["date_key"])
        return True
      if "pg_advisory_lock_shared" in sql:
        self.factor_lock = True
        return None
      if "pg_advisory_unlock_all" in sql:
        self.date_locks.clear()
        self.factor_lock = False
        return None
      if "pg_backend_pid" in sql:
        return 7301
      raise AssertionError(sql)

    async def execute(self, statement, parameters):
      del statement, parameters
      self.transaction_active = True
      return Result(self)

    async def commit(self):
      assert self.transaction_active is True
      self.transaction_active = False
      self.commit_count += 1

    async def rollback(self):
      self.transaction_active = False

    async def invalidate(self):
      self.invalidated = True
      self.date_locks.clear()
      self.factor_lock = False

    async def close(self):
      self.closed = True
      self.close_called = True
      self.date_locks.clear()
      self.factor_lock = False

  connection = Connection()

  async def connect():
    return connection

  monkeypatch.setattr(
    indicator_flow,
    "relational_engine",
    SimpleNamespace(connect=connect),
  )
  locks = indicator_flow.SnapshotDatabaseLocks(snapshot_dates)

  await locks.acquire()
  assert connection.commit_count == 5
  assert len(connection.date_locks) == 2
  assert connection.factor_lock is True
  assert connection.transaction_active is False

  await locks.assert_held()
  assert connection.commit_count == 6
  assert len(connection.date_locks) == 2
  assert connection.factor_lock is True

  await locks.release()
  assert connection.commit_count == 7
  assert connection.date_locks == set()
  assert connection.factor_lock is False
  assert connection.close_called is True


@pytest.mark.asyncio
async def test_snapshot_lock_query_failure_rolls_back_and_invalidates():
  class Connection:
    closed = False
    invalidated = False

    def __init__(self):
      self.rollback_count = 0
      self.invalidate_count = 0
      self.close_called = False

    async def execute(self, statement, parameters):
      del statement, parameters
      raise indicator_flow.SQLAlchemyError("query failed")

    async def rollback(self):
      self.rollback_count += 1

    async def invalidate(self):
      self.invalidate_count += 1
      self.invalidated = True

    async def close(self):
      self.close_called = True
      self.closed = True

  connection = Connection()
  locks = indicator_flow.SnapshotDatabaseLocks([date(2026, 7, 29)])
  locks.connection = connection
  locks.backend_pid = 8301

  with pytest.raises(indicator_flow.SnapshotLockLost, match="连接已失效"):
    await locks.assert_held()

  assert connection.rollback_count == 1
  assert connection.invalidate_count == 1
  assert connection.close_called is True
  assert locks.connection is None


@pytest.mark.asyncio
async def test_snapshot_lock_cleanup_finishes_and_preserves_cancellation(monkeypatch):
  started = asyncio.Event()
  allow_finish = asyncio.Event()
  finished = asyncio.Event()

  async def release(_locks):
    started.set()
    await allow_finish.wait()
    finished.set()

  monkeypatch.setattr(indicator_flow, "_release_snapshot_locks", release)
  task = asyncio.create_task(indicator_flow._release_snapshot_locks_safely(object()))
  await started.wait()
  task.cancel()
  await asyncio.sleep(0)
  allow_finish.set()

  with pytest.raises(asyncio.CancelledError):
    await task
  assert finished.is_set()


def test_batch_errors_are_attributed_only_to_their_snapshot_date():
  first = date(2026, 7, 28)
  second = date(2026, 7, 29)
  aggregate = {
    "saved": 0,
    "skipped": 0,
    "failed": 0,
    "missing_target": 0,
    "inactive_target": 0,
    "insufficient_history": 0,
    "errors": [],
  }
  batch_result = {
    "errors": ["000002.SZ 2026-07-29 指标计算失败"],
    "dates": {
      first.isoformat(): {
        "saved": 1,
        "skipped": 0,
        "failed": 0,
        "missing_target": 0,
        "inactive_target": 0,
        "insufficient_history": 0,
        "errors": [],
      },
      second.isoformat(): {
        "saved": 0,
        "skipped": 0,
        "failed": 1,
        "missing_target": 0,
        "inactive_target": 0,
        "insufficient_history": 0,
        "errors": ["000002.SZ 2026-07-29 指标计算失败"],
      },
    },
  }

  indicator_flow._merge_batch_date_result(aggregate, batch_result, first)

  assert aggregate["saved"] == 1
  assert aggregate["failed"] == 0
  assert aggregate["errors"] == []


@pytest.mark.asyncio
async def test_market_sync_resolves_sectors_and_uses_durable_transfer(
  monkeypatch,
):
  resolve = AsyncMock(
    return_value=[
      {
        "code": "600000.SH",
        "name": "浦发银行",
        "instrument_type": "stock",
        "float_volume": None,
      }
    ]
  )
  request = AsyncMock(
    side_effect=lambda payload, **kwargs: completed_transfer(payload, "request-1")
  )
  monkeypatch.setattr(
    market_flow,
    "resolve_instruments",
    resolve,
  )
  monkeypatch.setattr(
    market_flow,
    "_request_and_wait",
    request,
  )

  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)
  result = await market_flow.daily_market_data_sync_flow.fn(
    sectors=["沪深A股"],
    start_time="20260729",
    end_time="20260729",
    periods=["1d"],
  )

  assert result["status"] == "success"
  assert resolve.await_args.args[0] == ["沪深A股"]
  assert request.await_args.args[0]["stock_list"] == ["600000.SH"]
  assert request.await_args.args[0]["download"] is True
  assert result["transfer"]["batch_count"] == 1


@pytest.mark.asyncio
async def test_market_sync_splits_universe_at_agent_request_limit(
  monkeypatch,
):
  instruments = [
    {
      "code": f"{index:06d}.SZ",
      "name": "",
      "instrument_type": "stock",
      "float_volume": None,
    }
    for index in range(301)
  ]
  request = AsyncMock(
    side_effect=lambda payload, **kwargs: completed_transfer(
      payload, "request-1" if len(payload["stock_list"]) == 300 else "request-2"
    )
  )
  monkeypatch.setattr(
    market_flow,
    "resolve_instruments",
    AsyncMock(return_value=instruments),
  )
  monkeypatch.setattr(market_flow, "_request_and_wait", request)
  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)

  result = await market_flow.daily_market_data_sync_flow.fn(
    start_time="20260729",
    end_time="20260729",
    periods=["1d"],
  )

  assert request.await_count == 2
  assert [len(call.args[0]["stock_list"]) for call in request.await_args_list] == [
    300,
    1,
  ]
  assert result["transfer"]["request_id"] is None
  assert "request_ids" not in result["transfer"]
  assert result["transfer"]["batch_count"] == 2
  assert result["transfer"]["records_received"] == 301
  assert result["transfer"]["records_saved"] == 301


@pytest.mark.asyncio
async def test_market_sync_keeps_7552_daily_symbols_at_26_durable_batches(
  monkeypatch,
) -> None:
  instruments = [
    {
      "code": f"{index:06d}.SZ",
      "name": "",
      "instrument_type": "stock",
      "float_volume": None,
    }
    for index in range(7552)
  ]
  active = 0
  peak = 0
  scopes_by_offset: dict[int, str] = {}

  async def request(payload, **kwargs):
    nonlocal active, peak
    offset = int(str(payload["stock_list"][0]).split(".")[0])
    scopes_by_offset[offset] = kwargs["idempotency_scope"]
    active += 1
    peak = max(peak, active)
    try:
      await asyncio.sleep(0.004 if offset == 0 else 0.001)
    finally:
      active -= 1
    return completed_transfer(payload, f"request-{offset}")

  monkeypatch.setattr(
    market_flow,
    "resolve_instruments",
    AsyncMock(return_value=instruments),
  )
  monkeypatch.setattr(market_flow, "_request_and_wait", request)
  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)

  result = await market_flow.daily_market_data_sync_flow.fn(
    start_time="20260828",
    end_time="20260828",
    periods=["1d"],
    idempotency_scope="repair-7552",
  )

  assert peak == 2
  assert sorted(scopes_by_offset) == [index * 300 for index in range(26)]
  assert [scopes_by_offset[index * 300] for index in range(26)] == [
    f"repair-7552:batch:{index:04d}" for index in range(1, 27)
  ]
  assert result["transfer"]["batch_count"] == 26
  assert "batches" not in result["transfer"]
  assert result["transfer"]["records_received"] == 7552


def test_market_sync_idempotency_scope_is_retry_stable_and_run_scoped(
  monkeypatch,
) -> None:
  monkeypatch.setattr(
    market_flow,
    "flow_run_runtime",
    SimpleNamespace(id="flow-run-1"),
  )

  first = market_flow._market_data_sync_idempotency_scope("")
  retry = market_flow._market_data_sync_idempotency_scope("")
  monkeypatch.setattr(
    market_flow,
    "flow_run_runtime",
    SimpleNamespace(id="flow-run-2"),
  )
  next_run = market_flow._market_data_sync_idempotency_scope("")

  assert first == retry == "daily-market-data-sync-v1:flow-run-1"
  assert next_run == "daily-market-data-sync-v1:flow-run-2"
  assert next_run != first
  assert (
    market_flow._market_data_sync_idempotency_scope(" explicit-campaign ")
    == "explicit-campaign"
  )


@pytest.mark.asyncio
async def test_market_sync_drains_remaining_batches_after_failure(monkeypatch):
  instruments = [{"code": f"{index:06d}.SZ"} for index in range(601)]
  started = []
  second_finished = asyncio.Event()

  async def request(payload, **kwargs):
    offset = int(payload["stock_list"][0].split(".")[0])
    started.append(offset)
    if offset == 0:
      await second_finished.wait()
      return {"status": "failed", "request_id": "request-failed", "reason": "injected"}
    if offset == 300:
      second_finished.set()
    return completed_transfer(payload, f"request-{offset}")

  monkeypatch.setattr(
    market_flow, "resolve_instruments", AsyncMock(return_value=instruments)
  )
  monkeypatch.setattr(market_flow, "_request_and_wait", request)
  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)
  indicator = AsyncMock()
  monkeypatch.setattr(market_flow, "daily_indicator_snapshot_flow", indicator)

  with pytest.raises(
    market_flow.MarketDataSyncIncomplete, match="request-failed"
  ) as caught:
    await market_flow.daily_market_data_sync_flow.fn(
      start_time="20260828",
      end_time="20260828",
      periods=["1d"],
      compute_daily_signals=True,
      idempotency_scope="failure-campaign",
    )
  assert sorted(started) == [0, 300, 600]
  assert caught.value.total_batches == 3
  assert len(caught.value.failures) == 1
  assert caught.value.failures[0]["stock_list"] == [f"{i:06d}.SZ" for i in range(300)]
  indicator.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_sync_propagates_agent_timeout(monkeypatch):
  monkeypatch.setattr(
    market_flow,
    "resolve_instruments",
    AsyncMock(
      return_value=[
        {
          "code": "600000.SH",
          "name": "",
          "instrument_type": "stock",
          "float_volume": None,
        }
      ]
    ),
  )
  monkeypatch.setattr(
    market_flow,
    "_request_and_wait",
    AsyncMock(
      return_value={
        "status": "timeout",
        "request_id": "request-timeout",
      }
    ),
  )
  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)

  with pytest.raises(RuntimeError, match="request-timeout"):
    await market_flow.daily_market_data_sync_flow.fn(
      start_time="20260729",
      end_time="20260729",
      periods=["1d"],
    )


@pytest.mark.asyncio
async def test_market_sync_binds_explicit_live_agent(monkeypatch):
  request = AsyncMock(
    side_effect=lambda payload, **kwargs: completed_transfer(payload, "request-bound")
  )
  monkeypatch.setattr(
    market_flow,
    "resolve_instruments",
    AsyncMock(
      return_value=[
        {
          "code": "600000.SH",
          "name": "",
          "instrument_type": "stock",
          "float_volume": None,
        }
      ]
    ),
  )
  monkeypatch.setattr(market_flow, "_request_and_wait", request)
  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)

  await market_flow.daily_market_data_sync_flow.fn(
    stock_list=["600000.SH"],
    start_time="20260729",
    end_time="20260729",
    periods=["1d"],
    agent_device_id="device-live",
  )

  assert request.await_args.kwargs["agent_device_id"] == "device-live"
  assert request.await_args.kwargs["idempotency_scope"].endswith(":batch:0001")


@pytest.mark.asyncio
async def test_skip_download_only_runs_snapshot_flow(monkeypatch):
  request = AsyncMock()
  indicator = AsyncMock(
    return_value={
      "status": "success",
      "dates": [{"snapshot_date": "2026-07-29", "status": "success"}],
    }
  )
  probability = AsyncMock(return_value={"status": "success", "runs": []})
  monkeypatch.setattr(
    market_flow,
    "resolve_instruments",
    AsyncMock(
      return_value=[
        {
          "code": "600000.SH",
          "name": "",
          "instrument_type": "stock",
          "float_volume": None,
        }
      ]
    ),
  )
  monkeypatch.setattr(
    market_flow,
    "_request_and_wait",
    request,
  )
  monkeypatch.setattr(
    market_flow,
    "daily_indicator_snapshot_flow",
    indicator,
  )
  monkeypatch.setattr(
    market_flow,
    "stock_probability_inference_flow",
    probability,
  )

  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)
  result = await market_flow.daily_market_data_sync_flow.fn(
    sectors=["沪深A股", "沪深ETF"],
    start_time="20260729",
    end_time="20260729",
    periods=["1d"],
    skip_download=True,
    compute_daily_signals=True,
  )

  assert result["status"] == "success"
  request.assert_not_awaited()
  indicator.assert_awaited_once()
  probability.assert_awaited_once_with(as_of="2026-07-29")
  assert result["probability_inference"]["status"] == "success"
