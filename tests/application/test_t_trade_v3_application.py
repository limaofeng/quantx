from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_application.t_trade_v3 import (
  D1ProfileReadReason,
  D1ProfileReadRequest,
  EvaluateIntentEmissionGate,
  EvaluationMaterializationError,
  EvaluationMaterializationStatus,
  IntentEmissionGateInput,
  MaterializeEvaluationAfterCAS,
  PostCasEvaluationInput,
  ReadD1ReferenceProfile,
  normalize_signal_policy,
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  OpportunityPolicy,
  OpportunityReferenceProfile,
)


def _profile(**overrides):
  values = {
    "profile_version": "profile-1",
    "profile_schema_version": 1,
    "as_of_trade_date": "2026-08-22",
    "pullback_threshold_pct": 0.8,
    "momentum_rise_threshold_pct": 0.8,
    "momentum_amount_velocity_ratio": 2.0,
    "pullback_max_spread_ticks": 3,
    "momentum_max_spread_ticks": 10,
  }
  values.update(overrides)
  return values


def _full_policy(**overrides):
  values = OpportunityPolicy().to_dict()
  values.update(overrides)
  return values


def _gate_input(**overrides):
  values = {
    "account_id": "account-1",
    "runtime_run_id": "run-1",
    "context_run_id": "run-1",
    "instrument_code": "600000.SH",
    "universe_entry": {
      "account_id": "account-1",
      "run_id": "run-1",
      "instrument_code": "600000.SH",
      "eligible": True,
      "allowed": True,
      "blockers": [],
    },
    "reconciliation_required": False,
    "account_concurrent_batch_limit_reached": False,
    "account_total_exposure_limit_reached": False,
    "same_instrument_pending_intent_exists": False,
  }
  values.update(overrides)
  return IntentEmissionGateInput(**values)


@pytest.mark.asyncio
async def test_d1_profile_use_case_accepts_prior_profile_and_normalizes_code():
  port = AsyncMock()
  port.load_reference_profile.return_value = _profile()
  use_case = ReadD1ReferenceProfile(port)

  result = await use_case.execute(
    D1ProfileReadRequest(
      instrument_code="600000.sh",
      evaluated_at=datetime(2026, 8, 23, 10, 0),
      required_version="profile-1",
    )
  )

  assert result.available is True
  assert isinstance(result.profile, OpportunityReferenceProfile)
  assert result.reason is D1ProfileReadReason.AVAILABLE
  assert result.request.instrument_code == "600000.SH"
  port.load_reference_profile.assert_awaited_once_with(
    instrument_code="600000.SH",
    evaluated_at=datetime(2026, 8, 23, 10, 0),
    required_version="profile-1",
  )


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("payload", "reason"),
  [
    (None, D1ProfileReadReason.NOT_FOUND),
    (_profile(as_of_trade_date="2026-08-23"), D1ProfileReadReason.FUTURE),
    (_profile(profile_version="other"), D1ProfileReadReason.VERSION_MISMATCH),
    ({"profile_version": "bad"}, D1ProfileReadReason.INVALID),
  ],
)
async def test_d1_profile_use_case_fail_closes_invalid_or_noncausal_payload(
  payload,
  reason,
):
  port = AsyncMock()
  port.load_reference_profile.return_value = payload
  result = await ReadD1ReferenceProfile(port).execute(
    D1ProfileReadRequest(
      instrument_code="600000.SH",
      evaluated_at=datetime(2026, 8, 23, 10, 0),
      required_version=("profile-1" if reason is D1ProfileReadReason.VERSION_MISMATCH else None),
    )
  )

  assert result.available is False
  assert result.profile is None
  assert result.reason is reason


@pytest.mark.asyncio
async def test_d1_profile_read_failure_returns_blocked_result_without_fallback():
  port = AsyncMock()
  port.load_reference_profile.side_effect = ConnectionError("database unavailable")

  result = await ReadD1ReferenceProfile(port).execute(
    D1ProfileReadRequest(
      instrument_code="600000.SH",
      evaluated_at=datetime(2026, 8, 23, 10, 0),
    )
  )

  assert result.reason is D1ProfileReadReason.READ_FAILED
  assert result.profile is None
  assert result.error_type == "ConnectionError"


