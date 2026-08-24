"""Pure application orchestration for T-trade opportunity V3."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional

from quantx_domain.trading.t_trade_opportunity_engine import (
  OPPORTUNITY_FEATURE_SCHEMA_VERSION,
  OPPORTUNITY_REFERENCE_PROFILE_SCHEMA_VERSION,
  OpportunityPolicy,
  OpportunityReferenceProfile,
)

from .contracts import (
  D1ProfileReadReason,
  D1ProfileReadRequest,
  D1ProfileReadResult,
  EvaluationMaterializationResult,
  EvaluationMaterializationStatus,
  IntentEmissionGateInput,
  IntentEmissionGateResult,
  PostCasEvaluationInput,
  SignalPolicyChangePlan,
  SignalPolicyChangeRequest,
  SignalPolicyConfigSnapshot,
)
from .ports import (
  D1ReferenceProfilePort,
  OpportunityEvaluationMaterializerPort,
)


class EvaluationMaterializationError(RuntimeError):
  """The post-CAS adapter failed; the caller must retain its durable outbox."""

  def __init__(self, event_key: str, cause: Exception) -> None:
    self.event_key = event_key
    self.cause = cause
    super().__init__(f"evaluation materialization failed: {event_key}")


def _stable_policy_version(values: Mapping[str, Any]) -> str:
  encoded = json.dumps(
    dict(values),
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
  ).encode("utf-8")
  return f"t_trade_opportunity_v3.{hashlib.sha256(encoded).hexdigest()[:12]}"


def normalize_signal_policy(value: Any) -> dict[str, Any]:
  """Normalize and validate the complete typed V3 policy without I/O.

  The policy version is derived from semantic fields.  A caller-supplied
  version is metadata only and cannot spoof a changed policy or hide a change.
  """

  default = OpportunityPolicy()
  if value is None:
    candidate = default
  else:
    if not isinstance(value, Mapping):
      # Keep the historical service contract: malformed policy payloads are
      # validation errors, not transport/type errors.
      raise ValueError("signal_policy must be a complete typed mapping")
    raw = dict(value)
    allowed = {
      "policy_version",
      "feature_schema_version",
      *OpportunityPolicy.configurable_field_names(),
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
      raise ValueError(f"signal_policy has unknown fields: {', '.join(unknown)}")
    required = set(OpportunityPolicy.configurable_field_names())
    missing = sorted(required - set(raw))
    if missing:
      raise ValueError(f"signal_policy missing fields: {', '.join(missing)}")
    supplied_feature_version = raw.pop("feature_schema_version", None)
    if supplied_feature_version is not None:
      try:
        feature_version = int(supplied_feature_version)
      except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("signal_policy feature_schema_version is invalid") from exc
      if feature_version != OPPORTUNITY_FEATURE_SCHEMA_VERSION:
        raise ValueError("signal_policy feature_schema_version is not current")
    raw.pop("policy_version", None)
    candidate = OpportunityPolicy(
      feature_schema_version=OPPORTUNITY_FEATURE_SCHEMA_VERSION,
      **{name: raw[name] for name in OpportunityPolicy.configurable_field_names()},
    )

  values = candidate.to_dict()
  semantic_values = {
    key: item for key, item in values.items() if key != "policy_version"
  }
  default_semantic_values = {
    key: item
    for key, item in default.to_dict().items()
    if key != "policy_version"
  }
  values["policy_version"] = (
    default.policy_version
    if semantic_values == default_semantic_values
    else _stable_policy_version(semantic_values)
  )
  return OpportunityPolicy.from_dict(values).to_dict()


class ReadD1ReferenceProfile:
  """Application use case for a strictly prior instrument profile."""

  def __init__(self, port: D1ReferenceProfilePort) -> None:
    self.port = port

  async def execute(self, request: D1ProfileReadRequest) -> D1ProfileReadResult:
    try:
      payload = await self.port.load_reference_profile(
        instrument_code=request.instrument_code,
        evaluated_at=request.evaluated_at,
        required_version=request.required_version,
      )
    except Exception as exc:
      return D1ProfileReadResult(
        request=request,
        profile=None,
        reason=D1ProfileReadReason.READ_FAILED,
        error_type=exc.__class__.__name__,
      )

    if payload is None:
      return D1ProfileReadResult(
        request=request,
        profile=None,
        reason=D1ProfileReadReason.NOT_FOUND,
      )
    try:
      raw = (
        payload.to_dict()
        if isinstance(payload, OpportunityReferenceProfile)
        else dict(payload)
      )
      profile = (
        payload
        if isinstance(payload, OpportunityReferenceProfile)
        else OpportunityReferenceProfile.from_dict(raw)
      )
    except (TypeError, ValueError, OverflowError):
      return D1ProfileReadResult(
        request=request,
        profile=None,
        reason=D1ProfileReadReason.INVALID,
      )

    if profile.profile_schema_version != OPPORTUNITY_REFERENCE_PROFILE_SCHEMA_VERSION:
      return D1ProfileReadResult(
        request=request,
        profile=None,
        reason=D1ProfileReadReason.INVALID,
      )
    if request.required_version and profile.profile_version != request.required_version:
      return D1ProfileReadResult(
        request=request,
        profile=None,
        reason=D1ProfileReadReason.VERSION_MISMATCH,
      )
    try:
      evaluated_date = request.evaluated_at.date()
      profile_date = datetime.fromisoformat(
        f"{profile.as_of_trade_date}T00:00:00"
      ).date()
    except (TypeError, ValueError, OverflowError):
      return D1ProfileReadResult(
        request=request,
        profile=None,
        reason=D1ProfileReadReason.INVALID,
      )
    if profile_date >= evaluated_date:
      return D1ProfileReadResult(
        request=request,
        profile=None,
        reason=D1ProfileReadReason.FUTURE,
      )

    fingerprint = str(raw.get("profile_fingerprint") or "").strip()
    if not fingerprint:
      fingerprint = hashlib.sha256(
        json.dumps(
          profile.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
      ).hexdigest()
    return D1ProfileReadResult(
      request=request,
      profile=profile,
      reason=D1ProfileReadReason.AVAILABLE,
      profile_fingerprint=fingerprint,
    )


class MaterializeEvaluationAfterCAS:
  """Materialize only after the caller proves a successful state CAS."""

  def __init__(self, port: OpportunityEvaluationMaterializerPort) -> None:
    self.port = port

  async def execute(
    self,
    request: PostCasEvaluationInput,
  ) -> EvaluationMaterializationResult:
    event_key = str(request.event.get("event_key") or "").strip()
    if not event_key:
      raise ValueError("evaluation event_key is required")
    if not request.cas_committed:
      return EvaluationMaterializationResult(
        status=EvaluationMaterializationStatus.BLOCKED_BEFORE_CAS,
        event_key=event_key,
        reason="RUNTIME_STATE_CAS_NOT_COMMITTED",
      )
    try:
      record = await self.port.materialize_evaluation(
        event=request.event,
        account_id=request.account_id,
        strategy_run_id=request.strategy_run_id,
      )
    except Exception as exc:
      raise EvaluationMaterializationError(event_key, exc) from exc
    return EvaluationMaterializationResult(
      status=EvaluationMaterializationStatus.MATERIALIZED,
      event_key=event_key,
      record=record,
    )

  async def execute_checkpoint_batch(
    self,
    requests: Iterable[PostCasEvaluationInput],
  ) -> tuple[str, ...]:
    """Materialize one committed checkpoint batch through its owned port.

    The checkpoint may contain coalesced diagnostics and non-actionable
    MATERIAL evidence.  Actionability is classified by the Engine before it
    reaches this generic boundary; this use case only enforces the committed,
    single-account/run, stable-source-identity contract.
    """

    normalized = tuple(requests)
    if not normalized:
      return ()
    event_keys = [
      str(request.event.get("event_key") or "").strip()
      for request in normalized
    ]
    if any(not event_key for event_key in event_keys):
      raise ValueError("evaluation event_key is required")
    if len(set(event_keys)) != len(event_keys):
      raise ValueError("checkpoint batch requires unique evaluation event_key values")
    if not all(request.cas_committed for request in normalized):
      # The caller receives an empty durable receipt and must retain every
      # outbox item.  Crucially, the infrastructure port is never invoked.
      return ()
    account_id = normalized[0].account_id
    strategy_run_id = normalized[0].strategy_run_id
    if any(
      request.account_id != account_id
      or request.strategy_run_id != strategy_run_id
      for request in normalized
    ):
      raise ValueError("checkpoint batch must share account_id and strategy_run_id")
    events = [dict(request.event) for request in normalized]
    if any(
      str(event.get("record_kind") or "").upper()
      not in {"COALESCED_DIAGNOSTIC", "MATERIAL"}
      for event in events
    ):
      raise ValueError(
        "checkpoint batch requires COALESCED_DIAGNOSTIC or MATERIAL events"
      )
    try:
      receipt = await self.port.materialize_checkpoint_batch(
        events=events,
        account_id=account_id,
        strategy_run_id=strategy_run_id,
      )
    except Exception as exc:
      raise EvaluationMaterializationError(event_keys[0], exc) from exc
    raw_persisted_keys = getattr(receipt, "persisted_event_keys", receipt)
    if isinstance(raw_persisted_keys, (str, bytes)):
      raise ValueError("checkpoint batch receipt must contain event_key values")
    try:
      persisted_keys = tuple(
        str(value or "").strip() for value in (raw_persisted_keys or ())
      )
    except TypeError as exc:
      raise ValueError("checkpoint batch receipt must contain event_key values") from exc
    if any(not event_key for event_key in persisted_keys):
      raise ValueError("checkpoint batch receipt contains an empty event_key")
    if len(set(persisted_keys)) != len(persisted_keys):
      raise ValueError("checkpoint batch receipt contains duplicate event_key values")
    if not set(persisted_keys).issubset(set(event_keys)):
      raise ValueError("checkpoint batch receipt contains an unknown event_key")
    return persisted_keys


class EvaluateIntentEmissionGate:
  """Pure, fail-closed external gate for candidate intent emission."""

  def execute(self, request: IntentEmissionGateInput) -> IntentEmissionGateResult:
    blockers: list[str] = []
    if not request.account_id:
      blockers.extend(
        [
          "T_TRADE_INTENT_EMISSION_SCOPE_UNAVAILABLE",
          "T_TRADE_INTENT_EMISSION_ACCOUNT_SCOPE_UNAVAILABLE",
        ]
      )
    if not request.runtime_run_id or not request.context_run_id:
      blockers.extend(
        [
          "T_TRADE_INTENT_EMISSION_SCOPE_UNAVAILABLE",
          "T_TRADE_INTENT_EMISSION_RUN_SCOPE_UNAVAILABLE",
        ]
      )
    if not request.instrument_code:
      blockers.append("T_TRADE_INTENT_EMISSION_INSTRUMENT_SCOPE_UNAVAILABLE")
    if request.runtime_run_id != request.context_run_id:
      blockers.extend(
        [
          "T_TRADE_INTENT_EMISSION_SCOPE_MISMATCH",
          "T_TRADE_INTENT_EMISSION_RUN_SCOPE_MISMATCH",
        ]
      )

    entry = request.universe_entry
    if entry is None:
      blockers.append("UNIVERSE_ELIGIBILITY_UNAVAILABLE")
    else:
      entry_account_id = str(entry.get("account_id") or "").strip()
      entry_run_id = str(
        entry.get("run_id") or entry.get("strategy_run_id") or ""
      ).strip()
      entry_code = str(entry.get("instrument_code") or "").strip().upper()
      if not entry_account_id:
        blockers.extend(
          [
            "T_TRADE_INTENT_EMISSION_SCOPE_UNAVAILABLE",
            "T_TRADE_INTENT_EMISSION_ACCOUNT_SCOPE_UNAVAILABLE",
          ]
        )
      elif entry_account_id != request.account_id:
        blockers.extend(
          [
            "T_TRADE_INTENT_EMISSION_SCOPE_MISMATCH",
            "T_TRADE_INTENT_EMISSION_ACCOUNT_SCOPE_MISMATCH",
          ]
        )
      if not entry_run_id:
        blockers.extend(
          [
            "T_TRADE_INTENT_EMISSION_SCOPE_UNAVAILABLE",
            "T_TRADE_INTENT_EMISSION_RUN_SCOPE_UNAVAILABLE",
          ]
        )
      elif entry_run_id != request.runtime_run_id:
        blockers.extend(
          [
            "T_TRADE_INTENT_EMISSION_SCOPE_MISMATCH",
            "T_TRADE_INTENT_EMISSION_RUN_SCOPE_MISMATCH",
          ]
        )
      if entry_code != request.instrument_code:
        blockers.append("T_TRADE_INTENT_EMISSION_INSTRUMENT_SCOPE_MISMATCH")
      blockers.extend(_text_blockers(entry.get("blockers")))
      if entry.get("draining") is True:
        blockers.append("INSTRUMENT_DRAINING")
      if entry.get("eligible") is not True and not _text_blockers(
        entry.get("blockers")
      ):
        blockers.append(str(entry.get("reason") or "POSITION_NOT_ELIGIBLE"))
      if entry.get("eligible") is not True and entry.get("eligible") is not False:
        blockers.append("UNIVERSE_ELIGIBILITY_UNAVAILABLE")
      if entry.get("allowed") is not True:
        if not blockers:
          blockers.append(
            "INTENT_EMISSION_NOT_ALLOWED"
            if entry.get("allowed") is False
            else "T_TRADE_INTENT_EMISSION_CONTEXT_INVALID"
          )

    blockers.extend(
      _tri_state_blocker(
        request.reconciliation_required,
        true_code="T_TRADE_RECONCILIATION_REQUIRED",
        missing_code="T_TRADE_RECONCILIATION_STATUS_UNKNOWN",
      )
    )
    blockers.extend(
      _tri_state_blocker(
        request.account_concurrent_batch_limit_reached,
        true_code="T_TRADE_ACCOUNT_CONCURRENT_BATCH_LIMIT_REACHED",
        missing_code="T_TRADE_ACCOUNT_CONCURRENT_BATCH_LIMIT_UNKNOWN",
      )
    )
    blockers.extend(
      _tri_state_blocker(
        request.account_total_exposure_limit_reached,
        true_code="T_TRADE_ACCOUNT_TOTAL_EXPOSURE_LIMIT_REACHED",
        missing_code="T_TRADE_ACCOUNT_TOTAL_EXPOSURE_LIMIT_UNKNOWN",
      )
    )
    blockers.extend(
      _tri_state_blocker(
        request.same_instrument_pending_intent_exists,
        true_code="T_TRADE_SAME_INSTRUMENT_PENDING_INTENT_EXISTS",
        missing_code="T_TRADE_SAME_INSTRUMENT_PENDING_INTENT_UNKNOWN",
      )
    )
    unique = tuple(dict.fromkeys(item for item in blockers if item))
    return IntentEmissionGateResult(
      account_id=request.account_id,
      strategy_run_id=request.runtime_run_id,
      instrument_code=request.instrument_code,
      allowed=not unique,
      blockers=unique,
    )


def _text_blockers(value: Any) -> list[str]:
  if value is None:
    return []
  values = [value] if isinstance(value, str) else value
  if not isinstance(values, (list, tuple, set)):
    return ["T_TRADE_INTENT_EMISSION_CONTEXT_INVALID"]
  return [str(item).strip() for item in values if str(item).strip()]


def _tri_state_blocker(
  value: Optional[bool],
  *,
  true_code: str,
  missing_code: str,
) -> list[str]:
  if value is None:
    return [missing_code]
  return [true_code] if value else []


class SignalPolicyChangePlanner:
  """Pure policy normalization, diff, conflict, and rewarm planning."""

  def plan(
    self,
    request: SignalPolicyChangeRequest,
    current: SignalPolicyConfigSnapshot,
  ) -> SignalPolicyChangePlan:
    if request.account_id != current.account_id:
      return SignalPolicyChangePlan(
        account_id=request.account_id,
        expected_config_version=request.expected_config_version,
        actual_config_version=current.config_version,
        normalized_policy=None,
        errors=("ACCOUNT_SCOPE_MISMATCH",),
      )
    if request.expected_config_version != current.config_version:
      return SignalPolicyChangePlan(
        account_id=request.account_id,
        expected_config_version=request.expected_config_version,
        actual_config_version=current.config_version,
        normalized_policy=None,
        errors=("CONFIG_VERSION_CONFLICT",),
      )
    try:
      current_policy = normalize_signal_policy(current.signal_policy)
      requested_policy = normalize_signal_policy(request.signal_policy)
    except (TypeError, ValueError, OverflowError) as exc:
      return SignalPolicyChangePlan(
        account_id=request.account_id,
        expected_config_version=request.expected_config_version,
        actual_config_version=current.config_version,
        normalized_policy=None,
        errors=(str(exc),),
      )
    changed_fields = tuple(
      sorted(
        key
        for key in set(current_policy) | set(requested_policy)
        if key not in {"policy_version", "feature_schema_version"}
        and current_policy.get(key) != requested_policy.get(key)
      )
    )
    return SignalPolicyChangePlan(
      account_id=request.account_id,
      expected_config_version=request.expected_config_version,
      actual_config_version=current.config_version,
      normalized_policy=requested_policy,
      changed_fields=changed_fields,
      requires_rewarm=bool(changed_fields),
      warnings=(
        ("保存后将清空机会窗口并使旧待确认入场失效",)
        if changed_fields
        else ()
      ),
    )


__all__ = [
  "EvaluationMaterializationError",
  "EvaluateIntentEmissionGate",
  "MaterializeEvaluationAfterCAS",
  "ReadD1ReferenceProfile",
  "SignalPolicyChangePlanner",
  "normalize_signal_policy",
]
