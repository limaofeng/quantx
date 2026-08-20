from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from quantx_domain.strategies.base import (
  StrategyBase,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
)
from quantx_engine.strategy_manager import StrategyManager
from quantx_infrastructure.models.enums import StrategyRunStatus


class ReplayStrategy(StrategyBase):
  @property
  def name(self) -> str:
    return "ReplayStrategy"

  @property
  def description(self) -> str:
    return "打板回放策略管理器测试桩"

  @property
  def version(self) -> str:
    return "1.0.0"

  async def step(self, input: StrategyInput) -> StrategyOutput:
    return StrategyOutput()


@pytest.mark.asyncio
async def test_deferred_start_error_terminalizes_board_replay_only() -> None:
  StrategyManager._instance = None
  manager = StrategyManager()
  run_id = "board-replay-run"
  manager.executor.create(
    run_id=run_id,
    name="Board replay",
    strategy_id=1,
    strategy_class=ReplayStrategy,
    context=StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["600000.SH"],
      parameters={
        "account_id": "account-1",
        "limit_up_board_replay": True,
        "limit_up_board_replay_job_id": "board-job-1",
      },
      backtest_id="board-backtest-1",
    ),
  )

  with (
    patch.object(
      manager,
      "_update_runtime_status",
      new_callable=AsyncMock,
    ) as update_runtime,
    patch.object(
      manager,
      "_mark_backtest_error_safely",
      new_callable=AsyncMock,
    ) as mark_backtest_error,
    patch(
      "quantx_engine.strategy_manager.limit_up_board_replay_projection_service.update_job_error",
      new_callable=AsyncMock,
    ) as update_board,
  ):
    await manager.converge_deferred_start_error(run_id, "history unavailable")

  update_runtime.assert_awaited_once_with(
    run_id,
    "ERROR",
    "history unavailable",
  )
  mark_backtest_error.assert_awaited_once_with(
    "board-backtest-1",
    "history unavailable",
  )
  update_board.assert_awaited_once_with(
    job_id="board-job-1",
    error_message="history unavailable",
  )
  manager.executor.runs.clear()
  StrategyManager._instance = None


@pytest.mark.asyncio
async def test_restore_pending_board_replay_resumes_background_start() -> None:
  StrategyManager._instance = None
  manager = StrategyManager()
  run = SimpleNamespace(
    id="restored-board-replay",
    name="Restored board replay",
    strategy_id=1,
    strategy=SimpleNamespace(class_name="ReplayStrategy", file_path=""),
    parameters={
      "account_id": "account-1",
      "limit_up_board_replay": True,
      "limit_up_board_replay_job_id": "board-job-2",
    },
    mode=StrategyRunMode.BACKTEST,
    status=StrategyRunStatus.PENDING,
    instruments=["600000.SH"],
    initial_capital=100_000.0,
    metrics=None,
  )
  backtest = SimpleNamespace(
    id="restored-board-backtest",
    status="PENDING",
    version=1,
    backtest_start_time=datetime(2026, 8, 19, 9, 30),
    backtest_end_time=datetime(2026, 8, 19, 15, 0),
  )
  run_repo = AsyncMock()
  run_repo.find_all_active_runs.return_value = [run]
  backtest_repo = AsyncMock()
  backtest_repo.get_backtests_by_run.return_value = [backtest]

  async def fake_get_async_db():
    yield AsyncMock()

  with (
    patch("quantx_engine.strategy_manager.get_async_db", fake_get_async_db),
    patch(
      "quantx_engine.strategy_manager.StrategyRunRepository",
      return_value=run_repo,
    ),
    patch(
      "quantx_infrastructure.repositories.backtest_repository.BacktestRepository",
      return_value=backtest_repo,
    ),
    patch(
      "quantx_engine.strategy_manager.strategy_registry.get_strategy_class",
      return_value=ReplayStrategy,
    ),
    patch(
      "quantx_engine.strategy_manager.limit_up_board_replay_projection_service.get",
      new_callable=AsyncMock,
      return_value={"status": "RUNNING"},
    ),
    patch.object(
      manager,
      "defer_start_strategy",
      new_callable=AsyncMock,
      return_value=True,
    ) as defer_start,
    patch.object(manager, "start_strategy", new_callable=AsyncMock) as start_strategy,
  ):
    await manager._restore_runs()

  defer_start.assert_awaited_once_with("restored-board-replay")
  start_strategy.assert_not_awaited()
  assert manager.get_run("restored-board-replay") is not None
  manager.executor.runs.clear()
  StrategyManager._instance = None