@pytest.mark.asyncio
async def test_materialization_is_never_called_before_successful_cas():
  port = AsyncMock()
  use_case = MaterializeEvaluationAfterCAS(port)
  request = PostCasEvaluationInput(
    event={"event_key": "event-1", "type": "T_TRADE_OPPORTUNITY_EVALUATION"},
    account_id="account-1",
    strategy_run_id="run-1",
    cas_committed=False,
  )

  result = await use_case.execute(request)

  assert result.status is EvaluationMaterializationStatus.BLOCKED_BEFORE_CAS
  assert result.reason == "RUNTIME_STATE_CAS_NOT_COMMITTED"
  port.materialize_evaluation.assert_not_awaited()


@pytest.mark.asyncio
async def test_materialization_receives_scope_only_after_cas():
  port = AsyncMock()
  port.materialize_evaluation.return_value = "row-1"
  result = await MaterializeEvaluationAfterCAS(port).execute(
    PostCasEvaluationInput(
      event={"event_key": "event-1", "type": "T_TRADE_OPPORTUNITY_EVALUATION"},
      account_id="account-1",
      strategy_run_id="run-1",
      cas_committed=True,
    )
  )

  assert result.status is EvaluationMaterializationStatus.MATERIALIZED
  assert result.materialized is True
  assert result.record == "row-1"
  port.materialize_evaluation.assert_awaited_once_with(
    event={"event_key": "event-1", "type": "T_TRADE_OPPORTUNITY_EVALUATION"},
    account_id="account-1",
    strategy_run_id="run-1",
  )


@pytest.mark.asyncio
async def test_materialization_failure_preserves_typed_retry_error():
  port = AsyncMock()
  port.materialize_evaluation.side_effect = TimeoutError("append timed out")

  with pytest.raises(EvaluationMaterializationError) as captured:
    await MaterializeEvaluationAfterCAS(port).execute(
      PostCasEvaluationInput(
        event={"event_key": "event-1"},
        account_id="account-1",
        strategy_run_id="run-1",
        cas_committed=True,
      )
    )
  assert captured.value.event_key == "event-1"
  assert isinstance(captured.value.cause, TimeoutError)


@pytest.mark.asyncio
async def test_checkpoint_batch_passes_mixed_committed_sources_and_preserves_receipt_order():
  port = AsyncMock()
  port.materialize_checkpoint_batch.return_value = SimpleNamespace(
    persisted_event_keys=("diagnostic-1", "material-1")
  )
  requests = (
    PostCasEvaluationInput(
      event={
        "event_key": "diagnostic-1",
        "record_kind": "COALESCED_DIAGNOSTIC",
      },
      account_id="account-1",
      strategy_run_id="run-1",
      cas_committed=True,
    ),
    PostCasEvaluationInput(
      event={"event_key": "material-1", "record_kind": "MATERIAL"},
      account_id="account-1",
      strategy_run_id="run-1",
      cas_committed=True,
    ),
  )

  receipt = await MaterializeEvaluationAfterCAS(port).execute_checkpoint_batch(requests)

  assert receipt == ("diagnostic-1", "material-1")
  port.materialize_checkpoint_batch.assert_awaited_once_with(
    events=[dict(request.event) for request in requests],
    account_id="account-1",
    strategy_run_id="run-1",
  )


@pytest.mark.asyncio
async def test_checkpoint_batch_rejects_unknown_kind_before_calling_port():
  port = AsyncMock()
  request = PostCasEvaluationInput(
    event={"event_key": "unsupported-1", "record_kind": "ACTIONABLE"},
    account_id="account-1",
    strategy_run_id="run-1",
    cas_committed=True,
  )

  with pytest.raises(ValueError, match="COALESCED_DIAGNOSTIC or MATERIAL"):
    await MaterializeEvaluationAfterCAS(port).execute_checkpoint_batch((request,))

  port.materialize_checkpoint_batch.assert_not_awaited()


