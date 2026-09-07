"""Convert strategy trade intent into legal A-share order sizes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Optional

from quantx_domain.brokers.base import OrderType
from quantx_domain.strategies.base import TradeIntent

from .exit_plan import estimate_buy_fee_cny
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
    *,
    allocated_amount_cap: Decimal | float | int | None = None,
  ) -> OrderDraft:
    allocation_cap = None
    if allocated_amount_cap is not None:
      if isinstance(allocated_amount_cap, bool) or not isinstance(
        allocated_amount_cap, (Decimal, float, int)
      ):
        raise ValueError("ALLOCATION_AMOUNT_CAP_INVALID")
      allocation_cap = Decimal(str(allocated_amount_cap))
      if not allocation_cap.is_finite() or allocation_cap < 0:
        raise ValueError("ALLOCATION_AMOUNT_CAP_INVALID")
      if order_type is not OrderType.BUY:
        raise ValueError("ALLOCATION_AMOUNT_CAP_REQUIRES_BUY")
      if (
        isinstance(price, bool)
        or not isinstance(price, (Decimal, float, int))
        or not Decimal(str(price)).is_finite()
        or price <= 0
      ):
        raise ValueError("ALLOCATION_AMOUNT_CAP_PRICE_INVALID")
    metadata = dict(intent.metadata or {})
    requested_volume = intent.target_volume or _optional_int(
      metadata.get("requested_volume", metadata.get("volume"))
    )
    raw_target_amount = self._raw_target_amount(intent, price, account, metadata)
    raw_target_volume = requested_volume
    reason_codes: list[str] = []
    liquidity_cap = _optional_positive_number(metadata.get("liquidity_cap_amount"))
    if (
      order_type == OrderType.BUY
      and liquidity_cap is not None
      and raw_target_amount is not None
      and raw_target_amount > liquidity_cap
    ):
      metadata["uncapped_target_amount"] = raw_target_amount
      raw_target_amount = liquidity_cap
      raw_target_volume = (
        min(raw_target_volume, int(liquidity_cap // price))
        if raw_target_volume and price > 0
        else None
      )
      reason_codes.append("LIQUIDITY_PARTICIPATION_CAP")

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
      if str(metadata.get("t_trade_role") or "").lower() == "entry":
        # Positive T must be exit-capable using old shares under T+1. This is
        # an execution fact supplied by the portfolio, never strategy metadata.
        holding = dict(position or {})
        capacity = max(
          0,
          int(
            holding.get(
              "t_trade_exit_capacity",
              max(
                0,
                int(holding.get("available_volume", 0))
                - int(holding.get("locked_core_available_volume", 0)),
              ),
            )
          ),
        )
        capped = min(sized_volume, self.rules.normalize_buy_volume(capacity))
        if capped != sized_volume:
          reason_codes.append("T_TRADE_OLD_INVENTORY_CAP")
        sized_volume = capped
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

    if allocation_cap is not None:
      # Allocation is a cash ceiling including the same conservative buy fees
      # used by account capacity. Preserve the intent and its requested draft.
      def cash_required(volume: int) -> Decimal:
        return Decimal(str(price)) * volume + Decimal(
          str(estimate_buy_fee_cny(price=price, volume=volume))
        )

      low, high = 0, sized_volume // self.rules.lot_size
      while low < high:
        middle = (low + high + 1) // 2
        if cash_required(middle * self.rules.lot_size) <= allocation_cap:
          low = middle
        else:
          high = middle - 1
      capped_volume = low * self.rules.lot_size
      if capped_volume < sized_volume:
        reason_codes.append("ALLOCATION_AMOUNT_CAP")
        if capped_volume == 0:
          reason_codes.append("MIN_LOT_EXCEEDS_ALLOCATION_BUDGET")
      sized_volume = capped_volume
      metadata.update(
        allocated_amount_cap=str(allocation_cap),
        allocation_estimated_fee_cny=str(
          estimate_buy_fee_cny(
            price=price,
            volume=sized_volume,
          )
        ),
        allocation_cash_required=str(cash_required(sized_volume)),
      )

    if sized_volume <= 0:
      if (
        order_type == OrderType.BUY
        and raw_target_amount is not None
        and raw_target_amount > 0
        and price * self.rules.lot_size > raw_target_amount
      ):
        reason_codes.append("MIN_LOT_EXCEEDS_RISK_BUDGET")
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
    return self.draft_intent(intent, order_type, price, account, position).sized_volume

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


def _optional_positive_number(value: Any) -> Optional[float]:
  try:
    parsed = float(value)
  except (TypeError, ValueError):
    return None
  return parsed if parsed > 0 else None
