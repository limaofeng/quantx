"""Device-bound two-phase authorization for one exact live exit plan."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.account import Account
from quantx_infrastructure.models.agent_runtime import RuntimeComponentHeartbeat
from quantx_infrastructure.models.auth import AuthUserAccountAccess
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.services.exit_plan_authorization_service import (
  authorization_expiry_for_challenge,
  build_exit_plan_authorization_snapshot,
  grant_exact_auto_exit_authorization,
  lock_exit_plan_scope_for_plan,
  require_authorizable_live_plan,
)
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  TradeCommandService,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from quantx_api.auth.errors import AuthError
from quantx_api.auth.principal import Principal
from quantx_api.auth.service import AuthService

from .trade_approval import (
  TradeApprovalChallengeError,
  challenge_token_digest,
  signed_payload_fingerprint,
  validate_persistent_trade_challenge,
)

EXIT_PLAN_AUTHORIZATION_ACTION = "EXIT_PLAN_AUTHORIZATION"
_CHALLENGE_LIFETIME = timedelta(seconds=60)
_MAX_TOKEN_LENGTH = 256
_MAX_IDEMPOTENCY_KEY_LENGTH = 128
_MAX_PLAN_ID_LENGTH = 128
_MAX_LIVE_SNAPSHOT_AGE = timedelta(seconds=90)


@dataclass(frozen=True)
class ExitPlanAuthorizationRequestData:
  account_id: str
  plan_id: str
  expected_config_version: int
  idempotency_key: str

  def payload(self) -> dict[str, Any]:
    return {
      "account_id": self.account_id,
      "plan_id": self.plan_id,
      "expected_config_version": self.expected_config_version,
      "idempotency_key": self.idempotency_key,
    }


@dataclass(frozen=True)
class ExitPlanAuthorizationPreviewData:
  challenge_id: str
  confirmation_token: str
  request: ExitPlanAuthorizationRequestData
  plan_binding: dict[str, Any]
  safety_subject: dict[str, Any]
  position_updated_at: datetime | None
  readiness: dict[str, Any]
  authorization_fingerprint: str
  authorization_expires_at: datetime
  challenge_expires_at: datetime


@dataclass(frozen=True)
class ExitPlanAuthorizationConfirmationData:
  challenge_id: str
  plan_id: str
  config_version: int
  authorization_expires_at: datetime
  audit_event_id: str


def normalize_exit_plan_authorization_request(
  *,
  account_id: str,
  plan_id: str,
  expected_config_version: int,
  idempotency_key: str,
) -> ExitPlanAuthorizationRequestData:
  normalized_account = str(account_id or "").strip()
  normalized_plan = str(plan_id or "").strip()
  normalized_key = str(idempotency_key or "").strip()
  try:
    normalized_version = int(expected_config_version)
  except (TypeError, ValueError) as exc:
    raise TradeApprovalChallengeError(
      "INVALID_CONFIG_VERSION",
      "退出计划配置版本无效",
    ) from exc
  if not normalized_account:
    raise TradeApprovalChallengeError("ACCOUNT_REQUIRED", "必须指定资金账号")
  if not normalized_plan or len(normalized_plan) > _MAX_PLAN_ID_LENGTH:
    raise TradeApprovalChallengeError("INVALID_EXIT_PLAN_ID", "退出计划 ID 无效")
  if normalized_version <= 0:
    raise TradeApprovalChallengeError(
      "INVALID_CONFIG_VERSION",
      "退出计划配置版本必须大于 0",
    )
  if not normalized_key or len(normalized_key) > _MAX_IDEMPOTENCY_KEY_LENGTH:
    raise TradeApprovalChallengeError(
      "INVALID_IDEMPOTENCY_KEY",
      "幂等键不能为空且不能超过 128 个字符",
    )
  return ExitPlanAuthorizationRequestData(
    account_id=normalized_account,
    plan_id=normalized_plan,
    expected_config_version=normalized_version,
    idempotency_key=normalized_key,
  )


def _request_from_payload(payload: dict[str, Any]) -> ExitPlanAuthorizationRequestData:
  if str(payload.get("action") or "") != EXIT_PLAN_AUTHORIZATION_ACTION:
    raise TradeApprovalChallengeError(
      "CONFIRMATION_CONTEXT_MISMATCH",
      "退出计划授权确认上下文无效",
    )
  request = dict(payload.get("request") or {})
  return normalize_exit_plan_authorization_request(
    account_id=str(request.get("account_id") or ""),
    plan_id=str(request.get("plan_id") or ""),
    expected_config_version=int(request.get("expected_config_version") or 0),
    idempotency_key=str(request.get("idempotency_key") or ""),
  )


def _require_current_session_account(
  principal: Principal,
  account_id: str,
) -> None:
  if principal.authorized_account_ids != (account_id,):
    raise TradeApprovalChallengeError(
      "SINGLE_ACCOUNT_SESSION_REQUIRED",
      "退出计划自动授权要求当前会话只授权该资金账户",
    )
  principal.require_account(account_id)


def _aware(value: datetime) -> datetime:
  return time_utils.to_shanghai(value, keep_tz=True)


def _age(value: datetime | None, now: datetime) -> timedelta | None:
  if value is None:
    return None
  return now - time_utils.to_shanghai(value)


async def _live_readiness_binding(
  db: Any,
  *,
  user_id: str,
  account_id: str,
  lock_mutable_rows: bool,
) -> dict[str, Any]:
  try:
    command_service = TradeCommandService(db)
    rollout = await command_service._require_manual_live_authorization(
      account_id,
      risk_reducing=True,
    )
    # Automatic execution also requires the server-side automated-live gate;
    # the manual SELL escape hatch alone is intentionally insufficient.
    await command_service._require_live_authorization(
      account_id,
      risk_reducing=True,
    )
    device = await command_service._device_for(
      user_id=user_id,
      account_id=account_id,
      execution_mode="live",
    )
  except AgentUnavailableError as exc:
    raise TradeApprovalChallengeError(
      "LIVE_AUTHORIZATION_REJECTED",
      str(exc),
    ) from exc

  heartbeat = await db.get(
    RuntimeComponentHeartbeat,
    f"qmt-agent:{device.id}",
  )
  details = dict(heartbeat.details or {}) if heartbeat is not None else {}
  reported_capabilities = {
    str(value).strip().lower()
    for value in list(details.get("capabilities") or [])
    if str(value).strip()
  }
  protocol_version = str(details.get("protocolVersion") or "")
  if (
    heartbeat is None
    or str(heartbeat.status or "").upper() != "READY"
    or "live" not in reported_capabilities
    or protocol_version != "1.1"
  ):
    raise TradeApprovalChallengeError(
      "LIVE_AGENT_NOT_READY",
      "自动实盘退出要求唯一 READY、live、协议 1.1 的 QMT Agent",
    )

  account_stmt = select(Account).where(
    Account.account_id == account_id,
    Account.account_type == AccountType.STOCK,
  )
  if lock_mutable_rows:
    account_stmt = account_stmt.with_for_update()
  account = (await db.execute(account_stmt)).scalar_one_or_none()
  now = time_utils.now()
  account_age = _age(getattr(account, "updated_at", None), now)
  if (
    account is None
    or account_age is None
    or account_age < timedelta(0)
    or account_age > _MAX_LIVE_SNAPSHOT_AGE
  ):
    raise TradeApprovalChallengeError(
      "ACCOUNT_SNAPSHOT_STALE",
      "账户快照缺失或已超过 90 秒",
    )

  return {
    "account_updated_at": account.updated_at.isoformat(timespec="microseconds"),
    "authorization_state": str(rollout.authorization_state or "").upper(),
    "state_version": int(rollout.state_version or 0),
    "kill_switch": str(rollout.authorization_state or "").upper() == "KILLED",
    "reconcile_status": str(rollout.reconcile_status or "").upper(),
    "snapshot_id": str(rollout.last_snapshot_id or ""),
    "snapshot_hash": str(rollout.last_snapshot_hash or ""),
    "snapshot_at": (
      rollout.last_snapshot_at.isoformat(timespec="microseconds")
      if rollout.last_snapshot_at is not None
      else None
    ),
    "agent_device_id": str(device.id),
    "agent_status": str(heartbeat.status or "").upper(),
    "agent_mode": "live",
    "protocol_version": protocol_version,
  }


def _challenge_payload(
  *,
  request: ExitPlanAuthorizationRequestData,
  plan_binding: dict[str, Any],
  safety_subject: dict[str, Any],
  authorization_fingerprint: str,
  readiness: dict[str, Any],
  authorization_expires_at: datetime,
) -> dict[str, Any]:
  return {
    "action": EXIT_PLAN_AUTHORIZATION_ACTION,
    "request": request.payload(),
    "plan_binding": plan_binding,
    "safety_subject": safety_subject,
    "authorization_fingerprint": authorization_fingerprint,
    "readiness": readiness,
    "authorization_expires_at": authorization_expires_at.isoformat(
      timespec="microseconds"
    ),
  }


def _authorization_expiry(payload: dict[str, Any]) -> datetime:
  try:
    return datetime.fromisoformat(str(payload["authorization_expires_at"]))
  except (KeyError, TypeError, ValueError) as exc:
    raise TradeApprovalChallengeError(
      "CONFIRMATION_CONTEXT_MISMATCH",
      "退出计划授权有效期无效",
    ) from exc


def _plan_error(exc: ValueError) -> TradeApprovalChallengeError:
  code = str(exc)
  messages = {
    "EXIT_PLAN_NOT_FOUND": "退出计划不存在",
    "ACCOUNT_SCOPE_MISMATCH": "退出计划不属于当前主账户",
    "LIVE_EXIT_PLAN_REQUIRED": "只有明确的 LIVE 退出计划需要自动实盘授权",
    "CONFIG_VERSION_CONFLICT": "退出计划版本已变化，请刷新后重新预览",
    "EXIT_PLAN_NOT_ACTIVE": "退出计划当前未处于可授权的活动状态",
    "EXIT_PLAN_HAS_NO_REMAINING_VOLUME": "退出计划已无待保护数量",
    "EXIT_PLAN_RULES_UNAVAILABLE": "退出计划缺少可授权的明确规则",
    "POSITION_SNAPSHOT_UNAVAILABLE": "当前持仓安全快照不可用",
  }
  return TradeApprovalChallengeError(
    code if code in messages else "EXIT_PLAN_AUTHORIZATION_REJECTED",
    messages.get(code, "退出计划暂不满足自动实盘授权条件"),
  )


class ExitPlanAuthorizationChallengeService:
  @staticmethod
  async def issue(
    *,
    principal: Principal,
    request: ExitPlanAuthorizationRequestData,
  ) -> ExitPlanAuthorizationPreviewData:
    principal.require_permission("liquidation:control")
    _require_current_session_account(principal, request.account_id)
    challenge_id = str(uuid.uuid4())
    now = time_utils.now()
    challenge_expires_at = now + _CHALLENGE_LIFETIME
    authorization_expires_at = authorization_expiry_for_challenge(challenge_expires_at)
    async with AsyncSessionLocal() as db:
      try:
        record = await db.scalar(
          select(AutoExitPlanRecord).where(
            AutoExitPlanRecord.plan_id == request.plan_id
          )
        )
        require_authorizable_live_plan(
          record,
          account_id=request.account_id,
          expected_config_version=request.expected_config_version,
        )
        snapshot = await build_exit_plan_authorization_snapshot(
          db,
          record,
          lock_mutable_rows=False,
        )
      except ValueError as exc:
        raise _plan_error(exc) from exc
      if snapshot.has_pending_sell:
        raise TradeApprovalChallengeError(
          "PENDING_SELL_CONFLICT",
          "存在待成交 SELL，不能扩大为自动实盘授权",
        )
      readiness = await _live_readiness_binding(
        db,
        user_id=principal.user_id,
        account_id=request.account_id,
        lock_mutable_rows=False,
      )
      plan_binding = dict(snapshot.subject["plan"])
      payload = _challenge_payload(
        request=request,
        plan_binding=plan_binding,
        safety_subject=snapshot.subject,
        authorization_fingerprint=snapshot.fingerprint,
        readiness=readiness,
        authorization_expires_at=authorization_expires_at,
      )
      raw_token = secrets.token_urlsafe(48)
      challenge = TradeConfirmationChallenge(
        id=challenge_id,
        action=EXIT_PLAN_AUTHORIZATION_ACTION,
        user_id=principal.user_id,
        device_session_id=principal.device_session_id,
        account_id=request.account_id,
        idempotency_key=request.idempotency_key,
        payload=payload,
        payload_fingerprint=signed_payload_fingerprint(payload),
        token_digest=challenge_token_digest(raw_token),
        expires_at=challenge_expires_at,
        consumed_at=None,
      )
      db.add(challenge)
      try:
        await db.commit()
      except IntegrityError as exc:
        await db.rollback()
        raise TradeApprovalChallengeError(
          "IDEMPOTENCY_KEY_ALREADY_USED",
          "该幂等键已用于退出计划授权，请使用原确认结果或重新预览",
        ) from exc
    return ExitPlanAuthorizationPreviewData(
      challenge_id=challenge_id,
      confirmation_token=raw_token,
      request=request,
      plan_binding=plan_binding,
      safety_subject=snapshot.subject,
      position_updated_at=snapshot.position_updated_at,
      readiness=readiness,
      authorization_fingerprint=snapshot.fingerprint,
      authorization_expires_at=_aware(authorization_expires_at),
      challenge_expires_at=_aware(challenge_expires_at),
    )

  @staticmethod
  async def confirm(
    *,
    principal: Principal,
    request: ExitPlanAuthorizationRequestData,
    challenge_id: str,
    confirmation_token: str,
  ) -> ExitPlanAuthorizationConfirmationData:
    normalized_id = str(challenge_id or "").strip()
    token = str(confirmation_token or "")
    if not normalized_id or not token or len(token) > _MAX_TOKEN_LENGTH:
      raise TradeApprovalChallengeError(
        "INVALID_CONFIRMATION_TOKEN",
        "确认凭据无效，请重新获取授权预览",
      )
    _require_current_session_account(principal, request.account_id)

    async with AsyncSessionLocal() as db:
      try:
        challenge = (
          await db.execute(
            select(TradeConfirmationChallenge)
            .where(TradeConfirmationChallenge.id == normalized_id)
            .with_for_update()
          )
        ).scalar_one_or_none()
        if challenge is None:
          raise TradeApprovalChallengeError(
            "CONFIRMATION_NOT_FOUND",
            "退出计划授权挑战不存在或已失效",
          )
        payload = dict(challenge.payload or {})
        validate_persistent_trade_challenge(
          challenge=challenge,
          principal=principal,
          action=EXIT_PLAN_AUTHORIZATION_ACTION,
          confirmation_token=token,
          now=time_utils.now(),
          payload=payload,
          allow_consumed=True,
        )
        signed_request = _request_from_payload(payload)
        if signed_request != request:
          raise TradeApprovalChallengeError(
            "CONFIRMATION_CONTEXT_MISMATCH",
            "账户、计划、版本或幂等键与授权预览不一致",
          )
        if challenge.consumed_at is not None:
          return ExitPlanAuthorizationChallengeService._existing_result(challenge)

        scope = await lock_exit_plan_scope_for_plan(db, request.plan_id)
        record = scope.plan(request.plan_id)
        try:
          require_authorizable_live_plan(
            record,
            account_id=request.account_id,
            expected_config_version=request.expected_config_version,
          )
        except ValueError as exc:
          raise _plan_error(exc) from exc

        try:
          current = await AuthService(db).lock_and_validate_session(
            principal,
            required_permission="liquidation:control",
            account_id=request.account_id,
          )
          current.require_permission("trade:approve")
          _require_current_session_account(current, request.account_id)
        except AuthError as exc:
          raise TradeApprovalChallengeError(exc.code, exc.message) from exc
        access = await db.scalar(
          select(AuthUserAccountAccess)
          .where(
            AuthUserAccountAccess.user_id == current.user_id,
            AuthUserAccountAccess.account_id == request.account_id,
          )
          .with_for_update()
        )
        if access is None:
          raise TradeApprovalChallengeError(
            "FORBIDDEN",
            "当前用户已无权使用该退出计划账户",
          )

        try:
          snapshot = await build_exit_plan_authorization_snapshot(
            db,
            record,
            lock_mutable_rows=True,
            locked_scope=scope,
          )
        except ValueError as exc:
          raise _plan_error(exc) from exc
        if snapshot.has_pending_sell:
          raise TradeApprovalChallengeError(
            "PENDING_SELL_CONFLICT",
            "确认前出现待成交 SELL，请重新预览",
          )
        if (
          snapshot.subject != dict(payload.get("safety_subject") or {})
          or snapshot.fingerprint != str(payload.get("authorization_fingerprint") or "")
          or dict(snapshot.subject["plan"]) != dict(payload.get("plan_binding") or {})
        ):
          raise TradeApprovalChallengeError(
            "EXIT_PLAN_AUTHORIZATION_SCOPE_CHANGED",
            "规则、保护量、版本、持仓、T+1、现有保护或待成交 SELL 已变化",
          )

        readiness = await _live_readiness_binding(
          db,
          user_id=current.user_id,
          account_id=request.account_id,
          lock_mutable_rows=True,
        )
        if readiness != dict(payload.get("readiness") or {}):
          raise TradeApprovalChallengeError(
            "READINESS_CHANGED",
            "实盘安全或对账快照已变化，请重新预览",
          )
        now = time_utils.now()
        authorization_expires_at = _authorization_expiry(payload)
        if authorization_expires_at <= now:
          raise TradeApprovalChallengeError(
            "AUTHORIZATION_EXPIRY_INVALID",
            "自动实盘授权期限已失效，请重新预览",
          )

        audit_event_id = str(uuid.uuid4())
        grant_exact_auto_exit_authorization(
          record,
          fingerprint=snapshot.fingerprint,
          challenge_id=normalized_id,
          user_id=current.user_id,
          device_session_id=current.device_session_id,
          authorized_at=now,
          authorization_expires_at=authorization_expires_at,
        )
        db.add(
          AutoExitPlanEvent(
            event_id=audit_event_id,
            business_key=f"auto-exit-authorized:{record.plan_id}:{normalized_id}",
            plan_id=str(record.plan_id),
            event_type="AUTO_EXIT_AUTHORIZED",
            payload={
              "actor_user_id": current.user_id,
              "device_session_id": current.device_session_id,
              "challenge_id": normalized_id,
              "plan_id": str(record.plan_id),
              "config_version": int(record.config_version or 0),
              "authorization_fingerprint": snapshot.fingerprint,
              "authorization_expires_at": authorization_expires_at.isoformat(),
            },
            created_at=now,
          )
        )
        challenge.consumed_at = now
        challenge.result_reference = {
          "plan_id": str(record.plan_id),
          "config_version": int(record.config_version or 0),
          "authorization_expires_at": authorization_expires_at.isoformat(),
          "audit_event_id": audit_event_id,
          "status": "AUTHORIZED",
        }
        await db.commit()
      except Exception:
        await db.rollback()
        raise

    return ExitPlanAuthorizationConfirmationData(
      challenge_id=normalized_id,
      plan_id=request.plan_id,
      config_version=request.expected_config_version,
      authorization_expires_at=_aware(authorization_expires_at),
      audit_event_id=audit_event_id,
    )

  @staticmethod
  def _existing_result(
    challenge: TradeConfirmationChallenge,
  ) -> ExitPlanAuthorizationConfirmationData:
    reference = dict(challenge.result_reference or {})
    if str(reference.get("status") or "") != "AUTHORIZED":
      raise TradeApprovalChallengeError(
        "CONFIRMATION_RESULT_PENDING",
        "授权确认已消费，但结果暂不可用，请刷新退出计划",
      )
    try:
      expires_at = datetime.fromisoformat(str(reference["authorization_expires_at"]))
      config_version = int(reference["config_version"])
    except (KeyError, TypeError, ValueError) as exc:
      raise TradeApprovalChallengeError(
        "CONFIRMATION_RESULT_PENDING",
        "授权确认结果不完整，请刷新退出计划",
      ) from exc
    return ExitPlanAuthorizationConfirmationData(
      challenge_id=str(challenge.id),
      plan_id=str(reference.get("plan_id") or ""),
      config_version=config_version,
      authorization_expires_at=_aware(expires_at),
      audit_event_id=str(reference.get("audit_event_id") or ""),
    )


__all__ = [
  "EXIT_PLAN_AUTHORIZATION_ACTION",
  "ExitPlanAuthorizationChallengeService",
  "ExitPlanAuthorizationConfirmationData",
  "ExitPlanAuthorizationPreviewData",
  "ExitPlanAuthorizationRequestData",
  "normalize_exit_plan_authorization_request",
]
