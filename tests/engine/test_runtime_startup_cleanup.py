from __future__ import annotations

import asyncio
import copy
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import quantx_engine.strategy_executor as executor_module
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.base import (
  StrategyBase,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  DataHealth,
  OpportunitySample,
  OpportunityState,
)
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  RuntimeMarketEvent,
  StrategyExecutor,
)
from quantx_infrastructure.core.brokers.live import LiveBroker
from quantx_infrastructure.core.data.adapter_manager import AdapterManager
from quantx_infrastructure.core.runtime_state_manager import (
  MARKET_CONTINUITY_RECONCILE_REQUIRED_KEY,
  RuntimeStateManager,
  RuntimeStateRestoreResult,
  RuntimeStateRestoreStatus,
)


class LifecycleStrategy(StrategyBase):
  @property
  def name(self) -> str:
    return "lifecycle-test"

  @property
  def description(self) -> str:
    return "lifecycle-test"

  @property
  def version(self) -> str:
    return "1"

  @classmethod
  def get_parameter_schema(cls) -> dict:
    return {"type": "object", "properties": {}, "required": []}

  @classmethod
  def get_data_requirements(cls) -> dict:
    return {"use_tick_data": False, "periods": ["1m"]}

  async def on_init(self) -> None:
    return None

  async def on_stop(self) -> None:
    return None

  async def step(self, _input: StrategyInput) -> StrategyOutput:
    return StrategyOutput()


class InitializeFailureStrategy(LifecycleStrategy):
  init_attempts = 0

  async def on_init(self) -> None:
    type(self).init_attempts += 1
    await asyncio.sleep(0)
    raise RuntimeError("initialize failed after await")


class BlockingInitializeFailureStrategy(LifecycleStrategy):
  init_attempts = 0
  entered: asyncio.Event
  release: asyncio.Event

  async def on_init(self) -> None:
    type(self).init_attempts += 1
    type(self).entered.set()
    await type(self).release.wait()
    raise RuntimeError("blocked initialize failed")


class ContinuityBlindStrategy(LifecycleStrategy):
  def invalidate_realtime_market_window(
    self,
    instrument_code: str,
    *,
    reason: str,
  ) -> bool:
    del instrument_code, reason
    return False


class FakeBroker:
  def __init__(self, *, disconnect_failures: int = 0) -> None:
    self.is_connected = False
    self.disconnect_failures = disconnect_failures
    self.connect_calls = 0
    self.disconnect_calls = 0
    self.orders = {}
    self.positions = {}

  async def connect(self) -> bool:
    self.connect_calls += 1
    self.is_connected = True
    return True

  async def disconnect(self) -> None:
    self.disconnect_calls += 1
    if self.disconnect_failures > 0:
      self.disconnect_failures -= 1
      raise RuntimeError("broker disconnect transient failure")
    self.is_connected = False

  def subscribe_order_updates(self, callback):
    self.order_callback = callback

  def subscribe_trade_updates(self, callback):
    self.trade_callback = callback

  async def get_position(self):
    return {}

  async def get_account(self):
    return SimpleNamespace(
      cash=100_000.0,
      total_asset=100_000.0,
      frozen_cash=0.0,
      market_value=0.0,
      total_pnl=0.0,
      daily_pnl=0.0,
    )

  def get_performance_metrics(self) -> dict:
    return {}


class FakeAdapter:
  def __init__(
    self,
    *,
    connect_result: bool = True,
    connect_error: Exception | None = None,
    connect_gate: asyncio.Event | None = None,
    subscribe_gate: asyncio.Event | None = None,
    subscribe_error: Exception | None = None,
  ) -> None:
    self.is_connected = False
    self.connect_result = connect_result
    self.connect_error = connect_error
    self.connect_gate = connect_gate
    self.connect_entered = asyncio.Event()
    self.subscribe_gate = subscribe_gate
    self.subscribe_error = subscribe_error
    self.subscribe_entered = asyncio.Event()
    self.connect_calls = 0
    self.disconnect_calls = 0
    self.unsubscribe_calls: list[str] = []
    self._next_subscription = 0

  async def connect(self) -> bool:
    self.connect_calls += 1
    self.connect_entered.set()
    if self.connect_gate is not None:
      await self.connect_gate.wait()
    if self.connect_error is not None:
      raise self.connect_error
    self.is_connected = self.connect_result
    return self.connect_result

  async def disconnect(self) -> None:
    self.disconnect_calls += 1
    await asyncio.sleep(0)
    self.is_connected = False

  async def subscribe_kline(self, **_kwargs) -> str:
    self.subscribe_entered.set()
    if self.subscribe_gate is not None:
      await self.subscribe_gate.wait()
    if self.subscribe_error is not None:
      raise self.subscribe_error
    self._next_subscription += 1
    return f"sub-{self._next_subscription}"

  async def subscribe_tick(self, **_kwargs) -> str:
    return await self.subscribe_kline(**_kwargs)

  async def unsubscribe(self, subscription_id: str) -> bool:
    self.unsubscribe_calls.append(subscription_id)
    return True


