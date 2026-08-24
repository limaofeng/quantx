import json
from dataclasses import replace
from random import Random

import pytest
from quantx_domain.trading.t_trade_opportunity_engine import (
  OPPORTUNITY_REFERENCE_PROFILE_SCHEMA_VERSION,
  CandidateControl,
  CandidateStatus,
  DataHealth,
  MomentumBranchState,
  MomentumPhase,
  OpportunityGateContext,
  OpportunityPath,
  OpportunityPolicy,
  OpportunityReferenceProfile,
  OpportunitySample,
  OpportunityState,
  PullbackBranchState,
  PullbackPhase,
  _candidate_path,
  reduce_opportunity,
  transition_candidate,
)

INSTRUMENT = "000001.SZ"
TRADE_DATE = "2026-08-21"
PROFILE = OpportunityReferenceProfile(
  profile_version="profile-v1",
  profile_schema_version=OPPORTUNITY_REFERENCE_PROFILE_SCHEMA_VERSION,
  as_of_trade_date="2026-08-20",
  pullback_threshold_pct=0.8,
  momentum_rise_threshold_pct=0.8,
  momentum_amount_velocity_ratio=2.0,
  pullback_max_spread_ticks=3,
  momentum_max_spread_ticks=10,
)


def _sample(
  seconds: int,
  price: float,
  ordinal: int,
  *,
  amount: float | None = None,
  volume: float | None = None,
  bid: float | None = None,
  ask: float | None = None,
  generation: str = "generation-1",
  trade_date: str = TRADE_DATE,
) -> OpportunitySample:
  return OpportunitySample(
    instrument_code=INSTRUMENT,
    trade_date=trade_date,
    source_time_ms=seconds * 1000,
    tick_ordinal=ordinal,
    price=price,
    continuity_generation=generation,
    bid_price=price - 0.01 if bid is None else bid,
    ask_price=price if ask is None else ask,
    cumulative_amount=amount,
    cumulative_volume=volume,
  )


def _reduce_all(
  samples,
  *,
  state=None,
  policy=None,
  gate_context=None,
  reference_profile=PROFILE,
):
  current = state or OpportunityState.initial()
  results = []
  for sample in samples:
    result = reduce_opportunity(
      current,
      sample,
      policy=policy,
      gate_context=gate_context,
      reference_profile=reference_profile,
    )
    current = result.state
    results.append(result)
  return current, results


def _pullback_samples():
  return [
    _sample(0, 100.0, 0, amount=1_000_000, volume=10_000),
    _sample(5, 99.0, 1, amount=1_050_000, volume=10_500),
    _sample(20, 99.0, 2, amount=1_100_000, volume=11_000),
    _sample(22, 99.30, 3, amount=1_120_000, volume=11_200),
    _sample(24, 99.32, 4, amount=1_140_000, volume=11_400),
  ]


def _momentum_samples():
  # A causal baseline followed by two distinct confirming observations while
  # the low remains inside the 60-second move window.
  rows = [
    (0, 27.35, 139_550_205.23, 5_151_984),
    (60, 27.41, 140_158_282.23, 5_174_184),
    (120, 27.50, 146_892_465.23, 5_418_784),
    (180, 27.58, 150_553_455.23, 5_551_684),
    (240, 27.53, 152_226_340.73, 5_612_473),
    (300, 27.53, 154_724_134.73, 5_703_073),
    (358, 27.78, 161_000_000.00, 5_930_000),
    (360, 27.80, 161_400_000.00, 5_945_000),
  ]
  return [
    _sample(
      seconds,
      price,
      index,
      amount=amount,
      volume=volume,
      bid=price - 0.03,
      ask=price + 0.02,
    )
    for index, (seconds, price, amount, volume) in enumerate(rows)
  ]


def _generated_causal_samples(seed: int, *, count: int = 28):
  random = Random(seed)
  seconds = 0
  price = 100.0
  amount = 1_000_000.0
  volume = 10_000.0
  samples = []
  for ordinal in range(count):
    seconds += random.choice((1, 2, 5, 11, 47))
    price = max(
      1.0,
      price + random.choice((-0.45, -0.20, -0.05, 0.0, 0.04, 0.18, 0.40)),
    )
    amount += random.randint(10_000, 90_000)
    volume += random.randint(100, 900)
    spread = random.choice((0.01, 0.02, 0.03, 0.08))
    samples.append(
      _sample(
        seconds,
        round(price, 4),
        ordinal,
        amount=amount,
        volume=volume,
        bid=round(price - spread, 4),
        ask=round(price + 0.01, 4),
      )
    )
  return samples


# Hypothesis is not a declared project dependency.  These fixed seeds keep the
# property corpus reproducible while exercising more than one price/feature
# path on every run.
_PROPERTY_SEEDS = (3, 11, 29, 47, 71, 101, 149)


def _causal_output_snapshot(result):
  """The complete observable decision for one source identity.

  Keeping source identity and candidate decision explicit makes this a guard
  against a future suffix rewriting an earlier output, even if the reducer
  gains additional diagnostic fields later.
  """

  evaluation = result.evaluation
  candidate = result.candidate_created
  return {
    "state": result.state.to_dict(),
    "evaluation": evaluation.to_dict(),
    "accepted": result.accepted,
    "ignored": result.ignored,
    "ignored_reason": result.ignored_reason,
    "source_identity": (
      evaluation.continuity_generation,
      evaluation.source_time_ms,
      evaluation.tick_ordinal,
    ),
    "candidate_decision": {
      "candidate_id": evaluation.candidate_id,
      "candidate_fingerprint": evaluation.candidate_fingerprint,
      "candidate_status": evaluation.candidate_status.value,
      "episode_id": evaluation.episode_id,
      "created": candidate.to_dict() if candidate is not None else None,
    },
  }


def _assert_all_prefixes_are_causally_invariant(samples):
  _, full_results = _reduce_all(samples)
  full_snapshots = [_causal_output_snapshot(result) for result in full_results]

  for prefix_length in range(1, len(samples) + 1):
    prefix_state, prefix_results = _reduce_all(samples[:prefix_length])
    assert [
      _causal_output_snapshot(result) for result in prefix_results
    ] == full_snapshots[:prefix_length], f"prefix_length={prefix_length}"
    assert prefix_state.to_dict() == full_results[prefix_length - 1].state.to_dict()


def _property_pullback_cycle(
  *,
  trade_date: str,
  generation: str,
  base_price: float,
  amount_base: float,
  ordinal_base: int = 0,
):
  """Generate one robust pullback/rebound episode from a deterministic seed."""

  low = round(base_price * 0.99, 4)
  rows = (
    (0, round(base_price, 4)),
    (5, low),
    (20, low),
    (22, round(low * 1.003, 4)),
    (24, round(low * 1.0032, 4)),
  )
  return [
    _sample(
      seconds,
      price,
      ordinal_base + index,
      amount=amount_base + index * 50_000,
      volume=10_000 + index * 500,
      generation=generation,
      trade_date=trade_date,
    )
    for index, (seconds, price) in enumerate(rows)
  ]


