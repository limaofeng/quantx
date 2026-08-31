import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.market import KLine
from quantx_domain.strategies.base import (
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
)
from quantx_domain.trading.bar_timing import bar_query_start, resolve_bar_timing
from quantx_engine import strategy_executor as executor_module
from quantx_engine.replay_clock import ReplayClock
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)


def bar(stamp, period="1d", code="600000.SH"):
  return KLine(
    stock_code=code, period=period, time=stamp, open=10, high=11, low=9, close=10
  )


def runtime_for(executor, *, parameters=None, start=None):
  context = StrategyContext(
    run_id="bar-causality",
    mode=StrategyRunMode.BACKTEST,
    instruments=["600000.SH"],
    parameters=parameters or {},
    backtest_start_time=start or datetime(2024, 1, 2, 9, 30),
    backtest_end_time=datetime(2024, 1, 2, 15),
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="causality",
    strategy_id=1,
    strategy_class=object,
    context=context,
    status=ExecutionStatus.RUNNING,
  )
  runtime.replay_clock = ReplayClock(context.backtest_start_time)
  executor._runtime_log = lambda *_args, **_kwargs: None
  return runtime


def test_daily_label_is_not_its_availability_time():
  event = bar(datetime(2024, 1, 1, 16, tzinfo=timezone.utc))
  timing = resolve_bar_timing(event)
  assert timing.label_time == datetime(2024, 1, 2)
  assert timing.available_at == datetime(2024, 1, 2, 15)
  assert event.time == datetime(2024, 1, 1, 16, tzinfo=timezone.utc)
  minute = bar(datetime(2024, 1, 2, 9, 30), "1m")
  assert resolve_bar_timing(minute, alignment="start").available_at == datetime(
    2024, 1, 2, 9, 31
  )
  with pytest.raises(ValueError, match="BAR_PERIOD_UNSUPPORTED"):
    resolve_bar_timing(bar(datetime(2024, 1, 2), "unknown"))
  with pytest.raises(ValueError, match="BAR_PERIOD_UNSUPPORTED"):
    bar_query_start(datetime(2024, 1, 2), "unknown", alignment="end")
  with pytest.raises(ValueError, match="BAR_TIME_ALIGNMENT_INVALID"):
    bar_query_start(datetime(2024, 1, 2), "1d", alignment="unknown")


@pytest.mark.asyncio
@pytest.mark.parametrize("use_ticks", [False, True])
@pytest.mark.parametrize("end_hour", [11, 15])
async def test_all_replay_shapes_deliver_daily_only_after_close(
  monkeypatch, use_ticks, end_hour
):
  events = [bar(datetime(2024, 1, 2)), bar(datetime(2024, 1, 2, 9, 31), "1m")]

  class Adapter:
    async def get_klines(self, *, period, start_time, end_time, **_kwargs):
      return [
        event
        for event in events
        if event.period == period and start_time <= event.time <= end_time
      ]

  class Calendar:
    async def get_trading_calendar(self, **_kwargs):
      return [datetime(2024, 1, 2).date()]

  monkeypatch.setattr(executor_module, "HistoricalDataAdapter", Adapter)
  monkeypatch.setattr(executor_module, "TradingDateHelper", Calendar)
  executor = StrategyExecutor()
  runtime = runtime_for(executor)
  runtime.data_adapter = Adapter()
  executor._get_backtest_window_hours = lambda: 1
  executor._run_backtest_warmup_klines = AsyncMock()
  executor._report_t_trade_replay_progress = AsyncMock()
  tick = SimpleNamespace(stock_code="600000.SH", time=datetime(2024, 1, 2, 9, 31))

  async def load_ticks(_runtime, _adapter, *, start_time, end_time, **_kwargs):
    return [tick] if start_time <= tick.time <= end_time else []

  delivered = []

  async def consume_tick(_runtime, event):
    runtime.replay_clock.advance_to(event.time)
    delivered.append(("tick", event.time))

  async def consume_bar(_runtime, event):
    available = resolve_bar_timing(event).available_at
    runtime.replay_clock.advance_to(available)
    delivered.append((event.period, available))

  executor._load_backtest_ticks = load_ticks
  executor._process_tick = consume_tick
  executor._process_kline = consume_bar
  await executor._run_backtest_timeline(
    runtime,
    ["600000.SH"],
    ["1m", "1d"],
    datetime(2024, 1, 2, 9, 30),
    datetime(2024, 1, 2, end_hour),
    use_tick_data=use_ticks,
  )
  expected = ([("tick", tick.time)] if use_ticks else []) + [("1m", tick.time)]
  if end_hour == 15:
    expected.append(("1d", datetime(2024, 1, 2, 15)))
  assert delivered == expected


@pytest.mark.asyncio
async def test_start_label_across_window_boundary_is_delivered_once(monkeypatch):
  events = [bar(datetime(2024, 1, 2, 10, 30), "1m")]

  class Adapter:
    async def get_klines(self, *, start_time, end_time, **_kwargs):
      return [item for item in events if start_time <= item.time <= end_time]

  class Calendar:
    async def get_trading_calendar(self, **_kwargs):
      return [datetime(2024, 1, 2).date()]

  monkeypatch.setattr(executor_module, "HistoricalDataAdapter", Adapter)
  monkeypatch.setattr(executor_module, "TradingDateHelper", Calendar)
  executor = StrategyExecutor()
  runtime = runtime_for(executor, parameters={"kline_time_alignment": "start"})
  runtime.data_adapter = Adapter()
  executor._get_backtest_window_hours = lambda: 1
  executor._run_backtest_warmup_klines = AsyncMock()
  executor._process_kline = AsyncMock()
  executor._report_t_trade_replay_progress = AsyncMock()
  await executor._run_backtest_timeline(
    runtime,
    ["600000.SH"],
    ["1m"],
    datetime(2024, 1, 2, 9, 30),
    datetime(2024, 1, 2, 11, 30),
    use_tick_data=False,
  )
  executor._process_kline.assert_awaited_once_with(runtime, events[0])


