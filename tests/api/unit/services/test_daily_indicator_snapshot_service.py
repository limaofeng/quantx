from datetime import date, timedelta

import pandas as pd
import pytest
from quantx_infrastructure.services.daily_indicator_snapshot_service import (
  DailyIndicatorSnapshotService,
)


class FakeKLineRepository:
  def __init__(self, market_data=None, error=None):
    self.market_data = market_data or {}
    self.error = error
    self.calls = []

  def find_daily_batch(self, **kwargs):
    self.calls.append(kwargs)
    if self.error:
      raise self.error
    if callable(self.market_data):
      return self.market_data(kwargs, len(self.calls))
    return self.market_data


class InMemorySnapshotRepo:
  rows = {}

  def __init__(self, db):
    self.db = db

  async def bulk_upsert(self, records, *, snapshot_run_ids, lock_backend_pid):
    assert snapshot_run_ids
    assert lock_backend_pid == 101
    for record in records:
      key = (record["code"], record["snapshot_date"])
      self.rows[key] = record
    return len(records)

  async def invalidate_indicator_scope(
    self, codes, snapshot_dates, *, snapshot_run_ids
  ):
    assert snapshot_run_ids
    for (code, target), record in self.rows.items():
      if code in codes and target in snapshot_dates:
        record["calculation_version"] = None

  async def delete_older_than(self, cutoff_date):
    old_keys = [key for key in self.rows if key[1] < cutoff_date]
    for key in old_keys:
      del self.rows[key]
    return len(old_keys)


async def fake_db_factory():
  yield object()


def daily_frame(last_close: float, end: str = "2026-05-20", periods: int = 40):
  dates = pd.bdate_range(end=end, periods=periods, tz="Asia/Shanghai")
  closes = [last_close + i * 0.1 for i in range(periods)]
  return pd.DataFrame(
    {
      "time": dates,
      "open": [value - 0.05 for value in closes],
      "high": [value + 0.1 for value in closes],
      "low": [value - 0.1 for value in closes],
      "close": closes,
      "volume": [1000 + i for i in range(periods)],
      "amount": [10000 + i for i in range(periods)],
    }
  )


def make_service(repository, *, inactive_empty_proof_loader=None):
  async def verified_history(frames):
    return {
      code: frame.sort_values("time").drop_duplicates("time", keep="last")
      for code, frame in frames.items()
    }

  class VerifiedCalendar:
    async def get_trading_calendar(self, market, start_date, end_date):
      return list(pd.bdate_range(start_date, end_date).date)

  async def no_inactive_empty_proofs(_candidates):
    return set()

  return DailyIndicatorSnapshotService(
    kline_repo_factory=lambda: repository,
    db_factory=fake_db_factory,
    snapshot_repo_cls=InMemorySnapshotRepo,
    price_history_loader=verified_history,
    inactive_empty_proof_loader=(
      inactive_empty_proof_loader or no_inactive_empty_proofs
    ),
    trading_dates=VerifiedCalendar(),
  )


@pytest.mark.asyncio
async def test_snapshot_run_identity_is_required_before_any_market_read():
  repository = FakeKLineRepository({"000001.SZ": daily_frame(10)})
  service = make_service(repository)

  with pytest.raises(ValueError):
    await service.compute_and_save_batch(
      codes=["000001.SZ"],
      snapshot_date=date(2026, 5, 20),
      instrument_type_map={},
      name_map={},
    )

  with pytest.raises(ValueError):
    await service.compute_and_save_dates_batch(
      codes=["000001.SZ"],
      snapshot_dates=[date(2026, 5, 20)],
      instrument_type_map={},
      name_map={},
      snapshot_run_ids={},
    )

  with pytest.raises(ValueError, match="数据库锁会话"):
    await service.compute_and_save_dates_batch(
      codes=["000001.SZ"],
      snapshot_dates=[date(2026, 5, 20)],
      instrument_type_map={},
      name_map={},
      snapshot_run_ids={date(2026, 5, 20): 1},
    )

  assert repository.calls == []