def _generated_episode_adversary_samples(seed: int):
  """A seeded corpus spanning causal boundaries and invalid source events."""

  random = Random(seed)
  first = _property_pullback_cycle(
    trade_date=TRADE_DATE,
    generation=f"property-{seed}-generation-1",
    base_price=100.0 + random.randrange(1, 50) / 100,
    amount_base=1_000_000 + random.randrange(0, 100_000),
  )
  second = _property_pullback_cycle(
    trade_date=TRADE_DATE,
    generation=f"property-{seed}-generation-2",
    base_price=100.0 + random.randrange(1, 50) / 100,
    amount_base=1_100_000 + random.randrange(0, 100_000),
  )
  third = _property_pullback_cycle(
    trade_date="2026-08-24",
    generation=f"property-{seed}-generation-3",
    base_price=100.0 + random.randrange(1, 50) / 100,
    amount_base=1_200_000 + random.randrange(0, 100_000),
  )
  missing_fields = replace(
    _sample(
      30,
      first[-1].price,
      5,
      generation=first[-1].continuity_generation,
      trade_date=first[-1].trade_date,
    ),
    bid_price=None,
    ask_price=None,
    cumulative_amount=None,
    cumulative_volume=None,
  )
  samples = [
    *first,
    first[-1],  # exact duplicate: must not advance the existing episode.
    first[2],  # out of order: must not advance the existing episode.
    missing_fields,  # accepted audit event, but must conservatively downgrade.
    *second,  # explicit continuity-generation boundary.
    second[-1],
    second[2],
    *third,  # trade-date boundary (and fresh continuity generation).
  ]
  return samples, {
    "first_duplicate": 5,
    "first_out_of_order": 6,
    "missing_fields": 7,
    "generation_boundary": 8,
    "second_duplicate": 13,
    "second_out_of_order": 14,
    "trade_date_boundary": 15,
    "candidate_indexes": (4, 12, 19),
  }


def test_pullback_fsm_latches_one_stable_candidate_per_episode():
  state, results = _reduce_all(_pullback_samples())

  assert [item.evaluation.pullback.phase for item in results] == [
    PullbackPhase.OBSERVING.value,
    PullbackPhase.LOW_STABILIZING.value,
    PullbackPhase.LOW_STABILIZING.value,
    PullbackPhase.REBOUND_CONFIRMING.value,
    PullbackPhase.CANDIDATE_LATCHED.value,
  ]
  assert results[-2].candidate_created is None
  candidate = results[-1].candidate_created
  assert candidate is not None
  assert candidate.path == OpportunityPath.PULLBACK_REBOUND
  assert candidate.score >= OpportunityPolicy().candidate_score
  assert state.candidate_status == CandidateStatus.LATCHED

  next_result = reduce_opportunity(
    state,
    _sample(26, 99.34, 5, amount=1_160_000, volume=11_600),
    reference_profile=PROFILE,
  )
  assert next_result.candidate_created is None
  assert next_result.state.candidate == candidate
  assert next_result.evaluation.candidate_fingerprint == candidate.fingerprint


def test_lower_low_restarts_pullback_stabilization_without_changing_episode():
  state, _ = _reduce_all(_pullback_samples()[:4])
  episode_id = state.pullback.episode_id

  result = reduce_opportunity(
    state,
    _sample(23, 98.80, 4, amount=1_130_000, volume=11_300),
    reference_profile=PROFILE,
  )

  assert result.state.pullback.episode_id == episode_id
  assert result.state.pullback.phase == PullbackPhase.LOW_STABILIZING
  assert result.state.pullback.confirmation_started_at_ms is None
  assert result.state.pullback.confirmation_ticks == 0
  assert result.candidate_created is None


def test_rolling_window_anchor_change_does_not_rewrite_active_episode():
  policy = OpportunityPolicy(
    pullback_lookback_seconds=30,
    candidate_ttl_seconds=1_000,
  )
  blocked = OpportunityGateContext(continuous_session=False)
  state, _ = _reduce_all(
    _pullback_samples(),
    policy=policy,
    gate_context=blocked,
  )
  episode_id = state.pullback.episode_id
  episode_source_time_ms = state.pullback.episode_started_source_time_ms
  assert episode_id is not None

  # The original 100.0 high has left the 30-second window. A new raw anchor
  # exists, but it is still the same uninterrupted pullback episode.
  changed_anchor = reduce_opportunity(
    state,
    _sample(35, 98.80, 5, amount=1_200_000, volume=12_000),
    gate_context=blocked,
    policy=policy,
    reference_profile=PROFILE,
  )

  assert changed_anchor.evaluation.features.pullback_pct == pytest.approx(0.5235602094)
  assert changed_anchor.state.pullback.episode_id == episode_id
  assert (
    changed_anchor.state.pullback.episode_started_source_time_ms
    == episode_source_time_ms
  )


def test_candidate_path_owns_evaluation_when_other_fsm_scores_higher():
  policy = OpportunityPolicy(
    pullback_lookback_seconds=30,
    momentum_window_seconds=15,
    momentum_min_move_seconds=15,
    momentum_baseline_seconds=15,
    momentum_baseline_coverage_ratio=1.0,
    momentum_min_coverage_seconds=15,
    candidate_ttl_seconds=1_000,
  )
  state, results = _reduce_all(_pullback_samples(), policy=policy)
  candidate = results[-1].candidate_created
  assert candidate is not None
  assert candidate.path == OpportunityPath.PULLBACK_REBOUND

  # The old pullback anchor leaves the rolling window while momentum becomes
  # the higher-scoring branch. The latched pullback candidate must survive and
  # the top-level approval projection must still be pullback-specific.
  result = reduce_opportunity(
    state,
    _sample(35, 100.50, 5, amount=1_300_000, volume=13_000),
    policy=policy,
    reference_profile=PROFILE,
  )

  assert result.evaluation.momentum.score > result.evaluation.pullback.score
  assert result.state.candidate == candidate
  assert result.state.candidate_status == CandidateStatus.LATCHED
  assert result.state.pullback.episode_id == candidate.episode_id
  assert result.evaluation.selected_path == candidate.path
  assert result.evaluation.opportunity_score == result.evaluation.pullback.score
  assert result.evaluation.hard_gates == result.evaluation.pullback.hard_gates
  assert result.evaluation.blockers == result.evaluation.pullback.blockers

  restored = OpportunityState.from_dict(result.state.to_dict())
  replayed = reduce_opportunity(
    restored,
    _sample(36, 100.51, 6, amount=1_310_000, volume=13_100),
    policy=policy,
    reference_profile=PROFILE,
  )
  direct = reduce_opportunity(
    result.state,
    _sample(36, 100.51, 6, amount=1_310_000, volume=13_100),
    policy=policy,
    reference_profile=PROFILE,
  )
  assert replayed.to_dict() == direct.to_dict()


