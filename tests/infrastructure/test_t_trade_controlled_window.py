from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AgentDevice,
  PendingTradeOrder,
  TradeCommandOutbox,
  TTradeBatch,
  TTradeRollout,
  TTradeRolloutEvent,
)
from quantx_infrastructure.services import (
  t_trade_operations_service as operations_module,
)
from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationsService,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _account_safety() -> dict:
  return {
    "account_id": "account-1",
    "authorization_state": "ENABLED",
    "health_status": "HEALTHY",
    "execution_mode": "TRADING",
    "can_increase_risk": True,
    "can_reduce_risk": True,
    "checks": [
      {
        "code": "ACCOUNT_RISK_INCREASE_AUTHORIZED",
        "passed": True,
        "message": "",
        "scope": "INCREASE_RISK",
      }
    ],
    "engine_status": "READY",
    "agent_status": "READY",
    "agent_mode": "live",
    "protocol_version": "1.1",
    "reconcile_status": "READY",
    "execution_window_active": True,
    "controlled_window_snapshot_id": "snapshot-1",
    "snapshot_id": "snapshot-1",
    "snapshot_hash": "a" * 64,
  }


class _ReadinessDb:
  async def get(self, model, account_id, **_kwargs):
    assert account_id == "account-1"
    if model is TTradeRollout:
      return SimpleNamespace(
        stage="SHADOW",
        enabled=False,
        policy_version=3,
        acknowledged_policy_version=0,
      )
    assert model is AccountExecutionControl
    return SimpleNamespace(
      last_snapshot_id="snapshot-1",
      last_snapshot_hash="a" * 64,
    )


@pytest.mark.asyncio
async def test_t_switch_is_feature_local_in_composed_readiness(monkeypatch) -> None:
  class AccountSafety:
    async def status(self, account_id):
      assert account_id == "account-1"
      return _account_safety()

  @asynccontextmanager
  async def sessions():
    yield _ReadinessDb()

  monkeypatch.setattr(operations_module, "AccountExecutionSafetyService", AccountSafety)
  monkeypatch.setattr(operations_module, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(operations_module.settings, "t_trade_live_enabled", False)
  service = TTradeOperationsService()
  service.rollout_evidence_evaluator = SimpleNamespace(
    evaluate=AsyncMock(return_value={"checks": [], "summary": {}})
  )

  readiness = await service.readiness("account-1")

  assert readiness["account_safety"]["can_increase_risk"] is True
  assert readiness["automation_ready"] is False
  assert readiness["feature_checks"][0]["code"] == "T_TRADE_LIVE_ENABLED"
  assert readiness["feature_checks"][0]["passed"] is False
  assert all(
    item["code"] != "T_TRADE_LIVE_ENABLED"
    for item in readiness["account_safety"]["checks"]
  )


@pytest.fixture
async def rollout_database(tmp_path, monkeypatch):
  engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't-rollout.db'}")
  tables = [
    AccountExecutionControl.__table__,
    AgentDevice.__table__,
    TTradeRollout.__table__,
    TTradeRolloutEvent.__table__,
    TTradeBatch.__table__,
    PendingTradeOrder.__table__,
    TradeCommandOutbox.__table__,
  ]
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(sync, tables=tables)
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(operations_module, "AsyncSessionLocal", sessions)
  async with sessions() as db:
    db.add_all(
      [
        AccountExecutionControl(
          account_id="account-1",
          authorization_state="ENABLED",
          state_version=7,
          reconcile_status="READY",
          last_snapshot_id="snapshot-1",
          last_snapshot_hash="a" * 64,
          last_snapshot_at=utcnow(),
          controlled_window_active=True,
          controlled_window_snapshot_id="snapshot-1",
          controlled_window_snapshot_hash="a" * 64,
        ),
        TTradeRollout(
          account_id="account-1",
          stage="LIVE",
          enabled=True,
          policy_version=3,
          acknowledged_policy_version=3,
        ),
        TradeCommandOutbox(
          message_id="manual-message",
          client_order_id="manual-order",
          idempotency_key="manual-order",
          device_id="device-1",
          account_id="account-1",
          payload={"command_kind": "PLACE_ORDER", "source": "manual"},
          delivery_status="QUEUED",
          expires_at=utcnow(),
          attempts=0,
        ),
        TTradeBatch(
          batch_id="t-batch-1",
          account_id="account-1",
          instrument_code="600000.SH",
          strategy_run_id="strategy-run-1",
          status="AWAITING_ENTRY_APPROVAL",
          entry_client_order_id="t-order-1",
        ),
        PendingTradeOrder(
          client_order_id="t-order-1",
          user_id="user-1",
          account_id="account-1",
          instrument_code="600000.SH",
          side="BUY",
          order_type="FIX_PRICE",
          limit_price="10.00",
          volume=100,
          status="QUEUED",
          execution_mode="live",
          batch_id="t-batch-1",
          bucket="swing",
          t_trade_role="ENTRY",
        ),
        TradeCommandOutbox(
          message_id="t-message",
          client_order_id="t-order-1",
          idempotency_key="t-order-1",
          device_id="device-1",
          account_id="account-1",
          payload={"command_kind": "PLACE_ORDER", "source": "t-trade"},
          delivery_status="QUEUED",
          expires_at=utcnow(),
          attempts=0,
        ),
      ]
    )
    await db.commit()
  yield sessions
  await engine.dispose()


