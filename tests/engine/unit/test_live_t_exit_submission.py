"""Stopped independent source: next-day authorization, sizing and exit routing.

Only TradingService account/order IO is a spy; no real order is sent.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.trading.exit_plan import ExitEvaluationContext, ExitPlanTemplate
from quantx_infrastructure.models.auth import AuthDeviceSession
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.liquidation import ConditionalLiquidationOrder
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import (
  exit_plan_authorization_service as authorization_module,
)
from quantx_infrastructure.services import trade_intent_processor as processor_module
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
async def test_next_day_original_plan_authorization_reaches_sized_order_port(
  authorization_database, monkeypatch, clock, revoke
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
    return
  assert result and result["success"] and not result.get("awaiting_approval"), result
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
    record = await db.get(AutoExitPlanRecord, auth.PLAN_ID)
    assert record.source_execution_owner_id == auth.RUN_ID
    assert record.source_id == auth.BATCH_ID and record.remaining_volume == 100
    assert record.auto_exit_authorization_challenge_id == auth.CHALLENGE_ID
