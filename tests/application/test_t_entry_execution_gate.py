"""Entry revalidation is causal, fail closed and never refreshes an intent TTL."""

from dataclasses import fields, replace

import pytest
from quantx_application.t_trade_v3.entry_execution_gate import (
  EntryExecutionBinding,
  EntryExecutionDecision,
  EntryExecutionGate,
  EntryExecutionGateInput,
  EntryExecutionGatePolicy,
  EntryExecutionGateResult,
  MarketDataCapabilityManifest,
)
from quantx_contracts import ExecutionEnvironment
from quantx_domain.trading.t_assistant_market_state import AcceptedTMarketTick
from quantx_domain.trading.t_trade_opportunity_engine import (
  OpportunityCandidate,
  OpportunityPath,
  OpportunitySample,
)


@pytest.fixture
def gate_input():
  candidate = OpportunityCandidate(
    candidate_id="candidate",
    fingerprint="fingerprint",
    episode_id="episode",
    path=OpportunityPath.PULLBACK_REBOUND,
    latched_at_ms=10_000,
    expires_at_ms=70_000,
    source_time_ms=10_000,
    tick_ordinal=1,
    price=10,
    score=80,
    policy_version="policy-v1",
    feature_schema_version=1,
    reference_profile_version="profile-v1",
    reference_profile_schema_version=1,
  )
  binding = EntryExecutionBinding(
    candidate_fingerprint="fingerprint",
    config_version_id="config-v1",
    config_snapshot_hash="config-hash",
    policy_version="policy-v1",
    gate_policy_version="entry-gate-v1",
    feature_schema_version=1,
    capability_version="source-v1",
  )
  return EntryExecutionGateInput(
    candidate=candidate,
    instrument_code="600000.SH",
    intent_id="intent",
    intent_active=True,
    candidate_valid=True,
    intent_created_at_ms=10_000,
    intent_expires_at_ms=60_000,
    evaluated_at_ms=12_000,
    execution_environment=ExecutionEnvironment.PAPER,
    frozen_binding=binding,
    current_binding=binding,
    policy=EntryExecutionGatePolicy(
      version="entry-gate-v1",
      quote_max_age_ms=3_000,
      max_price_deviation_bps=100,
      max_spread_bps=30,
      minimum_book_depth=10,
      minimum_book_imbalance=-0.5,
    ),
    capabilities=MarketDataCapabilityManifest(
      version="source-v1",
      required_fields=frozenset({"price", "bid_price", "ask_price"}),
      optional_fields=frozenset({"bid_volume", "ask_volume"}),
    ),
    stream_id="stream",
    continuity_generation="gen",
    candidate_accepted_sequence=1,
    candidate_ring_generation=1,
    latest_ring_generation=1,
    latest_accepted_sequence=2,
    latest_tick=AcceptedTMarketTick(
      stream_id="stream",
      accepted_sequence=2,
      received_at_ms=11_900,
      sample=OpportunitySample(
        instrument_code="600000.SH",
        trade_date="2026-09-07",
        source_time_ms=11_800,
        tick_ordinal=2,
        price=10,
        continuity_generation="gen",
        bid_price=9.99,
        ask_price=10.01,
        bid_volume=100,
        ask_volume=100,
      ),
    ),
    quality_fields=frozenset(
      {"price", "bid_price", "ask_price", "bid_volume", "ask_volume"}
    ),
  )


def test_allows_deterministically_without_execution_outputs(gate_input):
  assert gate_input.candidate.policy_version == "policy-v1"
  assert gate_input.policy.version == "entry-gate-v1"
  result = EntryExecutionGate.evaluate(gate_input)
  assert result == EntryExecutionGate.evaluate(gate_input)
  assert result.decision == EntryExecutionDecision.ALLOW
  assert result.reason_codes == ("T_ENTRY_ALLOWED",)
  assert {field.name for field in fields(EntryExecutionGateResult)} == {
    "decision",
    "reason_codes",
    "candidate_id",
    "intent_id",
    "evaluated_at_ms",
    "expires_at_ms",
    "accepted_sequence",
  }


