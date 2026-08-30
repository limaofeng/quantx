from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.base import StrategyContext
from quantx_domain.trading import MarketDataSnapshot
from quantx_domain.trading.exit_plan import (
  ExitPlanBook,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
  ExitT1Policy,
)
from quantx_engine.strategy_executor import StrategyExecutor
from quantx_infrastructure.models.enums import StrategyRunMode


def _runtime(parameters):
  class RuntimeExitPlanOwner:
    OWNS_RUNTIME_EXIT_PLAN_BOOK = True

  return SimpleNamespace(
    context=StrategyContext(
      run_id="exit-replay-1",
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters=parameters,
    ),
    strategy=None,
    strategy_class=RuntimeExitPlanOwner,
    state_manager=None,
    durable_event_barrier_key=None,
    exit_plan_recovery_intents={},
  )


def test_exit_plan_replay_fails_fast_on_market_event_errors() -> None:
  assert (
    StrategyExecutor._requires_replay_event_integrity(
      _runtime({"exit_plan_replay": True})
    )
    is True
  )


@pytest.mark.asyncio
async def test_exit_plan_replay_does_not_use_t_trade_end_force_close(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  wait_for_reports = AsyncMock()
  monkeypatch.setattr(executor, "_wait_for_backtest_reports", wait_for_reports)
  runtime = _runtime({"exit_plan_replay": True})
  runtime.broker = None

  await executor._finalize_t_trade_replay(runtime)

  wait_for_reports.assert_not_awaited()


@pytest.mark.asyncio
async def test_adaptive_exit_replay_rejects_missing_depth() -> None:
  executor = StrategyExecutor()
  template = ExitPlanTemplate(
    plan_id="plan-1",
    source_type="T_TRADE_BATCH",
    source_id="batch-1",
    account_id="account-1",
    instrument_code="000001.SZ",
    bucket="swing",
    run_id="exit-replay-1",
    strategy_id="strategy-1",
    rules=[
      ExitRuleSpec(
        rule_id="adaptive",
        strategy=ExitRuleType.ADAPTIVE_VOLUME_PRICE_TRAILING,
        parameters={"arm_target_profit_pct": 2.0},
      )
    ],
    t1_policy=ExitT1Policy.WAIT_UNTIL_SELLABLE,
    auto_exit_authorized=True,
  )
  book = ExitPlanBook()
  book.register_entry_fill(template, volume=100, price=10.0)
  runtime = _runtime({"exit_plan_replay": True})
  runtime.exit_plan_book = book
  market = MarketDataSnapshot(
    instrument_code="000001.SZ",
    timestamp=datetime(2026, 8, 20, 10, 0),
    price=10.0,
    bid_price=[],
    ask_price=[],
    bid_vol=[],
    ask_vol=[],
    source="tick",
  )

  with pytest.raises(RuntimeError, match="EXIT_PLAN_REPLAY_DEPTH_DATA_MISSING"):
    await executor._process_auto_exit_plans(
      runtime,
      instrument_code="000001.SZ",
      timestamp=market.timestamp,
      market_data=market,
    )
