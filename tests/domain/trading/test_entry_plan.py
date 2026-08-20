from dataclasses import replace

import pytest
from quantx_domain.trading.entry_plan import (
  CausalPriceObservation,
  EntryAuthorizationMode,
  EntryBaselineSnapshot,
  EntryCompletionPolicy,
  EntryEnvironment,
  EntryEvaluationContext,
  EntryExecutionPolicy,
  EntryGapCalculator,
  EntryPacingPolicy,
  EntryPlanStatus,
  EntryRuleSpec,
  EntryRuleType,
  EntryTargetMode,
  EntryTargetPolicy,
  ManagedEntryPlanConfig,
  ManagedEntryPlanEvaluator,
  ManagedEntryPlanState,
  PendingBuyExposure,
)


def baseline() -> EntryBaselineSnapshot:
  return EntryBaselineSnapshot(
    position_volume=0,
    market_value_cny=0,
    total_asset_cny=100_000,
    reference_price=10,
    account_snapshot_version="account-v1",
  )


def target(
  mode: EntryTargetMode = EntryTargetMode.INCREMENTAL_AMOUNT_CNY,
) -> EntryTargetPolicy:
  values = {
    "target_position_pct": None,
    "incremental_amount_cny": None,
    "additional_volume": None,
  }
  if mode == EntryTargetMode.TARGET_POSITION_PCT:
    values["target_position_pct"] = 0.5
  elif mode == EntryTargetMode.ADDITIONAL_VOLUME:
    values["additional_volume"] = 3_000
  else:
    values["incremental_amount_cny"] = 30_000
  return EntryTargetPolicy(
    mode=mode,
    max_total_amount_cny=50_000,
    max_position_pct=0.6,
    baseline_snapshot=baseline(),
    **values,
  )


def manual_rule(**parameters) -> EntryRuleSpec:
  return EntryRuleSpec(
    rule_id="manual",
    rule_type=EntryRuleType.MANUAL_TRIGGER,
    priority=100,
    parameters=parameters,
  )


def config(
  *,
  target_policy=None,
  rules=None,
  single_cap=10_000,
  daily_cap=20_000,
) -> ManagedEntryPlanConfig:
  return ManagedEntryPlanConfig(
    template_version=1,
    config_version=3,
    instrument_code="600000.SH",
    bucket="core",
    target_policy=target_policy or target(),
    trigger_rules=tuple(rules or [manual_rule()]),
    pacing_policy=EntryPacingPolicy(
      tranche_count=3,
      max_single_intent_amount_cny=single_cap,
      max_daily_filled_amount_cny=daily_cap,
      max_orders_per_day=3,
      min_interval_seconds=5,
      cooldown_after_reject_seconds=30,
    ),
    execution_policy=EntryExecutionPolicy(
      environment=EntryEnvironment.PAPER,
      authorization_mode=EntryAuthorizationMode.AUTO,
      max_price_deviation_bps=50,
    ),
    completion_policy=EntryCompletionPolicy(max_buy_price=15),
  )


def context(**changes) -> EntryEvaluationContext:
  values = dict(
    plan_id="run-1",
    decision_time_ms=1_000_000,
    trade_date="2026-08-20",
    instrument_code="600000.SH",
    executable_price=10,
    total_equity_cny=100_000,
    current_position_volume=0,
    current_market_value_cny=0,
    data_quality="OK",
    manual_trigger_rule_id="manual",
  )
  values.update(changes)
  return EntryEvaluationContext(**values)


def test_target_policy_rejects_multiple_or_mismatched_target_fields():
  with pytest.raises(ValueError, match="exactly one"):
    EntryTargetPolicy(
      mode=EntryTargetMode.TARGET_POSITION_PCT,
      target_position_pct=0.4,
      incremental_amount_cny=10_000,
      max_total_amount_cny=30_000,
      max_position_pct=0.5,
      baseline_snapshot=baseline(),
    )

  with pytest.raises(ValueError, match="does not match"):
    EntryTargetPolicy(
      mode=EntryTargetMode.ADDITIONAL_VOLUME,
      incremental_amount_cny=10_000,
      max_total_amount_cny=30_000,
      max_position_pct=0.5,
      baseline_snapshot=baseline(),
    )


def test_locked_core_is_not_a_valid_entry_bucket():
  with pytest.raises(ValueError, match="core or swing"):
    replace(config(), bucket="locked_core")


def test_plan_cash_buffer_is_a_bounded_persisted_risk_cap():
  pacing = replace(config().pacing_policy, cash_buffer_pct=0.25)
  restored = EntryPacingPolicy.from_dict(pacing.__dict__)
  assert restored.cash_buffer_pct == 0.25
  with pytest.raises(ValueError, match="cash_buffer_pct"):
    replace(pacing, cash_buffer_pct=1.0)


