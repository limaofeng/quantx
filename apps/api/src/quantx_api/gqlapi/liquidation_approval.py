"""Two-phase, snapshot-bound liquidation groups for native clients."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from quantx_domain.clock import utcnow
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.account import Account
from quantx_infrastructure.models.agent_runtime import (
  EngineCommandOutbox,
  PendingTradeOrder,
)
from quantx_infrastructure.models.auth import AuthUserAccountAccess
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  RESERVING_EXIT_PLAN_STATUSES,
)
from quantx_infrastructure.services.engine_command_service import (
  engine_command_service,
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

LIQUIDATION_GROUP_ACTION = "LIQUIDATION_GROUP"
_CHALLENGE_LIFETIME = timedelta(seconds=60)
_MAX_TOKEN_LENGTH = 256
_MAX_LIVE_SNAPSHOT_AGE = timedelta(seconds=90)
_INSTRUMENT_CODE = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")
_SCOPES = frozenset({"SINGLE", "SELECTED", "ALL"})
_COMPLETION_STRATEGIES = frozenset(
  {"AVAILABLE_NOW", "UNTIL_SNAPSHOT_CLEARED"}
)
_CONFLICT_STRATEGIES = frozenset(
  {"UNALLOCATED_ONLY", "REPLACE_CANCELLABLE"}
)
_EXECUTION_MODES = frozenset({"PAPER", "LIVE"})
_ACTIVE_PENDING_SELL_STATUSES = frozenset(
  {"QUEUED", "PENDING", "SUBMITTED", "REPORTED", "PARTIAL_FILLED"}
)


@dataclass(frozen=True)
class LiquidationRequestData:
  account_id: str
  scope: str
  instrument_codes: tuple[str, ...]
  completion_strategy: str
  conflict_strategy: str
  execution_mode: str
  idempotency_key: str

  def payload(self) -> dict[str, Any]:
    return {
      "action": LIQUIDATION_GROUP_ACTION,
      "account_id": self.account_id,
      "scope": self.scope,
      "instrument_codes": list(self.instrument_codes),
      "completion_strategy": self.completion_strategy,
      "conflict_strategy": self.conflict_strategy,
      "execution_mode": self.execution_mode,
      "idempotency_key": self.idempotency_key,
    }

  @classmethod
  def from_payload(cls, payload: dict[str, Any]) -> "LiquidationRequestData":
    if str(payload.get("action") or "") != LIQUIDATION_GROUP_ACTION:
      raise TradeApprovalChallengeError(
        "CONFIRMATION_CONTEXT_MISMATCH",
        "确认上下文无效，请重新获取清仓预览",
      )
    return normalize_liquidation_request(
      account_id=str(payload.get("account_id") or ""),
      scope=str(payload.get("scope") or ""),
      instrument_codes=list(payload.get("instrument_codes") or []),
      completion_strategy=str(payload.get("completion_strategy") or ""),
      conflict_strategy=str(payload.get("conflict_strategy") or ""),
      execution_mode=str(payload.get("execution_mode") or ""),
      idempotency_key=str(payload.get("idempotency_key") or ""),
    )


@dataclass(frozen=True)
class LiquidationConflictData:
  plan_id: str
  source_type: str
  status: str
  remaining_volume: int
  config_version: int
  pending: bool

  def payload(self) -> dict[str, Any]:
    return {
      "plan_id": self.plan_id,
      "source_type": self.source_type,
      "status": self.status,
      "remaining_volume": self.remaining_volume,
      "config_version": self.config_version,
      "pending": self.pending,
    }


@dataclass(frozen=True)
class LiquidationItemData:
  instrument_code: str
  instrument_name: Optional[str]
  total_volume: int
  available_volume: int
  frozen_volume: int
  t1_unavailable_volume: int
  protected_volume: int
  pending_sell_volume: int
  max_protected_volume: int
  included: bool
  reason_code: str
  reason_detail: str
  position_updated_at: Optional[datetime]
  conflicts: tuple[LiquidationConflictData, ...]

  def payload(self) -> dict[str, Any]:
    return {
      "instrument_code": self.instrument_code,
      "instrument_name": self.instrument_name,
      "total_volume": self.total_volume,
      "available_volume": self.available_volume,
      "frozen_volume": self.frozen_volume,
      "t1_unavailable_volume": self.t1_unavailable_volume,
      "protected_volume": self.protected_volume,
      "pending_sell_volume": self.pending_sell_volume,
      "max_protected_volume": self.max_protected_volume,
      "included": self.included,
      "reason_code": self.reason_code,
      "reason_detail": self.reason_detail,
      "position_updated_at": _version_token(self.position_updated_at),
      "conflicts": [item.payload() for item in self.conflicts],
    }


@dataclass(frozen=True)
class LiquidationSnapshotData:
  account_updated_at: datetime
  rollout_snapshot_id: Optional[str]
  rollout_snapshot_hash: Optional[str]
  items: tuple[LiquidationItemData, ...]
  snapshot_version: str
  warnings: tuple[str, ...]

  def payload(self) -> dict[str, Any]:
    return {
      "account_updated_at": _version_token(self.account_updated_at),
      "rollout_snapshot_id": self.rollout_snapshot_id,
      "rollout_snapshot_hash": self.rollout_snapshot_hash,
      "items": [item.payload() for item in self.items],
      "snapshot_version": self.snapshot_version,
    }


@dataclass(frozen=True)
class LiquidationPreviewData:
  challenge_id: str
  confirmation_token: str
  group_id: str
  request: LiquidationRequestData
  snapshot: LiquidationSnapshotData
  challenge_expires_at: datetime


@dataclass(frozen=True)
class LiquidationConfirmationData:
  challenge_id: str
  group_id: str
  command_id: str
  status: str
  result: Optional[dict[str, Any]] = None
  error: Optional[str] = None


def normalize_liquidation_request(
  *,
  account_id: str,
  scope: Any,
  instrument_codes: Iterable[Any],
  completion_strategy: Any,
  conflict_strategy: Any,
  execution_mode: Any,
  idempotency_key: str,
) -> LiquidationRequestData:
  normalized_account = str(account_id or "").strip()
  normalized_scope = str(getattr(scope, "value", scope) or "").strip().upper()
  normalized_completion = str(
    getattr(completion_strategy, "value", completion_strategy) or ""
  ).strip().upper()
  normalized_conflict = str(
    getattr(conflict_strategy, "value", conflict_strategy) or ""
  ).strip().upper()
  normalized_mode = str(
    getattr(execution_mode, "value", execution_mode) or ""
  ).strip().upper()
  normalized_key = str(idempotency_key or "").strip()
  normalized_codes = tuple(
    sorted(
      {
        str(value or "").strip().upper()
        for value in list(instrument_codes or [])
        if str(value or "").strip()
      }
    )
  )

  if not normalized_account:
    raise TradeApprovalChallengeError("ACCOUNT_REQUIRED", "必须指定清仓账户")
  if normalized_scope not in _SCOPES:
    raise TradeApprovalChallengeError(
      "INVALID_LIQUIDATION_SCOPE", "清仓范围必须是 SINGLE、SELECTED 或 ALL"
    )
  invalid_codes = [
    code for code in normalized_codes if not _INSTRUMENT_CODE.fullmatch(code)
  ]
  if invalid_codes:
    raise TradeApprovalChallengeError(
      "INVALID_INSTRUMENT_CODE", "证券代码必须使用六位代码和市场后缀"
    )
  if normalized_scope == "SINGLE" and len(normalized_codes) != 1:
    raise TradeApprovalChallengeError(
      "SINGLE_INSTRUMENT_REQUIRED", "SINGLE 清仓必须且只能指定一只证券"
    )
  if normalized_scope == "SELECTED" and not normalized_codes:
    raise TradeApprovalChallengeError(
      "SELECTED_INSTRUMENTS_REQUIRED", "SELECTED 清仓必须指定证券集合"
    )
  if normalized_scope == "ALL" and normalized_codes:
    raise TradeApprovalChallengeError(
      "ALL_SCOPE_FORBIDS_INSTRUMENTS", "ALL 清仓不能额外指定证券集合"
    )
  if len(normalized_codes) > 200:
    raise TradeApprovalChallengeError(
      "TOO_MANY_INSTRUMENTS", "一次最多预览 200 只证券"
    )
  if normalized_completion not in _COMPLETION_STRATEGIES:
    raise TradeApprovalChallengeError(
      "INVALID_COMPLETION_STRATEGY", "必须选择明确的清仓完成策略"
    )
  if normalized_conflict not in _CONFLICT_STRATEGIES:
    raise TradeApprovalChallengeError(
      "INVALID_CONFLICT_STRATEGY", "必须选择明确的计划冲突策略"
    )
  if normalized_mode not in _EXECUTION_MODES:
    raise TradeApprovalChallengeError(
      "INVALID_EXECUTION_MODE", "执行模式必须是 PAPER 或 LIVE"
    )
  if not normalized_key or len(normalized_key) > 128:
    raise TradeApprovalChallengeError(
      "INVALID_IDEMPOTENCY_KEY", "幂等键不能为空且不能超过 128 个字符"
    )
  return LiquidationRequestData(
    account_id=normalized_account,
    scope=normalized_scope,
    instrument_codes=normalized_codes,
    completion_strategy=normalized_completion,
    conflict_strategy=normalized_conflict,
    execution_mode=normalized_mode,
    idempotency_key=normalized_key,
  )


def _version_token(value: Optional[datetime]) -> Optional[str]:
  return value.isoformat(timespec="microseconds") if value is not None else None


def _snapshot_hash(payload: dict[str, Any]) -> str:
  encoded = json.dumps(
    payload,
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
    default=str,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def _group_id(challenge_id: str) -> str:
  return str(
    uuid.uuid5(uuid.NAMESPACE_URL, f"quantx:liquidation-group:{challenge_id}")
  )


def _challenge_payload(
  request: LiquidationRequestData,
  snapshot: LiquidationSnapshotData,
  *,
  group_id: str,
) -> dict[str, Any]:
  return {
    **request.payload(),
    "group_id": group_id,
    "snapshot": snapshot.payload(),
  }


def _snapshot_age(value: Optional[datetime], now: datetime) -> Optional[timedelta]:
  if value is None:
    return None
  return now - time_utils.to_shanghai(value)


def _conflict_data(record: AutoExitPlanRecord) -> LiquidationConflictData:
  return LiquidationConflictData(
    plan_id=str(record.plan_id),
    source_type=str(record.source_type),
    status=str(record.status),
    remaining_volume=max(0, int(record.remaining_volume or 0)),
    config_version=max(0, int(record.config_version or 0)),
    pending=(
      str(record.status or "").upper() == "EXIT_PENDING"
      or bool(record.pending_client_order_id)
    ),
  )


async def _build_snapshot(
  request: LiquidationRequestData,
  *,
  db: Any,
  user_id: str,
  lock_mutable_rows: bool,
) -> LiquidationSnapshotData:
  rollout_snapshot_id: Optional[str] = None
  rollout_snapshot_hash: Optional[str] = None
  if request.execution_mode == "LIVE":
    try:
      rollout = await TradeCommandService(db)._require_manual_live_authorization(
        request.account_id,
        risk_reducing=True,
      )
      await TradeCommandService(db)._device_for(
        user_id=user_id,
        account_id=request.account_id,
        execution_mode="live",
      )
    except AgentUnavailableError as exc:
      raise TradeApprovalChallengeError(
        "LIVE_AUTHORIZATION_REJECTED", str(exc)
      ) from exc
    rollout_snapshot_id = str(rollout.last_snapshot_id or "")
    rollout_snapshot_hash = str(rollout.last_snapshot_hash or "")

  account_stmt = select(Account).where(
    Account.account_id == request.account_id,
    Account.account_type == AccountType.STOCK,
  )
  if lock_mutable_rows:
    account_stmt = account_stmt.with_for_update()
  account = (await db.execute(account_stmt)).scalar_one_or_none()
  if account is None or account.updated_at is None:
    raise TradeApprovalChallengeError(
      "ACCOUNT_SNAPSHOT_UNAVAILABLE", "账户快照不存在，无法预览清仓"
    )
  if request.execution_mode == "LIVE":
    age = _snapshot_age(account.updated_at, time_utils.now())
    if age is None or age < timedelta(0) or age > _MAX_LIVE_SNAPSHOT_AGE:
      raise TradeApprovalChallengeError(
        "ACCOUNT_SNAPSHOT_STALE", "账户快照已超过 90 秒，无法预览实盘清仓"
      )

  position_stmt = (
    select(Position)
    .where(Position.account_id == request.account_id)
    .where(Position.volume > 0)
    .order_by(Position.stock_code)
  )
  if request.scope != "ALL":
    position_stmt = position_stmt.where(
      Position.stock_code.in_(request.instrument_codes)
    )
  if lock_mutable_rows:
    position_stmt = position_stmt.with_for_update()
  positions = list((await db.execute(position_stmt)).scalars().all())
  positions_by_code = {str(item.stock_code).upper(): item for item in positions}
  selected_codes = (
    tuple(positions_by_code)
    if request.scope == "ALL"
    else request.instrument_codes
  )
  if request.scope == "ALL" and len(selected_codes) > 200:
    raise TradeApprovalChallengeError(
      "TOO_MANY_INSTRUMENTS", "全仓预览一次最多处理 200 只持仓"
    )

  plan_stmt = (
    select(AutoExitPlanRecord)
    .where(AutoExitPlanRecord.account_id == request.account_id)
    .where(AutoExitPlanRecord.instrument_code.in_(selected_codes or ("",)))
    .where(AutoExitPlanRecord.status.in_(RESERVING_EXIT_PLAN_STATUSES))
    .order_by(AutoExitPlanRecord.instrument_code, AutoExitPlanRecord.created_at)
  )
  if lock_mutable_rows:
    plan_stmt = plan_stmt.with_for_update()
  plans = list((await db.execute(plan_stmt)).scalars().all())
  plans_by_code: dict[str, list[AutoExitPlanRecord]] = {}
  for plan in plans:
    plans_by_code.setdefault(str(plan.instrument_code).upper(), []).append(plan)

  pending_stmt = (
    select(PendingTradeOrder)
    .where(PendingTradeOrder.account_id == request.account_id)
    .where(PendingTradeOrder.instrument_code.in_(selected_codes or ("",)))
    .where(PendingTradeOrder.side == "SELL")
    .where(PendingTradeOrder.status.in_(_ACTIVE_PENDING_SELL_STATUSES))
    .order_by(PendingTradeOrder.instrument_code, PendingTradeOrder.created_at)
  )
  if lock_mutable_rows:
    pending_stmt = pending_stmt.with_for_update()
  pending_orders = list((await db.execute(pending_stmt)).scalars().all())
  pending_by_code: dict[str, list[PendingTradeOrder]] = {}
  for order in pending_orders:
    pending_by_code.setdefault(str(order.instrument_code).upper(), []).append(order)

  items: list[LiquidationItemData] = []
  for code in selected_codes:
    position = positions_by_code.get(code)
    if position is None:
      items.append(
        LiquidationItemData(
          instrument_code=code,
          instrument_name=None,
          total_volume=0,
          available_volume=0,
          frozen_volume=0,
          t1_unavailable_volume=0,
          protected_volume=0,
          pending_sell_volume=0,
          max_protected_volume=0,
          included=False,
          reason_code="POSITION_NOT_FOUND",
          reason_detail="预览时未找到有效持仓",
          position_updated_at=None,
          conflicts=(),
        )
      )
      continue

    if request.execution_mode == "LIVE":
      age = _snapshot_age(position.updated_at, time_utils.now())
      if age is None or age < timedelta(0) or age > _MAX_LIVE_SNAPSHOT_AGE:
        raise TradeApprovalChallengeError(
          "POSITION_SNAPSHOT_STALE",
          f"{code} 持仓快照已超过 90 秒，无法预览实盘清仓",
        )
    total = max(0, int(position.volume or 0))
    available = max(0, min(total, int(position.can_use_volume or 0)))
    frozen = max(0, min(total, int(position.frozen_volume or 0)))
    t1_unavailable = max(0, total - available - frozen)
    conflicts = tuple(_conflict_data(item) for item in plans_by_code.get(code, []))
    protected = sum(item.remaining_volume for item in conflicts)
    pending_sell = sum(
      max(0, int(item.volume or 0)) for item in pending_by_code.get(code, [])
    )
    pending_conflicts = [item for item in conflicts if item.pending]
    snapshot_target = (
      available
      if request.completion_strategy == "AVAILABLE_NOW"
      else total
    )
    reserved = (
      0
      if request.conflict_strategy == "REPLACE_CANCELLABLE"
      else protected
    )
    maximum = max(0, min(snapshot_target, total - reserved))
    included = True
    reason_code = "INCLUDED"
    reason_detail = "已纳入清仓组预览"
    if pending_sell or pending_conflicts:
      included = False
      maximum = 0
      reason_code = "PENDING_SELL_CONFLICT"
      reason_detail = "存在待成交 SELL，必须先等待回报或撤单"
    elif maximum <= 0:
      included = False
      reason_code = (
        "NO_AVAILABLE_VOLUME"
        if request.completion_strategy == "AVAILABLE_NOW" and available <= 0
        else "NO_UNALLOCATED_VOLUME"
      )
      reason_detail = (
        "当前无可卖数量；可能受 T+1、冻结或在途委托影响"
        if reason_code == "NO_AVAILABLE_VOLUME"
        else "持仓数量已被其他退出计划保护"
      )
    items.append(
      LiquidationItemData(
        instrument_code=code,
        instrument_name=position.instrument_name,
        total_volume=total,
        available_volume=available,
        frozen_volume=frozen,
        t1_unavailable_volume=t1_unavailable,
        protected_volume=protected,
        pending_sell_volume=pending_sell,
        max_protected_volume=maximum,
        included=included,
        reason_code=reason_code,
        reason_detail=reason_detail,
        position_updated_at=position.updated_at,
        conflicts=conflicts,
      )
    )

  if not items:
    raise TradeApprovalChallengeError(
      "NO_POSITIONS", "当前账户没有可预览的持仓"
    )
  if not any(item.included for item in items):
    raise TradeApprovalChallengeError(
      "NO_LIQUIDATABLE_POSITIONS", "当前选择没有可创建清仓计划的持仓"
    )
  subject = {
    "account_updated_at": _version_token(account.updated_at),
    "rollout_snapshot_id": rollout_snapshot_id,
    "rollout_snapshot_hash": rollout_snapshot_hash,
    "items": [item.payload() for item in items],
  }
  return LiquidationSnapshotData(
    account_updated_at=account.updated_at,
    rollout_snapshot_id=rollout_snapshot_id,
    rollout_snapshot_hash=rollout_snapshot_hash,
    items=tuple(items),
    snapshot_version=_snapshot_hash(subject),
    warnings=(
      "确认只会排队创建固定快照清仓计划，不表示委托已报或成交",
      "确认后的新增持仓不会自动加入本清仓组",
      "每只证券仍会独立经过 T+1、可卖量、风控和券商回报收敛",
      "批量结果允许部分失败，失败证券不会扩大其他证券的授权数量",
    ),
  )


def _validate_snapshot_binding(
  payload: dict[str, Any], current: LiquidationSnapshotData
) -> None:
  expected = dict(payload.get("snapshot") or {})
  if not expected or expected != current.payload():
    raise TradeApprovalChallengeError(
      "LIQUIDATION_SNAPSHOT_CHANGED",
      "账户、持仓、保护计划或待成交 SELL 已变化，请重新预览",
    )


def _engine_payload(
  *,
  challenge: TradeConfirmationChallenge,
  request: LiquidationRequestData,
  snapshot: LiquidationSnapshotData,
  group_id: str,
) -> dict[str, Any]:
  return {
    "account_id": request.account_id,
    # The Engine must receive the signed set, never rescan ALL after confirmation.
    "scope": "SELECTED",
    "requested_scope": request.scope,
    "instrument_codes": [item.instrument_code for item in snapshot.items],
    "completion_strategy": request.completion_strategy,
    "conflict_strategy": request.conflict_strategy,
    "execution_mode": request.execution_mode.lower(),
    "auto_exit_authorized": True,
    "confirm": True,
    "group_id": group_id,
    "authorization_challenge_id": str(challenge.id),
    "authorization_snapshot_version": snapshot.snapshot_version,
    "expected_items": [item.payload() for item in snapshot.items],
  }


class LiquidationChallengeService:
  @staticmethod
  async def issue(
    *,
    principal: Principal,
    request: LiquidationRequestData,
  ) -> LiquidationPreviewData:
    principal.require_permission("liquidation:control")
    principal.require_account(request.account_id)
    challenge_id = str(uuid.uuid4())
    group_id = _group_id(challenge_id)
    async with AsyncSessionLocal() as db:
      snapshot = await _build_snapshot(
        request,
        db=db,
        user_id=principal.user_id,
        lock_mutable_rows=False,
      )
      payload = _challenge_payload(request, snapshot, group_id=group_id)
      token = secrets.token_urlsafe(48)
      now = time_utils.now()
      challenge = TradeConfirmationChallenge(
        id=challenge_id,
        action=LIQUIDATION_GROUP_ACTION,
        user_id=principal.user_id,
        device_session_id=principal.device_session_id,
        account_id=request.account_id,
        idempotency_key=request.idempotency_key,
        payload=payload,
        payload_fingerprint=signed_payload_fingerprint(payload),
        token_digest=challenge_token_digest(token),
        expires_at=now + _CHALLENGE_LIFETIME,
        consumed_at=None,
      )
      db.add(challenge)
      try:
        await db.commit()
      except IntegrityError as exc:
        await db.rollback()
        raise TradeApprovalChallengeError(
          "IDEMPOTENCY_KEY_ALREADY_USED",
          "该幂等键已用于清仓预览，请查询原清仓组状态",
        ) from exc
    return LiquidationPreviewData(
      challenge_id=challenge_id,
      confirmation_token=token,
      group_id=group_id,
      request=request,
      snapshot=snapshot,
      challenge_expires_at=time_utils.to_shanghai(
        now + _CHALLENGE_LIFETIME, keep_tz=True
      ),
    )

  @staticmethod
  async def confirm(
    *,
    principal: Principal,
    challenge_id: str,
    confirmation_token: str,
  ) -> LiquidationConfirmationData:
    normalized_id = str(challenge_id or "").strip()
    token = str(confirmation_token or "")
    if not normalized_id or not token or len(token) > _MAX_TOKEN_LENGTH:
      raise TradeApprovalChallengeError(
        "INVALID_CONFIRMATION_TOKEN", "确认凭据无效，请重新获取预览"
      )

    async with AsyncSessionLocal() as db:
      challenge = await db.get(TradeConfirmationChallenge, normalized_id)
      if challenge is None:
        raise TradeApprovalChallengeError(
          "CONFIRMATION_NOT_FOUND", "清仓确认挑战不存在或已失效"
        )
      LiquidationChallengeService._validate_challenge(
        challenge=challenge,
        principal=principal,
        token=token,
        now=time_utils.now(),
        allow_consumed=True,
      )
      if challenge.consumed_at is not None:
        return await LiquidationChallengeService._existing_result(db, challenge)

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
            "CONFIRMATION_NOT_FOUND", "清仓确认挑战不存在或已失效"
          )
        now = time_utils.now()
        LiquidationChallengeService._validate_challenge(
          challenge=challenge,
          principal=principal,
          token=token,
          now=now,
          allow_consumed=True,
        )
        if challenge.consumed_at is not None:
          return await LiquidationChallengeService._existing_result(db, challenge)

        payload = dict(challenge.payload or {})
        request = LiquidationRequestData.from_payload(payload)
        group_id = str(payload.get("group_id") or "")
        if group_id != _group_id(str(challenge.id)):
          raise TradeApprovalChallengeError(
            "CONFIRMATION_CONTEXT_MISMATCH", "清仓组标识与确认挑战不一致"
          )
        try:
          current_principal = await AuthService(db).lock_and_validate_session(
            principal,
            required_permission="liquidation:control",
            account_id=request.account_id,
          )
          # This second permission is intentionally enforced in the resolver
          # transaction, not encoded as a substitute top-level permission.
          current_principal.require_permission("trade:approve")
        except AuthError as exc:
          raise TradeApprovalChallengeError(exc.code, exc.message) from exc
        account_access = await db.scalar(
          select(AuthUserAccountAccess)
          .where(
            AuthUserAccountAccess.user_id == current_principal.user_id,
            AuthUserAccountAccess.account_id == request.account_id,
          )
          .with_for_update()
        )
        if account_access is None:
          raise TradeApprovalChallengeError(
            "FORBIDDEN", "当前用户已无权使用该清仓账户"
          )

        snapshot = await _build_snapshot(
          request,
          db=db,
          user_id=current_principal.user_id,
          lock_mutable_rows=True,
        )
        _validate_snapshot_binding(payload, snapshot)

        message_id = str(uuid.uuid4())
        command = EngineCommandOutbox(
          message_id=message_id,
          idempotency_key=f"liquidation-confirm:{challenge.id}",
          command_type="EXIT_PLAN_LIQUIDATE_POSITIONS",
          aggregate_id=f"{request.account_id}:{group_id}",
          payload=_engine_payload(
            challenge=challenge,
            request=request,
            snapshot=snapshot,
            group_id=group_id,
          ),
          processing_status="PENDING",
          available_at=utcnow(),
        )
        db.add(command)
        challenge.consumed_at = now
        challenge.result_reference = {
          "group_id": group_id,
          "command_id": message_id,
          "status": "PENDING",
        }
        await db.commit()
      except Exception:
        await db.rollback()
        raise
    return LiquidationConfirmationData(
      challenge_id=normalized_id,
      group_id=group_id,
      command_id=message_id,
      status="PENDING",
    )

  @staticmethod
  def _validate_challenge(
    *,
    challenge: TradeConfirmationChallenge,
    principal: Principal,
    token: str,
    now: datetime,
    allow_consumed: bool,
  ) -> None:
    payload = dict(challenge.payload or {})
    validate_persistent_trade_challenge(
      challenge=challenge,
      principal=principal,
      action=LIQUIDATION_GROUP_ACTION,
      confirmation_token=token,
      now=now,
      payload=payload,
      allow_consumed=allow_consumed,
    )

  @staticmethod
  async def _existing_result(
    db: Any,
    challenge: TradeConfirmationChallenge,
  ) -> LiquidationConfirmationData:
    reference = dict(challenge.result_reference or {})
    group_id = str(reference.get("group_id") or "")
    command_id = str(reference.get("command_id") or "")
    if not group_id or not command_id:
      raise TradeApprovalChallengeError(
        "CONFIRMATION_RESULT_PENDING",
        "清仓确认已消费，但排队结果暂不可用，请刷新后重试",
      )
    receipt = await engine_command_service.get(command_id)
    if receipt is None:
      raise TradeApprovalChallengeError(
        "CONFIRMATION_RESULT_PENDING", "清仓 Engine 命令暂不可用，请稍后重试"
      )
    if reference.get("status") != receipt.status:
      challenge.result_reference = {
        "group_id": group_id,
        "command_id": command_id,
        "status": receipt.status,
        "result": receipt.result,
        "error": receipt.error,
      }
      await db.commit()
    return LiquidationConfirmationData(
      challenge_id=str(challenge.id),
      group_id=group_id,
      command_id=command_id,
      status=receipt.status,
      result=receipt.result,
      error=receipt.error,
    )
