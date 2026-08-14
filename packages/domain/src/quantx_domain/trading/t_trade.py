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
  momentum_enabled: bool = True
  momentum_window_seconds: int = 60
  momentum_min_rise_pct: float = 0.8
  momentum_min_move_seconds: int = 15
  momentum_baseline_seconds: int = 300
  momentum_min_amount_velocity_ratio: float = 2.0
  momentum_min_vwap_premium_pct: float = 2.0
  momentum_max_vwap_premium_pct: float = 3.5
  momentum_high_tolerance_ticks: int = 1
  momentum_max_spread_ticks: int = 10
  momentum_max_spread_pct: float = 0.3


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
  signal_type: str = "NONE"
  momentum_rise_pct: float = 0.0
  momentum_move_seconds: float = 0.0
  momentum_amount_velocity_ratio: float = 0.0
  vwap_premium_pct: float = 0.0
  spread_pct: float = 0.0
  momentum_baseline_coverage_seconds: float = 0.0


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
  """Detect either a mean-reversion entry or an early momentum acceleration.

  The momentum branch is deliberately causal: it only compares the latest
  short-window move with turnover observed before that move.  It is a positive-T
  BUY candidate, not a reverse-T sell signal.  A bounded premium above session
  VWAP separates established intraday strength from both ordinary noise and a
  late vertical chase.
  """

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
  spread_pct = (
    max(0.0, latest.ask_price - latest.bid_price) / latest.price * 100.0
    if latest.price > 0 and latest.ask_price > 0 and latest.bid_price > 0
    else 0.0
  )
  vwap_premium_pct = (latest.price / vwap - 1.0) * 100.0 if vwap > 0 else 0.0

  if latest.ask_price <= 0 or latest.bid_price <= 0:
    pullback_reason = "ORDER_BOOK_UNAVAILABLE"
  elif pullback_pct < config.pullback_threshold_pct:
    pullback_reason = "PULLBACK_TOO_SMALL"
  elif rebound_pct < config.rebound_threshold_pct:
    pullback_reason = "REBOUND_NOT_CONFIRMED"
  elif (
    latest.timestamp_ms - low_sample.timestamp_ms < config.stabilization_seconds * 1000
  ):
    pullback_reason = "LOW_NOT_STABILIZED"
  elif spread_ticks > config.max_spread_ticks:
    pullback_reason = "SPREAD_TOO_WIDE"
  elif vwap > 0 and latest.price > vwap:
    pullback_reason = "PRICE_ABOVE_VWAP"
  else:
    pullback_reason = "PULLBACK_REBOUND_CONFIRMED"

  if pullback_reason == "PULLBACK_REBOUND_CONFIRMED":
    return IntradayTSignal(
      triggered=True,
      reason=pullback_reason,
      signal_price=latest.price,
      window_high=high,
      window_low=low,
      pullback_pct=pullback_pct,
      rebound_pct=rebound_pct,
      vwap=vwap,
      spread_ticks=spread_ticks,
      signal_type="PULLBACK_REBOUND",
      vwap_premium_pct=vwap_premium_pct,
      spread_pct=spread_pct,
    )

  momentum = _evaluate_momentum_signal(
    ordered,
    policy=config,
    vwap=vwap,
    spread_ticks=spread_ticks,
    spread_pct=spread_pct,
    vwap_premium_pct=vwap_premium_pct,
  )
  if momentum.triggered:
    return momentum

  # Below VWAP, the pullback branch is the relevant audit explanation.  Above
  # VWAP, expose the momentum rejection so an operator can distinguish a weak
  # move from a late/illiquid chase.
  reason = (
    momentum.reason
    if config.momentum_enabled and vwap > 0 and latest.price > vwap
    else pullback_reason
  )

  return IntradayTSignal(
    triggered=False,
    reason=reason,
    signal_price=latest.price,
    window_high=high,
    window_low=low,
    pullback_pct=pullback_pct,
    rebound_pct=rebound_pct,
    vwap=vwap,
    spread_ticks=spread_ticks,
    signal_type="NONE",
    momentum_rise_pct=momentum.momentum_rise_pct,
    momentum_move_seconds=momentum.momentum_move_seconds,
    momentum_amount_velocity_ratio=momentum.momentum_amount_velocity_ratio,
    vwap_premium_pct=vwap_premium_pct,
    spread_pct=spread_pct,
    momentum_baseline_coverage_seconds=(momentum.momentum_baseline_coverage_seconds),
  )


