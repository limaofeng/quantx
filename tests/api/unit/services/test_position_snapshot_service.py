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

  async def get(self, model, key, **_kwargs):
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
async def test_prepared_empty_snapshot_can_clear_positions_then_finalize(monkeypatch):
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
  service = PositionService()
  result = await service.prepare_full_snapshot(
    account_id="account-1",
    positions=[],
    sequence=10,
    reported_at=datetime(2026, 7, 13, 10, 0),
    source="MINIQMT",
  )

  assert result["applied"] is True
  assert len(db.deleted) == 1
  assert db.status.position_count == 0
  assert db.status.is_complete is False
  assert db.status.last_error == "SNAPSHOT_APPLY_IN_PROGRESS"
  assert db.commits == 1

  finalized = await service.finalize_full_snapshot(
    account_id="account-1",
    sequence=10,
    reported_at=datetime(2026, 7, 13, 10, 0),
    source="MINIQMT",
  )
  assert finalized["applied"] is True
  assert db.status.is_complete is True
  assert db.status.last_error is None
  assert db.commits == 2


@pytest.mark.asyncio
async def test_begin_full_snapshot_attempt_advances_generation_before_apply(
  monkeypatch,
):
  status = BrokerPositionSnapshot(
    account_id="account-1",
    sequence=5,
    source="QMT_AGENT",
    reported_at=datetime(2026, 7, 13, 10, 0),
    received_at=datetime(2026, 7, 13, 10, 0),
    is_complete=True,
  )
  db = FakeDb([], status)

  async def fake_get_async_db():
    yield db

  monkeypatch.setattr(position_service_module, "get_async_db", fake_get_async_db)
  service = PositionService()
  started = await service.begin_full_snapshot_attempt(
    account_id="account-1",
    sequence=7,
    reported_at=datetime(2026, 7, 13, 10, 1),
    source="QMT_AGENT",
  )

  assert started["applied"] is True
  assert started["reason"] == "STARTED"
  assert db.status.sequence == 7
  assert db.status.is_complete is False
  assert db.status.last_error == "SNAPSHOT_APPLY_IN_PROGRESS"
  assert db.commits == 1

  stale = await service.begin_full_snapshot_attempt(
    account_id="account-1",
    sequence=6,
    reported_at=datetime(2026, 7, 13, 10, 2),
    source="QMT_AGENT",
  )
  assert stale["applied"] is False
  assert stale["reason"] == "STALE_SEQUENCE"
  assert db.status.sequence == 7
  assert db.commits == 1

  resumed = await service.begin_full_snapshot_attempt(
    account_id="account-1",
    sequence=7,
    reported_at=datetime(2026, 7, 13, 10, 3),
    source="QMT_AGENT",
  )
  assert resumed["applied"] is True
  assert resumed["reason"] == "STARTED"
  assert db.status.sequence == 7
  assert db.commits == 2

  db.status.last_error = (
    "T_TRADE_PORTFOLIO_SNAPSHOT_STALE:持仓增量未形成完整账户快照"
  )
  delta_marker = await service.begin_full_snapshot_attempt(
    account_id="account-1",
    sequence=7,
    reported_at=datetime(2026, 7, 13, 10, 4),
    source="QMT_AGENT",
  )
  assert delta_marker["applied"] is False
  assert delta_marker["reason"] == "STALE_SEQUENCE"
  assert db.commits == 2


