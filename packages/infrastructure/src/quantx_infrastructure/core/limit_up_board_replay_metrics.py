"""Metrics for the account-level limit-up-board historical replay."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from math import ceil
from typing import Any, Iterable


def _number(value: Any, default: float = 0.0) -> float:
  try:
    return float(value) if value is not None else default
  except (TypeError, ValueError):
    return default


def _integer(value: Any, default: int = 0) -> int:
  try:
    return int(value) if value is not None else default
  except (TypeError, ValueError):
    return default


def _enum_value(value: Any) -> str:
  return str(getattr(value, "value", value) or "").upper()


def _iso(value: Any) -> Any:
  return value.isoformat() if isinstance(value, datetime) else value


def _trade_payload(trade: Any) -> dict[str, Any]:
  metadata = dict(getattr(trade, "metadata", {}) or {})
  return {
    "trade_id": str(getattr(trade, "trade_id", "") or ""),
    "order_id": str(getattr(trade, "order_id", "") or ""),
    "instrument_code": str(getattr(trade, "instrument_code", "") or ""),
    "side": _enum_value(getattr(trade, "trade_type", "")),
    "price": _number(getattr(trade, "price", 0.0)),
    "volume": _integer(getattr(trade, "volume", 0)),
    "amount": _number(getattr(trade, "amount", 0.0)),
    "fees": _number(getattr(trade, "commission", 0.0)),
    "trade_time": _iso(getattr(trade, "trade_time", None)),
    "metadata": metadata,
  }


def _order_payload(order: Any) -> dict[str, Any]:
  request = getattr(order, "request", None)
  return {
    "order_id": str(getattr(order, "order_id", "") or ""),
    "instrument_code": str(getattr(request, "instrument_code", "") or ""),
    "side": _enum_value(getattr(request, "order_type", "")),
    "status": _enum_value(getattr(order, "status", "")),
    "requested_volume": _integer(getattr(request, "volume", 0)),
    "filled_volume": _integer(getattr(order, "filled_volume", 0)),
    "limit_price": _number(getattr(request, "price", 0.0)),
    "average_price": _number(getattr(order, "avg_price", 0.0)),
    "fees": _number(getattr(order, "commission", 0.0)),
    "submitted_at": _iso(getattr(order, "submit_time", None)),
    "updated_at": _iso(getattr(order, "last_update_time", None)),
    "error_message": str(getattr(order, "error_message", "") or ""),
    "metadata": dict(getattr(request, "metadata", {}) or {}),
  }


def _closed_returns(trades: Iterable[dict[str, Any]]) -> list[float]:
  """Return conservative average-cost returns for fully/partly closed stock lots."""

  inventory: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
  returns: list[float] = []
  for trade in trades:
    code = str(trade.get("instrument_code") or "")
    volume = max(0, _integer(trade.get("volume")))
    amount = max(0.0, _number(trade.get("amount")))
    fees = max(0.0, _number(trade.get("fees")))
    if not code or volume <= 0:
      continue
    open_volume, open_cost = inventory[code]
    if trade.get("side") in {"BUY", "BUY_TO_COVER"}:
      inventory[code] = [open_volume + volume, open_cost + amount + fees]
      continue
    if open_volume <= 0 or open_cost <= 0:
      continue
    closed = min(open_volume, float(volume))
    allocated_cost = open_cost * closed / open_volume
    proceeds = max(0.0, amount - fees) * closed / volume
    if allocated_cost > 0:
      returns.append((proceeds - allocated_cost) / allocated_cost * 100.0)
    remaining_volume = open_volume - closed
    remaining_cost = max(0.0, open_cost - allocated_cost)
    inventory[code] = [remaining_volume, remaining_cost]
  return returns


def _cvar95_loss_pct(returns: list[float]) -> float:
  losses = sorted(max(0.0, -value) for value in returns if value < 0)
  if not losses:
    return 0.0
  tail_count = max(1, ceil(len(losses) * 0.05))
  return sum(losses[-tail_count:]) / tail_count


def build_limit_up_board_replay_metrics(runtime: Any) -> dict[str, Any]:
  """Build a compact JSON-safe scenario result without forcing open positions."""

  broker = getattr(runtime, "broker", None)
  context = getattr(runtime, "context", None)
  parameters = dict(getattr(context, "parameters", {}) or {})
  counters = dict(parameters.get("limit_up_board_replay_counters") or {})
  trades = [_trade_payload(item) for item in list(getattr(broker, "trades", []) or [])]
  orders = [
    _order_payload(item)
    for item in list(dict(getattr(broker, "orders", {}) or {}).values())
  ]
  constraints = (
    dict(broker.get_constraint_statistics() or {})
    if broker is not None and hasattr(broker, "get_constraint_statistics")
    else {}
  )
  performance = (
    dict(broker.get_performance_metrics() or {})
    if broker is not None and hasattr(broker, "get_performance_metrics")
    else {}
  )
  filled_orders = [item for item in orders if item["filled_volume"] > 0]
  partial_orders = [
    item
    for item in filled_orders
    if item["filled_volume"] < item["requested_volume"]
  ]
  rejected_orders = [item for item in orders if item["status"] == "REJECTED"]
  expired_orders = [item for item in orders if item["status"] == "EXPIRED"]
  open_orders = [
    item
    for item in orders
    if item["status"] in {"PENDING", "SUBMITTED", "ACCEPTED", "PARTIAL_FILLED"}
  ]

  positions = []
  for code, position in sorted(dict(getattr(broker, "positions", {}) or {}).items()):
    volume = max(0, _integer(getattr(position, "long_volume", 0)))
    if volume <= 0:
      continue
    market = dict(getattr(runtime, "latest_market_data", {}) or {}).get(code)
    bids = list(getattr(market, "bid_price", []) or []) if market else []
    at_limit_down = bool(
      market
      and getattr(market, "limit_down", None) is not None
      and _number(getattr(market, "price", 0.0))
      <= _number(getattr(market, "limit_down", 0.0))
    )
    if market is None:
      status = "OPEN_DATA_INCOMPLETE"
    elif at_limit_down and (not bids or _number(bids[0]) <= 0):
      status = "OPEN_UNSELLABLE"
    else:
      status = "OPEN_AT_WINDOW_END"
    positions.append(
      {
        "instrument_code": str(code),
        "volume": volume,
        "available_volume": max(
          0, _integer(getattr(position, "available_volume", 0))
        ),
        "average_price": _number(getattr(position, "long_avg_price", 0.0)),
        "last_price": _number(getattr(position, "last_price", 0.0)),
        "market_value": _number(getattr(position, "market_value", 0.0)),
        "status": status,
      }
    )

  rejection_reasons: dict[str, int] = defaultdict(int)
  for item in rejected_orders:
    reason = str(item.get("error_message") or "BROKER_REJECTED")
    rejection_reasons[reason] += 1
  for reason, count in dict(counters.get("rejection_reasons") or {}).items():
    rejection_reasons[str(reason)] += max(0, _integer(count))

  closed_returns = _closed_returns(trades)
  open_mark_to_market_returns = [
    (item["last_price"] - item["average_price"])
    / item["average_price"]
    * 100.0
    for item in positions
    if item["average_price"] > 0 and item["last_price"] > 0
  ]
  curve = [
    {
      "timestamp": _iso(dict(point or {}).get("timestamp")),
      "equity": _number(dict(point or {}).get("equity")),
      "return_pct": (
        (_number(dict(point or {}).get("equity")) - _number(getattr(broker, "initial_capital", 0.0)))
        / _number(getattr(broker, "initial_capital", 0.0))
        * 100.0
        if _number(getattr(broker, "initial_capital", 0.0)) > 0
        else 0.0
      ),
    }
    for point in list(getattr(broker, "replay_curve", []) or [])
  ]
  data_quality = dict(parameters.get("limit_up_board_replay_data_quality") or {})
  return {
    "schema_version": 1,
    "scenario_id": str(parameters.get("limit_up_board_replay_scenario_id") or ""),
    "confirmation_delay_ms": _integer(
      parameters.get("limit_up_board_replay_confirmation_delay_ms")
    ),
    "participation_cap_pct": _number(parameters.get("participation_cap_pct")),
    "book_depth_participation_pct": _number(
      parameters.get("book_depth_participation_pct")
    ),
    "no_queue_credit": True,
    "data_quality": data_quality,
    "funnel": {
      "candidate_frames": _integer(counters.get("candidate_frames")),
      "candidate_observations": _integer(counters.get("candidate_observations")),
      "qualified_observations": _integer(counters.get("qualified_observations")),
      "entry_intents": _integer(counters.get("entry_intents")),
      "approval_due": _integer(counters.get("approval_due")),
      "approval_rejected": _integer(counters.get("approval_rejected")),
      "orders": len(orders),
      "filled_orders": len(filled_orders),
      "partial_orders": len(partial_orders),
      "expired_orders": len(expired_orders),
      "trades": len(trades),
      "completed_exits": sum(1 for item in trades if item["side"] == "SELL"),
    },
    "summary": {
      "initial_equity": _number(getattr(broker, "initial_capital", 0.0)),
      "final_equity": _number(performance.get("final_equity")),
      "total_return_pct": _number(performance.get("total_return_pct")),
      "max_drawdown_pct": _number(performance.get("max_drawdown_pct")),
      # Open positions are censored, not discarded.  Their window-end
      # mark-to-market return stays in the tail sample, especially when a
      # limit-down book makes the position unsellable.
      "cvar95_loss_pct": _cvar95_loss_pct(
        [*closed_returns, *open_mark_to_market_returns]
      ),
      "fees": sum(_number(item.get("fees")) for item in trades),
      "fill_rate_pct": len(filled_orders) / len(orders) * 100.0 if orders else 0.0,
      "open_position_count": len(positions),
      "open_order_count": len(open_orders),
      "unsellable_position_count": sum(
        item["status"] == "OPEN_UNSELLABLE" for item in positions
      ),
    },
    "constraint_statistics": constraints,
    "rejection_reasons": dict(sorted(rejection_reasons.items())),
    "open_positions": positions,
    "open_orders": open_orders,
    "orders": orders,
    "trades": trades,
    "curve": curve,
  }


__all__ = ["build_limit_up_board_replay_metrics"]