@pytest.mark.parametrize("inactive_kind", ["no_volume", "suspended"])
@pytest.mark.asyncio
async def test_inactive_target_is_audited_skip_not_calculation_failure(inactive_kind):
  InMemorySnapshotRepo.rows = {}
  frame = daily_frame(10)
  inactive = frame.copy()
  if inactive_kind == "no_volume":
    inactive.loc[inactive.index[-1], "volume"] = 0
  else:
    inactive["suspend_flag"] = 0
    inactive.loc[inactive.index[-1], "suspend_flag"] = 1

  async def unexpected_proof_lookup(_candidates):
    raise AssertionError("精确目标日停牌不应查询历史空行证明")

  service = make_service(
    FakeKLineRepository({"000001.SZ": frame, "000002.SZ": inactive}),
    inactive_empty_proof_loader=unexpected_proof_lookup,
  )
  result = await service.compute_and_save_batch(
    codes=["000001.SZ", "000002.SZ"],
    snapshot_date=date(2026, 5, 20),
    instrument_type_map={},
    name_map={},
    snapshot_run_id=1,
    lock_backend_pid=101,
  )
  assert result["saved"] == 1
  assert result["skipped"] == result["inactive_target"] == 1
  assert result["failed"] == 0


@pytest.mark.asyncio
async def test_bad_ohlc_remains_failed_and_past_inactivity_invalidates_windows():
  InMemorySnapshotRepo.rows = {}
  active = daily_frame(10)
  active.loc[active.index[-2], "volume"] = 0
  bad = daily_frame(10)
  bad.loc[bad.index[-1], "high"] = 1
  service = make_service(FakeKLineRepository({"000001.SZ": active, "000002.SZ": bad}))
  result = await service.compute_and_save_batch(
    codes=["000001.SZ", "000002.SZ"],
    snapshot_date=date(2026, 5, 20),
    instrument_type_map={},
    name_map={},
    snapshot_run_id=1,
    lock_backend_pid=101,
  )
  assert result["saved"] == result["failed"] == 1
  assert result["inactive_target"] == 0
  assert InMemorySnapshotRepo.rows[("000001.SZ", date(2026, 5, 20))]["ma5"] is None


@pytest.mark.parametrize(
  "changed_kind", ["no_volume", "suspended", "missing", "bad", "read_error"]
)
@pytest.mark.asyncio
async def test_rerun_invalidates_old_indicator_rows_only_in_requested_scope(
  changed_kind,
):
  from quantx_domain.indicators import INDICATOR_VERSION

  InMemorySnapshotRepo.rows = {}
  repository = FakeKLineRepository(
    {"000001.SZ": daily_frame(10), "000002.SZ": daily_frame(20)}
  )
  service = make_service(repository)
  await service.compute_and_save_dates_batch(
    codes=["000001.SZ", "000002.SZ"],
    snapshot_dates=[date(2026, 5, 19), date(2026, 5, 20)],
    instrument_type_map={},
    name_map={},
    snapshot_run_ids={date(2026, 5, 19): 1, date(2026, 5, 20): 2},
    lock_backend_pid=101,
  )
  changed = daily_frame(10)
  if changed_kind == "no_volume":
    changed.loc[changed.index[-1], "volume"] = 0
  elif changed_kind == "suspended":
    changed["suspend_flag"] = 0
    changed.loc[changed.index[-1], "suspend_flag"] = 1
  elif changed_kind == "missing":
    changed = changed.iloc[:-1]
  elif changed_kind == "bad":
    changed.loc[changed.index[-1], "high"] = 1
  else:
    repository.error = RuntimeError("history unavailable")
  repository.market_data["000001.SZ"] = changed
  result = await service.compute_and_save_batch(
    codes=["000001.SZ"],
    snapshot_date=date(2026, 5, 20),
    instrument_type_map={},
    name_map={},
    snapshot_run_id=3,
    lock_backend_pid=101,
  )
  assert result["saved"] == 0
  assert len(InMemorySnapshotRepo.rows) == 4  # retain evidence, do not delete
  assert (
    InMemorySnapshotRepo.rows[("000001.SZ", date(2026, 5, 20))]["calculation_version"]
    is None
  )
  for key in [
    ("000001.SZ", date(2026, 5, 19)),
    ("000002.SZ", date(2026, 5, 19)),
    ("000002.SZ", date(2026, 5, 20)),
  ]:
    assert InMemorySnapshotRepo.rows[key]["calculation_version"] == INDICATOR_VERSION