def test_momentum_fsm_uses_prior_baseline_and_latches_after_two_sources():
  state, results = _reduce_all(_momentum_samples())

  assert results[-2].evaluation.momentum.phase == MomentumPhase.ACCELERATING.value
  assert results[-2].state.momentum.confirmation_started_at_ms == 358_000
  assert results[-2].state.momentum.confirmation_started_tick_ordinal == 6
  assert results[-2].candidate_created is None
  candidate = results[-1].candidate_created
  assert candidate is not None
  assert candidate.path == OpportunityPath.MOMENTUM_ACCELERATION
  assert results[-1].evaluation.momentum.score >= OpportunityPolicy().candidate_score
  assert results[-1].evaluation.features.momentum_baseline_coverage_seconds == 300
  assert results[-1].evaluation.features.momentum_amount_velocity_ratio is not None
  assert state.momentum.phase == MomentumPhase.CANDIDATE_LATCHED


def test_confirmation_source_ordinal_is_serialized_and_reset_with_confirmation():
  state, results = _reduce_all(_pullback_samples())

  assert results[-2].state.pullback.confirmation_started_at_ms == 22_000
  assert results[-2].state.pullback.confirmation_started_tick_ordinal == 3
  assert results[-1].state.pullback.confirmation_started_tick_ordinal == 3

  restored = OpportunityState.from_dict(state.to_dict())
  assert restored.to_dict() == state.to_dict()
  assert restored.pullback.confirmation_started_tick_ordinal == 3

  restarted = reduce_opportunity(
    results[-2].state,
    _sample(23, 98.80, 4, amount=1_130_000, volume=11_300),
    reference_profile=PROFILE,
  )
  assert restarted.state.pullback.confirmation_started_at_ms is None
  assert restarted.state.pullback.confirmation_started_tick_ordinal is None
  assert restarted.state.pullback.confirmation_ticks == 0


def test_equal_candidate_scores_choose_first_confirmation_source_identity():
  sample = _sample(20, 100.0, 20)
  pullback = PullbackBranchState(
    phase=PullbackPhase.REBOUND_CONFIRMING,
    episode_id="pullback-episode",
    confirmation_started_at_ms=10_000,
    confirmation_started_tick_ordinal=10,
    confirmation_ticks=2,
  )
  momentum = MomentumBranchState(
    phase=MomentumPhase.ACCELERATING,
    episode_id="momentum-episode",
    confirmation_started_at_ms=9_000,
    confirmation_started_tick_ordinal=9,
    confirmation_ticks=2,
  )

  assert (
    _candidate_path(pullback, momentum, 80.0, 80.0, sample, OpportunityPolicy())
    == OpportunityPath.MOMENTUM_ACCELERATION
  )

  same_source_momentum = replace(
    momentum,
    confirmation_started_at_ms=10_000,
    confirmation_started_tick_ordinal=10,
  )
  assert (
    _candidate_path(
      pullback,
      same_source_momentum,
      80.0,
      80.0,
      sample,
      OpportunityPolicy(),
    )
    == OpportunityPath.PULLBACK_REBOUND
  )

  tie_state = OpportunityState(pullback=pullback, momentum=momentum)
  replayed_state = OpportunityState.from_dict(tie_state.to_dict())
  direct_path = _candidate_path(
    tie_state.pullback,
    tie_state.momentum,
    80.0,
    80.0,
    sample,
    OpportunityPolicy(),
  )
  replayed_path = _candidate_path(
    replayed_state.pullback,
    replayed_state.momentum,
    80.0,
    80.0,
    sample,
    OpportunityPolicy(),
  )
  assert direct_path == replayed_path == OpportunityPath.MOMENTUM_ACCELERATION


def test_market_gate_vetoes_high_scoring_pattern_without_stopping_observation():
  state, results = _reduce_all(
    _pullback_samples(),
    gate_context=OpportunityGateContext(continuous_session=False),
  )

  final = results[-1]
  assert final.evaluation.pullback.score >= OpportunityPolicy().candidate_score
  assert "CONTINUOUS_SESSION" in final.evaluation.pullback.blockers
  assert final.evaluation.pullback.candidate_ready is False
  assert final.candidate_created is None
  assert state.pullback.phase == PullbackPhase.REBOUND_CONFIRMING
  assert state.pullback.episode_id is not None


def test_duplicate_and_out_of_order_sources_are_nonadvancing():
  state, _ = _reduce_all(_pullback_samples()[:4])
  snapshot = state.to_dict()

  duplicate = reduce_opportunity(
    state,
    _pullback_samples()[3],
    reference_profile=PROFILE,
  )
  out_of_order = reduce_opportunity(
    state,
    replace(_pullback_samples()[2], tick_ordinal=99),
    reference_profile=PROFILE,
  )

  assert duplicate.state.to_dict() == snapshot
  assert out_of_order.state.to_dict() == snapshot
  assert duplicate.candidate_created is None
  assert out_of_order.candidate_created is None
  assert duplicate.accepted is False
  assert duplicate.ignored is True
  assert out_of_order.accepted is False
  assert out_of_order.ignored is True
  assert duplicate.evaluation.data_health_reasons == ("DUPLICATE_SOURCE_IDENTITY",)
  assert out_of_order.evaluation.data_health_reasons == (
    "OUT_OF_ORDER_SOURCE_IDENTITY",
  )