@pytest.mark.asyncio
async def test_full_snapshot_finalize_requires_prepared_marker(monkeypatch):
  db = FakeDb([])

  async def fake_get_async_db():
    yield db

  monkeypatch.setattr(position_service_module, "get_async_db", fake_get_async_db)
  service = PositionService()
  prepared = await service.prepare_full_snapshot(
    account_id="account-1",
    positions=[],
    sequence=10,
    reported_at=datetime(2026, 7, 13, 10, 0),
    source="QMT_AGENT",
  )
  assert prepared["reason"] == "PREPARED"
  assert db.status.is_complete is False
  assert db.status.last_error == "SNAPSHOT_APPLY_IN_PROGRESS"

  finalized = await service.finalize_full_snapshot(
    account_id="account-1",
    sequence=10,
    reported_at=datetime(2026, 7, 13, 10, 0),
    source="QMT_AGENT",
  )
  assert finalized["applied"] is True
  assert db.status.is_complete is True
  assert db.status.last_error is None

  delta_status = BrokerPositionSnapshot(
    account_id="account-1",
    sequence=10,
    source="QMT_AGENT",
    is_complete=False,
    last_error="T_TRADE_PORTFOLIO_SNAPSHOT_STALE:持仓增量未形成完整账户快照",
  )
  delta_db = FakeDb([], delta_status)

  async def fake_delta_db():
    yield delta_db

  monkeypatch.setattr(position_service_module, "get_async_db", fake_delta_db)
  rejected = await service.finalize_full_snapshot(
    account_id="account-1",
    sequence=10,
    reported_at=datetime(2026, 7, 13, 10, 0),
    source="QMT_AGENT",
  )
  assert rejected["applied"] is False
  assert rejected["reason"] == "STALE_SEQUENCE"
  assert delta_db.status.is_complete is False
  assert delta_db.commits == 0


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
  stale = await PositionService().prepare_full_snapshot(
    account_id="account-1",
    positions=[],
    sequence=19,
    reported_at=datetime(2026, 7, 13, 10, 0),
    source="MINIQMT",
  )

  assert stale["applied"] is False
  assert db.deleted == []
  assert db.commits == 0


@pytest.mark.asyncio
async def test_position_delta_invalidates_complete_snapshot_atomically(monkeypatch):
  status = BrokerPositionSnapshot(
    account_id="account-1",
    sequence=20,
    source="QMT_AGENT",
    reported_at=datetime(2026, 7, 13, 10, 0),
    received_at=datetime(2026, 7, 13, 10, 0),
    position_count=1,
    is_complete=True,
  )
  db = FakeDb(
    [
      Position.from_dict(
        {"account_id": "account-1", "stock_code": "600000.SH", "volume": 100}
      )
    ],
    status,
  )

  async def fake_get_async_db():
    yield db

  monkeypatch.setattr(position_service_module, "get_async_db", fake_get_async_db)
  await PositionService().apply_position_delta(
    broker_position("600000.SH", 200),
    "account-1",
  )

  assert db.status.is_complete is False
  assert db.status.sequence == 20
  assert db.status.last_error.startswith("T_TRADE_PORTFOLIO_SNAPSHOT_STALE:")
  assert db.commits == 1


@pytest.mark.asyncio
async def test_validated_read_rejects_delta_invalidation_between_reads():
  service = PositionService()
  state = {"delta_applied": False}
  snapshot = {
    "account_id": "account-1",
    "sequence": 20,
    "source": "QMT_AGENT",
    "reported_at": datetime(2026, 7, 13, 10, 0),
    "received_at": datetime(2026, 7, 13, 10, 0),
    "position_count": 1,
    "is_complete": True,
    "last_error": None,
  }

  async def read_snapshot(_account_id):
    if state["delta_applied"]:
      raise RuntimeError(
        "T_TRADE_PORTFOLIO_SNAPSHOT_STALE:持仓增量未形成完整账户快照"
      )
    return snapshot

  async def read_positions(*, account_id):
    assert account_id == "account-1"
    # This models apply_position_delta's same-transaction invalidation having
    # committed after the first snapshot read and before the row read returns.
    state["delta_applied"] = True
    return []

  service.read_agent_snapshot = read_snapshot
  service.get_positions = read_positions

  with pytest.raises(RuntimeError, match="T_TRADE_PORTFOLIO_SNAPSHOT_STALE"):
    await service.read_validated_snapshot_and_positions("account-1")