@pytest.mark.asyncio
async def test_same_stock_same_day_upserts_one_snapshot():
  InMemorySnapshotRepo.rows = {}
  repository = FakeKLineRepository({"000001.SZ": daily_frame(10)})
  service = make_service(repository)

  first = await service.compute_and_save_batch(
    codes=["000001.SZ"],
    snapshot_date=date(2026, 5, 19),
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
    snapshot_run_id=1,
    lock_backend_pid=101,
  )
  repository.market_data = {"000001.SZ": daily_frame(20)}
  second = await service.compute_and_save_batch(
    codes=["000001.SZ"],
    snapshot_date=date(2026, 5, 19),
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
    snapshot_run_id=2,
    lock_backend_pid=101,
  )

  assert first["saved"] == 1
  assert second["saved"] == 1
  assert len(InMemorySnapshotRepo.rows) == 1
  snapshot = InMemorySnapshotRepo.rows[("000001.SZ", date(2026, 5, 19))]
  assert snapshot["current_price"] > 20


@pytest.mark.asyncio
async def test_snapshot_db_sessions_close_before_the_next_stage():
  InMemorySnapshotRepo.rows = {}
  events = []

  async def tracked_db_factory():
    events.append("open")
    try:
      yield object()
    finally:
      events.append("close")

  repository = FakeKLineRepository({"000001.SZ": daily_frame(10)})
  service = make_service(repository)
  service.db_factory = tracked_db_factory

  result = await service.compute_and_save_batch(
    codes=["000001.SZ"],
    snapshot_date=date(2026, 5, 20),
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
    snapshot_run_id=1,
    lock_backend_pid=101,
  )

  assert result["saved"] == 1
  assert events == ["open", "close", "open", "close"]


@pytest.mark.asyncio
async def test_multiple_target_dates_share_one_kline_read():
  InMemorySnapshotRepo.rows = {}
  repository = FakeKLineRepository({"000001.SZ": daily_frame(10)})
  service = make_service(repository)

  result = await service.compute_and_save_dates_batch(
    codes=["000001.SZ"],
    snapshot_dates=[date(2026, 5, 19), date(2026, 5, 20)],
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
    lookback_days=30,
    snapshot_run_ids={date(2026, 5, 19): 1, date(2026, 5, 20): 2},
    lock_backend_pid=101,
  )

  assert len(repository.calls) == 1
  assert result["saved"] == 2
  assert len(InMemorySnapshotRepo.rows) == 2


@pytest.mark.asyncio
async def test_multiple_target_dates_use_each_dates_instrument_lifecycle_scope():
  InMemorySnapshotRepo.rows = {}
  repository = FakeKLineRepository(
    {
      "000001.SZ": daily_frame(10),
      "000002.SZ": daily_frame(20),
    }
  )
  service = make_service(repository)
  first_date = date(2026, 5, 19)
  second_date = date(2026, 5, 20)

  result = await service.compute_and_save_dates_batch(
    codes=["000001.SZ", "000002.SZ"],
    snapshot_dates=[first_date, second_date],
    instrument_type_map={"000001.SZ": "stock", "000002.SZ": "stock"},
    name_map={"000001.SZ": "股票一", "000002.SZ": "股票二"},
    codes_by_snapshot_date={
      first_date: ["000001.SZ"],
      second_date: ["000002.SZ"],
    },
    snapshot_run_ids={first_date: 1, second_date: 2},
    lock_backend_pid=101,
  )

  assert len(repository.calls) == 7
  assert result["total"] == result["saved"] == 2
  assert result["dates"][first_date.isoformat()]["total"] == 1
  assert result["dates"][second_date.isoformat()]["total"] == 1
  assert set(InMemorySnapshotRepo.rows) == {
    ("000001.SZ", first_date),
    ("000002.SZ", second_date),
  }


