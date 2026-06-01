"""Convert strategy trade intent into legal A-share order sizes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core.brokers.base import OrderType
from core.strategies.base import TradeIntent

from .market_rules import AShareMarketRules


@dataclass
class OrderDraft:
  """OrderSizer output before post-order risk checks."""

  draft_id: str
  intent_id: str
  side: OrderType
  instrument_code: str
  bucket: str
  limit_price: float
  raw_target_amount: Optional[float]
  raw_target_volume: Optional[int]
  sized_amount: float
  sized_volume: int
  size_reason_codes: list[str] = field(default_factory=list)
  metadata: Dict[str, Any] = field(default_factory=dict)


class OrderSizer:
  def __init__(self, rules: Optional[AShareMarketRules] = None) -> None:
    self.rules = rules or AShareMarketRules()

  def draft_intent(
    self,
    intent: TradeIntent,
    order_type: OrderType,
    price: float,
    account: Dict[str, Any],
    position: Optional[Dict[str, Any]] = None,
  ) -> OrderDraft:
    metadata = dict(intent.metadata or {})
    requested_volume = intent.target_volume or _optional_int(
      metadata.get("requested_volume", metadata.get("volume"))
    )
    raw_target_amount = self._raw_target_amount(intent, price, account, metadata)
    raw_target_volume = requested_volume
    reason_codes: list[str] = []

    if price <= 0:
      reason_codes.append("INVALID_PRICE")
      sized_volume = 0
    elif order_type == OrderType.BUY:
      if raw_target_volume is None or raw_target_volume <= 0:
        raw_target_volume = (
          int(float(raw_target_amount) // float(price))
          if raw_target_amount and raw_target_amount > 0
          else 0
        )
      sized_volume = self.rules.normalize_buy_volume(raw_target_volume)
      if sized_volume != raw_target_volume:
        reason_codes.append("BUY_LOT_NORMALIZED")
    elif order_type == OrderType.SELL:
      available = int((position or {}).get("available_volume", 0) or 0)
      if (
        metadata.get("sell_all")
        or metadata.get("close_position")
        or raw_target_volume is None
        or raw_target_volume <= 0
      ):
        raw_target_volume = available
        reason_codes.append("SELL_ALL_AVAILABLE")
      sized_volume = self.rules.normalize_sell_volume(raw_target_volume, available)
      if sized_volume != raw_target_volume:
        reason_codes.append("SELL_VOLUME_NORMALIZED_OR_CAPPED")
    else:
      sized_volume = 0
      reason_codes.append("UNSUPPORTED_ORDER_TYPE")

    if sized_volume <= 0:
      reason_codes.append("ZERO_SIZED_VOLUME")

    return OrderDraft(
      draft_id=str(uuid.uuid4()),
      intent_id=intent.intent_id,
      side=order_type,
      instrument_code=intent.instrument_code,
      bucket=intent.bucket,
      limit_price=price,
      raw_target_amount=raw_target_amount,
      raw_target_volume=raw_target_volume,
      sized_amount=float(price) * float(sized_volume),
      sized_volume=sized_volume,
      size_reason_codes=reason_codes,
      metadata=metadata,
    )

  def size_intent(
    self,
    intent: TradeIntent,
    order_type: OrderType,
    price: float,
    account: Dict[str, Any],
    position: Optional[Dict[str, Any]] = None,
  ) -> int:
    if price <= 0:
      return 0

    metadata = dict(intent.metadata or {})
    requested_volume = int(
      intent.target_volume
      or metadata.get("requested_volume", metadata.get("volume", 0))
      or 0
    )

    if order_type == OrderType.BUY:
      raw_volume = requested_volume
      if raw_volume <= 0:
        raw_volume = self._target_buy_volume(intent, price, account, metadata)
      return self.rules.normalize_buy_volume(raw_volume)

    if order_type == OrderType.SELL:
      available = int((position or {}).get("available_volume", 0) or 0)
      if metadata.get("sell_all") or metadata.get("close_position") or requested_volume <= 0:
        requested_volume = available
      return self.rules.normalize_sell_volume(requested_volume, available)

    return 0

  def _target_buy_volume(
    self,
    intent: TradeIntent,
    price: float,
    account: Dict[str, Any],
    metadata: Dict[str, Any],
  ) -> int:
    total_asset = float(
      account.get("total_asset")
      or account.get("cash_total")
      or account.get("available_cash")
      or 0.0
    )

    target_amount = _first_number(
      intent.target_amount,
      metadata.get("target_amount"),
      metadata.get("budget"),
    )
    if target_amount is None:
      target_pct = _first_number(
        intent.target_position_pct,
        metadata.get("target_position_pct"),
        metadata.get("allocation_pct"),
      )
      if target_pct is not None and total_asset > 0:
        target_amount = total_asset * target_pct

    if target_amount is None or target_amount <= 0:
      return 0
    return int(float(target_amount) // float(price))

  def _raw_target_amount(
    self,
    intent: TradeIntent,
    price: float,
    account: Dict[str, Any],
    metadata: Dict[str, Any],
  ) -> Optional[float]:
    total_asset = float(
      account.get("total_asset")
      or account.get("cash_total")
      or account.get("available_cash")
      or 0.0
    )
    target_amount = _first_number(
      intent.target_amount,
      metadata.get("target_amount"),
      metadata.get("budget"),
    )
    if target_amount is not None:
      return target_amount
    target_pct = _first_number(
      intent.target_position_pct,
      metadata.get("target_position_pct"),
      metadata.get("allocation_pct"),
    )
    if target_pct is not None and total_asset > 0:
      return total_asset * target_pct
    requested_volume = intent.target_volume or _optional_int(
      metadata.get("requested_volume", metadata.get("volume"))
    )
    if requested_volume and price > 0:
      return requested_volume * price
    return None


def _first_number(*values: Any) -> Optional[float]:
  for value in values:
    if value is None:
      continue
    try:
      return float(value)
    except (TypeError, ValueError):
      continue
  return None


def _optional_int(value: Any) -> Optional[int]:
  if value is None:
    return None
  try:
    return int(value)
  except (TypeError, ValueError):
    return None
