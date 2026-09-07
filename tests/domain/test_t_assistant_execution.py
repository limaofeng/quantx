from datetime import UTC, datetime

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef, ExecutionOwnerType
from quantx_domain.strategies.base import (
  ExitPlanIntentOrigin,
  StrategyCadence,
  StrategyInput,
  TAssistantExecutionIntentOrigin,
  TradeIntent,
  TradeIntentDirection,
)
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  TAssistantEntryAuthorization,
  TAssistantEntryReadiness,
  TAssistantEntryReadinessProjection,
  TAssistantExecution,
  TAssistantExecutionStatus,
  TAssistantRolloutStage,
  TAssistantScorerMode,
  stable_manifest_hash,
  t_assistant_config_snapshot_material,
)

NOW = datetime(2026, 9, 3, 9, 30, tzinfo=UTC)


def _execution(status=TAssistantExecutionStatus.DRAINING) -> TAssistantExecution:
  return TAssistantExecution(
    execution_id="execution-1",
    config_id="config-1",
    config_version_id="config-version-1",
    frozen_config_version=1,
    config_snapshot_hash="a" * 64,
    account_id="account-1",
    environment=ExecutionEnvironment.PAPER,
    entry_authorization=TAssistantEntryAuthorization.MANUAL_CONFIRM,
    rollout_stage=TAssistantRolloutStage.CANARY,
    status=status,
    readiness=TAssistantEntryReadinessProjection(
      readiness=TAssistantEntryReadiness.DRAINING,
      reasons=("T_ASSISTANT_EXECUTION_DRAINING",),
      as_of=NOW,
    ),
    policy_version="policy-v1",
    feature_schema_version=1,
    scorer_mode=TAssistantScorerMode.RULE_ONLY,
    drain_requested_at=NOW,
  )


def test_config_version_hash_is_canonical_and_covers_all_safety_axes():
  payload = {"portfolio_policy": {"max": 3}, "symbol_rule_policy": {"b": 2}}
  version = TAssistantConfigVersion.create(
    config_version_id="version-1",
    config_id="config-1",
    version=1,
    config_schema_version="t_assistant_config_v1",
    canonical_payload=payload,
    entry_authorization=TAssistantEntryAuthorization.AUTO,
    rollout_stage=TAssistantRolloutStage.CANARY,
    policy_version="policy-v1",
    feature_schema_version=1,
  )

  assert version.config_snapshot_hash == stable_manifest_hash(
    t_assistant_config_snapshot_material(
      config_schema_version="t_assistant_config_v1",
      canonical_payload=payload,
      entry_authorization=TAssistantEntryAuthorization.AUTO,
      rollout_stage=TAssistantRolloutStage.CANARY,
      policy_version="policy-v1",
      feature_schema_version=1,
      scorer_mode=TAssistantScorerMode.RULE_ONLY,
      model_runtime_binding=None,
    )
  )
  manual = TAssistantConfigVersion.create(
    config_version_id="version-2",
    config_id="config-1",
    version=2,
    config_schema_version="t_assistant_config_v1",
    canonical_payload=payload,
    entry_authorization=TAssistantEntryAuthorization.MANUAL_CONFIRM,
    rollout_stage=TAssistantRolloutStage.CANARY,
    policy_version="policy-v1",
    feature_schema_version=1,
  )
  assert manual.config_snapshot_hash != version.config_snapshot_hash
  assert version.entry_authorization is TAssistantEntryAuthorization.AUTO
  assert version.rollout_stage is TAssistantRolloutStage.CANARY
  assert version.scorer_mode is TAssistantScorerMode.RULE_ONLY


def test_rule_only_rejects_model_binding_and_active_requires_one():
  common = dict(
    config_version_id="version-1",
    config_id="config-1",
    version=1,
    config_schema_version="v1",
    canonical_payload={"policy": "v1"},
    entry_authorization=TAssistantEntryAuthorization.MANUAL_CONFIRM,
    rollout_stage=TAssistantRolloutStage.CANARY,
    policy_version="policy-v1",
    feature_schema_version=1,
  )
  with pytest.raises(ValueError, match="RULE_ONLY"):
    TAssistantConfigVersion.create(
      **common,
      scorer_mode=TAssistantScorerMode.RULE_ONLY,
      model_runtime_binding={"binding_hash": "b" * 64},
    )
  with pytest.raises(ValueError, match="requires a model"):
    TAssistantConfigVersion.create(
      **common,
      scorer_mode=TAssistantScorerMode.ACTIVE,
    )


