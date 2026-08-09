"""Deterministic end-to-end execution test for the A-share board strategy."""

from datetime import datetime, timedelta

import pytest
from quantx_domain.brokers.backtest import BacktestBroker
from quantx_domain.brokers.base import OrderStatus, OrderType
from quantx_domain.strategies.ashare_limit_up_board import (
  AshareLimitUpBoardStrategy,
)
from quantx_domain.strategies.base import (
  OrderStateEvent,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyRunMode,
  TradeExecutionEvent,
)
from quantx_domain.trading.exit_plan import ExitPlanStatus, ExitT1Policy
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_engine.strategy_executor import StrategyExecutor, StrategyRuntime
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager
from quantx_infrastructure.models import ExecutionMetrics


def _snapshot(
  timestamp: datetime,
  price: float,
  *,
  ask_price: float | None = None,
  bid_price: float | None = None,
) -> MarketDataSnapshot:
  return MarketDataSnapshot(
    instrument_code="000001.SZ",
    timestamp=timestamp,
    price=price,
    open=10.50,
    high=max(10.99, price),
    low=min(10.40, price),
    close=price,
    volume=1_000_000,
    amount=200_000_000,
    price_tick=0.01,
    limit_up=11.0,
    limit_down=9.0,
    bid_price=[bid_price if bid_price is not None else price],
    ask_price=[ask_price if ask_price is not None else price],
    bid_vol=[100_000],
    ask_vol=[100_000],
    source="integration-test",
  )


async def _apply_latest_broker_reports(
  executor: StrategyExecutor,
  runtime: StrategyRuntime,
) -> None:
  order = next(reversed(runtime.broker.orders.values()))
  trade = runtime.broker.trades[-1]
  runtime.state_manager.apply_trade(trade)
  await executor._notify_strategy_order(runtime, OrderStateEvent.from_raw(order))
  await executor._notify_strategy_trade(
    runtime,
    TradeExecutionEvent.from_raw(trade),
  )


