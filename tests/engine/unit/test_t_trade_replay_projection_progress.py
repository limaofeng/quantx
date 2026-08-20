from datetime import datetime
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
async def test_replay_progress_projection_is_throttled_but_forced_at_boundary(
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
  clock = iter((100.0, 100.5, 100.6))
  monkeypatch.setattr(strategy_executor_module, "monotonic", lambda: next(clock))
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

  assert update.await_count == 2
  first_call = update.await_args_list[0].kwargs
  second_call = update.await_args_list[1].kwargs
  assert first_call["progress_pct"] == pytest.approx(50.0)
  assert second_call["progress_pct"] > first_call["progress_pct"]
  assert second_call["processed_until"] == datetime(2024, 1, 2, 14, 0)


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