@pytest.mark.parametrize(
  ("field", "value", "reason"),
  [
    ("candidate_fingerprint", "changed", "FINGERPRINT_MISMATCH"),
    ("config_version_id", "changed", "CONFIG_BINDING_MISMATCH"),
    ("config_snapshot_hash", "changed", "CONFIG_BINDING_MISMATCH"),
    ("policy_version", "changed", "POLICY_BINDING_MISMATCH"),
    ("gate_policy_version", "changed", "GATE_POLICY_BINDING_MISMATCH"),
    ("feature_schema_version", 2, "SCHEMA_BINDING_MISMATCH"),
    ("capability_version", "changed", "CAPABILITY_BINDING_MISMATCH"),
    ("model_binding_hash", "model", "MODEL_BINDING_INVALID"),
    ("scorer_mode", "ACTIVE", "SCORER_UNSUPPORTED"),
    ("scorer_mode", "SHADOW", "SCORER_UNSUPPORTED"),
  ],
)
def test_rejects_changed_binding(gate_input, field, value, reason):
  result = EntryExecutionGate.evaluate(
    replace(
      gate_input,
      current_binding=replace(gate_input.current_binding, **{field: value}),
    )
  )
  assert result.decision == EntryExecutionDecision.REJECT
  assert f"T_ENTRY_{reason}" in result.reason_codes


@pytest.mark.parametrize(
  ("changes", "reason"),
  [
    ({"execution_environment": "LIVE"}, "ENVIRONMENT_UNSUPPORTED"),
    ({"intent_active": False}, "INTENT_INACTIVE"),
    ({"candidate_valid": False}, "CANDIDATE_INVALID"),
    ({"evaluated_at_ms": 60_000}, "TTL_EXPIRED"),
    ({"intent_created_at_ms": 13_000}, "FUTURE_TIMESTAMP"),
  ],
)
def test_rejects_invalid_lifecycle(gate_input, changes, reason):
  result = EntryExecutionGate.evaluate(replace(gate_input, **changes))
  assert result.decision == EntryExecutionDecision.REJECT
  assert f"T_ENTRY_{reason}" in result.reason_codes


def test_candidate_expiry_and_future_are_independent(gate_input):
  for change, reason in [
    ({"expires_at_ms": 12_000}, "TTL_EXPIRED"),
    ({"latched_at_ms": 12_001}, "FUTURE_TIMESTAMP"),
    ({"source_time_ms": 12_001}, "FUTURE_TIMESTAMP"),
    ({"fingerprint": "changed"}, "FINGERPRINT_MISMATCH"),
  ]:
    result = EntryExecutionGate.evaluate(
      replace(
        gate_input,
        candidate=replace(gate_input.candidate, **change),
      )
    )
    assert result.decision == EntryExecutionDecision.REJECT
    assert f"T_ENTRY_{reason}" in result.reason_codes


@pytest.mark.parametrize(
  ("changes", "decision", "reason"),
  [
    ({"source_time_ms": 12_001}, "REJECT", "FUTURE_TIMESTAMP"),
    ({"received_at_ms": 12_001}, "REJECT", "FUTURE_TIMESTAMP"),
    ({"source_time_ms": 9_999}, "REJECT", "QUOTE_PRECEDES_CANDIDATE"),
    ({"instrument_code": "000001.SZ"}, "REJECT", "INSTRUMENT_MISMATCH"),
    ({"continuity_generation": "new"}, "REJECT", "MARKET_DISCONTINUITY"),
    ({"ask_price": 10.10}, "DELAY", "SPREAD_EXCEEDED"),
    ({"ask_price": 10.12, "bid_price": 10.11}, "DELAY", "PRICE_DEVIATION_EXCEEDED"),
    ({"price": 10.11}, "DELAY", "PRICE_DEVIATION_EXCEEDED"),
    ({"bid_price": 10.02}, "DELAY", "BOOK_INVALID"),
    ({"ask_price": None}, "DELAY", "REQUIRED_CAPABILITY_UNAVAILABLE"),
    ({"price": float("nan")}, "DELAY", "REQUIRED_CAPABILITY_UNAVAILABLE"),
    ({"bid_price": float("inf")}, "DELAY", "REQUIRED_CAPABILITY_UNAVAILABLE"),
    ({"bid_price": 0}, "DELAY", "REQUIRED_CAPABILITY_UNAVAILABLE"),
    ({"bid_volume": 1}, "DELAY", "BOOK_DEPTH_INSUFFICIENT"),
    ({"bid_volume": 20, "ask_volume": 100}, "DELAY", "BOOK_IMBALANCE_INSUFFICIENT"),
  ],
)
def test_quote_revalidation(gate_input, changes, decision, reason):
  tick = replace(
    gate_input.latest_tick, sample=replace(gate_input.latest_tick.sample, **changes)
  )
  result = EntryExecutionGate.evaluate(replace(gate_input, latest_tick=tick))
  assert result.decision == decision
  assert f"T_ENTRY_{reason}" in result.reason_codes


