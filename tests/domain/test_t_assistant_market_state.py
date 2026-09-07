from dataclasses import replace
from datetime import UTC, datetime

import pytest
from quantx_contracts import ExecutionOwnerRef, ExecutionOwnerType
from quantx_domain.trading.t_assistant_execution import (
  TAssistantEntryReadiness,
  TAssistantExecutionStatus,
)
from quantx_domain.trading.t_assistant_market_state import (
  T_ASSISTANT_DELTA_RING_CAPACITY,
  T_ASSISTANT_REDUCER_MAX_LAG_TICKS,
  T_MARKET_RING_OVERFLOW,
  T_MARKET_SEQUENCE_GAP,
  AcceptedTMarketTick,
  SymbolDecisionSnapshot,
  SymbolDeltaCoverage,
  SymbolMarketDeltaRing,
  SymbolMarketStateReducer,
  TAssistantSymbolState,
  TDecisionSnapshot,
  TickAcceptance,
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  CandidateStatus,
  OpportunityCandidate,
  OpportunityGateContext,
  OpportunityPath,
  OpportunityPolicy,
  OpportunitySample,
)


def _tick(
  sequence: int,
  *,
  code: str = "600000.SH",
  source_time_ms: int | None = None,
  ordinal: int | None = None,
  generation: str = "1",
  discontinuity_reason: str | None = None,
) -> AcceptedTMarketTick:
  observed = source_time_ms if source_time_ms is not None else 1_000 + sequence
  return AcceptedTMarketTick(
    stream_id="stream-1",
    accepted_sequence=sequence,
    received_at_ms=observed,
    discontinuity_reason=discontinuity_reason,
    sample=OpportunitySample(
      instrument_code=code,
      trade_date="2026-09-03",
      source_time_ms=observed,
      tick_ordinal=ordinal if ordinal is not None else sequence,
      price=10.0 + sequence / 10_000,
      continuity_generation=generation,
      received_at_ms=observed,
      bid_price=9.99,
      ask_price=10.01,
      bid_volume=1_000,
      ask_volume=1_000,
      cumulative_amount=float(sequence * 10_000),
      cumulative_volume=float(sequence * 1_000),
    ),
  )


def _initial(code: str = "600000.SH") -> TAssistantSymbolState:
  return TAssistantSymbolState.initial(
    execution_id="execution-1",
    instrument_code=code,
    policy_version=OpportunityPolicy().policy_version,
    feature_schema_version=OpportunityPolicy().feature_schema_version,
    trade_date="2026-09-03",
  )


def _decision_for_item(
  item: SymbolDecisionSnapshot,
  *,
  fence_sequence: int,
) -> TDecisionSnapshot:
  now = datetime(2026, 9, 3, 1, 30, tzinfo=UTC)
  return TDecisionSnapshot(
    execution_ref=ExecutionOwnerRef(
      ExecutionOwnerType.T_ASSISTANT_EXECUTION,
      "execution-1",
    ),
    decision_time=now,
    trade_date="2026-09-03",
    stream_id="stream-1",
    continuity_generation="1",
    fence_sequence=fence_sequence,
    capture_as_of=now,
    universe_revision=0,
    config_version=1,
    config_snapshot_hash="a" * 64,
    policy_version=OpportunityPolicy().policy_version,
    feature_schema_version=OpportunityPolicy().feature_schema_version,
    execution_status=TAssistantExecutionStatus.RUNNING,
    entry_readiness=TAssistantEntryReadiness.READY,
    entry_readiness_as_of=now,
    symbols=(item,),
  )


def test_duplicate_and_out_of_order_ticks_never_enter_ring_twice():
  ring = SymbolMarketDeltaRing("600000.SH")
  first = _tick(1)

  assert (
    ring.accept(first, capture_time_ms=first.sample.source_time_ms).acceptance
    is TickAcceptance.ACCEPTED
  )
  assert (
    ring.accept(first, capture_time_ms=first.sample.source_time_ms).acceptance
    is TickAcceptance.DUPLICATE
  )
  older = _tick(2, source_time_ms=999, ordinal=0)
  assert (
    ring.accept(older, capture_time_ms=1_002).acceptance is TickAcceptance.OUT_OF_ORDER
  )
  delta = ring.slice_after(None, decision_time_ms=first.received_at_ms)
  assert [item.accepted_sequence for item in delta.ticks] == [1]


