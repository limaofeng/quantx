"""Account-level execution safety projection shared by every live workflow."""

from __future__ import annotations

from typing import Any

from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationsService,
)

_T_TRADE_ONLY_CHECKS = frozenset({"T_TRADE_LIVE_ENABLED"})
_RISK_REDUCTION_CHECKS = frozenset(
  {
    "SERVER_REAL_TRADING_ENABLED",
    "ACCOUNT_ALLOWLISTED",
    "ENGINE_READY",
    "LIVE_AGENT_READY",
    "AGENT_MODE_LIVE",
    "PROTOCOL_1_1",
    "ROLLOUT_CONFIGURED",
    "SNAPSHOT_RECONCILED",
    "SNAPSHOT_FRESH",
  }
)


def _unique_messages(values: list[str]) -> list[str]:
  return list(dict.fromkeys(value for value in values if value))


def project_account_execution_safety(readiness: dict[str, Any]) -> dict[str, Any]:
  """Remove assistant-specific policy and derive account execution capabilities."""

  checks = [
    dict(item)
    for item in list(readiness.get("checks") or [])
    if str(item.get("code") or "") not in _T_TRADE_ONLY_CHECKS
  ]
  failed_checks = [item for item in checks if not bool(item.get("passed"))]
  observation_failures = [
    item
    for item in failed_checks
    if str(item.get("scope") or "") == "PREPARATION"
  ]
  reduction_failures = [
    item
    for item in checks
    if str(item.get("code") or "") in _RISK_REDUCTION_CHECKS
    and not bool(item.get("passed"))
  ]

  kill_switch = bool(readiness.get("kill_switch"))
  stage = str(readiness.get("stage") or "SHADOW").upper()
  rollout_enabled = bool(readiness.get("rollout_enabled"))
  account_window_active = bool(readiness.get("controlled_window_active"))
  increase_checks_ready = not failed_checks
  can_reduce_risk = not reduction_failures
  can_increase_risk = bool(
    increase_checks_ready
    and rollout_enabled
    and stage in {"CANARY", "LIVE"}
    and not kill_switch
  )

  if kill_switch:
    health_status = "KILLED"
  elif observation_failures:
    health_status = "BLOCKED"
  else:
    health_status = "HEALTHY"

  if kill_switch:
    execution_mode = "KILLED"
  elif can_increase_risk:
    execution_mode = "TRADING"
  elif can_reduce_risk:
    execution_mode = "REDUCE_ONLY"
  else:
    execution_mode = "OBSERVE_ONLY"

  blocked_reasons = [
    str(item.get("message") or item.get("code") or "账户实盘门禁未通过")
    for item in failed_checks
  ]
  if increase_checks_ready and not can_increase_risk:
    blocked_reasons.append("账户自动执行尚未启用 CANARY/LIVE")
  blocked_reasons = _unique_messages(blocked_reasons)

  if kill_switch:
    summary = (
      "账户紧急停止已触发；仍仅允许风险降低型卖出"
      if can_reduce_risk
      else "账户紧急停止已触发；实盘执行已关闭"
    )
  elif can_increase_risk:
    summary = "账户事实与新增风险门禁均已通过"
  elif can_reduce_risk:
    summary = "账户事实已收敛；当前仅允许减仓"
  elif health_status == "HEALTHY":
    summary = "账户观察与对账正常；实盘执行保持关闭"
  else:
    summary = blocked_reasons[0] if blocked_reasons else "账户安全状态未就绪"

  return {
    "account_id": str(readiness.get("account_id") or ""),
    "health_status": health_status,
    "execution_mode": execution_mode,
    "can_increase_risk": can_increase_risk,
    "can_reduce_risk": can_reduce_risk,
    "can_activate_automation": increase_checks_ready and not kill_switch,
    "summary": summary,
    "blocked_reasons": blocked_reasons,
    "checks": [
      {
        **item,
        "scope": (
          "OBSERVATION"
          if str(item.get("scope") or "") == "PREPARATION"
          else "INCREASE_RISK"
        ),
      }
      for item in checks
    ],
    "engine_status": str(readiness.get("engine_status") or "OFFLINE"),
    "agent_status": str(readiness.get("agent_status") or "OFFLINE"),
    "agent_mode": str(readiness.get("agent_mode") or "offline"),
    "protocol_version": str(readiness.get("protocol_version") or ""),
    "reconcile_status": str(readiness.get("reconcile_status") or "UNKNOWN"),
    "kill_switch": kill_switch,
    "execution_window_active": account_window_active,
    "snapshot_at": readiness.get("snapshot_at"),
    "reconciliation_age_seconds": readiness.get("reconciliation_age_seconds"),
    "queued_command_count": int(readiness.get("queued_command_count") or 0),
    "queue_delay_seconds": float(readiness.get("queue_delay_seconds") or 0),
    "dead_letter_count": int(readiness.get("dead_letter_count") or 0),
    "unresolved_critical_alert_count": int(
      readiness.get("unresolved_critical_alert_count") or 0
    ),
    "external_order_count": int(readiness.get("external_order_count") or 0),
    "external_trade_count": int(readiness.get("external_trade_count") or 0),
    "new_external_order_count": int(
      readiness.get("new_external_order_count") or 0
    ),
    "new_external_trade_count": int(
      readiness.get("new_external_trade_count") or 0
    ),
    "working_external_order_count": int(
      readiness.get("working_external_order_count") or 0
    ),
    "last_backup_at": readiness.get("last_backup_at"),
    "checked_at": readiness.get("checked_at"),
  }


class AccountExecutionSafetyService:
  """Expose account execution capability without T-assistant policy switches."""

  def __init__(self, operations_service: TTradeOperationsService | None = None) -> None:
    self.operations_service = operations_service or TTradeOperationsService()

  async def status(self, account_id: str) -> dict[str, Any]:
    readiness = await self.operations_service.readiness(account_id)
    return project_account_execution_safety(readiness)
