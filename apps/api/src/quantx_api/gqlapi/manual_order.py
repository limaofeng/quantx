"""Fail-closed two-phase contract for authenticated mobile manual orders."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import uuid
from dataclasses import dataclass, replace
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
from quantx_domain.trading.risk_checker import RiskAction, TradingRiskChecker
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models import (
  Account,
  Instrument,
  Position,
  TradeCommandOutbox,
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.services.latest_market_quote_cache import (
  latest_market_quote_cache,
)
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  TradeCommandService,
)
from quantx_infrastructure.services.trading_time_service import TradingTimeService
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

MANUAL_ORDER_ACTION = "MANUAL_ORDER"
_CHALLENGE_LIFETIME = timedelta(seconds=60)
_MAX_QUOTE_AGE = timedelta(seconds=30)
_BEST_CONFIRMATION_MAX_AGE = timedelta(seconds=10)
_MAX_ACCOUNT_SNAPSHOT_AGE = timedelta(seconds=90)
_MAX_TOKEN_LENGTH = 256
_INSTRUMENT_CODE = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")
_SIDES = frozenset({"BUY", "SELL"})
_PRICE_TYPES = frozenset({"LIMIT", "BEST"})
_EXECUTION_MODES = frozenset({"PAPER", "LIVE"})
_trading_time_service = TradingTimeService()


@dataclass(frozen=True)
class ManualOrderRequestData:
  account_id: str
  instrument_code: str
  side: str
  price_type: str
  volume: int
  limit_price: Optional[float]
  idempotency_key: str
  execution_mode: str

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
      "execution_mode": self.execution_mode.lower(),
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
      execution_mode=str(payload.get("execution_mode") or ""),
    )


@dataclass(frozen=True)
class ManualOrderPreflightData:
  quote_timestamp: datetime
  quote_fingerprint: str
  reference_price: float
  requested_volume: int
  final_volume: int
  estimated_amount: float
  estimated_fees: Optional[float]
  available_cash: float
  available_volume: Optional[int]
  rollout_snapshot_id: str
  rollout_snapshot_hash: str
  account_updated_at: datetime
  position_updated_at: Optional[datetime]
  risk_decision_id: str
  risk_action: str
  risk_reason_code: str
  risk_reason_detail: str
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
  execution_mode: Any = "PAPER",
) -> ManualOrderRequestData:
  normalized_account_id = str(account_id or "").strip()
  normalized_code = str(instrument_code or "").strip().upper()
  normalized_side = str(getattr(side, "value", side) or "").strip().upper()
  normalized_price_type = (
    str(getattr(price_type, "value", price_type) or "").strip().upper()
  )
  normalized_key = str(idempotency_key or "").strip()
  normalized_execution_mode = (
    str(getattr(execution_mode, "value", execution_mode) or "").strip().upper()
  )
  try:
    normalized_volume = int(volume)
  except (TypeError, ValueError) as exc:
    raise TradeApprovalChallengeError("INVALID_VOLUME", "委托数量必须是正整数") from exc

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
  if normalized_execution_mode not in _EXECUTION_MODES:
    raise TradeApprovalChallengeError(
      "INVALID_EXECUTION_MODE",
      "执行模式必须是 PAPER 或 LIVE",
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
      raise TradeApprovalChallengeError("INVALID_LIMIT_PRICE", "限价必须是有限数字")

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
    execution_mode=normalized_execution_mode,
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


def _canonical_number(value: Any) -> Optional[str]:
  if value is None:
    return None
  try:
    parsed = Decimal(str(value))
  except (ArithmeticError, TypeError, ValueError):
    return None
  if not parsed.is_finite():
    return None
  return format(parsed.normalize(), "f")


def _version_token(value: Optional[datetime]) -> Optional[str]:
  if value is None:
    return None
  return value.isoformat(timespec="microseconds")


def _paper_snapshot_binding(account: Any, position: Any) -> tuple[str, str]:
  """Bind paper rehearsal to the same account/position facts used by sizing."""

  payload = {
    "account_id": str(getattr(account, "account_id", "") or ""),
    "cash": _canonical_number(getattr(account, "cash", None)),
    "total_asset": _canonical_number(getattr(account, "total_asset", None)),
    "account_updated_at": _version_token(getattr(account, "updated_at", None)),
    "position_volume": (
      int(getattr(position, "volume", 0) or 0) if position is not None else None
    ),
    "position_available_volume": (
      int(getattr(position, "can_use_volume", 0) or 0) if position is not None else None
    ),
    "position_updated_at": (
      _version_token(getattr(position, "updated_at", None))
      if position is not None
      else None
    ),
  }
  encoded = json.dumps(
    payload,
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
  ).encode("utf-8")
  digest = hashlib.sha256(encoded).hexdigest()
  return f"paper-{digest[:24]}", digest


def _quote_fingerprint(
  *,
  instrument_code: str,
  tick: Any,
  market: MarketDataSnapshot,
) -> str:
  """Hash one normalized quote book; the signed challenge binds this digest."""

  payload = {
    "instrument_code": instrument_code,
    "timestamp": _aware_local(market.timestamp).isoformat(timespec="microseconds"),
    "last_price": _canonical_number(market.price),
    "bid_price": [_canonical_number(value) for value in list(market.bid_price or [])],
    "ask_price": [_canonical_number(value) for value in list(market.ask_price or [])],
    "bid_volume": [_canonical_number(value) for value in list(market.bid_vol or [])],
    "ask_volume": [_canonical_number(value) for value in list(market.ask_vol or [])],
    "stock_status": str(getattr(tick, "stock_status", "")),
    "is_trading": bool(market.is_trading),
    "suspended": bool(market.suspended),
    "limit_up": _canonical_number(market.limit_up),
    "limit_down": _canonical_number(market.limit_down),
  }
  encoded = json.dumps(
    payload,
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def _stable_risk_decision_id(challenge_id: str) -> str:
  return str(uuid.uuid5(uuid.NAMESPACE_URL, f"quantx:manual-order-risk:{challenge_id}"))


def _challenge_payload(
  request: ManualOrderRequestData,
  preflight: ManualOrderPreflightData,
) -> dict[str, Any]:
  return {
    **request.payload(),
    "preview_reference_price": _canonical_number(preflight.reference_price),
    "preview_quote_timestamp": preflight.quote_timestamp.isoformat(
      timespec="microseconds"
    ),
    "preview_quote_fingerprint": preflight.quote_fingerprint,
    "requested_volume": preflight.requested_volume,
    "final_volume": preflight.final_volume,
    "rollout_snapshot_id": preflight.rollout_snapshot_id,
    "rollout_snapshot_hash": preflight.rollout_snapshot_hash,
    "account_updated_at": _version_token(preflight.account_updated_at),
    "position_updated_at": _version_token(preflight.position_updated_at),
    "risk_decision_id": preflight.risk_decision_id,
    "risk_action": preflight.risk_action,
    "risk_reason_code": preflight.risk_reason_code,
    "risk_reason_detail": preflight.risk_reason_detail,
  }


def _validate_snapshot_binding(
  payload: dict[str, Any],
  preflight: ManualOrderPreflightData,
) -> None:
  expected = {
    "rollout_snapshot_id": str(payload.get("rollout_snapshot_id") or ""),
    "rollout_snapshot_hash": str(payload.get("rollout_snapshot_hash") or ""),
    "account_updated_at": payload.get("account_updated_at"),
    "position_updated_at": payload.get("position_updated_at"),
  }
  current = {
    "rollout_snapshot_id": preflight.rollout_snapshot_id,
    "rollout_snapshot_hash": preflight.rollout_snapshot_hash,
    "account_updated_at": _version_token(preflight.account_updated_at),
    "position_updated_at": _version_token(preflight.position_updated_at),
  }
  if not expected["rollout_snapshot_id"] or not expected["rollout_snapshot_hash"]:
    raise TradeApprovalChallengeError(
      "CONFIRMATION_CONTEXT_MISMATCH",
      "确认挑战缺少权威快照绑定，请重新获取预览",
    )
  if expected != current:
    raise TradeApprovalChallengeError(
      "ACCOUNT_SNAPSHOT_CHANGED",
      "预览后账户、持仓或完整对账快照已变化，请重新获取预览",
    )


def _validate_risk_binding(
  payload: dict[str, Any],
  preflight: ManualOrderPreflightData,
) -> None:
  try:
    requested_volume = int(payload.get("requested_volume"))
    final_volume = int(payload.get("final_volume"))
  except (TypeError, ValueError) as exc:
    raise TradeApprovalChallengeError(
      "CONFIRMATION_CONTEXT_MISMATCH",
      "确认挑战缺少规范化数量绑定",
    ) from exc
  expected = {
    "requested_volume": requested_volume,
    "final_volume": final_volume,
    "risk_action": str(payload.get("risk_action") or ""),
    "risk_reason_code": str(payload.get("risk_reason_code") or ""),
    "risk_reason_detail": str(payload.get("risk_reason_detail") or ""),
  }
  current = {
    "requested_volume": preflight.requested_volume,
    "final_volume": preflight.final_volume,
    "risk_action": preflight.risk_action,
    "risk_reason_code": preflight.risk_reason_code,
    "risk_reason_detail": preflight.risk_reason_detail,
  }
  if expected != current:
    raise TradeApprovalChallengeError(
      "RISK_DECISION_CHANGED",
      "确认时合法委托数量或风控决策已变化，请重新预览",
    )


def _validate_best_quote_binding(
  payload: dict[str, Any],
  preflight: ManualOrderPreflightData,
  *,
  now: datetime,
) -> None:
  try:
    preview_timestamp = datetime.fromisoformat(
      str(payload.get("preview_quote_timestamp") or "")
    )
  except (TypeError, ValueError) as exc:
    raise TradeApprovalChallengeError(
      "CONFIRMATION_CONTEXT_MISMATCH",
      "BEST 确认挑战缺少行情时间绑定",
    ) from exc
  preview_age = now - _local_naive(preview_timestamp)
  if preview_age < timedelta(0) or preview_age > _BEST_CONFIRMATION_MAX_AGE:
    raise TradeApprovalChallengeError(
      "BEST_PREVIEW_EXPIRED",
      "BEST 行情预览已超过 10 秒，请重新获取预览",
    )
  preview_reference = str(payload.get("preview_reference_price") or "")
  preview_fingerprint = str(payload.get("preview_quote_fingerprint") or "")
  if not preview_reference or not preview_fingerprint:
    raise TradeApprovalChallengeError(
      "CONFIRMATION_CONTEXT_MISMATCH",
      "BEST 确认挑战缺少行情指纹绑定",
    )
  if (
    preview_reference != _canonical_number(preflight.reference_price)
    or preview_fingerprint != preflight.quote_fingerprint
  ):
    raise TradeApprovalChallengeError(
      "BEST_QUOTE_CHANGED",
      "BEST 对手方价或行情快照已变化，请重新获取预览",
    )


async def _preflight(
  request: ManualOrderRequestData,
  *,
  db: Any = None,
  lock_mutable_rows: bool = False,
  risk_decision_id: Optional[str] = None,
) -> ManualOrderPreflightData:
  if db is None:
    async with AsyncSessionLocal() as owned_db:
      return await _preflight(
        request,
        db=owned_db,
        lock_mutable_rows=lock_mutable_rows,
        risk_decision_id=risk_decision_id,
      )

  rollout = None
  if request.execution_mode == "LIVE":
    try:
      rollout = await TradeCommandService(db)._require_manual_live_authorization(
        request.account_id,
        risk_reducing=request.side == "SELL",
      )
    except AgentUnavailableError as exc:
      raise TradeApprovalChallengeError(
        "LIVE_AUTHORIZATION_REJECTED",
        str(exc),
      ) from exc
  ticks = await latest_market_quote_cache.get_ticks([request.instrument_code])
  tick = next(
    (
      item
      for item in ticks
      if str(getattr(item, "stock_code", "")).strip().upper() == request.instrument_code
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
    raise TradeApprovalChallengeError("QUOTE_STALE", "行情已过期，已拒绝生成交易确认")

  instrument = await db.get(
    Instrument,
    request.instrument_code,
    with_for_update=lock_mutable_rows,
  )
  if instrument is None:
    raise TradeApprovalChallengeError(
      "INSTRUMENT_NOT_FOUND", "证券主数据不存在，已拒绝下单"
    )
  if getattr(instrument, "is_trading", None) is False:
    raise TradeApprovalChallengeError(
      "INSTRUMENT_NOT_TRADING", "证券主数据标记为不可交易"
    )

  account_statement = select(Account).where(
    Account.account_id == request.account_id,
    Account.account_type == AccountType.STOCK,
  )
  if lock_mutable_rows:
    account_statement = account_statement.with_for_update()
  account = (await db.execute(account_statement)).scalar_one_or_none()
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

  position_statement = select(Position).where(
    Position.account_id == request.account_id,
    Position.stock_code == request.instrument_code,
  )
  if lock_mutable_rows:
    position_statement = position_statement.with_for_update()
  position = (await db.execute(position_statement)).scalar_one_or_none()
  if request.side == "SELL" and position is None:
    raise TradeApprovalChallengeError(
      "POSITION_SNAPSHOT_UNAVAILABLE",
      "缺少持仓快照，已拒绝卖出",
    )
  if position is not None:
    position_age = _snapshot_age(getattr(position, "updated_at", None), now)
    if (
      position_age is None
      or position_age < timedelta(0)
      or position_age > _MAX_ACCOUNT_SNAPSHOT_AGE
    ):
      raise TradeApprovalChallengeError(
        "POSITION_SNAPSHOT_STALE",
        "持仓可卖量快照已过期，已拒绝下单",
      )

  market = MarketDataSnapshot.from_tick(tick)
  market.price_tick = float(getattr(instrument, "price_tick", None) or 0.01)
  market.limit_up = float(getattr(instrument, "up_stop_price", None) or 0) or None
  market.limit_down = float(getattr(instrument, "down_stop_price", None) or 0) or None
  rules = AShareMarketRules()

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
    {
      "available_cash": float(account.cash or 0),
      "total_asset": float(account.total_asset or 0),
    },
    {"available_volume": available_volume},
  )
  if draft.sized_volume <= 0 or draft.sized_volume > request.volume:
    raise TradeApprovalChallengeError(
      "ORDER_SIZING_REJECTED",
      "请求数量无法生成正数且不增加风险的 A 股合法委托",
    )
  order_request = OrderRequest(
    instrument_code=request.instrument_code,
    order_type=domain_side,
    price_type=domain_price_type,
    volume=draft.sized_volume,
    price=price,
    metadata={"bucket": "manual", "origin": "IOS_MANUAL"},
  )

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
  setattr(
    market,
    "min_limit_sell_order_volume"
    if request.side == "SELL"
    else "min_limit_order_volume",
    min_volume,
  )
  setattr(
    market,
    "max_limit_sell_order_volume"
    if request.side == "SELL"
    else "max_limit_order_volume",
    max_volume,
  )

  decision = await TradingRiskChecker(
    rules,
    trading_time_service=_trading_time_service,
    strict_market_data=True,
    strict_limit_data=True,
    enforce_trading_hours=True,
    market=request.instrument_code.rsplit(".", 1)[1],
  ).evaluate_order(
    order_request,
    account={
      "available_cash": float(account.cash or 0),
      "cash": float(account.cash or 0),
      "total_asset": float(account.total_asset or 0),
    },
    position=(
      {
        "available_volume": available_volume,
        "long_volume": int(getattr(position, "volume", 0) or 0),
        "market_value": float(getattr(position, "market_value", 0) or 0),
      }
      if position is not None
      else None
    ),
    market_data=market,
    current_time=now,
  )
  decision.risk_decision_id = risk_decision_id or decision.risk_decision_id
  if not decision.allowed:
    raise TradeApprovalChallengeError(
      decision.reason_code.upper(),
      decision.reason_detail or "手动委托未通过统一交易风控",
    )
  final_volume = int(decision.final_volume or 0)
  if final_volume <= 0 or final_volume > request.volume:
    raise TradeApprovalChallengeError(
      "ORDER_RISK_REJECTED",
      "风控未生成正数且不增加风险的最终委托数量",
    )
  if final_volume < request.volume and decision.action != RiskAction.CAP:
    decision.action = RiskAction.CAP
    decision.reason_code = (
      str(draft.size_reason_codes[0]).upper()
      if draft.size_reason_codes
      else "ORDER_SIZER_CAP"
    )
    decision.reason_detail = (
      f"请求 {request.volume} 股，按 A 股整手、零股与可卖量规则"
      f"规范化为 {final_volume} 股"
    )
  decision.original_volume = request.volume
  decision.original_amount = float(price) * float(request.volume)
  decision.final_volume = final_volume
  decision.final_amount = float(price) * float(final_volume)

  estimated_amount = float(price) * float(final_volume)
  available_cash = float(account.cash or 0)
  if request.execution_mode == "PAPER":
    snapshot_id, snapshot_hash = _paper_snapshot_binding(account, position)
    mode_warnings = [
      "当前为 PAPER 演练：不会向券商发送真实委托",
      "PAPER 结果不代表真实盘口排队、成交价格或真实费用",
    ]
  else:
    if rollout is None:
      raise TradeApprovalChallengeError(
        "LIVE_AUTHORIZATION_REJECTED",
        "账户执行控制快照不存在",
      )
    snapshot_id = str(rollout.last_snapshot_id or "")
    snapshot_hash = str(rollout.last_snapshot_hash or "")
    mode_warnings = [
      "确认只会将命令排队，不表示券商受理、委托成功或成交",
      "最终状态只能以 QMT Agent 上报并由 Engine 收敛的券商回报为准",
    ]

  return ManualOrderPreflightData(
    quote_timestamp=_aware_local(quote_timestamp),
    quote_fingerprint=_quote_fingerprint(
      instrument_code=request.instrument_code,
      tick=tick,
      market=market,
    ),
    reference_price=float(price),
    requested_volume=request.volume,
    final_volume=final_volume,
    estimated_amount=estimated_amount,
    estimated_fees=None,
    available_cash=available_cash,
    available_volume=available_volume if request.side == "SELL" else None,
    rollout_snapshot_id=snapshot_id,
    rollout_snapshot_hash=snapshot_hash,
    account_updated_at=account.updated_at,
    position_updated_at=(position.updated_at if position is not None else None),
    risk_decision_id=decision.risk_decision_id,
    risk_action=decision.action.value,
    risk_reason_code=decision.reason_code.upper(),
    risk_reason_detail=decision.reason_detail,
    warnings=[
      "风控已使用保守手续费缓冲校验购买力；预览不展示非权威费用报价",
      "确认时会锁定并复核设备会话、账户、持仓和风控快照",
      *mode_warnings,
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
    challenge_id = str(uuid.uuid4())
    risk_decision_id = _stable_risk_decision_id(challenge_id)
    preflight = await _preflight(
      request,
      risk_decision_id=risk_decision_id,
    )
    if preflight.risk_decision_id != risk_decision_id:
      preflight = replace(preflight, risk_decision_id=risk_decision_id)
    payload = _challenge_payload(request, preflight)
    raw_token = secrets.token_urlsafe(48)
    now = time_utils.now()
    challenge = TradeConfirmationChallenge(
      id=challenge_id,
      action=MANUAL_ORDER_ACTION,
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

    async with AsyncSessionLocal() as db:
      try:
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
        payload = dict(challenge.payload or {})
        locked_request = ManualOrderRequestData.from_payload(payload)
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

        risk_decision_id = str(payload.get("risk_decision_id") or "")
        if risk_decision_id != _stable_risk_decision_id(challenge.id):
          raise TradeApprovalChallengeError(
            "CONFIRMATION_CONTEXT_MISMATCH",
            "确认挑战缺少稳定风控决策绑定",
          )

        # Fixed lock order: challenge -> auth user/session -> rollout -> account
        # -> position -> pending/outbox. Revocation, permission/account removal,
        # and kill therefore linearize before or after this atomic enqueue.
        try:
          current_principal = await AuthService(db).lock_and_validate_session(
            principal,
            required_permission="trade:manual",
            account_id=request.account_id,
          )
        except AuthError as exc:
          raise TradeApprovalChallengeError(exc.code, exc.message) from exc
        preflight = await _preflight(
          request,
          db=db,
          lock_mutable_rows=True,
          risk_decision_id=risk_decision_id,
        )
        _validate_snapshot_binding(payload, preflight)
        _validate_risk_binding(payload, preflight)
        if request.price_type == "BEST":
          _validate_best_quote_binding(payload, preflight, now=now)

        challenge.consumed_at = now
        queued = await TradeCommandService(db).enqueue_order(
          user_id=current_principal.user_id,
          account_id=request.account_id,
          instrument_code=request.instrument_code,
          side=request.side,
          order_type=(
            "FIX_PRICE" if request.price_type == "LIMIT" else "MARKET_PEER_PRICE_FIRST"
          ),
          limit_price=Decimal(str(request.limit_price or 0)),
          volume=preflight.final_volume,
          strategy_name="manual-mobile",
          order_remark="QuantX iOS 手动委托",
          trace_id=challenge.id,
          idempotency_key=_command_idempotency_key(challenge.id, request),
          execution_mode=request.execution_mode.lower(),
          bucket="manual",
          manual_live=request.execution_mode == "LIVE",
          risk_decision_id=risk_decision_id,
          reason_tags=["MOBILE_MANUAL_ORDER", preflight.risk_reason_code],
          commit_transaction=False,
          request_metadata={
            "origin": "IOS_MANUAL",
            "challenge_id": challenge.id,
            "payload_fingerprint": challenge.payload_fingerprint,
            "quote_timestamp": preflight.quote_timestamp.isoformat(),
            "quote_fingerprint": preflight.quote_fingerprint,
            "reference_price": _canonical_number(preflight.reference_price),
            "requested_volume": preflight.requested_volume,
            "final_volume": preflight.final_volume,
            "rollout_snapshot_id": preflight.rollout_snapshot_id,
            "rollout_snapshot_hash": preflight.rollout_snapshot_hash,
            "account_updated_at": _version_token(preflight.account_updated_at),
            "position_updated_at": _version_token(preflight.position_updated_at),
            "risk_decision_id": risk_decision_id,
            "risk_action": preflight.risk_action,
            "risk_reason_code": preflight.risk_reason_code,
            "risk_reason_detail": preflight.risk_reason_detail,
          },
        )
        challenge.result_reference = {
          "client_order_id": queued.client_order_id,
          "message_id": queued.message_id,
          "status": queued.status,
        }
        await db.commit()
      except Exception:
        await db.rollback()
        raise
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
