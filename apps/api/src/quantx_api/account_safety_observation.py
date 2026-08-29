"""Local, account-free observation projection consumed by QuantX Monitor."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from quantx_contracts import (
  ACCOUNT_EXECUTION_SAFETY_CHECK_CODES,
  AccountSafetyCheckObservation,
  AccountSafetyCheckStatus,
  AccountSafetyObservationSnapshot,
)
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.services.account_execution_safety_service import (
  AccountExecutionSafetyService,
)

_HEX_SECRET_PATTERN = re.compile(r"\b[0-9a-fA-F]{32,128}\b")
_UUID_PATTERN = re.compile(
  r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
  r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def _configured_account_ids() -> list[str]:
  return list(
    dict.fromkeys(
      str(value).strip()
      for value in list(settings.real_trading_account_allowlist or [])
      if str(value).strip()
    )
  )


def _public_message(value: object, *, account_id: str) -> str:
  message = str(value or "").strip()
  if account_id:
    message = message.replace(account_id, "[ACCOUNT]")
  message = _UUID_PATTERN.sub("[ID]", message)
  message = _HEX_SECRET_PATTERN.sub("[HASH]", message)
  return message[:500]


async def account_safety_observation_snapshot() -> AccountSafetyObservationSnapshot:
  """Return the complete sanitized snapshot for the configured personal account."""

  observed_at = datetime.now(timezone.utc)
  account_ids = _configured_account_ids()
  if len(account_ids) != 1:
    return AccountSafetyObservationSnapshot(
      status="disabled",
      observed_at=observed_at,
      checks=[],
    )

  account_id = account_ids[0]
  payload = await AccountExecutionSafetyService().status(account_id)
  by_code = {
    str(item.get("code") or ""): item
    for item in list(payload.get("checks") or [])
  }
  checks: list[AccountSafetyCheckObservation] = []
  for code in ACCOUNT_EXECUTION_SAFETY_CHECK_CODES:
    item = by_code.get(code)
    if item is None:
      raise RuntimeError(f"account-safety snapshot missing check: {code}")
    status = AccountSafetyCheckStatus(str(item.get("status") or "FAILED").upper())
    message = _public_message(item.get("message"), account_id=account_id)
    if status is AccountSafetyCheckStatus.PASSED:
      reason_code = None
      message = ""
    elif status is AccountSafetyCheckStatus.STANDBY:
      reason_code = "MARKET_CLOSED_STANDBY"
      message = message or "当前休市，权威行情链路已收敛并保持待机"
    else:
      reason_code = f"{code}_FAILED"
      message = message or "该项账户准入检查未通过"
    checks.append(
      AccountSafetyCheckObservation(
        code=code,
        status=status,
        scope=str(item.get("scope") or "INCREASE_RISK").upper(),
        reason_code=reason_code,
        public_message=message,
      )
    )
  return AccountSafetyObservationSnapshot(
    status="ready",
    observed_at=observed_at,
    checks=checks,
  )
