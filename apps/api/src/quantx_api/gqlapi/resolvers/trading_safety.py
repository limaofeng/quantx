"""Resolvers for account-level execution safety."""

from __future__ import annotations

from datetime import datetime, timezone

import strawberry
from quantx_infrastructure.services.account_execution_safety_service import (
  AccountExecutionSafetyService,
)

from quantx_api.account_safety_history import fetch_account_safety_history

from ..types.trading_safety_types import (
  AccountExecutionHealthStatus,
  AccountExecutionSafety,
  AccountExecutionSafetyCheck,
  AccountExecutionSafetyCheckStatus,
  AccountSafetyCheckHistory,
  AccountSafetyHistory,
  AccountSafetyHistoryPoint,
  AccountSafetyHistoryRange,
  AccountSafetyHistoryStatus,
  AccountSafetyIncident,
  QuarantinedOrder,
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
          status=AccountExecutionSafetyCheckStatus(
            str(item.get("status") or "FAILED").upper()
          ),
          message=str(item.get("message") or ""),
          scope=str(item.get("scope") or "INCREASE_RISK"),
        )
        for item in list(payload.get("checks") or [])
      ],
      quarantined_orders=[
        QuarantinedOrder(
          client_order_id=str(item.get("client_order_id") or ""),
          plan_id=str(item.get("plan_id") or ""),
          intent_id=str(item.get("intent_id") or ""),
          quarantine_reason=str(item.get("quarantine_reason") or ""),
          broker_order_id=str(item.get("broker_order_id") or ""),
          repairable=bool(item.get("repairable")),
          blocked_reason=str(item.get("blocked_reason") or ""),
          quarantined_at=_aware(item.get("quarantined_at"))
          or datetime.now(timezone.utc),
          source_sequence=max(0, int(item.get("source_sequence") or 0)),
        )
        for item in list(payload.get("quarantined_orders") or [])
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

  @classmethod
  async def history(
    cls,
    history_range: AccountSafetyHistoryRange,
  ) -> AccountSafetyHistory:
    payload = await fetch_account_safety_history(history_range.value)
    return AccountSafetyHistory(
      available=bool(payload.get("available")),
      range=history_range,
      generated_at=_aware(payload.get("generatedAt"))
      or datetime.now(timezone.utc),
      first_observed_at=_aware(payload.get("firstObservedAt")),
      last_observed_at=_aware(payload.get("lastObservedAt")),
      observer_fresh=bool(payload.get("observerFresh")),
      bucket_seconds=int(payload.get("bucketSeconds") or 0),
      checks=[
        cls._history_check(item)
        for item in payload.get("checks", [])
        if isinstance(item, dict)
      ],
      incidents=[
        cls._history_incident(item)
        for item in payload.get("incidents", [])
        if isinstance(item, dict)
      ],
      incidents_truncated=bool(payload.get("incidentsTruncated")),
    )

  @staticmethod
  def _history_status(value: object) -> AccountSafetyHistoryStatus:
    normalized = str(value or "unknown").upper()
    try:
      return AccountSafetyHistoryStatus(normalized)
    except ValueError:
      return AccountSafetyHistoryStatus.UNKNOWN

  @classmethod
  def _history_check(cls, item: dict) -> AccountSafetyCheckHistory:
    return AccountSafetyCheckHistory(
      code=str(item.get("code") or ""),
      current_status=cls._history_status(item.get("currentStatus")),
      checked_at=_aware(item.get("checkedAt")),
      reason_code=item.get("reasonCode"),
      public_message=item.get("publicMessage"),
      coverage_pct=float(item.get("coveragePct") or 0),
      incident_count=int(item.get("incidentCount") or 0),
      points=[
        AccountSafetyHistoryPoint(
          start=_aware(point.get("start")) or datetime.now(timezone.utc),
          status=cls._history_status(point.get("status")),
          coverage_pct=float(point.get("coveragePct") or 0),
          sample_count=int(point.get("sampleCount") or 0),
          passed_count=int(point.get("passedCount") or 0),
          standby_count=int(point.get("standbyCount") or 0),
          failed_count=int(point.get("failedCount") or 0),
          unknown_count=int(point.get("unknownCount") or 0),
        )
        for point in item.get("points", [])
        if isinstance(point, dict)
      ],
    )

  @classmethod
  def _history_incident(cls, item: dict) -> AccountSafetyIncident:
    opened_at = _aware(item.get("openedAt")) or datetime.now(timezone.utc)
    return AccountSafetyIncident(
      id=strawberry.ID(str(item.get("id") or "")),
      check_code=str(item.get("checkCode") or ""),
      opened_at=opened_at,
      resolved_at=_aware(item.get("resolvedAt")),
      last_confirmed_failed_at=(
        _aware(item.get("lastConfirmedFailedAt")) or opened_at
      ),
      active=bool(item.get("active")),
      observation_fresh=bool(item.get("observationFresh")),
      opened_reason_code=str(item.get("openedReasonCode") or "UNKNOWN"),
      last_reason_code=str(item.get("lastReasonCode") or "UNKNOWN"),
      opened_message=str(item.get("openedMessage") or "准入检查未通过"),
      last_message=str(item.get("lastMessage") or "准入检查未通过"),
    )
