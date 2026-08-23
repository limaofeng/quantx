from __future__ import annotations

from quantx_domain.trading.t_trade_opportunity_engine import (
  OpportunityPath,
  OpportunitySample,
)
from quantx_domain.trading.t_trade_phase_one_baseline import (
  PHASE_ONE_BASELINE_VERSION,
  PhaseOneBaselinePolicy,
  PhaseOneBaselineState,
  reduce_phase_one_baseline,
)


def _sample(
  source_time_ms: int,
  price: float,
  *,
  ordinal: int,
  generation: str = "1",
  amount: float | None = None,
  pvolume: float | None = None,
  bid: float | None = None,
  ask: float | None = None,
) -> OpportunitySample:
  return OpportunitySample(
    instrument_code="600000.SH",
    trade_date="2026-08-21",
    source_time_ms=source_time_ms,
    tick_ordinal=ordinal,
    price=price,
    continuity_generation=generation,
    received_at_ms=source_time_ms,
    bid_price=bid,
    ask_price=ask,
    bid_volume=1000,
    ask_volume=1000,
    cumulative_amount=amount,
    cumulative_volume=pvolume,
    price_tick=0.01,
  )


def _advance(
  state: PhaseOneBaselineState | None,
  sample: OpportunitySample,
):
  return reduce_phase_one_baseline(
    state,
    sample,
    continuous_session=True,
  )


def test_phase_one_pullback_rule_fires_once_on_false_to_true_edge() -> None:
  state = None
  for sample in (
    _sample(
      1_000,
      100.0,
      ordinal=1,
      amount=100_000,
      pvolume=1_000,
      bid=99.99,
      ask=100.0,
    ),
    _sample(
      2_000,
      99.0,
      ordinal=2,
      amount=109_900,
      pvolume=1_100,
      bid=98.99,
      ask=99.0,
    ),
  ):
    reduction = _advance(state, sample)
    state = reduction.state

  reduction = _advance(
    state,
    _sample(
      17_000,
      99.3,
      ordinal=3,
      amount=119_830,
      pvolume=1_200,
      bid=99.29,
      ask=99.3,
    ),
  )
  state = reduction.state
  evaluation = reduction.evaluation

  assert evaluation.baseline_version == PHASE_ONE_BASELINE_VERSION
  assert evaluation.selected_path is OpportunityPath.PULLBACK_REBOUND
  assert evaluation.raw_triggered is True
  assert evaluation.trigger_edge is True
  assert evaluation.top_blocker is None
  assert evaluation.metrics["pullback_pct"] == 1.0
  assert float(evaluation.metrics["rebound_pct"] or 0) > 0.2

  repeated_condition = _advance(
    state,
    _sample(
      18_000,
      99.31,
      ordinal=4,
      amount=129_761,
      pvolume=1_300,
      bid=99.30,
      ask=99.31,
    ),
  )
  assert repeated_condition.evaluation.raw_triggered is True
  assert repeated_condition.evaluation.trigger_edge is False


def test_phase_one_momentum_rule_uses_strictly_prior_300_second_baseline() -> None:
  state = None
  samples = (
    _sample(
      1_000,
      98.0,
      ordinal=1,
      amount=980,
      pvolume=10,
      bid=97.99,
      ask=98.0,
    ),
    _sample(
      241_000,
      100.0,
      ordinal=2,
      amount=24_500,
      pvolume=250,
      bid=99.99,
      ask=100.0,
    ),
    _sample(
      256_000,
      100.8,
      ordinal=3,
      amount=27_500,
      pvolume=280,
      bid=100.79,
      ask=100.8,
    ),
  )
  for sample in samples:
    reduction = _advance(state, sample)
    state = reduction.state

  evaluation = reduction.evaluation
  assert evaluation.selected_path is OpportunityPath.MOMENTUM_ACCELERATION
  assert evaluation.trigger_edge is True
  assert evaluation.metrics["momentum_baseline_coverage_seconds"] == 240.0
  assert float(evaluation.metrics["momentum_amount_velocity_ratio"] or 0) >= 2.0
  assert 2.0 <= float(evaluation.metrics["momentum_vwap_premium_pct"] or 0) <= 3.5