@pytest.mark.asyncio
async def test_inactive_lifecycle_scope_invalidates_stale_row_without_market_read():
  from quantx_domain.indicators import INDICATOR_VERSION

  target = date(2026, 5, 20)
  InMemorySnapshotRepo.rows = {
    ("000001.SZ", target): {
      "code": "000001.SZ",
      "snapshot_date": target,
      "calculation_version": INDICATOR_VERSION,
    }
  }
  repository = FakeKLineRepository({"000001.SZ": daily_frame(10)})
  service = make_service(repository)

  result = await service.compute_and_save_dates_batch(
    codes=["000001.SZ"],
    snapshot_dates=[target],
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "旧生命周期标的"},
    codes_by_snapshot_date={target: []},
    snapshot_run_ids={target: 1},
    lock_backend_pid=101,
  )

  assert result["total"] == 0
  assert result["saved"] == result["skipped"] == result["failed"] == 0
  assert repository.calls == []
  assert InMemorySnapshotRepo.rows[("000001.SZ", target)]["calculation_version"] is None


@pytest.mark.asyncio
async def test_inactive_only_invalidation_failure_is_reported_without_market_read():
  class FailingInvalidationRepo(InMemorySnapshotRepo):
    async def invalidate_indicator_scope(
      self, codes, snapshot_dates, *, snapshot_run_ids
    ):
      raise RuntimeError("database unavailable")

  target = date(2026, 5, 20)
  repository = FakeKLineRepository({"000001.SZ": daily_frame(10)})
  service = make_service(repository)
  service.snapshot_repo_cls = FailingInvalidationRepo

  result = await service.compute_and_save_dates_batch(
    codes=["000001.SZ"],
    snapshot_dates=[target],
    instrument_type_map={},
    name_map={},
    codes_by_snapshot_date={target: []},
    snapshot_run_ids={target: 1},
    lock_backend_pid=101,
  )

  assert result["total"] == result["failed"] == 0
  assert result["systemic_failure"] is True
  assert "database unavailable" in result["errors"][0]
  assert result["dates"][target.isoformat()]["errors"] == result["errors"]
  assert repository.calls == []


@pytest.mark.asyncio
async def test_lifecycle_scoped_errors_are_bound_to_the_affected_dates():
  InMemorySnapshotRepo.rows = {}
  good = daily_frame(10)
  bad = daily_frame(20).drop(columns=["time"])
  service = make_service(FakeKLineRepository({"000001.SZ": good, "000002.SZ": bad}))

  async def pass_through_history(frames):
    return {
      code: (
        frame.sort_values("time").drop_duplicates("time", keep="last")
        if "time" in frame.columns
        else frame
      )
      for code, frame in frames.items()
    }

  service.price_history_loader = pass_through_history
  first_date = date(2026, 5, 19)
  second_date = date(2026, 5, 20)

  result = await service.compute_and_save_dates_batch(
    codes=["000001.SZ", "000002.SZ"],
    snapshot_dates=[first_date, second_date],
    instrument_type_map={},
    name_map={},
    codes_by_snapshot_date={
      first_date: ["000001.SZ"],
      second_date: ["000002.SZ"],
    },
    snapshot_run_ids={first_date: 1, second_date: 2},
    lock_backend_pid=101,
  )

  assert result["dates"][first_date.isoformat()]["errors"] == []
  assert result["dates"][second_date.isoformat()]["errors"] == [
    "000002.SZ K 线格式异常: K 线缺少 time 字段"
  ]


@pytest.mark.asyncio
async def test_long_history_is_read_in_non_overlapping_time_windows():
  InMemorySnapshotRepo.rows = {}
  frame = daily_frame(10)

  def market_data(_kwargs, call_number):
    start = (call_number - 1) * 10
    rows = frame.iloc[start : start + 10]
    return {"000001.SZ": rows.copy()}

  repository = FakeKLineRepository(market_data)
  service = make_service(repository)

  result = await service.compute_and_save_batch(
    codes=["000001.SZ"],
    snapshot_date=date(2026, 5, 20),
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
    snapshot_run_id=1,
    lock_backend_pid=101,
  )

  assert len(repository.calls) == 7
  assert all(
    current["end"] < following["start"]
    for current, following in zip(repository.calls, repository.calls[1:])
  )
  assert all(
    call["end"] - call["start"] <= timedelta(days=90) for call in repository.calls
  )
  assert result["saved"] == 1
  assert result["systemic_failure"] is False