def test_gap_fails_closed_once_and_next_cursor_starts_rewarm_generation():
  ring = SymbolMarketDeltaRing("600000.SH")
  ring.accept(_tick(1), capture_time_ms=2_000)
  cursor = ring.slice_after(None, decision_time_ms=2_000).next_cursor
  ring.accept(
    _tick(3, discontinuity_reason=T_MARKET_SEQUENCE_GAP),
    capture_time_ms=2_000,
  )

  gap = ring.slice_after(cursor, decision_time_ms=2_000)
  assert gap.coverage is SymbolDeltaCoverage.SEQUENCE_GAP
  assert gap.reason_codes == (T_MARKET_SEQUENCE_GAP, "T_REWARM_REQUIRED")
  assert gap.ticks == ()
  replay = ring.slice_after(
    gap.next_cursor,
    decision_time_ms=2_000,
    rewarm_replay=True,
  )
  assert replay.coverage is SymbolDeltaCoverage.READY
  assert [tick.accepted_sequence for tick in replay.ticks] == [3]


def test_per_symbol_sequence_is_contiguous_across_sparse_global_batches():
  ring = SymbolMarketDeltaRing("600000.SH")
  first = replace(_tick(1), market_fence_sequence=10)
  second = replace(_tick(2), market_fence_sequence=12)
  ring.accept(first, capture_time_ms=2_000)
  cursor = ring.slice_after(
    None,
    decision_time_ms=2_000,
    through_accepted_sequence=10,
  ).next_cursor
  ring.accept(second, capture_time_ms=2_000)

  delta = ring.slice_after(
    cursor,
    decision_time_ms=2_000,
    through_accepted_sequence=12,
  )
  assert delta.coverage is SymbolDeltaCoverage.READY
  assert [tick.accepted_sequence for tick in delta.ticks] == [2]
  assert [tick.market_fence_sequence for tick in delta.ticks] == [12]


def test_decision_snapshot_rejects_delta_that_crosses_header_fence():
  now = datetime(2026, 9, 3, 1, 30, tzinfo=UTC)
  ring = SymbolMarketDeltaRing("600000.SH")
  tick = replace(_tick(1), market_fence_sequence=2)
  ring.accept(tick, capture_time_ms=2_000)
  item = SymbolDecisionSnapshot(
    instrument_code="600000.SH",
    state=_initial(),
    delta_slice=ring.slice_after(None, decision_time_ms=2_000),
    gate_context=OpportunityGateContext(),
  )

  with pytest.raises(ValueError, match="crosses header fence"):
    TDecisionSnapshot(
      execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.T_ASSISTANT_EXECUTION,
        "execution-1",
      ),
      decision_time=now,
      trade_date="2026-09-03",
      stream_id="stream-1",
      continuity_generation="1",
      fence_sequence=1,
      capture_as_of=now,
      universe_revision=0,
      config_version=1,
      config_snapshot_hash="a" * 64,
      policy_version=OpportunityPolicy().policy_version,
      feature_schema_version=OpportunityPolicy().feature_schema_version,
      execution_status=TAssistantExecutionStatus.RUNNING,
      entry_readiness=TAssistantEntryReadiness.READY,
      entry_readiness_as_of=now,
      symbols=(item,),
    )


def test_decision_snapshot_rejects_delta_cursor_identity_mismatch():
  now = datetime(2026, 9, 3, 1, 30, tzinfo=UTC)
  ring = SymbolMarketDeltaRing("600000.SH")
  tick = _tick(1)
  ring.accept(tick, capture_time_ms=2_000)
  item = SymbolDecisionSnapshot(
    instrument_code="600000.SH",
    state=_initial(),
    delta_slice=ring.slice_after(None, decision_time_ms=2_000),
    gate_context=OpportunityGateContext(),
  )

  with pytest.raises(ValueError, match="delta/header identity mismatch"):
    TDecisionSnapshot(
      execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.T_ASSISTANT_EXECUTION,
        "execution-1",
      ),
      decision_time=now,
      trade_date="2026-09-03",
      stream_id="other-stream",
      continuity_generation="1",
      fence_sequence=1,
      capture_as_of=now,
      universe_revision=0,
      config_version=1,
      config_snapshot_hash="a" * 64,
      policy_version=OpportunityPolicy().policy_version,
      feature_schema_version=OpportunityPolicy().feature_schema_version,
      execution_status=TAssistantExecutionStatus.RUNNING,
      entry_readiness=TAssistantEntryReadiness.READY,
      entry_readiness_as_of=now,
      symbols=(item,),
    )


