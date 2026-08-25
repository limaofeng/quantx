"""Device-bound two-phase controls for account-wide live execution authority."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models import TradeConfirmationChallenge
from quantx_infrastructure.models.agent_runtime import AccountExecutionControl
from quantx_infrastructure.services.account_execution_safety_service import (
  AccountExecutionControlIdempotencyError,
  AccountExecutionSafetyService,
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
from .types.trading_safety_types import AccountExecutionControlAction

ACCOUNT_EXECUTION_CONTROL_CHALLENGE = "ACCOUNT_EXECUTION_CONTROL"
_CHALLENGE_LIFETIME = timedelta(seconds=60)
_MAX_TOKEN_LENGTH = 256
_MAX_REASON_LENGTH = 512
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class AccountExecutionControlRequestData:
  account_id: str
  action: AccountExecutionControlAction
  state_version: int
  snapshot_id: str
  reason: str
  idempotency_key: str


@dataclass(frozen=True)
class AccountExecutionControlPreviewData:
  challenge_id: str
  confirmation_token: Optional[str]
  token_issued: bool
  request: AccountExecutionControlRequestData
  safety: dict[str, Any]
  challenge_expires_at: datetime
  challenge_status: str
  operation_status: str


@dataclass(frozen=True)
class AccountExecutionControlConfirmationData:
  challenge_id: str
  action: AccountExecutionControlAction
  account_id: str
  operation_status: str
  operation_code: str
  message: str
  safety: Optional[dict[str, Any]] = None


def normalize_account_execution_control_request(
  *,
  account_id: str,
  action: AccountExecutionControlAction | str,
  state_version: int,
  snapshot_id: str,
  reason: str,
  idempotency_key: str,
) -> AccountExecutionControlRequestData:
  normalized_account = str(account_id or "").strip()
  normalized_snapshot = str(snapshot_id or "").strip()
  normalized_reason = str(reason or "").strip()
  normalized_key = str(idempotency_key or "").strip()
  try:
    normalized_action = (
      action
      if isinstance(action, AccountExecutionControlAction)
      else AccountExecutionControlAction(str(action))
    )
  except ValueError as exc:
    raise TradeApprovalChallengeError(
      "INVALID_ACCOUNT_EXECUTION_CONTROL_ACTION",
      "账户执行控制动作无效",
    ) from exc
  if not normalized_account:
    raise TradeApprovalChallengeError("ACCOUNT_REQUIRED", "必须指定资金账号")
  if int(state_version) < 0:
    raise TradeApprovalChallengeError("INVALID_STATE_VERSION", "账户状态版本无效")
  if not _IDEMPOTENCY_KEY.fullmatch(normalized_key):
    raise TradeApprovalChallengeError(
      "INVALID_IDEMPOTENCY_KEY",
      "幂等键格式无效或超过 128 个字符",
    )
  if len(normalized_reason) > _MAX_REASON_LENGTH:
    raise TradeApprovalChallengeError("INVALID_REASON", "操作原因不能超过 512 个字符")
  if normalized_action == AccountExecutionControlAction.BEGIN_CONTROLLED_WINDOW:
    if not normalized_snapshot:
      raise TradeApprovalChallengeError("SNAPSHOT_REQUIRED", "必须绑定最新完整快照")
  elif normalized_snapshot:
    raise TradeApprovalChallengeError(
      "UNEXPECTED_SNAPSHOT",
      "当前账户控制动作不接受快照参数",
    )
  if (
    normalized_action
    in {
      AccountExecutionControlAction.PAUSE_RISK_INCREASE,
      AccountExecutionControlAction.KILL_SWITCH,
    }
    and not normalized_reason
  ):
    raise TradeApprovalChallengeError("REASON_REQUIRED", "暂停或紧急停止必须填写原因")
  return AccountExecutionControlRequestData(
    account_id=normalized_account,
    action=normalized_action,
    state_version=int(state_version),
    snapshot_id=normalized_snapshot,
    reason=normalized_reason,
    idempotency_key=normalized_key,
  )


def _request_binding(request: AccountExecutionControlRequestData) -> dict[str, Any]:
  return {
    "account_id": request.account_id,
    "action": request.action.value,
    "state_version": request.state_version,
    "snapshot_id": request.snapshot_id,
    "reason": request.reason,
    "idempotency_key": request.idempotency_key,
  }


def _request_from_payload(
  payload: dict[str, Any],
) -> AccountExecutionControlRequestData:
  if str(payload.get("action") or "") != ACCOUNT_EXECUTION_CONTROL_CHALLENGE:
    raise TradeApprovalChallengeError(
      "CONFIRMATION_CONTEXT_MISMATCH",
      "确认挑战不属于账户执行控制",
    )
  request = dict(payload.get("request") or {})
  return normalize_account_execution_control_request(
    account_id=str(request.get("account_id") or ""),
    action=str(request.get("action") or ""),
    state_version=int(request.get("state_version") or 0),
    snapshot_id=str(request.get("snapshot_id") or ""),
    reason=str(request.get("reason") or ""),
    idempotency_key=str(request.get("idempotency_key") or ""),
  )


def _safety_binding(safety: dict[str, Any]) -> dict[str, Any]:
  return {
    "authorization_state": str(safety.get("authorization_state") or ""),
    "state_version": int(safety.get("state_version") or 0),
    "snapshot_id": str(safety.get("snapshot_id") or ""),
    "snapshot_hash": str(safety.get("snapshot_hash") or ""),
    "execution_window_active": bool(safety.get("execution_window_active")),
    "controlled_window_snapshot_id": str(
      safety.get("controlled_window_snapshot_id") or ""
    ),
    "new_external_order_count": int(safety.get("new_external_order_count") or 0),
    "new_external_trade_count": int(safety.get("new_external_trade_count") or 0),
    "working_external_order_count": int(
      safety.get("working_external_order_count") or 0
    ),
    "checks": sorted(
      [
        {
          "code": str(item.get("code") or ""),
          "passed": bool(item.get("passed")),
        }
        for item in list(safety.get("checks") or [])
      ],
      key=lambda item: item["code"],
    ),
  }


def _canonical_hash(value: Any) -> str:
  return hashlib.sha256(
    json.dumps(
      value,
      ensure_ascii=True,
      separators=(",", ":"),
      sort_keys=True,
      default=str,
    ).encode("utf-8")
  ).hexdigest()


def _json_safe(value: Any) -> Any:
  return json.loads(json.dumps(value, default=str))


def _validate_action(
  request: AccountExecutionControlRequestData,
  safety: dict[str, Any],
) -> None:
  if int(safety.get("state_version") or 0) != request.state_version:
    raise TradeApprovalChallengeError(
      "ACCOUNT_EXECUTION_STATE_CHANGED",
      "账户执行控制状态已变化，请刷新后重试",
    )
  state = str(safety.get("authorization_state") or "DISABLED").upper()
  if request.action == AccountExecutionControlAction.BEGIN_CONTROLLED_WINDOW:
    if str(safety.get("snapshot_id") or "") != request.snapshot_id:
      raise TradeApprovalChallengeError(
        "SNAPSHOT_CHANGED",
        "完整快照已经更新，请刷新后重试",
      )
  elif request.action == AccountExecutionControlAction.ENABLE_RISK_INCREASE:
    if not bool(safety.get("can_activate_automation")):
      reason = next(iter(safety.get("blocked_reasons") or []), "账户买入条件未就绪")
      raise TradeApprovalChallengeError("ACCOUNT_EXECUTION_NOT_READY", str(reason))
  elif request.action == AccountExecutionControlAction.PAUSE_RISK_INCREASE:
    if state == "KILLED":
      raise TradeApprovalChallengeError(
        "ACCOUNT_KILL_SWITCH_ACTIVE",
        "账户紧急停止只能通过清除 kill switch 操作解除",
      )
  elif request.action == AccountExecutionControlAction.CLEAR_KILL_SWITCH:
    if state != "KILLED":
      raise TradeApprovalChallengeError(
        "ACCOUNT_NOT_KILLED",
        "账户未触发紧急停止，无需清除 kill switch",
      )


def _preview_from_challenge(
  challenge: TradeConfirmationChallenge,
  *,
  request: AccountExecutionControlRequestData,
  confirmation_token: Optional[str],
) -> AccountExecutionControlPreviewData:
  payload = dict(challenge.payload or {})
  result = dict(challenge.result_reference or {})
  return AccountExecutionControlPreviewData(
    challenge_id=str(challenge.id),
    confirmation_token=confirmation_token,
    token_issued=confirmation_token is not None,
    request=request,
    safety=dict(payload.get("safety_preview") or {}),
    challenge_expires_at=time_utils.to_shanghai(challenge.expires_at, keep_tz=True),
    challenge_status="CONSUMED" if challenge.consumed_at is not None else "PENDING",
    operation_status=str(result.get("operation_status") or "PENDING"),
  )


class AccountExecutionControlChallengeService:
  safety_service = AccountExecutionSafetyService()

  @classmethod
  async def _lock_principal(
    cls,
    db,
    principal: Principal,
    account_id: str,
  ) -> Principal:
    try:
      current = await AuthService(db).lock_and_validate_session(
        principal,
        required_permission="trade:approve",
        account_id=account_id,
      )
      current.require_permission("account-execution:control")
      current.require_account(account_id)
    except AuthError as exc:
      raise TradeApprovalChallengeError(exc.code, exc.message) from exc
    return current

  @classmethod
  async def issue(
    cls,
    *,
    principal: Principal,
    request: AccountExecutionControlRequestData,
  ) -> AccountExecutionControlPreviewData:
    raw_token = secrets.token_urlsafe(48)
    now = time_utils.now()
    try:
      async with AsyncSessionLocal() as db:
        current = await cls._lock_principal(db, principal, request.account_id)
        existing = (
          await db.execute(
            select(TradeConfirmationChallenge)
            .where(
              TradeConfirmationChallenge.user_id == current.user_id,
              TradeConfirmationChallenge.account_id == request.account_id,
              TradeConfirmationChallenge.action == ACCOUNT_EXECUTION_CONTROL_CHALLENGE,
              TradeConfirmationChallenge.idempotency_key == request.idempotency_key,
            )
            .with_for_update()
          )
        ).scalar_one_or_none()
        if existing is not None:
          payload = dict(existing.payload or {})
          if _request_binding(_request_from_payload(payload)) != _request_binding(
            request
          ):
            raise TradeApprovalChallengeError(
              "IDEMPOTENCY_CONFLICT",
              "该幂等键已绑定不同的账户执行控制",
            )
          if (
            existing.consumed_at is None
            and time_utils.to_shanghai(existing.expires_at) <= now
          ):
            raise TradeApprovalChallengeError(
              "IDEMPOTENCY_KEY_ALREADY_USED",
              "该幂等键对应的确认已过期，请使用新的幂等键",
            )
          return _preview_from_challenge(
            existing,
            request=request,
            confirmation_token=None,
          )
        safety = await cls.safety_service.status(request.account_id)
        _validate_action(request, safety)
        payload = {
          "action": ACCOUNT_EXECUTION_CONTROL_CHALLENGE,
          "request": _request_binding(request),
          "session_binding": {
            "user_id": current.user_id,
            "device_session_id": current.device_session_id,
            "account_id": current.require_account(),
          },
          "safety_fingerprint": _canonical_hash(_safety_binding(safety)),
          "safety_preview": _json_safe(safety),
        }
        challenge = TradeConfirmationChallenge(
          id=str(uuid.uuid4()),
          action=ACCOUNT_EXECUTION_CONTROL_CHALLENGE,
          user_id=current.user_id,
          device_session_id=current.device_session_id,
          account_id=request.account_id,
          idempotency_key=request.idempotency_key,
          payload=payload,
          payload_fingerprint=signed_payload_fingerprint(payload),
          token_digest=challenge_token_digest(raw_token),
          expires_at=now + _CHALLENGE_LIFETIME,
          consumed_at=None,
          result_reference={
            "challenge_status": "PENDING",
            "operation_status": "PENDING",
          },
        )
        db.add(challenge)
        await db.commit()
    except IntegrityError as exc:
      raise TradeApprovalChallengeError(
        "IDEMPOTENCY_CONFLICT",
        "账户执行控制幂等状态冲突，请刷新后重试",
      ) from exc
    return _preview_from_challenge(
      challenge,
      request=request,
      confirmation_token=raw_token,
    )

  @classmethod
  async def confirm(
    cls,
    *,
    principal: Principal,
    challenge_id: str,
    confirmation_token: str,
  ) -> AccountExecutionControlConfirmationData:
    normalized_id = str(challenge_id or "").strip()
    token = str(confirmation_token or "")
    if not normalized_id or not token or len(token) > _MAX_TOKEN_LENGTH:
      raise TradeApprovalChallengeError(
        "INVALID_CONFIRMATION_TOKEN",
        "确认凭据无效，请重新获取预览",
      )
    async with AsyncSessionLocal() as db:
      routing = await db.get(TradeConfirmationChallenge, normalized_id)
      if routing is None:
        raise TradeApprovalChallengeError("CONFIRMATION_NOT_FOUND", "确认挑战不存在")
      request = _request_from_payload(dict(routing.payload or {}))
      current = await cls._lock_principal(db, principal, request.account_id)
      control = await db.get(
        AccountExecutionControl,
        request.account_id,
        with_for_update=True,
      )
      challenge = (
        await db.execute(
          select(TradeConfirmationChallenge)
          .where(TradeConfirmationChallenge.id == normalized_id)
          .with_for_update()
        )
      ).scalar_one_or_none()
      if challenge is None:
        raise TradeApprovalChallengeError("CONFIRMATION_NOT_FOUND", "确认挑战不存在")
      payload = dict(challenge.payload or {})
      try:
        validate_persistent_trade_challenge(
          challenge=challenge,
          principal=current,
          action=ACCOUNT_EXECUTION_CONTROL_CHALLENGE,
          confirmation_token=token,
          now=time_utils.now(),
          payload=payload,
          allow_consumed=True,
        )
      except AuthError as exc:
        raise TradeApprovalChallengeError(exc.code, exc.message) from exc
      if not hmac.compare_digest(
        str(challenge.payload_fingerprint or ""),
        signed_payload_fingerprint(payload),
      ):
        raise TradeApprovalChallengeError(
          "TRADE_PAYLOAD_CHANGED",
          "账户执行控制内容已变化，请重新预览",
        )
      if challenge.consumed_at is not None:
        result = dict(challenge.result_reference or {})
        if str(result.get("operation_status") or "") != "DISPATCHING":
          return AccountExecutionControlConfirmationData(
            challenge_id=normalized_id,
            action=request.action,
            account_id=request.account_id,
            operation_status=str(result.get("operation_status") or "REJECTED"),
            operation_code=str(result.get("operation_code") or "REJECTED"),
            message=str(result.get("message") or "账户执行控制已结束"),
            safety=(
              dict(result["safety"]) if isinstance(result.get("safety"), dict) else None
            ),
          )
      else:
        if control is None or int(control.state_version) != request.state_version:
          raise TradeApprovalChallengeError(
            "ACCOUNT_EXECUTION_STATE_CHANGED",
            "账户执行控制状态已变化，请重新预览",
          )
        safety = await cls.safety_service.status(request.account_id)
        _validate_action(request, safety)
        if request.action in {
          AccountExecutionControlAction.BEGIN_CONTROLLED_WINDOW,
          AccountExecutionControlAction.ENABLE_RISK_INCREASE,
        } and not hmac.compare_digest(
          _canonical_hash(_safety_binding(safety)),
          str(payload.get("safety_fingerprint") or ""),
        ):
          raise TradeApprovalChallengeError(
            "READINESS_CHANGED",
            "账户执行安全快照已变化，请重新预览",
          )
        challenge.consumed_at = time_utils.now()
        challenge.result_reference = {
          "challenge_status": "CONSUMED",
          "operation_status": "DISPATCHING",
        }
        await db.commit()

    try:
      if request.action == AccountExecutionControlAction.BEGIN_CONTROLLED_WINDOW:
        safety = await cls.safety_service.begin_controlled_window(
          request.account_id,
          user_id=current.user_id,
          snapshot_id=request.snapshot_id,
          expected_state_version=request.state_version,
          operation_id=normalized_id,
        )
      else:
        target_state = {
          AccountExecutionControlAction.ENABLE_RISK_INCREASE: "ENABLED",
          AccountExecutionControlAction.PAUSE_RISK_INCREASE: "PAUSED",
          AccountExecutionControlAction.KILL_SWITCH: "KILLED",
          AccountExecutionControlAction.CLEAR_KILL_SWITCH: "DISABLED",
        }[request.action]
        safety = await cls.safety_service.set_authorization_state(
          request.account_id,
          target_state=target_state,
          user_id=current.user_id,
          expected_state_version=request.state_version,
          operation_id=normalized_id,
          reason=request.reason,
        )
      status, code, message = (
        "APPLIED",
        f"{request.action.value}_APPLIED",
        "账户执行控制已应用；委托与成交终态仍以券商回报为准",
      )
    except (ValueError, AccountExecutionControlIdempotencyError) as exc:
      safety = None
      status, code, message = "REJECTED", "ACCOUNT_EXECUTION_CONTROL_REJECTED", str(exc)
    async with AsyncSessionLocal() as db:
      challenge = await db.get(
        TradeConfirmationChallenge,
        normalized_id,
        with_for_update=True,
      )
      if challenge is None:
        raise TradeApprovalChallengeError("CONFIRMATION_NOT_FOUND", "确认挑战不存在")
      challenge.result_reference = {
        "challenge_status": "CONSUMED",
        "operation_status": status,
        "operation_code": code,
        "message": message[:512],
        **({"safety": _json_safe(safety)} if safety is not None else {}),
      }
      await db.commit()
    return AccountExecutionControlConfirmationData(
      challenge_id=normalized_id,
      action=request.action,
      account_id=request.account_id,
      operation_status=status,
      operation_code=code,
      message=message,
      safety=safety,
    )


__all__ = [
  "ACCOUNT_EXECUTION_CONTROL_CHALLENGE",
  "AccountExecutionControlChallengeService",
  "AccountExecutionControlConfirmationData",
  "AccountExecutionControlPreviewData",
  "AccountExecutionControlRequestData",
  "normalize_account_execution_control_request",
]
