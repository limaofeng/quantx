"""Stopped independent source: next-day authorization, sizing and exit routing.

Platform/device/capacity reads are isolated; no real order is sent.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_contracts.agent import PROTOCOL_VERSION
from quantx_domain.trading.exit_plan import ExitEvaluationContext, ExitPlanTemplate
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  OrderCorrelation,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.auth import AuthDeviceSession
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.liquidation import ConditionalLiquidationOrder
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import (
  exit_plan_authorization_service as authorization_module,
)
from quantx_infrastructure.services import trade_command_service as command_module
from quantx_infrastructure.services import trade_intent_processor as processor_module
from quantx_infrastructure.services import trading_service as trading_module
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from sqlalchemy import select

from tests.infrastructure import test_t_trade_exit_authorization_derivation as auth

authorization_database = auth.authorization_database


@pytest.fixture(autouse=True)
def clock(monkeypatch):
  value = SimpleNamespace(now=datetime(2026, 9, 10, 10))
  monkeypatch.setattr(auth.time_utils, "now", lambda: value.now)
  monkeypatch.setattr(auth, "utcnow", lambda: value.now - timedelta(hours=8))
  return value


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "authorization_database", ["T_ASSISTANT_EXECUTION"], indirect=True
)
@pytest.mark.parametrize("revoke", [False, True])
@pytest.mark.parametrize(
  "durable,capacity_available", [(False, 500), (True, 500), (True, 0)]
)
async def test_next_day_original_plan_authorization_reaches_sized_order_port(
  authorization_database, monkeypatch, clock, revoke, durable, capacity_available
):
  factory = authorization_database
  monkeypatch.setattr(authorization_module, "AsyncSessionLocal", factory)
  monkeypatch.setattr(processor_module, "AsyncSessionLocal", factory)
  async with factory.kw["bind"].begin() as connection:
    await connection.run_sync(
      lambda db: ConditionalLiquidationOrder.__table__.create(db)
    )
  async with factory() as db, db.begin():
    record = await db.get(AutoExitPlanRecord, auth.PLAN_ID)
    record.plan_state = {
      **record.plan_state,
      "template": AutoExitPlanService._execution_plan_template(
        ExitPlanTemplate.from_dict(record.plan_state["template"]),
        ExecutionOwnerRef("T_ASSISTANT_EXECUTION", auth.RUN_ID),
        ExecutionEnvironment.LIVE,
      ).to_dict(),
    }
    (await db.get(Position, "position-1")).can_use_volume = 0
    # The original device session remains valid across the two simulated days.
    (await db.get(AuthDeviceSession, "session-1")).expires_at += timedelta(days=2)
  port = SimpleNamespace(
    get_account_info=AsyncMock(
      return_value=SimpleNamespace(cash=10000, frozen_cash=0, total_asset=16000)
    ),
    place_order=AsyncMock(
      return_value={
        "success": True,
        "status": "PENDING",
        "client_order_id": "exit-order",
      }
    ),
  )
  monkeypatch.setattr(processor_module, "TradingService", lambda **kwargs: port)
  if durable:
    async with factory.kw["bind"].begin() as connection:
      for model in (OrderCorrelation, TradeCommandOutbox, RuntimeComponentHeartbeat):
        await connection.run_sync(model.__table__.create)
    async with factory() as db, db.begin():
      db.add(
        RuntimeComponentHeartbeat(
          component="qmt-agent:device-1",
          instance_id="test-agent",
          status="READY",
          details={"capabilities": ["live"], "protocolVersion": PROTOCOL_VERSION},
          updated_at=clock.now - timedelta(hours=8),
        )
      )
    monkeypatch.setattr(trading_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(
      command_module, "utcnow", lambda: clock.now - timedelta(hours=8)
    )

    async def platform_control(service, account_id, **kwargs):
      return await service.db.get(AccountExecutionControl, account_id)

    monkeypatch.setattr(
      command_module.TradeCommandService,
      "_require_live_authorization",
      platform_control,
    )
    monkeypatch.setattr(
      command_module.TradeCommandService,
      "_device_for",
      AsyncMock(
        return_value=SimpleNamespace(id="device-1", user_id="user-1"),
      ),
    )
    capacity = SimpleNamespace(
      snapshot_id="isolated-snapshot",
      obligation_watermark="e" * 64,
      available_cash=Decimal("10000"),
      available_volume=500,
      unclaimed_volume=capacity_available,
      available_by_bucket={"swing": 500},
      unclaimed_by_bucket={"swing": capacity_available},
      protected_old_position_floor=0,
      old_inventory_claim_allocation={},
    )
    monkeypatch.setattr(
      command_module.AccountCapacityService, "read", AsyncMock(return_value=capacity)
    )
    port.place_order = AsyncMock(
      wraps=trading_module.TradingService(
        account_id=auth.ACCOUNT_ID,
        execution_mode="live",
      ).place_order
    )
  service = AutoExitPlanService()

  def context():
    return ExitEvaluationContext(
      timestamp=clock.now,
      current_price=10.8,
      bid_price=10.79,
      ask_price=10.8,
      limit_up=11,
      limit_down=9,
      price_tick=0.01,
      market_data_age_seconds=0,
      volume_data_age_seconds=0,
      source="QMT_WHOLE_QUOTE",
    )

  async with factory() as db:
    position = await db.get(Position, "position-1")
  result = await service.evaluate_and_submit(
    plan_id=auth.PLAN_ID,
    context=context(),
    position=position,
    market_session_open=True,
    market_ready=lambda: True,
  )
  assert result is None
  port.place_order.assert_not_awaited()
  async with factory() as db:
    record = await db.get(AutoExitPlanRecord, auth.PLAN_ID)
    assert not record.plan_state["pending_intent_id"]
    assert record.remaining_volume == 100

  clock.now = datetime(2026, 9, 11, 9, 31)
  async with factory() as db, db.begin():
    position = await db.get(Position, "position-1")
    position.can_use_volume = 500
    position.yesterday_volume = 600
    await db.flush()
    record = await db.get(AutoExitPlanRecord, auth.PLAN_ID)
    authorization = await auth.derive_exact_auto_exit_authorization_from_t_trade_entry(
      db,
      record,
      entry_intent_id=auth.INTENT_ID,
      challenge_id=auth.CHALLENGE_ID,
      cumulative_filled_volume=100,
    )
    assert authorization.valid, authorization
  if revoke:
    async with factory() as db, db.begin():
      (await db.get(AuthDeviceSession, "session-1")).revoked_at = clock.now - timedelta(
        hours=8
      )
  result = await service.evaluate_and_submit(
    plan_id=auth.PLAN_ID,
    context=context(),
    position=position,
    market_session_open=True,
    market_ready=lambda: True,
  )
  if revoke:
    assert result and result["awaiting_approval"]
    port.place_order.assert_not_awaited()
    async with factory() as db:
      record = await db.get(AutoExitPlanRecord, auth.PLAN_ID)
      assert not record.auto_exit_authorized and record.remaining_volume == 100
      assert record.source_execution_owner_id == auth.RUN_ID
      if durable:
        assert not list(await db.scalars(select(TradeCommandOutbox)))
    return
  async with factory() as db:
    failure = (await db.get(AutoExitPlanRecord, auth.PLAN_ID)).last_error
    if durable and not capacity_available:
      assert result is None and "ACCOUNT_SELL_CAPACITY_EXCEEDED" in failure
      for model in (PendingTradeOrder, TradeCommandOutbox, OrderCorrelation):
        assert not list(await db.scalars(select(model)))
      record = await db.get(AutoExitPlanRecord, auth.PLAN_ID)
      assert (
        record.remaining_volume == 100 and not record.plan_state["pending_intent_id"]
      )
      return
  assert result and result["success"] and not result.get("awaiting_approval"), (
    result,
    failure,
  )
  port.place_order.assert_awaited_once()
  submitted = port.place_order.call_args.kwargs
  assert submitted["execution_ref"] == ExecutionOwnerRef("EXIT_PLAN", auth.PLAN_ID)
  assert submitted["environment"] is ExecutionEnvironment.LIVE
  assert submitted["order_volume"] == 100
  async with factory() as db:
    intents = list(
      await db.scalars(
        select(TradeIntentRecord).where(TradeIntentRecord.direction == "SELL")
      )
    )
    assert len(intents) == 1
    assert intents[0].owner_type == "EXIT_PLAN" and intents[0].owner_id == auth.PLAN_ID
    assert intents[0].strategy_run_id is None
    assert intents[0].intent_metadata["source_business_id"] == auth.BATCH_ID
    assert intents[0].intent_metadata["intent_origin_type"] == "T_ASSISTANT_EXECUTION"
    assert "exit_metrics" in intents[0].intent_metadata
    record = await db.get(AutoExitPlanRecord, auth.PLAN_ID)
    assert record.source_execution_owner_id == auth.RUN_ID
    assert record.source_id == auth.BATCH_ID and record.remaining_volume == 100
    assert record.auto_exit_authorization_challenge_id == auth.CHALLENGE_ID
  if durable:
    queued_id = result["client_order_id"]
    # Replay the exact accepted request through the public durable boundary.
    replay = await port.place_order(**submitted)
    assert replay["client_order_id"] == queued_id
    async with factory() as db:
      pending = (await db.scalars(select(PendingTradeOrder))).one()
      outbox = (await db.scalars(select(TradeCommandOutbox))).one()
      correlation = (await db.scalars(select(OrderCorrelation))).one()
      for row in (pending, outbox, correlation):
        assert row.client_order_id == queued_id
        assert row.owner_type == "EXIT_PLAN" and row.owner_id == auth.PLAN_ID
        assert row.environment == "LIVE"
      assert pending.strategy_run_id is None and correlation.strategy_run_id is None
      assert pending.batch_id == auth.BATCH_ID and pending.t_trade_role == "EXIT"
      assert pending.bucket == "swing" and correlation.bucket == "swing"
      assert "source_business_id" not in pending.request_metadata
      assert "exit_metrics" not in pending.request_metadata
      assert pending.request_metadata["exact_auto_exit_authorized"] is True
      assert pending.volume == 100 and pending.status == "QUEUED"
      assert outbox.delivery_status == "QUEUED" and outbox.attempts == 0
      assert outbox.delivered_at is None and outbox.acknowledged_at is None
      assert outbox.payload["side"] == "SELL" and outbox.payload["volume"] == 100
