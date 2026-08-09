"""Pure domain helpers for the A-share intraday T assistant."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from quantx_domain.trading.exit_plan import (  # noqa: F401
  TradingCostPolicy,
  TrailingProfitPolicy,
  calculate_trailing_floor_pct,
  estimate_net_profit_pct,
)


@dataclass(frozen=True)
class TickSample:
  timestamp_ms: int
  price: float
  bid_price: float = 0.0
  ask_price: float = 0.0
  cumulative_amount: float = 0.0
  cumulative_volume: float = 0.0


@dataclass(frozen=True)
class SignalPolicy:
  lookback_seconds: int = 300
  stabilization_seconds: int = 15
  pullback_threshold_pct: float = 0.8
  rebound_threshold_pct: float = 0.2
  max_spread_ticks: int = 3
  price_tick: float = 0.01


@dataclass(frozen=True)
class IntradayTSignal:
  triggered: bool
  reason: str
  signal_price: float
  window_high: float
  window_low: float
  pullback_pct: float
  rebound_pct: float
  vwap: float
  spread_ticks: float


@dataclass(frozen=True)
class TTradeSizingResult:
  """Lot-aligned positive-T entry size derived from a notional budget."""

  volume: int
  estimated_amount: float
  reason: str


def calculate_target_trade_volume(
  *,
  entry_price: float,
  available_volume: int,
  target_amount: float = 10_000.0,
  max_amount: float = 12_000.0,
  lot_size: int = 100,
) -> TTradeSizingResult:
  """Size a positive-T buy near the target amount without exceeding hard caps.

  One lot is allowed above the target amount only when it remains within the
  hard amount cap. Yesterday's sellable inventory is always the share ceiling.
  """

  if entry_price <= 0:
    return TTradeSizingResult(0, 0.0, "ENTRY_PRICE_UNAVAILABLE")
  if lot_size <= 0 or target_amount <= 0 or max_amount < target_amount:
    return TTradeSizingResult(0, 0.0, "INVALID_AMOUNT_POLICY")

  available_lots = max(0, int(available_volume)) // lot_size
  if available_lots <= 0:
    return TTradeSizingResult(0, 0.0, "AVAILABLE_VOLUME_BELOW_ONE_LOT")

  one_lot_amount = entry_price * lot_size
  max_lots = int(max_amount // one_lot_amount)
  if max_lots <= 0:
    return TTradeSizingResult(0, 0.0, "ONE_LOT_EXCEEDS_MAX_AMOUNT")

  target_lots = int(target_amount // one_lot_amount)
  desired_lots = max(1, target_lots)
  selected_lots = min(desired_lots, available_lots, max_lots)
  volume = selected_lots * lot_size
  estimated_amount = volume * entry_price
  if selected_lots < desired_lots and selected_lots == available_lots:
    reason = "LIMITED_BY_AVAILABLE_VOLUME"
  elif target_lots <= 0:
    reason = "MINIMUM_ONE_LOT"
  else:
    reason = "TARGET_AMOUNT"
  return TTradeSizingResult(volume, estimated_amount, reason)


def evaluate_intraday_t_signal(
  samples: Iterable[TickSample],
  *,
  policy: Optional[SignalPolicy] = None,
) -> IntradayTSignal:
  """Detect a pullback followed by a stabilized rebound in the recent tick window."""

  config = policy or SignalPolicy()
  ordered = sorted(
    (item for item in samples if item.price > 0), key=lambda item: item.timestamp_ms
  )
  if len(ordered) < 3:
    return _empty_signal("INSUFFICIENT_TICKS", ordered)

  latest = ordered[-1]
  cutoff = latest.timestamp_ms - config.lookback_seconds * 1000
  window = [item for item in ordered if item.timestamp_ms >= cutoff]
  if len(window) < 3:
    return _empty_signal("INSUFFICIENT_WINDOW", window)

  peak_sample = window[0]
  high_sample = window[0]
  low_sample = window[0]
  best_pullback_pct = 0.0
  for item in window[1:]:
    pullback = (
      (peak_sample.price - item.price) / peak_sample.price * 100.0
      if peak_sample.price > 0
      else 0.0
    )
    if pullback > best_pullback_pct:
      best_pullback_pct = pullback
      high_sample = peak_sample
      low_sample = item
    if item.price > peak_sample.price:
      peak_sample = item

  high = high_sample.price
  low = low_sample.price
  pullback_pct = (high - low) / high * 100.0 if high > 0 else 0.0
  rebound_pct = (latest.price - low) / low * 100.0 if low > 0 else 0.0
  tick_size = max(config.price_tick, 1e-8)
  spread_ticks = (
    max(0.0, latest.ask_price - latest.bid_price) / tick_size
    if latest.ask_price > 0 and latest.bid_price > 0
    else 0.0
  )
  vwap = (
    latest.cumulative_amount / latest.cumulative_volume
    if latest.cumulative_amount > 0 and latest.cumulative_volume > 0
    else 0.0
  )

  if latest.ask_price <= 0 or latest.bid_price <= 0:
    reason = "ORDER_BOOK_UNAVAILABLE"
  elif pullback_pct < config.pullback_threshold_pct:
    reason = "PULLBACK_TOO_SMALL"
  elif rebound_pct < config.rebound_threshold_pct:
    reason = "REBOUND_NOT_CONFIRMED"
  elif latest.timestamp_ms - low_sample.timestamp_ms < config.stabilization_seconds * 1000:
    reason = "LOW_NOT_STABILIZED"
  elif spread_ticks > config.max_spread_ticks:
    reason = "SPREAD_TOO_WIDE"
  elif vwap > 0 and latest.price > vwap:
    reason = "PRICE_ABOVE_VWAP"
  else:
    reason = "PULLBACK_REBOUND_CONFIRMED"

  return IntradayTSignal(
    triggered=reason == "PULLBACK_REBOUND_CONFIRMED",
    reason=reason,
    signal_price=latest.price,
    window_high=high,
    window_low=low,
    pullback_pct=pullback_pct,
    rebound_pct=rebound_pct,
    vwap=vwap,
    spread_ticks=spread_ticks,
  )


def _empty_signal(reason: str, samples: list[TickSample]) -> IntradayTSignal:
  latest_price = samples[-1].price if samples else 0.0
  return IntradayTSignal(
    triggered=False,
    reason=reason,
    signal_price=latest_price,
    window_high=latest_price,
    window_low=latest_price,
    pullback_pct=0.0,
    rebound_pct=0.0,
    vwap=0.0,
    spread_ticks=0.0,
  )