@pytest.mark.asyncio
async def test_pause_changes_only_t_rollout(rollout_database) -> None:
  service = TTradeOperationsService()
  service.readiness = AsyncMock(return_value={"status": "PREPARING"})

  await service.pause("account-1", "pause T assistant", user_id="user-1")

  async with rollout_database() as db:
    account = await db.get(AccountExecutionControl, "account-1")
    rollout = await db.get(TTradeRollout, "account-1")
    assert account.authorization_state == "ENABLED"
    assert account.state_version == 7
    assert account.controlled_window_active is True
    assert rollout.stage == "PAUSED"
    assert rollout.enabled is False


@pytest.mark.asyncio
async def test_t_stop_does_not_trigger_account_kill_or_cancel_manual_orders(
  rollout_database,
) -> None:
  service = TTradeOperationsService()
  service.readiness = AsyncMock(return_value={"status": "PREPARING"})

  await service.kill(
    "account-1",
    "stop only T assistant",
    user_id="user-1",
    operation_id="t-stop-1",
  )

  async with rollout_database() as db:
    account = await db.get(AccountExecutionControl, "account-1")
    rollout = await db.get(TTradeRollout, "account-1")
    manual = await db.get(TradeCommandOutbox, "manual-message")
    t_command = await db.get(TradeCommandOutbox, "t-message")
    t_pending = await db.get(PendingTradeOrder, "t-order-1")
    events = (await db.execute(select(TTradeRolloutEvent))).scalars().all()
    outbox = (await db.execute(select(TradeCommandOutbox))).scalars().all()
    assert account.authorization_state == "ENABLED"
    assert account.controlled_window_active is True
    assert rollout.stage == "PAUSED"
    assert manual.delivery_status == "QUEUED"
    assert t_command.delivery_status == "CANCELLED_KILL"
    assert t_pending.status == "CANCELLED"
    assert [event.event_type for event in events] == ["T_TRADE_STOPPED"]
    assert all(item.payload.get("command_kind") != "EMERGENCY_STOP" for item in outbox)


@pytest.mark.asyncio
async def test_reconciliation_update_delegates_to_account_control(monkeypatch) -> None:
  mark = AsyncMock()

  class AccountSafety:
    mark_reconciled = mark

  monkeypatch.setattr(operations_module, "AccountExecutionSafetyService", AccountSafety)
  await TTradeOperationsService().mark_reconciled(
    "account-1",
    ready=False,
    reason="snapshot mismatch",
  )
  mark.assert_awaited_once_with(
    "account-1",
    ready=False,
    reason="snapshot mismatch",
  )