@pytest.mark.parametrize(
  ("changes", "decision", "reason"),
  [
    ({"received_at_ms": 12_001}, "REJECT", "FUTURE_TIMESTAMP"),
    ({"received_at_ms": 8_999}, "DELAY", "QUOTE_STALE"),
    ({"stream_id": "new"}, "REJECT", "MARKET_DISCONTINUITY"),
    (
      {"discontinuity_reason": "T_MARKET_SEQUENCE_GAP"},
      "REJECT",
      "MARKET_DISCONTINUITY",
    ),
    ({"accepted_sequence": 1}, "DELAY", "QUOTE_NOT_LATEST"),
  ],
)
def test_accepted_tick_witness(gate_input, changes, decision, reason):
  result = EntryExecutionGate.evaluate(
    replace(
      gate_input,
      latest_tick=replace(gate_input.latest_tick, **changes),
    )
  )
  assert result.decision == decision
  assert f"T_ENTRY_{reason}" in result.reason_codes


def test_delay_never_extends_original_ttl(gate_input):
  delayed = replace(gate_input, latest_tick=None)
  result = EntryExecutionGate.evaluate(delayed)
  assert result.decision == "DELAY"
  assert result.expires_at_ms == 60_000
  result = EntryExecutionGate.evaluate(replace(delayed, evaluated_at_ms=60_000))
  assert result.decision == "REJECT"
  assert result.expires_at_ms == 60_000


def test_intervening_sequence_gap_invalidates_even_a_clean_latest_tick(gate_input):
  assert gate_input.latest_tick.discontinuity_reason is None
  result = EntryExecutionGate.evaluate(replace(gate_input, latest_ring_generation=2))
  assert result.decision == "REJECT"
  assert "T_ENTRY_MARKET_DISCONTINUITY" in result.reason_codes


def test_multiple_contiguous_ticks_since_candidate_are_valid(gate_input):
  tick = replace(gate_input.latest_tick, accepted_sequence=10)
  result = EntryExecutionGate.evaluate(
    replace(
      gate_input,
      latest_tick=tick,
      latest_accepted_sequence=10,
    )
  )
  assert result.decision == "ALLOW"


def test_staleness_uses_source_clock_with_inclusive_age_boundary(gate_input):
  assert (
    EntryExecutionGate.evaluate(replace(gate_input, evaluated_at_ms=14_800)).decision
    == "ALLOW"
  )
  result = EntryExecutionGate.evaluate(replace(gate_input, evaluated_at_ms=14_801))
  assert result.decision == "DELAY"
  assert "T_ENTRY_QUOTE_STALE" in result.reason_codes


@pytest.mark.parametrize("available", [False, True])
def test_required_depth_needs_declared_good_quality_and_value(gate_input, available):
  capabilities = replace(
    gate_input.capabilities,
    required_fields=gate_input.capabilities.required_fields
    | {"bid_volume", "ask_volume"},
    optional_fields=frozenset(),
  )
  tick = gate_input.latest_tick
  quality = gate_input.quality_fields
  if available:
    quality = quality - {"ask_volume"}
  else:
    tick = replace(tick, sample=replace(tick.sample, ask_volume=None))
  result = EntryExecutionGate.evaluate(
    replace(
      gate_input,
      latest_tick=tick,
      quality_fields=quality,
      capabilities=capabilities,
    )
  )
  assert result.decision == "DELAY"
  assert "T_ENTRY_FIELD_UNAVAILABLE:ask_volume" in result.reason_codes


@pytest.mark.parametrize("mode", ["missing", "bad_quality", "undeclared"])
def test_optional_depth_never_fabricated_or_used_without_capability(gate_input, mode):
  tick = replace(
    gate_input.latest_tick,
    sample=replace(
      gate_input.latest_tick.sample,
      bid_volume=None if mode == "missing" else 0,
    ),
  )
  quality = (
    gate_input.quality_fields - {"bid_volume"}
    if mode == "bad_quality"
    else gate_input.quality_fields
  )
  manifest = (
    replace(gate_input.capabilities, optional_fields=frozenset())
    if mode == "undeclared"
    else gate_input.capabilities
  )
  assert (
    EntryExecutionGate.evaluate(
      replace(
        gate_input,
        latest_tick=tick,
        quality_fields=quality,
        capabilities=manifest,
      )
    ).decision
    == "ALLOW"
  )


