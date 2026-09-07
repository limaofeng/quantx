from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_infrastructure.services.trade_intent_processor as processor_module
from quantx_contracts import ExecutionOwnerRef, ExecutionOwnerType
from quantx_domain.trading import RiskAction
from quantx_domain.trading.exit_plan import ExitDecision, ExitEvaluationContext
from quantx_infrastructure.services.trade_intent_processor import (
  TradeIntentProcessor,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_run_id", ["run-1", None])
async def test_sell_intent_is_owned_by_exit_plan(strategy_run_id) -> None:
  processor = TradeIntentProcessor()
  processor._create_intent_record = AsyncMock()
  processor._route = AsyncMock(return_value={"success": True})
  plan = SimpleNamespace(
    plan_id="exit-plan-1",
    strategy_run_id=strategy_run_id,
    strategy_id="strategy-1",
    account_id="account-1",
    instrument_code="600000.SH",
    bucket="swing",
    source_type="T_TRADE_BATCH" if strategy_run_id else "MANUAL_POSITION",
    source_id="t-batch-1" if strategy_run_id else "manual-plan-1",
    group_id=None,
    completion_strategy=None,
    environment="PAPER",
    auto_exit_authorized=False,
    auto_exit_authorization_user_id=None,
    auto_exit_authorization_fingerprint=None,
    config_version=1,
    source_execution_owner_type=(
      "STRATEGY_RUN" if strategy_run_id else "MANUAL_COMMAND"
    ),
    source_execution_owner_id=strategy_run_id or "manual-plan-1",
    source_execution_environment="PAPER",
    plan_state={
      "template": {
        "plan_id": "exit-plan-1",
        "account_id": "account-1",
        "instrument_code": "600000.SH",
        "source_type": (
          "T_TRADE_BATCH" if strategy_run_id else "MANUAL_POSITION"
        ),
        "source_id": "t-batch-1" if strategy_run_id else "manual-plan-1",
        "run_id": strategy_run_id or "",
        "metadata": {},
      }
    },
  )
  decision = ExitDecision(
    plan_id=plan.plan_id,
    rule_id="hard-stop",
    rule_type="HARD_STOP",
    reason="hard stop",
    volume=100,
    priority=1_000,
  )

  await processor.process_exit_decision(
    plan=plan,
    decision=decision,
    intent_id="intent-1",
    context=ExitEvaluationContext(
      timestamp=datetime(2026, 8, 19, 10, 0),
      current_price=9.8,
      bid_price=9.79,
      ask_price=9.8,
      source="QMT_WHOLE_QUOTE",
    ),
    position=SimpleNamespace(volume=100, can_use_volume=100),
    limit_price=9.79,
    market_ready=lambda: True,
  )

  persisted_intent = processor._create_intent_record.await_args.args[1]
  assert persisted_intent.execution_ref == ExecutionOwnerRef(
    ExecutionOwnerType.EXIT_PLAN,
    plan.plan_id,
  )
  assert "owner_type" not in persisted_intent.metadata
  assert "owner_id" not in persisted_intent.metadata
  assert "exit_plan_id" not in persisted_intent.metadata
  assert persisted_intent.run_id == (strategy_run_id or "")
  processor._route.assert_awaited_once()


@pytest.mark.asyncio
async def test_public_t_exit_route_preserves_exit_role_and_source_batch(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  captured: dict = {}

  class TradingService:
    def __init__(self, **_kwargs):
      pass

    async def get_account_info(self):
      return SimpleNamespace(cash=0, frozen_cash=0, total_asset=100_000)

    async def place_order(self, **kwargs):
      captured.update(kwargs)
      return {"client_order_id": "client-exit", "status": "QUEUED"}

  class OrderSizer:
    def __init__(self, _rules):
      pass

    def draft_intent(self, *_args):
      return SimpleNamespace(
        sized_volume=100,
        size_reason_codes=[],
        draft_id="draft-exit",
      )

  class RiskChecker:
    def __init__(self, *_args, **_kwargs):
      pass

    async def evaluate_order(self, request, **_kwargs):
      return SimpleNamespace(
        allowed=True,
        final_volume=request.volume,
        action=RiskAction.ALLOW,
        risk_decision_id="risk-exit",
        reason_code="ALLOW",
        reason_detail="allowed",
        risk_tags=[],
      )

  monkeypatch.setattr(processor_module, "TradingService", TradingService)
  monkeypatch.setattr(processor_module, "OrderSizer", OrderSizer)
  monkeypatch.setattr(processor_module, "TradingRiskChecker", RiskChecker)
  plan = SimpleNamespace(
    plan_id="exit-plan-t",
    strategy_run_id="run-1",
    strategy_id="strategy-1",
    account_id="account-1",
    instrument_code="600000.SH",
    bucket="swing",
    source_type="T_TRADE_BATCH",
    source_id="t-batch-1",
    group_id=None,
    completion_strategy=None,
    environment="PAPER",
    auto_exit_authorized=False,
    auto_exit_authorization_user_id=None,
    auto_exit_authorization_fingerprint=None,
    config_version=1,
    source_execution_owner_type="STRATEGY_RUN",
    source_execution_owner_id="run-1",
    source_execution_environment="PAPER",
    plan_state={
      "template": {
        "plan_id": "exit-plan-t",
        "account_id": "account-1",
        "instrument_code": "600000.SH",
        "source_type": "T_TRADE_BATCH",
        "source_id": "t-batch-1",
        "run_id": "run-1",
        "metadata": {},
      }
    },
  )
  processor = TradeIntentProcessor()
  processor._create_intent_record = AsyncMock()
  processor._update_intent = AsyncMock()

  result = await processor.process_exit_decision(
    plan=plan,
    decision=ExitDecision(
      plan_id=plan.plan_id,
      rule_id="hard-stop",
      rule_type="HARD_STOP",
      reason="hard stop",
      volume=100,
      priority=1_000,
    ),
    intent_id="intent-exit",
    context=ExitEvaluationContext(
      timestamp=datetime(2026, 9, 3, 10),
      current_price=10,
      bid_price=9.99,
      ask_price=10,
      price_tick=0.01,
      source="QMT_WHOLE_QUOTE",
    ),
    position=SimpleNamespace(
      volume=100,
      can_use_volume=100,
      frozen_volume=0,
      yesterday_volume=100,
    ),
    limit_price=9.96,
    market_ready=lambda: True,
  )

  assert result["client_order_id"] == "client-exit"
  context = captured["execution_context"]
  assert context["t_trade_role"] == "exit"
  assert context["t_batch_id"] == "t-batch-1"
  assert context["exit_policy_version"] == "TExitOrderPolicy.v1"
  assert context["t_exit_order_policy_version"] == "TExitOrderPolicy.v1"
  assert context["t_exit_order_ttl_seconds"] == 30
  assert context["t_exit_total_ttl_seconds"] == 90
  assert context["t_exit_max_replace_count"] == 2
  assert context["t_exit_max_slippage_bps"] == 30
  assert context["price_type"] == "FIX_PRICE"
  assert context["price_reference"] == "BID"
  assert context["protected_limit"] is True
  assert context["max_exit_slippage_bps"] == 30
  assert context["config_version"] == 1
