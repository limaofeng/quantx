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

  async def bulk_upsert(self, records):
    for record in records:
      key = (record["code"], record["snapshot_date"])
      self.rows[key] = record
    return len(records)

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


def make_service(repository):
  return DailyIndicatorSnapshotService(
    kline_repo_factory=lambda: repository,
    db_factory=fake_db_factory,
    snapshot_repo_cls=InMemorySnapshotRepo,
  )


@pytest.mark.asyncio
async def test_same_stock_same_day_upserts_one_snapshot():
  InMemorySnapshotRepo.rows = {}
  repository = FakeKLineRepository(
    {"000001.SZ": daily_frame(10)}
  )
  service = make_service(repository)

  first = await service.compute_and_save_batch(
    codes=["000001.SZ"],
    snapshot_date=date(2026, 5, 19),
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
  )
  repository.market_data = {"000001.SZ": daily_frame(20)}
  second = await service.compute_and_save_batch(
    codes=["000001.SZ"],
    snapshot_date=date(2026, 5, 19),
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
  )

  assert first["saved"] == 1
  assert second["saved"] == 1
  assert len(InMemorySnapshotRepo.rows) == 1
  snapshot = InMemorySnapshotRepo.rows[
    ("000001.SZ", date(2026, 5, 19))
  ]
  assert snapshot["current_price"] > 20


@pytest.mark.asyncio
async def test_multiple_target_dates_share_one_kline_read():
  InMemorySnapshotRepo.rows = {}
  repository = FakeKLineRepository(
    {"000001.SZ": daily_frame(10)}
  )
  service = make_service(repository)

  result = await service.compute_and_save_dates_batch(
    codes=["000001.SZ"],
    snapshot_dates=[date(2026, 5, 19), date(2026, 5, 20)],
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
    lookback_days=30,
  )

  assert len(repository.calls) == 1
  assert result["saved"] == 2
  assert len(InMemorySnapshotRepo.rows) == 2


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
  )

  assert len(repository.calls) == 4
  assert all(
    current["end"] < following["start"]
    for current, following in zip(repository.calls, repository.calls[1:])
  )
  assert all(
    call["end"] - call["start"] <= timedelta(days=90)
    for call in repository.calls
  )
  assert result["saved"] == 1
  assert result["systemic_failure"] is False


@pytest.mark.asyncio
async def test_missing_target_day_does_not_reuse_previous_close():
  InMemorySnapshotRepo.rows = {}
  repository = FakeKLineRepository(
    {"000001.SZ": daily_frame(10, end="2026-05-19")}
  )
  service = make_service(repository)

  result = await service.compute_and_save_batch(
    codes=["000001.SZ"],
    snapshot_date=date(2026, 5, 20),
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
  )

  assert result["saved"] == 0
  assert result["skipped"] == 1
  assert result["missing_target"] == 1
  assert InMemorySnapshotRepo.rows == {}


@pytest.mark.asyncio
async def test_batch_reports_influx_read_error_as_systemic_failure():
  repository = FakeKLineRepository(
    error=RuntimeError("influx unavailable")
  )
  service = make_service(repository)

  result = await service.compute_and_save_batch(
    codes=["000001.SZ", "600000.SH"],
    snapshot_date=date(2026, 5, 19),
    instrument_type_map={},
    name_map={},
  )

  assert result["saved"] == 0
  assert result["failed"] == 2
  assert result["systemic_failure"] is True
  assert "influx unavailable" in result["errors"][0]
