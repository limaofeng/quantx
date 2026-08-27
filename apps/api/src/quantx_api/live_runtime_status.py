"""Read-only effective live-capability status for local runtime operations."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.services.account_execution_safety_service import (
  AccountExecutionSafetyService,
)

from quantx_api.runtime_status import market_data_runtime_status

account_execution_safety_service = AccountExecutionSafetyService()


async def live_trading_runtime_status() -> dict[str, Any]:
  """Expose the existing single-account safety gate without duplicating it."""

  profile = str(getattr(settings, "runtime_profile", "web") or "web").lower()
  accounts = [
    str(account_id).strip()
    for account_id in list(settings.real_trading_account_allowlist or [])
    if str(account_id).strip()
  ]
  configured_live = bool(
    profile == "full" and settings.enable_real_trading and len(accounts) == 1
  )
  if not configured_live:
    return {
      "status": "DISABLED",
      "configuredLive": False,
      "accountId": accounts[0] if len(accounts) == 1 else None,
      "executionMode": "OBSERVE_ONLY",
      "agentStatus": "NOT_REQUIRED",
      "agentMode": "data-only",
      "protocolVersion": "",
      "reconciliationStatus": "NOT_REQUIRED",
      "snapshotAgeSeconds": None,
      "backupAgeSeconds": None,
      "marketStreamStatus": "NOT_REQUIRED",
      "blockedChecks": [],
    }

  account_id = accounts[0]
  try:
    safety, market_data = await asyncio.gather(
      account_execution_safety_service.status(account_id),
      market_data_runtime_status(),
    )
  except Exception as exc:
    return {
      "status": "DISABLED",
      "configuredLive": True,
      "accountId": account_id,
      "executionMode": "OBSERVE_ONLY",
      "agentStatus": "UNAVAILABLE",
      "agentMode": "offline",
      "protocolVersion": "",
      "reconciliationStatus": "UNKNOWN",
      "snapshotAgeSeconds": None,
      "backupAgeSeconds": None,
      "marketStreamStatus": "UNAVAILABLE",
      "blockedChecks": ["RUNTIME_STATUS_UNAVAILABLE"],
      "error": exc.__class__.__name__,
    }

  market_status = str(dict(market_data or {}).get("status") or "offline").upper()
  blocked_checks = [
    str(item.get("code") or "")
    for item in list(safety.get("checks") or [])
    if not bool(item.get("passed")) and str(item.get("code") or "")
  ]
  if market_status != "READY":
    blocked_checks.append("MARKET_STREAM_READY")
  blocked_checks = list(dict.fromkeys(blocked_checks))
  effective = bool(
    str(safety.get("execution_mode") or "").upper() == "TRADING"
    and bool(safety.get("can_increase_risk"))
    and market_status == "READY"
  )
  checked_at = safety.get("checked_at")
  backup_at = safety.get("last_backup_at")
  backup_age = (
    max(0.0, (checked_at - backup_at).total_seconds())
    if isinstance(checked_at, datetime) and isinstance(backup_at, datetime)
    else None
  )
  return {
    "status": "ENABLED" if effective else "DISABLED",
    "configuredLive": True,
    "accountId": account_id,
    "executionMode": str(safety.get("execution_mode") or "OBSERVE_ONLY"),
    "agentStatus": str(safety.get("agent_status") or "OFFLINE"),
    "agentMode": str(safety.get("agent_mode") or "offline"),
    "protocolVersion": str(safety.get("protocol_version") or ""),
    "reconciliationStatus": str(safety.get("reconcile_status") or "UNKNOWN"),
    "snapshotAgeSeconds": safety.get("reconciliation_age_seconds"),
    "backupAgeSeconds": backup_age,
    "marketStreamStatus": market_status,
    "blockedChecks": blocked_checks,
  }