def test_zero_depth_is_not_zero_imbalance(gate_input):
  tick = replace(
    gate_input.latest_tick,
    sample=replace(
      gate_input.latest_tick.sample,
      bid_volume=0,
      ask_volume=0,
    ),
  )
  result = EntryExecutionGate.evaluate(replace(gate_input, latest_tick=tick))
  assert "T_ENTRY_BOOK_IMBALANCE_UNAVAILABLE" in result.reason_codes


def test_unknown_or_weakened_capability_manifest_is_invalid():
  with pytest.raises(ValueError):
    MarketDataCapabilityManifest(version="v1", required_fields=frozenset({"price"}))
  with pytest.raises(ValueError):
    MarketDataCapabilityManifest(
      version="v1",
      required_fields=frozenset({"price", "bid_price", "ask_price", "unknown"}),
    )


@pytest.mark.parametrize("target", ["policy", "frozen_binding"])
def test_gate_policy_version_is_independently_bound(gate_input, target):
  field = "version" if target == "policy" else "gate_policy_version"
  changed = replace(getattr(gate_input, target), **{field: "entry-gate-v2"})
  result = EntryExecutionGate.evaluate(replace(gate_input, **{target: changed}))
  assert result.decision == "REJECT"
  assert "T_ENTRY_GATE_POLICY_BINDING_MISMATCH" in result.reason_codes
  assert "T_ENTRY_POLICY_BINDING_MISMATCH" not in result.reason_codes


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), 12_000.0])
@pytest.mark.parametrize(
  "field",
  [
    "intent_created_at_ms",
    "intent_expires_at_ms",
    "evaluated_at_ms",
    "candidate_accepted_sequence",
    "candidate_ring_generation",
    "latest_ring_generation",
    "latest_accepted_sequence",
  ],
)
def test_gate_input_rejects_non_integer_time_and_sequence(gate_input, field, value):
  with pytest.raises(ValueError):
    replace(gate_input, **{field: value})


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), 3_000.0])
def test_quote_age_must_be_a_positive_integer(gate_input, value):
  with pytest.raises(ValueError):
    replace(gate_input.policy, quote_max_age_ms=value)


@pytest.mark.parametrize("value", [True, float("nan"), 12_000.0])
@pytest.mark.parametrize(
  "field",
  [
    "source_time_ms",
    "latched_at_ms",
    "expires_at_ms",
    "tick_ordinal",
  ],
)
def test_nested_candidate_integer_evidence_is_strict(gate_input, field, value):
  with pytest.raises(ValueError):
    replace(gate_input, candidate=replace(gate_input.candidate, **{field: value}))


@pytest.mark.parametrize("value", [True, float("nan"), 12_000.0])
@pytest.mark.parametrize("field", ["source_time_ms", "received_at_ms", "tick_ordinal"])
def test_nested_sample_integer_evidence_is_strict(gate_input, field, value):
  with pytest.raises(ValueError):
    sample = replace(gate_input.latest_tick.sample, **{field: value})
    replace(gate_input, latest_tick=replace(gate_input.latest_tick, sample=sample))


@pytest.mark.parametrize("value", [True, float("nan"), 12_000.0])
@pytest.mark.parametrize("field", ["accepted_sequence", "received_at_ms"])
def test_nested_tick_integer_evidence_is_strict(gate_input, field, value):
  with pytest.raises(ValueError):
    replace(gate_input, latest_tick=replace(gate_input.latest_tick, **{field: value}))


@pytest.mark.parametrize("damage", [None, "missing", "changed", "mode"])
def test_paper_shadow_gate_requires_same_model_binding_but_no_score(gate_input, damage):
  binding = replace(gate_input.frozen_binding, scorer_mode="SHADOW", model_binding_hash="a" * 64)
  current = binding
  if damage == "missing":
    current = replace(binding, model_binding_hash=None)
  elif damage == "changed":
    current = replace(binding, model_binding_hash="b" * 64)
  elif damage == "mode":
    current = replace(binding, scorer_mode="RULE_ONLY")
  request = replace(gate_input, frozen_binding=binding, current_binding=current)
  result = EntryExecutionGate.evaluate(request)
  assert (result.decision is EntryExecutionDecision.ALLOW) == (damage is None)