def test_decision_snapshot_rejects_state_delta_from_cursor_mismatch():
  ring = SymbolMarketDeltaRing("600000.SH")
  tick = _tick(1)
  ring.accept(tick, capture_time_ms=2_000)
  delta = ring.slice_after(None, decision_time_ms=2_000)
  item = SymbolDecisionSnapshot(
    instrument_code="600000.SH",
    state=replace(_initial(), cursor=delta.next_cursor),
    delta_slice=delta,
    gate_context=OpportunityGateContext(),
  )

  with pytest.raises(ValueError, match="state/delta cursor mismatch"):
    _decision_for_item(item, fence_sequence=1)


def test_decision_snapshot_recomputes_delta_manifest():
  ring = SymbolMarketDeltaRing("600000.SH")
  tick = _tick(1)
  ring.accept(tick, capture_time_ms=2_000)
  delta = ring.slice_after(None, decision_time_ms=2_000)
  object.__setattr__(delta, "manifest_hash", "0" * 64)
  item = SymbolDecisionSnapshot(
    instrument_code="600000.SH",
    state=_initial(),
    delta_slice=delta,
    gate_context=OpportunityGateContext(),
  )

  with pytest.raises(ValueError, match="delta manifest mismatch"):
    _decision_for_item(item, fence_sequence=1)


def test_decision_snapshot_rejects_noncontiguous_tick_sequence():
  ring = SymbolMarketDeltaRing("600000.SH")
  first = _tick(1)
  second = _tick(2)
  ring.accept(first, capture_time_ms=2_000)
  ring.accept(second, capture_time_ms=2_000)
  delta = ring.slice_after(None, decision_time_ms=2_000)
  object.__setattr__(
    delta,
    "ticks",
    (first, replace(second, accepted_sequence=3)),
  )
  object.__setattr__(
    delta,
    "next_cursor",
    replace(delta.next_cursor, accepted_sequence=3),
  )
  object.__setattr__(delta, "to_accepted_sequence_inclusive", 3)
  object.__setattr__(delta, "manifest_hash", delta.computed_manifest_hash)
  item = SymbolDecisionSnapshot(
    instrument_code="600000.SH",
    state=_initial(),
    delta_slice=delta,
    gate_context=OpportunityGateContext(),
  )

  with pytest.raises(ValueError, match="Tick sequence is not contiguous"):
    _decision_for_item(item, fence_sequence=3)


def test_decision_snapshot_rejects_tick_next_cursor_source_mismatch():
  ring = SymbolMarketDeltaRing("600000.SH")
  first = _tick(1)
  second = _tick(2)
  ring.accept(first, capture_time_ms=2_000)
  ring.accept(second, capture_time_ms=2_000)
  delta = ring.slice_after(None, decision_time_ms=2_000)
  object.__setattr__(
    delta,
    "next_cursor",
    replace(delta.next_cursor, source_identity=first.source_identity),
  )
  object.__setattr__(delta, "manifest_hash", delta.computed_manifest_hash)
  item = SymbolDecisionSnapshot(
    instrument_code="600000.SH",
    state=_initial(),
    delta_slice=delta,
    gate_context=OpportunityGateContext(),
  )

  with pytest.raises(ValueError, match="Tick/cursor source identity mismatch"):
    _decision_for_item(item, fence_sequence=2)


def test_decision_snapshot_rejects_nonadvancing_tick_source_identity():
  ring = SymbolMarketDeltaRing("600000.SH")
  first = _tick(1)
  second = _tick(2)
  ring.accept(first, capture_time_ms=2_000)
  ring.accept(second, capture_time_ms=2_000)
  delta = ring.slice_after(None, decision_time_ms=2_000)
  duplicate_identity = replace(
    second,
    sample=replace(
      second.sample,
      source_time_ms=first.sample.source_time_ms,
      tick_ordinal=first.sample.tick_ordinal,
    ),
  )
  object.__setattr__(delta, "ticks", (first, duplicate_identity))
  object.__setattr__(
    delta,
    "next_cursor",
    replace(delta.next_cursor, source_identity=duplicate_identity.source_identity),
  )
  object.__setattr__(delta, "manifest_hash", delta.computed_manifest_hash)
  item = SymbolDecisionSnapshot(
    instrument_code="600000.SH",
    state=_initial(),
    delta_slice=delta,
    gate_context=OpportunityGateContext(),
  )

  with pytest.raises(ValueError, match="Tick source identity is not ordered"):
    _decision_for_item(item, fence_sequence=2)


