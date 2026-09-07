"""Frozen order-lifecycle policies for positive-T ENTRY and EXIT orders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")


class TOrderPolicyDecision(str, Enum):
  ALLOW_NEW = "ALLOW_NEW"
  ALLOW_REPLACE = "ALLOW_REPLACE"
  WAIT_AUTHORITATIVE_TERMINAL = "WAIT_AUTHORITATIVE_TERMINAL"
  EXPIRED = "EXPIRED"
  CUTOFF = "CUTOFF"
  REPLACE_LIMIT_REACHED = "REPLACE_LIMIT_REACHED"


@dataclass(frozen=True)
class TOrderPolicyResult:
  decision: TOrderPolicyDecision
  reason_code: str
  limit_price: Decimal | None = None
  remaining_volume: int = 0

  @property
  def allowed(self) -> bool:
    return self.decision in {
      TOrderPolicyDecision.ALLOW_NEW,
      TOrderPolicyDecision.ALLOW_REPLACE,
    }


def _positive_decimal(value: Any, *, reason: str) -> Decimal:
  result = Decimal(str(value or 0))
  if not result.is_finite() or result <= 0:
    raise ValueError(reason)
  return result


def _round_to_tick(value: Decimal, tick: Decimal, *, upward: bool) -> Decimal:
  rounding = ROUND_CEILING if upward else ROUND_FLOOR
  return (value / tick).to_integral_value(rounding=rounding) * tick


def _exchange_time(value: datetime) -> time:
  if value.tzinfo is None:
    return value.time()
  return value.astimezone(_SHANGHAI).time().replace(tzinfo=None)


def _elapsed_seconds(later: datetime, earlier: datetime) -> float:
  if later.tzinfo is None and earlier.tzinfo is None:
    return max(0.0, (later - earlier).total_seconds())
  normalized_later = (
    later.replace(tzinfo=_SHANGHAI) if later.tzinfo is None else later
  ).astimezone(timezone.utc)
  normalized_earlier = (
    earlier.replace(tzinfo=_SHANGHAI) if earlier.tzinfo is None else earlier
  ).astimezone(timezone.utc)
  return max(0.0, (normalized_later - normalized_earlier).total_seconds())


@dataclass(frozen=True)
class _TOrderPolicy:
  version: str
  side: str
  max_slippage_bps: int
  order_ttl_seconds: int
  max_replace_count: int
  total_ttl_seconds: int

  def protected_limit_price(
    self,
    *,
    reference_price: Any,
    price_tick: Any,
    limit_up: Any | None = None,
    limit_down: Any | None = None,
    cage_upper: Any | None = None,
    cage_lower: Any | None = None,
  ) -> Decimal:
    reference = _positive_decimal(reference_price, reason="T_ORDER_REFERENCE_PRICE_INVALID")
    tick = _positive_decimal(price_tick, reason="T_ORDER_PRICE_TICK_INVALID")
    bps = Decimal(self.max_slippage_bps) / Decimal(10_000)
    if self.side == "BUY":
      value = reference * (Decimal(1) + bps)
      ceilings = [
        Decimal(str(item))
        for item in (limit_up, cage_upper)
        if item is not None and Decimal(str(item or 0)) > 0
      ]
      if ceilings:
        value = min(value, *ceilings)
      return _round_to_tick(value, tick, upward=False)
    value = reference * (Decimal(1) - bps)
    floors = [
      Decimal(str(item))
      for item in (limit_down, cage_lower)
      if item is not None and Decimal(str(item or 0)) > 0
    ]
    if floors:
      value = max(value, *floors)
    return _round_to_tick(value, tick, upward=True)

  def decide_new(
    self,
    *,
    now: datetime,
    reference_price: Any,
    price_tick: Any,
    limit_up: Any | None = None,
    limit_down: Any | None = None,
    cage_upper: Any | None = None,
    cage_lower: Any | None = None,
  ) -> TOrderPolicyResult:
    if self.side == "BUY" and _exchange_time(now) >= time(14, 50):
      return TOrderPolicyResult(
        TOrderPolicyDecision.CUTOFF,
        "T_ENTRY_CUTOFF_REACHED",
      )
    return TOrderPolicyResult(
      TOrderPolicyDecision.ALLOW_NEW,
      f"{self.version}_NEW_ALLOWED",
      self.protected_limit_price(
        reference_price=reference_price,
        price_tick=price_tick,
        limit_up=limit_up,
        limit_down=limit_down,
        cage_upper=cage_upper,
        cage_lower=cage_lower,
      ),
    )

  def decide_replace(
    self,
    *,
    now: datetime,
    original_created_at: datetime,
    replace_count: int,
    requested_volume: int,
    authoritative_filled_volume: int,
    prior_order_authoritative_terminal: bool,
    result_unknown: bool,
    cancel_unconfirmed: bool,
    reference_price: Any,
    price_tick: Any,
    limit_up: Any | None = None,
    limit_down: Any | None = None,
    cage_upper: Any | None = None,
    cage_lower: Any | None = None,
    prior_order_created_at: datetime | None = None,
  ) -> TOrderPolicyResult:
    if result_unknown:
      return TOrderPolicyResult(
        TOrderPolicyDecision.WAIT_AUTHORITATIVE_TERMINAL,
        "ORDER_RESULT_UNKNOWN",
      )
    if cancel_unconfirmed or not prior_order_authoritative_terminal:
      return TOrderPolicyResult(
        TOrderPolicyDecision.WAIT_AUTHORITATIVE_TERMINAL,
        "ORDER_CANCEL_UNCONFIRMED",
      )
    elapsed = _elapsed_seconds(now, original_created_at)
    if elapsed >= self.total_ttl_seconds:
      return TOrderPolicyResult(
        TOrderPolicyDecision.EXPIRED,
        "T_ENTRY_ORDER_EXPIRED" if self.side == "BUY" else "T_EXIT_ORDER_EXPIRED",
      )
    if self.side == "BUY" and _exchange_time(now) >= time(14, 50):
      return TOrderPolicyResult(
        TOrderPolicyDecision.CUTOFF,
        "T_ENTRY_CUTOFF_REACHED",
      )
    if max(0, int(replace_count)) >= self.max_replace_count:
      return TOrderPolicyResult(
        TOrderPolicyDecision.REPLACE_LIMIT_REACHED,
        "ORDER_REPLACE_LIMIT_REACHED",
      )
    prior_created = prior_order_created_at or original_created_at
    if _elapsed_seconds(now, prior_created) < self.order_ttl_seconds:
      return TOrderPolicyResult(
        TOrderPolicyDecision.WAIT_AUTHORITATIVE_TERMINAL,
        "T_ENTRY_ORDER_ACTIVE" if self.side == "BUY" else "T_EXIT_ORDER_ACTIVE",
      )
    remaining = max(
      0,
      int(requested_volume) - max(0, int(authoritative_filled_volume)),
    )
    if remaining <= 0:
      return TOrderPolicyResult(
        TOrderPolicyDecision.EXPIRED,
        "T_ENTRY_ORDER_EXPIRED" if self.side == "BUY" else "T_EXIT_ORDER_EXPIRED",
      )
    return TOrderPolicyResult(
      TOrderPolicyDecision.ALLOW_REPLACE,
      f"{self.version}_REPLACE_ALLOWED",
      self.protected_limit_price(
        reference_price=reference_price,
        price_tick=price_tick,
        limit_up=limit_up,
        limit_down=limit_down,
        cage_upper=cage_upper,
        cage_lower=cage_lower,
      ),
      remaining,
    )


@dataclass(frozen=True)
class TEntryOrderPolicy(_TOrderPolicy):
  version: str = "TEntryOrderPolicy.v1"
  side: str = "BUY"
  max_slippage_bps: int = 30
  order_ttl_seconds: int = 30
  max_replace_count: int = 1
  total_ttl_seconds: int = 60

  def __post_init__(self) -> None:
    if (
      self.version,
      self.side,
      self.max_slippage_bps,
      self.order_ttl_seconds,
      self.max_replace_count,
      self.total_ttl_seconds,
    ) != ("TEntryOrderPolicy.v1", "BUY", 30, 30, 1, 60):
      raise ValueError("T_ENTRY_ORDER_POLICY_V1_IMMUTABLE")


@dataclass(frozen=True)
class TExitOrderPolicy(_TOrderPolicy):
  version: str = "TExitOrderPolicy.v1"
  side: str = "SELL"
  max_slippage_bps: int = 30
  order_ttl_seconds: int = 30
  max_replace_count: int = 2
  total_ttl_seconds: int = 90

  def __post_init__(self) -> None:
    if (
      self.version,
      self.side,
      self.max_slippage_bps,
      self.order_ttl_seconds,
      self.max_replace_count,
      self.total_ttl_seconds,
    ) != ("TExitOrderPolicy.v1", "SELL", 30, 30, 2, 90):
      raise ValueError("T_EXIT_ORDER_POLICY_V1_IMMUTABLE")


__all__ = [
  "TEntryOrderPolicy",
  "TExitOrderPolicy",
  "TOrderPolicyDecision",
  "TOrderPolicyResult",
]