def test_ignored_identity_cannot_apply_candidate_control_or_causal_ttl():
  policy = OpportunityPolicy(candidate_ttl_seconds=60)
  state, results = _reduce_all(_pullback_samples(), policy=policy)
  candidate = results[-1].candidate_created
  assert candidate is not None

  duplicate = reduce_opportunity(
    state,
    _pullback_samples()[-1],
    candidate_control=CandidateControl(
      awaiting_approval_candidate_id=candidate.candidate_id
    ),
    policy=policy,
    reference_profile=PROFILE,
  )
  assert duplicate.state == state
  assert duplicate.accepted is False
  assert duplicate.ignored is True
  assert duplicate.ignored_reason == "DUPLICATE_SOURCE_IDENTITY"

  invalid_quote = replace(
    _pullback_samples()[-1],
    source_time_ms=_pullback_samples()[-1].source_time_ms + 1_000,
    tick_ordinal=_pullback_samples()[-1].tick_ordinal + 1,
    price=0.0,
  )
  invalid = reduce_opportunity(
    state,
    invalid_quote,
    candidate_control=CandidateControl(
      awaiting_approval_candidate_id=candidate.candidate_id
    ),
    policy=policy,
    reference_profile=PROFILE,
  )
  assert invalid.state == state
  assert invalid.accepted is False
  assert invalid.ignored is False
  assert invalid.ignored_reason == "INVALID_PRICE"
  assert invalid.evaluation.data_health == DataHealth.INSUFFICIENT

  # Construct a valid persisted state whose latest consumed sample is already
  # past the candidate TTL. The probe itself is older than that latest sample,
  # so the identity guard must win over transition_candidate's TTL check.
  expired_tail = replace(
    _pullback_samples()[-1],
    source_time_ms=candidate.expires_at_ms + 1_000,
    tick_ordinal=_pullback_samples()[-1].tick_ordinal + 1,
  )
  expired_state = replace(state, samples=(*state.samples, expired_tail))
  ttl_probe = replace(
    _pullback_samples()[-1],
    source_time_ms=candidate.expires_at_ms,
    tick_ordinal=_pullback_samples()[-1].tick_ordinal + 2,
  )
  out_of_order = reduce_opportunity(
    expired_state,
    ttl_probe,
    policy=policy,
    reference_profile=PROFILE,
  )
  assert out_of_order.state == expired_state
  assert out_of_order.accepted is False
  assert out_of_order.ignored is True
  assert out_of_order.ignored_reason == "OUT_OF_ORDER_SOURCE_IDENTITY"
  assert out_of_order.state.candidate_status == CandidateStatus.LATCHED


def test_normal_sparse_samples_do_not_mean_continuity_loss():
  samples = [
    _sample(0, 10.0, 0, amount=100_000, volume=10_000),
    _sample(120, 9.95, 1, amount=110_000, volume=11_000),
    _sample(240, 9.96, 2, amount=120_000, volume=12_000),
  ]

  state, results = _reduce_all(samples)

  assert len(state.samples) == 3
  assert results[-1].evaluation.data_health == DataHealth.DEGRADED
  assert "SPARSE_SAMPLE_COVERAGE" in results[-1].evaluation.data_health_reasons
  assert results[-1].evaluation.data_health != DataHealth.CONTINUITY_LOST


def test_generation_change_invalidates_latched_candidate_and_causal_window():
  state, results = _reduce_all(_pullback_samples())
  assert results[-1].candidate_created is not None

  changed = reduce_opportunity(
    state,
    _sample(
      25,
      99.33,
      5,
      amount=1_150_000,
      volume=11_500,
      generation="generation-2",
    ),
    reference_profile=PROFILE,
  )

  assert changed.evaluation.data_health == DataHealth.CONTINUITY_LOST
  assert changed.evaluation.data_health_reasons == ("CONTINUITY_GENERATION_CHANGED",)
  assert changed.state.candidate is None
  assert changed.state.candidate_status == CandidateStatus.NONE
  assert changed.state.pullback.phase == PullbackPhase.OBSERVING
  assert changed.state.momentum.phase == MomentumPhase.BASELINING
  assert len(changed.state.samples) == 1


def test_cumulative_counter_rollback_invalidates_candidate_and_starts_new_segment():
  state, results = _reduce_all(_pullback_samples())
  assert results[-1].candidate_created is not None

  reset = reduce_opportunity(
    state,
    _sample(25, 99.33, 5, amount=10_000, volume=100),
    reference_profile=PROFILE,
  )

  assert reset.evaluation.data_health == DataHealth.DEGRADED
  assert reset.evaluation.data_health_reasons == ("CUMULATIVE_COUNTER_ROLLBACK",)
  assert reset.state.candidate is None
  assert len(reset.state.samples) == 1


def test_source_identity_includes_continuity_generation():
  first = _sample(6, 99.1, 2, generation="generation-1")
  second = _sample(6, 99.1, 2, generation="generation-2")

  assert first.source_identity == ("generation-1", 6_000, 2)
  assert first.source_identity != second.source_identity


def test_latched_candidate_can_be_suppressed_then_rearmed_after_low_score_dwell():
  policy = OpportunityPolicy(
    pullback_lookback_seconds=30,
    candidate_ttl_seconds=1_000,
  )
  state, results = _reduce_all(_pullback_samples(), policy=policy)
  candidate = results[-1].candidate_created
  assert candidate is not None

  suppressed = reduce_opportunity(
    state,
    _sample(25, 99.33, 5, amount=1_150_000, volume=11_500),
    candidate_control=CandidateControl(suppress_candidate_id=candidate.candidate_id),
    policy=policy,
    reference_profile=PROFILE,
  )
  assert suppressed.state.candidate_status == CandidateStatus.SUPPRESSED
  assert suppressed.candidate_created is None

  rearming = reduce_opportunity(
    suppressed.state,
    _sample(50, 100.0, 6, amount=1_300_000, volume=13_000),
    policy=policy,
    reference_profile=PROFILE,
  )
  assert rearming.state.candidate_status == CandidateStatus.REARMING
  assert rearming.state.candidate is not None

  rearmed = reduce_opportunity(
    rearming.state,
    _sample(65, 99.8, 7, amount=1_310_000, volume=13_100),
    policy=policy,
    reference_profile=PROFILE,
  )
  assert rearmed.state.candidate is None
  assert rearmed.state.candidate_status == CandidateStatus.NONE
  assert rearmed.candidate_created is None
  assert len(rearmed.state.samples) == 1
  assert rearmed.state.data_health == DataHealth.WARMING