@pytest.mark.asyncio
async def test_missing_target_day_does_not_reuse_previous_close():
  InMemorySnapshotRepo.rows = {}
  repository = FakeKLineRepository({"000001.SZ": daily_frame(10, end="2026-05-19")})
  service = make_service(repository)

  result = await service.compute_and_save_batch(
    codes=["000001.SZ"],
    snapshot_date=date(2026, 5, 20),
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
    snapshot_run_id=1,
    lock_backend_pid=101,
  )

  assert result["saved"] == 0
  assert result["skipped"] == 1
  assert result["missing_target"] == 1
  assert InMemorySnapshotRepo.rows == {}


@pytest.mark.parametrize("inactive_kind", ["no_volume", "suspended"])
@pytest.mark.asyncio
async def test_missing_target_with_last_known_inactive_bar_without_proof_stays_missing(
  inactive_kind,
):
  InMemorySnapshotRepo.rows = {}
  frame = daily_frame(10, end="2026-05-19")
  if inactive_kind == "no_volume":
    frame.loc[frame.index[-1], "volume"] = 0
  else:
    frame["suspend_flag"] = 0
    frame.loc[frame.index[-1], "suspend_flag"] = 1
  service = make_service(FakeKLineRepository({"560650.SH": frame}))

  result = await service.compute_and_save_batch(
    codes=["560650.SH"],
    snapshot_date=date(2026, 5, 20),
    instrument_type_map={"560650.SH": "etf"},
    name_map={"560650.SH": "停牌 ETF"},
    snapshot_run_id=1,
    lock_backend_pid=101,
  )

  assert result["saved"] == 0
  assert result["skipped"] == result["missing_target"] == 1
  assert result["inactive_target"] == 0
  assert result["failed"] == 0
  assert InMemorySnapshotRepo.rows == {}


@pytest.mark.asyncio
async def test_missing_targets_require_one_batched_dual_empty_proof_lookup():
  InMemorySnapshotRepo.rows = {}
  first = daily_frame(10, end="2026-05-19")
  first.loc[first.index[-1], "volume"] = 0
  second = daily_frame(20, end="2026-05-19")
  second["suspend_flag"] = 0
  second.loc[second.index[-1], "suspend_flag"] = 1
  proof_calls = []

  async def load_proofs(candidates):
    proof_calls.append(set(candidates))
    return set(candidates)

  service = make_service(
    FakeKLineRepository({"560650.SH": first, "000001.SZ": second}),
    inactive_empty_proof_loader=load_proofs,
  )

  result = await service.compute_and_save_batch(
    codes=["560650.SH", "000001.SZ"],
    snapshot_date=date(2026, 5, 20),
    instrument_type_map={"560650.SH": "etf", "000001.SZ": "stock"},
    name_map={"560650.SH": "停牌 ETF", "000001.SZ": "停牌股票"},
    snapshot_run_id=1,
    lock_backend_pid=101,
  )

  assert proof_calls == [
    {
      ("560650.SH", date(2026, 5, 20)),
      ("000001.SZ", date(2026, 5, 20)),
    }
  ]
  assert result["saved"] == 0
  assert result["skipped"] == result["inactive_target"] == 2
  assert result["missing_target"] == result["failed"] == 0


@pytest.mark.asyncio
async def test_batch_reports_influx_read_error_as_systemic_failure():
  repository = FakeKLineRepository(error=RuntimeError("influx unavailable"))
  service = make_service(repository)

  result = await service.compute_and_save_batch(
    codes=["000001.SZ", "600000.SH"],
    snapshot_date=date(2026, 5, 19),
    instrument_type_map={},
    name_map={},
    snapshot_run_id=1,
    lock_backend_pid=101,
  )

  assert result["saved"] == 0
  assert result["failed"] == 2
  assert result["systemic_failure"] is True
  assert "influx unavailable" in result["errors"][0]
