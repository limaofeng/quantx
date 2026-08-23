from __future__ import annotations

import pytest
from quantx_domain.trading.t_trade_candidate_outcome import (
  CandidateExecutionFill,
  CandidateOutcomeDefinition,
  CandidateOutcomeState,
  CandidateOutcomeStatus,
  CandidateOutcomeUnavailableReason,
  CandidatePriceObservation,
  apply_candidate_execution_fill,
  finalize_candidate_outcome,
  observe_candidate_outcome,
  start_candidate_outcome,
)


def _state(*, max_gap_ms: int = 120_000) -> CandidateOutcomeState:
  return start_candidate_outcome(
    CandidateOutcomeDefinition(
      candidate_id="candidate-1",
      candidate_fingerprint="a" * 64,
      strategy_run_id="run-1",
      instrument_code="600000.SH",
      source_time_ms=1_000_000,
      tick_ordinal=10,
      continuity_generation="7",
      reference_price=10.0,
      policy_version="policy-3",
      feature_schema_version="1",
      profile_version="profile-2",
      profile_fingerprint="b" * 64,
      horizons_seconds=(60, 120),
      max_observation_gap_ms=max_gap_ms,
    )
  )


def _observe(
  state: CandidateOutcomeState,
  *,
  elapsed_ms: int,
  ordinal: int,
  price: float,
  generation: str = "7",
  halted: bool = False,
) -> CandidateOutcomeState:
  return observe_candidate_outcome(
    state,
    CandidatePriceObservation(
      source_time_ms=1_000_000 + elapsed_ms,
      tick_ordinal=ordinal,
      continuity_generation=generation,
      price=price,
      trading_halted=halted,
    ),
  )


def test_matures_fixed_horizons_from_strictly_later_ticks() -> None:
  state = _state()

  _observe(state, elapsed_ms=0, ordinal=10, price=99.0)
  assert state.sample_count == 0

  _observe(state, elapsed_ms=30_000, ordinal=11, price=10.5)
  _observe(state, elapsed_ms=60_000, ordinal=12, price=10.2)
  assert state.horizons[0].return_pct == pytest.approx(2.0)
  assert state.horizons[0].mfe_pct == pytest.approx(5.0)
  assert state.horizons[0].mae_pct == pytest.approx(0.0)

  _observe(state, elapsed_ms=90_000, ordinal=13, price=9.8)
  _observe(state, elapsed_ms=120_000, ordinal=14, price=10.1)

  assert state.status is CandidateOutcomeStatus.MATURED
  assert state.available is True
  assert state.horizons[1].return_pct == pytest.approx(1.0)
  assert state.horizons[1].mfe_pct == pytest.approx(5.0)
  assert state.horizons[1].mae_pct == pytest.approx(-2.0)


def test_matured_horizon_is_not_rewritten_by_later_prices() -> None:
  state = _state()
  _observe(state, elapsed_ms=60_000, ordinal=11, price=10.2)
  first = (
    state.horizons[0].to_dict()
    if hasattr(state.horizons[0], "to_dict")
    else vars(state.horizons[0]).copy()
  )
  _observe(state, elapsed_ms=120_000, ordinal=12, price=20.0)
  assert vars(state.horizons[0]) == first


@pytest.mark.parametrize(
  ("observations", "reason"),
  [
    (
      [(1_000, 11, 10.1, "8", False)],
      CandidateOutcomeUnavailableReason.CONTINUITY_CHANGED,
    ),
    (
      [(1_000, 11, 10.1, "7", True)],
      CandidateOutcomeUnavailableReason.TRADING_HALTED,
    ),
    (
      [(121_000, 11, 10.1, "7", False)],
      CandidateOutcomeUnavailableReason.OBSERVATION_GAP,
    ),
    (
      [(1_000, 12, 10.1, "7", False), (500, 11, 10.0, "7", False)],
      CandidateOutcomeUnavailableReason.OUT_OF_ORDER,
    ),
  ],
)
def test_bad_market_continuity_fails_closed_without_zero_metrics(
  observations: list[tuple[int, int, float, str, bool]],
  reason: CandidateOutcomeUnavailableReason,
) -> None:
  state = _state()
  for elapsed_ms, ordinal, price, generation, halted in observations:
    _observe(
      state,
      elapsed_ms=elapsed_ms,
      ordinal=ordinal,
      price=price,
      generation=generation,
      halted=halted,
    )

  assert state.status is CandidateOutcomeStatus.UNAVAILABLE
  assert state.unavailable_reason is reason
  assert state.available is False
  assert state.horizons[-1].return_pct is None
  assert state.horizons[-1].mfe_pct is None
  assert state.horizons[-1].mae_pct is None


