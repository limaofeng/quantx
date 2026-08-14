"""Exact, revocable authorization envelopes for automatic live exits.

The historical ``auto_exit_authorized`` boolean is kept as a compatibility
projection only.  A live plan is authorized exclusively when the exact plan,
position and competing-sell snapshot still matches the durable envelope
created by a device-bound confirmation challenge.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from quantx_domain.clock import utcnow
from sqlalchemy import select

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.auth import (
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  RESERVING_EXIT_PLAN_STATUSES,
)

AUTO_EXIT_AUTHORIZATION_LIFETIME = timedelta(days=7)
REQUIRED_AUTO_EXIT_SCOPES = frozenset(
  {"liquidation:control", "trade:approve"}
)
ACTIVE_PENDING_SELL_STATUSES = frozenset(
  {"QUEUED", "PENDING", "SUBMITTED", "REPORTED", "PARTIAL_FILLED"}
)
AUTHORIZABLE_PLAN_STATUSES = frozenset({"ACTIVE", "PARTIALLY_EXITED"})


@dataclass(frozen=True)
class ExitPlanAuthorizationSnapshot:
  """Stable safety facts covered by one automatic-exit authorization."""

  subject: dict[str, Any]
  fingerprint: str
  position_updated_at: Optional[datetime]
  has_pending_sell: bool


@dataclass(frozen=True)
class ExitPlanAuthorizationValidation:
  valid: bool
  code: str
  message: str


def authorization_expiry_for_challenge(expires_at: datetime) -> datetime:
  """Return the server-owned authorization expiry bound to a challenge."""

  return time_utils.to_shanghai(expires_at) + AUTO_EXIT_AUTHORIZATION_LIFETIME


def _version_token(value: Optional[datetime]) -> Optional[str]:
  return value.isoformat(timespec="microseconds") if value is not None else None


def _canonical_fingerprint(value: dict[str, Any]) -> str:
  # ``status`` temporarily becomes EXIT_PENDING as soon as Engine reserves an
  # intent, before the broker command is queued.  That state-machine detail is
  # shown in the preview and rechecked during confirmation, but is not part of
  # the durable authorization scope; otherwise every legitimate trigger would
  # invalidate itself before it could reach the atomic enqueue gate.
  scope = {
    **value,
    "plan": dict(value.get("plan") or {}),
  }
  scope["plan"].pop("status", None)
  encoded = json.dumps(
    scope,
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
    default=str,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def _template_binding(record: AutoExitPlanRecord) -> dict[str, Any]:
  state = dict(record.plan_state or {})
  template = dict(state.get("template") or {})
  template.pop("auto_exit_authorized", None)
  return {
    "plan_id": str(record.plan_id),
    "account_id": str(record.account_id),
    "instrument_code": str(record.instrument_code),
    "bucket": str(record.bucket),
    "source_type": str(record.source_type),
    "source_id": str(record.source_id),
    "strategy_run_id": str(record.strategy_run_id or ""),
    "enabled": bool(record.enabled),
    "status": str(record.status or "").upper(),
    "execution_mode": str(record.execution_mode or "").lower(),
    "config_version": int(record.config_version or 0),
    "protected_volume": max(0, int(record.protected_volume or 0)),
    "exited_volume": max(0, int(record.exited_volume or 0)),
    "remaining_volume": max(0, int(record.remaining_volume or 0)),
    "template": template,
  }


def require_authorizable_live_plan(
  record: Optional[AutoExitPlanRecord],
  *,
  account_id: str,
  expected_config_version: int,
) -> AutoExitPlanRecord:
  if record is None:
    raise ValueError("EXIT_PLAN_NOT_FOUND")
  if str(record.account_id) != str(account_id):
    raise ValueError("ACCOUNT_SCOPE_MISMATCH")
  if str(record.execution_mode or "").lower() != "live":
    raise ValueError("LIVE_EXIT_PLAN_REQUIRED")
  if int(record.config_version or 0) != int(expected_config_version):
    raise ValueError("CONFIG_VERSION_CONFLICT")
  if not bool(record.enabled) or str(record.status or "").upper() not in (
    AUTHORIZABLE_PLAN_STATUSES
  ):
    raise ValueError("EXIT_PLAN_NOT_ACTIVE")
  if int(record.protected_volume or 0) <= 0 or int(record.remaining_volume or 0) <= 0:
    raise ValueError("EXIT_PLAN_HAS_NO_REMAINING_VOLUME")
  state = dict(record.plan_state or {})
  template = dict(state.get("template") or {})
  rules = list(template.get("rules") or [])
  if not rules or not any(bool(dict(rule or {}).get("enabled", True)) for rule in rules):
    raise ValueError("EXIT_PLAN_RULES_UNAVAILABLE")
  return record


async def build_exit_plan_authorization_snapshot(
  db: Any,
  record: AutoExitPlanRecord,
  *,
  lock_mutable_rows: bool,
) -> ExitPlanAuthorizationSnapshot:
  """Build the stable plan/position/T+1/protection subject to be signed."""

  position_stmt = select(Position).where(
    Position.account_id == record.account_id,
    Position.stock_code == record.instrument_code,
  )
  if lock_mutable_rows:
    position_stmt = position_stmt.with_for_update()
  position = (await db.execute(position_stmt)).scalar_one_or_none()
  if position is None or int(position.volume or 0) <= 0:
    raise ValueError("POSITION_SNAPSHOT_UNAVAILABLE")

  conflict_stmt = (
    select(AutoExitPlanRecord)
    .where(
      AutoExitPlanRecord.account_id == record.account_id,
      AutoExitPlanRecord.instrument_code == record.instrument_code,
      AutoExitPlanRecord.plan_id != record.plan_id,
      AutoExitPlanRecord.status.in_(RESERVING_EXIT_PLAN_STATUSES),
    )
    .order_by(AutoExitPlanRecord.plan_id)
  )
  if lock_mutable_rows:
    conflict_stmt = conflict_stmt.with_for_update()
  conflicts = list((await db.execute(conflict_stmt)).scalars().all())

  pending_stmt = (
    select(PendingTradeOrder)
    .where(
      PendingTradeOrder.account_id == record.account_id,
      PendingTradeOrder.instrument_code == record.instrument_code,
      PendingTradeOrder.side == "SELL",
      PendingTradeOrder.status.in_(ACTIVE_PENDING_SELL_STATUSES),
    )
    .order_by(PendingTradeOrder.client_order_id)
  )
  if lock_mutable_rows:
    pending_stmt = pending_stmt.with_for_update()
  pending_sells = list((await db.execute(pending_stmt)).scalars().all())

  total_volume = max(0, int(position.volume or 0))
  available_volume = max(
    0,
    min(total_volume, int(position.can_use_volume or 0)),
  )
  frozen_volume = max(0, min(total_volume, int(position.frozen_volume or 0)))
  yesterday_volume = max(
    0,
    min(total_volume, int(position.yesterday_volume or 0)),
  )
  subject = {
    "plan": _template_binding(record),
    "position": {
      "account_id": str(position.account_id),
      "instrument_code": str(position.stock_code),
      "total_volume": total_volume,
      "available_volume": available_volume,
      "frozen_volume": frozen_volume,
      "yesterday_volume": yesterday_volume,
      "t1_unavailable_volume": max(
        0,
        total_volume - available_volume - frozen_volume,
      ),
    },
    "other_protections": [
      {
        "plan_id": str(item.plan_id),
        "source_type": str(item.source_type),
        "status": str(item.status or "").upper(),
        "config_version": int(item.config_version or 0),
        "remaining_volume": max(0, int(item.remaining_volume or 0)),
        "pending": bool(
          str(item.status or "").upper() == "EXIT_PENDING"
          or item.pending_client_order_id
        ),
      }
      for item in conflicts
    ],
    "pending_sells": [
      {
        "client_order_id": str(item.client_order_id),
        "status": str(item.status or "").upper(),
        "volume": max(0, int(item.volume or 0)),
        "intent_id": str(item.intent_id or ""),
      }
      for item in pending_sells
    ],
  }
  return ExitPlanAuthorizationSnapshot(
    subject=subject,
    fingerprint=_canonical_fingerprint(subject),
    position_updated_at=position.updated_at,
    has_pending_sell=bool(pending_sells),
  )


def clear_exact_auto_exit_authorization(record: AutoExitPlanRecord) -> None:
  """Remove only autonomous-live authority; the plan keeps monitoring."""

  record.auto_exit_authorized = False
  record.auto_exit_authorization_fingerprint = None
  record.auto_exit_authorization_config_version = None
  record.auto_exit_authorized_at = None
  record.auto_exit_authorization_expires_at = None
  record.auto_exit_authorization_challenge_id = None
  record.auto_exit_authorization_user_id = None
  record.auto_exit_authorization_device_session_id = None
  state = dict(record.plan_state or {})
  template = dict(state.get("template") or {})
  if template:
    template["auto_exit_authorized"] = False
    state["template"] = template
    record.plan_state = state


def grant_exact_auto_exit_authorization(
  record: AutoExitPlanRecord,
  *,
  fingerprint: str,
  challenge_id: str,
  user_id: str,
  device_session_id: str,
  authorized_at: datetime,
  authorization_expires_at: datetime,
) -> None:
  if str(record.execution_mode or "").lower() != "live":
    raise ValueError("LIVE_EXIT_PLAN_REQUIRED")
  if not fingerprint or len(fingerprint) != 64:
    raise ValueError("INVALID_AUTHORIZATION_FINGERPRINT")
  if authorization_expires_at <= authorized_at:
    raise ValueError("INVALID_AUTHORIZATION_EXPIRY")
  record.auto_exit_authorized = True
  record.auto_exit_authorization_fingerprint = fingerprint
  record.auto_exit_authorization_config_version = int(record.config_version or 0)
  record.auto_exit_authorized_at = authorized_at
  record.auto_exit_authorization_expires_at = authorization_expires_at
  record.auto_exit_authorization_challenge_id = challenge_id
  record.auto_exit_authorization_user_id = user_id
  record.auto_exit_authorization_device_session_id = device_session_id
  state = dict(record.plan_state or {})
  template = dict(state.get("template") or {})
  if not template:
    raise ValueError("EXIT_PLAN_RULES_UNAVAILABLE")
  template["auto_exit_authorized"] = True
  state["template"] = template
  record.plan_state = state


async def _authorization_session_valid(
  db: Any,
  record: AutoExitPlanRecord,
  *,
  lock_mutable_rows: bool,
) -> bool:
  session_id = str(record.auto_exit_authorization_device_session_id or "")
  user_id = str(record.auto_exit_authorization_user_id or "")
  if not session_id or not user_id:
    return False
  session_stmt = (
    select(AuthDeviceSession, AuthUser)
    .join(AuthUser, AuthUser.id == AuthDeviceSession.user_id)
    .where(
      AuthDeviceSession.id == session_id,
      AuthDeviceSession.user_id == user_id,
    )
  )
  if lock_mutable_rows:
    session_stmt = session_stmt.with_for_update()
  row = (await db.execute(session_stmt)).one_or_none()
  if row is None:
    return False
  session, user = row
  if (
    session.revoked_at is not None
    or session.expires_at <= utcnow()
    or not bool(user.is_active)
    or str(session.active_account_id or "") != str(record.account_id)
  ):
    return False
  session_scopes = {
    str(value).strip()
    for value in list(session.granted_permissions or [])
    if isinstance(value, str) and value.strip()
  }
  user_scopes = {
    str(value).strip()
    for value in list(user.permissions or [])
    if isinstance(value, str) and value.strip()
  }
  if not REQUIRED_AUTO_EXIT_SCOPES <= (session_scopes & user_scopes):
    return False
  access_stmt = select(AuthUserAccountAccess).where(
      AuthUserAccountAccess.user_id == user_id,
      AuthUserAccountAccess.account_id == record.account_id,
  )
  if lock_mutable_rows:
    access_stmt = access_stmt.with_for_update()
  access = await db.scalar(access_stmt)
  return access is not None


async def validate_exact_auto_exit_authorization(
  db: Any,
  record: AutoExitPlanRecord,
  *,
  now: Optional[datetime] = None,
  lock_mutable_rows: bool = False,
) -> ExitPlanAuthorizationValidation:
  checked_at = now or time_utils.now()
  if not bool(record.auto_exit_authorized):
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_NOT_AUTHORIZED",
      "退出计划尚未获得精确自动实盘授权",
    )
  required_values = (
    record.auto_exit_authorization_fingerprint,
    record.auto_exit_authorization_config_version,
    record.auto_exit_authorized_at,
    record.auto_exit_authorization_expires_at,
    record.auto_exit_authorization_challenge_id,
    record.auto_exit_authorization_user_id,
    record.auto_exit_authorization_device_session_id,
  )
  if any(value is None or value == "" for value in required_values):
    return ExitPlanAuthorizationValidation(
      False,
      "LEGACY_BOOLEAN_AUTHORIZATION_REJECTED",
      "旧布尔授权不具备自动实盘权限",
    )
  if str(record.execution_mode or "").lower() != "live":
    return ExitPlanAuthorizationValidation(
      False,
      "LIVE_EXIT_PLAN_REQUIRED",
      "只有明确的 LIVE 退出计划可使用自动实盘授权",
    )
  if str(record.status or "").upper() not in (
    AUTHORIZABLE_PLAN_STATUSES | {"EXIT_PENDING"}
  ):
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_PLAN_NOT_ACTIVE",
      "退出计划当前状态不允许自动实盘执行",
    )
  if int(record.auto_exit_authorization_config_version or 0) != int(
    record.config_version or 0
  ):
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_CONFIG_CHANGED",
      "退出计划配置版本已变化",
    )
  expires_at = time_utils.to_shanghai(record.auto_exit_authorization_expires_at)
  if expires_at <= checked_at:
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_AUTHORIZATION_EXPIRED",
      "自动实盘授权已过期",
    )
  if not await _authorization_session_valid(
    db,
    record,
    lock_mutable_rows=lock_mutable_rows,
  ):
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_AUTHORIZATION_REVOKED",
      "授权设备会话、权限或账户范围已失效",
    )
  try:
    snapshot = await build_exit_plan_authorization_snapshot(
      db,
      record,
      lock_mutable_rows=lock_mutable_rows,
    )
  except ValueError:
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_SAFETY_SNAPSHOT_UNAVAILABLE",
      "当前持仓安全快照不可用",
    )
  if snapshot.fingerprint != str(record.auto_exit_authorization_fingerprint):
    return ExitPlanAuthorizationValidation(
      False,
      "AUTO_EXIT_AUTHORIZATION_SCOPE_CHANGED",
      "规则、保护量、持仓、T+1、冲突或待成交 SELL 已变化",
    )
  return ExitPlanAuthorizationValidation(True, "AUTHORIZED", "精确授权有效")


class AutoExitAuthorizationGuard:
  """Engine-side defense that downgrades invalid live authority to approval."""

  @staticmethod
  async def validate_or_invalidate(plan_id: str) -> ExitPlanAuthorizationValidation:
    async with AsyncSessionLocal() as db:
      record = (
        await db.execute(
          select(AutoExitPlanRecord)
          .where(AutoExitPlanRecord.plan_id == plan_id)
          .with_for_update()
        )
      ).scalar_one_or_none()
      if record is None:
        return ExitPlanAuthorizationValidation(
          False,
          "EXIT_PLAN_NOT_FOUND",
          "退出计划不存在",
        )
      result = await validate_exact_auto_exit_authorization(
        db,
        record,
        lock_mutable_rows=True,
      )
      if result.valid:
        return result

      challenge_id = str(record.auto_exit_authorization_challenge_id or "legacy")
      if bool(record.auto_exit_authorized):
        clear_exact_auto_exit_authorization(record)
        business_key = (
          f"auto-exit-authorization-invalidated:{record.plan_id}:"
          f"{challenge_id}:{result.code}"
        )
        existing = await db.scalar(
          select(AutoExitPlanEvent).where(
            AutoExitPlanEvent.business_key == business_key
          )
        )
        if existing is None:
          db.add(
            AutoExitPlanEvent(
              event_id=str(uuid.uuid4()),
              business_key=business_key,
              plan_id=str(record.plan_id),
              event_type="AUTO_EXIT_AUTHORIZATION_INVALIDATED",
              payload={
                "challenge_id": None if challenge_id == "legacy" else challenge_id,
                "config_version": int(record.config_version or 0),
                "reason_code": result.code,
              },
              created_at=time_utils.now(),
            )
          )
        await db.commit()
      return result


__all__ = [
  "ACTIVE_PENDING_SELL_STATUSES",
  "AUTO_EXIT_AUTHORIZATION_LIFETIME",
  "AutoExitAuthorizationGuard",
  "ExitPlanAuthorizationSnapshot",
  "ExitPlanAuthorizationValidation",
  "authorization_expiry_for_challenge",
  "build_exit_plan_authorization_snapshot",
  "clear_exact_auto_exit_authorization",
  "grant_exact_auto_exit_authorization",
  "require_authorizable_live_plan",
  "validate_exact_auto_exit_authorization",
]