def test_stop_guard_only_checks_source_execution_buy_work_not_downstream_exit_plan():
  execution = _execution()

  stopped = execution.transition(
    TAssistantExecutionStatus.STOPPED,
    at=NOW,
    has_unsettled_buy_work=False,
  )

  assert stopped.status is TAssistantExecutionStatus.STOPPED
  assert stopped.completed_at == NOW


def test_stop_guard_blocks_unsettled_buy_approval_pending_or_unknown_work():
  with pytest.raises(ValueError, match="T_ASSISTANT_BUY_RECONCILE_REQUIRED"):
    _execution().transition(
      TAssistantExecutionStatus.STOPPED,
      at=NOW,
      has_unsettled_buy_work=True,
    )


def test_warming_activation_sets_ready_in_one_state_version() -> None:
  execution = TAssistantExecution(
    **{
      **_execution().transition(
        TAssistantExecutionStatus.FAILED,
        at=NOW,
        has_unsettled_buy_work=False,
      ).__dict__,
      "status": TAssistantExecutionStatus.WARMING,
      "readiness": TAssistantEntryReadinessProjection(
        readiness=TAssistantEntryReadiness.WARMING,
        reasons=("T_REWARM_REQUIRED",),
        as_of=NOW,
      ),
      "completed_at": None,
      "drain_requested_at": None,
      "state_version": 4,
    }
  )

  activated = execution.activate_ready(at=NOW)

  assert activated.status is TAssistantExecutionStatus.RUNNING
  assert activated.readiness.readiness is TAssistantEntryReadiness.READY
  assert activated.state_version == 5


def test_t_assistant_intent_has_no_strategy_run_identity():
  origin = TAssistantExecutionIntentOrigin(
    execution_id="execution-1",
    producer_id="ashare-intraday-t-assistant",
    candidate_id="candidate-1",
    cycle_id="cycle-1",
  )
  intent = TradeIntent(
    strategy_id="ashare-intraday-t-assistant",
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="T_TRADE_ENTRY",
    target_amount=10_000,
    origin=origin,
    execution_ref=origin.execution_ref,
  )

  assert intent.run_id == ""
  assert intent.execution_ref == ExecutionOwnerRef(
    ExecutionOwnerType.T_ASSISTANT_EXECUTION,
    "execution-1",
  )


def test_exit_plan_source_accepts_entry_owners_but_rejects_exit_plan_self_reference():
  for owner_type in (
    ExecutionOwnerType.STRATEGY_RUN,
    ExecutionOwnerType.T_ASSISTANT_EXECUTION,
    ExecutionOwnerType.ENTRY_PLAN,
    ExecutionOwnerType.BOARD_ASSISTANT_EXECUTION,
    ExecutionOwnerType.MANUAL_COMMAND,
  ):
    origin = ExitPlanIntentOrigin(
      plan_id="plan-1",
      source_execution_ref=ExecutionOwnerRef(owner_type, "source-1"),
    )
    assert origin.source_execution_ref.owner_type is owner_type
  with pytest.raises(ValueError, match="invalid owner type"):
    ExitPlanIntentOrigin(
      plan_id="plan-1",
      source_execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.EXIT_PLAN,
        "plan-1",
      ),
    )


def test_snapshot_input_requires_typed_snapshot_before_step():
  with pytest.raises(TypeError, match="TDecisionSnapshot"):
    StrategyInput(
      strategy_id="ashare-intraday-t-assistant",
      timestamp=NOW,
      cadence=StrategyCadence.SNAPSHOT,
      instrument_code=None,
      market_data={},
      execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.T_ASSISTANT_EXECUTION,
        "execution-1",
      ),
    )
