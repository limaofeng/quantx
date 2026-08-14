"""Two-phase, device-bound approval challenges for manual trade intents."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
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


def _token_digest(token: str) -> str:
  key = require_signing_key(settings)
  return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


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
  """Issue and atomically consume a short-lived challenge on the intent row."""

  @staticmethod
  async def issue(
    *,
    principal: Principal,
    action: str,
    account_id: str,
    run_id: str,
    intent_id: str,
  ) -> TradeApprovalPreviewData:
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

      challenge_id = str(uuid.uuid4())
      challenge = {
        "challenge_id": challenge_id,
        "token_digest": _token_digest(raw_token),
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
      metadata = dict(record.intent_metadata or {})
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
        challenge_id=challenge_id,
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
  async def consume(
    *,
    principal: Principal,
    action: str,
    account_id: str,
    run_id: str,
    intent_id: str,
    confirmation_token: str,
  ) -> str:
    token = str(confirmation_token or "")
    if not token or len(token) > _MAX_TOKEN_LENGTH:
      raise TradeApprovalChallengeError(
        "INVALID_CONFIRMATION_TOKEN",
        "确认凭据无效，请重新获取预览",
      )
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
      stored_digest = str(challenge.get("token_digest") or "")
      if not stored_digest or not hmac.compare_digest(
        stored_digest,
        _token_digest(token),
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