def test_ring_capacity_boundary_and_overflow_are_exact():
  ring = SymbolMarketDeltaRing("600000.SH")
  ring.accept(_tick(1), capture_time_ms=10_000)
  cursor = ring.slice_after(None, decision_time_ms=1_001).next_cursor
  for sequence in range(2, T_ASSISTANT_DELTA_RING_CAPACITY + 2):
    ring.accept(_tick(sequence), capture_time_ms=20_000)
  at_capacity = ring.slice_after(
    cursor,
    decision_time_ms=cursor.source_identity.source_time_ms,
  )
  assert at_capacity.coverage is SymbolDeltaCoverage.REDUCER_LAG_EXCEEDED

  ring.accept(_tick(T_ASSISTANT_DELTA_RING_CAPACITY + 2), capture_time_ms=20_000)
  overflow = ring.slice_after(cursor, decision_time_ms=2_000)
  assert overflow.coverage is SymbolDeltaCoverage.RING_OVERFLOW
  assert overflow.reason_codes[0] == T_MARKET_RING_OVERFLOW


def test_reducer_lag_tick_boundary_is_512_then_fails_at_513():
  ring = SymbolMarketDeltaRing("600000.SH")
  ring.accept(_tick(1, source_time_ms=1_000), capture_time_ms=1_000)
  cursor = ring.slice_after(None, decision_time_ms=1_000).next_cursor
  for sequence in range(2, T_ASSISTANT_REDUCER_MAX_LAG_TICKS + 2):
    ring.accept(
      _tick(sequence, source_time_ms=1_001, ordinal=sequence),
      capture_time_ms=1_001,
    )
  boundary = ring.slice_after(cursor, decision_time_ms=1_001)
  assert len(boundary.ticks) == T_ASSISTANT_REDUCER_MAX_LAG_TICKS
  assert boundary.coverage is SymbolDeltaCoverage.READY

  next_sequence = T_ASSISTANT_REDUCER_MAX_LAG_TICKS + 2
  ring.accept(
    _tick(next_sequence, source_time_ms=1_001, ordinal=next_sequence),
    capture_time_ms=1_001,
  )
  assert (
    ring.slice_after(cursor, decision_time_ms=1_001).coverage
    is SymbolDeltaCoverage.REDUCER_LAG_EXCEEDED
  )


def test_initial_backlog_obeys_lag_gate_then_allows_explicit_rewarm_replay():
  ring = SymbolMarketDeltaRing("600000.SH")
  for sequence in range(1, T_ASSISTANT_REDUCER_MAX_LAG_TICKS + 2):
    ring.accept(
      _tick(sequence, source_time_ms=1_000, ordinal=sequence),
      capture_time_ms=1_000,
    )

  blocked = ring.slice_after(None, decision_time_ms=1_000)
  assert blocked.coverage is SymbolDeltaCoverage.REDUCER_LAG_EXCEEDED
  assert blocked.ticks == ()
  replay = ring.slice_after(
    blocked.next_cursor,
    decision_time_ms=1_000,
    rewarm_replay=True,
  )
  assert replay.coverage is SymbolDeltaCoverage.READY
  assert len(replay.ticks) == T_ASSISTANT_REDUCER_MAX_LAG_TICKS + 1


def test_reducer_lag_time_boundary_is_two_seconds_then_fails_after():
  ring = SymbolMarketDeltaRing("600000.SH")
  ring.accept(_tick(1, source_time_ms=1_000), capture_time_ms=1_000)
  cursor = ring.slice_after(None, decision_time_ms=1_000).next_cursor
  ring.accept(_tick(2, source_time_ms=1_001), capture_time_ms=1_001)

  assert (
    ring.slice_after(cursor, decision_time_ms=3_001).coverage
    is SymbolDeltaCoverage.READY
  )
  assert (
    ring.slice_after(cursor, decision_time_ms=3_002).coverage
    is SymbolDeltaCoverage.REDUCER_LAG_EXCEEDED
  )


