from datetime import UTC, datetime

import pytest
from quantx_application.t_trade_v3.decision_cycle_use_cases import (
  TAssistantCyclePolicy,
)
from quantx_application.t_trade_v3.execution_use_cases import (
  TAssistantExecutionLifecycle,
)
from quantx_contracts import ExecutionEnvironment
from quantx_domain.trading.t_assistant_execution import (
  TAssistantEntryAuthorization,
  TAssistantEntryReadiness,
  TAssistantEntryReadinessProjection,
  TAssistantExecution,
  TAssistantExecutionStatus,
  TAssistantRolloutStage,
  TAssistantScorerMode,
)

NOW = datetime(2026, 9, 3, 9, 30, tzinfo=UTC)


class TransitionPort:
  def __init__(self) -> None:
    self.calls = []

  async def save_transition_with_event(
    self,
    execution,
    *,
    expected_state_version,
    event,
  ):
    self.calls.append((execution, expected_state_version, event))


def _warming_execution() -> TAssistantExecution:
  return TAssistantExecution(
    execution_id="execution-1",
    config_id="config-1",
    config_version_id="version-1",
    frozen_config_version=1,
    config_snapshot_hash="a" * 64,
    account_id="account-1",
    environment=ExecutionEnvironment.PAPER,
    entry_authorization=TAssistantEntryAuthorization.MANUAL_CONFIRM,
    rollout_stage=TAssistantRolloutStage.CANARY,
    status=TAssistantExecutionStatus.WARMING,
    readiness=TAssistantEntryReadinessProjection(
      readiness=TAssistantEntryReadiness.WARMING,
      reasons=("T_REWARM_REQUIRED",),
      as_of=NOW,
    ),
    policy_version="policy-v1",
    feature_schema_version=1,
    scorer_mode=TAssistantScorerMode.RULE_ONLY,
  )


async def test_execution_lifecycle_persists_transition_with_same_event_boundary():
  port = TransitionPort()
  lifecycle = TAssistantExecutionLifecycle(port)
  warming = _warming_execution()

  revised = await lifecycle.revise_universe(
    warming,
    universe_revision=1,
    at=NOW,
    payload={"instruments": ["600000.SH"]},
  )
  activated = await lifecycle.activate_ready(
    revised,
    at=NOW,
    payload={"paper_shadow_only": True},
  )

  assert activated.status is TAssistantExecutionStatus.RUNNING
  assert activated.readiness.readiness is TAssistantEntryReadiness.READY
  assert [call[1] for call in port.calls] == [1, 2]
  assert [call[2].event_type for call in port.calls] == [
    "EXECUTION_UNIVERSE_REVISED",
    "EXECUTION_ENTRY_READY",
  ]


def test_cycle_policy_refuses_threshold_drift() -> None:
  assert TAssistantCyclePolicy().lease_seconds == 10
  with pytest.raises(ValueError, match="frozen"):
    TAssistantCyclePolicy(lease_seconds=11)