@pytest.mark.asyncio
async def test_snapshot_times_are_persisted_as_naive_utc(monkeypatch):
  db = FakeDb([])

  async def fake_get_async_db():
    yield db

  monkeypatch.setattr(position_service_module, "get_async_db", fake_get_async_db)
  service = PositionService()
  await service.prepare_full_snapshot(
    account_id="account-1",
    positions=[],
    sequence=10,
    reported_at=datetime(
      2026, 7, 13, 18, 0, tzinfo=timezone(timedelta(hours=8))
    ),
    source="QMT_AGENT",
  )
  await service.finalize_full_snapshot(
    account_id="account-1",
    sequence=10,
    reported_at=datetime(
      2026, 7, 13, 18, 0, tzinfo=timezone(timedelta(hours=8))
    ),
    source="QMT_AGENT",
  )

  assert db.status.reported_at == datetime(2026, 7, 13, 10, 0)
  assert db.status.reported_at.tzinfo is None
  assert db.status.received_at.tzinfo is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("overrides", "reason"),
  (
    ({"is_complete": False}, "不完整"),
    ({"sequence": 0}, "序列无效"),
    ({"last_error": "agent disconnected"}, "包含错误"),
    ({"reported_at": datetime(2026, 7, 13, 9, 58)}, "已过期"),
  ),
)
async def test_read_agent_snapshot_rejects_non_current_snapshot(
  monkeypatch,
  overrides,
  reason,
):
  checked_at = datetime(2026, 7, 13, 10, 0)
  values = {
    "account_id": "account-1",
    "sequence": 10,
    "source": "QMT_AGENT",
    "reported_at": checked_at - timedelta(seconds=1),
    "received_at": checked_at - timedelta(seconds=1),
    "position_count": 0,
    "is_complete": True,
    "last_error": None,
  }
  values.update(overrides)
  status = BrokerPositionSnapshot(**values)
  db = FakeDb([], status)

  async def fake_get_async_db():
    yield db

  monkeypatch.setattr(position_service_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(position_service_module, "utcnow", lambda: checked_at)

  with pytest.raises(RuntimeError, match=f"T_TRADE_PORTFOLIO_SNAPSHOT_STALE.*{reason}"):
    await PositionService().read_agent_snapshot("account-1")


@pytest.mark.asyncio
async def test_read_agent_snapshot_accepts_fresh_aware_utc_timestamps(monkeypatch):
  checked_at = datetime(2026, 7, 13, 10, 0)
  aware_at = datetime(2026, 7, 13, 9, 59, 59, tzinfo=timezone.utc)
  status = BrokerPositionSnapshot(
    account_id="account-1",
    sequence=10,
    source="QMT_AGENT",
    reported_at=aware_at,
    received_at=aware_at,
    position_count=0,
    is_complete=True,
  )
  db = FakeDb([], status)

  async def fake_get_async_db():
    yield db

  monkeypatch.setattr(position_service_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(position_service_module, "utcnow", lambda: checked_at)

  result = await PositionService().read_agent_snapshot("account-1")

  assert result["sequence"] == 10
  assert result["is_complete"] is True


@pytest.mark.asyncio
async def test_mark_snapshot_failure_invalidates_prior_complete_status(monkeypatch):
  status = BrokerPositionSnapshot(
    account_id="account-1",
    sequence=20,
    source="QMT_AGENT",
    reported_at=datetime(2026, 7, 13, 10, 0),
    received_at=datetime(2026, 7, 13, 10, 0),
    is_complete=True,
  )
  db = FakeDb([], status)

  async def fake_get_async_db():
    yield db

  monkeypatch.setattr(position_service_module, "get_async_db", fake_get_async_db)
  await PositionService().mark_snapshot_failure("account-1", "agent disconnected")

  assert db.status.is_complete is False
  assert db.status.sequence == 20
  assert db.status.source == "QMT_AGENT"
  assert db.status.reported_at == datetime(2026, 7, 13, 10, 0)
  assert db.status.received_at == datetime(2026, 7, 13, 10, 0)
  assert db.status.last_error == "agent disconnected"
