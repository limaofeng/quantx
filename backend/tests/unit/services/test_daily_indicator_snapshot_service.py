from datetime import date

import pandas as pd
import pytest

from services.daily_indicator_snapshot_service import DailyIndicatorSnapshotService


class FakeManager:
  def __init__(self, market_data):
    self.market_data = market_data

  def get_market_data(self, **kwargs):
    return self.market_data


class FakeRegistry:
  def __init__(self, manager):
    self.manager = manager

  def get_manager(self):
    return self.manager


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


def daily_frame(last_close: float):
  closes = [last_close + i * 0.1 for i in range(30)]
  return pd.DataFrame(
    {
      "open": [value - 0.05 for value in closes],
      "high": [value + 0.1 for value in closes],
      "low": [value - 0.1 for value in closes],
      "close": closes,
      "volume": [1000 + i for i in range(30)],
      "amount": [10000 + i for i in range(30)],
    }
  )


def make_service(market_data):
  return DailyIndicatorSnapshotService(
    data_registry_factory=lambda: FakeRegistry(FakeManager(market_data)),
    db_factory=fake_db_factory,
    snapshot_repo_cls=InMemorySnapshotRepo,
  )


@pytest.mark.asyncio
async def test_same_stock_same_day_upserts_one_snapshot():
  InMemorySnapshotRepo.rows = {}
  service = make_service({"000001.SZ": daily_frame(10)})

  first = await service.compute_and_save_batch(
    codes=["000001.SZ"],
    snapshot_date=date(2026, 5, 19),
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
  )
  service = make_service({"000001.SZ": daily_frame(20)})
  second = await service.compute_and_save_batch(
    codes=["000001.SZ"],
    snapshot_date=date(2026, 5, 19),
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
  )

  assert first["saved"] == 1
  assert second["saved"] == 1
  assert len(InMemorySnapshotRepo.rows) == 1
  assert InMemorySnapshotRepo.rows[("000001.SZ", date(2026, 5, 19))]["current_price"] > 20


@pytest.mark.asyncio
async def test_same_stock_different_day_adds_snapshot_history():
  InMemorySnapshotRepo.rows = {}
  service = make_service({"000001.SZ": daily_frame(10)})

  await service.compute_and_save_batch(
    codes=["000001.SZ"],
    snapshot_date=date(2026, 5, 19),
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
  )
  await service.compute_and_save_batch(
    codes=["000001.SZ"],
    snapshot_date=date(2026, 5, 20),
    instrument_type_map={"000001.SZ": "stock"},
    name_map={"000001.SZ": "平安银行"},
  )

  assert len(InMemorySnapshotRepo.rows) == 2
  assert ("000001.SZ", date(2026, 5, 19)) in InMemorySnapshotRepo.rows
  assert ("000001.SZ", date(2026, 5, 20)) in InMemorySnapshotRepo.rows


@pytest.mark.asyncio
async def test_batch_reports_fetch_error_as_failed():
  class BrokenManager:
    def get_market_data(self, **kwargs):
      raise RuntimeError("xtdata unavailable")

  service = DailyIndicatorSnapshotService(
    data_registry_factory=lambda: FakeRegistry(BrokenManager()),
    db_factory=fake_db_factory,
    snapshot_repo_cls=InMemorySnapshotRepo,
  )

  result = await service.compute_and_save_batch(
    codes=["000001.SZ", "600000.SH"],
    snapshot_date=date(2026, 5, 19),
    instrument_type_map={},
    name_map={},
  )

  assert result["saved"] == 0
  assert result["failed"] == 2
  assert "xtdata unavailable" in result["errors"][0]
