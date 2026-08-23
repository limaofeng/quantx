"""Two-phase, device-bound approval challenges for manual trade intents."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from quantx_domain.clock import utcnow
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.engine_command_service import (
  engine_command_service,
)
from sqlalchemy import select

from quantx_api.auth.principal import Principal
from quantx_api.auth.tokens import require_signing_key

_CHALLENGE_METADATA_KEY = "mobile_trade_approval_challenge_v1"
_MAX_CHALLENGE_LIFETIME = timedelta(seconds=60)
_MAX_TOKEN_LENGTH = 256

T_TRADE_ENTRY_APPROVAL = "T_TRADE_ENTRY_APPROVAL"
STRATEGY_TRADE_INTENT_APPROVAL = "STRATEGY_TRADE_INTENT_APPROVAL"
EXIT_PLAN_SELL_APPROVAL = "EXIT_PLAN_SELL_APPROVAL"


class TradeApprovalChallengeError(ValueError):
  def __init__(self, code: str, message: str):
    super().__init__(message)
    self.code = code
    self.message = message


@dataclass(frozen=True)
class TradeApprovalPreviewData:
  challenge_id: str
  confirmation_token: str
  action: str
  account_id: str
  run_id: str
  intent_id: str
  instrument_code: str
  side: str
  bucket: str
  reason: str
  target_volume: Optional[int]
  reference_price: Optional[float]
  estimated_amount: Optional[float]
  signal_expires_at: Optional[datetime]
  challenge_expires_at: datetime
  warnings: list[str]


@dataclass(frozen=True)
class TradeApprovalDispatchData:
  challenge_id: str
  message_id: str
  idempotency_key: str


def _optional_float(value: Any) -> Optional[float]:
  if value is None:
    return None
  try:
    parsed = float(value)
  except (TypeError, ValueError):
    return None
  return parsed if parsed >= 0 else None


def _local_naive(value: datetime) -> datetime:
  return time_utils.to_shanghai(value)


def _parse_local_datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime):
    return _local_naive(value)
  if not value:
    return None
  try:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
  except (TypeError, ValueError):
    return None
  return _local_naive(parsed)


def _intent_expiry(record: TradeIntentRecord) -> Optional[datetime]:
  metadata = dict(record.intent_metadata or {})
  created_at = _parse_local_datetime(metadata.get("intent_created_at"))
  if created_at is None and record.created_at is not None:
    created_at = _local_naive(record.created_at)
  try:
    ttl_ms = max(0, int(metadata.get("approval_ttl_ms", 0) or 0))
  except (TypeError, ValueError):
    ttl_ms = 0
  if created_at is None or ttl_ms <= 0:
    return None
  return created_at + timedelta(milliseconds=ttl_ms)


def _intent_subject_payload(record: TradeIntentRecord) -> dict[str, Any]:
  metadata = dict(record.intent_metadata or {})
  # The legacy strategy/exit approval path still uses this slot because the
  # Engine validates those challenge audits from the intent snapshot.  T-trade
  # approvals use the independent TradeConfirmationChallenge table below.
  metadata.pop(_CHALLENGE_METADATA_KEY, None)
  return {
    "id": record.id,
    "run_id": record.strategy_run_id,
    "owner_type": record.owner_type,
    "owner_id": record.owner_id,
    "account_id": record.account_id,
    "instrument_code": record.instrument_code,
    "direction": record.direction,
    "bucket": record.bucket,
    "reason": record.reason,
    "status": record.status,
    "confidence": record.confidence,
    "target_amount": record.target_amount,
    "target_position_pct": record.target_position_pct,
    "target_volume": record.target_volume,
    "limit_price_hint": record.limit_price_hint,
    "metadata": metadata,
  }


def _intent_fingerprint(record: TradeIntentRecord) -> str:
  encoded = json.dumps(
    _intent_subject_payload(record),
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
    default=str,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def _command_payload_fingerprint(payload: Any) -> str:
  _normalized, encoded = engine_command_service._canonical_payload(payload)
  return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def challenge_token_digest(token: str) -> str:
  """Return an HMAC digest suitable for persisting a confirmation token."""

  key = require_signing_key(settings)
  return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def signed_payload_fingerprint(payload: dict[str, Any]) -> str:
  """Bind a canonical payload to the server signing key without storing secrets."""

  encoded = json.dumps(
    payload,
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
    default=str,
  ).encode("utf-8")
  key = require_signing_key(settings)
  return hmac.new(key, encoded, hashlib.sha256).hexdigest()


def validate_persistent_trade_challenge(
  *,
  challenge: Any,
  principal: Principal,
  action: str,
  confirmation_token: str,
  now: datetime,
  payload: dict[str, Any],
  allow_consumed: bool = False,
) -> None:
  """Validate the common binding contract for a durable one-time challenge.

  The persistence model deliberately remains action-neutral so later liquidation
  confirmations can reuse the same binding and replay rules.
  """

  principal.require_account(str(challenge.account_id))
  if (
    str(challenge.action) != action
    or str(challenge.user_id) != principal.user_id
    or str(challenge.device_session_id) != principal.device_session_id
  ):
    raise TradeApprovalChallengeError(
      "CONFIRMATION_CONTEXT_MISMATCH",
      "确认凭据不属于当前用户、设备会话、账户或交易动作",
    )
  if not hmac.compare_digest(
    str(challenge.token_digest or ""),
    challenge_token_digest(confirmation_token),
  ):
    raise TradeApprovalChallengeError(
      "INVALID_CONFIRMATION_TOKEN", "确认凭据无效，请重新获取预览"
    )
  if challenge.consumed_at is not None and not allow_consumed:
    raise TradeApprovalChallengeError(
      "CONFIRMATION_ALREADY_USED", "确认凭据已使用，请刷新交易状态"
    )
  expires_at = _parse_local_datetime(challenge.expires_at)
  # Once a command has been durably queued, the challenge TTL must not turn a
  # client timeout into an ambiguous result.  A consumed replay still has to
  # match the original principal, device, token and signed payload above, but
  # it can only recover the persisted result; it cannot enqueue again.
  if (
    expires_at is None
    or (
      expires_at <= now
      and not (allow_consumed and challenge.consumed_at is not None)
    )
  ):
    raise TradeApprovalChallengeError(
      "CONFIRMATION_EXPIRED", "确认凭据已过期，请重新获取预览"
    )
  if not hmac.compare_digest(
    str(challenge.payload_fingerprint or ""),
    signed_payload_fingerprint(payload),
  ):
    raise TradeApprovalChallengeError(
      "TRADE_PAYLOAD_CHANGED", "交易内容已变化，请重新获取预览"
    )


def _aware_shanghai(value: datetime) -> datetime:
  return time_utils.to_shanghai(value, keep_tz=True)


def _validate_pending_intent(
  record: Optional[TradeIntentRecord],
  *,
  action: str,
  run_id: str,
  intent_id: str,
) -> TradeIntentRecord:
  is_exit_plan = action == EXIT_PLAN_SELL_APPROVAL
  belongs_to_owner = bool(
    record
    and (
      (is_exit_plan and record.owner_type == "EXIT_PLAN" and record.owner_id == run_id)
      or (not is_exit_plan and record.strategy_run_id == run_id)
    )
  )
  if record is None or record.id != intent_id or not belongs_to_owner:
    raise TradeApprovalChallengeError(
      "INTENT_NOT_FOUND",
      "交易信号不存在或不属于当前业务对象",
    )
  if str(record.status or "").upper() != "AWAITING_APPROVAL":
    raise TradeApprovalChallengeError(
      "INTENT_NOT_AWAITING_APPROVAL",
      "交易信号已处理、已过期或不再等待确认",
    )
  expected_direction = "SELL" if is_exit_plan else "BUY"
  if str(record.direction or "").upper() != expected_direction:
    raise TradeApprovalChallengeError(
      "UNSUPPORTED_APPROVAL_ACTION",
      f"当前确认动作只支持 {expected_direction} 意图",
    )
  return record


class TradeApprovalChallengeService:
  """Issue and atomically consume durable, device-bound approval challenges."""

  @staticmethod
  async def _issue_legacy(
    *,
    principal: Principal,
    action: str,
    account_id: str,
    run_id: str,
    intent_id: str,
  ) -> TradeApprovalPreviewData:
    """Keep the Engine-managed strategy/exit approval contract intact.

    The V3 T-trade path is the only path that dispatches its Engine command
    from this service.  Strategy and exit confirmations still pass a consumed
    challenge id to their existing application services, and the Engine reads
    that audit from the intent snapshot.  Do not silently migrate those paths
    to the API-owned challenge table without changing their Engine port.
    """

    normalized_account_id = principal.require_account(account_id)
    raw_token = secrets.token_urlsafe(48)
    now = time_utils.now()

    async for db in get_async_db():
      result = await db.execute(
        select(TradeIntentRecord)
        .where(TradeIntentRecord.id == intent_id)
        .with_for_update()
      )
      record = _validate_pending_intent(
        result.scalar_one_or_none(),
        action=action,
        run_id=run_id,
        intent_id=intent_id,
      )
      if record.account_id and record.account_id != normalized_account_id:
        raise TradeApprovalChallengeError(
          "CONFIRMATION_CONTEXT_MISMATCH",
          "交易意图不属于当前账户",
        )
      signal_expires_at = _intent_expiry(record)
      if signal_expires_at is not None and signal_expires_at <= now:
        raise TradeApprovalChallengeError(
          "APPROVAL_TTL_EXPIRED",
          "信号已超过确认有效期，请等待新信号",
        )
      challenge_expires_at = now + _MAX_CHALLENGE_LIFETIME
      if signal_expires_at is not None:
        challenge_expires_at = min(challenge_expires_at, signal_expires_at)

      metadata = dict(record.intent_metadata or {})
      challenge = {
        "challenge_id": str(uuid.uuid4()),
        "token_digest": challenge_token_digest(raw_token),
        "action": action,
        "user_id": principal.user_id,
        "device_session_id": principal.device_session_id,
        "account_id": normalized_account_id,
        "run_id": run_id,
        "intent_id": intent_id,
        "intent_fingerprint": _intent_fingerprint(record),
        "created_at": _aware_shanghai(now).isoformat(),
        "expires_at": _aware_shanghai(challenge_expires_at).isoformat(),
        "consumed_at": None,
      }
      metadata[_CHALLENGE_METADATA_KEY] = challenge
      record.intent_metadata = metadata
      await db.commit()

      signal = dict(metadata.get("signal", {}) or {})
      reference_price = _optional_float(
        signal.get("signal_price")
        or metadata.get("signal_price")
        or record.limit_price_hint
      )
      target_volume = (
        int(record.target_volume) if record.target_volume is not None else None
      )
      estimated_amount = _optional_float(record.target_amount)
      if estimated_amount is None and reference_price and target_volume:
        estimated_amount = reference_price * target_volume
      return TradeApprovalPreviewData(
        challenge_id=challenge["challenge_id"],
        confirmation_token=raw_token,
        action=action,
        account_id=normalized_account_id,
        run_id=run_id,
        intent_id=intent_id,
        instrument_code=str(record.instrument_code or ""),
        side=str(record.direction or ""),
        bucket=str(record.bucket or ""),
        reason=str(record.reason or ""),
        target_volume=target_volume,
        reference_price=reference_price,
        estimated_amount=estimated_amount,
        signal_expires_at=(
          _aware_shanghai(signal_expires_at) if signal_expires_at else None
        ),
        challenge_expires_at=_aware_shanghai(challenge_expires_at),
        warnings=[
          "确认仅授权该意图进入统一下单风控，不代表委托已提交或成交",
          "价格、资金、整手、涨跌停、T+1 与可用量会在确认后重新校验",
          "最终状态只能以 QMT Agent 上报的券商委托与成交回报为准",
        ],
      )
    raise TradeApprovalChallengeError("DATABASE_UNAVAILABLE", "确认服务暂不可用")

  @staticmethod
  async def _consume_legacy(
    *,
    principal: Principal,
    action: str,
    account_id: str,
    run_id: str,
    intent_id: str,
    confirmation_token: str,
  ) -> str:
    """Consume the pre-existing strategy/exit challenge contract."""

    normalized_account_id = principal.require_account(account_id)
    now = time_utils.now()
    async for db in get_async_db():
      result = await db.execute(
        select(TradeIntentRecord)
        .where(TradeIntentRecord.id == intent_id)
        .with_for_update()
      )
      record = _validate_pending_intent(
        result.scalar_one_or_none(),
        action=action,
        run_id=run_id,
        intent_id=intent_id,
      )
      if record.account_id and record.account_id != normalized_account_id:
        raise TradeApprovalChallengeError(
          "CONFIRMATION_CONTEXT_MISMATCH",
          "交易意图不属于当前账户",
        )
      metadata = dict(record.intent_metadata or {})
      challenge = dict(metadata.get(_CHALLENGE_METADATA_KEY, {}) or {})
      expected_bindings = {
        "action": action,
        "user_id": principal.user_id,
        "device_session_id": principal.device_session_id,
        "account_id": normalized_account_id,
        "run_id": run_id,
        "intent_id": intent_id,
      }
      if not challenge or any(
        str(challenge.get(key) or "") != str(value)
        for key, value in expected_bindings.items()
      ):
        raise TradeApprovalChallengeError(
          "CONFIRMATION_CONTEXT_MISMATCH",
          "确认上下文已变化，请重新获取预览",
        )
      if not hmac.compare_digest(
        str(challenge.get("token_digest") or ""),
        challenge_token_digest(confirmation_token),
      ):
        raise TradeApprovalChallengeError(
          "INVALID_CONFIRMATION_TOKEN",
          "确认凭据无效，请重新获取预览",
        )
      if challenge.get("consumed_at"):
        raise TradeApprovalChallengeError(
          "CONFIRMATION_ALREADY_USED",
          "确认凭据已使用，请刷新交易状态",
        )
      expires_at = _parse_local_datetime(challenge.get("expires_at"))
      if expires_at is None or expires_at <= now:
        raise TradeApprovalChallengeError(
          "CONFIRMATION_EXPIRED",
          "确认凭据已过期，请重新检查信号",
        )
      if str(challenge.get("intent_fingerprint") or "") != _intent_fingerprint(record):
        raise TradeApprovalChallengeError(
          "INTENT_CHANGED",
          "交易信号已变化，请重新获取预览",
        )
      challenge["consumed_at"] = _aware_shanghai(now).isoformat()
      metadata[_CHALLENGE_METADATA_KEY] = challenge
      record.intent_metadata = metadata
      await db.commit()
      return str(challenge["challenge_id"])
    raise TradeApprovalChallengeError("DATABASE_UNAVAILABLE", "确认服务暂不可用")

  @staticmethod
  async def _ensure_engine_command(
    *,
    db: Any,
    command_type: str,
    aggregate_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
  ) -> EngineCommandOutbox:
    normalized_payload, payload_json = engine_command_service._canonical_payload(
      payload
    )
    existing = await db.scalar(
      select(EngineCommandOutbox)
      .where(EngineCommandOutbox.idempotency_key == idempotency_key)
      .with_for_update()
    )
    if existing is not None:
      _existing_payload, existing_payload_json = (
        engine_command_service._canonical_payload(existing.payload)
      )
      if (
        str(existing.command_type) != command_type
        or str(existing.aggregate_id or "") != str(aggregate_id)
        or existing_payload_json != payload_json
      ):
        raise TradeApprovalChallengeError(
          "IDEMPOTENCY_KEY_REUSED",
          "确认幂等键已绑定不同请求，请生成新的请求幂等键",
        )
      return existing

    command = EngineCommandOutbox(
      message_id=str(uuid.uuid4()),
      idempotency_key=idempotency_key,
      command_type=command_type,
      aggregate_id=aggregate_id,
      payload=normalized_payload,
      processing_status="PENDING",
      available_at=utcnow(),
    )
    db.add(command)
    await db.flush()
    return command

  @staticmethod
  def _challenge_payload(
    *,
    principal: Principal,
    action: str,
    account_id: str,
    run_id: str,
    intent_id: str,
    intent_fingerprint: str,
  ) -> dict[str, Any]:
    return {
      "action": action,
      "user_id": principal.user_id,
      "device_session_id": principal.device_session_id,
      "account_id": account_id,
      "run_id": run_id,
      "intent_id": intent_id,
      "intent_fingerprint": intent_fingerprint,
    }

  @staticmethod
  def _matches_payload(
    challenge: TradeConfirmationChallenge,
    *,
    action: str,
    user_id: str,
    device_session_id: str,
    account_id: str,
    run_id: str,
    intent_id: str,
  ) -> bool:
    payload = dict(challenge.payload or {})
    expected = {
      "action": action,
      "user_id": user_id,
      "device_session_id": device_session_id,
      "account_id": account_id,
      "run_id": run_id,
      "intent_id": intent_id,
    }
    return all(
      str(payload.get(key) or "") == str(value)
      for key, value in expected.items()
    )

  @staticmethod
  def _matches_operation(
    challenge: TradeConfirmationChallenge,
    *,
    action: str,
    user_id: str,
    account_id: str,
    run_id: str,
    intent_id: str,
  ) -> bool:
    """Match an operation independently of the issuing device.

    A consumed challenge is a durable operation marker.  A second preview on
    another authenticated device must therefore observe an in-flight marker,
    rather than creating a second command while the first result is unknown.
    The token itself remains device-bound in ``consume``.
    """

    payload = dict(challenge.payload or {})
    expected = {
      "action": action,
      "user_id": user_id,
      "account_id": account_id,
      "run_id": run_id,
      "intent_id": intent_id,
    }
    return all(
      str(payload.get(key) or "") == str(value)
      for key, value in expected.items()
    )

  @staticmethod
  async def _command_for_challenge(
    db: Any,
    challenge: TradeConfirmationChallenge,
  ) -> Optional[EngineCommandOutbox]:
    reference = dict(challenge.result_reference or {}).get(
      "engine_command",
      {},
    )
    message_id = str(reference.get("message_id") or "")
    idempotency_key = str(reference.get("idempotency_key") or "")
    if message_id:
      return await db.scalar(
        select(EngineCommandOutbox).where(
          EngineCommandOutbox.message_id == message_id
        )
      )
    if idempotency_key:
      return await db.scalar(
        select(EngineCommandOutbox).where(
          EngineCommandOutbox.idempotency_key == idempotency_key
        )
      )
    return None

  @staticmethod
  def _terminal_rejection(command: EngineCommandOutbox) -> bool:
    status = str(command.processing_status or "").upper()
    result = dict(command.result or {})
    return status == "FAILED" or (
      status == "SUCCEEDED" and result.get("success") is False
    )

  @staticmethod
  def _preview_data(
    *,
    challenge: TradeConfirmationChallenge,
    record: TradeIntentRecord,
    action: str,
    account_id: str,
    run_id: str,
    intent_id: str,
    confirmation_token: str,
    signal_expires_at: Optional[datetime],
  ) -> TradeApprovalPreviewData:
    metadata = dict(record.intent_metadata or {})
    signal = dict(metadata.get("signal", {}) or {})
    reference_price = _optional_float(
      signal.get("signal_price")
      or metadata.get("signal_price")
      or record.limit_price_hint
    )
    target_volume = (
      int(record.target_volume) if record.target_volume is not None else None
    )
    estimated_amount = _optional_float(record.target_amount)
    if estimated_amount is None and reference_price and target_volume:
      estimated_amount = reference_price * target_volume
    return TradeApprovalPreviewData(
      challenge_id=str(challenge.id),
      confirmation_token=confirmation_token,
      action=action,
      account_id=account_id,
      run_id=run_id,
      intent_id=intent_id,
      instrument_code=str(record.instrument_code or ""),
      side=str(record.direction or ""),
      bucket=str(record.bucket or ""),
      reason=str(record.reason or ""),
      target_volume=target_volume,
      reference_price=reference_price,
      estimated_amount=estimated_amount,
      signal_expires_at=(
        _aware_shanghai(signal_expires_at) if signal_expires_at else None
      ),
      challenge_expires_at=_aware_shanghai(challenge.expires_at),
      warnings=[
        "确认仅授权该意图进入统一下单风控，不代表委托已提交或成交",
        "价格、资金、整手、涨跌停、T+1 与可用量会在确认后重新校验",
        "最终状态只能以 QMT Agent 上报的券商委托与成交回报为准",
      ],
    )

  @staticmethod
  async def issue(
    *,
    principal: Principal,
    action: str,
    account_id: str,
    run_id: str,
    intent_id: str,
  ) -> TradeApprovalPreviewData:
    if action != T_TRADE_ENTRY_APPROVAL:
      return await TradeApprovalChallengeService._issue_legacy(
        principal=principal,
        action=action,
        account_id=account_id,
        run_id=run_id,
        intent_id=intent_id,
      )
    normalized_account_id = principal.require_account(account_id)
    raw_token = secrets.token_urlsafe(48)
    now = time_utils.now()

    async for db in get_async_db():
      result = await db.execute(
        select(TradeIntentRecord)
        .where(TradeIntentRecord.id == intent_id)
        .with_for_update()
      )
      raw_record = result.scalar_one_or_none()
      if raw_record is None or raw_record.id != intent_id:
        raise TradeApprovalChallengeError(
          "INTENT_NOT_FOUND",
          "交易信号不存在或不属于当前业务对象",
        )
      if raw_record.account_id and raw_record.account_id != normalized_account_id:
        raise TradeApprovalChallengeError(
          "CONFIRMATION_CONTEXT_MISMATCH",
          "交易意图不属于当前账户",
        )
      rows = (
        await db.execute(
          select(TradeConfirmationChallenge)
          .where(
            TradeConfirmationChallenge.user_id == principal.user_id,
            TradeConfirmationChallenge.account_id == normalized_account_id,
            TradeConfirmationChallenge.action == action,
            TradeConfirmationChallenge.payload["run_id"].as_string() == run_id,
            TradeConfirmationChallenge.payload["intent_id"].as_string()
            == intent_id,
          )
          .order_by(TradeConfirmationChallenge.created_at.desc())
          .limit(2)
          .with_for_update()
        )
      ).scalars().all()
      existing = next(
        (
          item
          for item in rows
          if TradeApprovalChallengeService._matches_operation(
            item,
            action=action,
            user_id=principal.user_id,
            account_id=normalized_account_id,
            run_id=run_id,
            intent_id=intent_id,
          )
        ),
        None,
      )
      if existing is not None and existing.consumed_at is not None:
        command = await TradeApprovalChallengeService._command_for_challenge(
          db,
          existing,
        )
        if command is None:
          raise TradeApprovalChallengeError(
            "APPROVAL_RESULT_PENDING",
            "确认命令提交结果尚不知是否已提交，请继续重试原确认请求",
          )
        if not TradeApprovalChallengeService._terminal_rejection(command):
          raise TradeApprovalChallengeError(
            "APPROVAL_RESULT_PENDING",
            "已有确认请求尚未确认结果，请继续重试原确认请求",
          )

      # There is one active preview per T-trade intent.  The intent row lock
      # serializes concurrent previews; invalidate any older unconsumed token
      # before issuing the new one so two previews cannot dispatch two outboxes.
      for item in rows:
        if (
          item.consumed_at is None
          and TradeApprovalChallengeService._matches_operation(
            item,
            action=action,
            user_id=principal.user_id,
            account_id=normalized_account_id,
            run_id=run_id,
            intent_id=intent_id,
          )
        ):
          item.expires_at = now
          item.result_reference = {
            "challenge_status": "REPLACED",
            "operation_status": "REPLACED",
          }

      record = _validate_pending_intent(
        raw_record,
        action=action,
        run_id=run_id,
        intent_id=intent_id,
      )
      signal_expires_at = _intent_expiry(record)
      if signal_expires_at is not None and signal_expires_at <= now:
        raise TradeApprovalChallengeError(
          "APPROVAL_TTL_EXPIRED",
          "信号已超过确认有效期，请等待新信号",
        )
      challenge_expires_at = now + _MAX_CHALLENGE_LIFETIME
      if signal_expires_at is not None:
        challenge_expires_at = min(challenge_expires_at, signal_expires_at)

      payload = TradeApprovalChallengeService._challenge_payload(
        principal=principal,
        action=action,
        account_id=normalized_account_id,
        run_id=run_id,
        intent_id=intent_id,
        intent_fingerprint=_intent_fingerprint(record),
      )
      challenge = TradeConfirmationChallenge(
        id=str(uuid.uuid4()),
        action=action,
        user_id=principal.user_id,
        device_session_id=principal.device_session_id,
        account_id=normalized_account_id,
        idempotency_key=str(uuid.uuid4()),
        payload=payload,
        payload_fingerprint=signed_payload_fingerprint(payload),
        token_digest=challenge_token_digest(raw_token),
        expires_at=challenge_expires_at,
        consumed_at=None,
        result_reference={
          "challenge_status": "PENDING",
          "operation_status": "PENDING",
        },
      )
      db.add(challenge)
      await db.commit()
      return TradeApprovalChallengeService._preview_data(
        challenge=challenge,
        record=record,
        action=action,
        account_id=normalized_account_id,
        run_id=run_id,
        intent_id=intent_id,
        confirmation_token=raw_token,
        signal_expires_at=signal_expires_at,
      )
    raise TradeApprovalChallengeError("DATABASE_UNAVAILABLE", "确认服务暂不可用")

  @staticmethod
  async def consume(
    *,
    principal: Principal,
    action: str,
    account_id: str,
    run_id: str,
    intent_id: str,
    confirmation_token: str,
    command_type: Optional[str] = None,
    command_aggregate_id: Optional[str] = None,
    command_idempotency_key: Optional[str] = None,
    command_idempotency_key_factory: Optional[Callable[[str], str]] = None,
    command_payload: Optional[dict[str, Any]] = None,
    return_command_reference: bool = False,
  ) -> str | TradeApprovalDispatchData:
    token = str(confirmation_token or "")
    if not token or len(token) > _MAX_TOKEN_LENGTH:
      raise TradeApprovalChallengeError(
        "INVALID_CONFIRMATION_TOKEN",
        "确认凭据无效，请重新获取预览",
      )
    if action != T_TRADE_ENTRY_APPROVAL:
      return await TradeApprovalChallengeService._consume_legacy(
        principal=principal,
        action=action,
        account_id=account_id,
        run_id=run_id,
        intent_id=intent_id,
        confirmation_token=token,
      )
    normalized_account_id = principal.require_account(account_id)
    now = time_utils.now()
    command_args = (
      command_type,
      command_aggregate_id,
      command_idempotency_key,
      command_idempotency_key_factory,
      command_payload,
    )
    if (action == T_TRADE_ENTRY_APPROVAL or any(value is not None for value in command_args)) and (
      command_type is None
      or command_aggregate_id is None
      or (command_idempotency_key is None and command_idempotency_key_factory is None)
      or command_payload is None
    ):
      raise TradeApprovalChallengeError(
        "INVALID_APPROVAL_COMMAND_BINDING",
        "确认命令绑定信息不完整",
      )

    async for db in get_async_db():
      result = await db.execute(
        select(TradeIntentRecord)
        .where(TradeIntentRecord.id == intent_id)
        .with_for_update()
      )
      record = result.scalar_one_or_none()
      is_exit_plan = action == EXIT_PLAN_SELL_APPROVAL
      belongs_to_owner = bool(
        record
        and (
          (
            is_exit_plan
            and record.owner_type == "EXIT_PLAN"
            and record.owner_id == run_id
          )
          or (not is_exit_plan and record.strategy_run_id == run_id)
        )
      )
      if record is None or record.id != intent_id or not belongs_to_owner:
        raise TradeApprovalChallengeError(
          "INTENT_NOT_FOUND",
          "交易信号不存在或不属于当前业务对象",
        )
      if record.account_id and record.account_id != normalized_account_id:
        raise TradeApprovalChallengeError(
          "CONFIRMATION_CONTEXT_MISMATCH",
          "交易意图不属于当前账户",
        )
      token_digest = challenge_token_digest(token)
      challenge_rows = (
        await db.execute(
          select(TradeConfirmationChallenge)
          .where(
            TradeConfirmationChallenge.user_id == principal.user_id,
            TradeConfirmationChallenge.account_id == normalized_account_id,
            TradeConfirmationChallenge.action == action,
            TradeConfirmationChallenge.payload["run_id"].as_string() == run_id,
            TradeConfirmationChallenge.payload["intent_id"].as_string()
            == intent_id,
            TradeConfirmationChallenge.token_digest == token_digest,
          )
          .order_by(TradeConfirmationChallenge.created_at.desc())
          .limit(1)
          .with_for_update()
        )
      ).scalars().all()
      challenge = next(
        (
          item
          for item in challenge_rows
          if hmac.compare_digest(str(item.token_digest or ""), token_digest)
        ),
        None,
      )
      if challenge is None:
        raise TradeApprovalChallengeError(
          "INVALID_CONFIRMATION_TOKEN",
          "确认凭据无效，请重新获取预览",
        )
      if str(challenge.device_session_id) != principal.device_session_id:
        raise TradeApprovalChallengeError(
          "CONFIRMATION_CONTEXT_MISMATCH",
          "确认凭据不属于当前用户、设备会话、账户或交易动作",
        )
      payload = dict(challenge.payload or {})
      if not TradeApprovalChallengeService._matches_payload(
        challenge,
        action=action,
        user_id=principal.user_id,
        device_session_id=principal.device_session_id,
        account_id=normalized_account_id,
        run_id=run_id,
        intent_id=intent_id,
      ):
        raise TradeApprovalChallengeError(
          "CONFIRMATION_CONTEXT_MISMATCH",
          "确认上下文已变化，请重新获取预览",
        )
      if not hmac.compare_digest(
        str(challenge.payload_fingerprint or ""),
        signed_payload_fingerprint(payload),
      ):
        raise TradeApprovalChallengeError(
          "TRADE_PAYLOAD_CHANGED",
          "交易内容已变化，请重新获取预览",
        )
      result_reference = dict(challenge.result_reference or {})
      if (
        challenge.consumed_at is None
        and str(result_reference.get("challenge_status") or "") == "REPLACED"
      ):
        raise TradeApprovalChallengeError(
          "CONFIRMATION_SUPERSEDED",
          "确认凭据已被更新的预览替代，请使用最新凭据",
        )
      resolved_command_idempotency_key = command_idempotency_key
      if command_idempotency_key_factory is not None:
        try:
          resolved_command_idempotency_key = command_idempotency_key_factory(
            str(challenge.id)
          )
        except (TypeError, ValueError) as exc:
          raise TradeApprovalChallengeError(
            "INVALID_APPROVAL_COMMAND_BINDING",
            "确认命令幂等键无效",
          ) from exc
      if command_payload is not None and not resolved_command_idempotency_key:
        raise TradeApprovalChallengeError(
          "INVALID_APPROVAL_COMMAND_BINDING",
          "确认命令幂等键无效",
        )
      if challenge.consumed_at is not None:
        # The challenge id is the stable operation identity for the follow-up
        # Engine command.  Re-consuming the same bound token must therefore
        # return it so a retry after a transport/crash window can read or
        # re-submit the same outbox row.  Do not require the intent to remain
        # AWAITING_APPROVAL: a prior delivery may already have advanced it to
        # a terminal state while the client was disconnected.
        existing_command = None
        command_reference = dict(result_reference.get("engine_command") or {})
        if command_payload is not None:
          if (
            str(command_reference.get("command_type") or "") != command_type
            or str(command_reference.get("aggregate_id") or "")
            != str(command_aggregate_id)
            or str(command_reference.get("idempotency_key") or "")
            != str(resolved_command_idempotency_key)
            or str(command_reference.get("payload_fingerprint") or "")
            != _command_payload_fingerprint(command_payload)
          ):
            raise TradeApprovalChallengeError(
              "IDEMPOTENCY_KEY_REUSED",
              "确认幂等键已绑定不同请求，请生成新的请求幂等键",
            )
          existing_command = await db.scalar(
            select(EngineCommandOutbox).where(
              EngineCommandOutbox.idempotency_key
              == resolved_command_idempotency_key
            )
          )
          if existing_command is None:
            raise TradeApprovalChallengeError(
              "APPROVAL_RESULT_PENDING",
              "确认命令提交结果尚不知是否已提交，请继续重试原确认请求",
            )
        if return_command_reference:
          if existing_command is None:
            raise TradeApprovalChallengeError(
              "APPROVAL_RESULT_PENDING",
            "确认命令提交结果尚不知是否已提交，请继续重试原确认请求",
            )
          return TradeApprovalDispatchData(
            challenge_id=str(challenge.id),
            message_id=str(existing_command.message_id),
            idempotency_key=str(resolved_command_idempotency_key),
          )
        return str(challenge.id)
      record = _validate_pending_intent(
        record,
        action=action,
        run_id=run_id,
        intent_id=intent_id,
      )
      expires_at = _parse_local_datetime(challenge.expires_at)
      if expires_at is None or expires_at <= now:
        raise TradeApprovalChallengeError(
          "CONFIRMATION_EXPIRED",
          "确认凭据已过期，请重新检查信号",
        )
      if str(payload.get("intent_fingerprint") or "") != _intent_fingerprint(record):
        raise TradeApprovalChallengeError(
          "INTENT_CHANGED",
          "交易信号已变化，请重新获取预览",
        )

      command = None
      if command_payload is not None:
        command = await TradeApprovalChallengeService._ensure_engine_command(
          db=db,
          command_type=str(command_type),
          aggregate_id=str(command_aggregate_id),
          idempotency_key=str(resolved_command_idempotency_key),
          payload=command_payload,
        )
        command_reference = {
          "message_id": str(command.message_id),
          "command_type": str(command_type),
          "aggregate_id": str(command_aggregate_id),
          "idempotency_key": str(resolved_command_idempotency_key),
          "payload_fingerprint": _command_payload_fingerprint(command_payload),
        }
      else:
        command_reference = None
      challenge.consumed_at = now
      challenge.result_reference = {
        "challenge_status": "CONSUMED",
        "operation_status": "DISPATCHING" if command else "CONSUMED",
        **({"engine_command": command_reference} if command_reference else {}),
      }
      await db.commit()
      if return_command_reference:
        if command is None:
          raise TradeApprovalChallengeError(
            "INVALID_APPROVAL_COMMAND_BINDING",
            "确认命令绑定信息不完整",
          )
        return TradeApprovalDispatchData(
          challenge_id=str(challenge.id),
          message_id=str(command.message_id),
          idempotency_key=str(resolved_command_idempotency_key),
        )
      return str(challenge.id)
    raise TradeApprovalChallengeError("DATABASE_UNAVAILABLE", "确认服务暂不可用")
