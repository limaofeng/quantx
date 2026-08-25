from types import SimpleNamespace

import pytest
from quantx_api.gqlapi.t_trade_control import (
  _ACTIVATION_GATE_CODES,
  _operation_marker_exists,
  _rollout_binding,
  _validate_action_readiness,
  normalize_t_trade_control_request,
)
from quantx_api.gqlapi.trade_approval import TradeApprovalChallengeError
from quantx_api.gqlapi.types.t_trade_types import (
  TTradeControlAction,
  TTradeRolloutTarget,
)
from quantx_infrastructure.models.agent_runtime import TTradeRolloutEvent


def _request(
  action: TTradeControlAction = TTradeControlAction.ACTIVATE_CANARY,
):
  return normalize_t_trade_control_request(
    account_id="ACCOUNT-1",
    action=action,
    policy_version=3,
    snapshot_id="snapshot-1",
    target_stage=(
      TTradeRolloutTarget.CANARY
      if action == TTradeControlAction.ACTIVATE_CANARY
      else TTradeRolloutTarget.LIVE
    ),
    reason="operator reviewed rollout evidence",
    idempotency_key=f"activate-{action.value.lower()}",
  )


def _rollout(stage: str = "SHADOW"):
  return SimpleNamespace(
    stage=stage,
    enabled=False,
    policy_version=3,
    acknowledged_policy_version=0,
  )


def _readiness(*, failed: set[str] | None = None):
  failed = failed or set()
  return {
    "status": "READY",
    "stage": "SHADOW",
    "policy_version": 3,
    "snapshot_id": "snapshot-1",
    "snapshot_hash": "a" * 64,
    "ready_live_agent_count": 1,
    "agent_mode": "live",
    "protocol_version": "1.1",
    "reconcile_status": "READY",
    "kill_switch": False,
    "controlled_window_active": True,
    "controlled_window_snapshot_id": "snapshot-1",
    "checks": [
      {
        "code": code,
        "passed": code not in failed,
        "message": f"{code} failed" if code in failed else "",
        "scope": "FEATURE" if code.startswith("T_TRADE_") else "ACCOUNT",
      }
      for code in sorted(_ACTIVATION_GATE_CODES)
    ],
  }


def test_graphql_contract_exposes_activation_actions_only() -> None:
  assert {item.value for item in TTradeControlAction} == {
    "ACTIVATE_CANARY",
    "ACTIVATE_LIVE",
  }


def test_action_target_mismatch_is_rejected() -> None:
  with pytest.raises(TradeApprovalChallengeError) as error:
    normalize_t_trade_control_request(
      account_id="ACCOUNT-1",
      action=TTradeControlAction.ACTIVATE_CANARY,
      policy_version=3,
      snapshot_id="snapshot-1",
      target_stage=TTradeRolloutTarget.LIVE,
      reason="",
      idempotency_key="mismatch",
    )
  assert error.value.code == "CONTROL_ACTION_TARGET_MISMATCH"


def test_t_rollout_binding_contains_feature_state_only() -> None:
  binding = _rollout_binding(_rollout())
  assert binding == {
    "exists": True,
    "stage": "SHADOW",
    "enabled": False,
    "policy_version": 3,
    "acknowledged_policy_version": 0,
  }
  assert "kill_switch" not in binding
  assert "reconcile_status" not in binding
  assert "controlled_window_active" not in binding


def test_t_feature_switch_blocks_t_activation() -> None:
  with pytest.raises(TradeApprovalChallengeError, match="T_TRADE_LIVE_ENABLED failed"):
    _validate_action_readiness(
      _request(),
      _readiness(failed={"T_TRADE_LIVE_ENABLED"}),
      _rollout(),
    )


def test_account_gate_still_blocks_t_activation_composition() -> None:
  with pytest.raises(
    TradeApprovalChallengeError, match="ACCOUNT_RISK_INCREASE_AUTHORIZED"
  ):
    _validate_action_readiness(
      _request(),
      _readiness(failed={"ACCOUNT_RISK_INCREASE_AUTHORIZED"}),
      _rollout(),
    )


class _Db:
  def __init__(self, event):
    self.event = event

  async def get(self, model, key):
    assert model is TTradeRolloutEvent
    assert key == "challenge-1"
    return self.event


@pytest.mark.asyncio
async def test_operation_marker_is_bound_to_t_activation() -> None:
  event = TTradeRolloutEvent(
    event_id="challenge-1",
    account_id="ACCOUNT-1",
    event_type="CANARY_ACTIVATED",
    snapshot_id="snapshot-1",
    details={
      "operationId": "challenge-1",
      "targetStage": "CANARY",
      "policyVersion": 3,
    },
  )
  assert await _operation_marker_exists(
    _Db(event),
    challenge_id="challenge-1",
    request=_request(),
  )