def test_rearm_requires_strict_continuous_below_threshold_dwell():
  policy = OpportunityPolicy(
    pullback_lookback_seconds=30,
    candidate_ttl_seconds=1_000,
  )
  state, results = _reduce_all(_pullback_samples(), policy=policy)
  candidate = results[-1].candidate_created
  assert candidate is not None
  state = transition_candidate(
    state,
    CandidateControl(suppress_candidate_id=candidate.candidate_id),
    source_time_ms=25_000,
  )

  started = reduce_opportunity(
    state,
    _sample(50, 100.0, 6, amount=1_300_000, volume=13_000),
    policy=policy,
    reference_profile=PROFILE,
  )
  assert started.state.candidate_status == CandidateStatus.REARMING
  assert started.state.rearm_started_at_ms == 50_000

  # Equality is not "below" the rearm threshold, so it resets the timer.
  reset = reduce_opportunity(
    started.state,
    _sample(55, 99.0, 7, amount=1_310_000, volume=13_100),
    policy=policy,
    reference_profile=PROFILE,
  )
  assert reset.evaluation.pullback.score == policy.rearm_score
  assert reset.state.candidate_status == CandidateStatus.SUPPRESSED
  assert reset.state.rearm_started_at_ms is None
  assert reset.state.candidate == candidate

  high = reduce_opportunity(
    reset.state,
    _sample(65, 99.3, 8, amount=1_320_000, volume=13_200),
    policy=policy,
    reference_profile=PROFILE,
  )
  assert high.evaluation.pullback.score > policy.rearm_score
  assert high.state.candidate == candidate
  assert high.state.rearm_started_at_ms is None

  low_again = reduce_opportunity(
    high.state,
    _sample(90, 99.8, 9, amount=1_330_000, volume=13_300),
    policy=policy,
    reference_profile=PROFILE,
  )
  assert low_again.state.candidate_status == CandidateStatus.REARMING
  assert low_again.state.rearm_started_at_ms == 90_000
  not_yet = reduce_opportunity(
    low_again.state,
    _sample(104, 99.8, 10, amount=1_340_000, volume=13_400),
    policy=policy,
    reference_profile=PROFILE,
  )
  assert not_yet.state.candidate == candidate
  rearmed = reduce_opportunity(
    not_yet.state,
    _sample(105, 99.8, 11, amount=1_350_000, volume=13_500),
    policy=policy,
    reference_profile=PROFILE,
  )
  assert rearmed.state.candidate is None
  assert rearmed.state.candidate_status == CandidateStatus.NONE
  assert len(rearmed.state.samples) == 1

  next_state = rearmed.state
  next_candidate = None
  for ordinal, (seconds, price) in enumerate(
    [(110, 101.0), (115, 100.0), (130, 100.0), (132, 100.3), (134, 100.32)],
    start=12,
  ):
    next_result = reduce_opportunity(
      next_state,
      _sample(
        seconds,
        price,
        ordinal,
        amount=1_350_000 + ordinal * 10_000,
        volume=13_500 + ordinal * 100,
      ),
      policy=policy,
      reference_profile=PROFILE,
    )
    next_state = next_result.state
    next_candidate = next_result.candidate_created or next_candidate

  assert next_candidate is not None
  assert next_candidate.episode_id != candidate.episode_id


def test_latched_candidate_can_enter_awaiting_approval_without_relatching():
  state, results = _reduce_all(_pullback_samples())
  candidate = results[-1].candidate_created
  assert candidate is not None
  control = CandidateControl(awaiting_approval_candidate_id=candidate.candidate_id)

  awaiting = transition_candidate(
    state,
    CandidateControl.from_dict(control.to_dict()),
    source_time_ms=candidate.source_time_ms,
  )

  assert awaiting.candidate_status == CandidateStatus.AWAITING_APPROVAL
  assert awaiting.candidate == candidate
  assert awaiting.pullback.phase == PullbackPhase.CANDIDATE_LATCHED
  assert len(awaiting.samples) == len(state.samples)
  assert OpportunityState.from_dict(awaiting.to_dict()) == awaiting


def test_candidate_ttl_is_causal_and_expired_candidate_cannot_be_approved():
  state, results = _reduce_all(_pullback_samples())
  created = results[-1]
  candidate = created.candidate_created
  assert candidate is not None
  assert candidate.latched_at_ms == candidate.source_time_ms
  assert candidate.expires_at_ms == (
    candidate.source_time_ms + OpportunityPolicy().candidate_ttl_seconds * 1000
  )
  assert created.evaluation.continuity_generation == "generation-1"
  assert created.evaluation.candidate_created_at_ms == candidate.source_time_ms
  assert created.evaluation.candidate_expires_at_ms == candidate.expires_at_ms

  expired = transition_candidate(
    state,
    CandidateControl(awaiting_approval_candidate_id=candidate.candidate_id),
    source_time_ms=candidate.expires_at_ms,
  )
  retried = transition_candidate(
    expired,
    CandidateControl(awaiting_approval_candidate_id=candidate.candidate_id),
    source_time_ms=candidate.expires_at_ms,
  )

  assert expired.candidate_status == CandidateStatus.SUPPRESSED
  assert expired.candidate_suppressed is True
  assert expired.candidate_awaiting_approval is False
  assert expired.pullback.phase == PullbackPhase.SUPPRESSED
  assert retried == expired


@pytest.mark.parametrize(
  "overrides",
  [
    {"preview_score": 45.0},
    {"preview_score": 60.0, "revalidate_score": 60.0},
    {"revalidate_score": 72.0},
    {"candidate_score": 101.0},
    {"candidate_confirm_seconds": 0},
    {"rearm_seconds": 0},
    {"candidate_ttl_seconds": 0},
  ],
)
def test_policy_rejects_invalid_score_ordering_and_nonpositive_timing(overrides):
  with pytest.raises(ValueError):
    OpportunityPolicy(**overrides)


def test_policy_rejects_incoherent_causal_windows():
  with pytest.raises(ValueError, match="pullback_stabilization_seconds"):
    OpportunityPolicy(
      pullback_lookback_seconds=15,
      pullback_stabilization_seconds=15,
    )
  with pytest.raises(ValueError, match="momentum_min_move_seconds"):
    OpportunityPolicy(
      momentum_window_seconds=30,
      momentum_min_move_seconds=31,
    )
  with pytest.raises(ValueError, match="momentum_window_seconds"):
    OpportunityPolicy(
      momentum_window_seconds=301,
      momentum_baseline_seconds=300,
    )


@pytest.mark.parametrize(
  ("overrides", "message"),
  [
    ({"max_samples": 3_001}, "max_samples must not exceed"),
    ({"max_quote_age_ms": 30_001}, "max_quote_age_ms must not exceed"),
    ({"candidate_confirm_seconds": 61}, "candidate_confirm_seconds must not exceed"),
    ({"candidate_confirm_ticks": 121}, "candidate_confirm_ticks must not exceed"),
    ({"rearm_seconds": 14_401}, "rearm_seconds must not exceed"),
    ({"candidate_ttl_seconds": 14_401}, "candidate_ttl_seconds must not exceed"),
    ({"pullback_lookback_seconds": 14_401}, "state window must not exceed"),
  ],
)
def test_policy_rejects_values_above_persisted_state_safety_limits(overrides, message):
  with pytest.raises(ValueError, match=message):
    OpportunityPolicy(**overrides)