def test_phase_one_baseline_treats_missing_values_as_unknown_not_zero() -> None:
  state = None
  for sample in (
    _sample(1_000, 100.0, ordinal=1),
    _sample(2_000, 99.0, ordinal=2),
    _sample(17_000, 99.3, ordinal=3),
  ):
    reduction = _advance(state, sample)
    state = reduction.state

  pullback = {item.code: item for item in reduction.evaluation.pullback_checks}
  momentum = {item.code: item for item in reduction.evaluation.momentum_checks}
  assert pullback["PULLBACK_BOOK_COMPLETE"].passed is False
  assert pullback["PULLBACK_SPREAD_AT_MOST_TICKS"].passed is None
  assert pullback["PULLBACK_NOT_ABOVE_VWAP_IF_AVAILABLE"].actual is None
  assert pullback["PULLBACK_NOT_ABOVE_VWAP_IF_AVAILABLE"].passed is True
  assert momentum["MOMENTUM_VWAP_AVAILABLE"].passed is False
  assert momentum["MOMENTUM_VWAP_PREMIUM_AT_LEAST"].passed is None


def test_phase_one_baseline_duplicate_and_out_of_order_are_idempotent() -> None:
  first = _advance(
    None,
    _sample(
      1_000,
      100.0,
      ordinal=1,
      amount=100_000,
      pvolume=1_000,
      bid=99.99,
      ask=100.0,
    ),
  )
  duplicate = _advance(first.state, first.state.samples[-1])
  assert duplicate.state == first.state
  assert duplicate.evaluation.duplicate is True
  assert duplicate.evaluation.trigger_edge is False

  out_of_order = _advance(
    first.state,
    _sample(
      999,
      100.0,
      ordinal=2,
      amount=99_000,
      pvolume=990,
      bid=99.99,
      ask=100.0,
    ),
  )
  assert out_of_order.state == first.state
  assert out_of_order.evaluation.ignored_reason == "OUT_OF_ORDER_SOURCE_IDENTITY"
  assert out_of_order.evaluation.trigger_edge is False


def test_phase_one_baseline_resets_on_explicit_continuity_change() -> None:
  first = _advance(
    None,
    _sample(
      1_000,
      100.0,
      ordinal=1,
      amount=100_000,
      pvolume=1_000,
      bid=99.99,
      ask=100.0,
    ),
  )
  changed = _advance(
    first.state,
    _sample(
      2_000,
      100.1,
      ordinal=2,
      generation="2",
      amount=110_010,
      pvolume=1_100,
      bid=100.09,
      ask=100.1,
    ),
  )
  assert len(changed.state.samples) == 1
  assert changed.state.continuity_generation == "2"
  assert changed.evaluation.ignored_reason == "CONTINUITY_CHANGED"
  assert changed.evaluation.trigger_edge is False


def test_phase_one_baseline_state_is_bounded_and_round_trips() -> None:
  policy = PhaseOneBaselinePolicy(max_samples=3)
  state = None
  for ordinal in range(1, 7):
    reduction = reduce_phase_one_baseline(
      state,
      _sample(
        ordinal * 1_000,
        100.0,
        ordinal=ordinal,
        amount=100_000 + ordinal * 1_000,
        pvolume=1_000 + ordinal * 10,
        bid=99.99,
        ask=100.0,
      ),
      continuous_session=True,
      policy=policy,
    )
    state = reduction.state
  assert state is not None
  assert len(state.samples) == 3
  assert PhaseOneBaselineState.from_dict(state.to_dict()) == state


def test_phase_one_baseline_never_fires_outside_continuous_session() -> None:
  reduction = reduce_phase_one_baseline(
    None,
    _sample(
      1_000,
      100.0,
      ordinal=1,
      amount=100_000,
      pvolume=1_000,
      bid=99.99,
      ask=100.0,
    ),
    continuous_session=False,
  )
  assert reduction.evaluation.raw_triggered is False
  assert reduction.evaluation.trigger_edge is False
  assert reduction.evaluation.top_blocker == "CONTINUOUS_SESSION"
