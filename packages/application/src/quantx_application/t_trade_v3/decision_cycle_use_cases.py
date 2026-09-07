"""Pure input identity and fixed lease policy for T-assistant cycles."""

from __future__ import annotations

from dataclasses import dataclass

from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_domain.trading.t_assistant_market_state import TDecisionSnapshot

T_ASSISTANT_CYCLE_POLICY_VERSION = "t_assistant_cycle_v1"
T_ASSISTANT_CYCLE_LEASE_SECONDS = 10
T_ASSISTANT_CYCLE_LEASE_RENEW_SECONDS = 3
T_ASSISTANT_ENTRY_CYCLE_TTL_SECONDS = 15


@dataclass(frozen=True)
class TAssistantCyclePolicy:
  policy_version: str = T_ASSISTANT_CYCLE_POLICY_VERSION
  lease_seconds: int = T_ASSISTANT_CYCLE_LEASE_SECONDS
  renew_seconds: int = T_ASSISTANT_CYCLE_LEASE_RENEW_SECONDS
  entry_ttl_seconds: int = T_ASSISTANT_ENTRY_CYCLE_TTL_SECONDS

  def __post_init__(self) -> None:
    if (
      self.policy_version != T_ASSISTANT_CYCLE_POLICY_VERSION
      or self.lease_seconds != T_ASSISTANT_CYCLE_LEASE_SECONDS
      or self.renew_seconds != T_ASSISTANT_CYCLE_LEASE_RENEW_SECONDS
      or self.entry_ttl_seconds != T_ASSISTANT_ENTRY_CYCLE_TTL_SECONDS
    ):
      raise ValueError("P3 cycle policy is frozen at v1")


def decision_key_for_snapshot(snapshot: TDecisionSnapshot) -> str:
  """Stable decision identity containing every frozen input safety axis."""

  return stable_manifest_hash(
    {
      "execution_ref": snapshot.execution_ref.to_dict(),
      "stream_id": snapshot.stream_id,
      "continuity_generation": snapshot.continuity_generation,
      "fence_sequence": snapshot.fence_sequence,
      "market_delta_manifest_hash": snapshot.market_delta_manifest_hash,
      "reducer_cursor_manifest_hash": snapshot.reducer_cursor_manifest_hash,
      "config_version": snapshot.config_version,
      "config_snapshot_hash": snapshot.config_snapshot_hash,
      "policy_version": snapshot.policy_version,
      "feature_schema_version": snapshot.feature_schema_version,
      "execution_status": snapshot.execution_status.value,
      "entry_readiness": snapshot.entry_readiness.value,
      "entry_readiness_as_of": snapshot.entry_readiness_as_of.isoformat(),
      "universe_revision": snapshot.universe_revision,
      "scorer_mode": snapshot.scorer_mode,
      "model_runtime_binding_hash": snapshot.model_runtime_binding_hash,
      "canonical_decision_payload_hash": snapshot.snapshot_hash,
    }
  )


__all__ = [
  "TAssistantCyclePolicy",
  "T_ASSISTANT_CYCLE_LEASE_RENEW_SECONDS",
  "T_ASSISTANT_CYCLE_LEASE_SECONDS",
  "T_ASSISTANT_CYCLE_POLICY_VERSION",
  "T_ASSISTANT_ENTRY_CYCLE_TTL_SECONDS",
  "decision_key_for_snapshot",
]
