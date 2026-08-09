from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import quantx_infrastructure.services.position_service as position_service_module
from quantx_infrastructure.models.broker_position_snapshot import BrokerPositionSnapshot
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.services.position_service import PositionService


class FakeScalarResult:
  def __init__(self, rows):
    self.rows = rows

  def scalars(self):
    return self

  def all(self):
    return list(self.rows)


class FakeDb:
  def __init__(self, positions, status=None):
    self.positions = list(positions)
    self.status = status
    self.deleted = []
    self.merged = []
    self.commits = 0
    self.execute_calls = 0

  async def get(self, model, key):
    if model is BrokerPositionSnapshot:
      return self.status
    if model is Position:
      return next((item for item in self.positions if item.id == key), None)
    return None

  async def execute(self, statement):
    self.execute_calls += 1
    return FakeScalarResult(self.positions if self.execute_calls == 1 else [])

  async def delete(self, value):
    self.deleted.append(value)

  async def merge(self, value):
    self.merged.append(value)
    if isinstance(value, BrokerPositionSnapshot):
      self.status = value
    return value

  async def commit(self):
    self.commits += 1


def broker_position(code, volume):
  return SimpleNamespace(
    account_type=None,
    stock_code=code,
    instrument_name=code,
    volume=volume,
    can_use_volume=volume,
    open_price=10.0,
    market_value=volume * 10.0,
    frozen_volume=0,
    on_road_volume=0,
    yesterday_volume=volume,
    avg_price=10.0,
    direction=0,
  )


@pytest.mark.asyncio
async def test_complete_empty_snapshot_can_clear_positions(monkeypatch):
  db = FakeDb(
    [
      Position.from_dict(
        {"account_id": "account-1", "stock_code": "600000.SH", "volume": 100}
      )
    ]
  )

  async def fake_get_async_db():
    yield db

  monkeypatch.setattr(position_service_module, "get_async_db", fake_get_async_db)
  result = await PositionService().apply_full_snapshot(
    account_id="account-1",
    positions=[],
    sequence=10,
    reported_at=datetime(2026, 7, 13, 10, 0),
    source="MINIQMT",
    is_complete=True,
  )

  assert result["applied"] is True
  assert len(db.deleted) == 1
  assert db.status.position_count == 0
  assert db.commits == 1


@pytest.mark.asyncio
async def test_stale_or_incomplete_snapshot_never_clears_positions(monkeypatch):
  status = BrokerPositionSnapshot(
    account_id="account-1",
    sequence=20,
    source="MINIQMT",
    is_complete=True,
  )
  db = FakeDb([Position.from_dict({"account_id": "account-1", "stock_code": "600000.SH", "volume": 100})], status)

  async def fake_get_async_db():
    yield db

  monkeypatch.setattr(position_service_module, "get_async_db", fake_get_async_db)
  stale = await PositionService().apply_full_snapshot(
    account_id="account-1",
    positions=[],
    sequence=19,
    reported_at=datetime(2026, 7, 13, 10, 0),
    source="MINIQMT",
    is_complete=True,
  )
  with pytest.raises(ValueError, match="不完整"):
    await PositionService().apply_full_snapshot(
      account_id="account-1",
      positions=[],
      sequence=21,
      reported_at=datetime(2026, 7, 13, 10, 1),
      source="MINIQMT",
      is_complete=False,
    )

  assert stale["applied"] is False
  assert db.deleted == []
  assert db.commits == 0


@pytest.mark.asyncio
async def test_snapshot_times_are_persisted_as_naive_utc(monkeypatch):
  db = FakeDb([])

  async def fake_get_async_db():
    yield db

  monkeypatch.setattr(position_service_module, "get_async_db", fake_get_async_db)
  await PositionService().apply_full_snapshot(
    account_id="account-1",
    positions=[],
    sequence=10,
    reported_at=datetime(
      2026, 7, 13, 18, 0, tzinfo=timezone(timedelta(hours=8))
    ),
    source="QMT_AGENT",
    is_complete=True,
  )

  assert db.status.reported_at == datetime(2026, 7, 13, 10, 0)
  assert db.status.reported_at.tzinfo is None
  assert db.status.received_at.tzinfo is None
