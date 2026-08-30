from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
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
    execution_mode="paper",
    auto_exit_authorized=False,
    auto_exit_authorization_user_id=None,
    auto_exit_authorization_fingerprint=None,
    config_version=1,
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
  assert persisted_intent.metadata["owner_type"] == "EXIT_PLAN"
  assert persisted_intent.metadata["owner_id"] == plan.plan_id
  assert persisted_intent.metadata["exit_plan_id"] == plan.plan_id
  assert persisted_intent.run_id == (strategy_run_id or "")
  processor._route.assert_awaited_once()
