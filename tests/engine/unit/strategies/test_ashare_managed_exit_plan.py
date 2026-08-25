from datetime import datetime

import pytest
from quantx_domain.enums import StrategyRunMode
from quantx_domain.strategies.ashare_managed_exit_plan import (
  EXIT_PLAN_ENABLED_KEY,
  MANAGED_EXIT_PLAN_KEY,
  MANAGED_EXIT_RUNTIME_KEY,
  AshareManagedExitPlanStrategy,
)
from quantx_domain.strategies.base import (
  ManualCommandIntentOrigin,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyRunIntentOrigin,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_domain.trading.exit_plan import (
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
)

NOW = datetime(2026, 8, 25, 10, 0)


def _template() -> ExitPlanTemplate:
  return ExitPlanTemplate(
    plan_id="exit-plan-1",
    source_type="MANUAL_POSITION",
    source_id="position-1",
    account_id="account-1",
    instrument_code="600000.SH",
    bucket="manual",
    rules=[
      ExitRuleSpec(
        rule_id="target-1",
        strategy=ExitRuleType.TARGET_PRICE,
        parameters={"target_price": 10.5},
      )
    ],
    config_version=3,
  )


async def _strategy(mode: StrategyRunMode) -> AshareManagedExitPlanStrategy:
  strategy = AshareManagedExitPlanStrategy(
    StrategyContext(
      run_id="run-v3",
      mode=mode,
      instruments=["600000.SH"],
      parameters={
        MANAGED_EXIT_PLAN_KEY: _template().to_dict(),
        EXIT_PLAN_ENABLED_KEY: True,
        "account_id": "account-1",
        "initial_protected_volume": 1_000,
        "initial_entry_avg_price": 10.0,
        "initial_entry_time": NOW.isoformat(),
      },
      current_time=NOW,
    )
  )
  await strategy.initialize()
  return strategy


def _input() -> StrategyInput:
  return StrategyInput(
    run_id="run-v3",
    strategy_id="managed-exit-strategy",
    timestamp=NOW,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    market_data={
      "price": 10.6,
      "bid_price": [10.59],
      "ask_price": [10.61],
      "bid_vol": [1_000],
      "ask_vol": [900],
    },
  )


@pytest.mark.asyncio
async def test_fixed_exit_strategy_emits_stable_plan_origin_and_state_patch():
  strategy = await _strategy(StrategyRunMode.PAPER)

  output = await strategy.step(_input())

  [intent] = output.trade_intents
  assert intent.direction == TradeIntentDirection.SELL
  assert intent.execution_mode == TradeIntentExecutionMode.AUTO
  assert intent.run_id == "run-v3"
  assert isinstance(intent.origin, StrategyRunIntentOrigin)
  assert intent.origin.plan_id == "exit-plan-1"
  assert intent.metadata["exit_plan_id"] == "exit-plan-1"
  assert output.runtime_state_patch is not None
  assert (
    output.runtime_state_patch.set[MANAGED_EXIT_RUNTIME_KEY]["pending_intent_id"]
    == intent.intent_id
  )


@pytest.mark.asyncio
async def test_live_exit_defaults_to_manual_confirmation_without_exact_grant():
  strategy = await _strategy(StrategyRunMode.LIVE)

  [intent] = (await strategy.step(_input())).trade_intents

  assert intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM


def test_manual_command_intent_has_no_fake_strategy_run_identity():
  intent = TradeIntent(
    strategy_id="",
    run_id="",
    origin=ManualCommandIntentOrigin(
      command_id="liquidation-command-1",
      action_type="LIQUIDATE_POSITIONS",
      liquidation_group_id="group-1",
    ),
    instrument_code="600000.SH",
    direction=TradeIntentDirection.SELL,
    bucket="manual",
    reason="USER_LIQUIDATION",
    target_volume=1_000,
  )

  assert intent.run_id == ""
  assert intent.strategy_id == ""
  assert intent.origin.command_id == "liquidation-command-1"
