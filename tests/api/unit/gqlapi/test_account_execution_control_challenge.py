import pytest
from quantx_api.gqlapi.account_execution_control import (
  _safety_binding,
  _validate_action,
  normalize_account_execution_control_request,
)
from quantx_api.gqlapi.trade_approval import TradeApprovalChallengeError
from quantx_api.gqlapi.types.trading_safety_types import (
  AccountExecutionControlAction,
)


def _request(
  action: AccountExecutionControlAction,
  *,
  state_version: int = 4,
  snapshot_id: str = "",
  reason: str = "",
  client_order_id: str = "",
  quarantine_reason: str = "",
):
  return normalize_account_execution_control_request(
    account_id="ACCOUNT-1",
    action=action,
    state_version=state_version,
    snapshot_id=snapshot_id,
    reason=reason,
    idempotency_key=f"account-{action.value.lower()}",
    client_order_id=client_order_id,
    quarantine_reason=quarantine_reason,
  )


def _safety(**overrides):
  values = {
    "authorization_state": "DISABLED",
    "state_version": 4,
    "snapshot_id": "snapshot-1",
    "snapshot_hash": "a" * 64,
    "execution_window_active": False,
    "can_activate_automation": True,
    "blocked_reasons": [],
    "checks": [],
  }
  values.update(overrides)
  return values


def test_account_control_actions_are_independent_from_t_rollout_actions() -> None:
  assert {item.value for item in AccountExecutionControlAction} == {
    "BEGIN_CONTROLLED_WINDOW",
    "ENABLE_RISK_INCREASE",
    "PAUSE_RISK_INCREASE",
    "KILL_SWITCH",
    "CLEAR_KILL_SWITCH",
    "REPAIR_QUARANTINED_ORDER",
  }


def test_controlled_window_requires_and_binds_latest_snapshot() -> None:
  request = _request(
    AccountExecutionControlAction.BEGIN_CONTROLLED_WINDOW,
    snapshot_id="snapshot-1",
  )
  _validate_action(request, _safety())

  with pytest.raises(TradeApprovalChallengeError) as error:
    _validate_action(request, _safety(snapshot_id="snapshot-2"))
  assert error.value.code == "SNAPSHOT_CHANGED"


def test_enable_requires_account_readiness_but_pause_and_kill_do_not() -> None:
  blocked = _safety(
    can_activate_automation=False,
    blocked_reasons=["account facts are stale"],
  )
  with pytest.raises(TradeApprovalChallengeError) as error:
    _validate_action(
      _request(AccountExecutionControlAction.ENABLE_RISK_INCREASE),
      blocked,
    )
  assert error.value.code == "ACCOUNT_EXECUTION_NOT_READY"

  _validate_action(
    _request(
      AccountExecutionControlAction.PAUSE_RISK_INCREASE,
      reason="operator pause",
    ),
    blocked,
  )
  _validate_action(
    _request(AccountExecutionControlAction.KILL_SWITCH, reason="emergency"),
    blocked,
  )


def test_clear_kill_switch_requires_killed_state() -> None:
  request = _request(AccountExecutionControlAction.CLEAR_KILL_SWITCH)
  with pytest.raises(TradeApprovalChallengeError) as error:
    _validate_action(request, _safety())
  assert error.value.code == "ACCOUNT_NOT_KILLED"
  _validate_action(request, _safety(authorization_state="KILLED"))


def test_pause_cannot_downgrade_a_hard_kill() -> None:
  request = _request(
    action=AccountExecutionControlAction.PAUSE_RISK_INCREASE,
    reason="operator pause",
  )

  with pytest.raises(TradeApprovalChallengeError) as error:
    _validate_action(request, _safety(authorization_state="KILLED"))

  assert error.value.code == "ACCOUNT_KILL_SWITCH_ACTIVE"


def test_safety_fingerprint_binding_has_no_t_feature_state() -> None:
  binding = _safety_binding(
    _safety(
      stage="LIVE",
      rollout_enabled=True,
      policy_version=9,
    )
  )
  assert "stage" not in binding
  assert "rollout_enabled" not in binding
  assert "policy_version" not in binding


def test_repair_quarantined_order_binds_candidate_snapshot_and_state() -> None:
  request = _request(
    AccountExecutionControlAction.REPAIR_QUARANTINED_ORDER,
    snapshot_id="snapshot-1",
    reason="operator verified broker terminal state",
    client_order_id="client-1",
    quarantine_reason="ACCOUNT_WIDE_STALE_SELL",
  )
  candidate = {
    "client_order_id": "client-1",
    "plan_id": "plan-1",
    "intent_id": "intent-1",
    "quarantine_reason": "ACCOUNT_WIDE_STALE_SELL",
    "broker_order_id": "broker-1",
    "repairable": True,
    "blocked_reason": "",
  }
  _validate_action(request, _safety(quarantined_orders=[candidate]))

  with pytest.raises(TradeApprovalChallengeError) as error:
    _validate_action(
      request,
      _safety(
        snapshot_id="snapshot-2",
        quarantined_orders=[candidate],
      ),
    )
  assert error.value.code == "SNAPSHOT_CHANGED"

  fingerprint = _safety_binding(_safety(quarantined_orders=[candidate]))
  assert fingerprint["quarantined_orders"] == [
    {
      "client_order_id": "client-1",
      "plan_id": "plan-1",
      "intent_id": "intent-1",
      "quarantine_reason": "ACCOUNT_WIDE_STALE_SELL",
      "broker_order_id": "broker-1",
      "repairable": True,
      "blocked_reason": "",
    }
  ]