def test_default_policy_freezes_v3_scores_and_component_weights():
  policy = OpportunityPolicy()
  _, pullback_results = _reduce_all(_pullback_samples(), policy=policy)
  _, momentum_results = _reduce_all(_momentum_samples(), policy=policy)

  pullback = pullback_results[-1].evaluation.pullback
  momentum = momentum_results[-1].evaluation.momentum
  assert pullback.score == 90.0
  assert momentum.score == 86.0
  pullback_weights = {
    item.name: item.weight
    for item in pullback.components
    if not item.name.endswith("PENALTY")
  }
  momentum_weights = {
    item.name: item.weight
    for item in momentum.components
    if not item.name.endswith("PENALTY")
  }
  assert pullback_weights == {
    "PULLBACK_DEPTH": policy.pullback_depth_weight,
    "REBOUND_STRENGTH": policy.pullback_rebound_weight,
    "LOW_STABILIZATION": policy.pullback_stabilization_weight,
    "TURN_SLOPE": policy.pullback_turn_slope_weight,
    "PULLBACK_VWAP_POSITION": policy.pullback_vwap_weight,
    "PULLBACK_LIQUIDITY": policy.pullback_liquidity_weight,
    "PULLBACK_VOLUME_CONFIRMATION": policy.pullback_volume_weight,
  }
  assert momentum_weights == {
    "MOMENTUM_RISE": policy.momentum_rise_weight,
    "MOMENTUM_TURNOVER": policy.momentum_turnover_weight,
    "MOMENTUM_SLOPE": policy.momentum_slope_weight,
    "HIGH_PERSISTENCE": policy.momentum_persistence_weight,
    "MOMENTUM_VWAP_REGIME": policy.momentum_vwap_weight,
    "MOMENTUM_LIQUIDITY": policy.momentum_liquidity_weight,
    "BOOK_IMBALANCE": policy.momentum_book_imbalance_weight,
  }


def test_policy_round_trip_is_complete_and_rejects_missing_or_unknown_fields():
  payload = OpportunityPolicy().to_dict()
  assert OpportunityPolicy.from_dict(payload).to_dict() == payload

  missing = dict(payload)
  missing.pop("pullback_depth_weight")
  with pytest.raises(ValueError, match="missing fields: pullback_depth_weight"):
    OpportunityPolicy.from_dict(missing)
  with pytest.raises(ValueError, match="unknown fields: hidden_magic"):
    OpportunityPolicy.from_dict({**payload, "hidden_magic": 1})
  with pytest.raises(ValueError, match="feature_schema_version does not match"):
    OpportunityPolicy.from_dict({**payload, "feature_schema_version": 999})


@pytest.mark.parametrize(
  ("overrides", "message"),
  [
    (
      {"pullback_depth_weight": 24.0},
      "pullback weights must sum to 100",
    ),
    (
      {
        "momentum_rise_weight": -1.0,
        "momentum_turnover_weight": 41.0,
      },
      "momentum weights must be finite and non-negative",
    ),
    (
      {"pullback_vwap_zero_score_premium_pct": 0.0},
      "pullback_vwap_full_score_max_premium_pct must be below",
    ),
    (
      {"allowed_session_codes": ("CLOSING_AUCTION",)},
      "allowed_session_codes has unsupported values",
    ),
    (
      {"continuous_pm_start_time": "15:00", "continuous_pm_end_time": "14:57"},
      "trading window start must be before end",
    ),
    (
      {"continuous_am_start_time": "09:99"},
      "must be a valid wall-clock time",
    ),
    (
      {"continuous_am_start_time": "09:29"},
      "must stay within 09:30-11:30",
    ),
    (
      {"pullback_required_fields": ("last_price",)},
      "pullback_required_fields has unsupported fields",
    ),
    (
      {"momentum_min_coverage_seconds": 239},
      "momentum_min_coverage_seconds must cover",
    ),
    (
      {"pullback_min_coverage_seconds": 301},
      "must not exceed pullback lookback",
    ),
    (
      {
        "continuous_am_start_time": "11:29:00",
        "candidate_ttl_seconds": 60,
      },
      "cannot contain TTL/confirmation/rearm",
    ),
  ],
)
def test_policy_rejects_invalid_weights_boundaries_sessions_and_data_contract(
  overrides,
  message,
):
  with pytest.raises(ValueError, match=message):
    OpportunityPolicy(**overrides)


def test_policy_normalizes_time_and_collection_identity():
  policy = OpportunityPolicy(
    continuous_am_start_time="9:30",
    allowed_session_codes=["CONTINUOUS_PM", "CONTINUOUS_AM"],
    pullback_required_fields=[
      "cumulative_volume",
      "bid_price",
      "cumulative_amount",
      "ask_price",
    ],
  )

  assert policy.continuous_am_start_time == "09:30:00"
  assert policy.allowed_session_codes == ("CONTINUOUS_AM", "CONTINUOUS_PM")
  assert policy.pullback_required_fields == (
    "bid_price",
    "ask_price",
    "cumulative_amount",
    "cumulative_volume",
  )


def test_missing_market_values_remain_none_and_json_serializable():
  sample = OpportunitySample(
    instrument_code=INSTRUMENT,
    trade_date=TRADE_DATE,
    source_time_ms=0,
    tick_ordinal=0,
    price=10.0,
    continuity_generation="generation-1",
  )

  result = reduce_opportunity(OpportunityState.initial(), sample)
  features = result.evaluation.features

  assert features.bid_price is None
  assert features.ask_price is None
  assert features.spread_ticks is None
  assert features.session_vwap is None
  assert features.vwap_premium_pct is None
  assert features.return_60s_pct is None
  assert features.pullback_pct is None
  assert features.momentum_rise_pct is None
  assert features.momentum_move_seconds is None
  assert result.evaluation.data_health == DataHealth.INSUFFICIENT
  assert result.evaluation.data_health_reasons[0] == "REFERENCE_PROFILE_MISSING"
  assert result.evaluation.pullback.score is None
  assert result.evaluation.momentum.score is None
  assert result.evaluation.selected_path == OpportunityPath.NONE
  assert result.evaluation.opportunity_score is None
  json.dumps(result.to_dict(), allow_nan=False)


def test_startup_coverage_with_valid_profile_is_warming_and_scores_are_null():
  result = reduce_opportunity(
    OpportunityState.initial(),
    _sample(0, 10.0, 0, amount=100_000, volume=10_000),
    reference_profile=PROFILE,
  )

  assert result.evaluation.data_health == DataHealth.WARMING
  assert result.evaluation.pullback.score is None
  assert result.evaluation.momentum.score is None
  assert result.evaluation.opportunity_score is None


def test_state_round_trip_is_exact_and_rejects_old_schema():
  state, _ = _reduce_all(_pullback_samples())

  restored = OpportunityState.from_dict(state.to_dict())

  assert restored == state
  json.dumps(restored.to_dict(), allow_nan=False)
  with pytest.raises(ValueError, match="schema_version=2"):
    OpportunityState.from_dict({"schema_version": 2})


def test_candidate_fingerprint_is_deterministic_for_identical_prefix():
  first_state, first_results = _reduce_all(_pullback_samples())
  second_state, second_results = _reduce_all(_pullback_samples())

  first_candidate = first_results[-1].candidate_created
  second_candidate = second_results[-1].candidate_created
  assert first_candidate is not None and second_candidate is not None
  assert first_candidate == second_candidate
  assert first_state.to_dict() == second_state.to_dict()