def test_config_and_runtime_state_round_trip_without_account_fields():
  original = config()
  restored = ManagedEntryPlanConfig.from_dict(original.to_dict())
  state = ManagedEntryPlanState(
    phase=EntryPlanStatus.ACCUMULATING,
    completed_activation_ids={"ladder:first"},
    rule_state={"trend": {"phase": "WAITING_PULLBACK"}},
  )

  assert restored == original
  assert ManagedEntryPlanState.from_dict(state.to_dict()).to_dict() == state.to_dict()
  assert "cash" not in state.to_dict()
  assert "total_volume" not in state.to_dict()


def test_position_target_gap_deducts_holdings_and_all_working_buys():
  gap = EntryGapCalculator.calculate(
    target(EntryTargetMode.TARGET_POSITION_PCT),
    context(
      current_position_volume=1_000,
      current_market_value_cny=10_000,
      pending_buys=(
        PendingBuyExposure(remaining_volume=500, protected_limit_price=10),
        PendingBuyExposure(remaining_volume=200, protected_limit_price=10.5),
      ),
    ),
  )

  assert gap.pending_amount_cny == pytest.approx(7_100)
  assert gap.remaining_amount_cny == pytest.approx(32_900)
  assert gap.target_reached is False


def test_additional_volume_gap_is_incremental_and_respects_amount_caps():
  gap = EntryGapCalculator.calculate(
    target(EntryTargetMode.ADDITIONAL_VOLUME),
    context(
      plan_filled_volume=1_000,
      plan_filled_amount_cny=10_000,
      pending_buys=(PendingBuyExposure(500, 10),),
    ),
  )

  assert gap.remaining_volume == 1_500
  assert gap.remaining_amount_cny == 15_000


def test_working_buy_can_close_the_gap_but_cannot_fake_target_completion():
  gap = EntryGapCalculator.calculate(
    target(EntryTargetMode.TARGET_POSITION_PCT),
    context(
      current_market_value_cny=40_000,
      current_position_volume=4_000,
      pending_buys=(PendingBuyExposure(1_000, 10),),
    ),
  )

  assert gap.remaining_amount_cny == 0
  assert gap.target_reached is False

  result = ManagedEntryPlanEvaluator().evaluate(
    config(target_policy=target(EntryTargetMode.TARGET_POSITION_PCT)),
    ManagedEntryPlanState(),
    context(
      current_market_value_cny=40_000,
      current_position_volume=4_000,
      pending_buys=(PendingBuyExposure(1_000, 10),),
    ),
  )
  assert result.reason == "ENTRY_CAPACITY_ZERO"
  assert result.state.phase == EntryPlanStatus.ARMED


def test_evaluator_applies_all_tranche_hard_caps_before_creating_decision():
  result = ManagedEntryPlanEvaluator().evaluate(
    config(single_cap=9_000, daily_cap=12_000),
    ManagedEntryPlanState(),
    context(
      daily_filled_amount_cny=5_000,
      risk_max_buy_amount_cny=8_000,
      liquidity_cap_cny=6_000,
    ),
  )

  assert result.decision is not None
  assert result.decision.target_amount_cny == 6_000
  assert result.decision.target_volume is None
  assert result.state.phase == EntryPlanStatus.ENTRY_PENDING


def test_pending_intent_is_a_hard_barrier_for_every_rule():
  evaluator = ManagedEntryPlanEvaluator()
  first = evaluator.evaluate(config(), ManagedEntryPlanState(), context())
  second = evaluator.evaluate(
    config(), first.state, replace(context(), decision_time_ms=2_000_000)
  )

  assert first.decision is not None
  assert second.decision is None
  assert second.reason == "ENTRY_PENDING_EXISTS"


def test_price_ladder_selects_only_one_level_when_price_gaps_below_many_levels():
  rule = EntryRuleSpec(
    rule_id="ladder",
    rule_type=EntryRuleType.PRICE_LADDER,
    parameters={
      "levels": [
        {
          "level_id": "near",
          "trigger_price": 10,
          "tranche_amount_cny": 2_000,
          "priority": 10,
        },
        {
          "level_id": "deep",
          "trigger_price": 9,
          "tranche_amount_cny": 3_000,
          "priority": 20,
        },
      ]
    },
  )
  result = ManagedEntryPlanEvaluator().evaluate(
    config(rules=[rule]),
    ManagedEntryPlanState(),
    context(executable_price=8.5, manual_trigger_rule_id=None),
  )

  assert result.decision is not None
  assert result.decision.target_amount_cny == 3_000
  assert result.decision.metrics["level_id"] == "deep"


