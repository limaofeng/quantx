"""Regression coverage for StrategyManager lifecycle across Engine supervision."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.base import (
  StrategyBase,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
)
from quantx_engine.strategy_executor import ExecutionStatus
from quantx_engine.strategy_manager import StrategyManager


class RecoveryStrategy(StrategyBase):
  @property
  def name(self) -> str:
    return "Recovery strategy"

  @property
  def description(self) -> str:
    return "StrategyManager supervised restart fixture"

  @property
  def version(self) -> str:
    return "1.0.0"

  @classmethod
  def get_parameter_schema(cls) -> dict:
    return {"type": "object", "properties": {}}

  async def step(self, input: StrategyInput) -> StrategyOutput:
    return StrategyOutput()


@pytest.fixture
def isolated_manager() -> StrategyManager:
  StrategyManager._instance = None
  manager = StrategyManager()
  yield manager
  manager.executor.runs.clear()
  manager.executor.thread_pool.shutdown(wait=False)
  StrategyManager._instance = None


@pytest.mark.asyncio
async def test_process_stop_then_start_uses_fresh_executor(
  isolated_manager: StrategyManager,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  manager = isolated_manager
  sync_strategies = AsyncMock()
  restore_runs = AsyncMock()
  monkeypatch.setattr(manager, "_sync_strategies", sync_strategies)
  monkeypatch.setattr(manager, "_restore_runs", restore_runs)

  await manager.start()
  retired_executor = manager.executor
  exit_registry = retired_executor.exit_strategy_registry

  await manager.stop()

  assert retired_executor._shutdown_event.is_set()
  assert manager._executor_retired is True

  await manager.start()

  assert manager.executor is not retired_executor
  assert manager.executor.exit_strategy_registry is exit_registry
  assert not manager.executor._shutdown_event.is_set()
  assert restore_runs.await_count == 2

  # A callback scheduled late by the retired generation must not overwrite a
  # run restored by the new generation.
  update_status = AsyncMock()
  monkeypatch.setattr(manager, "_update_runtime_status", update_status)
  completed_task = asyncio.create_task(asyncio.sleep(0))
  await completed_task
  await manager._on_run_task_done(
    "retired-run",
    completed_task,
    executor=retired_executor,
  )
  update_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_start_restores_same_run_with_heartbeat_and_consumer(
  isolated_manager: StrategyManager,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  manager = isolated_manager
  run_id = "durable-t-run"
  restore_count = 0
  restored_runtimes = []

  async def restore_same_run() -> None:
    nonlocal restore_count
    restore_count += 1
    runtime = manager.executor.create(
      run_id=run_id,
      name="Durable T run",
      strategy_id=1,
      strategy_class=RecoveryStrategy,
      context=StrategyContext(
        run_id=run_id,
        mode=StrategyRunMode.PAPER,
        instruments=[],
        parameters={},
      ),
    )
    if restore_count == 2:
      runtime.status = ExecutionStatus.RUNNING
    restored_runtimes.append(runtime)

  monkeypatch.setattr(manager, "_sync_strategies", AsyncMock())
  monkeypatch.setattr(manager, "_restore_runs", restore_same_run)

  await manager.start()
  first_runtime = manager.get_run(run_id)
  assert first_runtime is restored_runtimes[0]

  await manager.stop()
  await manager.start()

  runtime = manager.get_run(run_id)
  assert runtime is restored_runtimes[1]
  assert runtime is not first_runtime
  assert runtime.run_id == run_id

  runtime.broker = AsyncMock()
  runtime.broker.get_position.return_value = {}
  runtime.broker.get_account.return_value = SimpleNamespace(
    cash=100_000.0,
    total_asset=100_000.0,
    frozen_cash=0.0,
    market_value=0.0,
    total_pnl=0.0,
    daily_pnl=0.0,
  )
  runtime.data_adapter = AsyncMock()
  runtime.state_manager = SimpleNamespace(
    update_account=lambda **_kwargs: None,
    update_position=lambda *_args, **_kwargs: None,
  )
  runtime.metrics.last_heartbeat = None
  runtime.event_task = asyncio.create_task(
    manager.executor._process_event_queue(runtime)
  )
  heartbeat_task = asyncio.create_task(
    manager.executor._run_realtime_loop(runtime)
  )

  try:
    await asyncio.sleep(0.05)
    assert runtime.metrics.last_heartbeat is not None
    assert not heartbeat_task.done()
    assert manager.executor.require_durable_event_consumer(run_id) is runtime
  finally:
    runtime.status = ExecutionStatus.STOPPED
    heartbeat_task.cancel()
    runtime.event_task.cancel()
    await asyncio.gather(
      heartbeat_task,
      runtime.event_task,
      return_exceptions=True,
    )


@pytest.mark.asyncio
async def test_cancelled_stop_keeps_shutdown_alive_and_start_waits_for_it(
  isolated_manager: StrategyManager,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  manager = isolated_manager
  manager.running = True
  run_id = "same-run-after-timeout"
  retired_executor = manager.executor
  keep_old_runtime_alive = asyncio.Event()
  old_runtime_task = asyncio.create_task(keep_old_runtime_alive.wait())
  retired_executor.runs[run_id] = SimpleNamespace(
    run_id=run_id,
    task=old_runtime_task,
    event_task=None,
  )
  shutdown_entered = asyncio.Event()
  allow_shutdown_to_finish = asyncio.Event()
  shutdown_calls = 0

  async def controlled_shutdown() -> None:
    nonlocal shutdown_calls
    shutdown_calls += 1
    retired_executor._shutdown_event.set()
    shutdown_entered.set()
    await allow_shutdown_to_finish.wait()
    old_runtime_task.cancel()
    await asyncio.gather(old_runtime_task, return_exceptions=True)
    retired_executor.thread_pool.shutdown(wait=False)

  restored = []

  async def restore_same_run() -> None:
    # A new generation may not be populated until every task in the retired
    # generation is proven dead.
    assert old_runtime_task.done()
    restored.append(
      manager.executor.create(
        run_id=run_id,
        name="Recovered run",
        strategy_id=1,
        strategy_class=RecoveryStrategy,
        context=StrategyContext(
          run_id=run_id,
          mode=StrategyRunMode.PAPER,
          instruments=[],
          parameters={},
        ),
      )
    )

  monkeypatch.setattr(retired_executor, "shutdown", controlled_shutdown)
  monkeypatch.setattr(manager, "_sync_strategies", AsyncMock())
  monkeypatch.setattr(manager, "_restore_runs", restore_same_run)

  outer_stop = asyncio.create_task(manager.stop())
  await asyncio.wait_for(shutdown_entered.wait(), timeout=1.0)
  generation_shutdown = manager._executor_shutdown_task
  assert generation_shutdown is not None

  # This is what main._stop_component's wait_for timeout does to stop().
  outer_stop.cancel()
  with pytest.raises(asyncio.CancelledError):
    await outer_stop

  assert manager._executor_shutdown_task is generation_shutdown
  assert not generation_shutdown.done()
  assert not old_runtime_task.done()

  repeated_stop = asyncio.create_task(manager.stop())
  restarted = asyncio.create_task(manager.start())
  await asyncio.sleep(0)

  assert not repeated_stop.done()
  assert not restarted.done()
  assert manager.executor is retired_executor
  assert restored == []
  assert shutdown_calls == 1

  allow_shutdown_to_finish.set()
  await asyncio.wait_for(repeated_stop, timeout=1.0)
  await asyncio.wait_for(restarted, timeout=1.0)

  assert generation_shutdown.done()
  assert old_runtime_task.done()
  assert manager.executor is not retired_executor
  assert manager.get_run(run_id) is restored[0]
  assert shutdown_calls == 1


@pytest.mark.asyncio
async def test_incomplete_retired_generation_fails_closed(
  isolated_manager: StrategyManager,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  manager = isolated_manager
  manager.running = True
  retired_executor = manager.executor
  live_task = asyncio.create_task(asyncio.Event().wait())
  retired_executor.runs["unsafe-run"] = SimpleNamespace(
    run_id="unsafe-run",
    task=live_task,
    event_task=None,
  )

  async def incomplete_shutdown() -> None:
    retired_executor._shutdown_event.set()
    retired_executor.thread_pool.shutdown(wait=False)

  sync_strategies = AsyncMock()
  restore_runs = AsyncMock()
  monkeypatch.setattr(retired_executor, "shutdown", incomplete_shutdown)
  monkeypatch.setattr(manager, "_sync_strategies", sync_strategies)
  monkeypatch.setattr(manager, "_restore_runs", restore_runs)

  try:
    with pytest.raises(RuntimeError, match="未安全关闭"):
      await manager.stop()
    shutdown_task = manager._executor_shutdown_task
    assert shutdown_task is not None and shutdown_task.done()

    with pytest.raises(RuntimeError, match="未安全关闭"):
      await manager.stop()
    assert manager._executor_shutdown_task is shutdown_task

    with pytest.raises(RuntimeError, match="未安全关闭"):
      await manager.start()
    assert manager.executor is retired_executor
    assert manager.running is False
    sync_strategies.assert_not_awaited()
    restore_runs.assert_not_awaited()
  finally:
    live_task.cancel()
    await asyncio.gather(live_task, return_exceptions=True)
    retired_executor.runs.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("persisted_status", ["RUNNING", "PAUSED", "PENDING"])
async def test_process_shutdown_preserves_active_persisted_run_status(
  isolated_manager: StrategyManager,
  monkeypatch: pytest.MonkeyPatch,
  persisted_status: str,
) -> None:
  manager = isolated_manager
  manager.running = True
  executor = manager.executor
  persisted = {"status": persisted_status}

  async def update_status(_run_id: str, status: str, *_args) -> None:
    persisted["status"] = status

  async def shutdown_with_normal_task_completion() -> None:
    executor._shutdown_event.set()
    completed_task = asyncio.create_task(asyncio.sleep(0))
    await completed_task
    await manager._on_run_task_done(
      "active-run",
      completed_task,
      executor=executor,
    )
    executor.thread_pool.shutdown(wait=False)

  monkeypatch.setattr(manager, "_update_runtime_status", update_status)
  monkeypatch.setattr(executor, "shutdown", shutdown_with_normal_task_completion)

  await manager.stop()

  assert persisted["status"] == persisted_status


@pytest.mark.asyncio
async def test_current_generation_explicit_stop_still_persists_stopped(
  isolated_manager: StrategyManager,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  manager = isolated_manager
  manager.running = True
  run_id = "operator-stopped-run"
  runtime = SimpleNamespace(
    status=ExecutionStatus.STOPPED,
    context=SimpleNamespace(backtest_id=None),
  )
  manager.executor.runs[run_id] = runtime
  update_status = AsyncMock()
  monkeypatch.setattr(manager, "_update_runtime_status", update_status)

  completed_task = asyncio.create_task(asyncio.sleep(0))
  await completed_task
  await manager._on_run_task_done(
    run_id,
    completed_task,
    executor=manager.executor,
  )

  update_status.assert_awaited_once_with(run_id, "STOPPED")
  manager.executor.runs.clear()
