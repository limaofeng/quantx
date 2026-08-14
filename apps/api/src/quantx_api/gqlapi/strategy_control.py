"""Device-bound two-phase controls for starting live strategy risk."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models import TradeConfirmationChallenge
from quantx_infrastructure.models.enums import StrategyRunMode, StrategyRunStatus
from quantx_infrastructure.repositories.strategy_run_repository import (
  StrategyRunRepository,
)
from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationsService,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from quantx_api.auth.errors import AuthError
from quantx_api.auth.principal import Principal
from quantx_api.auth.service import AuthService

from .resolvers.strategies import StrategyResolver
from .trade_approval import (
  TradeApprovalChallengeError,
  challenge_token_digest,
  signed_payload_fingerprint,
  validate_persistent_trade_challenge,
)
from .types.strategy_types import StrategyControlAction

STRATEGY_CONTROL_CHALLENGE = "STRATEGY_CONTROL"
_CHALLENGE_LIFETIME = timedelta(seconds=60)
_MAX_TOKEN_LENGTH = 256
_MAX_IDEMPOTENCY_KEY_LENGTH = 128
_IGNORED_READINESS_CHECKS = frozenset({"T_TRADE_LIVE_ENABLED"})


@dataclass(frozen=True)
class StrategyControlRequestData:
  account_id: str
  instance_id: str
  action: StrategyControlAction
  expected_config_version: str
  idempotency_key: str


@dataclass(frozen=True)
class StrategyControlPreviewData:
  challenge_id: str
  confirmation_token: str
  request: StrategyControlRequestData
  target_instance_id: str
  current_mode: str
  current_status: str
  config_version: str
  readiness: dict[str, Any]
  challenge_expires_at: datetime


@dataclass(frozen=True)
class StrategyControlConfirmationData:
  challenge_id: str
  instance_id: str
  status: str


def normalize_strategy_control_request(
  *,
  account_id: str,
  instance_id: str,
  action: StrategyControlAction | str,
  expected_config_version: str,
  idempotency_key: str,
) -> StrategyControlRequestData:
  normalized_account = str(account_id or "").strip()
  normalized_instance = str(instance_id or "").strip()
  normalized_version = str(expected_config_version or "").strip()
  normalized_key = str(idempotency_key or "").strip()
  try:
    normalized_action = (
      action if isinstance(action, StrategyControlAction) else StrategyControlAction(str(action))
    )
  except ValueError as exc:
    raise TradeApprovalChallengeError(
      "INVALID_STRATEGY_CONTROL_ACTION",
      "策略控制动作无效",
    ) from exc
  if not normalized_account:
    raise TradeApprovalChallengeError("ACCOUNT_REQUIRED", "必须指定资金账号")
  if not normalized_instance or len(normalized_instance) > 64:
    raise TradeApprovalChallengeError(
      "INVALID_STRATEGY_INSTANCE",
      "策略实例 ID 无效",
    )
  if not normalized_version.isdigit() or int(normalized_version) <= 0:
    raise TradeApprovalChallengeError(
      "INVALID_CONFIG_VERSION",
      "策略配置版本无效",
    )
  if not normalized_key or len(normalized_key) > _MAX_IDEMPOTENCY_KEY_LENGTH:
    raise TradeApprovalChallengeError(
      "INVALID_IDEMPOTENCY_KEY",
      "幂等键不能为空且不能超过 128 个字符",
    )
  return StrategyControlRequestData(
    account_id=normalized_account,
    instance_id=normalized_instance,
    action=normalized_action,
    expected_config_version=str(int(normalized_version)),
    idempotency_key=normalized_key,
  )


def _enum_value(value: Any) -> str:
  return str(getattr(value, "value", value) or "").lower()


def _parameters(run: Any) -> dict[str, Any]:
  return StrategyResolver._json_object(run.parameters)


def _config_version(run: Any) -> str:
  return StrategyResolver._mobile_config_version(_parameters(run))


def _account_id(run: Any) -> str:
  parameters = _parameters(run)
  return str(parameters.get("account_id") or parameters.get("accountId") or "").strip()


def _canonical_hash(value: Any) -> str:
  encoded = json.dumps(
    value,
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
    default=str,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def _run_binding(run: Any) -> dict[str, Any]:
  return {
    "instance_id": str(run.id),
    "strategy_id": int(run.strategy_id),
    "account_id": _account_id(run),
    "mode": _enum_value(run.mode),
    "status": _enum_value(run.status),
    "config_version": _config_version(run),
    "instruments": sorted(str(value) for value in list(run.instruments or [])),
    "parameters_hash": _canonical_hash(_parameters(run)),
  }


def _readiness_binding(readiness: dict[str, Any]) -> dict[str, Any]:
  checks = [
    {
      "code": str(item.get("code") or ""),
      "passed": bool(item.get("passed")),
    }
    for item in list(readiness.get("checks") or [])
    if str(item.get("code") or "") not in _IGNORED_READINESS_CHECKS
  ]
  return {
    "status": str(readiness.get("status") or ""),
    "stage": str(readiness.get("stage") or ""),
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
    "snapshot_at": str(readiness.get("snapshot_at") or ""),
    "controlled_window_active": bool(readiness.get("controlled_window_active")),
    "controlled_window_snapshot_id": str(
      readiness.get("controlled_window_snapshot_id") or ""
    ),
    "new_external_order_count": int(readiness.get("new_external_order_count") or 0),
    "new_external_trade_count": int(readiness.get("new_external_trade_count") or 0),
    "working_external_order_count": int(
      readiness.get("working_external_order_count") or 0
    ),
    "checks": sorted(checks, key=lambda item: item["code"]),
  }


def _validate_readiness(readiness: dict[str, Any]) -> None:
  failed = [
    item
    for item in list(readiness.get("checks") or [])
    if str(item.get("code") or "") not in _IGNORED_READINESS_CHECKS
    and not bool(item.get("passed"))
  ]
  if failed:
    reason = str(failed[0].get("message") or failed[0].get("code") or "实盘未就绪")
    raise TradeApprovalChallengeError("STRATEGY_LIVE_NOT_READY", reason)
  binding = _readiness_binding(readiness)
  if (
    not binding["snapshot_id"]
    or not binding["snapshot_hash"]
    or binding["kill_switch"]
    or binding["ready_live_agent_count"] != 1
    or binding["agent_mode"].lower() != "live"
    or binding["protocol_version"] != "1.1"
  ):
    raise TradeApprovalChallengeError(
      "STRATEGY_LIVE_NOT_READY",
      "账户缺少可绑定的实盘对账、Agent 或安全快照",
    )


def _validate_action_state(run: Any, request: StrategyControlRequestData) -> None:
  account_id = _account_id(run)
  if not account_id or account_id != request.account_id:
    raise TradeApprovalChallengeError(
      "ACCOUNT_SCOPE_MISMATCH",
      "策略实例不属于当前资金账号",
    )
  current_version = _config_version(run)
  if current_version != request.expected_config_version:
    raise TradeApprovalChallengeError(
      "VERSION_CONFLICT",
      "策略配置版本已变化，请刷新后重新预览",
    )
  mode = _enum_value(run.mode)
  status = _enum_value(run.status)
  if request.action == StrategyControlAction.START_LIVE:
    valid = mode == StrategyRunMode.LIVE.value and status in {
      StrategyRunStatus.PENDING.value,
      StrategyRunStatus.STOPPED.value,
    }
  elif request.action == StrategyControlAction.RESUME_LIVE:
    valid = (
      mode == StrategyRunMode.LIVE.value
      and status == StrategyRunStatus.PAUSED.value
    )
  else:
    valid = mode == StrategyRunMode.PAPER.value and status in {
      StrategyRunStatus.PAUSED.value,
      StrategyRunStatus.STOPPED.value,
    }
  if not valid:
    raise TradeApprovalChallengeError(
      "STRATEGY_STATE_CONFLICT",
      f"当前策略模式 {mode or 'unknown'}、状态 {status or 'unknown'} 不允许该动作",
    )


def _payload(
  *,
  request: StrategyControlRequestData,
  target_instance_id: str,
  run_binding: dict[str, Any],
  readiness_binding: dict[str, Any],
) -> dict[str, Any]:
  return {
    "action": STRATEGY_CONTROL_CHALLENGE,
    "control_action": request.action.value,
    "account_id": request.account_id,
    "instance_id": request.instance_id,
    "target_instance_id": target_instance_id,
    "expected_config_version": request.expected_config_version,
    "idempotency_key": request.idempotency_key,
    "run_binding": run_binding,
    "readiness_binding": readiness_binding,
  }


def _request_from_payload(payload: dict[str, Any]) -> StrategyControlRequestData:
  if str(payload.get("action") or "") != STRATEGY_CONTROL_CHALLENGE:
    raise TradeApprovalChallengeError(
      "CONFIRMATION_CONTEXT_MISMATCH",
      "策略控制确认上下文无效",
    )
  return normalize_strategy_control_request(
    account_id=str(payload.get("account_id") or ""),
    instance_id=str(payload.get("instance_id") or ""),
    action=str(payload.get("control_action") or ""),
    expected_config_version=str(payload.get("expected_config_version") or ""),
    idempotency_key=str(payload.get("idempotency_key") or ""),
  )


class StrategyControlChallengeService:
  @staticmethod
  async def instance_requires_confirmation(instance_id: str) -> bool:
    async with AsyncSessionLocal() as db:
      run = await StrategyRunRepository(db).find_run_by_id(instance_id)
      if run is None:
        raise TradeApprovalChallengeError(
          "STRATEGY_NOT_FOUND",
          "策略实例不存在",
        )
      return _enum_value(run.mode) == StrategyRunMode.LIVE.value

  @staticmethod
  async def issue(
    *,
    principal: Principal,
    request: StrategyControlRequestData,
  ) -> StrategyControlPreviewData:
    principal.require_permission("strategy:control")
    principal.require_permission("trade:approve")
    principal.require_account(request.account_id)
    async with AsyncSessionLocal() as db:
      run = await StrategyRunRepository(db).find_run_by_id(request.instance_id)
      if run is None:
        raise TradeApprovalChallengeError(
          "STRATEGY_NOT_FOUND",
          "策略实例不存在",
        )
      _validate_action_state(run, request)
      run_binding = _run_binding(run)

    readiness = await TTradeOperationsService().readiness(request.account_id)
    _validate_readiness(readiness)
    target_instance_id = (
      str(uuid.uuid4())
      if request.action == StrategyControlAction.CLONE_TO_LIVE
      else request.instance_id
    )
    payload = _payload(
      request=request,
      target_instance_id=target_instance_id,
      run_binding=run_binding,
      readiness_binding=_readiness_binding(readiness),
    )
    raw_token = secrets.token_urlsafe(48)
    now = time_utils.now()
    challenge = TradeConfirmationChallenge(
      id=str(uuid.uuid4()),
      action=STRATEGY_CONTROL_CHALLENGE,
      user_id=principal.user_id,
      device_session_id=principal.device_session_id,
      account_id=request.account_id,
      idempotency_key=request.idempotency_key,
      payload=payload,
      payload_fingerprint=signed_payload_fingerprint(payload),
      token_digest=challenge_token_digest(raw_token),
      expires_at=now + _CHALLENGE_LIFETIME,
      consumed_at=None,
    )
    async with AsyncSessionLocal() as db:
      db.add(challenge)
      try:
        await db.commit()
      except IntegrityError as exc:
        await db.rollback()
        raise TradeApprovalChallengeError(
          "IDEMPOTENCY_KEY_ALREADY_USED",
          "该幂等键已用于策略控制，请刷新策略状态",
        ) from exc
    return StrategyControlPreviewData(
      challenge_id=str(challenge.id),
      confirmation_token=raw_token,
      request=request,
      target_instance_id=target_instance_id,
      current_mode=str(run_binding["mode"]),
      current_status=str(run_binding["status"]),
      config_version=str(run_binding["config_version"]),
      readiness=readiness,
      challenge_expires_at=time_utils.to_shanghai(challenge.expires_at, keep_tz=True),
    )

  @staticmethod
  async def confirm(
    *,
    principal: Principal,
    challenge_id: str,
    confirmation_token: str,
  ) -> StrategyControlConfirmationData:
    normalized_id = str(challenge_id or "").strip()
    token = str(confirmation_token or "")
    if not normalized_id or not token or len(token) > _MAX_TOKEN_LENGTH:
      raise TradeApprovalChallengeError(
        "INVALID_CONFIRMATION_TOKEN",
        "确认凭据无效，请重新获取预览",
      )

    dispatch_payload: dict[str, Any]
    request: StrategyControlRequestData
    target_instance_id: str
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
        validate_persistent_trade_challenge(
          challenge=challenge,
          principal=principal,
          action=STRATEGY_CONTROL_CHALLENGE,
          confirmation_token=token,
          now=time_utils.now(),
          payload=payload,
          allow_consumed=True,
        )
        request = _request_from_payload(payload)
        target_instance_id = str(payload.get("target_instance_id") or "")
        existing = dict(challenge.result_reference or {})
        if challenge.consumed_at is not None:
          if str(existing.get("status") or "") == "APPLIED":
            return StrategyControlConfirmationData(
              challenge_id=normalized_id,
              instance_id=str(existing.get("instance_id") or target_instance_id),
              status="APPLIED",
            )
          raise TradeApprovalChallengeError(
            "CONFIRMATION_ALREADY_USED",
            "该策略控制确认已消费，请刷新策略终态",
          )
        try:
          current = await AuthService(db).lock_and_validate_session(
            principal,
            required_permission="trade:approve",
            account_id=request.account_id,
          )
          current.require_permission("strategy:control")
        except AuthError as exc:
          raise TradeApprovalChallengeError(exc.code, exc.message) from exc
        run = await StrategyRunRepository(db).find_run_by_id_for_update(
          request.instance_id
        )
        if run is None:
          raise TradeApprovalChallengeError(
            "STRATEGY_NOT_FOUND",
            "策略实例不存在",
          )
        _validate_action_state(run, request)
        if _run_binding(run) != dict(payload.get("run_binding") or {}):
          raise TradeApprovalChallengeError(
            "STRATEGY_STATE_CONFLICT",
            "策略实例在确认前已变化，请重新预览",
          )
        if request.action == StrategyControlAction.CLONE_TO_LIVE:
          dispatch_payload = StrategyResolver._strategy_create_payload(
            run_id=target_instance_id,
            strategy_id=int(run.strategy_id),
            mode=StrategyRunMode.LIVE,
            instruments=list(run.instruments or []),
            parameters=_parameters(run),
            name=f"{run.name}-Live",
            auto_start=True,
          )
        else:
          dispatch_payload = {"run_id": request.instance_id}
        challenge.consumed_at = time_utils.now()
        challenge.result_reference = {
          "instance_id": target_instance_id,
          "status": "DISPATCHING",
        }
        await db.commit()
      except Exception:
        await db.rollback()
        raise

    readiness = await TTradeOperationsService().readiness(request.account_id)
    _validate_readiness(readiness)
    if _readiness_binding(readiness) != dict(payload.get("readiness_binding") or {}):
      await StrategyControlChallengeService._record_result(
        normalized_id,
        target_instance_id,
        "REJECTED_READINESS_CHANGED",
      )
      raise TradeApprovalChallengeError(
        "READINESS_CHANGED",
        "实盘安全快照已变化，请重新预览并确认",
      )

    try:
      if request.action == StrategyControlAction.CLONE_TO_LIVE:
        result = await StrategyResolver._engine_request(
          "STRATEGY_CREATE",
          dispatch_payload,
          aggregate_id=target_instance_id,
          idempotency_key=f"strategy-control-create:{normalized_id}",
        )
        if str(result.get("run_id") or target_instance_id) != target_instance_id:
          raise RuntimeError("Engine 返回的策略实例与确认目标不一致")
      else:
        command = (
          "STRATEGY_START"
          if request.action == StrategyControlAction.START_LIVE
          else "STRATEGY_RESUME"
        )
        result = await StrategyResolver._engine_request(
          command,
          dispatch_payload,
          aggregate_id=request.instance_id,
          idempotency_key=f"strategy-control:{normalized_id}",
        )
        if not bool(result.get("success")):
          raise RuntimeError("Engine 未应用策略控制")
    except Exception as exc:
      await StrategyControlChallengeService._record_result(
        normalized_id,
        target_instance_id,
        "FAILED",
      )
      raise TradeApprovalChallengeError(
        "STRATEGY_CONTROL_FAILED",
        "策略控制未应用，请刷新策略状态后重新预览",
      ) from exc

    await StrategyControlChallengeService._record_result(
      normalized_id,
      target_instance_id,
      "APPLIED",
    )
    return StrategyControlConfirmationData(
      challenge_id=normalized_id,
      instance_id=target_instance_id,
      status="APPLIED",
    )

  @staticmethod
  async def _record_result(
    challenge_id: str,
    instance_id: str,
    status: str,
  ) -> None:
    async with AsyncSessionLocal() as db:
      challenge = (
        await db.execute(
          select(TradeConfirmationChallenge)
          .where(TradeConfirmationChallenge.id == challenge_id)
          .with_for_update()
        )
      ).scalar_one_or_none()
      if challenge is None:
        return
      challenge.result_reference = {
        "instance_id": instance_id,
        "status": status,
      }
      await db.commit()
