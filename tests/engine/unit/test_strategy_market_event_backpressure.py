from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta
from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.base import (
  MarketDataContext,
  MarketDataSession,
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
from quantx_domain.trading.t_trade_opportunity_engine import (
  CandidateStatus,
  DataHealth,
  OpportunityCandidate,
  OpportunityPath,
  OpportunityPolicy,
  OpportunitySample,
  OpportunityState,
)
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

  async def update_trade_intent_status_strict(
    self,
    intent_id: str,
    status: str,
    **updates,
  ) -> None:
    await self.update_trade_intent_status(intent_id, status, **updates)

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


_EVENT_SEQUENCES = count(1)

_REFERENCE_PROFILE = {
  "profile_version": "profile-20260819",
  "profile_schema_version": 1,
  "as_of_trade_date": "2026-08-19",
  "pullback_threshold_pct": 0.8,
  "momentum_rise_threshold_pct": 0.8,
  "momentum_amount_velocity_ratio": 2.0,
  "pullback_max_spread_ticks": 3,
  "momentum_max_spread_ticks": 10,
  "profile_fingerprint": "profile-fingerprint-20260819",
}


def _event(stock_code: str = "600000.SH") -> SimpleNamespace:
  sequence = next(_EVENT_SEQUENCES)
  now = time_utils.now()
  return SimpleNamespace(
    stock_code=stock_code,
    time=now,
    source_time_ms=int(now.timestamp() * 1000),
    tick_ordinal=sequence,
    continuity_generation=1,
    market_stream_id="test-stream-1",
    market_stream_sequence=sequence,
    market_stream_reset=False,
  )


def _transport_event(
  *,
  generation: int,
  sequence: int,
  stream_id: str = "stream-1",
  reset: bool = False,
) -> SimpleNamespace:
  now = time_utils.now()
  return SimpleNamespace(
    stock_code="600000.SH",
    time=now,
    source_time_ms=int(now.timestamp() * 1000),
    tick_ordinal=sequence,
    continuity_generation=generation,
    market_stream_id=stream_id,
    market_stream_sequence=sequence,
    market_stream_reset=reset,
  )


def test_sparse_transport_sequence_does_not_invalidate_continuity() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("sparse-transport")
  runtime.strategy = _ContinuityAwareStrategy()

  assert executor._observe_runtime_market_transport(
    runtime,
    _transport_event(generation=4, sequence=10),
  )
  assert executor._observe_runtime_market_transport(
    runtime,
    _transport_event(generation=4, sequence=10_000),
  )

  assert runtime._pending_market_invalidations == {}
  assert runtime._market_transport_generation == 4
  assert runtime._market_transport_sequences["600000.SH"] == 10_000
  executor.thread_pool.shutdown(wait=False)


def test_live_tick_without_authority_lineage_is_rejected_fail_closed() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("missing-transport-lineage")
  runtime.strategy = _ContinuityAwareStrategy()

  accepted = executor._observe_runtime_market_transport(
    runtime,
    SimpleNamespace(stock_code="600000.SH", time=time_utils.now()),
  )

  assert accepted is False
  assert runtime.market_tick_source_rejections == 1
  assert runtime.market_events_dropped == 1
  assert runtime._pending_market_invalidations == {
    "600000.SH": "MARKET_TRANSPORT_LINEAGE_UNAVAILABLE"
  }
  assert runtime._market_fail_closed_codes == {
    "600000.SH": "MARKET_TRANSPORT_LINEAGE_UNAVAILABLE"
  }
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("second", "reason"),
  [
    (
      _transport_event(generation=5, sequence=1, stream_id="stream-2"),
      "MARKET_TRANSPORT_IDENTITY_CHANGED",
    ),
    (
      _transport_event(generation=4, sequence=20, reset=True),
      "MARKET_STREAM_RESYNC",
    ),
  ],
)
async def test_transport_generation_or_resync_invalidates_before_dispatch(
  second: SimpleNamespace,
  reason: str,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime(f"transport-{reason.lower()}")
  runtime.strategy = _ContinuityAwareStrategy()
  assert executor._observe_runtime_market_transport(
    runtime,
    _transport_event(generation=4, sequence=10),
  )

  assert executor._observe_runtime_market_transport(runtime, second)
  assert runtime._pending_market_invalidations == {"600000.SH": reason}
  await executor._apply_pending_runtime_market_invalidations(runtime)

  assert runtime.strategy.invalidations == [("600000.SH", reason)]
  assert "600000.SH" not in runtime._market_fail_closed_codes
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_transport_change_expires_exact_v3_pending_candidate_and_intent() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("transport-pending-candidate")
  strategy = AshareIntradayTAssistantStrategy(runtime.context)
  await strategy.initialize()
  runtime.strategy = strategy
  candidate = OpportunityCandidate(
    candidate_id="candidate-1",
    fingerprint="fingerprint-1",
    episode_id="episode-1",
    path=OpportunityPath.PULLBACK_REBOUND,
    latched_at_ms=1_000,
    expires_at_ms=31_000,
    source_time_ms=1_000,
    tick_ordinal=1,
    price=10.0,
    score=80.0,
    policy_version="policy-1",
    feature_schema_version=1,
    reference_profile_version="profile-1",
    reference_profile_schema_version=1,
  )
  opportunity = OpportunityState(
    instrument_code="600000.SH",
    trade_date="2026-08-20",
    continuity_generation="4",
    candidate=candidate,
    candidate_status=CandidateStatus.AWAITING_APPROVAL,
    candidate_awaiting_approval=True,
  ).to_dict()
  state = strategy._empty_instrument_state()
  intent = _trade_intent(
    runtime.run_id,
    manual=True,
    metadata={
      "t_trade_role": "entry",
      "instrument_code": "600000.SH",
      "candidate_id": candidate.candidate_id,
    },
  )
  state.update(
    {
      "opportunity": opportunity,
      "pending_entry_intent_id": intent.intent_id,
      "entry_order_status": "AWAITING_APPROVAL",
    }
  )
  strategy.state.set("instrument_states", {"600000.SH": state})
  runtime.pending_approvals[intent.intent_id] = intent
  assert executor._observe_runtime_market_transport(
    runtime,
    _transport_event(generation=4, sequence=10),
  )

  assert executor._observe_runtime_market_transport(
    runtime,
    _transport_event(generation=5, sequence=1, stream_id="stream-2"),
  )
  await executor._apply_pending_runtime_market_invalidations(runtime)

  current = strategy.state["instrument_states"]["600000.SH"]
  assert intent.intent_id not in runtime.pending_approvals
  assert runtime.state_manager.updates[-1][0:2] == (intent.intent_id, "EXPIRED")
  assert current["pending_entry_intent_id"] == ""
  assert current["opportunity"]["candidate"] is None
  assert current["opportunity"]["samples"] == []
  executor.thread_pool.shutdown(wait=False)


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


def _v3_pending_candidate(
  run_id: str,
) -> tuple[TradeIntent, dict]:
  policy = OpportunityPolicy()
  candidate = OpportunityCandidate(
    candidate_id="candidate-restored",
    fingerprint="fingerprint-restored",
    episode_id="episode-restored",
    path=OpportunityPath.PULLBACK_REBOUND,
    latched_at_ms=1_000,
    expires_at_ms=9_000_000_000_000,
    source_time_ms=1_000,
    tick_ordinal=1,
    price=10.0,
    score=80.0,
    policy_version=policy.policy_version,
    feature_schema_version=policy.feature_schema_version,
    reference_profile_version="profile-20260819",
    reference_profile_schema_version=1,
  )
  opportunity = OpportunityState(
    instrument_code="600000.SH",
    trade_date="2026-08-20",
    continuity_generation="1",
    candidate=candidate,
    candidate_status=CandidateStatus.AWAITING_APPROVAL,
    candidate_awaiting_approval=True,
  ).to_dict()
  opportunity.update(
    {
      "state_version": 7,
      "config_version": 3,
      "policy_version": policy.policy_version,
      "revalidate_score": policy.revalidate_score,
      "thresholds": {
        "preview": policy.preview_score,
        "candidate": policy.candidate_score,
        "revalidate": policy.revalidate_score,
        "rearm": policy.rearm_score,
      },
      "latest_evaluation": {
        "data_health": DataHealth.READY.value,
        "selected_path": OpportunityPath.PULLBACK_REBOUND.value,
        "external_blockers": [],
        "pullback": {
          "score": 80.0,
          "hard_gates": [{"code": "SPREAD_OK", "passed": True}],
          "blockers": [],
        },
      },
    }
  )
  intent = _trade_intent(
    run_id,
    manual=True,
    metadata={
      "t_trade_role": "entry",
      "instrument_code": "600000.SH",
      "opportunity_schema_version": 3,
      "candidate_id": candidate.candidate_id,
      "candidate_fingerprint": candidate.fingerprint,
      "candidate_state_version": 7,
      "candidate_status": CandidateStatus.AWAITING_APPROVAL.value,
      "config_version": 3,
      "policy_version": policy.policy_version,
    },
  )
  state = AshareIntradayTAssistantStrategy._empty_instrument_state()
  state.update(
    {
      "opportunity": opportunity,
      "pending_entry_intent_id": intent.intent_id,
      "entry_order_status": "AWAITING_APPROVAL",
    }
  )
  return intent, state


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
  strategy = AshareIntradayTAssistantStrategy(runtime.context)
  await strategy.initialize()
  state = strategy._empty_instrument_state()
  state["opportunity"] = _sampled_v3_opportunity()
  strategy.state.set("instrument_states", {"600000.SH": state})
  runtime.strategy = strategy
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
  opportunity = runtime.strategy.state["instrument_states"]["600000.SH"][
    "opportunity"
  ]
  assert opportunity["samples"] == []
  assert opportunity["candidate"] is None
  assert opportunity["continuity_generation"] == "invalidated:1"
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
  strategy.state.set("state_schema_version", 3)
  state = strategy._empty_instrument_state()
  state["opportunity"] = _sampled_v3_opportunity()
  strategy.state.set("instrument_states", {"600000.SH": state})
  await asyncio.wait_for(manager._state_queue.join(), timeout=1.0)
  assert await manager.save_snapshot() is True
  persisted = durable_snapshots[-1]["custom"]["instrument_states"][
    "600000.SH"
  ]["opportunity"]
  assert len(persisted["samples"]) == 1

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
  restored = restored_strategy.state["instrument_states"]["600000.SH"][
    "opportunity"
  ]
  assert restored["samples"] == []
  assert restored["candidate"] is None
  assert restored["continuity_generation"] == "invalidated:1"

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
async def test_t_pending_approval_expires_and_runtime_gate_reopens_on_fresh_tick(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  runtime = _runtime("pending-invalidation")
  strategy = AshareIntradayTAssistantStrategy(runtime.context)
  await strategy.initialize()
  runtime.strategy = strategy
  runtime.strategy_class = AshareIntradayTAssistantStrategy
  runtime.broker = SimpleNamespace(place_order=AsyncMock())
  intent, state = _v3_pending_candidate(runtime.run_id)
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
  invalidated = strategy.state["instrument_states"]["600000.SH"][
    "opportunity"
  ]
  assert invalidated["candidate"] is None
  assert invalidated["samples"] == []

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
      source_time_ms=int((now - timedelta(seconds=11)).timestamp() * 1000),
      tick_ordinal=1,
      continuity_generation=1,
      market_stream_id="stale-source-stream",
      market_stream_sequence=1,
      market_stream_reset=False,
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


def _tick(
  timestamp: datetime,
  price: float,
  *,
  amount: float = 1_000_000.0,
  volume: float = 10_000.0,
) -> Tick:
  return Tick(
    stock_code="600000.SH",
    period="tick",
    time=timestamp,
    last_price=price,
    open=100.0,
    high=100.0,
    low=99.0,
    last_close=100.0,
    amount=amount,
    volume=volume,
    pvolume=volume,
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


def _strategy_input(
  timestamp: datetime,
  price: float,
  *,
  amount: float = 1_000_000.0,
  volume: float = 10_000.0,
  continuity_generation: int = 1,
  run_id: str = "rewarm",
) -> StrategyInput:
  tick = _tick(timestamp, price, amount=amount, volume=volume)
  source_time_ms = int(timestamp.timestamp() * 1000)
  tick_ordinal = source_time_ms
  tick.source_time_ms = source_time_ms
  tick.tick_ordinal = tick_ordinal
  tick.continuity_generation = continuity_generation
  tick.market_stream_id = "test-stream"
  tick.market_stream_sequence = tick_ordinal
  return StrategyInput(
    run_id=run_id,
    strategy_id="1",
    timestamp=timestamp,
    cadence=StrategyCadence.TICK,
    instrument_code=tick.stock_code,
    event=tick,
    market_data_context=MarketDataContext(
      source="TEST",
      stream_id="test-stream",
      continuity_generation=continuity_generation,
      source_sequence=tick_ordinal,
      source_time_ms=source_time_ms,
      tick_ordinal=tick_ordinal,
      received_at_ms=source_time_ms,
      quote_stale=False,
      session=MarketDataSession.CONTINUOUS_AM,
      trade_date=timestamp.date(),
    ),
    market_context={
      "t_trade_instrument_profile": _REFERENCE_PROFILE,
      "t_trade_intent_emission": {"allowed": True, "blockers": []},
    },
  )


async def _apply_strategy_step(
  strategy: AshareIntradayTAssistantStrategy,
  input_snapshot: StrategyInput,
) -> StrategyOutput:
  output = await strategy.step(input_snapshot)
  if output.runtime_state_patch is not None:
    strategy.state.update(output.runtime_state_patch.set)
  return output


@pytest.mark.asyncio
async def test_first_live_tick_rewarms_restored_v3_window_fail_closed() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("v3-restart-authority")
  strategy = AshareIntradayTAssistantStrategy(runtime.context)
  await strategy.initialize()
  runtime.strategy = strategy
  start = datetime(2026, 8, 20, 10, 0)

  def strategy_input(sequence: int) -> StrategyInput:
    timestamp = start + timedelta(seconds=sequence)
    tick = _tick(timestamp, 100.0 + sequence / 100)
    tick.source_time_ms = int(timestamp.timestamp() * 1000)
    tick.tick_ordinal = sequence
    tick.continuity_generation = 7
    tick.market_stream_id = "authority-stream-7"
    tick.market_stream_sequence = sequence
    return StrategyInput(
      run_id=runtime.run_id,
      strategy_id="1",
      timestamp=timestamp,
      cadence=StrategyCadence.TICK,
      instrument_code=tick.stock_code,
      event=tick,
      market_data_context=MarketDataContext(
        source="REALTIME",
        stream_id="authority-stream-7",
        continuity_generation=7,
        source_sequence=sequence,
        source_time_ms=tick.source_time_ms,
        tick_ordinal=sequence,
        received_at_ms=tick.source_time_ms,
        quote_stale=False,
        session=MarketDataSession.CONTINUOUS_AM,
        trade_date=timestamp.date(),
      ),
    )

  for sequence in range(1, 4):
    output = await strategy.step(strategy_input(sequence))
    strategy.state.update(output.runtime_state_patch.set)
  restored = strategy.state["instrument_states"]["600000.SH"]["opportunity"]
  assert len(restored["samples"]) == 3
  runtime._restored_market_windows_unverified = (
    executor._restored_causal_market_window_codes(runtime)
  )

  first_live_tick = strategy_input(4).event
  assert executor._observe_runtime_market_transport(runtime, first_live_tick)
  assert runtime._pending_market_invalidations == {
    "600000.SH": "RUNTIME_RESTART_CONTINUITY_UNPROVEN"
  }
  await executor._apply_pending_runtime_market_invalidations(runtime)
  cleared = strategy.state["instrument_states"]["600000.SH"]["opportunity"]
  assert cleared["samples"] == []
  assert cleared["candidate"] is None

  output = await strategy.step(strategy_input(4))
  snapshot = output.trace_payload["signal_snapshot"]
  assert snapshot["data_health"] == "CONTINUITY_LOST"
  assert snapshot["data_health_reasons"] == ["CONTINUITY_GENERATION_CHANGED"]
  assert output.trade_intents == []
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_realtime_restart_preserves_v3_window_for_engine_authority_check() -> None:
  context = StrategyContext(
    run_id="restart-rewarm",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"signal_policy": OpportunityPolicy().to_dict()},
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  state = strategy._empty_instrument_state()
  state["opportunity"] = _sampled_v3_opportunity()
  strategy.apply_state_snapshot(
    {
      "state_schema_version": 3,
      "instrument_states": {"600000.SH": state},
    }
  )

  await strategy.initialize()

  restored = strategy.state["instrument_states"]["600000.SH"]["opportunity"]
  assert len(restored["samples"]) == 1
  runtime = _runtime(context.run_id)
  runtime.strategy = strategy
  executor = StrategyExecutor()
  assert executor._restored_causal_market_window_codes(runtime) == {
    "600000.SH"
  }
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_realtime_cold_start_rewarms_every_bound_instrument() -> None:
  context = StrategyContext(
    run_id="cold-start-rewarm",
    mode=StrategyRunMode.LIVE,
    instruments=["600000.SH"],
    parameters={"signal_policy": OpportunityPolicy().to_dict()},
  )
  strategy = AshareIntradayTAssistantStrategy(context)

  await strategy.initialize()

  output = await _apply_strategy_step(
    strategy,
    _strategy_input(
      datetime(2026, 8, 20, 10, 0),
      100.0,
      run_id=context.run_id,
    ),
  )
  assert output.trade_intents == []
  opportunity = strategy.state["instrument_states"]["600000.SH"][
    "opportunity"
  ]
  assert opportunity["data_health"] == DataHealth.WARMING.value
  assert len(opportunity["samples"]) == 1
  assert opportunity["candidate"] is None
  assert output.trace_payload["signal_snapshot"]["data_health"] == (
    DataHealth.WARMING.value
  )


@pytest.mark.asyncio
async def test_restart_v3_pending_candidate_requires_engine_authority_check() -> None:
  context = StrategyContext(
    run_id="pending-without-window",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"signal_policy": OpportunityPolicy().to_dict()},
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  intent, state = _v3_pending_candidate(context.run_id)
  strategy.apply_state_snapshot(
    {
      "state_schema_version": 3,
      "instrument_states": {"600000.SH": state},
    }
  )

  await strategy.initialize()

  assert strategy.pending_manual_intent_ids() == [intent.intent_id]
  assert strategy.invalidated_manual_intent_ids() == []
  runtime = _runtime(context.run_id)
  runtime.strategy = strategy
  executor = StrategyExecutor()
  assert executor._restored_causal_market_window_codes(runtime) == {
    "600000.SH"
  }
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_startup_expires_restored_t_pending_before_runtime_is_running() -> None:
  executor = StrategyExecutor()
  runtime = _runtime("startup-expire-pending")
  runtime.context.parameters["account_id"] = "account-1"
  runtime.context.parameters["signal_policy"] = OpportunityPolicy().to_dict()
  strategy = AshareIntradayTAssistantStrategy(runtime.context)
  intent, state = _v3_pending_candidate(runtime.run_id)
  intent.metadata["account_id"] = "account-1"
  strategy.apply_state_snapshot(
    {
      "state_schema_version": 3,
      "instrument_states": {"600000.SH": state},
    }
  )
  runtime.strategy = strategy
  status_update = AsyncMock()
  runtime.state_manager = SimpleNamespace(
    persist_enabled=True,
    pending_t_trade_material_events=lambda: [],
    pending_t_trade_paper_fill_facts=lambda: [],
    restore_v3_manual_candidate_intents=AsyncMock(
      return_value=[
        SimpleNamespace(intent=intent, durable_status="AWAITING_APPROVAL")
      ]
    ),
    restore_manual_trade_intent=AsyncMock(return_value=intent),
    update_trade_intent_status=status_update,
    update_trade_intent_status_strict=status_update,
    update_strategy_custom_state=Mock(),
    force_save=AsyncMock(return_value=True),
  )

  await executor._restore_pending_manual_approvals(runtime)

  status_update.assert_awaited_once_with(
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
async def test_normal_observation_gap_keeps_v3_causal_window() -> None:
  context = StrategyContext(
    run_id="sample-gap",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"signal_policy": OpportunityPolicy().to_dict()},
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  await strategy.initialize()
  start = datetime(2026, 8, 20, 10, 0)
  await _apply_strategy_step(
    strategy,
    _strategy_input(start, 100.0, run_id=context.run_id),
  )

  output = await _apply_strategy_step(
    strategy,
    _strategy_input(
      start + timedelta(seconds=20),
      99.0,
      run_id=context.run_id,
    ),
  )

  assert output.trade_intents == []
  opportunity = strategy.state["instrument_states"]["600000.SH"][
    "opportunity"
  ]
  assert len(opportunity["samples"]) == 2
  assert opportunity["continuity_generation"] == "1"
  assert opportunity["data_health"] != DataHealth.CONTINUITY_LOST.value
  assert opportunity["candidate"] is None


@pytest.mark.asyncio
async def test_v3_opportunity_rewarms_to_ready_after_continuity_loss() -> None:
  policy = OpportunityPolicy()
  context = StrategyContext(
    run_id="rewarm",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "target_trade_amount": 10_000.0,
      "max_trade_amount": 12_000.0,
      "signal_policy": policy.to_dict(),
    },
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  await strategy.initialize()
  seed_at = datetime(2026, 8, 20, 9, 59, 59)
  await _apply_strategy_step(
    strategy,
    _strategy_input(
      seed_at,
      100.0,
      amount=0.0,
      volume=0.0,
      run_id=context.run_id,
    ),
  )

  assert strategy.invalidate_realtime_market_window(
    "600000.SH",
    reason="MARKET_EVENT_QUEUE_OVERFLOW",
  )
  start = datetime(2026, 8, 20, 10, 0)
  first = await _apply_strategy_step(
    strategy,
    _strategy_input(
      start,
      100.0,
      amount=0.0,
      volume=0.0,
      run_id=context.run_id,
    ),
  )
  assert first.trade_intents == []
  assert first.trace_payload["signal_snapshot"]["data_health"] == (
    DataHealth.CONTINUITY_LOST.value
  )
  for seconds, price, amount, volume in [
    (60, 99.0, 0.0, 0.0),
    (80, 99.3, 995_000.0, 10_000.0),
  ]:
    output = await _apply_strategy_step(
      strategy,
      _strategy_input(
        start + timedelta(seconds=seconds),
        price,
        amount=amount,
        volume=volume,
        run_id=context.run_id,
      ),
    )
    assert output.trade_intents == []

  candidate_outputs = []
  for seconds, price, amount, volume in [
    (83, 99.31, 1_000_000.0, 10_100.0),
    (86, 99.32, 1_005_000.0, 10_200.0),
  ]:
    candidate_outputs.append(
      await _apply_strategy_step(
        strategy,
        _strategy_input(
          start + timedelta(seconds=seconds),
          price,
          amount=amount,
          volume=volume,
          run_id=context.run_id,
        ),
      )
    )

  intents = [
    intent
    for output in candidate_outputs
    for intent in output.trade_intents
  ]
  assert intents == []
  opportunity = strategy.state["instrument_states"]["600000.SH"][
    "opportunity"
  ]
  assert opportunity["data_health"] == DataHealth.READY.value
  assert opportunity["continuity_generation"] == "1"
  assert len(opportunity["samples"]) == 5


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