@pytest.mark.asyncio
async def test_board_entry_to_t1_exit_uses_real_executor_and_backtest_broker():
  signal_time = datetime(2026, 7, 30, 10, 0, 0)
  context = StrategyContext(
    run_id="board-full-chain",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={
      "instrument_code": "000001.SZ",
      "target_position_pct": 0.05,
      "auto_approve_manual_intents": True,
      "entry_order_ttl_ms": 15_000,
      "exit_min_seal_seconds": 0,
      "strict_market_data": True,
      "strict_limit_data": True,
    },
    initial_capital=1_000_000,
    current_time=signal_time,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="board-full-chain",
    strategy_id=1,
    strategy_class=AshareLimitUpBoardStrategy,
    context=context,
    metrics=ExecutionMetrics(
      start_time=signal_time,
      last_heartbeat=signal_time,
      initial_capital=context.initial_capital,
      current_capital=context.initial_capital,
    ),
  )
  runtime.strategy = AshareLimitUpBoardStrategy(context)
  runtime.broker = BacktestBroker(
    account_id=context.run_id,
    initial_capital=context.initial_capital,
    book_depth_participation_pct=1.0,
  )
  runtime.state_manager = RuntimeStateManager(
    run_id=context.run_id,
    persist_enabled=False,
  )
  runtime.state_manager.update_account(
    cash=context.initial_capital,
    total_asset=context.initial_capital,
  )
  await runtime.strategy.start()
  await runtime.broker.connect()

  executor = StrategyExecutor()
  executor.runs[runtime.run_id] = runtime

  signal_market = _snapshot(signal_time, 10.99, ask_price=11.0)
  runtime.latest_market_data["000001.SZ"] = signal_market
  await runtime.broker.update_market_data(
    "000001.SZ",
    signal_market.price,
    signal_time,
    market_data=signal_market,
  )
  strategy_input = StrategyInput(
    run_id=runtime.run_id,
    strategy_id=str(runtime.strategy_id),
    timestamp=signal_time,
    cadence=StrategyCadence.TICK,
    instrument_code="000001.SZ",
    market_data=signal_market,
    event=signal_market,
    market_context={
      "data_quality": "OK",
      "context_score": 0.2,
      "instrument_master": {
        "limit_up": 11.0,
        "limit_down": 9.0,
        "price_tick": 0.01,
        "data_quality": "OK",
      },
    },
    portfolio_state={
      "account": {
        "account_id": context.run_id,
        "total_asset": context.initial_capital,
        "available_cash": context.initial_capital,
      },
      "positions": {},
    },
  )

  output = await runtime.strategy.step(strategy_input)
  await executor._process_strategy_output(runtime, output, strategy_input)

  assert len(output.trade_intents) == 1
  assert runtime.pending_approvals == {}
  assert len(runtime.broker.pending_orders) == 1
  assert runtime.broker.trades == []
  entry_order = runtime.broker.pending_orders[0]
  assert entry_order.request.order_type == OrderType.BUY
  assert entry_order.status == OrderStatus.SUBMITTED
  assert entry_order.request.metadata["order_expire_at_ms"] > int(
    signal_time.timestamp() * 1000
  )

  entry_fill_time = signal_time + timedelta(seconds=1)
  entry_market = _snapshot(entry_fill_time, 10.99, ask_price=10.99)
  runtime.latest_market_data["000001.SZ"] = entry_market
  context.current_time = entry_fill_time
  await runtime.broker.update_market_data(
    "000001.SZ",
    entry_market.price,
    entry_fill_time,
    market_data=entry_market,
  )
  await _apply_latest_broker_reports(executor, runtime)

  assert len(runtime.broker.trades) == 1
  assert runtime.broker.trades[0].trade_type == OrderType.BUY
  assert runtime.strategy.state["last_entry_volume"] > 0
  assert runtime.broker.positions["000001.SZ"].available_volume == 0
  [plan] = runtime.exit_plan_book.active_plans()
  assert plan.template.t1_policy == ExitT1Policy.WAIT_UNTIL_SELLABLE
  assert plan.status == ExitPlanStatus.ACTIVE

  same_day_seal = _snapshot(
    entry_fill_time + timedelta(seconds=1),
    11.0,
    ask_price=11.0,
    bid_price=11.0,
  )
  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="000001.SZ",
    timestamp=same_day_seal.timestamp,
    market_data=same_day_seal,
  )
  same_day_break = _snapshot(
    entry_fill_time + timedelta(seconds=2),
    10.98,
    ask_price=10.99,
    bid_price=10.98,
  )
  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="000001.SZ",
    timestamp=same_day_break.timestamp,
    market_data=same_day_break,
  )

  assert all(
    order.request.order_type != OrderType.SELL
    for order in runtime.broker.orders.values()
  )
  assert plan.holding_trading_days == 1

  next_day = datetime(2026, 7, 31, 10, 0, 0)
  next_day_seal = _snapshot(
    next_day,
    11.0,
    ask_price=11.0,
    bid_price=11.0,
  )
  context.current_time = next_day
  runtime.latest_market_data["000001.SZ"] = next_day_seal
  runtime.state_manager.settle_trading_day(next_day.date())
  await runtime.broker.update_market_data(
    "000001.SZ",
    next_day_seal.price,
    next_day,
    market_data=next_day_seal,
  )
  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="000001.SZ",
    timestamp=next_day,
    market_data=next_day_seal,
  )
  assert runtime.broker.positions["000001.SZ"].available_volume == (
    runtime.broker.positions["000001.SZ"].long_volume
  )

  break_time = next_day + timedelta(seconds=1)
  next_day_break = _snapshot(
    break_time,
    10.98,
    ask_price=10.99,
    bid_price=10.98,
  )
  context.current_time = break_time
  runtime.latest_market_data["000001.SZ"] = next_day_break
  await runtime.broker.update_market_data(
    "000001.SZ",
    next_day_break.price,
    break_time,
    market_data=next_day_break,
  )
  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="000001.SZ",
    timestamp=break_time,
    market_data=next_day_break,
  )

  sell_orders = [
    order
    for order in runtime.broker.orders.values()
    if order.request.order_type == OrderType.SELL
  ]
  assert len(sell_orders) == 1
  assert sell_orders[0].status == OrderStatus.SUBMITTED
  assert len(runtime.broker.trades) == 1
  assert sell_orders[0].request.metadata["t1_policy"] == (
    ExitT1Policy.WAIT_UNTIL_SELLABLE.value
  )

  exit_fill_time = break_time + timedelta(seconds=1)
  exit_market = _snapshot(
    exit_fill_time,
    10.98,
    ask_price=10.99,
    bid_price=10.98,
  )
  await runtime.broker.update_market_data(
    "000001.SZ",
    exit_market.price,
    exit_fill_time,
    market_data=exit_market,
  )
  await _apply_latest_broker_reports(executor, runtime)

  assert [trade.trade_type for trade in runtime.broker.trades] == [
    OrderType.BUY,
    OrderType.SELL,
  ]
  assert plan.status == ExitPlanStatus.COMPLETED
  assert plan.remaining_volume == 0
  assert "000001.SZ" not in runtime.broker.positions
  performance = runtime.broker.get_performance_metrics()
  assert performance["total_trades"] == 2
  assert performance["constraint_statistics"]["full_fills"] == 2