def test_price_ladder_level_with_a_real_fill_is_not_rearmed():
  rule = EntryRuleSpec(
    rule_id="ladder",
    rule_type=EntryRuleType.PRICE_LADDER,
    parameters={
      "levels": [
        {
          "level_id": "first",
          "trigger_price": 10,
          "tranche_amount_cny": 3_000,
        }
      ]
    },
  )
  cfg = config(rules=[rule])
  evaluator = ManagedEntryPlanEvaluator()
  first = evaluator.evaluate(
    cfg,
    ManagedEntryPlanState(),
    context(executable_price=9.9, manual_trigger_rule_id=None),
  )
  assert first.state.apply_trade_fill(
    trade_key="ladder-trade",
    volume=100,
    price=9.9,
    trade_date="2026-08-20",
    timestamp_ms=1_000_100,
  )
  first.state.apply_order_terminal(
    status="CANCELLED",
    timestamp_ms=1_000_200,
    cooldown_after_reject_seconds=0,
    expected_filled_volume=100,
  )

  replay = evaluator.evaluate(
    cfg,
    first.state,
    context(
      decision_time_ms=2_000_000,
      executable_price=9.8,
      manual_trigger_rule_id=None,
      plan_filled_amount_cny=990,
      plan_filled_volume=100,
    ),
  )

  assert replay.decision is None
  assert replay.reason == "ENTRY_RULES_NOT_MATCHED"


def test_manual_rule_requires_the_matching_persisted_command():
  evaluator = ManagedEntryPlanEvaluator()
  waiting = evaluator.evaluate(
    config(), ManagedEntryPlanState(), context(manual_trigger_rule_id=None)
  )
  triggered = evaluator.evaluate(
    config(), ManagedEntryPlanState(), context(manual_trigger_rule_id="manual")
  )

  assert waiting.reason == "ENTRY_RULES_NOT_MATCHED"
  assert triggered.decision is not None
  assert triggered.decision.reason == "ENTRY_MANUAL_TRIGGER_CONFIRMED"


def test_trend_pullback_rule_uses_a_causal_three_stage_state_machine():
  rule = EntryRuleSpec(
    rule_id="trend",
    rule_type=EntryRuleType.TREND_PULLBACK_CONFIRMATION,
    parameters={
      "fast_ema_period": 2,
      "slow_ema_period": 3,
      "pullback_pct": 1.0,
      "rebound_pct": 0.4,
      "confirm_observations": 1,
      "tranche_amount_cny": 4_000,
    },
  )
  cfg = config(rules=[rule])
  daily = tuple(
    CausalPriceObservation(index * 1_000, price)
    for index, price in enumerate([8.0, 8.5, 9.0, 9.5, 10.0], start=1)
  )
  evaluator = ManagedEntryPlanEvaluator()
  state = ManagedEntryPlanState()

  peak = evaluator.evaluate(
    cfg,
    state,
    context(
      decision_time_ms=10_000,
      executable_price=10,
      daily_observations=daily,
      manual_trigger_rule_id=None,
    ),
  )
  pullback = evaluator.evaluate(
    cfg,
    peak.state,
    context(
      decision_time_ms=11_000,
      executable_price=9.8,
      daily_observations=daily,
      manual_trigger_rule_id=None,
    ),
  )
  pullback_phase = pullback.state.rule_state["trend"]["phase"]
  rebound = evaluator.evaluate(
    cfg,
    pullback.state,
    context(
      decision_time_ms=12_000,
      executable_price=9.85,
      daily_observations=daily,
      manual_trigger_rule_id=None,
    ),
  )

  assert peak.decision is None
  assert pullback.decision is None
  assert pullback_phase == "WAITING_REBOUND"
  assert rebound.decision is not None
  assert rebound.decision.target_amount_cny == 4_000


def test_future_or_non_monotonic_observations_fail_closed():
  future = (CausalPriceObservation(1_000_001, 10),)
  result = ManagedEntryPlanEvaluator().evaluate(
    config(),
    ManagedEntryPlanState(),
    context(daily_observations=future),
  )

  assert result.decision is None
  assert result.reason == "ENTRY_FUTURE_DATA_REJECTED"


def test_terminal_order_report_before_trade_does_not_release_pending():
  state = (
    ManagedEntryPlanEvaluator()
    .evaluate(config(), ManagedEntryPlanState(), context())
    .state
  )

  state.apply_order_terminal(
    status="FILLED",
    timestamp_ms=1_100_000,
    cooldown_after_reject_seconds=30,
    expected_filled_volume=300,
  )
  assert state.has_pending is True
  assert state.order_terminal_seen is True

  assert (
    state.apply_trade_fill(
      trade_key="trade-1",
      volume=300,
      price=10,
      trade_date="2026-08-20",
      timestamp_ms=1_100_100,
    )
    is True
  )
  assert state.has_pending is False
  assert state.phase == EntryPlanStatus.ACCUMULATING


