"""Resolvers for account-level execution safety."""

from __future__ import annotations

from datetime import datetime, timezone

from quantx_infrastructure.services.account_execution_safety_service import (
  AccountExecutionSafetyService,
)

from ..types.trading_safety_types import (
  AccountExecutionHealthStatus,
  AccountExecutionSafety,
  AccountExecutionSafetyCheck,
)


def _aware(value: datetime | str | None) -> datetime | None:
  if isinstance(value, str):
    normalized = value.strip().replace("Z", "+00:00")
    if not normalized:
      return None
    value = datetime.fromisoformat(normalized)
  if value is None or value.tzinfo is not None:
    return value
  return value.replace(tzinfo=timezone.utc)


class AccountExecutionSafetyResolver:
  service = AccountExecutionSafetyService()

  @classmethod
  async def status(cls, account_id: str) -> AccountExecutionSafety:
    payload = await cls.service.status(account_id)
    return cls.from_payload(payload)

  @classmethod
  def from_payload(cls, payload: dict) -> AccountExecutionSafety:
    return AccountExecutionSafety(
      account_id=str(payload["account_id"]),
      authorization_state=str(payload["authorization_state"]),
      state_version=int(payload["state_version"]),
      health_status=AccountExecutionHealthStatus(str(payload["health_status"])),
      execution_mode=str(payload["execution_mode"]),
      can_increase_risk=bool(payload["can_increase_risk"]),
      can_reduce_risk=bool(payload["can_reduce_risk"]),
      can_activate_automation=bool(payload["can_activate_automation"]),
      summary=str(payload["summary"]),
      blocked_reasons=list(payload.get("blocked_reasons") or []),
      checks=[
        AccountExecutionSafetyCheck(
          code=str(item.get("code") or ""),
          passed=bool(item.get("passed")),
          message=str(item.get("message") or ""),
          scope=str(item.get("scope") or "INCREASE_RISK"),
        )
        for item in list(payload.get("checks") or [])
      ],
      engine_status=str(payload.get("engine_status") or "OFFLINE"),
      agent_status=str(payload.get("agent_status") or "OFFLINE"),
      agent_mode=str(payload.get("agent_mode") or "offline"),
      protocol_version=str(payload.get("protocol_version") or ""),
      reconcile_status=str(payload.get("reconcile_status") or "UNKNOWN"),
      kill_switch=bool(payload.get("kill_switch")),
      execution_window_active=bool(payload.get("execution_window_active")),
      snapshot_id=payload.get("snapshot_id"),
      snapshot_hash=payload.get("snapshot_hash"),
      snapshot_at=_aware(payload.get("snapshot_at")),
      reconciliation_age_seconds=payload.get("reconciliation_age_seconds"),
      queued_command_count=int(payload.get("queued_command_count") or 0),
      queue_delay_seconds=float(payload.get("queue_delay_seconds") or 0),
      dead_letter_count=int(payload.get("dead_letter_count") or 0),
      unresolved_critical_alert_count=int(
        payload.get("unresolved_critical_alert_count") or 0
      ),
      external_order_count=int(payload.get("external_order_count") or 0),
      external_trade_count=int(payload.get("external_trade_count") or 0),
      new_external_order_count=int(payload.get("new_external_order_count") or 0),
      new_external_trade_count=int(payload.get("new_external_trade_count") or 0),
      working_external_order_count=int(
        payload.get("working_external_order_count") or 0
      ),
      last_backup_at=_aware(payload.get("last_backup_at")),
      checked_at=_aware(payload.get("checked_at")),
    )
