"""Device-bound two-phase controls for account-level T-trade rollout risk."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models import TradeConfirmationChallenge
from quantx_infrastructure.models.agent_runtime import (
  AccountTradingRollout,
  AccountTradingRolloutEvent,
)
from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationsService,
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
from .types.t_trade_types import TTradeControlAction, TTradeRolloutTarget

T_TRADE_CONTROL_CHALLENGE = "T_TRADE_CONTROL"
_CHALLENGE_LIFETIME = timedelta(seconds=60)
_DISPATCH_LEASE_LIFETIME = timedelta(seconds=30)
_MAX_TOKEN_LENGTH = 256
_MAX_IDEMPOTENCY_KEY_LENGTH = 128
_MAX_REASON_LENGTH = 512
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_PREPARATION_GATE_CODES = frozenset(
  {
    "SERVER_REAL_TRADING_ENABLED",
    "T_TRADE_LIVE_ENABLED",
    "ACCOUNT_ALLOWLISTED",
    "ENGINE_READY",
    "LIVE_AGENT_READY",
    "AGENT_MODE_LIVE",
    "PROTOCOL_1_1",
    "ROLLOUT_CONFIGURED",
    "SNAPSHOT_RECONCILED",
    "SNAPSHOT_FRESH",
    "SNAPSHOT_ACTIVITY_CLASSIFIED",
    "RECENT_BACKUP",
    "NO_CRITICAL_ALERTS",
    "NO_DEAD_LETTERS",
    "KILL_SWITCH_CLEAR",
  }
)
_ACTIVATION_GATE_CODES = _PREPARATION_GATE_CODES | frozenset(
  {
    "CONTROLLED_WINDOW_ACTIVE",
    "NO_EXTERNAL_BROKER_ACTIVITY",
  }
)

_CONTROL_EVENT_TYPES = {
  TTradeControlAction.BEGIN_CONTROLLED_WINDOW: "CONTROLLED_WINDOW_STARTED",
  TTradeControlAction.ACTIVATE_CANARY: "CANARY_ACTIVATED",
  TTradeControlAction.ACTIVATE_LIVE: "LIVE_ACTIVATED",
  TTradeControlAction.KILL_SWITCH: "KILL_SWITCHED",
}

_CONTROL_APPLIED_CODES = {
  TTradeControlAction.BEGIN_CONTROLLED_WINDOW: "CONTROLLED_WINDOW_APPLIED",
  TTradeControlAction.ACTIVATE_CANARY: "CANARY_ACTIVATION_APPLIED",
  TTradeControlAction.ACTIVATE_LIVE: "LIVE_ACTIVATION_APPLIED",
  TTradeControlAction.KILL_SWITCH: "KILL_SWITCH_APPLIED",
}


@dataclass(frozen=True)
class TTradeControlRequestData:
  account_id: str
  action: TTradeControlAction
  policy_version: int
  snapshot_id: str
  target_stage: Optional[TTradeRolloutTarget]
  reason: str
  idempotency_key: str


@dataclass(frozen=True)
class TTradeControlPreviewData:
  challenge_id: str
  confirmation_token: Optional[str] = field(repr=False)
  token_issued: bool
  request: TTradeControlRequestData
  readiness: dict[str, Any]
  readiness_fingerprint: str
  current_stage: str
  challenge_expires_at: datetime
  challenge_status: str
  operation_status: str


@dataclass(frozen=True)
class TTradeControlConfirmationData:
  challenge_id: str
  action: TTradeControlAction
  account_id: str
  challenge_consumed: bool
  operation_status: str
  operation_code: str
  message: str
  readiness: Optional[dict[str, Any]] = None

  @property
  def applied(self) -> bool:
    return self.operation_status == "APPLIED"


def _enum_value(value: Any) -> str:
  return str(getattr(value, "value", value) or "").strip().upper()


def _clean_reason(value: str, *, required: bool, default: str) -> str:
  normalized = str(value or "").strip()
  if not normalized:
    if required:
      raise TradeApprovalChallengeError(
        "CONTROL_REASON_REQUIRED",
        "该做 T 控制动作必须提供原因",
      )
    return default
  if len(normalized) > _MAX_REASON_LENGTH or any(
    ord(character) < 32 or ord(character) == 127 for character in normalized
  ):
    raise TradeApprovalChallengeError(
      "INVALID_CONTROL_REASON",
      "做 T 控制原因无效或过长",
    )
  return normalized


def normalize_t_trade_control_request(
  *,
  account_id: str,
  action: TTradeControlAction | str,
  policy_version: int,
  snapshot_id: str,
  target_stage: TTradeRolloutTarget | str | None,
  reason: str,
  idempotency_key: str,
) -> TTradeControlRequestData:
  normalized_account = str(account_id or "").strip()
  if not normalized_account or len(normalized_account) > 50:
    raise TradeApprovalChallengeError("ACCOUNT_REQUIRED", "必须指定资金账号")
  try:
    normalized_action = (
      action
      if isinstance(action, TTradeControlAction)
      else TTradeControlAction(_enum_value(action))
    )
  except ValueError as exc:
    raise TradeApprovalChallengeError(
      "INVALID_T_TRADE_CONTROL_ACTION",
      "做 T 控制动作无效",
    ) from exc
  if isinstance(policy_version, bool):
    raise TradeApprovalChallengeError("INVALID_POLICY_VERSION", "策略版本无效")
  try:
    normalized_policy_version = int(policy_version)
  except (TypeError, ValueError) as exc:
    raise TradeApprovalChallengeError("INVALID_POLICY_VERSION", "策略版本无效") from exc
  if normalized_policy_version < 1 or normalized_policy_version > 2_147_483_647:
    raise TradeApprovalChallengeError("INVALID_POLICY_VERSION", "策略版本无效")

  normalized_snapshot = str(snapshot_id or "").strip()
  if len(normalized_snapshot) > 128:
    raise TradeApprovalChallengeError("INVALID_SNAPSHOT_ID", "安全快照标识无效")
  if normalized_action != TTradeControlAction.KILL_SWITCH and not normalized_snapshot:
    raise TradeApprovalChallengeError(
      "SNAPSHOT_REQUIRED",
      "该做 T 控制动作必须绑定当前完整快照",
    )

  expected_target: Optional[TTradeRolloutTarget]
  if normalized_action == TTradeControlAction.ACTIVATE_CANARY:
    expected_target = TTradeRolloutTarget.CANARY
  elif normalized_action == TTradeControlAction.ACTIVATE_LIVE:
    expected_target = TTradeRolloutTarget.LIVE
  else:
    expected_target = None
  if target_stage is None:
    normalized_target = expected_target
  else:
    try:
      normalized_target = (
        target_stage
        if isinstance(target_stage, TTradeRolloutTarget)
        else TTradeRolloutTarget(_enum_value(target_stage))
      )
    except ValueError as exc:
      raise TradeApprovalChallengeError(
        "INVALID_TARGET_STAGE",
        "做 T 灰度目标无效",
      ) from exc
  if normalized_target != expected_target:
    raise TradeApprovalChallengeError(
      "CONTROL_ACTION_TARGET_MISMATCH",
      "做 T 控制动作与目标阶段不一致",
    )

  default_reasons = {
    TTradeControlAction.BEGIN_CONTROLLED_WINDOW: "begin controlled window",
    TTradeControlAction.ACTIVATE_CANARY: "activate canary",
    TTradeControlAction.ACTIVATE_LIVE: "activate live",
    TTradeControlAction.KILL_SWITCH: "",
  }
  normalized_reason = _clean_reason(
    reason,
    required=normalized_action == TTradeControlAction.KILL_SWITCH,
    default=default_reasons[normalized_action],
  )
  normalized_key = str(idempotency_key or "").strip()
  if (
    not normalized_key
    or len(normalized_key) > _MAX_IDEMPOTENCY_KEY_LENGTH
    or not _IDEMPOTENCY_KEY.fullmatch(normalized_key)
  ):
    raise TradeApprovalChallengeError(
      "INVALID_IDEMPOTENCY_KEY",
      "幂等键格式无效或超过 128 个字符",
    )
  return TTradeControlRequestData(
    account_id=normalized_account,
    action=normalized_action,
    policy_version=normalized_policy_version,
    snapshot_id=normalized_snapshot,
    target_stage=normalized_target,
    reason=normalized_reason,
    idempotency_key=normalized_key,
  )


def _json_safe(value: Any) -> Any:
  if isinstance(value, datetime):
    return time_utils.to_shanghai(value, keep_tz=True).isoformat()
  if isinstance(value, dict):
    return {str(key): _json_safe(item) for key, item in value.items()}
  if isinstance(value, (list, tuple)):
    return [_json_safe(item) for item in value]
  return value


def _canonical_hash(value: Any) -> str:
  encoded = json.dumps(
    _json_safe(value),
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
    default=str,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def _dispatch_reference(
  *,
  now: datetime,
  attempt: int,
) -> dict[str, Any]:
  return {
    "challenge_status": "CONSUMED",
    "operation_status": "DISPATCHING",
    "operation_code": "T_TRADE_CONTROL_DISPATCHING",
    "message": "确认已消费，正在应用做 T 控制",
    "dispatch_lease_id": str(uuid.uuid4()),
    "dispatch_attempt": max(1, int(attempt)),
    "dispatch_started_at": now.isoformat(),
    "dispatch_lease_expires_at": (now + _DISPATCH_LEASE_LIFETIME).isoformat(),
  }


def _dispatch_lease_is_fresh(
  result: dict[str, Any],
  *,
  now: datetime,
) -> bool:
  value = str(result.get("dispatch_lease_expires_at") or "")
  try:
    expires_at = datetime.fromisoformat(value)
  except ValueError:
    return False
  return time_utils.to_shanghai(expires_at) > now


def _applied_reference(action: TTradeControlAction) -> dict[str, Any]:
  return {
    "challenge_status": "CONSUMED",
    "operation_status": "APPLIED",
    "operation_code": _CONTROL_APPLIED_CODES[action],
    "message": "做 T 控制已应用；委托与成交终态仍以券商回报为准",
  }


async def _operation_marker_exists(
  db,
  *,
  challenge_id: str,
  request: TTradeControlRequestData,
) -> bool:
  event = await db.get(AccountTradingRolloutEvent, challenge_id)
  if event is None:
    return False
  details = dict(event.details or {})
  context_matches = str(details.get("operationId") or "") == challenge_id
  if request.action == TTradeControlAction.BEGIN_CONTROLLED_WINDOW:
    context_matches = context_matches and str(event.snapshot_id or "") == (
      request.snapshot_id
    )
  elif request.action in {
    TTradeControlAction.ACTIVATE_CANARY,
    TTradeControlAction.ACTIVATE_LIVE,
  }:
    context_matches = (
      context_matches
      and str(event.snapshot_id or "") == request.snapshot_id
      and str(details.get("targetStage") or "")
      == (request.target_stage.value if request.target_stage else "")
      and int(details.get("policyVersion") or 0) == request.policy_version
    )
  else:
    context_matches = (
      context_matches
      and str(details.get("reason") or "") == request.reason
    )
  if (
    str(event.account_id) != request.account_id
    or str(event.event_type) != _CONTROL_EVENT_TYPES[request.action]
    or not context_matches
  ):
    raise TradeApprovalChallengeError(
      "CONTROL_OPERATION_MARKER_CONFLICT",
      "做 T 控制幂等标识已绑定其他操作",
    )
  return True


def _request_binding(request: TTradeControlRequestData) -> dict[str, Any]:
  return {
    "account_id": request.account_id,
    "control_action": request.action.value,
    "policy_version": request.policy_version,
    "snapshot_id": request.snapshot_id,
    "target_stage": request.target_stage.value if request.target_stage else "",
    "reason": request.reason,
    "idempotency_key": request.idempotency_key,
  }


def _request_from_payload(payload: dict[str, Any]) -> TTradeControlRequestData:
  if str(payload.get("action") or "") != T_TRADE_CONTROL_CHALLENGE:
    raise TradeApprovalChallengeError(
      "CONFIRMATION_CONTEXT_MISMATCH",
      "做 T 控制确认上下文无效",
    )
  binding = dict(payload.get("request_binding") or {})
  return normalize_t_trade_control_request(
    account_id=str(binding.get("account_id") or ""),
    action=str(binding.get("control_action") or ""),
    policy_version=binding.get("policy_version"),
    snapshot_id=str(binding.get("snapshot_id") or ""),
    target_stage=(str(binding.get("target_stage") or "") or None),
    reason=str(binding.get("reason") or ""),
    idempotency_key=str(binding.get("idempotency_key") or ""),
  )


def _rollout_binding(rollout: Optional[AccountTradingRollout]) -> dict[str, Any]:
  return {
    "exists": rollout is not None,
    "stage": str(rollout.stage if rollout else "SHADOW").upper(),
    "enabled": bool(rollout and rollout.enabled),
    "kill_switch": bool(rollout and rollout.kill_switch),
    "reconcile_status": str(rollout.reconcile_status if rollout else "UNKNOWN").upper(),
    "policy_version": int(rollout.policy_version if rollout else 1),
    "acknowledged_policy_version": int(
      rollout.acknowledged_policy_version if rollout else 0
    ),
    "snapshot_id": str(rollout.last_snapshot_id if rollout else ""),
    "snapshot_hash": str(rollout.last_snapshot_hash if rollout else ""),
    "snapshot_at": _json_safe(rollout.last_snapshot_at) if rollout else None,
    "controlled_window_active": bool(rollout and rollout.controlled_window_active),
    "controlled_window_snapshot_id": str(
      rollout.controlled_window_snapshot_id if rollout else ""
    ),
    "controlled_window_snapshot_hash": str(
      rollout.controlled_window_snapshot_hash if rollout else ""
    ),
  }


def _readiness_binding(readiness: dict[str, Any]) -> dict[str, Any]:
  checks = sorted(
    [
      {
        "code": str(item.get("code") or ""),
        "passed": bool(item.get("passed")),
        "scope": str(item.get("scope") or ""),
      }
      for item in list(readiness.get("checks") or [])
    ],
    key=lambda item: item["code"],
  )
  return {
    "status": str(readiness.get("status") or ""),
    "stage": str(readiness.get("stage") or ""),
    "preparation_ready": bool(readiness.get("preparation_ready")),
    "automation_ready": bool(readiness.get("automation_ready")),
    "engine_status": str(readiness.get("engine_status") or ""),
    "agent_status": str(readiness.get("agent_status") or ""),
    "agent_device_id": str(readiness.get("agent_device_id") or ""),
    "ready_live_agent_count": int(readiness.get("ready_live_agent_count") or 0),
    "agent_mode": str(readiness.get("agent_mode") or ""),
    "protocol_version": str(readiness.get("protocol_version") or ""),
    "reconcile_status": str(readiness.get("reconcile_status") or ""),
    "kill_switch": bool(readiness.get("kill_switch")),
    "policy_version": int(readiness.get("policy_version") or 0),
    "snapshot_id": str(readiness.get("snapshot_id") or ""),
    "snapshot_hash": str(readiness.get("snapshot_hash") or ""),
    "snapshot_at": _json_safe(readiness.get("snapshot_at")),
    "controlled_window_active": bool(readiness.get("controlled_window_active")),
    "controlled_window_snapshot_id": str(
      readiness.get("controlled_window_snapshot_id") or ""
    ),
    "new_external_order_count": int(readiness.get("new_external_order_count") or 0),
    "new_external_trade_count": int(readiness.get("new_external_trade_count") or 0),
    "working_external_order_count": int(
      readiness.get("working_external_order_count") or 0
    ),
    "queued_command_count": int(readiness.get("queued_command_count") or 0),
    "dead_letter_count": int(readiness.get("dead_letter_count") or 0),
    "unresolved_critical_alert_count": int(
      readiness.get("unresolved_critical_alert_count") or 0
    ),
    "checks": checks,
  }


def _readiness_preview(readiness: dict[str, Any]) -> dict[str, Any]:
  return _json_safe(
    {
      **_readiness_binding(readiness),
      "checks": [
        {
          "code": str(item.get("code") or ""),
          "passed": bool(item.get("passed")),
          "message": str(item.get("message") or ""),
          "scope": str(item.get("scope") or ""),
        }
        for item in list(readiness.get("checks") or [])
      ],
    }
  )


def _kill_preview(
  account_id: str,
  rollout: Optional[AccountTradingRollout],
) -> dict[str, Any]:
  """Return display-only state without consulting ordinary live readiness.

  A kill switch is a risk-reducing action.  It must remain confirmable when the
  Agent, Engine, snapshot, allowlist, or real-trading gates are unavailable.
  """

  binding = _rollout_binding(rollout)
  return {
    "account_id": account_id,
    "status": "RISK_REDUCTION_READY",
    "stage": str(binding["stage"]),
    "preparation_ready": False,
    "automation_ready": False,
    "policy_version": int(binding["policy_version"]),
    "kill_switch": bool(binding["kill_switch"]),
    "checks": [],
  }


def _gate_failure(
  readiness: dict[str, Any],
  required_codes: frozenset[str],
) -> Optional[str]:
  by_code = {
    str(item.get("code") or ""): item for item in list(readiness.get("checks") or [])
  }
  missing = sorted(required_codes - set(by_code))
  if missing:
    return f"安全门禁缺少检查项：{missing[0]}"
  failed = [
    by_code[code] for code in sorted(required_codes) if not by_code[code]["passed"]
  ]
  if not failed:
    return None
  first = failed[0]
  return str(first.get("message") or first.get("code") or "做 T 实盘未就绪")


def _validate_action_readiness(
  request: TTradeControlRequestData,
  readiness: dict[str, Any],
  rollout: Optional[AccountTradingRollout],
) -> None:
  binding = _rollout_binding(rollout)
  if request.action == TTradeControlAction.KILL_SWITCH:
    return
  if request.policy_version != int(binding["policy_version"]):
    raise TradeApprovalChallengeError(
      "POLICY_VERSION_CONFLICT",
      "做 T 自动退出策略版本已变化，请刷新后重新预览",
    )
  if rollout is None:
    raise TradeApprovalChallengeError(
      "ROLLOUT_NOT_CONFIGURED",
      "账户尚未创建实盘灰度配置",
    )
  if request.snapshot_id != str(readiness.get("snapshot_id") or "") or not str(
    readiness.get("snapshot_hash") or ""
  ):
    raise TradeApprovalChallengeError(
      "SNAPSHOT_CHANGED",
      "完整安全快照已变化，请刷新后重新预览",
    )
  required = (
    _PREPARATION_GATE_CODES
    if request.action == TTradeControlAction.BEGIN_CONTROLLED_WINDOW
    else _ACTIVATION_GATE_CODES
  )
  failure = _gate_failure(readiness, required)
  if failure:
    raise TradeApprovalChallengeError("T_TRADE_CONTROL_NOT_READY", failure)
  if (
    int(readiness.get("ready_live_agent_count") or 0) != 1
    or str(readiness.get("agent_mode") or "").lower() != "live"
    or str(readiness.get("protocol_version") or "") != "1.1"
    or str(readiness.get("reconcile_status") or "").upper() != "READY"
    or bool(readiness.get("kill_switch"))
  ):
    raise TradeApprovalChallengeError(
      "T_TRADE_CONTROL_NOT_READY",
      "Agent、对账或账户安全状态不满足实盘控制要求",
    )
  if request.action == TTradeControlAction.BEGIN_CONTROLLED_WINDOW:
    if (
      str(binding["stage"]) not in {"SHADOW", "PAUSED"}
      or bool(binding["enabled"])
      or bool(binding["kill_switch"])
      or bool(binding["controlled_window_active"])
    ):
      raise TradeApprovalChallengeError(
        "ROLLOUT_STATE_CONFLICT",
        "当前灰度状态不允许建立新的受控交易窗口",
      )
    return
  if (
    str(binding["stage"]) != "SHADOW"
    or not bool(binding["controlled_window_active"])
    or str(binding["controlled_window_snapshot_id"]) != request.snapshot_id
  ):
    raise TradeApprovalChallengeError(
      "ROLLOUT_STATE_CONFLICT",
      "当前灰度状态或受控交易窗口不允许启用目标阶段",
    )


def _payload(
  *,
  principal: Principal,
  request: TTradeControlRequestData,
  rollout_binding: dict[str, Any],
  readiness: dict[str, Any],
) -> dict[str, Any]:
  readiness_binding = _readiness_binding(readiness)
  return {
    "action": T_TRADE_CONTROL_CHALLENGE,
    "request_binding": _request_binding(request),
    "session_binding": {
      "user_id": principal.user_id,
      "device_session_id": principal.device_session_id,
      "account_id": principal.require_account(),
    },
    "rollout_binding": rollout_binding,
    "readiness_fingerprint": _canonical_hash(readiness_binding),
    "readiness_preview": _readiness_preview(readiness),
  }


def _require_native_control_principal(
  principal: Principal,
  account_id: str,
) -> None:
  try:
    principal.require_permission("t-trade:control")
    principal.require_permission("trade:approve")
    principal.require_account(account_id)
  except AuthError as exc:
    raise TradeApprovalChallengeError(exc.code, exc.message) from exc
  if not principal.is_native_session or principal.authorized_account_ids != (account_id,):
    raise TradeApprovalChallengeError(
      "NATIVE_SESSION_ACCOUNT_REQUIRED",
      "做 T 原生控制只能作用于当前设备会话的唯一主账户",
    )


def _validate_stored_payload(
  challenge: TradeConfirmationChallenge,
  *,
  principal: Principal,
  request: TTradeControlRequestData,
) -> dict[str, Any]:
  payload = dict(challenge.payload or {})
  if (
    str(challenge.action) != T_TRADE_CONTROL_CHALLENGE
    or str(challenge.user_id) != principal.user_id
    or str(challenge.device_session_id) != principal.device_session_id
    or str(challenge.account_id) != request.account_id
  ):
    raise TradeApprovalChallengeError(
      "IDEMPOTENCY_CONTEXT_MISMATCH",
      "该幂等键已绑定其他用户、设备、账户或控制动作",
    )
  if not hmac.compare_digest(
    str(challenge.payload_fingerprint or ""),
    signed_payload_fingerprint(payload),
  ):
    raise TradeApprovalChallengeError(
      "TRADE_PAYLOAD_CHANGED",
      "做 T 控制内容已变化，请使用新的幂等键重新预览",
    )
  stored_request = _request_from_payload(payload)
  if _request_binding(stored_request) != _request_binding(request):
    raise TradeApprovalChallengeError(
      "IDEMPOTENCY_CONFLICT",
      "该幂等键已绑定不同的做 T 控制内容",
    )
  return payload


def _preview_from_challenge(
  challenge: TradeConfirmationChallenge,
  *,
  request: TTradeControlRequestData,
  payload: dict[str, Any],
  confirmation_token: Optional[str],
) -> TTradeControlPreviewData:
  result = dict(challenge.result_reference or {})
  challenge_status = "CONSUMED" if challenge.consumed_at is not None else "PENDING"
  operation_status = str(result.get("operation_status") or "PENDING")
  return TTradeControlPreviewData(
    challenge_id=str(challenge.id),
    confirmation_token=confirmation_token,
    token_issued=confirmation_token is not None,
    request=request,
    readiness=dict(payload.get("readiness_preview") or {}),
    readiness_fingerprint=str(payload.get("readiness_fingerprint") or ""),
    current_stage=str(
      dict(payload.get("rollout_binding") or {}).get("stage") or "UNKNOWN"
    ),
    challenge_expires_at=time_utils.to_shanghai(
      challenge.expires_at,
      keep_tz=True,
    ),
    challenge_status=challenge_status,
    operation_status=operation_status,
  )


class TTradeControlChallengeService:
  operations_service = TTradeOperationsService()

  @classmethod
  async def _lock_current_principal(
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
      current.require_permission("t-trade:control")
    except AuthError as exc:
      raise TradeApprovalChallengeError(exc.code, exc.message) from exc
    _require_native_control_principal(current, account_id)
    return current

  @classmethod
  async def issue(
    cls,
    *,
    principal: Principal,
    request: TTradeControlRequestData,
  ) -> TTradeControlPreviewData:
    _require_native_control_principal(principal, request.account_id)
    raw_token = secrets.token_urlsafe(48)
    now = time_utils.now()
    challenge: TradeConfirmationChallenge
    try:
      async with AsyncSessionLocal() as db:
        current = await cls._lock_current_principal(
          db,
          principal,
          request.account_id,
        )
        existing = (
          await db.execute(
            select(TradeConfirmationChallenge)
            .where(
              TradeConfirmationChallenge.user_id == current.user_id,
              TradeConfirmationChallenge.account_id == request.account_id,
              TradeConfirmationChallenge.action == T_TRADE_CONTROL_CHALLENGE,
              TradeConfirmationChallenge.idempotency_key == request.idempotency_key,
            )
            .with_for_update()
          )
        ).scalar_one_or_none()
        if existing is not None:
          payload = _validate_stored_payload(
            existing,
            principal=current,
            request=request,
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
            payload=payload,
            confirmation_token=None,
          )

        rollout = await db.get(
          AccountTradingRollout,
          request.account_id,
          with_for_update=True,
        )
        readiness = (
          _kill_preview(request.account_id, rollout)
          if request.action == TTradeControlAction.KILL_SWITCH
          else await cls.operations_service.readiness(request.account_id)
        )
        _validate_action_readiness(request, readiness, rollout)
        payload = _payload(
          principal=current,
          request=request,
          rollout_binding=_rollout_binding(rollout),
          readiness=readiness,
        )
        challenge = TradeConfirmationChallenge(
          id=str(uuid.uuid4()),
          action=T_TRADE_CONTROL_CHALLENGE,
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
    except IntegrityError:
      return await cls._recover_idempotent_preview(
        principal=principal,
        request=request,
      )
    return _preview_from_challenge(
      challenge,
      request=request,
      payload=dict(challenge.payload or {}),
      confirmation_token=raw_token,
    )

  @classmethod
  async def _recover_idempotent_preview(
    cls,
    *,
    principal: Principal,
    request: TTradeControlRequestData,
  ) -> TTradeControlPreviewData:
    async with AsyncSessionLocal() as db:
      current = await cls._lock_current_principal(
        db,
        principal,
        request.account_id,
      )
      challenge = (
        await db.execute(
          select(TradeConfirmationChallenge)
          .where(
            TradeConfirmationChallenge.user_id == current.user_id,
            TradeConfirmationChallenge.account_id == request.account_id,
            TradeConfirmationChallenge.action == T_TRADE_CONTROL_CHALLENGE,
            TradeConfirmationChallenge.idempotency_key == request.idempotency_key,
          )
          .with_for_update()
        )
      ).scalar_one_or_none()
      if challenge is None:
        raise TradeApprovalChallengeError(
          "IDEMPOTENCY_CONFLICT",
          "做 T 控制幂等状态无法恢复，请使用新的幂等键",
        )
      payload = _validate_stored_payload(
        challenge,
        principal=current,
        request=request,
      )
      if (
        challenge.consumed_at is None
        and time_utils.to_shanghai(challenge.expires_at) <= time_utils.now()
      ):
        raise TradeApprovalChallengeError(
          "IDEMPOTENCY_KEY_ALREADY_USED",
          "该幂等键对应的确认已过期，请使用新的幂等键",
        )
      return _preview_from_challenge(
        challenge,
        request=request,
        payload=payload,
        confirmation_token=None,
      )

  @classmethod
  async def confirm(
    cls,
    *,
    principal: Principal,
    challenge_id: str,
    confirmation_token: str,
  ) -> TTradeControlConfirmationData:
    normalized_id = str(challenge_id or "").strip()
    token = str(confirmation_token or "")
    if not normalized_id or not token or len(token) > _MAX_TOKEN_LENGTH:
      raise TradeApprovalChallengeError(
        "INVALID_CONFIRMATION_TOKEN",
        "确认凭据无效，请重新获取预览",
      )

    request: TTradeControlRequestData
    actor_user_id: str
    readiness: Optional[dict[str, Any]] = None
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
            "确认挑战不存在或已失效",
          )
        payload = dict(challenge.payload or {})
        try:
          validate_persistent_trade_challenge(
            challenge=challenge,
            principal=principal,
            action=T_TRADE_CONTROL_CHALLENGE,
            confirmation_token=token,
            now=time_utils.now(),
            payload=payload,
            allow_consumed=True,
          )
        except AuthError as exc:
          raise TradeApprovalChallengeError(exc.code, exc.message) from exc
        request = _request_from_payload(payload)
        current = await cls._lock_current_principal(
          db,
          principal,
          request.account_id,
        )
        actor_user_id = current.user_id
        session_binding = dict(payload.get("session_binding") or {})
        if session_binding != {
          "user_id": current.user_id,
          "device_session_id": current.device_session_id,
          "account_id": current.require_account(),
        }:
          raise TradeApprovalChallengeError(
            "CONFIRMATION_CONTEXT_MISMATCH",
            "设备会话或主账户已变化，请重新预览",
          )
        if challenge.consumed_at is not None:
          if await _operation_marker_exists(
            db,
            challenge_id=normalized_id,
            request=request,
          ):
            challenge.result_reference = _applied_reference(request.action)
            await db.commit()
            return cls._confirmation_from_result(
              challenge,
              request=request,
            )
          current_result = dict(challenge.result_reference or {})
          if str(
            current_result.get("operation_status") or ""
          ) != "DISPATCHING" or _dispatch_lease_is_fresh(
            current_result,
            now=time_utils.now(),
          ):
            return cls._confirmation_from_result(
              challenge,
              request=request,
            )

        rollout = await db.get(
          AccountTradingRollout,
          request.account_id,
          with_for_update=True,
        )
        gate_error: Optional[TradeApprovalChallengeError] = None
        if request.action != TTradeControlAction.KILL_SWITCH:
          readiness = await cls.operations_service.readiness(request.account_id)
          try:
            _validate_action_readiness(request, readiness, rollout)
            if _rollout_binding(rollout) != dict(payload.get("rollout_binding") or {}):
              raise TradeApprovalChallengeError(
                "ROLLOUT_CHANGED",
                "做 T 灰度状态已变化，请重新预览",
              )
            current_fingerprint = _canonical_hash(_readiness_binding(readiness))
            if not hmac.compare_digest(
              current_fingerprint,
              str(payload.get("readiness_fingerprint") or ""),
            ):
              raise TradeApprovalChallengeError(
                "READINESS_CHANGED",
                "做 T 实盘安全快照已变化，请重新预览",
              )
          except TradeApprovalChallengeError as exc:
            gate_error = exc
        now = time_utils.now()
        if challenge.consumed_at is None:
          challenge.consumed_at = now
        if gate_error is not None:
          challenge.result_reference = {
            "challenge_status": "CONSUMED",
            "operation_status": "REJECTED",
            "operation_code": gate_error.code,
            "message": gate_error.message,
            "readiness": _json_safe(readiness) if readiness is not None else None,
          }
          await db.commit()
          return cls._confirmation_from_result(
            challenge,
            request=request,
            readiness=readiness,
          )
        previous_result = dict(challenge.result_reference or {})
        challenge.result_reference = _dispatch_reference(
          now=now,
          attempt=int(previous_result.get("dispatch_attempt") or 0) + 1,
        )
        await db.commit()
      except Exception:
        await db.rollback()
        raise

    return await cls._apply_control(
      challenge_id=normalized_id,
      request=request,
      user_id=actor_user_id,
    )

  @classmethod
  async def _apply_control(
    cls,
    *,
    challenge_id: str,
    request: TTradeControlRequestData,
    user_id: str,
  ) -> TTradeControlConfirmationData:
    try:
      if request.action == TTradeControlAction.BEGIN_CONTROLLED_WINDOW:
        readiness = await cls.operations_service.begin_controlled_window(
          request.account_id,
          user_id=user_id,
          snapshot_id=request.snapshot_id,
          operation_id=challenge_id,
        )
        code = "CONTROLLED_WINDOW_APPLIED"
      elif request.action in {
        TTradeControlAction.ACTIVATE_CANARY,
        TTradeControlAction.ACTIVATE_LIVE,
      }:
        target = request.target_stage.value if request.target_stage else "CANARY"
        readiness = await cls.operations_service.activate_rollout(
          request.account_id,
          user_id=user_id,
          acknowledged_policy_version=request.policy_version,
          target_stage=target,
          confirmation=(
            f"LIVE:{request.account_id}"
            if request.action == TTradeControlAction.ACTIVATE_LIVE
            else ""
          ),
          operation_id=challenge_id,
        )
        code = f"{target}_ACTIVATION_APPLIED"
      else:
        readiness = await cls.operations_service.kill(
          request.account_id,
          request.reason,
          user_id=user_id,
          operation_id=challenge_id,
        )
        code = "KILL_SWITCH_APPLIED"
    except Exception as exc:
      async with AsyncSessionLocal() as db:
        applied = await _operation_marker_exists(
          db,
          challenge_id=challenge_id,
          request=request,
        )
      if applied:
        return await cls._record_operation_result(
          challenge_id=challenge_id,
          request=request,
          operation_status="APPLIED",
          operation_code=_CONTROL_APPLIED_CODES[request.action],
          message="做 T 控制已应用；委托与成交终态仍以券商回报为准",
        )
      if isinstance(exc, ValueError):
        return await cls._record_operation_result(
          challenge_id=challenge_id,
          request=request,
          operation_status="REJECTED",
          operation_code="T_TRADE_CONTROL_REJECTED",
          message=str(exc) or "做 T 控制未通过最终门禁",
        )
      return await cls._record_operation_result(
        challenge_id=challenge_id,
        request=request,
        operation_status="REJECTED",
        operation_code="T_TRADE_CONTROL_FAILED",
        message="做 T 控制未应用，请刷新安全状态后重试",
      )
    return await cls._record_operation_result(
      challenge_id=challenge_id,
      request=request,
      operation_status="APPLIED",
      operation_code=code,
      message="做 T 控制已应用；委托与成交终态仍以券商回报为准",
      readiness=readiness,
    )

  @classmethod
  async def _record_operation_result(
    cls,
    *,
    challenge_id: str,
    request: TTradeControlRequestData,
    operation_status: str,
    operation_code: str,
    message: str,
    readiness: Optional[dict[str, Any]] = None,
  ) -> TTradeControlConfirmationData:
    async with AsyncSessionLocal() as db:
      challenge = (
        await db.execute(
          select(TradeConfirmationChallenge)
          .where(TradeConfirmationChallenge.id == challenge_id)
          .with_for_update()
        )
      ).scalar_one_or_none()
      if challenge is None:
        raise TradeApprovalChallengeError(
          "CONFIRMATION_NOT_FOUND",
          "确认挑战不存在或已失效",
        )
      current = dict(challenge.result_reference or {})
      if str(current.get("operation_status") or "") == "DISPATCHING":
        result_reference = {
          "challenge_status": "CONSUMED",
          "operation_status": operation_status,
          "operation_code": operation_code,
          "message": message[:512],
        }
        if readiness is not None:
          result_reference["readiness"] = _json_safe(readiness)
        challenge.result_reference = result_reference
        await db.commit()
      return cls._confirmation_from_result(
        challenge,
        request=request,
      )

  @staticmethod
  def _confirmation_from_result(
    challenge: TradeConfirmationChallenge,
    *,
    request: TTradeControlRequestData,
    readiness: Optional[dict[str, Any]] = None,
  ) -> TTradeControlConfirmationData:
    result = dict(challenge.result_reference or {})
    operation_status = str(result.get("operation_status") or "DISPATCHING")
    persisted_readiness = result.get("readiness")
    return TTradeControlConfirmationData(
      challenge_id=str(challenge.id),
      action=request.action,
      account_id=request.account_id,
      challenge_consumed=challenge.consumed_at is not None,
      operation_status=operation_status,
      operation_code=str(result.get("operation_code") or "T_TRADE_CONTROL_DISPATCHING"),
      message=str(result.get("message") or "确认已消费，正在应用做 T 控制"),
      readiness=(
        dict(persisted_readiness)
        if isinstance(persisted_readiness, dict)
        else readiness
      ),
    )


__all__ = [
  "T_TRADE_CONTROL_CHALLENGE",
  "TTradeControlChallengeService",
  "TTradeControlConfirmationData",
  "TTradeControlPreviewData",
  "TTradeControlRequestData",
  "normalize_t_trade_control_request",
]
