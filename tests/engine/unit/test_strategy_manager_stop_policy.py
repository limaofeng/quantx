from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from quantx_domain.strategies.base import StrategyContext, StrategyRunMode
from quantx_engine.strategy_executor import ExecutionStatus
from quantx_engine.strategy_manager import StrategyManager
from quantx_infrastructure.models.enums import StrategyRunStatus


def _manager_with_runtime(runtime):
  manager = object.__new__(StrategyManager)
  manager.executor = SimpleNamespace(
    get=lambda _run_id: runtime,
    stop=AsyncMock(
      side_effect=lambda _run_id, *, force=False: (
        bool(force) if runtime.exit_plan_book.active_plans() else True
      )
    ),
  )
  manager.logger = SimpleNamespace(
    info=lambda *_args, **_kwargs: None,
    warning=lambda *_args, **_kwargs: None,
    error=lambda *_args, **_kwargs: None,
  )
  manager._update_runtime_metrics = AsyncMock()
  manager._update_runtime_status = AsyncMock()
  manager._shutdown_in_progress = False
  return manager


def _runtime(*, mode: StrategyRunMode, t_trade_replay: bool):
  return SimpleNamespace(
    context=StrategyContext(
      run_id="run-1",
      mode=mode,
      instruments=["600887.SH"],
      parameters={"t_trade_replay": t_trade_replay},
    ),
    metrics=None,
    exit_plan_book=SimpleNamespace(active_plans=lambda: [object()]),
  )


@pytest.mark.asyncio
async def test_normal_stop_remains_blocked_by_active_exit_plan() -> None:
  runtime = _runtime(mode=StrategyRunMode.BACKTEST, t_trade_replay=True)
  manager = _manager_with_runtime(runtime)

  assert await manager.stop_strategy("run-1") is False

  manager.executor.stop.assert_awaited_once_with("run-1", force=False)
  manager._update_runtime_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_stop_is_scoped_to_t_trade_backtest() -> None:
  replay_runtime = _runtime(
    mode=StrategyRunMode.BACKTEST,
    t_trade_replay=True,
  )
  replay_manager = _manager_with_runtime(replay_runtime)

  assert await replay_manager.stop_strategy("run-1", force=True) is True
  replay_manager.executor.stop.assert_awaited_once_with("run-1", force=True)
  replay_manager._update_runtime_status.assert_awaited_once_with(
    "run-1",
    "STOPPED",
  )

  live_runtime = _runtime(mode=StrategyRunMode.LIVE, t_trade_replay=True)
  live_manager = _manager_with_runtime(live_runtime)

  assert await live_manager.stop_strategy("run-1", force=True) is False
  live_manager.executor.stop.assert_not_awaited()
  live_manager._update_runtime_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_replay_task_converges_run_backtest_and_projection_to_error() -> None:
  runtime = _runtime(mode=StrategyRunMode.BACKTEST, t_trade_replay=True)
  runtime.context.parameters["account_id"] = "account-1"
  runtime.context.backtest_id = "backtest-1"
  runtime.context.current_time = datetime(2026, 8, 19, 10, 0)
  runtime.status = ExecutionStatus.ERROR
  runtime.error_message = "ReplayClock cannot move backwards"
  manager = _manager_with_runtime(runtime)
  # Exercise the real persistence convergence method instead of the helper
  # mock installed by the stop-policy fixture above.
  manager._update_runtime_status = StrategyManager._update_runtime_status.__get__(
    manager,
    StrategyManager,
  )
  run_repository = AsyncMock()
  backtest_repository = AsyncMock()

  async def fake_get_async_db():
    yield object()

  task = SimpleNamespace(
    cancelled=lambda: False,
    exception=lambda: None,
  )
  with (
    patch("quantx_engine.strategy_manager.get_async_db", fake_get_async_db),
    patch(
      "quantx_engine.strategy_manager.StrategyRunRepository",
      return_value=run_repository,
    ),
    patch(
      "quantx_infrastructure.repositories.backtest_repository."
      "BacktestRepository",
      return_value=backtest_repository,
    ),
    patch(
      "quantx_engine.strategy_manager.t_trade_replay_projection_service.update",
      new_callable=AsyncMock,
    ) as update_projection,
  ):
    await manager._on_run_task_done("run-1", task)

  run_repository.update_run.assert_awaited_once()
  assert run_repository.update_run.await_args.args == (
    "run-1",
    {
      "status": StrategyRunStatus.ERROR,
      "error_message": "ReplayClock cannot move backwards",
    },
  )
  backtest_repository.update_backtest_status.assert_awaited_once()
  assert backtest_repository.update_backtest_status.await_args.kwargs[
    "status"
  ] == "ERROR"
  assert backtest_repository.update_backtest_status.await_args.kwargs[
    "error_message"
  ] == "ReplayClock cannot move backwards"
  update_projection.assert_awaited_once()
  assert update_projection.await_args.kwargs["status"] == "ERROR"