def test_every_deterministic_prefix_is_invariant_to_its_future_suffix():
  pullback = [
    *_pullback_samples(),
    _sample(30, 101.0, 5, amount=1_200_000, volume=12_000),
    _sample(40, 98.0, 6, amount=1_250_000, volume=12_500),
  ]
  momentum = [
    *_momentum_samples(),
    _sample(370, 27.60, 8, amount=162_000_000, volume=5_970_000),
    _sample(390, 27.90, 9, amount=164_000_000, volume=6_010_000),
  ]
  sequences = [
    pullback,
    momentum,
    *(_generated_causal_samples(seed) for seed in (3, 11, 29, 47, 71, 101)),
  ]

  assert any(
    result.candidate_created is not None
    for samples in (pullback, momentum)
    for result in _reduce_all(samples)[1]
  )
  for samples in sequences:
    _assert_all_prefixes_are_causally_invariant(samples)


@pytest.mark.parametrize("seed", _PROPERTY_SEEDS)
def test_seeded_generated_prefixes_are_invariant_to_future_suffixes(seed):
  """Future valid and invalid observations cannot rewrite an earlier decision."""

  adversarial, _ = _generated_episode_adversary_samples(seed)
  for samples in (
    _generated_causal_samples(seed, count=36),
    adversarial,
  ):
    _assert_all_prefixes_are_causally_invariant(samples)


@pytest.mark.parametrize("seed", _PROPERTY_SEEDS)
def test_seeded_episodes_emit_at_most_one_candidate_across_adversarial_inputs(seed):
  """Duplicates, disorder, missing fields, and boundaries cannot relatch an episode."""

  samples, markers = _generated_episode_adversary_samples(seed)
  _, results = _reduce_all(samples)

  created = [
    (index, result.candidate_created)
    for index, result in enumerate(results)
    if result.candidate_created is not None
  ]
  assert tuple(index for index, _ in created) == markers["candidate_indexes"]
  assert len(created) == 3
  candidates_by_episode = {}
  for _, candidate in created:
    assert candidate is not None
    candidates_by_episode.setdefault(candidate.episode_id, []).append(candidate)
  assert all(
    len(candidates) <= 1 for candidates in candidates_by_episode.values()
  )
  assert len(candidates_by_episode) == len(created)

  for marker in ("first_duplicate", "second_duplicate"):
    result = results[markers[marker]]
    assert result.accepted is False
    assert result.ignored is True
    assert result.ignored_reason == "DUPLICATE_SOURCE_IDENTITY"
    assert result.evaluation.data_health == DataHealth.DEGRADED
    assert result.candidate_created is None

  for marker in ("first_out_of_order", "second_out_of_order"):
    result = results[markers[marker]]
    assert result.accepted is False
    assert result.ignored is True
    assert result.ignored_reason == "OUT_OF_ORDER_SOURCE_IDENTITY"
    assert result.evaluation.data_health == DataHealth.DEGRADED
    assert result.candidate_created is None

  missing = results[markers["missing_fields"]]
  assert missing.accepted is True
  assert missing.ignored is False
  assert missing.evaluation.data_health == DataHealth.DEGRADED
  assert missing.evaluation.data_health != DataHealth.READY
  assert missing.evaluation.pullback.candidate_ready is False
  assert "DATA_READY" in missing.evaluation.pullback.blockers
  assert missing.candidate_created is None

  generation_boundary = results[markers["generation_boundary"]]
  assert generation_boundary.evaluation.data_health == DataHealth.CONTINUITY_LOST
  assert generation_boundary.state.candidate is None
  trade_date_boundary = results[markers["trade_date_boundary"]]
  assert trade_date_boundary.state.trade_date == "2026-08-24"
  assert trade_date_boundary.state.candidate is None


def test_noncausal_reference_profile_is_a_hard_blocker():
  profile = OpportunityReferenceProfile(
    profile_version="profile-v1",
    profile_schema_version=OPPORTUNITY_REFERENCE_PROFILE_SCHEMA_VERSION,
    as_of_trade_date=TRADE_DATE,
    pullback_threshold_pct=0.7,
    momentum_rise_threshold_pct=0.8,
    momentum_amount_velocity_ratio=2.0,
    pullback_max_spread_ticks=3,
    momentum_max_spread_ticks=10,
  )
  state = OpportunityState.initial()
  result = None
  for sample in _pullback_samples():
    result = reduce_opportunity(state, sample, reference_profile=profile)
    state = result.state

  assert result is not None
  assert result.evaluation.data_health == DataHealth.INSUFFICIENT
  assert "REFERENCE_PROFILE_CAUSAL" in result.evaluation.pullback.blockers
  assert result.evaluation.pullback.score is None
  assert result.evaluation.opportunity_score is None
  assert result.candidate_created is None
  assert result.evaluation.reference_profile_version is None


def test_incompatible_reference_profile_schema_fails_closed():
  incompatible = replace(PROFILE, profile_schema_version=999)
  state, results = _reduce_all(
    _pullback_samples(),
    reference_profile=incompatible,
  )

  assert state.data_health == DataHealth.INSUFFICIENT
  assert results[-1].evaluation.data_health_reasons[0] == (
    "REFERENCE_PROFILE_SCHEMA_INCOMPATIBLE"
  )
  assert results[-1].evaluation.pullback.score is None
  assert results[-1].evaluation.selected_path == OpportunityPath.NONE
  assert results[-1].candidate_created is None


def test_quote_staleness_has_a_distinct_health_state_and_vetoes_candidate():
  state, _ = _reduce_all(_pullback_samples()[:3])
  result = reduce_opportunity(
    state,
    _pullback_samples()[3],
    gate_context=OpportunityGateContext(quote_stale=True),
    reference_profile=PROFILE,
  )

  assert result.evaluation.data_health == DataHealth.STALE
  assert "QUOTE_FRESH" in result.evaluation.pullback.blockers
  assert result.candidate_created is None


def test_policy_quote_age_is_authoritative_at_the_domain_boundary():
  state, _ = _reduce_all(_pullback_samples()[:3])
  fresh_at_boundary = replace(
    _pullback_samples()[3],
    received_at_ms=_pullback_samples()[3].source_time_ms + 5_000,
  )
  policy = OpportunityPolicy(max_quote_age_ms=5_000)
  fresh = reduce_opportunity(
    state,
    fresh_at_boundary,
    policy=policy,
    reference_profile=PROFILE,
  )
  stale = reduce_opportunity(
    state,
    replace(fresh_at_boundary, received_at_ms=fresh_at_boundary.source_time_ms + 5_001),
    policy=policy,
    reference_profile=PROFILE,
  )

  assert fresh.evaluation.data_health != DataHealth.STALE
  assert stale.evaluation.data_health == DataHealth.STALE
  assert "QUOTE_FRESH" in stale.evaluation.pullback.blockers