def test_cancel_report_waits_for_zero_fill_reconciliation_before_rearming():
  state = (
    ManagedEntryPlanEvaluator()
    .evaluate(config(), ManagedEntryPlanState(), context())
    .state
  )

  state.apply_order_terminal(
    status="CANCELLED",
    timestamp_ms=1_100_000,
    cooldown_after_reject_seconds=30,
    expected_filled_volume=0,
  )
  assert state.has_pending is True
  assert state.order_terminal_seen is True
  assert state.terminal_expected_filled_volume is None

  state.apply_order_terminal(
    status="RECONCILED_ZERO_FILL",
    timestamp_ms=1_100_100,
    cooldown_after_reject_seconds=30,
  )
  assert state.has_pending is False
  assert state.phase == EntryPlanStatus.ARMED
  assert state.retry_after_ms == 1_130_100


@pytest.mark.parametrize(
  "terminal_status",
  ["CANCELLED", "EXPIRED", "PARTIALLY_CANCELED"],
)
def test_non_reconciled_terminal_zero_fill_never_releases_pending(
  terminal_status: str,
):
  state = (
    ManagedEntryPlanEvaluator()
    .evaluate(config(), ManagedEntryPlanState(), context())
    .state
  )

  state.apply_order_terminal(
    status=terminal_status,
    timestamp_ms=1_100_000,
    cooldown_after_reject_seconds=30,
    expected_filled_volume=0,
  )

  assert state.has_pending is True
  assert state.order_terminal_seen is True
  assert state.terminal_expected_filled_volume is None

  assert state.apply_trade_fill(
    trade_key=f"late-{terminal_status}",
    volume=100,
    price=10,
    trade_date="2026-08-20",
    timestamp_ms=1_100_100,
  )
  assert state.has_pending is True
  assert state.pending_filled_volume == 100


def test_filled_zero_uses_requested_volume_as_late_execution_barrier():
  state = (
    ManagedEntryPlanEvaluator()
    .evaluate(config(), ManagedEntryPlanState(), context())
    .state
  )
  # Amount-target decisions are sized downstream. Model the durable sized
  # quantity used when a replayed terminal event no longer retains request.
  state.pending_requested_volume = 300
  expected = state.pending_requested_volume
  assert expected > 100

  state.apply_order_terminal(
    status="FILLED",
    timestamp_ms=1_100_000,
    cooldown_after_reject_seconds=30,
    expected_filled_volume=0,
  )

  assert state.has_pending is True
  assert state.terminal_expected_filled_volume == expected
  assert state.apply_trade_fill(
    trade_key="filled-zero-first-late",
    volume=100,
    price=10,
    trade_date="2026-08-20",
    timestamp_ms=1_100_100,
  )
  assert state.has_pending is True

  assert state.apply_trade_fill(
    trade_key="filled-zero-final-late",
    volume=expected - 100,
    price=10,
    trade_date="2026-08-20",
    timestamp_ms=1_100_200,
  )
  assert state.has_pending is False
  assert state.filled_volume == expected


def test_rejected_intent_with_no_broker_fill_settles_zero_immediately():
  state = (
    ManagedEntryPlanEvaluator()
    .evaluate(config(), ManagedEntryPlanState(), context())
    .state
  )

  state.apply_order_terminal(
    status="REJECTED",
    timestamp_ms=1_100_000,
    cooldown_after_reject_seconds=30,
  )

  assert state.has_pending is False
  assert state.phase == EntryPlanStatus.ARMED
  assert state.retry_after_ms == 1_130_000


def test_trade_fill_replay_is_idempotent():
  state = ManagedEntryPlanState()
  first = state.apply_trade_fill(
    trade_key="trade-1",
    volume=100,
    price=10,
    trade_date="2026-08-20",
    timestamp_ms=1_000,
    rule_id="manual",
  )
  replay = state.apply_trade_fill(
    trade_key="trade-1",
    volume=100,
    price=10,
    trade_date="2026-08-20",
    timestamp_ms=1_000,
    rule_id="manual",
  )

  assert first is True
  assert replay is False
  assert state.daily_filled_amounts_cny["2026-08-20"] == 1_000
  assert state.rule_filled_volumes["manual"] == 100
  assert state.filled_volume == 100
  assert state.filled_amount_cny == 1_000


def test_target_reached_never_creates_a_reverse_sell():
  result = ManagedEntryPlanEvaluator().evaluate(
    config(target_policy=target(EntryTargetMode.TARGET_POSITION_PCT)),
    ManagedEntryPlanState(),
    context(current_position_volume=5_100, current_market_value_cny=51_000),
  )

  assert result.decision is None
  assert result.reason == "ENTRY_TARGET_REACHED"
  assert result.state.phase == EntryPlanStatus.COMPLETED