def test_gap_reduction_clears_only_affected_symbol_state():
  policy = OpportunityPolicy()
  ring = SymbolMarketDeltaRing("600000.SH")
  ring.accept(_tick(1), capture_time_ms=2_000)
  cursor = ring.slice_after(None, decision_time_ms=2_000).next_cursor
  ring.accept(
    _tick(3, discontinuity_reason=T_MARKET_SEQUENCE_GAP),
    capture_time_ms=2_000,
  )
  gap = ring.slice_after(cursor, decision_time_ms=2_000)
  affected = _initial()
  affected = TAssistantSymbolState(
    execution_id=affected.execution_id,
    instrument_code=affected.instrument_code,
    revision=affected.revision,
    lifecycle=affected.lifecycle,
    cursor=cursor,
    opportunity_state=affected.opportunity_state,
    policy_version=affected.policy_version,
    feature_schema_version=affected.feature_schema_version,
  )
  reduction = SymbolMarketStateReducer().reduce(
    SymbolDecisionSnapshot(
      instrument_code="600000.SH",
      state=affected,
      delta_slice=gap,
      gate_context=OpportunityGateContext(),
    ),
    policy=policy,
  )

  assert reduction.material is True
  assert reduction.next_state.rewarm_reason == T_MARKET_SEQUENCE_GAP
  assert reduction.next_state.revision == affected.revision + 1
  assert reduction.next_state.opportunity_state.samples == ()


def test_symbol_states_are_independent_for_same_tick_shape():
  policy = OpportunityPolicy()
  states = {}
  for code in ("600000.SH", "000001.SZ"):
    ring = SymbolMarketDeltaRing(code)
    tick = _tick(1, code=code)
    ring.accept(tick, capture_time_ms=tick.received_at_ms)
    reduction = SymbolMarketStateReducer().reduce(
      SymbolDecisionSnapshot(
        instrument_code=code,
        state=_initial(code),
        delta_slice=ring.slice_after(None, decision_time_ms=tick.received_at_ms),
        gate_context=OpportunityGateContext(),
      ),
      policy=policy,
    )
    states[code] = reduction.next_state

  assert states["600000.SH"].opportunity_state.instrument_code == "600000.SH"
  assert states["000001.SZ"].opportunity_state.instrument_code == "000001.SZ"
  assert states["600000.SH"].opportunity_state.samples[0].instrument_code == "600000.SH"
  assert states["000001.SZ"].opportunity_state.samples[0].instrument_code == "000001.SZ"


def test_expired_deferred_candidate_is_audited_and_never_released():
  policy = OpportunityPolicy()
  ring = SymbolMarketDeltaRing("600000.SH")
  tick = _tick(1, source_time_ms=1_000)
  ring.accept(tick, capture_time_ms=1_000)
  cursor = ring.slice_after(None, decision_time_ms=1_000).next_cursor
  candidate = OpportunityCandidate(
    candidate_id="candidate-expired",
    fingerprint="f" * 64,
    episode_id="episode-expired",
    path=OpportunityPath.PULLBACK_REBOUND,
    latched_at_ms=1_000,
    expires_at_ms=2_000,
    source_time_ms=1_000,
    tick_ordinal=1,
    price=10.0,
    score=70.0,
    policy_version=policy.policy_version,
    feature_schema_version=policy.feature_schema_version,
    reference_profile_version="profile-v1",
    reference_profile_schema_version=1,
  )
  initial = _initial()
  state = replace(
    initial,
    lifecycle="ACTIVE",
    cursor=cursor,
    opportunity_state=replace(
      initial.opportunity_state,
      data_health="READY",
      candidate=candidate,
      candidate_status=CandidateStatus.LATCHED,
    ),
    deferred_candidate=candidate,
    deferred_candidate_fence_sequence=1,
  )
  reduction = SymbolMarketStateReducer().reduce(
    SymbolDecisionSnapshot(
      instrument_code="600000.SH",
      state=state,
      delta_slice=ring.slice_after(
        cursor,
        decision_time_ms=2_000,
        through_accepted_sequence=1,
      ),
      gate_context=OpportunityGateContext(
        continuous_session=True,
        session_code="CONTINUOUS_AM",
      ),
    ),
    policy=policy,
    allow_candidate_creation=True,
    decision_time_ms=2_000,
  )

  assert reduction.material is True
  assert reduction.opportunities == ()
  assert reduction.next_state.deferred_candidate is None
  assert reduction.next_state.deferred_candidate_fence_sequence is None
  assert reduction.next_state.opportunity_state.candidate_status is CandidateStatus.SUPPRESSED
  assert [event["event_type"] for event in reduction.material_events] == [
    "T_OPPORTUNITY_DEFERRED_CANDIDATE_EXPIRED"
  ]
  assert reduction.material_events[0]["candidate_id"] == candidate.candidate_id
