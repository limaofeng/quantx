from __future__ import annotations

from types import SimpleNamespace

import pytest
from quantx_infrastructure.core.runtime_state_manager import (
  BUCKET_LEDGER_CUSTOM_STATE_KEY,
  BUCKET_LEDGER_RECONCILE_REQUIRED_KEY,
  BUCKET_LEDGER_VIOLATIONS_KEY,
  RUNTIME_RECONCILIATION_REASON_KEY,
  RUNTIME_RECONCILIATION_STATUS_KEY,
  RUNTIME_SNAPSHOT_ATTEMPT_KEY,
  RuntimeStateManager,
  RuntimeStateRestoreError,
  RuntimeStateRestoreStatus,
)


def _position(*, total: int = 100, available: int = 80) -> SimpleNamespace:
  return SimpleNamespace(
    instrument_code="600000.SH",
    to_dict=lambda: {
      "instrument_code": "600000.SH",
      "long_volume": total,
      "short_volume": 0,
      "available_volume": available,
      "frozen_volume": 0,
      "today_buy_volume": total - available,
      "long_avg_price": 10.0,
      "short_avg_price": 0.0,
      "market_value": total * 10.0,
      "pnl": 0.0,
      "last_price": 10.0,
    },
  )


def _ledger_snapshot(*, total: int = 70, available: int = 40) -> dict:
  return {
    "run_id": "run-restore",
    "instruments": {
      "600000.SH": {
        "core": {
          "bucket": "core",
          "total_volume": total,
          "available_volume": available,
          "frozen_volume": 0,
          "today_buy_volume": total - available,
          "market_value": total * 10.0,
          "avg_price": 10.0,
          "last_price": 10.0,
        }
      }
    },
    "pending_orders": {},
    "pending_substitutions": {},
  }


@pytest.mark.asyncio
async def test_restore_query_failure_raises_then_a_later_query_can_recover(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as repository_module,
  )

  attempts = 0

  async def fake_get_async_db():
    yield object()

  class FakeStateRepository:
    def __init__(self, _db):
      pass

    async def get_state(self, _run_id):
      nonlocal attempts
      attempts += 1
      if attempts == 1:
        raise OSError("temporary database outage")
      return None

  class FakePositionRepository:
    def __init__(self, _db):
      pass

    async def get_all_positions(self, _run_id):
      return []

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    repository_module,
    "StrategyRunStateRepository",
    FakeStateRepository,
  )
  monkeypatch.setattr(
    repository_module,
    "StrategyRunPositionRepository",
    FakePositionRepository,
  )

  manager = RuntimeStateManager(run_id="run-restore", persist_enabled=True)

  with pytest.raises(RuntimeStateRestoreError, match="状态恢复查询失败"):
    await manager.restore()
  assert manager._running is False
  assert manager._snapshot_task is None

  recovered = await manager.restore()
  assert recovered.status == RuntimeStateRestoreStatus.NOT_FOUND
  assert recovered.state["positions"] == {}
  assert attempts == 2


@pytest.mark.asyncio
async def test_restore_rejects_orphan_position_rows_without_state_record(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as repository_module,
  )

  async def fake_get_async_db():
    yield object()

  class FakeStateRepository:
    def __init__(self, _db):
      pass

    async def get_state(self, _run_id):
      return None

  class FakePositionRepository:
    def __init__(self, _db):
      pass

    async def get_all_positions(self, _run_id):
      return [_position()]

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    repository_module,
    "StrategyRunStateRepository",
    FakeStateRepository,
  )
  monkeypatch.setattr(
    repository_module,
    "StrategyRunPositionRepository",
    FakePositionRepository,
  )

  manager = RuntimeStateManager(run_id="run-orphan", persist_enabled=True)

  with pytest.raises(RuntimeStateRestoreError, match="状态主记录缺失"):
    await manager.restore()
  assert manager._state["positions"] == {}
  assert manager._running is False
  assert manager._snapshot_task is None


@pytest.mark.asyncio
async def test_strategy_checkpoint_cannot_clear_manager_owned_reconcile_gate() -> None:
  manager = RuntimeStateManager(
    run_id="run-protected-reconcile-gate",
    persist_enabled=False,
  )
  manager._state["custom"] = {
    RUNTIME_RECONCILIATION_STATUS_KEY: "RECONCILE_REQUIRED",
    RUNTIME_RECONCILIATION_REASON_KEY: "BUCKET_LEDGER_INVARIANT_BROKEN",
    BUCKET_LEDGER_RECONCILE_REQUIRED_KEY: True,
    BUCKET_LEDGER_VIOLATIONS_KEY: ["600000.SH.total_volume"],
  }

  assert await manager.checkpoint_durable_runtime_event(
    "trade:protected-gate",
    custom_updates={
      RUNTIME_RECONCILIATION_STATUS_KEY: "READY",
      RUNTIME_RECONCILIATION_REASON_KEY: "strategy-overwrite",
      BUCKET_LEDGER_RECONCILE_REQUIRED_KEY: False,
      BUCKET_LEDGER_VIOLATIONS_KEY: [],
      "strategy_value": 42,
    },
  )

  assert manager.reconciliation_status() == "RECONCILE_REQUIRED"
  assert manager.get_custom(RUNTIME_RECONCILIATION_REASON_KEY) == (
    "BUCKET_LEDGER_INVARIANT_BROKEN"
  )
  assert manager.get_custom(BUCKET_LEDGER_VIOLATIONS_KEY) == [
    "600000.SH.total_volume"
  ]
  assert manager.get_custom("strategy_value") == 42