def _evaluate_momentum_signal(
  ordered: list[TickSample],
  *,
  policy: SignalPolicy,
  vwap: float,
  spread_ticks: float,
  spread_pct: float,
  vwap_premium_pct: float,
) -> IntradayTSignal:
  latest = ordered[-1]
  if not policy.momentum_enabled:
    return _empty_signal("MOMENTUM_DISABLED", ordered)

  cutoff = latest.timestamp_ms - policy.momentum_window_seconds * 1000
  move_window = [item for item in ordered if item.timestamp_ms >= cutoff]
  if len(move_window) < 2:
    return _empty_signal("MOMENTUM_WINDOW_INSUFFICIENT", move_window)

  low_sample = min(move_window, key=lambda item: (item.price, item.timestamp_ms))
  high = max(item.price for item in move_window)
  rise_pct = (
    (latest.price / low_sample.price - 1.0) * 100.0 if low_sample.price > 0 else 0.0
  )
  move_seconds = max(0.0, (latest.timestamp_ms - low_sample.timestamp_ms) / 1000.0)
  tick_size = max(policy.price_tick, 1e-8)
  near_high = latest.price >= (
    high - max(0, policy.momentum_high_tolerance_ticks) * tick_size - 1e-8
  )

  baseline_cutoff = low_sample.timestamp_ms - policy.momentum_baseline_seconds * 1000
  baseline_candidates = [
    item
    for item in ordered
    if baseline_cutoff <= item.timestamp_ms <= low_sample.timestamp_ms
  ]
  baseline_start = baseline_candidates[0] if baseline_candidates else low_sample
  baseline_seconds = max(
    0.0, (low_sample.timestamp_ms - baseline_start.timestamp_ms) / 1000.0
  )
  required_baseline_seconds = max(1.0, float(policy.momentum_baseline_seconds) * 0.8)
  move_amount = latest.cumulative_amount - low_sample.cumulative_amount
  baseline_amount = low_sample.cumulative_amount - baseline_start.cumulative_amount
  amount_velocity_ratio = (
    (move_amount / move_seconds) / (baseline_amount / baseline_seconds)
    if move_seconds > 0
    and baseline_seconds > 0
    and move_amount > 0
    and baseline_amount > 0
    else 0.0
  )

  if latest.ask_price <= 0 or latest.bid_price <= 0:
    reason = "ORDER_BOOK_UNAVAILABLE"
  elif rise_pct < policy.momentum_min_rise_pct:
    reason = "MOMENTUM_RISE_TOO_SMALL"
  elif move_seconds < policy.momentum_min_move_seconds:
    reason = "MOMENTUM_MOVE_TOO_SHORT"
  elif not near_high:
    reason = "MOMENTUM_NOT_AT_WINDOW_HIGH"
  elif baseline_seconds < required_baseline_seconds:
    reason = "MOMENTUM_BASELINE_INSUFFICIENT"
  elif move_amount <= 0 or baseline_amount <= 0:
    reason = "MOMENTUM_TURNOVER_UNAVAILABLE"
  elif amount_velocity_ratio < policy.momentum_min_amount_velocity_ratio:
    reason = "MOMENTUM_TURNOVER_TOO_WEAK"
  elif vwap <= 0:
    reason = "MOMENTUM_VWAP_UNAVAILABLE"
  elif vwap_premium_pct < policy.momentum_min_vwap_premium_pct:
    reason = "MOMENTUM_REGIME_NOT_STRONG"
  elif vwap_premium_pct > policy.momentum_max_vwap_premium_pct:
    reason = "MOMENTUM_VWAP_PREMIUM_TOO_HIGH"
  elif spread_ticks > policy.momentum_max_spread_ticks:
    reason = "MOMENTUM_SPREAD_TOO_WIDE"
  elif spread_pct > policy.momentum_max_spread_pct:
    reason = "MOMENTUM_SPREAD_PCT_TOO_WIDE"
  else:
    reason = "MOMENTUM_ACCELERATION_CONFIRMED"

  return IntradayTSignal(
    triggered=reason == "MOMENTUM_ACCELERATION_CONFIRMED",
    reason=reason,
    signal_price=latest.price,
    window_high=high,
    window_low=low_sample.price,
    pullback_pct=0.0,
    rebound_pct=0.0,
    vwap=vwap,
    spread_ticks=spread_ticks,
    signal_type=(
      "MOMENTUM_ACCELERATION" if reason == "MOMENTUM_ACCELERATION_CONFIRMED" else "NONE"
    ),
    momentum_rise_pct=rise_pct,
    momentum_move_seconds=move_seconds,
    momentum_amount_velocity_ratio=amount_velocity_ratio,
    vwap_premium_pct=vwap_premium_pct,
    spread_pct=spread_pct,
    momentum_baseline_coverage_seconds=baseline_seconds,
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