def _create_runtime(
  executor: StrategyExecutor,
  run_id: str,
  *,
  mode: StrategyRunMode = StrategyRunMode.BACKTEST,
  strategy_class: type[StrategyBase] = LifecycleStrategy,
):
  runtime = executor.create(
    run_id=run_id,
    strategy_id=1,
    strategy_class=strategy_class,
    context=StrategyContext(
      run_id=run_id,
      mode=mode,
      instruments=["600000.SH"],
      parameters={},
      initial_capital=100_000.0,
    ),
  )
  assert runtime.log_manager is not None
  runtime.log_manager.flush = AsyncMock()
  return runtime


def _sampled_v3_opportunity() -> dict:
  sample = OpportunitySample(
    instrument_code="600000.SH",
    trade_date="2026-08-20",
    source_time_ms=1_000,
    tick_ordinal=1,
    price=10.0,
    continuity_generation="1",
    received_at_ms=1_000,
    bid_price=9.99,
    ask_price=10.0,
    cumulative_amount=1_000.0,
    cumulative_volume=100.0,
  )
  return OpportunityState(
    instrument_code="600000.SH",
    trade_date="2026-08-20",
    continuity_generation="1",
    data_health=DataHealth.WARMING,
    samples=(sample,),
  ).to_dict()


def _v3_runtime_state_with_sample() -> dict:
  state = AshareIntradayTAssistantStrategy._empty_instrument_state()
  state["opportunity"] = _sampled_v3_opportunity()
  return {
    "state_schema_version": 3,
    "instrument_states": {"600000.SH": state},
    "universe_revision": 0,
  }


