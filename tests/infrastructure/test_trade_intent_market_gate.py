from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.base import (
  ManualCommandIntentOrigin,
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
    plan=SimpleNamespace(
      plan_id="plan-1",
      account_id="account-1",
      instrument_code="600000.SH",
      strategy_run_id=None,
      execution_mode="paper",
    ),
    intent=TradeIntent(
      intent_id="intent-1",
      strategy_id="",
      run_id="",
      origin=ManualCommandIntentOrigin(
        command_id="liquidation-command-1",
        action_type="LIQUIDATE_POSITIONS",
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
