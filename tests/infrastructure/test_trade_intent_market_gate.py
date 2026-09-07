from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef, ExecutionOwnerType
from quantx_domain.strategies.base import (
  ExitPlanIntentOrigin,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentPriority,
)
from quantx_domain.trading import RiskAction
from quantx_domain.trading.exit_plan import ExitEvaluationContext
from quantx_infrastructure.services import trade_intent_processor as processor_module
from quantx_infrastructure.services.trade_intent_processor import (
  MARKET_DATA_STREAM_NOT_READY,
  TradeIntentProcessor,
)


def _manual_plan() -> SimpleNamespace:
  template = {
    "plan_id": "plan-1", "account_id": "account-1",
    "instrument_code": "600000.SH", "source_type": "MANUAL_POSITION",
    "source_id": "manual-1", "run_id": "",
  }
  return SimpleNamespace(
    **{key: value for key, value in template.items() if key != "run_id"},
    strategy_run_id=None,
    environment=ExecutionEnvironment.PAPER.value,
    source_execution_owner_type="MANUAL_COMMAND",
    source_execution_owner_id="manual-1",
    source_execution_environment=ExecutionEnvironment.PAPER.value,
    plan_state={"template": template},
  )


@pytest.mark.asyncio
async def test_route_rechecks_market_gate_immediately_before_place_order(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  place_order = AsyncMock()

  class TradingService:
    def __init__(self, **_kwargs):
      pass

    async def get_account_info(self):
      return SimpleNamespace(cash=100_000, frozen_cash=0, total_asset=100_000)

    async def place_order(self, **kwargs):
      return await place_order(**kwargs)

  class RiskChecker:
    def __init__(self, *_args, **_kwargs):
      pass

    async def evaluate_order(self, *_args, **_kwargs):
      return SimpleNamespace(
        allowed=True,
        action=RiskAction.ALLOW,
        final_volume=100,
        risk_decision_id="risk-1",
        reason_code="ALLOW",
        reason_detail="",
        risk_tags=[],
      )

  monkeypatch.setattr(processor_module, "TradingService", TradingService)
  monkeypatch.setattr(processor_module, "TradingRiskChecker", RiskChecker)
  processor = TradeIntentProcessor()
  processor._update_intent = AsyncMock()
  readiness = iter((True, False))

  result = await processor._route(
    plan=_manual_plan(),
    intent=TradeIntent(
      intent_id="intent-1",
      strategy_id="",
      run_id="",
      execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.EXIT_PLAN,
        "plan-1",
      ),
      origin=ExitPlanIntentOrigin(
        plan_id="plan-1",
        source_execution_ref=ExecutionOwnerRef.manual_command("manual-1"),
      ),
      instrument_code="600000.SH",
      direction=TradeIntentDirection.SELL,
      bucket="manual",
      reason="target_reached",
      priority=TradeIntentPriority.HIGH,
      target_volume=100,
    ),
    context=ExitEvaluationContext(
      timestamp=datetime(2026, 8, 19, 10, 0),
      current_price=10.0,
      bid_price=9.99,
      ask_price=10.0,
      limit_up=11.0,
      limit_down=9.0,
      price_tick=0.01,
      source="QMT_WHOLE_QUOTE",
    ),
    position=SimpleNamespace(
      volume=500,
      can_use_volume=300,
      frozen_volume=0,
      yesterday_volume=500,
    ),
    limit_price=9.99,
    market_ready=lambda: next(readiness),
  )

  assert result == {
    "success": False,
    "intent_id": "intent-1",
    "error": MARKET_DATA_STREAM_NOT_READY,
  }
  place_order.assert_not_awaited()
  processor._update_intent.assert_awaited_once()
  assert processor._update_intent.await_args.kwargs["status"] == "REJECTED"
  assert (
    processor._update_intent.await_args.kwargs["notes"]
    == MARKET_DATA_STREAM_NOT_READY
  )


@pytest.mark.asyncio
async def test_route_uses_canonical_exit_plan_idempotency_key(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  place_order = AsyncMock(
    return_value={
      "success": True,
      "client_order_id": "client-1",
      "status": "QUEUED",
    }
  )

  class TradingService:
    def __init__(self, **_kwargs):
      pass

    async def get_account_info(self):
      return SimpleNamespace(cash=100_000, frozen_cash=0, total_asset=100_000)

    async def place_order(self, **kwargs):
      return await place_order(**kwargs)

  class RiskChecker:
    def __init__(self, *_args, **_kwargs):
      pass

    async def evaluate_order(self, *_args, **_kwargs):
      return SimpleNamespace(
        allowed=True,
        action=RiskAction.ALLOW,
        final_volume=100,
        risk_decision_id="risk-1",
        reason_code="ALLOW",
        reason_detail="",
        risk_tags=[],
      )

  monkeypatch.setattr(processor_module, "TradingService", TradingService)
  monkeypatch.setattr(processor_module, "TradingRiskChecker", RiskChecker)
  processor = TradeIntentProcessor()
  processor._update_intent = AsyncMock()

  result = await processor._route(
    plan=_manual_plan(),
    intent=TradeIntent(
      intent_id="intent-1",
      strategy_id="",
      run_id="",
      instrument_code="600000.SH",
      direction=TradeIntentDirection.SELL,
      bucket="manual",
      reason="target_reached",
      priority=TradeIntentPriority.HIGH,
      target_volume=100,
      execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.EXIT_PLAN,
        "plan-1",
      ),
      origin=ExitPlanIntentOrigin(
        plan_id="plan-1",
        source_execution_ref=ExecutionOwnerRef.manual_command("manual-1"),
      ),
      metadata={
        "owner_type": "EXIT_PLAN",
        "owner_id": "plan-1",
        "exit_plan_id": "plan-1",
        "strategy_name": "legacy-strategy-label",
        "remark": "legacy-order-remark",
      },
    ),
    context=ExitEvaluationContext(
      timestamp=datetime(2026, 8, 19, 10, 0),
      current_price=10.0,
      bid_price=9.99,
      ask_price=10.0,
      limit_up=11.0,
      limit_down=9.0,
      price_tick=0.01,
      source="QMT_WHOLE_QUOTE",
    ),
    position=SimpleNamespace(
      volume=500,
      can_use_volume=300,
      frozen_volume=0,
      yesterday_volume=500,
    ),
    limit_price=9.99,
    market_ready=lambda: True,
  )

  assert result["success"] is True
  assert place_order.await_args.kwargs["idempotency_key"] == (
    "strategy-exit:plan-1:intent-1"
  )
  assert place_order.await_args.kwargs["execution_ref"] == ExecutionOwnerRef(
    ExecutionOwnerType.EXIT_PLAN,
    "plan-1",
  )
  assert place_order.await_args.kwargs["environment"] is ExecutionEnvironment.PAPER
  assert "strategy_name" not in place_order.await_args.kwargs
  assert "order_remark" not in place_order.await_args.kwargs
  assert all(
    key not in place_order.await_args.kwargs["execution_context"]
    for key in (
      "owner_type",
      "owner_id",
      "exit_plan_id",
      "strategy_name",
      "remark",
      "order_remark",
      "strategy_run_id",
    )
  )