@pytest.mark.asyncio
async def test_restore_keeps_authoritative_positions_and_installs_reconcile_gate(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as repository_module,
  )

  state_record = SimpleNamespace(
    version=5,
    cash=9_000.0,
    frozen_cash=0.0,
    total_asset=10_000.0,
    custom_state={
      BUCKET_LEDGER_CUSTOM_STATE_KEY: _ledger_snapshot(total=70, available=40)
    },
  )

  async def fake_get_async_db():
    yield object()

  class FakeStateRepository:
    def __init__(self, _db):
      pass

    async def get_state(self, _run_id):
      return state_record

  class FakePositionRepository:
    def __init__(self, _db):
      pass

    async def get_all_positions(self, _run_id):
      return [_position(total=100, available=80)]

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    repository_module,
    "StrategyRunStateRepository",
    FakeStateRepository,
  )
  monkeypatch.setattr(
    repository_module,
    "StrategyRunPositionRepository",
    FakePositionRepository,
  )

  manager = RuntimeStateManager(run_id="run-restore", persist_enabled=True)
  restored = await manager.restore()

  assert restored.status == RuntimeStateRestoreStatus.RESTORED
  assert manager.requires_reconciliation() is True
  assert manager.reconciliation_status() == "RECONCILE_REQUIRED"
  assert manager.get_position("600000.SH")["long_volume"] == 100
  assert manager.get_position("600000.SH")["available_volume"] == 80
  assert (
    manager.get_bucket_ledger_snapshot()["instruments"]["600000.SH"]["core"][
      "total_volume"
    ]
    == 70
  )
  assert manager.get_custom("runtime_reconciliation_reason") == (
    "BUCKET_LEDGER_INVARIANT_BROKEN"
  )
  assert manager._dirty is True

  state_record.custom_state[RUNTIME_SNAPSHOT_ATTEMPT_KEY] = "snapshot-attempt"
  snapshot_manager = RuntimeStateManager(
    run_id="run-restore",
    persist_enabled=True,
  )
  snapshot_manager._dirty = True
  snapshot_manager._dirty_revision = 2
  assert (
    await snapshot_manager._reconcile_snapshot_attempt(
      "snapshot-attempt",
      snapshot_revision=2,
      expected_version=5,
    )
    is True
  )
  assert snapshot_manager.requires_reconciliation() is True
  assert snapshot_manager.get_position("600000.SH")["long_volume"] == 100
  assert (
    snapshot_manager.get_bucket_ledger_snapshot()["instruments"]["600000.SH"][
      "core"
    ]["total_volume"]
    == 70
  )
  assert snapshot_manager._dirty is True


@pytest.mark.asyncio
async def test_commit_unknown_adoption_preserves_mismatch_and_reconcile_gate(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  from quantx_infrastructure.database import connection as connection_module
  from quantx_infrastructure.repositories import (
    strategy_run_state_repository as repository_module,
  )

  state_record = SimpleNamespace(
    version=6,
    cash=9_000.0,
    frozen_cash=0.0,
    total_asset=10_000.0,
    custom_state={
      "applied_runtime_event_keys": ["trade:committed"],
      BUCKET_LEDGER_CUSTOM_STATE_KEY: _ledger_snapshot(total=70, available=40),
    },
  )

  async def fake_get_async_db():
    yield object()

  class FakeStateRepository:
    def __init__(self, _db):
      pass

    async def get_state(self, _run_id):
      return state_record

  class FakePositionRepository:
    def __init__(self, _db):
      pass

    async def get_all_positions(self, _run_id):
      return [_position(total=100, available=80)]

  monkeypatch.setattr(connection_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(
    repository_module,
    "StrategyRunStateRepository",
    FakeStateRepository,
  )
  monkeypatch.setattr(
    repository_module,
    "StrategyRunPositionRepository",
    FakePositionRepository,
  )

  manager = RuntimeStateManager(run_id="run-restore", persist_enabled=True)
  manager._dirty = True
  manager._dirty_revision = 1
  manager._last_snapshot_attempt_revision = 1

  assert await manager._adopt_committed_runtime_event("trade:committed") is True
  assert manager.requires_reconciliation() is True
  assert manager.get_position("600000.SH")["long_volume"] == 100
  assert manager.get_position("600000.SH")["available_volume"] == 80
  assert (
    manager.get_bucket_ledger_snapshot()["instruments"]["600000.SH"]["core"][
      "total_volume"
    ]
    == 70
  )
  assert manager._dirty is True


def test_market_continuity_gate_does_not_freeze_bucket_ledger_updates() -> None:
  manager = RuntimeStateManager(
    run_id="run-market-gate-ledger-sync",
    persist_enabled=False,
  )
  manager.require_market_continuity_reconciliation(
    "600000.SH",
    "MARKET_EVENT_QUEUE_OVERFLOW",
  )

  manager.update_position(
    "600000.SH",
    long_volume=120,
    short_volume=0,
    available_volume=90,
    frozen_volume=0,
    today_buy_volume=30,
    long_avg_price=10.0,
    short_avg_price=0.0,
    market_value=1_200.0,
    pnl=0.0,
    last_price=10.0,
  )

  assert manager.requires_reconciliation() is True
  assert manager.requires_bucket_reconciliation() is False
  assert manager.get_position("600000.SH")["long_volume"] == 120
  assert (
    manager.get_bucket_ledger_snapshot()["instruments"]["600000.SH"]["core"][
      "total_volume"
    ]
    == 120
  )