def test_invalid_receive_time_never_moves_evaluation_before_source_time():
  sample = replace(
    _pullback_samples()[3],
    received_at_ms=_pullback_samples()[3].source_time_ms - 1,
  )
  state, _ = _reduce_all(_pullback_samples()[:3])

  result = reduce_opportunity(
    state,
    sample,
    reference_profile=PROFILE,
  )

  assert result.evaluation.evaluated_at_ms == sample.source_time_ms
  assert result.evaluation.evaluated_at_ms >= result.evaluation.source_time_ms
  assert result.evaluation.data_health == DataHealth.DEGRADED
  assert "INVALID_RECEIVE_TIME" in result.evaluation.data_health_reasons


def test_policy_session_window_and_ttl_close_boundary_veto_candidate():
  state, _ = _reduce_all(_pullback_samples()[:3])
  sample = _pullback_samples()[3]
  policy = OpportunityPolicy()
  at_latest_safe_second = OpportunityGateContext(
    continuous_session=True,
    session_code="CONTINUOUS_PM",
    local_second_of_day=14 * 3600 + 56 * 60 + 30,
  )
  too_late = OpportunityGateContext(
    continuous_session=True,
    session_code="CONTINUOUS_PM",
    local_second_of_day=14 * 3600 + 56 * 60 + 31,
  )

  allowed = reduce_opportunity(
    state,
    sample,
    gate_context=at_latest_safe_second,
    policy=policy,
    reference_profile=PROFILE,
  )
  blocked = reduce_opportunity(
    state,
    sample,
    gate_context=too_late,
    policy=policy,
    reference_profile=PROFILE,
  )

  allowed_gate = next(
    item
    for item in allowed.evaluation.pullback.hard_gates
    if item.code == "TRADING_SESSION"
  )
  blocked_gate = next(
    item
    for item in blocked.evaluation.pullback.hard_gates
    if item.code == "TRADING_SESSION"
  )
  assert allowed_gate.passed is True
  assert blocked_gate.passed is False
  assert "TRADING_SESSION" in blocked.evaluation.pullback.blockers


def test_confirmation_dwell_does_not_cross_the_midday_session_boundary():
  morning_start_seconds = 11 * 3600 + 29 * 60
  policy = OpportunityPolicy(
    pullback_lookback_seconds=7_200,
    sparse_degraded_gap_seconds=0,
  )
  state = OpportunityState.initial()
  for raw in _pullback_samples()[:4]:
    shifted = replace(
      raw,
      source_time_ms=raw.source_time_ms + morning_start_seconds * 1000,
    )
    state = reduce_opportunity(
      state,
      shifted,
      gate_context=OpportunityGateContext(
        session_code="CONTINUOUS_AM",
        local_second_of_day=shifted.source_time_ms // 1000,
      ),
      policy=policy,
      reference_profile=PROFILE,
    ).state
  assert state.pullback.confirmation_ticks == 1

  afternoon = replace(
    _pullback_samples()[4],
    source_time_ms=13 * 3600 * 1000,
  )
  result = reduce_opportunity(
    state,
    afternoon,
    gate_context=OpportunityGateContext(
      session_code="CONTINUOUS_PM",
      local_second_of_day=13 * 3600,
    ),
    policy=policy,
    reference_profile=PROFILE,
  )

  assert result.candidate_created is None
  assert result.state.pullback.confirmation_ticks == 1


def test_stale_health_dominates_missing_profile_but_preserves_both_reasons():
  result = reduce_opportunity(
    OpportunityState.initial(),
    _sample(0, 10.0, 0, amount=100_000, volume=10_000),
    gate_context=OpportunityGateContext(quote_stale=True),
  )

  assert result.evaluation.data_health == DataHealth.STALE
  assert result.evaluation.data_health_reasons[:2] == (
    "QUOTE_STALE",
    "REFERENCE_PROFILE_MISSING",
  )
  assert result.evaluation.opportunity_score is None
  assert result.candidate_created is None


def test_score_is_finite_bounded_and_components_are_explainable():
  _, results = _reduce_all(_momentum_samples())
  for result in results:
    for path in (result.evaluation.pullback, result.evaluation.momentum):
      if path.score is None:
        assert path.components == ()
        continue
      assert 0 <= path.score <= 100
      assert all(component.name for component in path.components)
      assert all(
        component.contribution == pytest.approx(component.contribution)
        for component in path.components
      )


def test_degraded_high_score_never_bypasses_data_ready_gate():
  samples = [
    _sample(0, 100.0, 0, amount=1_000_000, volume=10_000),
    _sample(120, 99.0, 1, amount=1_050_000, volume=10_500),
    _sample(140, 99.30, 2, amount=1_100_000, volume=11_000),
    _sample(142, 99.32, 3, amount=1_120_000, volume=11_200),
  ]
  state, results = _reduce_all(samples)
  final = results[-1]

  assert final.evaluation.data_health == DataHealth.DEGRADED
  assert final.evaluation.pullback.score is not None
  assert final.evaluation.pullback.score >= OpportunityPolicy().candidate_score
  assert "DATA_READY" in final.evaluation.pullback.blockers
  assert final.candidate_created is None
  assert state.candidate is None


def test_state_window_is_bounded_for_an_arbitrarily_long_same_generation_stream():
  policy = OpportunityPolicy(max_samples=10)
  state = OpportunityState.initial()

  for index in range(100):
    result = reduce_opportunity(
      state,
      _sample(
        index,
        10.0 + index * 0.001,
        index,
        amount=100_000 + index * 1_000,
        volume=10_000 + index * 100,
      ),
      policy=policy,
      reference_profile=PROFILE,
    )
    state = result.state

  assert len(state.samples) == policy.max_samples
  assert state.samples[0].source_time_ms == 90_000
  assert state.samples[-1].source_time_ms == 99_000


def test_trade_date_change_starts_a_new_causal_state_without_a_compatibility_path():
  state, _ = _reduce_all(_pullback_samples()[:3])
  next_day = reduce_opportunity(
    state,
    _sample(
      0,
      101.0,
      0,
      amount=100_000,
      volume=1_000,
      trade_date="2026-08-24",
    ),
    reference_profile=replace(PROFILE, as_of_trade_date=TRADE_DATE),
  )

  assert next_day.state.trade_date == "2026-08-24"
  assert len(next_day.state.samples) == 1
  assert next_day.state.pullback.phase == PullbackPhase.OBSERVING
  assert next_day.state.candidate is None
