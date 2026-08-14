"""Fail-closed two-phase contract for authenticated mobile manual orders."""

from __future__ import annotations

import math
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from quantx_domain.brokers.base import (
  OrderRequest,
)
from quantx_domain.brokers.base import (
  OrderType as DomainOrderType,
)
from quantx_domain.brokers.base import (
  PriceType as DomainPriceType,
)
from quantx_domain.strategies.base import TradeIntent, TradeIntentDirection
from quantx_domain.trading.market_rules import AShareMarketRules, MarketDataSnapshot
from quantx_domain.trading.order_sizer import OrderSizer
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models import (
  Account,
  AccountTradingRollout,
  Instrument,
  Position,
  TradeCommandOutbox,
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.services.latest_market_quote_cache import (
  latest_market_quote_cache,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from quantx_api.auth.principal import Principal

from .trade_approval import (
  TradeApprovalChallengeError,
  challenge_token_digest,
  signed_payload_fingerprint,
  validate_persistent_trade_challenge,
)

MANUAL_ORDER_ACTION = "MANUAL_ORDER"
_CHALLENGE_LIFETIME = timedelta(seconds=60)
_MAX_QUOTE_AGE = timedelta(seconds=30)
_MAX_ACCOUNT_SNAPSHOT_AGE = timedelta(seconds=90)
_MAX_TOKEN_LENGTH = 256
_INSTRUMENT_CODE = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")
_SIDES = frozenset({"BUY", "SELL"})
_PRICE_TYPES = frozenset({"LIMIT", "BEST"})


@dataclass(frozen=True)
class ManualOrderRequestData:
  account_id: str
  instrument_code: str
  side: str
  price_type: str
  volume: int
  limit_price: Optional[float]
  idempotency_key: str

  def payload(self) -> dict[str, Any]:
    return {
      "action": MANUAL_ORDER_ACTION,
      "account_id": self.account_id,
      "instrument_code": self.instrument_code,
      "side": self.side,
      "price_type": self.price_type,
      "volume": self.volume,
      "limit_price": self.limit_price,
      "idempotency_key": self.idempotency_key,
      "execution_mode": "live",
    }

  @classmethod
  def from_payload(cls, payload: dict[str, Any]) -> "ManualOrderRequestData":
    if str(payload.get("action") or "") != MANUAL_ORDER_ACTION:
      raise TradeApprovalChallengeError(
        "CONFIRMATION_CONTEXT_MISMATCH",
        "确认上下文无效，请重新获取预览",
      )
    return normalize_manual_order_request(
      account_id=str(payload.get("account_id") or ""),
      instrument_code=str(payload.get("instrument_code") or ""),
      side=str(payload.get("side") or ""),
      price_type=str(payload.get("price_type") or ""),
      volume=int(payload.get("volume") or 0),
      limit_price=payload.get("limit_price"),
      idempotency_key=str(payload.get("idempotency_key") or ""),
    )


@dataclass(frozen=True)
class ManualOrderPreflightData:
  quote_timestamp: datetime
  reference_price: float
  estimated_amount: float
  estimated_fees: Optional[float]
  available_cash: float
  available_volume: Optional[int]
  warnings: list[str]


@dataclass(frozen=True)
class ManualOrderPreviewData:
  challenge_id: str
  confirmation_token: str
  request: ManualOrderRequestData
  preflight: ManualOrderPreflightData
  challenge_expires_at: datetime


@dataclass(frozen=True)
class ManualOrderConfirmationData:
  challenge_id: str
  client_order_id: str
  status: str


def _command_idempotency_key(
  challenge_id: str,
  request: ManualOrderRequestData,
) -> str:
  """Keep manual confirmation keys isolated from legacy direct-order callers."""

  return f"manual-order:{challenge_id}:{request.idempotency_key}"


def normalize_manual_order_request(
  *,
  account_id: str,
  instrument_code: str,
  side: str,
  price_type: str,
  volume: int,
  limit_price: Any,
  idempotency_key: str,
) -> ManualOrderRequestData:
  normalized_account_id = str(account_id or "").strip()
  normalized_code = str(instrument_code or "").strip().upper()
  normalized_side = str(getattr(side, "value", side) or "").strip().upper()
  normalized_price_type = (
    str(getattr(price_type, "value", price_type) or "").strip().upper()
  )
  normalized_key = str(idempotency_key or "").strip()
  try:
    normalized_volume = int(volume)
  except (TypeError, ValueError) as exc:
    raise TradeApprovalChallengeError(
      "INVALID_VOLUME", "委托数量必须是正整数"
    ) from exc

  if not normalized_account_id:
    raise TradeApprovalChallengeError("ACCOUNT_REQUIRED", "必须指定交易账户")
  if not _INSTRUMENT_CODE.fullmatch(normalized_code):
    raise TradeApprovalChallengeError(
      "INVALID_INSTRUMENT_CODE",
      "证券代码必须使用六位代码和 SH、SZ 或 BJ 市场后缀",
    )
  if normalized_side not in _SIDES:
    raise TradeApprovalChallengeError("INVALID_SIDE", "交易方向必须是 BUY 或 SELL")
  if normalized_price_type not in _PRICE_TYPES:
    raise TradeApprovalChallengeError(
      "INVALID_PRICE_TYPE", "报价类型必须是 LIMIT 或 BEST"
    )
  if normalized_volume <= 0:
    raise TradeApprovalChallengeError("INVALID_VOLUME", "委托数量必须大于 0")
  if not normalized_key or len(normalized_key) > 128:
    raise TradeApprovalChallengeError(
      "INVALID_IDEMPOTENCY_KEY", "幂等键不能为空且不能超过 128 个字符"
    )

  parsed_price: Optional[float]
  if limit_price is None:
    parsed_price = None
  else:
    try:
      parsed_price = float(limit_price)
    except (TypeError, ValueError) as exc:
      raise TradeApprovalChallengeError(
        "INVALID_LIMIT_PRICE", "限价必须是有效数字"
      ) from exc
    if not math.isfinite(parsed_price):
      raise TradeApprovalChallengeError(
        "INVALID_LIMIT_PRICE", "限价必须是有限数字"
      )

  if normalized_price_type == "LIMIT" and (parsed_price is None or parsed_price <= 0):
    raise TradeApprovalChallengeError(
      "LIMIT_PRICE_REQUIRED", "LIMIT 委托必须提供大于 0 的限价"
    )
  if normalized_price_type == "BEST":
    if normalized_code.endswith(".BJ"):
      raise TradeApprovalChallengeError(
        "BEST_NOT_SUPPORTED_FOR_MARKET",
        "BEST 暂未开放北交所；当前 QMT 契约仅明确映射沪深对手方最优价",
      )
    if parsed_price not in (None, 0.0):
      raise TradeApprovalChallengeError(
        "BEST_PRICE_MUST_BE_EMPTY", "BEST 委托不能携带限价"
      )
    parsed_price = None

  return ManualOrderRequestData(
    account_id=normalized_account_id,
    instrument_code=normalized_code,
    side=normalized_side,
    price_type=normalized_price_type,
    volume=normalized_volume,
    limit_price=parsed_price,
    idempotency_key=normalized_key,
  )


def _local_naive(value: datetime) -> datetime:
  return time_utils.to_shanghai(value)


def _aware_local(value: datetime) -> datetime:
  return time_utils.to_shanghai(value, keep_tz=True)


def _snapshot_age(value: Optional[datetime], now: datetime) -> Optional[timedelta]:
  if value is None:
    return None
  return now - _local_naive(value)


def _first_positive(values: Any) -> Optional[float]:
  for value in list(values or []):
    try:
      parsed = float(value)
    except (TypeError, ValueError):
      continue
    if math.isfinite(parsed) and parsed > 0:
      return parsed
  return None


async def _preflight(
  request: ManualOrderRequestData,
) -> ManualOrderPreflightData:
  ticks = await latest_market_quote_cache.get_ticks([request.instrument_code])
  tick = next(
    (
      item
      for item in ticks
      if str(getattr(item, "stock_code", "")).strip().upper()
      == request.instrument_code
    ),
    None,
  )
  if tick is None:
    raise TradeApprovalChallengeError(
      "QUOTE_UNAVAILABLE", "缺少最新行情，已拒绝生成交易确认"
    )

  now = time_utils.now()
  quote_timestamp = getattr(tick, "time", None)
  quote_age = _snapshot_age(quote_timestamp, now)
  if quote_age is None or quote_age < timedelta(0) or quote_age > _MAX_QUOTE_AGE:
    raise TradeApprovalChallengeError(
      "QUOTE_STALE", "行情已过期，已拒绝生成交易确认"
    )

  async with AsyncSessionLocal() as db:
    instrument = await db.get(Instrument, request.instrument_code)
    if instrument is None:
      raise TradeApprovalChallengeError(
        "INSTRUMENT_NOT_FOUND", "证券主数据不存在，已拒绝下单"
      )
    if getattr(instrument, "is_trading", None) is False:
      raise TradeApprovalChallengeError(
        "INSTRUMENT_NOT_TRADING", "证券主数据标记为不可交易"
      )

    account = (
      await db.execute(
        select(Account).where(
          Account.account_id == request.account_id,
          Account.account_type == AccountType.STOCK,
        )
      )
    ).scalar_one_or_none()
    if account is None:
      raise TradeApprovalChallengeError(
        "ACCOUNT_SNAPSHOT_UNAVAILABLE", "交易账户快照不存在"
      )
    account_age = _snapshot_age(getattr(account, "updated_at", None), now)
    if (
      account_age is None
      or account_age < timedelta(0)
      or account_age > _MAX_ACCOUNT_SNAPSHOT_AGE
    ):
      raise TradeApprovalChallengeError(
        "ACCOUNT_SNAPSHOT_STALE", "账户快照已过期，已拒绝下单"
      )

    position = (
      await db.execute(
        select(Position).where(
          Position.account_id == request.account_id,
          Position.stock_code == request.instrument_code,
        )
      )
    ).scalar_one_or_none()
    if request.side == "SELL":
      if position is None:
        raise TradeApprovalChallengeError(
          "POSITION_SNAPSHOT_UNAVAILABLE",
          "缺少持仓快照，已拒绝卖出",
        )
      position_age = _snapshot_age(getattr(position, "updated_at", None), now)
      if (
        position_age is None
        or position_age < timedelta(0)
        or position_age > _MAX_ACCOUNT_SNAPSHOT_AGE
      ):
        raise TradeApprovalChallengeError(
          "POSITION_SNAPSHOT_STALE",
          "持仓可卖量快照已过期，已拒绝卖出",
        )

    rollout = await db.get(AccountTradingRollout, request.account_id)
    if (
      request.side == "BUY"
      and rollout is not None
      and (
        bool(rollout.kill_switch)
        or str(rollout.stage or "").upper() == "KILL_SWITCHED"
      )
    ):
      raise TradeApprovalChallengeError(
        "ACCOUNT_KILL_SWITCHED", "账户交易熔断已触发，禁止新增风险"
      )

  market = MarketDataSnapshot.from_tick(tick)
  market.price_tick = float(getattr(instrument, "price_tick", None) or 0.01)
  market.limit_up = float(getattr(instrument, "up_stop_price", None) or 0) or None
  market.limit_down = float(getattr(instrument, "down_stop_price", None) or 0) or None
  rules = AShareMarketRules()
  trading_status = rules.check_trading_status(market, strict_market_data=True)
  if not trading_status.ok:
    raise TradeApprovalChallengeError(
      "INSTRUMENT_NOT_TRADING", trading_status.message
    )

  if request.price_type == "BEST":
    price = _first_positive(
      market.ask_price if request.side == "BUY" else market.bid_price
    )
    if price is None:
      raise TradeApprovalChallengeError(
        "BEST_PRICE_UNAVAILABLE", "对手方最优价不可用，已拒绝 BEST 委托"
      )
    domain_price_type = DomainPriceType.MARKET
  else:
    price = float(request.limit_price or 0)
    domain_price_type = DomainPriceType.LIMIT

  domain_side = DomainOrderType(request.side)
  available_volume = int(getattr(position, "can_use_volume", 0) or 0)
  order_request = OrderRequest(
    instrument_code=request.instrument_code,
    order_type=domain_side,
    price_type=domain_price_type,
    volume=request.volume,
    price=price,
  )
  price_check = rules.check_price(
    order_request,
    market,
    strict_limit_data=request.price_type == "LIMIT",
  )
  if not price_check.ok:
    raise TradeApprovalChallengeError("ORDER_PRICE_REJECTED", price_check.message)

  min_volume = (
    getattr(instrument, "min_limit_sell_order_volume", None)
    if request.side == "SELL" and request.price_type == "LIMIT"
    else getattr(instrument, "min_market_sell_order_volume", None)
    if request.side == "SELL"
    else getattr(instrument, "min_limit_order_volume", None)
    if request.price_type == "LIMIT"
    else getattr(instrument, "min_market_order_volume", None)
  )
  max_volume = (
    getattr(instrument, "max_limit_sell_order_volume", None)
    if request.side == "SELL" and request.price_type == "LIMIT"
    else getattr(instrument, "max_market_sell_order_volume", None)
    if request.side == "SELL"
    else getattr(instrument, "max_limit_order_volume", None)
    if request.price_type == "LIMIT"
    else getattr(instrument, "max_market_order_volume", None)
  )
  volume_check = rules.check_volume(
    order_request,
    available_volume=available_volume,
    min_volume=min_volume,
    max_volume=max_volume,
  )
  if not volume_check.ok:
    raise TradeApprovalChallengeError("ORDER_VOLUME_REJECTED", volume_check.message)

  intent = TradeIntent(
    strategy_id="manual-mobile",
    run_id="manual-mobile",
    instrument_code=request.instrument_code,
    direction=TradeIntentDirection(request.side),
    bucket="manual",
    reason="MOBILE_MANUAL_ORDER",
    target_volume=request.volume,
  )
  draft = OrderSizer(rules).draft_intent(
    intent,
    domain_side,
    price,
    {"available_cash": float(account.cash or 0), "total_asset": float(account.total_asset or 0)},
    {"available_volume": available_volume},
  )
  if draft.sized_volume != request.volume:
    raise TradeApprovalChallengeError(
      "ORDER_SIZING_REJECTED",
      "请求数量未通过 A 股整手、零股或可卖量校验",
    )

  estimated_amount = float(price) * float(request.volume)
  available_cash = float(account.cash or 0)
  if request.side == "BUY" and estimated_amount > available_cash:
    raise TradeApprovalChallengeError(
      "INSUFFICIENT_AVAILABLE_CASH",
      "可用资金不足；费用尚未计入，因此服务端已保守拒绝",
    )

  return ManualOrderPreflightData(
    quote_timestamp=_aware_local(quote_timestamp),
    reference_price=float(price),
    estimated_amount=estimated_amount,
    estimated_fees=None,
    available_cash=available_cash,
    available_volume=available_volume if request.side == "SELL" else None,
    warnings=[
      "手续费当前无权威费率服务，预估费用不可用；券商可能因费用或资金变化拒绝",
      "确认只会将命令排队，不表示券商受理、委托成功或成交",
      "确认时会重新检查行情、账户快照、可卖量、涨跌停和交易熔断状态",
      "最终状态只能以 QMT Agent 上报并由 Engine 收敛的券商回报为准",
    ],
  )


class ManualOrderChallengeService:
  @staticmethod
  async def issue(
    *,
    principal: Principal,
    request: ManualOrderRequestData,
  ) -> ManualOrderPreviewData:
    account_id = principal.require_account(request.account_id)
    if account_id != request.account_id:
      raise TradeApprovalChallengeError(
        "CONFIRMATION_CONTEXT_MISMATCH", "交易账户上下文不一致"
      )
    preflight = await _preflight(request)
    raw_token = secrets.token_urlsafe(48)
    now = time_utils.now()
    challenge = TradeConfirmationChallenge(
      id=str(uuid.uuid4()),
      action=MANUAL_ORDER_ACTION,
      user_id=principal.user_id,
      device_session_id=principal.device_session_id,
      account_id=request.account_id,
      idempotency_key=request.idempotency_key,
      payload=request.payload(),
      payload_fingerprint=signed_payload_fingerprint(request.payload()),
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
          "该幂等键已用于手动下单，请查询原委托状态",
        ) from exc
    return ManualOrderPreviewData(
      challenge_id=challenge.id,
      confirmation_token=raw_token,
      request=request,
      preflight=preflight,
      challenge_expires_at=_aware_local(challenge.expires_at),
    )

  @staticmethod
  async def confirm(
    *,
    principal: Principal,
    challenge_id: str,
    confirmation_token: str,
  ) -> ManualOrderConfirmationData:
    token = str(confirmation_token or "")
    normalized_challenge_id = str(challenge_id or "").strip()
    if not normalized_challenge_id or not token or len(token) > _MAX_TOKEN_LENGTH:
      raise TradeApprovalChallengeError(
        "INVALID_CONFIRMATION_TOKEN", "确认凭据无效，请重新获取预览"
      )

    async with AsyncSessionLocal() as db:
      challenge = await db.get(TradeConfirmationChallenge, normalized_challenge_id)
      if challenge is None:
        raise TradeApprovalChallengeError(
          "CONFIRMATION_NOT_FOUND", "确认挑战不存在或已失效"
        )
      ManualOrderChallengeService._validate_challenge(
        challenge=challenge,
        principal=principal,
        token=token,
        now=time_utils.now(),
        allow_consumed=True,
      )
      request = ManualOrderRequestData.from_payload(dict(challenge.payload or {}))
      if challenge.consumed_at is not None:
        return await ManualOrderChallengeService._existing_result(
          db=db,
          challenge=challenge,
          request=request,
        )

    # Mutable facts are intentionally fetched again after the preview and before
    # the one-time challenge is locked and consumed.
    preflight = await _preflight(request)

    async with AsyncSessionLocal() as db:
      challenge = (
        await db.execute(
          select(TradeConfirmationChallenge)
          .where(TradeConfirmationChallenge.id == normalized_challenge_id)
          .with_for_update()
        )
      ).scalar_one_or_none()
      if challenge is None:
        raise TradeApprovalChallengeError(
          "CONFIRMATION_NOT_FOUND", "确认挑战不存在或已失效"
        )
      now = time_utils.now()
      ManualOrderChallengeService._validate_challenge(
        challenge=challenge,
        principal=principal,
        token=token,
        now=now,
        allow_consumed=True,
      )
      locked_request = ManualOrderRequestData.from_payload(dict(challenge.payload or {}))
      if locked_request != request:
        raise TradeApprovalChallengeError(
          "CONFIRMATION_CONTEXT_MISMATCH", "确认期间订单内容发生变化"
        )
      if challenge.consumed_at is not None:
        return await ManualOrderChallengeService._existing_result(
          db=db,
          challenge=challenge,
          request=locked_request,
        )

      challenge.consumed_at = now
      try:
        queued = await TradeCommandService(db).enqueue_order(
          user_id=principal.user_id,
          account_id=request.account_id,
          instrument_code=request.instrument_code,
          side=request.side,
          order_type=(
            "FIX_PRICE"
            if request.price_type == "LIMIT"
            else "MARKET_PEER_PRICE_FIRST"
          ),
          limit_price=Decimal(str(request.limit_price or 0)),
          volume=request.volume,
          strategy_name="manual-mobile",
          order_remark="QuantX iOS 手动委托",
          trace_id=challenge.id,
          idempotency_key=_command_idempotency_key(challenge.id, request),
          execution_mode="live",
          bucket="manual",
          manual_live=True,
          request_metadata={
            "origin": "IOS_MANUAL",
            "challenge_id": challenge.id,
            "payload_fingerprint": challenge.payload_fingerprint,
            "quote_timestamp": preflight.quote_timestamp.isoformat(),
          },
        )
      except Exception:
        await db.rollback()
        raise

      # enqueue_order commits the consumed flag and outbox atomically on the new
      # command path. Store correlation fields in a follow-up idempotent update.
      challenge = await db.get(TradeConfirmationChallenge, normalized_challenge_id)
      if challenge is not None:
        challenge.result_reference = {
          "client_order_id": queued.client_order_id,
          "message_id": queued.message_id,
          "status": queued.status,
        }
        await db.commit()
      return ManualOrderConfirmationData(
        challenge_id=normalized_challenge_id,
        client_order_id=queued.client_order_id,
        status=queued.status,
      )

  @staticmethod
  def _validate_challenge(
    *,
    challenge: TradeConfirmationChallenge,
    principal: Principal,
    token: str,
    now: datetime,
    allow_consumed: bool = False,
  ) -> None:
    payload = dict(challenge.payload or {})
    validate_persistent_trade_challenge(
      challenge=challenge,
      principal=principal,
      action=MANUAL_ORDER_ACTION,
      confirmation_token=token,
      now=now,
      payload=payload,
      allow_consumed=allow_consumed,
    )

  @staticmethod
  async def _existing_result(
    *,
    db: Any,
    challenge: TradeConfirmationChallenge,
    request: ManualOrderRequestData,
  ) -> ManualOrderConfirmationData:
    reference = dict(challenge.result_reference or {})
    client_order_id = str(reference.get("client_order_id") or "")
    status = str(reference.get("status") or "")
    if client_order_id and status:
      return ManualOrderConfirmationData(
        challenge_id=str(challenge.id),
        client_order_id=client_order_id,
        status=status,
      )

    digest = TradeCommandService.order_idempotency_digest(
      user_id=str(challenge.user_id),
      account_id=request.account_id,
      idempotency_key=_command_idempotency_key(str(challenge.id), request),
    )
    outbox = (
      await db.execute(
        select(TradeCommandOutbox).where(
          TradeCommandOutbox.idempotency_key == digest,
          TradeCommandOutbox.account_id == request.account_id,
        )
      )
    ).scalar_one_or_none()
    if outbox is None:
      raise TradeApprovalChallengeError(
        "CONFIRMATION_RESULT_PENDING",
        "确认已消费但排队结果暂不可用，请刷新委托列表后重试",
      )
    challenge.result_reference = {
      "client_order_id": outbox.client_order_id,
      "message_id": outbox.message_id,
      "status": outbox.delivery_status,
    }
    await db.commit()
    return ManualOrderConfirmationData(
      challenge_id=str(challenge.id),
      client_order_id=str(outbox.client_order_id),
      status=str(outbox.delivery_status),
    )
