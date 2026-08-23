"""Framework-neutral contracts for the stateful T-trade opportunity flow.

The application package deliberately contains no database, broker, HTTP, or
Engine imports.  These DTOs describe the boundaries that an Engine adapter
must satisfy; the ports are the only things that cross into infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Optional

from quantx_domain.trading.t_trade_opportunity_engine import (
  OpportunityReferenceProfile,
)


class D1ProfileReadReason(StrEnum):
  """Why a point-in-time profile cannot be used for this evaluation."""

  AVAILABLE = "AVAILABLE"
  NOT_FOUND = "PROFILE_NOT_FOUND"
  READ_FAILED = "PROFILE_READ_FAILED"
  INVALID = "PROFILE_INVALID"
  FUTURE = "PROFILE_NOT_CAUSAL"
  VERSION_MISMATCH = "PROFILE_VERSION_MISMATCH"


@dataclass(frozen=True)
class D1ProfileReadRequest:
  """One prior-only profile lookup for an evaluation timestamp."""

  instrument_code: str
  evaluated_at: datetime
  required_version: Optional[str] = None

  def __post_init__(self) -> None:
    code = str(self.instrument_code or "").strip().upper()
    if not code:
      raise ValueError("instrument_code is required")
    if not isinstance(self.evaluated_at, datetime):
      raise TypeError("evaluated_at must be a datetime")
    version = (
      str(self.required_version or "").strip() or None
      if self.required_version is not None
      else None
    )
    object.__setattr__(self, "instrument_code", code)
    object.__setattr__(self, "required_version", version)


@dataclass(frozen=True)
class D1ProfileReadResult:
  """A fail-closed profile read result.

  ``profile`` is the only value the domain strategy may consume.  A missing,
  malformed, future, or unavailable profile is represented by ``None`` and a
  stable reason instead of falling back to an older or same-day profile.
  """

  request: D1ProfileReadRequest
  profile: Optional[OpportunityReferenceProfile]
  reason: D1ProfileReadReason
  profile_fingerprint: Optional[str] = None
  error_type: Optional[str] = None

  @property
  def available(self) -> bool:
    return (
      self.profile is not None
      and self.reason is D1ProfileReadReason.AVAILABLE
    )


class EvaluationMaterializationStatus(StrEnum):
  """Outcome of the post-CAS evaluation append."""

  MATERIALIZED = "MATERIALIZED"
  BLOCKED_BEFORE_CAS = "BLOCKED_BEFORE_CAS"


@dataclass(frozen=True)
class PostCasEvaluationInput:
  """An evaluation event and the successful RuntimeState CAS fact."""

  event: Mapping[str, Any]
  account_id: str
  strategy_run_id: str
  cas_committed: bool

  def __post_init__(self) -> None:
    if not isinstance(self.event, Mapping):
      raise TypeError("event must be a mapping")
    if not isinstance(self.cas_committed, bool):
      raise TypeError("cas_committed must be bool")
    account_id = str(self.account_id or "").strip()
    run_id = str(self.strategy_run_id or "").strip()
    if not account_id:
      raise ValueError("account_id is required")
    if not run_id:
      raise ValueError("strategy_run_id is required")
    object.__setattr__(self, "account_id", account_id)
    object.__setattr__(self, "strategy_run_id", run_id)
    object.__setattr__(self, "event", dict(self.event))


@dataclass(frozen=True)
class EvaluationMaterializationResult:
  """Successful or pre-CAS-blocked application outcome."""

  status: EvaluationMaterializationStatus
  event_key: Optional[str]
  record: Any = None
  reason: Optional[str] = None

  @property
  def materialized(self) -> bool:
    return self.status is EvaluationMaterializationStatus.MATERIALIZED


@dataclass(frozen=True)
class IntentEmissionGateInput:
  """All external facts required before a candidate may emit an intent.

  Every optional fact is intentionally tri-state.  ``None`` means the caller
  did not provide an authoritative value and therefore blocks emission.  This
  prevents a partially restored Engine context from being interpreted as a
  safe ``False``.
  """

  account_id: str
  runtime_run_id: str
  context_run_id: str
  instrument_code: str
  universe_entry: Optional[Mapping[str, Any]] = None
  reconciliation_required: Optional[bool] = None
  account_concurrent_batch_limit_reached: Optional[bool] = None
  account_total_exposure_limit_reached: Optional[bool] = None
  same_instrument_pending_intent_exists: Optional[bool] = None

  def __post_init__(self) -> None:
    account_id = str(self.account_id or "").strip()
    runtime_run_id = str(self.runtime_run_id or "").strip()
    context_run_id = str(self.context_run_id or "").strip()
    instrument_code = str(self.instrument_code or "").strip().upper()
    if self.universe_entry is not None and not isinstance(
      self.universe_entry, Mapping
    ):
      raise TypeError("universe_entry must be a mapping")
    for name in (
      "reconciliation_required",
      "account_concurrent_batch_limit_reached",
      "account_total_exposure_limit_reached",
      "same_instrument_pending_intent_exists",
    ):
      value = getattr(self, name)
      if value is not None and not isinstance(value, bool):
        raise TypeError(f"{name} must be bool or None")
    object.__setattr__(self, "account_id", account_id)
    object.__setattr__(self, "runtime_run_id", runtime_run_id)
    object.__setattr__(self, "context_run_id", context_run_id)
    object.__setattr__(self, "instrument_code", instrument_code)
    if self.universe_entry is not None:
      object.__setattr__(self, "universe_entry", dict(self.universe_entry))


@dataclass(frozen=True)
class IntentEmissionGateResult:
  """Scope-checked, deterministic external emission decision."""

  account_id: str
  strategy_run_id: str
  instrument_code: str
  allowed: bool
  blockers: tuple[str, ...] = ()

  @property
  def blocked(self) -> bool:
    return not self.allowed

  def to_market_context(self) -> dict[str, Any]:
    """Return the small mapping consumed by the pure domain strategy."""

    return {
      "allowed": self.allowed,
      "blockers": list(self.blockers),
    }


@dataclass(frozen=True)
class SignalPolicyConfigSnapshot:
  """Account-level V3 policy and its optimistic-concurrency version."""

  account_id: str
  config_version: int
  signal_policy: Mapping[str, Any]
  strategy_run_id: Optional[str] = None

  def __post_init__(self) -> None:
    account_id = str(self.account_id or "").strip()
    if not account_id:
      raise ValueError("account_id is required")
    try:
      version = int(self.config_version)
    except (TypeError, ValueError, OverflowError) as exc:
      raise ValueError("config_version must be a non-negative integer") from exc
    if version < 0:
      raise ValueError("config_version must be a non-negative integer")
    if not isinstance(self.signal_policy, Mapping):
      raise TypeError("signal_policy must be a mapping")
    run_id = str(self.strategy_run_id or "").strip() or None
    object.__setattr__(self, "account_id", account_id)
    object.__setattr__(self, "config_version", version)
    object.__setattr__(self, "signal_policy", dict(self.signal_policy))
    object.__setattr__(self, "strategy_run_id", run_id)


@dataclass(frozen=True)
class SignalPolicyChangeRequest:
  """One account-level policy save request."""

  account_id: str
  expected_config_version: int
  signal_policy: Mapping[str, Any]

  def __post_init__(self) -> None:
    account_id = str(self.account_id or "").strip()
    if not account_id:
      raise ValueError("account_id is required")
    try:
      expected = int(self.expected_config_version)
    except (TypeError, ValueError, OverflowError) as exc:
      raise ValueError(
        "expected_config_version must be a non-negative integer"
      ) from exc
    if expected < 0:
      raise ValueError("expected_config_version must be a non-negative integer")
    if not isinstance(self.signal_policy, Mapping):
      raise TypeError("signal_policy must be a mapping")
    object.__setattr__(self, "account_id", account_id)
    object.__setattr__(self, "expected_config_version", expected)
    object.__setattr__(self, "signal_policy", dict(self.signal_policy))


@dataclass(frozen=True)
class SignalPolicyChangePlan:
  """Pure preview of a policy change before any persistence side effect."""

  account_id: str
  expected_config_version: int
  actual_config_version: int
  normalized_policy: Optional[Mapping[str, Any]]
  changed_fields: tuple[str, ...] = ()
  requires_rewarm: bool = False
  errors: tuple[str, ...] = ()
  warnings: tuple[str, ...] = ()

  @property
  def valid(self) -> bool:
    return not self.errors and self.normalized_policy is not None

  @property
  def config_version_matches(self) -> bool:
    return self.expected_config_version == self.actual_config_version

  @property
  def changed(self) -> bool:
    return bool(self.changed_fields)


__all__ = [
  "D1ProfileReadReason",
  "D1ProfileReadRequest",
  "D1ProfileReadResult",
  "EvaluationMaterializationResult",
  "EvaluationMaterializationStatus",
  "IntentEmissionGateInput",
  "IntentEmissionGateResult",
  "PostCasEvaluationInput",
  "SignalPolicyChangePlan",
  "SignalPolicyChangeRequest",
  "SignalPolicyConfigSnapshot",
]
