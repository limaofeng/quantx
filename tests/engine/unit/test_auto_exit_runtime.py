from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.base import (
  OrderStateEvent,
  StrategyContext,
  StrategyRunMode,
  TradeExecutionEvent,
  TradeIntentDirection,
  TradeIntentPriority,
)
from quantx_domain.trading.exit_plan import (
  EXIT_PLAN_BOOK_STATE_KEY,
  ExitEvaluationContext,
  ExitPlanStatus,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
  ExitT1Policy,
)
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)


class FakeStateManager:
  def __init__(self):
    self.custom = {}
    self.records = []

  def set_custom(self, key, value):
    self.custom[key] = value

  async def record_trade_intent(self, intent, status="PENDING"):
    self.records.append((intent, status))


def make_runtime():
  context = StrategyContext(
    run_id="run-auto-exit",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="auto-exit",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = FakeStateManager()
  return runtime


def make_template():
  return ExitPlanTemplate(
    plan_id="exit-plan-1",
    source_type="LIMIT_UP_ENTRY",
    source_id="board-batch-1",
    account_id="account-1",
    instrument_code="600000.SH",
    bucket="swing",
    run_id="run-auto-exit",
    strategy_id="1",
    rules=[
      ExitRuleSpec(
        rule_id="hard-stop",
        strategy=ExitRuleType.HARD_STOP,
        priority=1000,
        parameters={"stop_loss_pct": -1.0},
      )
    ],
    t1_policy=ExitT1Policy.WAIT_UNTIL_SELLABLE,
    auto_exit_authorized=True,
  )


@pytest.mark.asyncio
async def test_engine_registers_filled_entry_and_routes_generic_exit_intent():
  executor = StrategyExecutor()
  runtime = make_runtime()
  executor.runs[runtime.run_id] = runtime
  executor._process_trade_intent = AsyncMock()
  template = make_template()

  await executor._notify_strategy_trade(
    runtime,
    TradeExecutionEvent(
      order_id="entry-order",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10.0,
      volume=100,
      trade_time=datetime(2026, 7, 30, 9, 35),
      metadata={"exit_plan_template": template.to_dict()},
    ),
  )

  plan = runtime.exit_plan_book.plans[template.plan_id]
  assert plan.status == ExitPlanStatus.ACTIVE
  assert plan.entry_filled_volume == 100
  assert EXIT_PLAN_BOOK_STATE_KEY in runtime.state_manager.custom

  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 7, 30, 10, 0),
    market_data=MarketDataSnapshot(
      instrument_code="600000.SH",
      price=9.8,
      bid_price=[9.79],
      ask_price=[9.8],
    ),
  )

  routed = executor._process_trade_intent.await_args.args[1]
  assert routed.direction == TradeIntentDirection.SELL
  assert routed.target_volume == 100
  assert routed.metadata["exit_plan_id"] == template.plan_id
  assert routed.metadata["exit_rule_type"] == ExitRuleType.HARD_STOP.value
  assert routed.metadata["allow_t1_substitution"] is False
  assert routed.max_price_deviation_bps == 30.0
  assert plan.status == ExitPlanStatus.EXIT_PENDING


@pytest.mark.asyncio
async def test_rejected_generic_exit_returns_plan_to_monitoring():
  executor = StrategyExecutor()
  runtime = make_runtime()
  template = make_template()
  plan = runtime.exit_plan_book.register_entry_fill(
    template,
    volume=100,
    price=10.0,
  )
  [decision] = runtime.exit_plan_book.evaluate(
    "600000.SH",
    ExitEvaluationContext(
      timestamp=datetime(2026, 7, 30, 10, 0),
      current_price=9.8,
    ),
  )
  runtime.exit_plan_book.mark_intent(decision, "exit-intent-1")

  await executor._notify_strategy_order(
    runtime,
    OrderStateEvent(
      order_id=None,
      status="REJECTED",
      metadata={
        "exit_plan_id": template.plan_id,
        "intent_id": "exit-intent-1",
      },
    ),
  )

  assert plan.status == ExitPlanStatus.ACTIVE
  assert plan.pending_intent_id == ""


@pytest.mark.asyncio
async def test_active_exit_plan_prevents_normal_pause_and_stop():
  executor = StrategyExecutor()
  runtime = make_runtime()
  runtime.status = ExecutionStatus.RUNNING
  runtime.exit_plan_book.register_entry_fill(
    make_template(),
    volume=100,
    price=10.0,
  )
  executor.runs[runtime.run_id] = runtime

  assert await executor.pause(runtime.run_id) is False
  assert await executor.stop(runtime.run_id) is False
  assert runtime.status == ExecutionStatus.RUNNING


@pytest.mark.asyncio
async def test_limit_up_break_uses_configured_instrument_limits_and_routes_urgent():
  executor = StrategyExecutor()
  runtime = make_runtime()
  runtime.context.parameters["instrument_master"] = {
    "up_stop_price": 11.0,
    "down_stop_price": 9.0,
    "price_tick": 0.01,
  }
  template = ExitPlanTemplate(
    plan_id="board-exit-plan",
    source_type="LIMIT_UP_BOARD",
    source_id="board-entry",
    account_id="account-1",
    instrument_code="600000.SH",
    bucket="swing",
    run_id=runtime.run_id,
    strategy_id="1",
    rules=[
      ExitRuleSpec(
        strategy=ExitRuleType.LIMIT_UP_BREAK,
        priority=1000,
        parameters={
          "break_ticks": 1,
          "min_seal_seconds": 0,
          "min_holding_trading_days": 2,
        },
      )
    ],
    t1_policy=ExitT1Policy.WAIT_UNTIL_SELLABLE,
    auto_exit_authorized=True,
  )
  runtime.exit_plan_book.register_entry_fill(
    template,
    volume=100,
    price=10.0,
    trade_time=datetime(2026, 7, 30, 10, 0),
  )
  executor._process_trade_intent = AsyncMock()

  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 7, 31, 10, 0, 0),
    market_data=MarketDataSnapshot(
      instrument_code="600000.SH",
      price=11.0,
      bid_price=[11.0],
      ask_price=[0.0],
    ),
  )
  executor._process_trade_intent.assert_not_awaited()

  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 7, 31, 10, 0, 1),
    market_data=MarketDataSnapshot(
      instrument_code="600000.SH",
      price=10.98,
      bid_price=[10.98],
      ask_price=[10.99],
    ),
  )

  routed = executor._process_trade_intent.await_args.args[1]
  assert routed.metadata["exit_rule_type"] == ExitRuleType.LIMIT_UP_BREAK.value
  assert routed.priority == TradeIntentPriority.URGENT


def test_limit_price_derivation_is_backtest_only_and_explicit():
  runtime = make_runtime()
  runtime.context.parameters["backtest_limit_rate"] = 0.10

  assert StrategyExecutor._backtest_limit_rate(runtime) is None

  runtime.context.mode = StrategyRunMode.BACKTEST
  assert StrategyExecutor._backtest_limit_rate(runtime) == pytest.approx(0.10)

  runtime.context.parameters["backtest_limit_rate"] = 0
  assert StrategyExecutor._backtest_limit_rate(runtime) is None
