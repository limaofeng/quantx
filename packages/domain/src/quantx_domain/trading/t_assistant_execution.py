"""Pure lifecycle values for the independent multi-symbol T assistant.

The execution is an ENTRY owner, not an account ledger and not a StrategyRun.
Its lifecycle guards only its own unsettled BUY work.  Downstream T batches and
ExitPlans retain their own durable owners and therefore do not keep a source
execution alive.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional

from quantx_contracts import (
  ExecutionEnvironment,
  ExecutionOwnerRef,
  ExecutionOwnerType,
)


class TAssistantEntryAuthorization(str, Enum):
  MANUAL_CONFIRM = "MANUAL_CONFIRM"
  AUTO = "AUTO"


class TAssistantRolloutStage(str, Enum):
  CANARY = "CANARY"
  STANDARD = "STANDARD"


class TAssistantScorerMode(str, Enum):
  RULE_ONLY = "RULE_ONLY"
  SHADOW = "SHADOW"
  ACTIVE = "ACTIVE"


class TAssistantExecutionStatus(str, Enum):
  CREATED = "CREATED"
  WARMING = "WARMING"
  RUNNING = "RUNNING"
  DRAINING = "DRAINING"
  STOPPED = "STOPPED"
  FAILED = "FAILED"
  RECONCILE_REQUIRED = "RECONCILE_REQUIRED"

  @property
  def terminal(self) -> bool:
    return self in {self.STOPPED, self.FAILED}


class TAssistantEntryReadiness(str, Enum):
  BLOCKED = "BLOCKED"
  WARMING = "WARMING"
  READY = "READY"
  DEGRADED = "DEGRADED"
  DRAINING = "DRAINING"
  RECONCILE_REQUIRED = "RECONCILE_REQUIRED"


class TAssistantSymbolLifecycle(str, Enum):
  WARMING = "WARMING"
  ACTIVE = "ACTIVE"
  DRAINING = "DRAINING"
  RETIRED = "RETIRED"


class TDecisionCycleStatus(str, Enum):
  PREPARED = "PREPARED"
  PROPOSALS_COMMITTED = "PROPOSALS_COMMITTED"
  ABORTED_STALE = "ABORTED_STALE"
  ABORTED = "ABORTED"

  @property
  def terminal(self) -> bool:
    return self is not self.PREPARED


_ALLOWED_TRANSITIONS: dict[
  TAssistantExecutionStatus, frozenset[TAssistantExecutionStatus]
] = {
  TAssistantExecutionStatus.CREATED: frozenset(
    {
      TAssistantExecutionStatus.WARMING,
      TAssistantExecutionStatus.DRAINING,
      TAssistantExecutionStatus.FAILED,
      TAssistantExecutionStatus.RECONCILE_REQUIRED,
    }
  ),
  TAssistantExecutionStatus.WARMING: frozenset(
    {
      TAssistantExecutionStatus.RUNNING,
      TAssistantExecutionStatus.DRAINING,
      TAssistantExecutionStatus.FAILED,
      TAssistantExecutionStatus.RECONCILE_REQUIRED,
    }
  ),
  TAssistantExecutionStatus.RUNNING: frozenset(
    {
      TAssistantExecutionStatus.DRAINING,
      TAssistantExecutionStatus.FAILED,
      TAssistantExecutionStatus.RECONCILE_REQUIRED,
    }
  ),
  TAssistantExecutionStatus.DRAINING: frozenset(
    {
      TAssistantExecutionStatus.STOPPED,
      TAssistantExecutionStatus.FAILED,
      TAssistantExecutionStatus.RECONCILE_REQUIRED,
    }
  ),
  TAssistantExecutionStatus.RECONCILE_REQUIRED: frozenset(
    {
      TAssistantExecutionStatus.DRAINING,
      TAssistantExecutionStatus.FAILED,
    }
  ),
  TAssistantExecutionStatus.STOPPED: frozenset(),
  TAssistantExecutionStatus.FAILED: frozenset(),
}


def canonical_json_payload(value: Mapping[str, Any]) -> str:
  """Return the sole canonical JSON representation used by config/cycle ids."""

  if not isinstance(value, Mapping):
    raise TypeError("canonical payload must be a mapping")
  try:
    encoded = json.dumps(
      dict(value),
      ensure_ascii=False,
      sort_keys=True,
      separators=(",", ":"),
      allow_nan=False,
    )
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError("canonical payload must contain finite JSON values") from exc
  decoded = json.loads(encoded)
  if not isinstance(decoded, dict):
    raise ValueError("canonical payload must encode an object")
  return encoded


def stable_manifest_hash(value: Mapping[str, Any]) -> str:
  return hashlib.sha256(canonical_json_payload(value).encode("utf-8")).hexdigest()


def t_assistant_config_snapshot_material(
  *,
  config_schema_version: str,
  canonical_payload: Mapping[str, Any],
  entry_authorization: "TAssistantEntryAuthorization | str",
  rollout_stage: "TAssistantRolloutStage | str",
  policy_version: str,
  feature_schema_version: int,
  scorer_mode: "TAssistantScorerMode | str",
  model_runtime_binding: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
  """Canonical safety material frozen by a config snapshot hash."""

  return {
    "config_schema_version": str(config_schema_version),
    "canonical_payload": dict(canonical_payload),
    "entry_authorization": TAssistantEntryAuthorization(entry_authorization).value,
    "rollout_stage": TAssistantRolloutStage(rollout_stage).value,
    "policy_version": str(policy_version),
    "feature_schema_version": int(feature_schema_version),
    "scorer_mode": TAssistantScorerMode(scorer_mode).value,
    "model_runtime_binding": (
      dict(model_runtime_binding) if model_runtime_binding is not None else None
    ),
  }


@dataclass(frozen=True)
class TAssistantConfigVersion:
  config_version_id: str
  config_id: str
  version: int
  config_schema_version: str
  canonical_payload: Mapping[str, Any]
  config_snapshot_hash: str
  entry_authorization: TAssistantEntryAuthorization
  rollout_stage: TAssistantRolloutStage
  policy_version: str
  feature_schema_version: int
  scorer_mode: TAssistantScorerMode = TAssistantScorerMode.RULE_ONLY
  model_runtime_binding: Optional[Mapping[str, Any]] = None

  def __post_init__(self) -> None:
    if not self.config_version_id or not self.config_id:
      raise ValueError("T-assistant config version requires stable ids")
    if self.version < 1 or self.feature_schema_version < 1:
      raise ValueError("T-assistant config/feature versions must be positive")
    if not self.config_schema_version or not self.policy_version:
      raise ValueError("T-assistant config and policy versions are required")
    object.__setattr__(
      self,
      "entry_authorization",
      TAssistantEntryAuthorization(self.entry_authorization),
    )
    object.__setattr__(self, "rollout_stage", TAssistantRolloutStage(self.rollout_stage))
    object.__setattr__(self, "scorer_mode", TAssistantScorerMode(self.scorer_mode))
    canonical = canonical_json_payload(self.canonical_payload)
    payload = json.loads(canonical)
    object.__setattr__(self, "canonical_payload", payload)
    binding = self.model_runtime_binding
    if self.scorer_mode is TAssistantScorerMode.RULE_ONLY and binding is not None:
      raise ValueError("RULE_ONLY T-assistant config cannot bind a model")
    if self.scorer_mode is not TAssistantScorerMode.RULE_ONLY and binding is None:
      raise ValueError("SHADOW/ACTIVE T-assistant config requires a model binding")
    if binding is not None:
      binding_canonical = canonical_json_payload(binding)
      object.__setattr__(self, "model_runtime_binding", json.loads(binding_canonical))
    expected_hash = stable_manifest_hash(
      t_assistant_config_snapshot_material(
        config_schema_version=self.config_schema_version,
        canonical_payload=payload,
        entry_authorization=self.entry_authorization,
        rollout_stage=self.rollout_stage,
        policy_version=self.policy_version,
        feature_schema_version=self.feature_schema_version,
        scorer_mode=self.scorer_mode,
        model_runtime_binding=self.model_runtime_binding,
      )
    )
    if self.config_snapshot_hash != expected_hash:
      raise ValueError("T-assistant config snapshot hash mismatch")

  @classmethod
  def create(
    cls,
    *,
    config_version_id: str,
    config_id: str,
    version: int,
    config_schema_version: str,
    canonical_payload: Mapping[str, Any],
    entry_authorization: TAssistantEntryAuthorization,
    rollout_stage: TAssistantRolloutStage,
    policy_version: str,
    feature_schema_version: int,
    scorer_mode: TAssistantScorerMode = TAssistantScorerMode.RULE_ONLY,
    model_runtime_binding: Optional[Mapping[str, Any]] = None,
  ) -> "TAssistantConfigVersion":
    return cls(
      config_version_id=config_version_id,
      config_id=config_id,
      version=version,
      config_schema_version=config_schema_version,
      canonical_payload=canonical_payload,
      config_snapshot_hash=stable_manifest_hash(
        t_assistant_config_snapshot_material(
          config_schema_version=config_schema_version,
          canonical_payload=canonical_payload,
          entry_authorization=entry_authorization,
          rollout_stage=rollout_stage,
          policy_version=policy_version,
          feature_schema_version=feature_schema_version,
          scorer_mode=scorer_mode,
          model_runtime_binding=model_runtime_binding,
        )
      ),
      entry_authorization=entry_authorization,
      rollout_stage=rollout_stage,
      policy_version=policy_version,
      feature_schema_version=feature_schema_version,
      scorer_mode=scorer_mode,
      model_runtime_binding=model_runtime_binding,
    )


@dataclass(frozen=True)
class TAssistantEntryReadinessProjection:
  readiness: TAssistantEntryReadiness
  reasons: tuple[str, ...]
  as_of: datetime

  def __post_init__(self) -> None:
    object.__setattr__(self, "readiness", TAssistantEntryReadiness(self.readiness))
    reasons = tuple(dict.fromkeys(str(item).strip() for item in self.reasons if str(item).strip()))
    if self.readiness is TAssistantEntryReadiness.READY and reasons:
      raise ValueError("READY T-assistant execution cannot carry blockers")
    if self.readiness is not TAssistantEntryReadiness.READY and not reasons:
      raise ValueError("non-READY T-assistant execution requires a reason")
    if self.as_of.tzinfo is None:
      raise ValueError("T-assistant readiness as_of must be timezone-aware")
    object.__setattr__(self, "reasons", reasons)


@dataclass(frozen=True)
class TAssistantExecution:
  execution_id: str
  config_id: str
  config_version_id: str
  frozen_config_version: int
  config_snapshot_hash: str
  account_id: str
  environment: ExecutionEnvironment
  entry_authorization: TAssistantEntryAuthorization
  rollout_stage: TAssistantRolloutStage
  status: TAssistantExecutionStatus
  readiness: TAssistantEntryReadinessProjection
  policy_version: str
  feature_schema_version: int
  scorer_mode: TAssistantScorerMode
  universe_revision: int = 0
  last_assigned_cycle_sequence: int = 0
  last_committed_cycle_sequence: int = 0
  checkpoint_revision: int = 0
  state_version: int = 1
  model_runtime_binding: Optional[Mapping[str, Any]] = None
  started_at: Optional[datetime] = None
  drain_requested_at: Optional[datetime] = None
  completed_at: Optional[datetime] = None

  def __post_init__(self) -> None:
    if not self.execution_id or not self.config_id or not self.config_version_id:
      raise ValueError("T-assistant execution requires stable ids")
    if not self.account_id or not self.policy_version:
      raise ValueError("T-assistant execution requires account and policy")
    object.__setattr__(self, "environment", ExecutionEnvironment(self.environment))
    object.__setattr__(
      self,
      "entry_authorization",
      TAssistantEntryAuthorization(self.entry_authorization),
    )
    object.__setattr__(self, "rollout_stage", TAssistantRolloutStage(self.rollout_stage))
    object.__setattr__(self, "status", TAssistantExecutionStatus(self.status))
    object.__setattr__(self, "scorer_mode", TAssistantScorerMode(self.scorer_mode))
    if self.frozen_config_version < 1 or self.feature_schema_version < 1:
      raise ValueError("T-assistant frozen versions must be positive")
    if self.state_version < 1:
      raise ValueError("T-assistant state_version must be positive")
    if min(
      self.universe_revision,
      self.last_assigned_cycle_sequence,
      self.last_committed_cycle_sequence,
      self.checkpoint_revision,
    ) < 0:
      raise ValueError("T-assistant revisions must be non-negative")
    if self.last_committed_cycle_sequence > self.last_assigned_cycle_sequence:
      raise ValueError("committed cycle sequence cannot exceed assigned sequence")
    if self.status is TAssistantExecutionStatus.RUNNING and self.started_at is None:
      raise ValueError("RUNNING T-assistant execution requires started_at")
    expected_readiness = {
      TAssistantExecutionStatus.RUNNING: TAssistantEntryReadiness.READY,
      TAssistantExecutionStatus.DRAINING: TAssistantEntryReadiness.DRAINING,
      TAssistantExecutionStatus.RECONCILE_REQUIRED: (
        TAssistantEntryReadiness.RECONCILE_REQUIRED
      ),
      TAssistantExecutionStatus.STOPPED: TAssistantEntryReadiness.BLOCKED,
      TAssistantExecutionStatus.FAILED: TAssistantEntryReadiness.BLOCKED,
    }.get(self.status)
    if expected_readiness is not None and self.readiness.readiness is not expected_readiness:
      raise ValueError("T_ASSISTANT_EXECUTION_READINESS_MISMATCH")
    if self.status is TAssistantExecutionStatus.DRAINING and self.drain_requested_at is None:
      raise ValueError("DRAINING T-assistant execution requires drain_requested_at")
    if self.status.terminal and self.completed_at is None:
      raise ValueError("terminal T-assistant execution requires completed_at")
    if self.environment is ExecutionEnvironment.BACKTEST and self.entry_authorization is TAssistantEntryAuthorization.AUTO:
      # Authorization is irrelevant to a Backtest Broker.  Requiring the safe
      # explicit value avoids presenting it as a live automatic permission.
      raise ValueError("BACKTEST T-assistant execution cannot carry AUTO authorization")

  @property
  def execution_ref(self) -> ExecutionOwnerRef:
    return ExecutionOwnerRef(
      ExecutionOwnerType.T_ASSISTANT_EXECUTION,
      self.execution_id,
    )

  @property
  def can_produce_entry(self) -> bool:
    return (
      self.status is TAssistantExecutionStatus.RUNNING
      and self.readiness.readiness is TAssistantEntryReadiness.READY
    )

  def transition(
    self,
    target: TAssistantExecutionStatus,
    *,
    at: datetime,
    has_unsettled_buy_work: bool,
  ) -> "TAssistantExecution":
    """Move lifecycle without treating downstream ExitPlans as BUY work."""

    target = TAssistantExecutionStatus(target)
    if at.tzinfo is None:
      raise ValueError("T-assistant transition time must be timezone-aware")
    if target not in _ALLOWED_TRANSITIONS[self.status]:
      raise ValueError("T_ASSISTANT_EXECUTION_TRANSITION_INVALID")
    if (
      target is TAssistantExecutionStatus.RUNNING
      and self.readiness.readiness is not TAssistantEntryReadiness.READY
    ):
      raise ValueError("T_ASSISTANT_ENTRY_NOT_READY")
    if target in {
      TAssistantExecutionStatus.STOPPED,
      TAssistantExecutionStatus.FAILED,
    } and has_unsettled_buy_work:
      raise ValueError("T_ASSISTANT_BUY_RECONCILE_REQUIRED")

    updates: dict[str, Any] = {"status": target, "state_version": self.state_version + 1}
    if target is TAssistantExecutionStatus.RUNNING:
      updates["started_at"] = self.started_at or at
    if target is TAssistantExecutionStatus.DRAINING:
      updates["drain_requested_at"] = self.drain_requested_at or at
      updates["readiness"] = TAssistantEntryReadinessProjection(
        readiness=TAssistantEntryReadiness.DRAINING,
        reasons=("T_ASSISTANT_EXECUTION_DRAINING",),
        as_of=at,
      )
    if target is TAssistantExecutionStatus.RECONCILE_REQUIRED:
      updates["readiness"] = TAssistantEntryReadinessProjection(
        readiness=TAssistantEntryReadiness.RECONCILE_REQUIRED,
        reasons=("T_ASSISTANT_BUY_RECONCILE_REQUIRED",),
        as_of=at,
      )
    if target.terminal:
      updates["completed_at"] = at
      updates["readiness"] = TAssistantEntryReadinessProjection(
        readiness=TAssistantEntryReadiness.BLOCKED,
        reasons=(f"T_ASSISTANT_EXECUTION_{target.value}",),
        as_of=at,
      )
    return replace(self, **updates)

  def with_readiness(
    self,
    projection: TAssistantEntryReadinessProjection,
  ) -> "TAssistantExecution":
    if self.status is TAssistantExecutionStatus.DRAINING and projection.readiness is not TAssistantEntryReadiness.DRAINING:
      raise ValueError("DRAINING execution readiness cannot be reopened")
    if self.status is TAssistantExecutionStatus.RECONCILE_REQUIRED and projection.readiness is not TAssistantEntryReadiness.RECONCILE_REQUIRED:
      raise ValueError("reconcile-required execution readiness cannot be reopened")
    if self.status.terminal and projection.readiness is TAssistantEntryReadiness.READY:
      raise ValueError("terminal execution readiness cannot be READY")
    return replace(self, readiness=projection, state_version=self.state_version + 1)

  def activate_ready(self, *, at: datetime) -> "TAssistantExecution":
    if self.status is not TAssistantExecutionStatus.WARMING:
      raise ValueError("only WARMING T-assistant execution can become ready")
    if at.tzinfo is None:
      raise ValueError("T-assistant readiness time must be timezone-aware")
    return replace(
      self,
      status=TAssistantExecutionStatus.RUNNING,
      readiness=TAssistantEntryReadinessProjection(
        readiness=TAssistantEntryReadiness.READY,
        reasons=(),
        as_of=at,
      ),
      started_at=self.started_at or at,
      state_version=self.state_version + 1,
    )

  def with_universe_revision(self, revision: int) -> "TAssistantExecution":
    normalized = int(revision)
    if normalized < self.universe_revision:
      raise ValueError("T-assistant universe revision cannot move backward")
    if normalized == self.universe_revision:
      return self
    return replace(
      self,
      universe_revision=normalized,
      state_version=self.state_version + 1,
    )


@dataclass(frozen=True)
class TAssistantExecutionEvent:
  execution_id: str
  event_key: str
  event_type: str
  occurred_at: datetime
  payload: Mapping[str, Any]
  source_type: Optional[str] = None
  source_id: Optional[str] = None

  def __post_init__(self) -> None:
    if not self.execution_id or not self.event_key or not self.event_type:
      raise ValueError("T-assistant execution event requires identity and type")
    if self.occurred_at.tzinfo is None:
      raise ValueError("T-assistant event occurred_at must be timezone-aware")
    canonical = canonical_json_payload(self.payload)
    object.__setattr__(self, "payload", json.loads(canonical))


__all__ = [
  "TAssistantConfigVersion",
  "TAssistantEntryAuthorization",
  "TAssistantEntryReadiness",
  "TAssistantEntryReadinessProjection",
  "TAssistantExecution",
  "TAssistantExecutionEvent",
  "TAssistantExecutionStatus",
  "TAssistantRolloutStage",
  "TAssistantScorerMode",
  "TAssistantSymbolLifecycle",
  "TDecisionCycleStatus",
  "canonical_json_payload",
  "stable_manifest_hash",
  "t_assistant_config_snapshot_material",
]