@pytest.mark.asyncio
async def test_warmup_cannot_see_the_current_days_full_ohlc(monkeypatch):
  events = [
    bar(datetime(2024, 1, 2)),
    bar(datetime(2024, 1, 1)),
    bar(datetime(2023, 12, 29)),
  ]

  class Adapter:
    async def get_klines(self, **_kwargs):
      return events

  monkeypatch.setattr(executor_module, "HistoricalDataAdapter", Adapter)
  executor = StrategyExecutor()
  runtime = runtime_for(executor)
  runtime.strategy = object()
  runtime.data_adapter = Adapter()
  executor._get_backtest_warmup_bars = lambda *_args: 2
  executor._process_warmup_kline = AsyncMock()
  await executor._run_backtest_warmup_klines(
    runtime, ["600000.SH"], ["1d"], datetime(2024, 1, 2, 10)
  )
  assert [item.args[1] for item in executor._process_warmup_kline.await_args_list] == [
    events[2],
    events[1],
  ]


@pytest.mark.asyncio
async def test_warmup_orders_all_instruments_and_periods_by_availability(monkeypatch):
  events = [
    bar(datetime(2024, 1, 1), code="600001.SH"),
    bar(datetime(2023, 12, 29), code="600000.SH"),
    bar(datetime(2024, 1, 1, 14), "1m", code="600000.SH"),
  ]

  class Adapter:
    async def get_klines(self, *, instrument_code, period, **_kwargs):
      return [
        event
        for event in events
        if event.stock_code == instrument_code and event.period == period
      ]

  monkeypatch.setattr(executor_module, "HistoricalDataAdapter", Adapter)
  executor = StrategyExecutor()
  runtime = runtime_for(executor)
  runtime.strategy = object()
  runtime.data_adapter = Adapter()
  executor._get_backtest_warmup_bars = lambda *_args: 2
  executor._process_warmup_kline = AsyncMock()
  await executor._run_backtest_warmup_klines(
    runtime, ["600001.SH", "600000.SH"], ["1d", "1m"], datetime(2024, 1, 2, 10)
  )
  assert [call.args[1] for call in executor._process_warmup_kline.await_args_list] == [
    events[1],
    events[2],
    events[0],
  ]


@pytest.mark.asyncio
async def test_backtest_market_cannot_enter_its_report_consumer():
  executor = StrategyExecutor()
  runtime = runtime_for(executor)
  event = bar(datetime(2024, 1, 2))
  with pytest.raises(RuntimeError, match="BACKTEST_TIMELINE_REQUIRED"):
    executor._enqueue_runtime_market_event(runtime, "kline", event)
  assert runtime.event_queue.empty()
  runtime.event_queue.put_nowait(("kline", event))
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  with pytest.raises(RuntimeError, match="BACKTEST_TIMELINE_REQUIRED"):
    await executor._replay_report_barrier(runtime)
  await runtime.event_task
  assert runtime.status == ExecutionStatus.ERROR


@pytest.mark.asyncio
async def test_bar_processing_uses_availability_for_decisions_and_watermarks():
  executor = StrategyExecutor()
  runtime = runtime_for(executor)
  runtime.strategy = SimpleNamespace(step=AsyncMock(return_value=StrategyOutput()))
  executor._expire_pending_approvals = AsyncMock()
  executor._cancel_expired_strategy_orders = AsyncMock()
  executor._process_auto_exit_plans = AsyncMock()
  executor._process_strategy_output = AsyncMock()
  executor._report_t_trade_replay_progress = AsyncMock()
  executor._backtest_limit_rate = lambda *_args, **_kwargs: 0.1
  executor._build_strategy_input = lambda _runtime, **kwargs: StrategyInput(
    run_id=runtime.run_id,
    strategy_id="1",
    **kwargs,
  )
  event = bar(datetime(2024, 1, 2))
  await executor._process_kline(runtime, event)
  decision = runtime.strategy.step.await_args.args[0]
  assert (
    decision.timestamp == decision.market_data.timestamp == datetime(2024, 1, 2, 15)
  )
  assert decision.event.time == datetime(2024, 1, 2)
  assert decision.bar_period == "1d"
  assert runtime.replay_clock.now() == datetime(2024, 1, 2, 15)
  assert (
    runtime._checkpoint_processed_watermark["source_time_ms"]
    == runtime.replay_clock.now_ms()
  )


@pytest.mark.asyncio
async def test_failed_broker_callback_fails_an_ordinary_backtest():
  executor = StrategyExecutor()
  runtime = runtime_for(executor)
  runtime.strategy = SimpleNamespace(
    on_order=AsyncMock(side_effect=RuntimeError("broken algorithm callback"))
  )
  executor._maybe_coordinate_session_checkpoints = AsyncMock()
  event = SimpleNamespace(
    order_id="order",
    status="SUBMITTED",
    request=None,
    last_update_time=datetime(2024, 1, 2, 10),
  )
  runtime.event_queue.put_nowait(("order", event))
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  try:
    with pytest.raises(RuntimeError, match="broken algorithm callback"):
      await executor._replay_report_barrier(runtime)
    assert runtime.status == ExecutionStatus.ERROR
  finally:
    runtime.event_task.cancel()
    with suppress(asyncio.CancelledError):
      await runtime.event_task
