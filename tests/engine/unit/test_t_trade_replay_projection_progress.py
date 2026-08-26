from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_engine.strategy_executor as strategy_executor_module
from quantx_domain.strategies.base import StrategyContext
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_infrastructure.models.enums import StrategyRunMode


@pytest.mark.asyncio
async def test_backtest_replay_progress_writes_only_at_forced_day_boundary(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="replay-progress",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"t_trade_replay": True, "account_id": "account-1"},
    backtest_start_time=datetime(2024, 1, 2, 9, 30),
    backtest_end_time=datetime(2024, 1, 2, 15, 0),
    current_time=datetime(2024, 1, 2, 12, 15),
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
  )
  update = AsyncMock()
  monkeypatch.setattr(
    strategy_executor_module.t_trade_replay_projection_service,
    "update",
    update,
  )

  await executor._report_t_trade_replay_progress(runtime)
  context.current_time = datetime(2024, 1, 2, 13, 0)
  await executor._report_t_trade_replay_progress(runtime)
  await executor._report_t_trade_replay_progress(
    runtime,
    processed_until=datetime(2024, 1, 2, 14, 0),
    force=True,
  )

  update.assert_awaited_once()
  call = update.await_args.kwargs
  assert call["progress_pct"] > 50.0
  assert call["processed_until"] == datetime(2024, 1, 2, 14, 0)


@pytest.mark.asyncio
async def test_multi_instrument_replay_marks_empty_window_processed(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class FakeHistoricalDataAdapter:
    async def get_ticks(self, **_kwargs):
      return []

  class FakeTradingDateHelper:
    async def get_trading_calendar(self, **_kwargs):
      return [datetime(2024, 1, 2).date()]

  monkeypatch.setattr(
    strategy_executor_module,
    "HistoricalDataAdapter",
    FakeHistoricalDataAdapter,
  )
  monkeypatch.setattr(
    strategy_executor_module,
    "TradingDateHelper",
    FakeTradingDateHelper,
  )
  executor = StrategyExecutor()
  executor._run_backtest_warmup_klines = AsyncMock()
  executor._report_t_trade_replay_progress = AsyncMock()
  executor._runtime_log = lambda *_args, **_kwargs: None
  end_time = datetime(2024, 1, 2, 15, 0)
  context = StrategyContext(
    run_id="replay-empty-window",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ", "600000.SH"],
    parameters={"t_trade_replay": True, "account_id": "account-1"},
    backtest_start_time=datetime(2024, 1, 2, 9, 30),
    backtest_end_time=end_time,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
    data_adapter=FakeHistoricalDataAdapter(),
    status=ExecutionStatus.RUNNING,
  )

  await executor._run_backtest_multi_instrument_timeline(
    runtime,
    context.instruments,
    [],
    context.backtest_start_time,
    end_time,
    use_tick_data=True,
  )

  executor._report_t_trade_replay_progress.assert_awaited_once_with(
    runtime,
    processed_until=end_time,
    force=True,
  )


@pytest.mark.asyncio
async def test_multi_instrument_replay_yields_engine_loop_in_fixed_batches(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class FakeHistoricalDataAdapter:
    pass

  class FakeTradingDateHelper:
    async def get_trading_calendar(self, **_kwargs):
      return [datetime(2024, 1, 2).date()]

  monkeypatch.setattr(
    strategy_executor_module,
    "HistoricalDataAdapter",
    FakeHistoricalDataAdapter,
  )
  monkeypatch.setattr(
    strategy_executor_module,
    "TradingDateHelper",
    FakeTradingDateHelper,
  )
  cooperative_sleep = AsyncMock()
  monkeypatch.setattr(strategy_executor_module.asyncio, "sleep", cooperative_sleep)

  start_time = datetime(2024, 1, 2, 9, 30)
  end_time = datetime(2024, 1, 2, 10, 0)
  ticks = [
    SimpleNamespace(
      stock_code="000001.SZ",
      time=start_time + timedelta(milliseconds=index),
      continuity_generation=1,
      source_time_ms=int(
        (start_time + timedelta(milliseconds=index)).timestamp() * 1000
      ),
      tick_ordinal=index,
    )
    for index in range(1, 130)
  ]
  executor = StrategyExecutor()
  executor._run_backtest_warmup_klines = AsyncMock()
  executor._load_backtest_ticks = AsyncMock(return_value=ticks)
  executor._process_tick = AsyncMock()
  executor._report_t_trade_replay_progress = AsyncMock()
  executor._runtime_log = lambda *_args, **_kwargs: None
  context = StrategyContext(
    run_id="replay-cooperative-yield",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"t_trade_replay": True, "account_id": "account-1"},
    backtest_start_time=start_time,
    backtest_end_time=end_time,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
    data_adapter=FakeHistoricalDataAdapter(),
    status=ExecutionStatus.RUNNING,
  )

  await executor._run_backtest_multi_instrument_timeline(
    runtime,
    context.instruments,
    [],
    start_time,
    end_time,
    use_tick_data=True,
  )

  assert executor._process_tick.await_count == 129
  cooperative_sleep.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_tick_driven_klines_count_toward_cooperative_yield(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class FakeHistoricalDataAdapter:
    async def get_klines(self, **_kwargs):
      return klines

  class FakeTradingDateHelper:
    async def get_trading_calendar(self, **_kwargs):
      return [datetime(2024, 1, 2).date()]

  monkeypatch.setattr(
    strategy_executor_module,
    "HistoricalDataAdapter",
    FakeHistoricalDataAdapter,
  )
  monkeypatch.setattr(
    strategy_executor_module,
    "TradingDateHelper",
    FakeTradingDateHelper,
  )
  cooperative_sleep = AsyncMock()
  monkeypatch.setattr(strategy_executor_module.asyncio, "sleep", cooperative_sleep)

  start_time = datetime(2024, 1, 2, 9, 30)
  end_time = datetime(2024, 1, 2, 10, 0)
  ticks = [
    SimpleNamespace(
      stock_code="000001.SZ",
      time=end_time,
    )
  ]
  klines = [
    SimpleNamespace(
      stock_code="000001.SZ",
      time=start_time + timedelta(seconds=index),
    )
    for index in range(127)
  ]
  executor = StrategyExecutor()
  executor._run_backtest_warmup_klines = AsyncMock()
  executor._load_backtest_ticks = AsyncMock(return_value=ticks)
  executor._process_tick = AsyncMock()
  executor._process_kline = AsyncMock()
  executor._report_t_trade_replay_progress = AsyncMock()
  executor._runtime_log = lambda *_args, **_kwargs: None
  context = StrategyContext(
    run_id="tick-driven-kline-cooperative-yield",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"account_id": "account-1"},
    backtest_start_time=start_time,
    backtest_end_time=end_time,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay",
    strategy_id=1,
    strategy_class=object,
    context=context,
    data_adapter=FakeHistoricalDataAdapter(),
    status=ExecutionStatus.RUNNING,
  )

  await executor._run_backtest_timeline_with_ticks(
    runtime,
    "000001.SZ",
    ["1d"],
    start_time,
    end_time,
  )

  executor._process_tick.assert_awaited_once()
  assert executor._process_kline.await_count == 127
  cooperative_sleep.assert_awaited_once_with(0)