@pytest.mark.parametrize("value", [0, 1, None, "true"])
def test_post_cas_evaluation_requires_real_bool(value):
  with pytest.raises(TypeError, match="cas_committed must be bool"):
    PostCasEvaluationInput(
      event={"event_key": "event-1"},
      account_id="account-1",
      strategy_run_id="run-1",
      cas_committed=value,
    )


def test_intent_emission_gate_allows_only_complete_scoped_facts():
  result = EvaluateIntentEmissionGate().execute(_gate_input())

  assert result.allowed is True
  assert result.blockers == ()
  assert result.to_market_context() == {"allowed": True, "blockers": []}


@pytest.mark.parametrize(
  ("field", "expected"),
  [
    ("universe_entry", "UNIVERSE_ELIGIBILITY_UNAVAILABLE"),
    ("reconciliation_required", "T_TRADE_RECONCILIATION_STATUS_UNKNOWN"),
    (
      "account_concurrent_batch_limit_reached",
      "T_TRADE_ACCOUNT_CONCURRENT_BATCH_LIMIT_UNKNOWN",
    ),
    (
      "account_total_exposure_limit_reached",
      "T_TRADE_ACCOUNT_TOTAL_EXPOSURE_LIMIT_UNKNOWN",
    ),
    (
      "same_instrument_pending_intent_exists",
      "T_TRADE_SAME_INSTRUMENT_PENDING_INTENT_UNKNOWN",
    ),
  ],
)
def test_intent_emission_gate_fails_closed_when_any_authority_is_missing(
  field,
  expected,
):
  values = _gate_input().__dict__
  values[field] = None
  result = EvaluateIntentEmissionGate().execute(IntentEmissionGateInput(**values))

  assert result.allowed is False
  assert expected in result.blockers


def test_intent_emission_gate_rejects_cross_scope_and_draining_entry():
  result = EvaluateIntentEmissionGate().execute(
    _gate_input(
      context_run_id="run-other",
      universe_entry={
        "account_id": "account-other",
        "run_id": "run-other",
        "instrument_code": "600000.SH",
        "eligible": True,
        "draining": True,
        "blockers": [],
      },
    )
  )

  assert result.allowed is False
  assert result.blockers == (
    "T_TRADE_INTENT_EMISSION_SCOPE_MISMATCH",
    "T_TRADE_INTENT_EMISSION_RUN_SCOPE_MISMATCH",
    "T_TRADE_INTENT_EMISSION_ACCOUNT_SCOPE_MISMATCH",
    "INSTRUMENT_DRAINING",
  )
  assert "T_TRADE_INTENT_EMISSION_SCOPE_MISMATCH" in result.blockers
  assert "T_TRADE_INTENT_EMISSION_ACCOUNT_SCOPE_MISMATCH" in result.blockers
  assert "INSTRUMENT_DRAINING" in result.blockers


def test_intent_emission_gate_turns_missing_scope_into_blockers_not_authorization():
  result = EvaluateIntentEmissionGate().execute(
    _gate_input(
      account_id="",
      runtime_run_id="",
      context_run_id="",
      instrument_code="",
      universe_entry=None,
    )
  )

  assert result.allowed is False
  assert "T_TRADE_INTENT_EMISSION_ACCOUNT_SCOPE_UNAVAILABLE" in result.blockers
  assert "T_TRADE_INTENT_EMISSION_RUN_SCOPE_UNAVAILABLE" in result.blockers
  assert "T_TRADE_INTENT_EMISSION_INSTRUMENT_SCOPE_UNAVAILABLE" in result.blockers


