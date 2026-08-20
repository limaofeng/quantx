from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.base import (
  RuntimeStatePatch,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_engine import strategy_executor as strategy_executor_module
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  RuntimeMarketEvent,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.tick import Tick


class _ContinuityAwareStrategy:
  def __init__(self) -> None:
    self.invalidations: list[tuple[str, str]] = []

  def invalidate_realtime_market_window(
    self,
    instrument_code: str,
    *,
    reason: str,
  ) -> bool:
    self.invalidations.append((instrument_code, reason))
    return True

  def on_order(self, _event: object) -> None:
    return None


class _ContinuityBlindStrategy(_ContinuityAwareStrategy):
  def invalidate_realtime_market_window(
    self,
    instrument_code: str,
    *,
    reason: str,
  ) -> bool:
    self.invalidations.append((instrument_code, reason))
    return False


class _SuccessfulCheckpointStateManager:
  def __init__(self) -> None:
    self.updates: list[tuple[str, str, dict]] = []

  async def checkpoint_strategy_state_changes(self) -> bool:
    return True

  async def force_save(self) -> bool:
    return True

  async def update_trade_intent_status(
    self,
    intent_id: str,
    status: str,
    **updates,
  ) -> None:
    self.updates.append((intent_id, status, updates))

  def record_decision_trace(self, _trace: object) -> None:
    return None


def _runtime(run_id: str = "market-queue") -> StrategyRuntime:
  runtime = StrategyRuntime(
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
    status=ExecutionStatus.RUNNING,
  )
  runtime.state_manager = _SuccessfulCheckpointStateManager()
  return runtime


def _event(stock_code: str = "600000.SH") -> SimpleNamespace:
  return SimpleNamespace(stock_code=stock_code, time=time_utils.now())


def _trade_intent(
  run_id: str,
  *,
  manual: bool = False,
  metadata: dict | None = None,
) -> TradeIntent:
  return TradeIntent(
    strategy_id="1",
    run_id=run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="continuity-test",
    target_volume=100,
    limit_price_hint=10.0,
    execution_mode=(
      TradeIntentExecutionMode.MANUAL_CONFIRM
      if manual
      else TradeIntentExecutionMode.AUTO
    ),
    metadata=dict(metadata or {}),
  )


@pytest.mark.asyncio
async def test_market_queue_overflow_is_bounded_observable_and_balances_tasks() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("overflow")
  runtime.market_event_queue = asyncio.Queue(maxsize=2)
  runtime.strategy = _ContinuityAwareStrategy()
  executor.runs[runtime.run_id] = runtime

  executor._enqueue_runtime_market_event(runtime, "tick", _event())
  executor._enqueue_runtime_market_event(runtime, "tick", _event())
  executor._enqueue_runtime_market_event(runtime, "tick", _event())

  assert runtime.market_event_queue.qsize() == 1
  assert runtime.market_event_overflows == 1
  assert runtime.market_events_dropped == 2
  assert runtime.market_queue_high_watermark == 2
  assert runtime._pending_market_invalidations == {
    "600000.SH": "MARKET_EVENT_QUEUE_OVERFLOW"
  }

  await executor._apply_pending_runtime_market_invalidations(runtime)
  assert runtime.strategy.invalidations == [
    ("600000.SH", "MARKET_EVENT_QUEUE_OVERFLOW")
  ]
  executor._drain_runtime_market_queue(runtime)
  assert runtime.market_event_queue._unfinished_tasks == 0

  queue_stats = executor.get_statistics()["market_event_queues"][runtime.run_id]
  assert queue_stats["capacity"] == 2
  assert queue_stats["overflows"] == 1
  assert queue_stats["dropped"] == 3
  assert queue_stats["window_invalidations"] == 1
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_inflight_tick_cannot_route_after_continuity_generation_changes(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime("inflight-overflow")
  runtime.strategy = _ContinuityAwareStrategy()
  runtime.broker = SimpleNamespace(place_order=AsyncMock())
  route_intent = AsyncMock()
  monkeypatch.setattr(executor, "_process_trade_intent", route_intent)
  started = asyncio.Event()
  release = asyncio.Event()

  async def process_tick(_runtime: StrategyRuntime, tick: object) -> None:
    started.set()
    await release.wait()
    strategy_input = StrategyInput(
      run_id=runtime.run_id,
      strategy_id="1",
      timestamp=tick.time,
      cadence=StrategyCadence.TICK,
      instrument_code=tick.stock_code,
      event=tick,
    )
    await executor._process_strategy_output(
      runtime,
      StrategyOutput(trade_intents=[_trade_intent(runtime.run_id)]),
      strategy_input,
    )

  monkeypatch.setattr(executor, "_process_tick", process_tick)
  executor._enqueue_runtime_market_event(runtime, "tick", _event())
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  await asyncio.wait_for(started.wait(), timeout=1.0)

  executor._mark_runtime_market_continuity_lost(
    runtime,
    ["600000.SH"],
    reason="MARKET_EVENT_QUEUE_OVERFLOW",
  )
  executor._mark_runtime_market_continuity_lost(
    runtime,
    ["600000.SH"],
    reason="MARKET_EVENT_QUEUE_OVERFLOW",
  )
  release.set()
  await asyncio.wait_for(runtime.market_event_queue.join(), timeout=1.0)
  runtime.status = ExecutionStatus.STOPPED
  runtime._event_queue_wakeup.set()
  await asyncio.wait_for(runtime.event_task, timeout=1.0)

  route_intent.assert_not_awaited()
  runtime.broker.place_order.assert_not_awaited()
  assert runtime._market_continuity_generations["600000.SH"] == 2
  assert runtime.strategy.invalidations == [
    ("600000.SH", "MARKET_EVENT_QUEUE_OVERFLOW")
  ]
  assert runtime._processing_market_events == {}
  assert runtime.market_event_queue._unfinished_tasks == 0
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_total_processing_age_drops_state_patch_and_invalidates_immediately(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime("processing-age")
  runtime.strategy = AshareIntradayTAssistantStrategy(runtime.context)
  clock = [100.0]
  monkeypatch.setattr(strategy_executor_module, "monotonic", lambda: clock[0])

  async def process_tick(_runtime: StrategyRuntime, tick: object) -> None:
    clock[0] = 111.0
    await executor._process_strategy_output(
      runtime,
      StrategyOutput(
        runtime_state_patch=RuntimeStatePatch(set={"stale_patch": True})
      ),
      StrategyInput(
        run_id=runtime.run_id,
        strategy_id="1",
        timestamp=tick.time,
        cadence=StrategyCadence.TICK,
        instrument_code=tick.stock_code,
        event=tick,
      ),
    )

  monkeypatch.setattr(executor, "_process_tick", process_tick)
  executor._enqueue_runtime_market_event(runtime, "tick", _event())
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  await asyncio.wait_for(runtime.market_event_queue.join(), timeout=1.0)
  runtime.status = ExecutionStatus.STOPPED
  runtime._event_queue_wakeup.set()
  await asyncio.wait_for(runtime.event_task, timeout=1.0)

  assert runtime.strategy.state.get("stale_patch") is None
  assert runtime.strategy.state["signal_window_rewarm"]["instruments"] == {
    "600000.SH": {
      "reason": "MARKET_EVENT_PROCESSING_EXPIRED",
      "started_at_ms": 0,
    }
  }
  assert runtime.market_events_expired == 1
  assert runtime.market_events_dropped == 1
  assert runtime.market_events_processed == 0
  assert runtime.market_event_queue._unfinished_tasks == 0
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_invalidation_is_durable_before_market_gate_reopens() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("durable-invalidation")
  strategy = AshareIntradayTAssistantStrategy(runtime.context)
  await strategy.initialize()
  runtime.strategy = strategy
  manager = RuntimeStateManager(run_id=runtime.run_id, persist_enabled=True)
  manager._running = True
  await manager.start_state_sync(strategy)
  runtime.state_manager = manager
  durable_snapshots: list[dict] = []

  async def save_snapshot() -> bool:
    durable_snapshots.append(copy.deepcopy(manager._state))
    manager._dirty = False
    return True

  manager.save_snapshot = save_snapshot
  old_window = {
    "version": 1,
    "instruments": {
      "600000.SH": [[1, 10.0, 9.99, 10.01, 1000.0, 100.0]],
    },
  }
  strategy._samples_by_instrument = strategy._decode_signal_sample_windows(old_window)
  strategy.state.set("signal_sample_windows", old_window)
  await asyncio.wait_for(manager._state_queue.join(), timeout=1.0)
  assert await manager.save_snapshot() is True
  assert durable_snapshots[-1]["custom"]["signal_sample_windows"] == old_window

  executor._mark_runtime_market_continuity_lost(
    runtime,
    ["600000.SH"],
    reason="MARKET_EVENT_QUEUE_OVERFLOW",
  )
  await executor._apply_pending_runtime_market_invalidations(runtime)

  assert "600000.SH" not in runtime._market_fail_closed_codes
  restored_manager = RuntimeStateManager(
    run_id=runtime.run_id,
    persist_enabled=False,
  )
  restored_manager._state = copy.deepcopy(durable_snapshots[-1])
  restored_strategy = AshareIntradayTAssistantStrategy(runtime.context)
  restored_strategy.apply_state_snapshot(restored_manager.get_custom_state())
  await restored_strategy.initialize()
  assert restored_strategy._samples_by_instrument.get("600000.SH") is None
  assert restored_strategy.state["signal_window_rewarm"]["instruments"] == {
    "600000.SH": {
      "reason": "MARKET_EVENT_QUEUE_OVERFLOW",
      "started_at_ms": 0,
    }
  }

  await manager.stop_state_sync(strategy)
  manager._running = False
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_failed_invalidation_checkpoint_keeps_market_gate_closed() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("failed-invalidation-checkpoint")
  runtime.strategy = _ContinuityAwareStrategy()
  runtime.state_manager = SimpleNamespace(
    checkpoint_strategy_state_changes=AsyncMock(return_value=False)
  )
  executor._mark_runtime_market_continuity_lost(
    runtime,
    ["600000.SH"],
    reason="MARKET_EVENT_QUEUE_OVERFLOW",
  )

  await executor._apply_pending_runtime_market_invalidations(runtime)

  assert runtime._market_fail_closed_codes == {
    "600000.SH": "MARKET_EVENT_QUEUE_OVERFLOW"
  }
  assert runtime._market_invalidation_checkpoints == {"600000.SH": 1}
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_continuity_blind_strategy_blocks_direct_and_manual_intents() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("blind-fail-closed")
  runtime.strategy = _ContinuityBlindStrategy()
  runtime.broker = SimpleNamespace(place_order=AsyncMock())
  executor.runs[runtime.run_id] = runtime
  executor._mark_runtime_market_continuity_lost(
    runtime,
    ["600000.SH"],
    reason="MARKET_EVENT_QUEUE_OVERFLOW",
  )
  await executor._apply_pending_runtime_market_invalidations(runtime)

  await executor._process_trade_intent(runtime, _trade_intent(runtime.run_id))
  manual_intent = _trade_intent(runtime.run_id, manual=True)
  runtime.pending_approvals[manual_intent.intent_id] = manual_intent
  result = await executor.approve_trade_intent(
    runtime.run_id,
    manual_intent.intent_id,
  )

  assert result["success"] is False
  assert result["code"] == "MARKET_DATA_CONTINUITY_LOST"
  assert manual_intent.intent_id not in runtime.pending_approvals
  runtime.broker.place_order.assert_not_awaited()
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_continuity_blind_gate_survives_runtime_state_restore() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("blind-gate-source")
  manager = RuntimeStateManager(run_id=runtime.run_id, persist_enabled=False)
  manager.checkpoint_strategy_state_changes = AsyncMock(return_value=True)
  runtime.state_manager = manager
  runtime.strategy = _ContinuityBlindStrategy()
  executor._mark_runtime_market_continuity_lost(
    runtime,
    ["600000.SH"],
    reason="MARKET_EVENT_QUEUE_OVERFLOW",
  )

  await executor._apply_pending_runtime_market_invalidations(runtime)

  restored = RuntimeStateManager(run_id="blind-gate-restored", persist_enabled=False)
  restored._state = copy.deepcopy(manager._state)
  restarted = _runtime("blind-gate-restored")
  restarted.state_manager = restored
  restarted.strategy = _ContinuityBlindStrategy()
  restarted.broker = SimpleNamespace(place_order=AsyncMock())

  assert executor._runtime_state_reconciliation_failure(restarted) == (
    "MARKET_CONTINUITY_RECONCILE_REQUIRED",
    "行情连续性失效且策略无法安全重建观察窗，需显式权威处置",
  )
  await executor._process_trade_intent(
    restarted,
    _trade_intent(restarted.run_id),
  )
  restarted.broker.place_order.assert_not_awaited()
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_t_pending_approval_expires_and_new_signal_can_follow_rewarm(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime("pending-invalidation")
  runtime.context.parameters.update(
    {
      "signal_lookback_seconds": 60,
      "momentum_enabled": False,
      "momentum_window_seconds": 15,
      "momentum_baseline_seconds": 60,
      "pullback_threshold_pct": 0.8,
      "rebound_threshold_pct": 0.2,
      "stabilization_seconds": 15,
      "target_trade_amount": 10_000.0,
      "max_trade_amount": 12_000.0,
    }
  )
  strategy = AshareIntradayTAssistantStrategy(runtime.context)
  await strategy.initialize()
  runtime.strategy = strategy
  runtime.strategy_class = AshareIntradayTAssistantStrategy
  runtime.broker = SimpleNamespace(place_order=AsyncMock())
  intent = _trade_intent(
    runtime.run_id,
    manual=True,
    metadata={
      "t_trade_role": "entry",
      "instrument_code": "600000.SH",
      "signal": {"triggered": True, "detected_at_ms": 1},
    },
  )
  state = strategy._empty_instrument_state()
  state.update(
    {
      "pending_entry_intent_id": intent.intent_id,
      "entry_order_status": "AWAITING_APPROVAL",
      "entry_eligible": True,
      "position_shares": 1_000,
      "position_available_shares": 1_000,
      "current_signal": {
        "triggered": True,
        "detected_at_ms": 1,
        "signal_price": 10.0,
      },
    }
  )
  strategy.state.set("instrument_states", {"600000.SH": state})
  runtime.pending_approvals[intent.intent_id] = intent
  executor.runs[runtime.run_id] = runtime
  executor._mark_runtime_market_continuity_lost(
    runtime,
    ["600000.SH"],
    reason="MARKET_EVENT_QUEUE_OVERFLOW",
  )
  await executor._apply_pending_runtime_market_invalidations(runtime)
  assert intent.intent_id not in runtime.pending_approvals
  assert runtime.state_manager.updates[-1][1] == "EXPIRED"
  assert (
    strategy.state["instrument_states"]["600000.SH"][
      "pending_entry_intent_id"
    ]
    == ""
  )

  async def process_fresh_tick(_runtime: StrategyRuntime, _tick: object) -> None:
    runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
      instrument_code="600000.SH",
      timestamp=time_utils.now(),
      price=10.0,
      ask_price=[10.0],
    )

  monkeypatch.setattr(executor, "_process_tick", process_fresh_tick)
  executor._enqueue_runtime_market_event(runtime, "tick", _event())
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  await asyncio.wait_for(runtime.market_event_queue.join(), timeout=1.0)
  runtime.status = ExecutionStatus.STOPPED
  runtime._event_queue_wakeup.set()
  await asyncio.wait_for(runtime.event_task, timeout=1.0)
  assert runtime._active_market_continuity_losses == {}

  result = await executor.approve_trade_intent(runtime.run_id, intent.intent_id)

  assert result["success"] is False
  assert result["code"] == "RUNTIME_NOT_RUNNING"
  runtime.broker.place_order.assert_not_awaited()

  start = datetime(2026, 8, 20, 10, 0)
  for seconds in range(75):
    price = 100.0 if seconds < 60 else (99.0 if seconds < 74 else 99.3)
    output = await strategy.step(
      _strategy_input(start + timedelta(seconds=seconds), price)
    )
    assert output.trade_intents == []
  output = await strategy.step(
    _strategy_input(start + timedelta(seconds=75), 99.3)
  )
  assert len(output.trade_intents) == 1
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_expired_market_backlog_is_invalidated_without_processing_tick(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime("expired")
  runtime.strategy = _ContinuityAwareStrategy()
  process_tick = AsyncMock()
  monkeypatch.setattr(executor, "_process_tick", process_tick)
  monkeypatch.setattr(strategy_executor_module, "monotonic", lambda: 100.0)
  runtime.market_event_queue.put_nowait(
    RuntimeMarketEvent("tick", _event(), enqueued_at=89.0)
  )

  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  await asyncio.wait_for(runtime.market_event_queue.join(), timeout=1.0)
  runtime.status = ExecutionStatus.STOPPED
  runtime._event_queue_wakeup.set()
  await asyncio.wait_for(runtime.event_task, timeout=1.0)

  process_tick.assert_not_awaited()
  assert runtime.market_events_expired == 1
  assert runtime.market_events_dropped == 1
  assert runtime.strategy.invalidations == [
    ("600000.SH", "MARKET_EVENT_PROCESSING_EXPIRED")
  ]
  assert runtime.market_event_queue._unfinished_tasks == 0
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_freshly_enqueued_cached_tick_is_rejected_by_source_age(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime("stale-source")
  runtime.strategy = _ContinuityAwareStrategy()
  process_tick = AsyncMock()
  monkeypatch.setattr(executor, "_process_tick", process_tick)
  now = datetime(2026, 8, 20, 10, 0)
  monkeypatch.setattr(strategy_executor_module.time_utils, "now", lambda: now)
  executor._enqueue_runtime_market_event(
    runtime,
    "tick",
    SimpleNamespace(
      stock_code="600000.SH",
      time=now - timedelta(seconds=11),
    ),
  )

  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  await asyncio.wait_for(runtime.market_event_queue.join(), timeout=1.0)
  runtime.status = ExecutionStatus.STOPPED
  runtime._event_queue_wakeup.set()
  await asyncio.wait_for(runtime.event_task, timeout=1.0)

  process_tick.assert_not_awaited()
  assert runtime.market_tick_source_rejections == 1
  assert runtime.market_events_dropped == 1
  assert runtime.strategy.invalidations == [
    ("600000.SH", "MARKET_TICK_SOURCE_STALE")
  ]
  assert runtime.market_event_queue._unfinished_tasks == 0
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_kline_queue_age_is_bounded_without_tick_source_age_policy(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime("kline-source-policy")
  process_kline = AsyncMock()
  monkeypatch.setattr(executor, "_process_kline", process_kline)
  now = time_utils.now()
  old_bar = SimpleNamespace(
    stock_code="600000.SH",
    time=now - timedelta(minutes=1),
  )
  executor._enqueue_runtime_market_event(runtime, "kline", old_bar)

  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  await asyncio.wait_for(runtime.market_event_queue.join(), timeout=1.0)
  runtime.status = ExecutionStatus.STOPPED
  runtime._event_queue_wakeup.set()
  await asyncio.wait_for(runtime.event_task, timeout=1.0)

  process_kline.assert_awaited_once_with(runtime, old_bar)
  assert runtime.market_tick_source_rejections == 0
  assert runtime.market_events_processed == 1
  assert runtime.market_event_queue._unfinished_tasks == 0
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["paused", "durable_barrier"])
async def test_market_queue_accounting_balances_when_runtime_gate_drops_backlog(
  gate: str,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime(f"gate-{gate}")
  runtime.strategy = _ContinuityAwareStrategy()
  if gate == "paused":
    runtime.status = ExecutionStatus.PAUSED
  else:
    runtime.durable_event_barrier_key = "trade:blocking-report"
  executor._enqueue_runtime_market_event(runtime, "tick", _event())
  executor._enqueue_runtime_market_event(runtime, "tick", _event())

  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  await asyncio.wait_for(runtime.market_event_queue.join(), timeout=1.0)
  runtime.status = ExecutionStatus.STOPPED
  runtime._event_queue_wakeup.set()
  await asyncio.wait_for(runtime.event_task, timeout=1.0)

  assert runtime.market_events_dropped == 2
  assert runtime.market_event_queue._unfinished_tasks == 0
  assert runtime.strategy.invalidations[0][0] == "600000.SH"
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_quiesce_drains_market_queue_with_balanced_task_accounting() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("quiesce-market")
  runtime.status = ExecutionStatus.STOPPING
  runtime.market_event_queue.put_nowait(
    RuntimeMarketEvent("tick", _event(), enqueued_at=1.0)
  )

  await executor._quiesce_runtime_tasks(runtime)

  assert runtime.market_event_queue.qsize() == 0
  assert runtime.market_event_queue._unfinished_tasks == 0
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_control_queue_is_selected_before_market_queue() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("control-priority")
  control = object()
  tick = _event()
  runtime.market_event_queue.put_nowait(
    RuntimeMarketEvent("tick", tick, enqueued_at=1.0)
  )
  runtime.event_queue.put_nowait(("order", control))

  first = await executor._next_runtime_event(runtime, timeout=0.0)
  assert first is not None
  first_queue, first_type, first_data, _first_enqueued_at = first
  assert first_queue is runtime.event_queue
  assert (first_type, first_data) == ("order", control)
  first_queue.task_done()

  second = await executor._next_runtime_event(runtime, timeout=0.0)
  assert second is not None
  second_queue, second_type, second_data, _second_enqueued_at = second
  assert second_queue is runtime.market_event_queue
  assert (second_type, second_data) == ("tick", tick)
  second_queue.task_done()
  assert runtime.event_queue._unfinished_tasks == 0
  assert runtime.market_event_queue._unfinished_tasks == 0
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_durable_control_drains_before_market_backlog_and_both_queues_join(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime("durable-priority")
  applied: list[str] = []

  async def checkpoint(_event_key: str) -> bool:
    applied.append("durable")
    return True

  runtime.state_manager = SimpleNamespace(
    has_applied_runtime_event=lambda _event_key: True,
    checkpoint_durable_runtime_event=checkpoint,
  )

  async def process_tick(_runtime: StrategyRuntime, tick: object) -> None:
    applied.append(f"tick:{tick.stock_code}")

  monkeypatch.setattr(executor, "_process_tick", process_tick)
  executor._enqueue_runtime_market_event(runtime, "tick", _event())
  executor._enqueue_runtime_market_event(runtime, "tick", _event())
  completion = asyncio.get_running_loop().create_future()
  runtime.event_queue.put_nowait(
    (
      "durable_order",
      (
        SimpleNamespace(metadata={"runtime_event_key": "order:priority"}),
        completion,
      ),
    )
  )

  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  await asyncio.wait_for(runtime.event_queue.join(), timeout=1.0)
  await asyncio.wait_for(runtime.market_event_queue.join(), timeout=1.0)
  runtime.status = ExecutionStatus.STOPPED
  runtime._event_queue_wakeup.set()
  await asyncio.wait_for(runtime.event_task, timeout=1.0)

  assert completion.result() is True
  assert applied == ["durable", "tick:600000.SH", "tick:600000.SH"]
  assert runtime.event_queue._unfinished_tasks == 0
  assert runtime.market_event_queue._unfinished_tasks == 0
  executor.thread_pool.shutdown(wait=False)


def _tick(timestamp: datetime, price: float) -> Tick:
  return Tick(
    stock_code="600000.SH",
    period="tick",
    time=timestamp,
    last_price=price,
    open=100.0,
    high=100.0,
    low=99.0,
    last_close=100.0,
    amount=1_000_000.0,
    volume=10_000.0,
    pvolume=10_000.0,
    tickvol=100,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=1,
    ask_price=[price],
    bid_price=[price - 0.01],
    ask_vol=[1_000],
    bid_vol=[1_000],
  )


def _strategy_input(timestamp: datetime, price: float) -> StrategyInput:
  tick = _tick(timestamp, price)
  return StrategyInput(
    run_id="rewarm",
    strategy_id="1",
    timestamp=timestamp,
    cadence=StrategyCadence.TICK,
    instrument_code=tick.stock_code,
    event=tick,
  )


@pytest.mark.asyncio
async def test_realtime_restart_discards_restored_window_before_first_tick() -> None:
  context = StrategyContext(
    run_id="restart-rewarm",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "signal_lookback_seconds": 60,
      "momentum_enabled": False,
      "momentum_window_seconds": 15,
      "momentum_baseline_seconds": 60,
    },
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  state = strategy._empty_instrument_state()
  state.update(
    {
      "entry_eligible": True,
      "position_shares": 1_000,
      "position_available_shares": 1_000,
    }
  )
  strategy.apply_state_snapshot(
    {
      "instrument_states": {"600000.SH": state},
      "signal_sample_windows": {
        "version": 1,
        "instruments": {
          "600000.SH": [[1, 100.0, 99.99, 100.01, 1_000.0, 100.0]],
        },
      },
    }
  )

  await strategy.initialize()

  assert strategy._samples_by_instrument.get("600000.SH") is None
  assert strategy.state["signal_window_rewarm"]["instruments"] == {
    "600000.SH": {
      "reason": "RUNTIME_RESTART_REWARM_REQUIRED",
      "started_at_ms": 0,
    }
  }
  output = await strategy.step(
    _strategy_input(datetime(2026, 8, 20, 10, 0), 100.0)
  )
  assert output.trade_intents == []
  assert output.trace_payload["reason"] == "SIGNAL_WINDOW_REWARMING"


@pytest.mark.asyncio
async def test_realtime_cold_start_rewarms_every_bound_instrument() -> None:
  context = StrategyContext(
    run_id="cold-start-rewarm",
    mode=StrategyRunMode.LIVE,
    instruments=["600000.SH"],
    parameters={
      "signal_lookback_seconds": 60,
      "momentum_enabled": False,
      "momentum_window_seconds": 15,
      "momentum_baseline_seconds": 60,
    },
  )
  strategy = AshareIntradayTAssistantStrategy(context)

  await strategy.initialize()

  assert strategy.state["signal_window_rewarm"]["instruments"] == {
    "600000.SH": {
      "reason": "RUNTIME_RESTART_REWARM_REQUIRED",
      "started_at_ms": 0,
    }
  }
  output = await strategy.step(
    _strategy_input(datetime(2026, 8, 20, 10, 0), 100.0)
  )
  assert output.trade_intents == []
  assert output.trace_payload["reason"] == "SIGNAL_WINDOW_REWARMING"


@pytest.mark.asyncio
async def test_restart_pending_signal_without_window_is_invalidated() -> None:
  context = StrategyContext(
    run_id="pending-without-window",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={},
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  intent = _trade_intent(
    context.run_id,
    manual=True,
    metadata={
      "t_trade_role": "entry",
      "signal": {"triggered": True, "detected_at_ms": 123},
    },
  )
  state = strategy._empty_instrument_state()
  state.update(
    {
      "pending_entry_intent_id": intent.intent_id,
      "entry_order_status": "AWAITING_APPROVAL",
      "current_signal": {"triggered": True, "detected_at_ms": 123},
    }
  )
  strategy.apply_state_snapshot(
    {
      "instrument_states": {"600000.SH": state},
      "signal_sample_windows": {"version": 1, "instruments": {}},
    }
  )

  await strategy.initialize()

  assert strategy.invalidated_manual_intent_ids() == [intent.intent_id]
  assert strategy.validate_manual_approval(intent, object()) == (
    "APPROVAL_SIGNAL_INVALIDATED",
    "信号行情窗口已失效并正在重新预热，请等待新信号",
  )


@pytest.mark.asyncio
async def test_startup_expires_restored_t_pending_before_runtime_is_running() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("startup-expire-pending")
  strategy = AshareIntradayTAssistantStrategy(runtime.context)
  intent = _trade_intent(
    runtime.run_id,
    manual=True,
    metadata={
      "t_trade_role": "entry",
      "signal": {"triggered": True, "detected_at_ms": 123},
    },
  )
  state = strategy._empty_instrument_state()
  state.update(
    {
      "pending_entry_intent_id": intent.intent_id,
      "entry_order_status": "AWAITING_APPROVAL",
      "current_signal": {"triggered": True, "detected_at_ms": 123},
    }
  )
  strategy.apply_state_snapshot({"instrument_states": {"600000.SH": state}})
  runtime.strategy = strategy
  runtime.state_manager = SimpleNamespace(
    restore_manual_trade_intent=AsyncMock(return_value=intent),
    update_trade_intent_status=AsyncMock(),
    update_strategy_custom_state=Mock(),
    force_save=AsyncMock(return_value=True),
  )

  await executor._restore_pending_manual_approvals(runtime)

  runtime.state_manager.update_trade_intent_status.assert_awaited_once_with(
    intent.intent_id,
    "EXPIRED",
    notes="APPROVAL_SIGNAL_INVALIDATED",
  )
  runtime.state_manager.force_save.assert_awaited_once()
  assert runtime.pending_approvals == {}
  assert (
    strategy.state["instrument_states"]["600000.SH"][
      "pending_entry_intent_id"
    ]
    == ""
  )
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_normal_observation_gap_invalidates_window_and_starts_rewarm() -> None:
  context = StrategyContext(
    run_id="sample-gap",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "signal_lookback_seconds": 60,
      "momentum_enabled": False,
      "momentum_window_seconds": 15,
      "momentum_baseline_seconds": 60,
    },
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  await strategy.initialize()
  state = strategy._empty_instrument_state()
  state.update(
    {
      "entry_eligible": True,
      "position_shares": 1_000,
      "position_available_shares": 1_000,
    }
  )
  strategy.state.set("instrument_states", {"600000.SH": state})
  start = datetime(2026, 8, 20, 10, 0)
  await strategy.step(_strategy_input(start, 100.0))

  output = await strategy.step(
    _strategy_input(start + timedelta(seconds=20), 99.0)
  )

  assert output.trade_intents == []
  assert output.trace_payload["reason"] == "SIGNAL_WINDOW_REWARMING"
  assert strategy.state["signal_window_rewarm"]["instruments"] == {
    "600000.SH": {
      "reason": "SIGNAL_SAMPLE_GAP",
      "started_at_ms": int((start + timedelta(seconds=20)).timestamp() * 1000),
    }
  }


@pytest.mark.asyncio
async def test_t_strategy_emits_no_intent_until_full_lookback_is_rewarmed() -> None:
  context = StrategyContext(
    run_id="rewarm",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "signal_lookback_seconds": 60,
      "momentum_enabled": False,
      "momentum_window_seconds": 15,
      "momentum_baseline_seconds": 60,
      "pullback_threshold_pct": 0.8,
      "rebound_threshold_pct": 0.2,
      "stabilization_seconds": 15,
      "target_trade_amount": 10_000.0,
      "max_trade_amount": 12_000.0,
    },
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  await strategy.initialize()
  state = strategy._empty_instrument_state()
  state.update(
    {
      "entry_eligible": True,
      "position_shares": 1_000,
      "position_available_shares": 1_000,
    }
  )
  strategy.state.update({"instrument_states": {"600000.SH": state}})

  assert strategy.invalidate_realtime_market_window(
    "600000.SH",
    reason="MARKET_EVENT_QUEUE_OVERFLOW",
  )
  start = datetime(2026, 8, 20, 10, 0)
  for seconds in range(75):
    price = 100.0 if seconds < 60 else (99.0 if seconds < 74 else 99.3)
    output = await strategy.step(
      _strategy_input(start + timedelta(seconds=seconds), price)
    )
    assert output.trade_intents == []
    assert output.trace_payload["reason"] == "SIGNAL_WINDOW_REWARMING"

  output = await strategy.step(_strategy_input(start + timedelta(seconds=75), 99.3))

  assert len(output.trade_intents) == 1
  assert output.trade_intents[0].reason == "T_TRADE_PULLBACK_REBOUND_ENTRY"
  assert strategy.state["signal_window_rewarm"]["instruments"] == {}


def test_t_manual_confirmation_cannot_disable_quote_age_gate() -> None:
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="t-quote-age",
    mode=StrategyRunMode.LIVE,
    instruments=["600000.SH"],
    parameters={"execution_quote_max_age_seconds": 0.0},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name=context.run_id,
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="T_TRADE_PULLBACK_REBOUND_ENTRY",
    target_volume=100,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    metadata={"t_trade_role": "entry"},
  )

  assert executor._approval_failure(runtime, intent)[0] == "APPROVAL_QUOTE_MISSING"
  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH",
    timestamp=time_utils.now() - timedelta(seconds=4),
    price=10.0,
    ask_price=[10.0],
  )
  assert executor._approval_failure(runtime, intent)[0] == "APPROVAL_QUOTE_STALE"
  executor.thread_pool.shutdown(wait=False)