def test_duplicate_tick_and_fill_are_idempotent() -> None:
  state = _state()
  _observe(state, elapsed_ms=1_000, ordinal=11, price=10.1)
  _observe(state, elapsed_ms=1_000, ordinal=11, price=10.1)
  assert state.sample_count == 1

  fill = CandidateExecutionFill(
    fill_id="fill-1",
    role="ENTRY",
    source_time_ms=1_001_000,
    price=10.1,
    volume=100,
    fee=5.0,
  )
  apply_candidate_execution_fill(state, fill)
  apply_candidate_execution_fill(state, fill)
  assert state.execution.entry_volume == 100
  assert state.execution.entry_fee == pytest.approx(5.0)
  with pytest.raises(ValueError, match="成交事实不一致"):
    apply_candidate_execution_fill(
      state,
      CandidateExecutionFill("fill-1", "ENTRY", 1_001_000, 10.2, 100, 5.0),
    )


def test_actual_fill_prices_and_fees_drive_realized_net_pnl() -> None:
  state = _state()
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("e1", "ENTRY", 1_001_000, 10.0, 100, 2.0, True, 200),
  )
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("e2", "ENTRY", 1_002_000, 10.2, 100, 3.0, True, 200),
  )
  _observe(state, elapsed_ms=3_000, ordinal=11, price=10.4)
  apply_candidate_execution_fill(
    state, CandidateExecutionFill("x1", "EXIT", 1_004_000, 10.5, 200, 4.0)
  )

  assert state.execution.entry_price == pytest.approx(10.1)
  assert state.execution.closed is True
  assert state.execution.realized_net_pnl == pytest.approx(71.0)


def test_missing_authoritative_fee_never_becomes_zero() -> None:
  state = _state()
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("e1", "ENTRY", 1_001_000, 10.0, 100, None),
  )
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("x1", "EXIT", 1_002_000, 10.5, 100, 4.0),
  )
  assert state.execution.entry_fee is None
  assert state.execution.realized_net_pnl is None


def test_fill_must_be_causally_bound_to_candidate_lifecycle() -> None:
  state = _state()
  with pytest.raises(ValueError, match="严格发生"):
    apply_candidate_execution_fill(
      state,
      CandidateExecutionFill("e0", "ENTRY", 1_000_000, 10.0, 100, 1.0),
    )
  with pytest.raises(ValueError, match="不得早于"):
    apply_candidate_execution_fill(
      state,
      CandidateExecutionFill("x0", "EXIT", 1_001_000, 10.1, 100, 1.0),
    )


def test_post_fill_windows_arm_only_after_weighted_entry_is_complete() -> None:
  state = _state()
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("e1", "ENTRY", 1_001_000, 10.0, 100, 2.0, True, 200),
  )
  _observe(state, elapsed_ms=1_500, ordinal=11, price=20.0)
  assert state.post_fill.horizons == []

  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("e2", "ENTRY", 1_002_000, 10.2, 100, 3.0, True, 200),
  )
  assert state.post_fill.reference_price == pytest.approx(10.1)
  assert state.post_fill.reference_volume == 200
  assert state.post_fill.armed_at_ms == 1_002_000

  _observe(state, elapsed_ms=62_000, ordinal=12, price=10.2)
  _observe(state, elapsed_ms=122_000, ordinal=13, price=9.9)

  assert state.post_fill.available is True
  assert state.post_fill.net_available is False
  assert state.post_fill.horizons[0].mfe_pct == pytest.approx((10.2 / 10.1 - 1) * 100)
  assert state.post_fill.horizons[0].mae_pct == pytest.approx(0.0)
  assert state.post_fill.horizons[0].net_return_pct is None
  assert state.post_fill.horizons[1].net_mfe_pct is None
  assert state.post_fill.horizons[1].net_mae_pct is None


def test_retry_completion_arms_at_latest_applied_partial_fill_time() -> None:
  state = _state()
  first = CandidateExecutionFill("e1", "ENTRY", 1_001_000, 10.0, 100, 2.0, False, 200)
  second = CandidateExecutionFill("e2", "ENTRY", 1_002_000, 10.2, 100, 3.0, False, 200)
  apply_candidate_execution_fill(state, first)
  apply_candidate_execution_fill(state, second)
  assert state.post_fill.horizons == []

  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("e1", "ENTRY", 1_001_000, 10.0, 100, 2.0, True, 200),
  )

  assert state.execution.entry_volume == 200
  assert state.post_fill.armed_at_ms == 1_002_000
  assert state.post_fill.reference_price == pytest.approx(10.1)


