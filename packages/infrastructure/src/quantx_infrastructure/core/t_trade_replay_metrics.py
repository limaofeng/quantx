"""Result aggregation for account-level T-assistant historical replays."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List


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


def _value(value: Any) -> Any:
  return getattr(value, "value", value)


def _timestamp(value: Any) -> Any:
  return value.isoformat() if isinstance(value, datetime) else value


def _trade_payload(trade: Any) -> Dict[str, Any]:
  metadata = dict(getattr(trade, "metadata", {}) or {})
  costs = dict(metadata.get("costs") or {})
  return {
    "instrument_code": str(getattr(trade, "instrument_code", "") or ""),
    "side": str(_value(getattr(trade, "trade_type", "")) or "").upper(),
    "price": _number(getattr(trade, "price", 0.0)),
    "volume": _integer(getattr(trade, "volume", 0)),
    "amount": _number(getattr(trade, "amount", 0.0)),
    "fees": _number(getattr(trade, "commission", 0.0)),
    "commission": _number(costs.get("commission")),
    "stamp_tax": _number(costs.get("stamp_tax")),
    "transfer_fee": _number(costs.get("transfer_fee")),
    "trade_time": getattr(trade, "trade_time", None),
    "metadata": metadata,
  }


def _cycle_from_trades(batch_id: str, trades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
  rows = sorted(
    list(trades),
    key=lambda row: row.get("trade_time") or datetime.min,
  )
  entries = [row for row in rows if row["side"] in {"BUY", "BUY_TO_COVER"}]
  exits = [row for row in rows if row["side"] not in {"BUY", "BUY_TO_COVER"}]
  entry_volume = sum(row["volume"] for row in entries)
  exit_volume = sum(row["volume"] for row in exits)
  entry_amount = sum(row["amount"] for row in entries)
  exit_amount = sum(row["amount"] for row in exits)
  entry_fees = sum(row["fees"] for row in entries)
  exit_fees = sum(row["fees"] for row in exits)
  realized_volume = min(entry_volume, exit_volume)
  allocated_entry_cost = (
    (entry_amount + entry_fees) * realized_volume / entry_volume
    if entry_volume > 0
    else 0.0
  )
  realized_exit_proceeds = (
    (exit_amount - exit_fees) * realized_volume / exit_volume
    if exit_volume > 0
    else 0.0
  )
  net_profit = realized_exit_proceeds - allocated_entry_cost
  net_return_pct = (
    net_profit / allocated_entry_cost * 100.0 if allocated_entry_cost > 0 else 0.0
  )
  open_volume = max(0, entry_volume - exit_volume)
  complete = entry_volume > 0 and open_volume == 0 and exit_volume >= entry_volume
  first = rows[0] if rows else {}
  last_exit = exits[-1] if exits else {}
  metadata = dict(last_exit.get("metadata") or {})
  return {
    "batch_id": batch_id,
    "stock_code": str(first.get("instrument_code") or ""),
    "status": "COMPLETED" if complete else "OPEN",
    "entry_time": _timestamp(entries[0].get("trade_time")) if entries else None,
    "exit_time": (
      _timestamp(exits[-1].get("trade_time")) if complete and exits else None
    ),
    "entry_volume": entry_volume,
    "exit_volume": exit_volume,
    "open_volume": open_volume,
    "entry_avg_price": entry_amount / entry_volume if entry_volume > 0 else 0.0,
    "exit_avg_price": exit_amount / exit_volume if exit_volume > 0 else 0.0,
    "total_fees": entry_fees + exit_fees,
    "net_profit": net_profit,
    "net_return_pct": net_return_pct,
    "exit_reason": str(metadata.get("exit_reason", "") or ""),
  }


def build_t_trade_replay_metrics(runtime: Any) -> Dict[str, Any]:
  """Build a compact, JSON-safe replay result from the completed runtime."""

  broker = getattr(runtime, "broker", None)
  params = dict(getattr(getattr(runtime, "context", None), "parameters", {}) or {})
  trades = [_trade_payload(item) for item in list(getattr(broker, "trades", []) or [])]
  grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
  for index, trade in enumerate(trades):
    metadata = dict(trade.get("metadata") or {})
    batch_id = str(metadata.get("t_batch_id", "") or f"unbatched-{index + 1}")
    grouped[batch_id].append(trade)

  cycles = [_cycle_from_trades(batch_id, rows) for batch_id, rows in grouped.items()]
  cycles.sort(key=lambda row: str(row.get("entry_time") or ""))
  completed = [row for row in cycles if row["status"] == "COMPLETED"]
  open_cycles = [row for row in cycles if row["status"] != "COMPLETED"]
  winning = [row for row in completed if row["net_profit"] > 0]

  names = {
    str(item.get("stock_code", "") or ""): str(item.get("instrument_name", "") or "")
    for item in list(params.get("initial_positions") or [])
  }
  per_instrument: Dict[str, Dict[str, Any]] = {}
  eligible_metadata = dict(params.get("initial_instrument_metadata") or {})
  for code, item in eligible_metadata.items():
    normalized_code = str(code or "")
    if not normalized_code:
      continue
    item = dict(item or {})
    per_instrument[normalized_code] = {
      "stock_code": normalized_code,
      "instrument_name": str(
        item.get("instrument_name", "") or names.get(normalized_code, "")
      ),
      "status": "NO_TRADE",
      "reason": "回放区间内未形成做 T 成交批次",
      "t_net_profit": 0.0,
      "total_fees": 0.0,
      "completed_cycles": 0,
      "open_cycles": 0,
      "winning_cycles": 0,
      "win_rate_pct": 0.0,
    }
  for cycle in cycles:
    code = cycle["stock_code"]
    result = per_instrument.setdefault(
      code,
      {
        "stock_code": code,
        "instrument_name": names.get(code, ""),
        "status": "OK",
        "reason": "",
        "t_net_profit": 0.0,
        "total_fees": 0.0,
        "completed_cycles": 0,
        "open_cycles": 0,
        "winning_cycles": 0,
        "win_rate_pct": 0.0,
      },
    )
    result["status"] = "OK"
    result["reason"] = ""
    result["t_net_profit"] += cycle["net_profit"]
    result["total_fees"] += cycle["total_fees"]
    if cycle["status"] == "COMPLETED":
      result["completed_cycles"] += 1
      if cycle["net_profit"] > 0:
        result["winning_cycles"] += 1
    else:
      result["open_cycles"] += 1

  skipped = list(params.get("replay_skipped_instruments") or [])
  for item in skipped:
    code = str(item.get("stock_code", "") or "")
    if not code:
      continue
    per_instrument.setdefault(
      code,
      {
        "stock_code": code,
        "instrument_name": str(item.get("instrument_name", "") or names.get(code, "")),
        "status": "DATA_INSUFFICIENT",
        "reason": str(item.get("reason", "历史 Tick 数据不足") or "历史 Tick 数据不足"),
        "t_net_profit": 0.0,
        "total_fees": 0.0,
        "completed_cycles": 0,
        "open_cycles": 0,
        "winning_cycles": 0,
        "win_rate_pct": 0.0,
      },
    )

  for result in per_instrument.values():
    count = result["completed_cycles"]
    result["win_rate_pct"] = (
      result["winning_cycles"] / count * 100.0 if count else 0.0
    )

  initial_equity = _number(params.get("initial_total_asset"), getattr(broker, "initial_capital", 0.0))
  curve = []
  for point in list(getattr(broker, "replay_curve", []) or []):
    equity = _number(point.get("equity"), initial_equity)
    passive = _number(point.get("passive_equity"), initial_equity)
    return_pct = (equity - initial_equity) / initial_equity * 100.0 if initial_equity else 0.0
    passive_return = (passive - initial_equity) / initial_equity * 100.0 if initial_equity else 0.0
    curve.append(
      {
        "timestamp": _timestamp(point.get("timestamp")),
        "equity": equity,
        "passive_equity": passive,
        "t_net_profit": equity - passive,
        "return_pct": return_pct,
        "passive_return_pct": passive_return,
        "excess_return_pct": return_pct - passive_return,
      }
    )

  final_equity = curve[-1]["equity"] if curve else initial_equity
  passive_final = curve[-1]["passive_equity"] if curve else initial_equity
  total_return_pct = (
    (final_equity - initial_equity) / initial_equity * 100.0 if initial_equity else 0.0
  )
  passive_return_pct = (
    (passive_final - initial_equity) / initial_equity * 100.0 if initial_equity else 0.0
  )
  total_fees = sum(row["fees"] for row in trades)
  turnover = sum(row["amount"] for row in trades)
  broker_metrics = broker.get_performance_metrics() if broker else {}
  return {
    "data_quality": "PARTIAL" if skipped else "OK",
    "data_quality_message": (
      f"{len(skipped)} 只持仓因历史数据不足未参与回放" if skipped else "历史回放数据完整"
    ),
    "skipped_stock_codes": [str(item.get("stock_code", "") or "") for item in skipped],
    "summary": {
      "initial_equity": initial_equity,
      "final_equity": final_equity,
      "t_net_profit": final_equity - passive_final,
      "total_return_pct": total_return_pct,
      "passive_final_equity": passive_final,
      "passive_return_pct": passive_return_pct,
      "excess_return_pct": total_return_pct - passive_return_pct,
      "max_drawdown_pct": _number(broker_metrics.get("max_drawdown_pct")),
      "total_fees": total_fees,
      "turnover": turnover,
      "completed_cycles": len(completed),
      "open_cycles": len(open_cycles),
      "winning_cycles": len(winning),
      "win_rate_pct": len(winning) / len(completed) * 100.0 if completed else 0.0,
    },
    "instruments": sorted(per_instrument.values(), key=lambda row: row["stock_code"]),
    "curve": curve,
    "cycles": cycles,
  }
