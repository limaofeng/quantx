from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.base import (
  StrategyContext,
  StrategyOutput,
  StrategyRunMode,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_engine import strategy_executor as strategy_executor_module
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  RuntimeConsumerUnavailable,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager


def _runtime(run_id: str = "run-lifecycle") -> StrategyRuntime:
  return StrategyRuntime(
    run_id=run_id,
    name=run_id,
    strategy_id=1,
    strategy_class=object,
    context=StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.LIVE,
      instruments=["600000.SH"],
      parameters={},
    ),
  )


@pytest.mark.asyncio
async def test_stop_quiesces_current_event_before_snapshot_and_disconnect(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime()
  runtime.status = ExecutionStatus.RUNNING
  calls: list[str] = []
  event_started = asyncio.Event()
  release_event = asyncio.Event()

  class Strategy:
    logger = logging.getLogger("lifecycle-test")
    state = SimpleNamespace(to_dict=lambda: {})

    async def stop(self) -> None:
      calls.append("strategy-stop")

  class StateManager:
    _reservations: dict[str, float] = {}
    _position_reservations: dict[str, dict[str, int]] = {}

    async def stop_state_sync(self, _strategy) -> None:
      calls.append("state-sync-stop")

    async def stop(self) -> None:
      calls.append("snapshot-stop")

  class Broker:
    orders: dict[str, object] = {}

    async def disconnect(self) -> None:
      calls.append("broker-disconnect")

  runtime.strategy = Strategy()
  runtime.state_manager = StateManager()
  runtime.broker = Broker()
  executor.runs[runtime.run_id] = runtime
  process_intent = AsyncMock()
  monkeypatch.setattr(executor, "_process_trade_intent", process_intent)

  async def process_tick(_runtime, _tick) -> None:
    event_started.set()
    await release_event.wait()
    await executor._process_strategy_output(
      runtime,
      StrategyOutput(trade_intents=[object()]),
    )
    calls.append("event-finished")

  monkeypatch.setattr(executor, "_process_tick", process_tick)
  await runtime.event_queue.put(("tick", object()))
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  await event_started.wait()

  stop_task = asyncio.create_task(executor.stop(runtime.run_id))
  while runtime.status != ExecutionStatus.STOPPING:
    await asyncio.sleep(0)
  release_event.set()

  assert await stop_task is True
  assert process_intent.await_count == 0
  assert calls == [
    "event-finished",
    "strategy-stop",
    "state-sync-stop",
    "snapshot-stop",
    "broker-disconnect",
  ]
  assert runtime.event_queue._unfinished_tasks == 0
  assert runtime.status == ExecutionStatus.STOPPED
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_stop_snapshot_failure_keeps_broker_connected_and_enters_error(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime("run-stop-snapshot-failure")
  runtime.status = ExecutionStatus.RUNNING

  class Strategy:
    logger = logging.getLogger("stop-snapshot-failure-test")
    state = SimpleNamespace(to_dict=lambda: {})

    async def stop(self) -> None:
      return None

  state_manager = RuntimeStateManager(
    run_id=runtime.run_id,
    persist_enabled=True,
  )
  save_snapshot = AsyncMock(return_value=False)
  monkeypatch.setattr(state_manager, "save_snapshot", save_snapshot)
  broker = SimpleNamespace(
    orders={},
    disconnect=AsyncMock(),
  )
  runtime.strategy = Strategy()
  runtime.state_manager = state_manager
  runtime.broker = broker
  executor.runs[runtime.run_id] = runtime

  assert await executor.stop(runtime.run_id) is False
  # The normal stop attempt and its owned terminal-cleanup retry both preserve
  # broker ownership when the authoritative snapshot remains unavailable.
  assert save_snapshot.await_count == 2
  broker.disconnect.assert_not_awaited()
  assert runtime.status == ExecutionStatus.ERROR
  assert runtime._terminal_cleanup_complete is False
  assert "最终状态快照保存失败" in (runtime.error_message or "")
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_pause_and_stop_refuse_unresolved_durable_lifecycle() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("run-blocked")
  runtime.status = ExecutionStatus.RUNNING
  runtime.durable_event_barrier_key = "trade:pending"
  executor.runs[runtime.run_id] = runtime

  assert await executor.pause(runtime.run_id) is False
  assert await executor.stop(runtime.run_id) is False
  assert runtime.status == ExecutionStatus.RUNNING
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "status",
  [ExecutionStatus.PENDING, ExecutionStatus.STOPPED],
)
async def test_unstarted_or_stopped_runtime_rejects_durable_apply(status) -> None:
  executor = StrategyExecutor()
  runtime = _runtime(f"run-{status.value.lower()}")
  runtime.status = status
  runtime.state_manager = object()
  executor.runs[runtime.run_id] = runtime

  with pytest.raises(RuntimeConsumerUnavailable, match=status.value):
    await executor.apply_durable_order_report(runtime.run_id, object())
  assert runtime.event_queue.qsize() == 0
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_stop_racing_manual_intent_persistence_rejects_instead_of_queuing(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime("run-manual-race")
  runtime.status = ExecutionStatus.RUNNING
  record_started = asyncio.Event()
  release_record = asyncio.Event()
  statuses: list[str] = []

  class Strategy:
    logger = logging.getLogger("manual-race-test")
    state = SimpleNamespace(to_dict=lambda: {})

    def record_trade_intent(self, _intent) -> None:
      return None

    def on_order(self, _event) -> None:
      return None

    async def stop(self) -> None:
      return None

  class StateManager:
    _reservations: dict[str, float] = {}
    _position_reservations: dict[str, dict[str, int]] = {}

    async def record_trade_intent(self, _intent, *, status: str) -> None:
      statuses.append(status)
      record_started.set()
      await release_record.wait()

    async def update_trade_intent_status(self, _intent_id, status: str, **_kwargs) -> None:
      statuses.append(status)

    async def stop_state_sync(self, _strategy) -> None:
      return None

    async def stop(self) -> None:
      return None

  class Broker:
    orders: dict[str, object] = {}

    async def disconnect(self) -> None:
      return None

  runtime.strategy = Strategy()
  runtime.state_manager = StateManager()
  runtime.broker = Broker()
  executor.runs[runtime.run_id] = runtime
  process_intent = AsyncMock()
  monkeypatch.setattr(executor, "_process_trade_intent", process_intent)
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="race-test",
    target_volume=100,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
  )
  output_task = asyncio.create_task(
    executor._process_strategy_output(
      runtime,
      StrategyOutput(trade_intents=[intent]),
    )
  )
  runtime.event_task = output_task
  await record_started.wait()

  stop_task = asyncio.create_task(executor.stop(runtime.run_id))
  while runtime.status != ExecutionStatus.STOPPING:
    await asyncio.sleep(0)
  release_record.set()

  assert await stop_task is True
  assert statuses == ["AWAITING_APPROVAL", "REJECTED"]
  assert runtime.pending_approvals == {}
  process_intent.assert_not_awaited()
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_paused_consumer_defers_then_resume_applies_durable_event() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("run-paused")
  runtime.status = ExecutionStatus.PAUSED
  runtime.state_manager = object()
  executor.runs[runtime.run_id] = runtime

  async def consume_one() -> None:
    _event_type, (_payload, completion) = await runtime.event_queue.get()
    try:
      completion.set_result(True)
    finally:
      runtime.event_queue.task_done()

  runtime.event_task = asyncio.create_task(consume_one())
  with pytest.raises(RuntimeConsumerUnavailable):
    await executor.apply_durable_order_report(runtime.run_id, object())

  assert await executor.resume(runtime.run_id) is True
  await executor.apply_durable_order_report(runtime.run_id, object())
  await runtime.event_task
  assert runtime.event_queue._unfinished_tasks == 0
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_durable_apply_times_out_when_consumer_stalls(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime("run-stalled")
  runtime.status = ExecutionStatus.RUNNING
  runtime.state_manager = object()
  executor.runs[runtime.run_id] = runtime
  never = asyncio.Event()
  runtime.event_task = asyncio.create_task(never.wait())
  monkeypatch.setattr(
    strategy_executor_module,
    "_DURABLE_EVENT_APPLY_TIMEOUT_SECONDS",
    0.01,
  )

  with pytest.raises(RuntimeConsumerUnavailable, match="未在时限内"):
    await executor.apply_durable_trade_report(runtime.run_id, object())

  runtime.status = ExecutionStatus.STOPPING
  runtime.event_task.cancel()
  await asyncio.gather(runtime.event_task, return_exceptions=True)
  await executor._quiesce_runtime_tasks(runtime)
  assert runtime.event_queue._unfinished_tasks == 0
  executor.thread_pool.shutdown(wait=False)