def test_post_fill_net_metrics_fail_closed_when_any_fee_is_unknown() -> None:
  state = _state()
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("e1", "ENTRY", 1_001_000, 10.0, 100, None, True, 100),
  )
  _observe(state, elapsed_ms=61_000, ordinal=11, price=10.2)
  _observe(state, elapsed_ms=121_000, ordinal=12, price=10.3)

  assert state.post_fill.available is True
  assert state.post_fill.net_available is False
  assert state.post_fill.horizons[0].return_pct == pytest.approx(2.0)
  assert state.post_fill.horizons[0].net_return_pct is None
  assert state.post_fill.horizons[0].net_mfe_pct is None
  assert state.post_fill.horizons[0].net_mae_pct is None


def test_unknown_exit_fee_keeps_net_marks_unavailable() -> None:
  state = _state()
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("e1", "ENTRY", 1_001_000, 10.0, 100, 2.0, True, 100),
  )
  _observe(state, elapsed_ms=61_000, ordinal=11, price=10.2)
  _observe(state, elapsed_ms=121_000, ordinal=12, price=10.3)
  assert state.post_fill.net_available is False

  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("x1", "EXIT", 1_122_000, 10.3, 100, None),
  )
  assert state.execution.realized_net_pnl is None
  assert state.post_fill.net_available is False
  assert all(horizon.net_return_pct is None for horizon in state.post_fill.horizons)


def test_matured_windows_gain_net_metrics_only_after_authoritative_exit_fee() -> None:
  state = _state()
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("e1", "ENTRY", 1_001_000, 10.0, 100, 2.0, True, 100),
  )
  _observe(state, elapsed_ms=61_000, ordinal=11, price=10.2)
  _observe(state, elapsed_ms=121_000, ordinal=12, price=10.3)
  assert state.post_fill.available is True
  assert state.post_fill.net_available is False

  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("x1", "EXIT", 1_122_000, 10.3, 100, 4.0),
  )

  fee_pct = 6.0 / 1_000.0 * 100
  assert state.post_fill.net_available is True
  assert state.post_fill.horizons[0].net_return_pct == pytest.approx(2.0 - fee_pct)
  assert state.post_fill.horizons[1].net_return_pct == pytest.approx(3.0 - fee_pct)
  assert state.post_fill.horizons[1].net_mfe_pct == pytest.approx(3.0 - fee_pct)


def test_partial_entry_finalizes_as_unavailable_instead_of_freezing_early() -> None:
  state = _state()
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("e1", "ENTRY", 1_001_000, 10.0, 100, 2.0),
  )
  finalize_candidate_outcome(state, finalized_at_ms=1_010_000)
  assert state.post_fill.available is False
  assert (
    state.post_fill.unavailable_reason
    is CandidateOutcomeUnavailableReason.ENTRY_INCOMPLETE
  )


@pytest.mark.parametrize(
  ("elapsed_ms", "generation", "halted", "reason"),
  [
    (2_000, "8", False, CandidateOutcomeUnavailableReason.CONTINUITY_CHANGED),
    (2_000, "7", True, CandidateOutcomeUnavailableReason.TRADING_HALTED),
    (122_000, "7", False, CandidateOutcomeUnavailableReason.OBSERVATION_GAP),
  ],
)
def test_post_fill_windows_share_continuity_gap_and_halt_fail_closed_rules(
  elapsed_ms: int,
  generation: str,
  halted: bool,
  reason: CandidateOutcomeUnavailableReason,
) -> None:
  state = _state()
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill("e1", "ENTRY", 1_001_000, 10.0, 100, 2.0, True, 100),
  )
  _observe(
    state,
    elapsed_ms=elapsed_ms,
    ordinal=11,
    price=10.1,
    generation=generation,
    halted=halted,
  )
  assert state.post_fill.available is False
  assert state.post_fill.unavailable_reason is reason
  assert state.post_fill.horizons[-1].return_pct is None
  assert state.post_fill.horizons[-1].net_return_pct is None


def test_incomplete_window_finalizes_unavailable_and_round_trips() -> None:
  state = _state()
  _observe(state, elapsed_ms=1_000, ordinal=11, price=10.1)
  finalize_candidate_outcome(state, finalized_at_ms=1_030_000)

  restored = CandidateOutcomeState.from_dict(state.to_dict())
  assert restored.to_dict() == state.to_dict()
  assert restored.status is CandidateOutcomeStatus.UNAVAILABLE
  assert (
    restored.unavailable_reason is CandidateOutcomeUnavailableReason.WINDOW_INCOMPLETE
  )
