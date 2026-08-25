from quantx_infrastructure.services.account_execution_safety_service import (
  project_account_execution_safety,
)


def _readiness(
  *,
  failed: set[str] | None = None,
  authorization_state: str = "ENABLED",
):
  failed = failed or set()
  checks = [
    ("SERVER_REAL_TRADING_ENABLED", "AUTOMATION"),
    ("T_TRADE_LIVE_ENABLED", "AUTOMATION"),
    ("ACCOUNT_ALLOWLISTED", "AUTOMATION"),
    ("ENGINE_READY", "OBSERVATION"),
    ("LIVE_AGENT_READY", "OBSERVATION"),
    ("AGENT_MODE_LIVE", "OBSERVATION"),
    ("PROTOCOL_1_1", "OBSERVATION"),
    ("EXECUTION_CONTROL_CONFIGURED", "OBSERVATION"),
    ("SNAPSHOT_RECONCILED", "OBSERVATION"),
    ("SNAPSHOT_FRESH", "OBSERVATION"),
    ("SNAPSHOT_ACTIVITY_CLASSIFIED", "OBSERVATION"),
    ("RECENT_BACKUP", "AUTOMATION"),
    ("NO_CRITICAL_ALERTS", "OBSERVATION"),
    ("NO_DEAD_LETTERS", "OBSERVATION"),
    ("CONTROLLED_WINDOW_ACTIVE", "AUTOMATION"),
    ("NO_EXTERNAL_BROKER_ACTIVITY", "AUTOMATION"),
    ("KILL_SWITCH_CLEAR", "OBSERVATION"),
    ("ACCOUNT_RISK_INCREASE_AUTHORIZED", "INCREASE_RISK"),
  ]
  return {
    "account_id": "account-1",
    "authorization_state": authorization_state,
    "controlled_window_active": "CONTROLLED_WINDOW_ACTIVE" not in failed,
    "reconcile_status": "READY",
    "checks": [
      {
        "code": code,
        "passed": code not in failed,
        "message": "" if code not in failed else f"{code} failed",
        "scope": scope,
      }
      for code, scope in checks
    ],
  }


def test_t_trade_switch_does_not_change_account_execution_capability() -> None:
  status = project_account_execution_safety(_readiness(failed={"T_TRADE_LIVE_ENABLED"}))

  assert status["health_status"] == "HEALTHY"
  assert status["execution_mode"] == "TRADING"
  assert status["can_increase_risk"] is True
  assert all(item["code"] != "T_TRADE_LIVE_ENABLED" for item in status["checks"])


def test_missing_account_window_is_a_healthy_reduce_only_state() -> None:
  status = project_account_execution_safety(
    _readiness(failed={"CONTROLLED_WINDOW_ACTIVE"})
  )

  assert status["health_status"] == "HEALTHY"
  assert status["execution_mode"] == "REDUCE_ONLY"
  assert status["can_reduce_risk"] is True
  assert status["can_increase_risk"] is False
  assert status["summary"] == "账户事实已收敛；当前仅允许减仓"


def test_stale_snapshot_blocks_both_execution_capabilities() -> None:
  status = project_account_execution_safety(_readiness(failed={"SNAPSHOT_FRESH"}))

  assert status["health_status"] == "BLOCKED"
  assert status["execution_mode"] == "OBSERVE_ONLY"
  assert status["can_reduce_risk"] is False
  assert status["can_increase_risk"] is False


def test_offline_engine_cannot_be_presented_as_reduce_only() -> None:
  status = project_account_execution_safety(_readiness(failed={"ENGINE_READY"}))

  assert status["health_status"] == "BLOCKED"
  assert status["execution_mode"] == "OBSERVE_ONLY"
  assert status["can_reduce_risk"] is False
  assert status["can_increase_risk"] is False


def test_kill_switch_is_explicit_while_preserving_reduction_capability() -> None:
  status = project_account_execution_safety(
    _readiness(
      failed={"KILL_SWITCH_CLEAR", "ACCOUNT_RISK_INCREASE_AUTHORIZED"},
      authorization_state="KILLED",
    )
  )

  assert status["health_status"] == "KILLED"
  assert status["execution_mode"] == "KILLED"
  assert status["can_reduce_risk"] is True
  assert status["can_increase_risk"] is False
