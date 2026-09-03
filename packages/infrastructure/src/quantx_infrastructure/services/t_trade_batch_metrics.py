"""Pure financial metrics for one persisted positive-T batch.

The operational batch is an aggregate of one entry side and one exit side.
Fees are therefore estimated once per side, including the minimum commission
for each side.  Historical rows without a frozen cost policy deliberately
remain incomplete; current configuration must never be used to rewrite them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from quantx_domain.clock import utcnow

METRIC_BASIS_ESTIMATED = "RULE_ESTIMATE"
METRIC_ORIGIN_RULE_ESTIMATE = "RULE_ESTIMATE"
METRIC_ORIGIN_LEGACY_BACKFILL = "LEGACY_BACKFILL"
METRIC_ORIGIN_UNKNOWN = "UNKNOWN"
METRIC_QUALITY_COMPLETE = "COMPLETE"
METRIC_QUALITY_INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class TTradeCostSnapshot:
  commission_rate: float
  minimum_commission: float
  stamp_tax_rate: float
  transfer_fee_rate: float


def _finite_nonnegative(value: Any) -> float | None:
  try:
    normalized = float(value)
  except (TypeError, ValueError):
    return None
  if not math.isfinite(normalized) or normalized < 0:
    return None
  return normalized


def extract_t_trade_cost_snapshot(
  metadata: Mapping[str, Any] | None,
) -> TTradeCostSnapshot | None:
  """Read an immutable cost policy from order lineage without defaults."""

  raw = dict(metadata or {})
  template = raw.get("exit_plan_template")
  if isinstance(template, Mapping):
    nested = template.get("costs")
    if isinstance(nested, Mapping):
      raw = {**dict(nested), **raw}

  commission_rate = _finite_nonnegative(raw.get("commission_rate"))
  minimum_commission = _finite_nonnegative(
    raw.get("minimum_commission", raw.get("min_commission"))
  )
  stamp_tax_rate = _finite_nonnegative(raw.get("stamp_tax_rate"))
  transfer_fee_rate = _finite_nonnegative(raw.get("transfer_fee_rate"))
  if None in {
    commission_rate,
    minimum_commission,
    stamp_tax_rate,
    transfer_fee_rate,
  }:
    return None
  return TTradeCostSnapshot(
    commission_rate=float(commission_rate),
    minimum_commission=float(minimum_commission),
    stamp_tax_rate=float(stamp_tax_rate),
    transfer_fee_rate=float(transfer_fee_rate),
  )


def t_trade_cost_snapshot_from_batch(batch: Any) -> TTradeCostSnapshot | None:
  values = {
    "commission_rate": getattr(batch, "commission_rate", None),
    "minimum_commission": getattr(batch, "minimum_commission", None),
    "stamp_tax_rate": getattr(batch, "stamp_tax_rate", None),
    "transfer_fee_rate": getattr(batch, "transfer_fee_rate", None),
  }
  return extract_t_trade_cost_snapshot(values)


def _side_fees(
  amount_cny: float,
  costs: TTradeCostSnapshot,
  *,
  sell: bool,
) -> float:
  if amount_cny <= 0:
    return 0.0
  commission = max(
    costs.minimum_commission,
    amount_cny * costs.commission_rate,
  )
  transfer_fee = amount_cny * costs.transfer_fee_rate
  stamp_tax = amount_cny * costs.stamp_tax_rate if sell else 0.0
  return commission + transfer_fee + stamp_tax


def _hours_between(start: datetime, end: datetime) -> float | None:
  try:
    seconds = (end - start).total_seconds()
  except (TypeError, ValueError):
    return None
  if not math.isfinite(seconds) or seconds < 0:
    return None
  return seconds / 3600.0


def _metric_origin(batch: Any) -> str:
  origin = str(getattr(batch, "metrics_origin", None) or "").strip().upper()
  if origin in {METRIC_ORIGIN_RULE_ESTIMATE, METRIC_ORIGIN_LEGACY_BACKFILL}:
    return origin
  return METRIC_ORIGIN_UNKNOWN


def incomplete_t_trade_batch_metrics(
  *,
  origin: str = METRIC_ORIGIN_UNKNOWN,
) -> dict[str, Any]:
  return {
    "basis": METRIC_BASIS_ESTIMATED,
    "origin": origin,
    "quality": METRIC_QUALITY_INCOMPLETE,
    "entry_capital_cny": None,
    "total_fees_cny": None,
    "realized_net_profit_cny": None,
    "mark_to_market_net_profit_cny": None,
    "net_return_pct": None,
    "holding_hours": None,
    "capital_utilization_pct": None,
  }


def calculate_t_trade_batch_metrics(
  batch: Any,
  *,
  as_of: datetime | None = None,
  market_price: float | None = None,
) -> dict[str, Any]:
  """Calculate one batch from persisted facts only.

  ``mark_to_market_net_profit_cny`` represents the total batch result if the
  remaining active volume were sold at ``last_price`` now.  For a closed
  batch it equals realized net profit.
  """

  result = incomplete_t_trade_batch_metrics(origin=_metric_origin(batch))
  costs = t_trade_cost_snapshot_from_batch(batch)
  execution_mode = str(getattr(batch, "environment", None) or "").lower()
  entry_filled_at = getattr(batch, "entry_filled_at", None)
  if costs is None or execution_mode not in {"paper", "live"}:
    return result
  if execution_mode == "live" and costs.commission_rate <= 0:
    return result

  entry_volume = max(0, int(getattr(batch, "entry_filled_volume", 0) or 0))
  reported_exit_volume = max(
    0,
    int(getattr(batch, "exit_filled_volume", 0) or 0),
  )
  if reported_exit_volume > entry_volume:
    return result
  exit_volume = reported_exit_volume
  active_volume = max(0, entry_volume - exit_volume)
  entry_price = _finite_nonnegative(getattr(batch, "entry_avg_price", None))
  exit_price = _finite_nonnegative(getattr(batch, "exit_avg_price", None))
  if (
    entry_volume <= 0
    or entry_price is None
    or entry_price <= 0
    or not isinstance(entry_filled_at, datetime)
  ):
    return result
  if exit_volume > 0 and (exit_price is None or exit_price <= 0):
    return result
  entry_gross = entry_price * entry_volume
  buy_fees = _side_fees(entry_gross, costs, sell=False)
  entry_capital = entry_gross + buy_fees
  exited_gross = float(exit_price or 0.0) * exit_volume
  current_exit_fees = _side_fees(exited_gross, costs, sell=True)
  allocated_entry_gross = entry_price * exit_volume
  allocated_buy_fees = buy_fees * exit_volume / entry_volume
  realized = (
    exited_gross
    - allocated_entry_gross
    - allocated_buy_fees
    - current_exit_fees
  )

  holding_end = as_of or utcnow()
  holding_hours = _hours_between(entry_filled_at, holding_end)
  if holding_hours is None:
    return result
  utilization = 100.0 if holding_hours <= 0 else min(100.0, 400.0 / holding_hours)
  if active_volume > 0:
    # The operational table has no authoritative, freshness-qualified quote
    # timestamp today.  Preserve known realized facts, but never publish a
    # fabricated mark-to-market result from its legacy ``last_price`` value.
    fresh_price = _finite_nonnegative(market_price)
    if fresh_price is None or fresh_price <= 0:
      return {
        **result,
        "entry_capital_cny": entry_capital,
        "total_fees_cny": buy_fees + current_exit_fees,
        "realized_net_profit_cny": realized,
        "holding_hours": holding_hours,
        "capital_utilization_pct": utilization,
      }
    marked_exit_gross = exited_gross + fresh_price * active_volume
    marked_exit_fees = _side_fees(marked_exit_gross, costs, sell=True)
    marked_profit = marked_exit_gross - entry_gross - buy_fees - marked_exit_fees
    return {
      **result,
      "quality": METRIC_QUALITY_COMPLETE,
      "entry_capital_cny": entry_capital,
      "total_fees_cny": buy_fees + marked_exit_fees,
      "realized_net_profit_cny": realized,
      "mark_to_market_net_profit_cny": marked_profit,
      "net_return_pct": marked_profit / entry_capital * 100.0,
      "holding_hours": holding_hours,
      "capital_utilization_pct": utilization,
    }

  marked_exit_gross = exited_gross
  marked_exit_fees = _side_fees(marked_exit_gross, costs, sell=True)
  marked_profit = marked_exit_gross - entry_gross - buy_fees - marked_exit_fees
  total_fees = buy_fees + marked_exit_fees

  end_at = getattr(batch, "closed_at", None)
  if not isinstance(end_at, datetime):
    return result
  holding_hours = _hours_between(entry_filled_at, end_at)
  if holding_hours is None:
    return result
  utilization = 100.0 if holding_hours <= 0 else min(100.0, 400.0 / holding_hours)

  selected_profit = realized if active_volume == 0 else marked_profit
  return {
    **result,
    "quality": METRIC_QUALITY_COMPLETE,
    "entry_capital_cny": entry_capital,
    "total_fees_cny": total_fees,
    "realized_net_profit_cny": realized,
    "mark_to_market_net_profit_cny": marked_profit,
    "net_return_pct": selected_profit / entry_capital * 100.0,
    "holding_hours": holding_hours,
    "capital_utilization_pct": utilization,
  }