def test_intent_emission_gate_does_not_trust_false_allowed_flag():
  result = EvaluateIntentEmissionGate().execute(
    _gate_input(
      universe_entry={
        "account_id": "account-1",
        "run_id": "run-1",
        "instrument_code": "600000.SH",
        "eligible": True,
        "allowed": False,
        "blockers": [],
      }
    )
  )

  assert result.allowed is False
  assert result.blockers == ("INTENT_EMISSION_NOT_ALLOWED",)


def test_intent_emission_gate_preserves_explicit_blockers_without_generic_fallback():
  blocked = EvaluateIntentEmissionGate().execute(
    _gate_input(
      universe_entry={
        "account_id": "account-1",
        "run_id": "run-1",
        "instrument_code": "600000.SH",
        "eligible": False,
        "allowed": False,
        "blockers": ["UNIVERSE_ELIGIBILITY_UNAVAILABLE"],
      }
    )
  )
  assert blocked.blockers == ("UNIVERSE_ELIGIBILITY_UNAVAILABLE",)

  unknown_allowed = EvaluateIntentEmissionGate().execute(
    _gate_input(
      universe_entry={
        "account_id": "account-1",
        "run_id": "run-1",
        "instrument_code": "600000.SH",
        "eligible": True,
        "blockers": ["INSTRUMENT_DRAINING"],
      }
    )
  )
  assert unknown_allowed.blockers == ("INSTRUMENT_DRAINING",)

  unknown_without_blocker = EvaluateIntentEmissionGate().execute(
    _gate_input(
      universe_entry={
        "account_id": "account-1",
        "run_id": "run-1",
        "instrument_code": "600000.SH",
        "eligible": True,
        "blockers": [],
      }
    )
  )
  assert unknown_without_blocker.blockers == (
    "T_TRADE_INTENT_EMISSION_CONTEXT_INVALID",
  )


def test_intent_emission_gate_deduplicates_universe_blockers():
  result = EvaluateIntentEmissionGate().execute(
    _gate_input(
      universe_entry={
        "account_id": "account-1",
        "run_id": "run-1",
        "instrument_code": "600000.SH",
        "eligible": False,
        "blockers": ["POSITION_NOT_ELIGIBLE", "POSITION_NOT_ELIGIBLE"],
      }
    )
  )

  assert result.allowed is False
  assert result.blockers.count("POSITION_NOT_ELIGIBLE") == 1


@pytest.mark.parametrize(
  ("field", "expected"),
  [
    ("reconciliation_required", "T_TRADE_RECONCILIATION_REQUIRED"),
    (
      "account_concurrent_batch_limit_reached",
      "T_TRADE_ACCOUNT_CONCURRENT_BATCH_LIMIT_REACHED",
    ),
    (
      "account_total_exposure_limit_reached",
      "T_TRADE_ACCOUNT_TOTAL_EXPOSURE_LIMIT_REACHED",
    ),
    (
      "same_instrument_pending_intent_exists",
      "T_TRADE_SAME_INSTRUMENT_PENDING_INTENT_EXISTS",
    ),
  ],
)
def test_intent_emission_gate_reports_each_true_account_fact(field, expected):
  values = _gate_input().__dict__
  values[field] = True
  result = EvaluateIntentEmissionGate().execute(IntentEmissionGateInput(**values))

  assert result.allowed is False
  assert result.blockers == (expected,)


def test_policy_normalization_rejects_partial_and_unknown_payloads():
  with pytest.raises(ValueError, match="missing fields"):
    normalize_signal_policy({"candidate_score": 75.0})
  with pytest.raises(ValueError, match="unknown fields"):
    normalize_signal_policy({**_full_policy(), "unexpected": 1})
  with pytest.raises(ValueError, match="feature_schema_version"):
    normalize_signal_policy(_full_policy(feature_schema_version=999))


def test_policy_normalization_ignores_spoofed_version_but_is_deterministic():
  first = normalize_signal_policy(_full_policy(candidate_score=74.0))
  second = normalize_signal_policy(
    _full_policy(candidate_score=74.0, policy_version="spoofed")
  )

  assert first == second
  assert first["policy_version"].startswith("t_trade_opportunity_v3.")