def _isolate_persistence(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
  calls = {"restore": 0, "checkpoint": 0, "save": 0}

  async def restore(manager: RuntimeStateManager):
    calls["restore"] += 1
    status = (
      RuntimeStateRestoreStatus.PERSISTENCE_DISABLED
      if not manager.persist_enabled
      else RuntimeStateRestoreStatus.NOT_FOUND
    )
    return RuntimeStateRestoreResult(status=status, state=manager._state)

  async def checkpoint(manager: RuntimeStateManager) -> bool:
    calls["checkpoint"] += 1
    manager._dirty = False
    return True

  async def save(manager: RuntimeStateManager) -> bool:
    calls["save"] += 1
    manager._dirty = False
    return True

  monkeypatch.setattr(RuntimeStateManager, "restore", restore)
  monkeypatch.setattr(
    RuntimeStateManager,
    "checkpoint_strategy_state_changes",
    checkpoint,
  )
  monkeypatch.setattr(RuntimeStateManager, "save_snapshot", save)
  return calls


def _install_io(
  monkeypatch: pytest.MonkeyPatch,
  *,
  mode: StrategyRunMode,
  broker: FakeBroker,
  adapter: FakeAdapter,
) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
  broker_name = "BacktestBroker" if mode == StrategyRunMode.BACKTEST else "SimulatorBroker"
  monkeypatch.setattr(executor_module, broker_name, lambda **_kwargs: broker)

  get_adapter = AsyncMock(return_value=adapter)

  async def ensure_connected(_mode, acquired_adapter) -> bool:
    return await acquired_adapter.connect()

  async def release(_mode: str) -> None:
    await adapter.disconnect()

  ensure_adapter = AsyncMock(side_effect=ensure_connected)
  release_adapter = AsyncMock(side_effect=release)
  monkeypatch.setattr(
    executor_module.adapter_manager,
    "get_adapter_for_mode",
    get_adapter,
  )
  monkeypatch.setattr(
    executor_module.adapter_manager,
    "ensure_adapter_connected_for_mode",
    ensure_adapter,
  )
  monkeypatch.setattr(
    executor_module.adapter_manager,
    "release_adapter_for_mode",
    release_adapter,
  )
  return get_adapter, ensure_adapter, release_adapter


@pytest.mark.asyncio
async def test_initialize_failure_rolls_back_without_handler_stacking(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  InitializeFailureStrategy.init_attempts = 0
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(
    executor,
    "initialize-failure",
    strategy_class=InitializeFailureStrategy,
  )
  _isolate_persistence(monkeypatch)
  attach = MagicMock()
  detach = MagicMock()
  monkeypatch.setattr(runtime.log_manager, "attach_handler", attach)
  monkeypatch.setattr(runtime.log_manager, "detach_handler", detach)

  assert await executor.start(runtime.run_id) is False
  assert await executor.start(runtime.run_id) is False

  assert InitializeFailureStrategy.init_attempts == 2
  assert runtime.status == ExecutionStatus.ERROR
  assert runtime.broker is None
  assert runtime.data_adapter is None
  assert runtime.task is None
  assert runtime.event_task is None
  assert runtime.state_manager is not None
  assert runtime.state_manager._running is False
  assert runtime.state_manager._snapshot_task is None
  assert runtime.state_manager._state_sync_task is None
  assert runtime._startup_abort_complete is True
  assert attach.call_count == 2
  assert detach.call_count == 2
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_duplicate_start_waits_for_the_same_foreground_failure(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  BlockingInitializeFailureStrategy.init_attempts = 0
  BlockingInitializeFailureStrategy.entered = asyncio.Event()
  BlockingInitializeFailureStrategy.release = asyncio.Event()
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(
    executor,
    "concurrent-start-failure",
    strategy_class=BlockingInitializeFailureStrategy,
  )
  persistence_calls = _isolate_persistence(monkeypatch)

  first = asyncio.create_task(executor.start(runtime.run_id))
  await asyncio.wait_for(
    BlockingInitializeFailureStrategy.entered.wait(),
    timeout=1.0,
  )
  second = asyncio.create_task(executor.start(runtime.run_id))
  await asyncio.sleep(0)
  assert second.done() is False

  BlockingInitializeFailureStrategy.release.set()
  assert await asyncio.gather(first, second) == [False, False]
  assert BlockingInitializeFailureStrategy.init_attempts == 1
  assert persistence_calls["restore"] == 1
  assert runtime.status == ExecutionStatus.ERROR
  assert runtime._startup_abort_complete is True
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_duplicate_stop_waits_for_the_same_failure(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(executor, "concurrent-stop-failure")
  runtime.status = ExecutionStatus.RUNNING
  entered = asyncio.Event()
  release = asyncio.Event()
  calls = 0

  async def fail_stop(_run_id: str, *, force: bool = False) -> bool:
    nonlocal calls
    del force
    calls += 1
    entered.set()
    await release.wait()
    return False

  monkeypatch.setattr(executor, "_stop_runtime", fail_stop)
  first = asyncio.create_task(executor.stop(runtime.run_id))
  await asyncio.wait_for(entered.wait(), timeout=1.0)
  second = asyncio.create_task(executor.stop(runtime.run_id))
  await asyncio.sleep(0)
  assert second.done() is False

  release.set()
  assert await asyncio.gather(first, second) == [False, False]
  assert calls == 1
  runtime.status = ExecutionStatus.STOPPED
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_stop_after_failed_start_never_writes_a_final_snapshot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(
    executor,
    "failed-start-then-stop",
    strategy_class=InitializeFailureStrategy,
  )
  persistence_calls = _isolate_persistence(monkeypatch)

  assert await executor.start(runtime.run_id) is False
  assert persistence_calls["save"] == 0
  assert await executor.stop(runtime.run_id, force=True) is True

  assert runtime.status == ExecutionStatus.STOPPED
  assert persistence_calls["save"] == 0
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_error_runtime_cannot_approve_or_mutate_pending_intent() -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(executor, "error-approval-gate")
  pending = object()
  runtime.pending_approvals["intent-1"] = pending
  runtime.status = ExecutionStatus.ERROR
  runtime.state_manager = SimpleNamespace(
    update_trade_intent_status=AsyncMock(),
  )

  result = await executor.approve_trade_intent(runtime.run_id, "intent-1")

  assert result["success"] is False
  assert result["code"] == "RUNTIME_NOT_RUNNING"
  assert runtime.pending_approvals == {"intent-1": pending}
  runtime.state_manager.update_trade_intent_status.assert_not_awaited()
  runtime.status = ExecutionStatus.STOPPED
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("connect_result", "connect_error"),
  [
    (False, None),
    (True, RuntimeError("adapter connect raised")),
  ],
)
async def test_adapter_connect_failure_releases_reference_and_broker(
  monkeypatch: pytest.MonkeyPatch,
  connect_result: bool,
  connect_error: Exception | None,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(executor, f"adapter-failure-{connect_result}")
  _isolate_persistence(monkeypatch)
  broker = FakeBroker()
  adapter = FakeAdapter(
    connect_result=connect_result,
    connect_error=connect_error,
  )
  _, ensure_adapter, release_adapter = _install_io(
    monkeypatch,
    mode=StrategyRunMode.BACKTEST,
    broker=broker,
    adapter=adapter,
  )

  assert await executor.start(runtime.run_id) is False

  assert runtime.status == ExecutionStatus.ERROR
  assert ensure_adapter.await_count == 1
  assert release_adapter.await_count == 1
  assert runtime._adapter_ref_acquired is False
  assert broker.disconnect_calls == 1
  assert broker.is_connected is False
  assert adapter.disconnect_calls == 1
  assert runtime.state_manager._snapshot_task is None
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_cancelled_start_shields_cleanup_until_resources_converge(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(executor, "cancelled-adapter-connect")
  _isolate_persistence(monkeypatch)
  broker = FakeBroker()
  adapter = FakeAdapter(connect_gate=asyncio.Event())
  _, _, release_adapter = _install_io(
    monkeypatch,
    mode=StrategyRunMode.BACKTEST,
    broker=broker,
    adapter=adapter,
  )

  start_task = asyncio.create_task(executor.start(runtime.run_id))
  await asyncio.wait_for(adapter.connect_entered.wait(), timeout=1.0)
  start_task.cancel()
  with pytest.raises(asyncio.CancelledError):
    await start_task
  assert runtime._startup_abort_task is not None
  await asyncio.wait_for(runtime._startup_abort_task, timeout=1.0)

  assert runtime.status == ExecutionStatus.ERROR
  assert runtime._startup_abort_complete is True
  assert broker.is_connected is False
  assert runtime._adapter_ref_acquired is False
  assert release_adapter.await_count == 1
  assert runtime.state_manager._snapshot_task is None
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_state_sync_start_failure_aborts_without_final_snapshot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(
    executor,
    "state-sync-failure",
    mode=StrategyRunMode.PAPER,
  )
  persistence_calls = _isolate_persistence(monkeypatch)
  broker = FakeBroker()
  adapter = FakeAdapter()
  _, _, release_adapter = _install_io(
    monkeypatch,
    mode=StrategyRunMode.PAPER,
    broker=broker,
    adapter=adapter,
  )

  async def fail_after_subscribe(manager, strategy) -> None:
    manager._state_queue = strategy.subscribe_state()
    raise RuntimeError("state sync failed after subscribe")

  monkeypatch.setattr(RuntimeStateManager, "start_state_sync", fail_after_subscribe)

  assert await executor.start(runtime.run_id) is False

  manager = runtime.state_manager
  assert manager is not None
  assert manager._running is False
  assert manager._snapshot_task is None
  assert manager._state_sync_task is None
  assert manager._state_queue is None
  assert runtime.strategy._state_subscribers == []
  assert persistence_calls["checkpoint"] == 0
  assert persistence_calls["save"] == 0
  assert broker.is_connected is False
  assert release_adapter.await_count == 1
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_realtime_initial_subscription_failure_runs_terminal_cleanup(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(
    executor,
    "subscribe-failure",
    mode=StrategyRunMode.PAPER,
  )
  _isolate_persistence(monkeypatch)
  broker = FakeBroker()
  adapter = FakeAdapter(subscribe_error=RuntimeError("subscribe failed"))
  _, _, release_adapter = _install_io(
    monkeypatch,
    mode=StrategyRunMode.PAPER,
    broker=broker,
    adapter=adapter,
  )

  assert await executor.start(runtime.run_id) is True
  await asyncio.wait_for(runtime.task, timeout=2.0)

  assert runtime.status == ExecutionStatus.ERROR
  assert runtime._terminal_cleanup_complete is True
  assert runtime.event_task.done()
  assert runtime.state_manager._running is False
  assert runtime.state_manager._snapshot_task is None
  assert runtime.state_manager._state_sync_task is None
  assert broker.is_connected is False
  assert runtime._adapter_ref_acquired is False
  assert release_adapter.await_count == 1
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_unexpected_run_loop_cancellation_converges_all_resources(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(
    executor,
    "unexpected-run-cancel",
    mode=StrategyRunMode.PAPER,
  )
  _isolate_persistence(monkeypatch)
  broker = FakeBroker()
  adapter = FakeAdapter(subscribe_gate=asyncio.Event())
  _, _, release_adapter = _install_io(
    monkeypatch,
    mode=StrategyRunMode.PAPER,
    broker=broker,
    adapter=adapter,
  )

  assert await executor.start(runtime.run_id) is True
  await asyncio.wait_for(adapter.subscribe_entered.wait(), timeout=1.0)
  runtime.task.cancel()
  with pytest.raises(asyncio.CancelledError):
    await runtime.task
  assert runtime._terminal_cleanup_task is not None
  await asyncio.wait_for(runtime._terminal_cleanup_task, timeout=1.0)

  assert runtime.status == ExecutionStatus.ERROR
  assert runtime._terminal_cleanup_complete is True
  assert runtime.event_task.done()
  assert runtime.state_manager._running is False
  assert broker.is_connected is False
  assert runtime._adapter_ref_acquired is False
  assert release_adapter.await_count == 1
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_startup_abort_retries_failed_step_without_double_adapter_release(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(executor, "retry-startup-abort")
  runtime.status = ExecutionStatus.ERROR
  runtime.strategy = LifecycleStrategy(runtime.context)
  await runtime.strategy.initialize()
  await runtime.strategy.start()
  runtime.state_manager = RuntimeStateManager(
    run_id=runtime.run_id,
    persist_enabled=False,
  )
  broker = FakeBroker(disconnect_failures=1)
  broker.is_connected = True
  runtime.broker = broker
  adapter = FakeAdapter()
  adapter.is_connected = True
  runtime.data_adapter = adapter
  runtime._adapter_ref_acquired = True

  async def release(_mode: str) -> None:
    await adapter.disconnect()

  release_adapter = AsyncMock(side_effect=release)
  monkeypatch.setattr(
    executor_module.adapter_manager,
    "release_adapter_for_mode",
    release_adapter,
  )

  await executor._ensure_startup_abort(runtime)
  assert runtime._startup_abort_complete is False
  assert runtime._adapter_ref_acquired is False

  await executor._ensure_startup_abort(runtime)
  assert runtime._startup_abort_complete is True
  assert broker.disconnect_calls == 2
  assert broker.is_connected is False
  assert release_adapter.await_count == 1
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_kind", ["startup", "terminal"])
async def test_completed_cleanup_retries_when_late_callback_breaks_convergence(
  cleanup_kind: str,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(executor, f"late-callback-{cleanup_kind}")
  runtime.status = ExecutionStatus.ERROR
  runtime.event_queue.put_nowait(
    ("order", SimpleNamespace(order_id="late-order"))
  )
  runtime.market_event_queue.put_nowait(
    RuntimeMarketEvent(
      "tick",
      SimpleNamespace(stock_code="600000.SH"),
      enqueued_at=1.0,
    )
  )

  if cleanup_kind == "startup":
    runtime._startup_abort_complete = True
    await executor._ensure_startup_abort(runtime)
    assert runtime._startup_abort_complete is True
  else:
    runtime._terminal_cleanup_complete = True
    await executor._ensure_terminal_cleanup(runtime)
    assert runtime._terminal_cleanup_complete is True

  assert runtime.event_queue.empty()
  assert runtime.event_queue._unfinished_tasks == 0
  assert runtime.market_event_queue.empty()
  assert runtime.market_event_queue._unfinished_tasks == 0
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_executor_retains_subscription_id_until_adapter_confirms_removal() -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(executor, "unsubscribe-confirmation")
  runtime.data_adapter = SimpleNamespace(
    unsubscribe=AsyncMock(return_value=False),
  )
  runtime.realtime_subscription_ids = {"600000.SH": ["sub-1"]}

  with pytest.raises(RuntimeError, match="实时订阅取消失败: sub-1"):
    await executor._clear_realtime_subscriptions(runtime)

  assert runtime.realtime_subscription_ids == {"600000.SH": ["sub-1"]}
  runtime.data_adapter.unsubscribe.return_value = True
  await executor._clear_realtime_subscriptions(runtime)
  assert runtime.realtime_subscription_ids == {}
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_failed_old_cleanup_blocks_new_start_until_retry_converges(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  InitializeFailureStrategy.init_attempts = 0
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(
    executor,
    "old-cleanup-gate",
    strategy_class=InitializeFailureStrategy,
  )
  persistence_calls = _isolate_persistence(monkeypatch)
  runtime.status = ExecutionStatus.ERROR
  runtime.strategy = LifecycleStrategy(runtime.context)
  await runtime.strategy.initialize()
  await runtime.strategy.start()
  runtime.state_manager = RuntimeStateManager(
    run_id=runtime.run_id,
    persist_enabled=False,
  )
  old_broker = FakeBroker(disconnect_failures=1)
  old_broker.is_connected = True
  runtime.broker = old_broker
  runtime.pending_approvals["stale-intent"] = object()
  runtime.t_trade_entry_reservations["stale-intent"] = {"amount": 1.0}
  runtime.latest_market_data["600000.SH"] = object()

  assert await executor.start(runtime.run_id) is False
  assert InitializeFailureStrategy.init_attempts == 0
  assert persistence_calls["restore"] == 0

  assert await executor.start(runtime.run_id) is False
  assert old_broker.disconnect_calls == 2
  assert old_broker.is_connected is False
  assert InitializeFailureStrategy.init_attempts == 1
  assert persistence_calls["restore"] == 1
  assert runtime.pending_approvals == {}
  assert runtime.t_trade_entry_reservations == {}
  assert runtime.latest_market_data == {}
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_normal_stop_awaits_adapter_release(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(
    executor,
    "normal-stop-release",
    mode=StrategyRunMode.PAPER,
  )
  _isolate_persistence(monkeypatch)
  broker = FakeBroker()
  adapter = FakeAdapter()
  _, _, release_adapter = _install_io(
    monkeypatch,
    mode=StrategyRunMode.PAPER,
    broker=broker,
    adapter=adapter,
  )

  assert await executor.start(runtime.run_id) is True
  assert await executor.stop(runtime.run_id, force=True) is True

  assert runtime.status == ExecutionStatus.STOPPED
  release_adapter.assert_awaited_once_with("paper")
  assert adapter.disconnect_calls == 1
  assert runtime._adapter_ref_acquired is False
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_stop_state_sync_failure_retains_broker_and_adapter_ownership(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(executor, "stop-sync-failure")
  runtime.status = ExecutionStatus.RUNNING
  runtime.strategy = LifecycleStrategy(runtime.context)
  await runtime.strategy.initialize()
  await runtime.strategy.start()
  runtime.state_manager = SimpleNamespace(
    stop_state_sync=AsyncMock(side_effect=RuntimeError("sync not authoritative")),
    stop=AsyncMock(),
  )
  broker = FakeBroker()
  broker.is_connected = True
  runtime.broker = broker
  runtime.data_adapter = FakeAdapter()
  runtime._adapter_ref_acquired = True
  release_adapter = AsyncMock()
  monkeypatch.setattr(
    executor_module.adapter_manager,
    "release_adapter_for_mode",
    release_adapter,
  )

  assert await executor.stop(runtime.run_id, force=True) is False

  assert runtime.status == ExecutionStatus.ERROR
  assert runtime._terminal_cleanup_complete is False
  runtime.state_manager.stop.assert_not_awaited()
  assert broker.disconnect_calls == 0
  assert broker.is_connected is True
  assert runtime._adapter_ref_acquired is True
  release_adapter.assert_not_awaited()
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_external_stop_cleanup_never_treats_live_producer_as_owner(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(executor, "external-stop-owner")
  runtime.status = ExecutionStatus.RUNNING
  runtime.strategy = LifecycleStrategy(runtime.context)
  await runtime.strategy.initialize()
  await runtime.strategy.start()
  runtime.state_manager = SimpleNamespace(
    stop_state_sync=AsyncMock(),
    stop=AsyncMock(),
  )
  broker = FakeBroker()
  broker.is_connected = True
  runtime.broker = broker
  runtime.task = asyncio.create_task(asyncio.Event().wait())
  owners: list[asyncio.Task | None] = []

  async def fail_quiesce(_runtime, *, owner_task=None) -> None:
    owners.append(owner_task)
    raise RuntimeError("producer still alive")

  monkeypatch.setattr(executor, "_quiesce_runtime_tasks", fail_quiesce)

  assert await executor.stop(runtime.run_id, force=True) is False

  assert len(owners) >= 2
  assert all(owner is None for owner in owners)
  assert runtime._terminal_cleanup_complete is False
  runtime.state_manager.stop.assert_not_awaited()
  assert broker.disconnect_calls == 0
  runtime.task.cancel()
  await asyncio.gather(runtime.task, return_exceptions=True)
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_stop_drains_callbacks_emitted_by_disconnect_and_adapter_release(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(executor, "disconnect-final-drain")
  runtime.status = ExecutionStatus.RUNNING
  runtime.strategy = LifecycleStrategy(runtime.context)
  await runtime.strategy.initialize()
  await runtime.strategy.start()
  runtime.state_manager = SimpleNamespace(
    stop_state_sync=AsyncMock(),
    stop=AsyncMock(),
  )

  broker = FakeBroker()
  broker.is_connected = True

  async def disconnect_with_callback() -> None:
    broker.disconnect_calls += 1
    executor._put_runtime_control_event_nowait(
      runtime,
      ("order", SimpleNamespace(order_id="late-order")),
    )
    broker.is_connected = False

  broker.disconnect = disconnect_with_callback
  runtime.broker = broker
  runtime.data_adapter = FakeAdapter()
  runtime._adapter_ref_acquired = True

  async def release_with_callback(_mode: str) -> None:
    runtime.market_event_queue.put_nowait(
      RuntimeMarketEvent(
        "tick",
        SimpleNamespace(stock_code="600000.SH"),
        enqueued_at=1.0,
      )
    )

  monkeypatch.setattr(
    executor_module.adapter_manager,
    "release_adapter_for_mode",
    release_with_callback,
  )

  assert await executor.stop(runtime.run_id, force=True) is True

  assert runtime.event_queue.empty()
  assert runtime.event_queue._unfinished_tasks == 0
  assert runtime.market_event_queue.empty()
  assert runtime.market_event_queue._unfinished_tasks == 0
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stop", "pause"])
async def test_pause_and_stop_recheck_lifecycle_blocker_under_approval_lock(
  operation: str,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(executor, f"approval-race-{operation}")
  runtime.status = ExecutionStatus.RUNNING
  broker = FakeBroker()
  broker.is_connected = True
  runtime.broker = broker

  await runtime.approval_lock.acquire()
  operation_task = asyncio.create_task(
    executor.stop(runtime.run_id) if operation == "stop" else executor.pause(runtime.run_id)
  )
  await asyncio.sleep(0)
  runtime.pending_approvals["new-intent"] = object()
  runtime.approval_lock.release()

  assert await operation_task is False
  assert runtime.status == ExecutionStatus.RUNNING
  assert broker.disconnect_calls == 0
  runtime.pending_approvals.clear()
  runtime.status = ExecutionStatus.STOPPED
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_stop_all_is_bounded_and_reports_active_lifecycle_task(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(executor, "shutdown-owned-lifecycle")
  runtime.status = ExecutionStatus.RUNNING
  owned_lifecycle = asyncio.create_task(
    asyncio.Event().wait(),
    name="owned-lifecycle-never-finishes",
  )
  runtime._lifecycle_operation_task = owned_lifecycle
  runtime._lifecycle_operation_kind = "start"

  async def blocked_stop(_run_id: str, *, force: bool = False) -> bool:
    del force
    await asyncio.Event().wait()
    return True

  monkeypatch.setattr(executor, "stop", blocked_stop)

  with pytest.raises(RuntimeError, match="owned lifecycle/cleanup timeout"):
    await asyncio.wait_for(executor.stop_all_runs(timeout=0.01), timeout=0.5)

  owned_lifecycle.cancel()
  await asyncio.gather(owned_lifecycle, return_exceptions=True)
  runtime._lifecycle_operation_task = None
  runtime.status = ExecutionStatus.STOPPED
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("strategy_class", "handled"),
  [
    (AshareIntradayTAssistantStrategy, True),
    (ContinuityBlindStrategy, False),
  ],
)
async def test_startup_replays_restored_continuity_gate_before_checkpoint(
  monkeypatch: pytest.MonkeyPatch,
  strategy_class: type[StrategyBase],
  handled: bool,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(
    executor,
    f"startup-restored-gate-{handled}",
    mode=StrategyRunMode.PAPER,
    strategy_class=strategy_class,
  )
  if handled:
    runtime.context.parameters["account_id"] = "account-1"
  broker = FakeBroker()
  adapter = FakeAdapter()
  _install_io(
    monkeypatch,
    mode=StrategyRunMode.PAPER,
    broker=broker,
    adapter=adapter,
  )
  checkpoint_snapshots: list[dict] = []

  async def restore(manager: RuntimeStateManager):
    restored_custom = {
      MARKET_CONTINUITY_RECONCILE_REQUIRED_KEY: {
        "600000.SH": "MARKET_EVENT_QUEUE_OVERFLOW",
      }
    }
    if handled:
      restored_custom.update(_v3_runtime_state_with_sample())
    manager._state["custom"] = restored_custom
    return RuntimeStateRestoreResult(
      status=RuntimeStateRestoreStatus.RESTORED,
      state=manager._state,
    )

  async def checkpoint(manager: RuntimeStateManager) -> bool:
    checkpoint_snapshots.append(copy.deepcopy(manager._state["custom"]))
    manager._dirty = False
    return True

  async def save(manager: RuntimeStateManager) -> bool:
    manager._dirty = False
    return True

  monkeypatch.setattr(RuntimeStateManager, "restore", restore)
  monkeypatch.setattr(
    RuntimeStateManager,
    "checkpoint_strategy_state_changes",
    checkpoint,
  )
  monkeypatch.setattr(RuntimeStateManager, "save_snapshot", save)
  if handled:
    monkeypatch.setattr(
      RuntimeStateManager,
      "restore_v3_manual_candidate_intents",
      AsyncMock(return_value=[]),
    )

  assert await executor.start(runtime.run_id) is True

  manager = runtime.state_manager
  assert manager is not None
  durable_gates = manager.market_continuity_reconciliation()
  if handled:
    assert durable_gates == {}
    opportunity = manager.get_custom("instrument_states")["600000.SH"][
      "opportunity"
    ]
    assert opportunity["samples"] == []
    assert opportunity["candidate"] is None
    assert opportunity["continuity_generation"] == "invalidated:1"
    assert checkpoint_snapshots[-1]["instrument_states"]["600000.SH"][
      "opportunity"
    ] == opportunity
    assert checkpoint_snapshots[-1].get(
      MARKET_CONTINUITY_RECONCILE_REQUIRED_KEY,
      {},
    ) == {}
  else:
    assert durable_gates == {
      "600000.SH": "MARKET_EVENT_QUEUE_OVERFLOW",
    }
    assert runtime._market_fail_closed_codes == {
      "600000.SH": "MARKET_EVENT_QUEUE_OVERFLOW",
    }
    assert checkpoint_snapshots[-1][
      MARKET_CONTINUITY_RECONCILE_REQUIRED_KEY
    ] == durable_gates

  assert await executor.stop(runtime.run_id, force=True) is True
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_runtime_continuity_gate_uses_two_phase_durable_clear(
  tmp_path,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _create_runtime(
    executor,
    "runtime-two-phase-gate",
    mode=StrategyRunMode.PAPER,
    strategy_class=AshareIntradayTAssistantStrategy,
  )
  strategy = AshareIntradayTAssistantStrategy(runtime.context)
  await strategy.initialize()
  runtime.strategy = strategy
  manager = RuntimeStateManager(
    run_id=runtime.run_id,
    persist_enabled=True,
    log_dir=str(tmp_path),
  )
  manager._running = True
  await manager.start_state_sync(strategy)
  runtime.state_manager = manager
  strategy.state.update(_v3_runtime_state_with_sample())
  await asyncio.wait_for(manager._state_queue.join(), timeout=1.0)
  save_attempts: list[dict] = []
  committed: list[dict] = []
  outcomes = iter((True, False))

  async def save_snapshot() -> bool:
    snapshot = copy.deepcopy(manager._state)
    save_attempts.append(snapshot)
    saved = next(outcomes)
    if saved:
      committed.append(snapshot)
      manager._dirty = False
    return saved

  manager.save_snapshot = save_snapshot
  executor._mark_runtime_market_continuity_lost(
    runtime,
    ["600000.SH"],
    reason="MARKET_EVENT_QUEUE_OVERFLOW",
  )

  await executor._apply_pending_runtime_market_invalidations(runtime)

  assert len(save_attempts) == 2
  assert len(committed) == 1
  durable_custom = committed[0]["custom"]
  assert durable_custom[MARKET_CONTINUITY_RECONCILE_REQUIRED_KEY] == {
    "600000.SH": "MARKET_EVENT_QUEUE_OVERFLOW",
  }
  durable_opportunity = durable_custom["instrument_states"]["600000.SH"][
    "opportunity"
  ]
  assert durable_opportunity["samples"] == []
  assert durable_opportunity["candidate"] is None
  assert durable_opportunity["continuity_generation"] == "invalidated:1"
  assert save_attempts[1]["custom"].get(
    MARKET_CONTINUITY_RECONCILE_REQUIRED_KEY,
    {},
  ) == {}
  assert save_attempts[1]["custom"]["instrument_states"]["600000.SH"][
    "opportunity"
  ] == durable_opportunity
  assert runtime._market_fail_closed_codes == {
    "600000.SH": "MARKET_EVENT_QUEUE_OVERFLOW",
  }
  assert runtime._market_invalidation_checkpoints == {"600000.SH": 1}

  await manager.stop_state_sync(strategy)
  manager._running = False
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_runtime_state_stop_is_idempotent_after_success_and_retries_failure(
  tmp_path,
) -> None:
  manager = RuntimeStateManager(
    run_id="state-stop-retry",
    persist_enabled=True,
    log_dir=str(tmp_path),
  )
  manager.save_snapshot = AsyncMock(side_effect=[False, True])
  await manager.start()

  with pytest.raises(RuntimeError, match="最终状态快照保存失败"):
    await manager.stop()
  await manager.stop()
  await manager.stop()

  assert manager.save_snapshot.await_count == 2
  assert manager._final_snapshot_saved is True
  assert manager._snapshot_task is None


class SharedAdapter(FakeAdapter):
  def __init__(self, *, leave_connected_once: bool = False) -> None:
    super().__init__()
    self.leave_connected_once = leave_connected_once

  async def connect(self) -> bool:
    self.connect_calls += 1
    await asyncio.sleep(0.01)
    self.is_connected = True
    return True

  async def disconnect(self) -> None:
    self.disconnect_calls += 1
    if self.leave_connected_once:
      self.leave_connected_once = False
      return
    self.is_connected = False


@pytest.mark.asyncio
async def test_adapter_manager_serializes_shared_connect(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  manager = AdapterManager()
  adapter = SharedAdapter()
  monkeypatch.setattr(manager, "_historical_adapter", adapter)
  monkeypatch.setattr(manager, "_ref_counts", defaultdict(int))
  monkeypatch.setattr(manager, "_lifecycle_locks", defaultdict(asyncio.Lock))

  acquired = await asyncio.gather(
    manager.get_adapter_for_mode(StrategyRunMode.BACKTEST),
    manager.get_adapter_for_mode(StrategyRunMode.BACKTEST),
  )
  assert acquired == [adapter, adapter]
  assert await asyncio.gather(
    manager.ensure_adapter_connected_for_mode(StrategyRunMode.BACKTEST, adapter),
    manager.ensure_adapter_connected_for_mode(StrategyRunMode.BACKTEST, adapter),
  ) == [True, True]

  assert adapter.connect_calls == 1
  assert manager._ref_counts["historical"] == 2
  await manager.release_adapter_for_mode("backtest")
  await manager.release_adapter_for_mode("backtest")
  assert adapter.disconnect_calls == 1
  assert manager._ref_counts["historical"] == 0


@pytest.mark.asyncio
async def test_failed_final_release_does_not_consume_new_owner_reference(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  manager = AdapterManager()
  adapter = SharedAdapter(leave_connected_once=True)
  adapter.is_connected = True
  monkeypatch.setattr(manager, "_historical_adapter", adapter)
  monkeypatch.setattr(manager, "_ref_counts", defaultdict(int))
  monkeypatch.setattr(manager, "_lifecycle_locks", defaultdict(asyncio.Lock))

  await manager.get_adapter_for_mode(StrategyRunMode.BACKTEST)
  with pytest.raises(RuntimeError, match="仍报告 connected"):
    await manager.release_adapter_for_mode("backtest")
  assert manager._ref_counts["historical"] == 1

  await manager.get_adapter_for_mode(StrategyRunMode.BACKTEST)
  assert manager._ref_counts["historical"] == 2
  await manager.release_adapter_for_mode("backtest")
  assert manager._ref_counts["historical"] == 1
  assert adapter.is_connected is True

  await manager.release_adapter_for_mode("backtest")
  assert manager._ref_counts["historical"] == 0
  assert adapter.is_connected is False
  assert adapter.disconnect_calls == 2


@pytest.mark.asyncio
async def test_live_broker_disconnect_awaits_monitor_task(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  service = SimpleNamespace(
    get_account_info=AsyncMock(
      return_value=SimpleNamespace(
        account_id="mock-account",
        cash=100_000.0,
      )
    )
  )
  from quantx_infrastructure.services import trading_service as trading_service_module

  monkeypatch.setattr(
    trading_service_module,
    "TradingService",
    lambda **_kwargs: service,
  )
  broker = LiveBroker(account_id="mock-account")

  assert await broker.connect() is True
  monitor_task = broker._monitor_task
  assert monitor_task is not None
  assert monitor_task.done() is False

  await broker.disconnect()

  assert broker._monitor_task is None
  assert monitor_task.done() is True
  assert broker.is_connected is False
