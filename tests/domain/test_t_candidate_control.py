"""Durable execution outcomes re-enter the same causal symbol reducer."""

from dataclasses import replace

import pytest
from quantx_domain.trading.t_assistant_market_state import (
  SymbolDecisionSnapshot,
  SymbolMarketDeltaRing,
  SymbolMarketStateReducer,
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  CandidateControl,
  OpportunityCandidate,
  OpportunityGateContext,
  OpportunityPolicy,
)

from tests.domain.test_t_assistant_market_state import (
  _decision_for_item,
  _initial,
  _tick,
)


def latched(*, deferred=False):
  policy = OpportunityPolicy()
  ring = SymbolMarketDeltaRing("600000.SH")
  tick = _tick(1, source_time_ms=1000)
  ring.accept(tick, capture_time_ms=1000)
  cursor = ring.slice_after(None, decision_time_ms=1000).next_cursor
  initial = _initial()
  candidate = OpportunityCandidate(
    "candidate",
    "a" * 64,
    "episode",
    "PULLBACK_REBOUND",
    1000,
    5000,
    1000,
    1,
    tick.sample.price,
    90,
    policy.policy_version,
    policy.feature_schema_version,
    "profile",
    1,
  )
  state = replace(
    initial,
    lifecycle="ACTIVE",
    cursor=cursor,
    opportunity_state=replace(
      initial.opportunity_state,
      data_health="READY",
      candidate=candidate,
      candidate_status="LATCHED",
    ),
    deferred_candidate=candidate if deferred else None,
    deferred_candidate_fence_sequence=1 if deferred else None,
  )
  return SymbolDecisionSnapshot(
    "600000.SH",
    state,
    ring.slice_after(cursor, decision_time_ms=1500),
    OpportunityGateContext(),
  )


def test_suppression_without_new_tick_prevents_deferred_release_and_is_idempotent():
  original = latched(deferred=True)
  snapshot = replace(
    original, candidate_control=CandidateControl(suppress_candidate_id="candidate")
  )
  reducer = SymbolMarketStateReducer()
  first = reducer.reduce(snapshot, policy=OpportunityPolicy(), decision_time_ms=1500)
  assert first.opportunities == () and first.evaluations == ()
  assert first.next_state.deferred_candidate is None
  assert first.next_state.opportunity_state.candidate_status.value == "SUPPRESSED"
  assert [item["event_type"] for item in first.material_events] == [
    "T_OPPORTUNITY_CANDIDATE_CONTROL_APPLIED"
  ]
  again = reducer.reduce(
    replace(snapshot, state=first.next_state),
    policy=OpportunityPolicy(),
    decision_time_ms=1501,
  )
  assert not again.material and again.opportunities == ()
  assert (
    again.next_state.material_manifest_hash == first.next_state.material_manifest_hash
  )
  assert (
    _decision_for_item(original, fence_sequence=1).snapshot_hash
    != _decision_for_item(snapshot, fence_sequence=1).snapshot_hash
  )


def test_control_for_another_candidate_cannot_cancel_the_current_one():
  snapshot = replace(
    latched(), candidate_control=CandidateControl(suppress_candidate_id="different")
  )
  result = SymbolMarketStateReducer().reduce(
    snapshot, policy=OpportunityPolicy(), decision_time_ms=1500
  )
  assert (
    not result.material
    and result.next_state.opportunity_state.candidate_status.value == "LATCHED"
  )


@pytest.mark.parametrize("now", [None, True, 1.5, 999])
def test_control_requires_an_explicit_causal_time(now):
  snapshot = replace(
    latched(), candidate_control=CandidateControl(suppress_candidate_id="candidate")
  )
  with pytest.raises(ValueError):
    SymbolMarketStateReducer().reduce(
      snapshot, policy=OpportunityPolicy(), decision_time_ms=now
    )
