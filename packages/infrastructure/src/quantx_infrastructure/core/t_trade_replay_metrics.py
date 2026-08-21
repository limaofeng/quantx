"""Result aggregation for account-level T-assistant historical replays."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

CAPITAL_UTILIZATION_REFERENCE_HOURS = 4.0


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


def _datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime):
    return value.replace(tzinfo=None) if value.tzinfo else value
  if isinstance(value, str) and value:
    try:
      parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
      return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
      return None
  return None


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


def _capital_events(trades: Iterable[Dict[str, Any]]) -> List[tuple[datetime, float]]:
  """Return capital deltas using average-cost release for partial exits."""

  events: List[tuple[datetime, float]] = []
  open_volume = 0
  open_cost = 0.0
  for row in sorted(
    list(trades), key=lambda item: item.get("trade_time") or datetime.min
  ):
    timestamp = _datetime(row.get("trade_time"))
    if timestamp is None:
      continue
    if row["side"] in {"BUY", "BUY_TO_COVER"}:
      delta = max(0.0, row["amount"] + row["fees"])
      open_volume += max(0, row["volume"])
      open_cost += delta
      events.append((timestamp, delta))
      continue
    if open_volume <= 0 or open_cost <= 0:
      continue
    sold = min(open_volume, max(0, row["volume"]))
    released = open_cost * sold / open_volume
    open_volume -= sold
    open_cost = max(0.0, open_cost - released)
    events.append((timestamp, -released))
  return events


def _cycle_from_trades(
  batch_id: str,
  trades: Iterable[Dict[str, Any]],
  *,
  evaluation_end: Optional[datetime],
) -> Dict[str, Any]:
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
  metadata = dict(exits[-1].get("metadata") or {}) if exits else {}
  forced_exit = any(
    bool(dict(row.get("metadata") or {}).get("backtest_forced_close")) for row in exits
  )
  entry_time = _datetime(entries[0].get("trade_time")) if entries else None
  holding_end = (
    _datetime(exits[-1].get("trade_time")) if complete and exits else evaluation_end
  )
  if holding_end is None and rows:
    holding_end = _datetime(rows[-1].get("trade_time"))
  holding_seconds = max(
    0.0,
    (holding_end - entry_time).total_seconds()
    if entry_time is not None and holding_end is not None
    else 0.0,
  )
  holding_hours = holding_seconds / 3600.0
  utilization_pct = (
    min(
      100.0,
      CAPITAL_UTILIZATION_REFERENCE_HOURS / max(holding_hours, 1e-9) * 100.0,
    )
    if entry_volume > 0
    else 0.0
  )
  return {
    "batch_id": batch_id,
    "stock_code": str(first.get("instrument_code") or ""),
    "status": "COMPLETED" if complete else "OPEN",
    "liquidation_status": (
      "FORCED_CLOSED"
      if complete and forced_exit
      else "NATURAL_CLOSED"
      if complete
      else "OPEN"
    ),
    "forced_exit": forced_exit,
    "entry_time": _timestamp(entry_time),
    "exit_time": _timestamp(holding_end) if complete else None,
    "entry_volume": entry_volume,
    "exit_volume": exit_volume,
    "open_volume": open_volume,
    "entry_avg_price": entry_amount / entry_volume if entry_volume > 0 else 0.0,
    "exit_avg_price": exit_amount / exit_volume if exit_volume > 0 else 0.0,
    "entry_capital": entry_amount + entry_fees,
    "total_fees": entry_fees + exit_fees,
    "net_profit": net_profit,
    "net_return_pct": net_return_pct,
    "holding_hours": holding_hours,
    "capital_utilization_pct": utilization_pct,
    "exit_reason": str(metadata.get("exit_reason", "") or ""),
  }


def _capital_usage(
  grouped: Dict[str, List[Dict[str, Any]]],
  *,
  start_time: Optional[datetime],
  end_time: Optional[datetime],
) -> Dict[str, float]:
  events = sorted(
    [event for rows in grouped.values() for event in _capital_events(rows)],
    key=lambda item: item[0],
  )
  if not events:
    return {
      "average_occupied_capital": 0.0,
      "peak_occupied_capital": 0.0,
      "occupied_capital_hours": 0.0,
    }
  period_start = start_time or events[0][0]
  period_end = end_time or events[-1][0]
  if period_end < period_start:
    period_start, period_end = period_end, period_start
  occupied = 0.0
  peak = 0.0
  occupied_seconds = 0.0
  cursor = period_start
  for timestamp, delta in events:
    clipped = min(max(timestamp, period_start), period_end)
    if clipped > cursor:
      occupied_seconds += occupied * (clipped - cursor).total_seconds()
      cursor = clipped
    occupied = max(0.0, occupied + delta)
    peak = max(peak, occupied)
  if period_end > cursor:
    occupied_seconds += occupied * (period_end - cursor).total_seconds()
  elapsed = max(0.0, (period_end - period_start).total_seconds())
  return {
    "average_occupied_capital": occupied_seconds / elapsed if elapsed > 0 else occupied,
    "peak_occupied_capital": peak,
    "occupied_capital_hours": occupied_seconds / 3600.0,
  }


def build_t_trade_replay_metrics(runtime: Any) -> Dict[str, Any]:
  """Build a compact, JSON-safe replay result from the completed runtime."""

  broker = getattr(runtime, "broker", None)
  context = getattr(runtime, "context", None)
  params = dict(getattr(context, "parameters", {}) or {})
  trades = [_trade_payload(item) for item in list(getattr(broker, "trades", []) or [])]
  grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
  for index, trade in enumerate(trades):
    metadata = dict(trade.get("metadata") or {})
    batch_id = str(metadata.get("t_batch_id", "") or f"unbatched-{index + 1}")
    grouped[batch_id].append(trade)

  configured_start = _datetime(params.get("replay_start_time")) or _datetime(
    getattr(context, "backtest_start_time", None)
  )
  configured_end = _datetime(params.get("replay_end_time")) or _datetime(
    getattr(context, "backtest_end_time", None)
  )
  broker_end = _datetime(getattr(broker, "current_time", None))
  evaluation_end = configured_end or broker_end
  cycles = [
    _cycle_from_trades(batch_id, rows, evaluation_end=evaluation_end)
    for batch_id, rows in grouped.items()
  ]
  cycles.sort(key=lambda row: str(row.get("entry_time") or ""))
  completed = [row for row in cycles if row["status"] == "COMPLETED"]
  open_cycles = [row for row in cycles if row["status"] != "COMPLETED"]
  winning = [row for row in completed if row["net_profit"] > 0]
  forced = [row for row in completed if row["forced_exit"]]

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
      "forced_exit_cycles": 0,
      "winning_cycles": 0,
      "win_rate_pct": 0.0,
      "capital_utilization_pct": 0.0,
      "average_holding_hours": 0.0,
      "_entry_capital": 0.0,
      "_weighted_utilization": 0.0,
      "_weighted_holding": 0.0,
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
        "forced_exit_cycles": 0,
        "winning_cycles": 0,
        "win_rate_pct": 0.0,
        "capital_utilization_pct": 0.0,
        "average_holding_hours": 0.0,
        "_entry_capital": 0.0,
        "_weighted_utilization": 0.0,
        "_weighted_holding": 0.0,
      },
    )
    result["status"] = "OK" if cycle["status"] == "COMPLETED" else "LIQUIDATION_FAILED"
    result["reason"] = "" if cycle["status"] == "COMPLETED" else "期末仍有未清算 T 仓位"
    result["t_net_profit"] += cycle["net_profit"]
    result["total_fees"] += cycle["total_fees"]
    capital = cycle["entry_capital"]
    result["_entry_capital"] += capital
    result["_weighted_utilization"] += capital * cycle["capital_utilization_pct"]
    result["_weighted_holding"] += capital * cycle["holding_hours"]
    if cycle["status"] == "COMPLETED":
      result["completed_cycles"] += 1
      if cycle["forced_exit"]:
        result["forced_exit_cycles"] += 1
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
        "status": str(item.get("data_status") or "DATA_INSUFFICIENT"),
        "reason": str(item.get("reason", "历史 Tick 数据不足") or "历史 Tick 数据不足"),
        "t_net_profit": 0.0,
        "total_fees": 0.0,
        "completed_cycles": 0,
        "open_cycles": 0,
        "forced_exit_cycles": 0,
        "winning_cycles": 0,
        "win_rate_pct": 0.0,
        "capital_utilization_pct": 0.0,
        "average_holding_hours": 0.0,
        "_entry_capital": 0.0,
        "_weighted_utilization": 0.0,
        "_weighted_holding": 0.0,
      },
    )

  for result in per_instrument.values():
    count = result["completed_cycles"]
    capital = result.pop("_entry_capital", 0.0)
    weighted_utilization = result.pop("_weighted_utilization", 0.0)
    weighted_holding = result.pop("_weighted_holding", 0.0)
    result["win_rate_pct"] = result["winning_cycles"] / count * 100.0 if count else 0.0
    result["capital_utilization_pct"] = (
      weighted_utilization / capital if capital else 0.0
    )
    result["average_holding_hours"] = weighted_holding / capital if capital else 0.0
    if result["open_cycles"] > 0:
      result["status"] = "LIQUIDATION_FAILED"
      result["reason"] = "期末仍有未清算 T 仓位"
    elif result["completed_cycles"] > 0:
      result["status"] = "OK"
      result["reason"] = ""

  reported_initial_equity = _number(params.get("initial_total_asset"))
  initial_equity = _number(
    getattr(broker, "initial_capital", None), reported_initial_equity
  )
  configured_reconciliation = dict(
    params.get("initial_asset_reconciliation") or {}
  )
  broker_reconciliation = dict(
    getattr(broker, "initial_asset_reconciliation", {}) or {}
  )
  reconciliation_flags = sorted(
    {
      str(flag)
      for flag in [
        *list(configured_reconciliation.get("quality_flags") or []),
        *list(broker_reconciliation.get("quality_flags") or []),
      ]
      if str(flag)
    }
  )
  asset_reconciliation = {
    **configured_reconciliation,
    **broker_reconciliation,
    "quality_flags": reconciliation_flags,
  }
  curve = []
  trading_dates = set()
  for point in list(getattr(broker, "replay_curve", []) or []):
    equity = _number(point.get("equity"), initial_equity)
    passive = _number(point.get("passive_equity"), initial_equity)
    point_time = _datetime(point.get("timestamp"))
    if point_time:
      trading_dates.add(point_time.date())
    return_pct = (
      (equity - initial_equity) / initial_equity * 100.0 if initial_equity else 0.0
    )
    passive_return = (
      (passive - initial_equity) / initial_equity * 100.0 if initial_equity else 0.0
    )
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
  entry_capital = sum(row["entry_capital"] for row in cycles)
  weighted_utilization = sum(
    row["entry_capital"] * row["capital_utilization_pct"] for row in cycles
  )
  weighted_holding = sum(row["entry_capital"] * row["holding_hours"] for row in cycles)
  capital_utilization_pct = (
    weighted_utilization / entry_capital if entry_capital else 0.0
  )
  average_holding_hours = weighted_holding / entry_capital if entry_capital else 0.0
  max_holding_hours = max((row["holding_hours"] for row in cycles), default=0.0)
  configured_capacity = initial_equity * max(
    0.0, _number(params.get("max_total_t_exposure_pct"), 0.1)
  )
  initial_cash = (
    configured_capacity
    if params.get("initial_cash") is None
    else max(0.0, _number(params.get("initial_cash")))
  )
  capital_capacity = min(configured_capacity, initial_cash)
  usage = _capital_usage(
    grouped,
    start_time=configured_start,
    end_time=configured_end or broker_end,
  )
  capital_occupancy_pct = (
    usage["average_occupied_capital"] / capital_capacity * 100.0
    if capital_capacity > 0
    else 0.0
  )
  capital_turnover_times = (
    entry_capital / capital_capacity if capital_capacity > 0 else 0.0
  )
  trading_day_count = max(1, len(trading_dates))
  occupied_capital_days = usage["occupied_capital_hours"] / 24.0
  t_net_profit = final_equity - passive_final
  liquidation = dict(params.get("replay_forced_liquidation") or {})
  liquidation_failed_cycles = max(
    len(open_cycles),
    _integer(liquidation.get("failed_cycles")),
  )
  broker_metrics = broker.get_performance_metrics() if broker else {}
  price_limit_policy = dict(params.get("replay_price_limit_policy") or {})
  price_limit_source_counts = {
    str(key): max(0, _integer(value))
    for key, value in dict(params.get("replay_price_limit_source_counts") or {}).items()
  }
  native_limit_events = sum(
    count
    for source, count in price_limit_source_counts.items()
    if source.startswith("NATIVE_")
  )
  derived_limit_events = sum(
    count
    for source, count in price_limit_source_counts.items()
    if source.startswith("DERIVED_")
  )
  missing_limit_events = sum(
    count
    for source, count in price_limit_source_counts.items()
    if source.startswith("MISSING_")
  )
  tick_read_audit = dict(params.get("replay_tick_read_audit") or {})
  tick_read_issues = list(tick_read_audit.get("issues") or [])
  quality_messages = []
  if skipped:
    quality_messages.append(f"{len(skipped)} 只持仓因历史数据不足未参与回放")
  if liquidation_failed_cycles:
    quality_messages.append(f"{liquidation_failed_cycles} 个批次期末未完成合法清算")
  if derived_limit_events:
    quality_messages.append(
      f"{derived_limit_events} 个行情事件的涨跌停价由前收盘价和交易所规则派生"
    )
  if missing_limit_events:
    quality_messages.append(
      f"{missing_limit_events} 个行情事件缺少可确认的涨跌停价，严格风控保持拒绝"
    )
  if tick_read_issues:
    quality_messages.append(
      f"{len(tick_read_issues)} 个 Tick 读取窗口未通过完整性校验"
    )
  non_trading_asset = max(
    0.0, _number(asset_reconciliation.get("non_trading_asset"))
  )
  raw_asset_residual = _number(asset_reconciliation.get("raw_residual"))
  if non_trading_asset > 0.01:
    quality_messages.append(
      f"初始组合有 {non_trading_asset:.2f} 元未归属于可用资金或回放持仓，"
      "已作为恒定非交易资产计入主动与被动权益"
    )
  if raw_asset_residual < -0.01:
    quality_messages.append(
      f"初始现金与持仓市值比快照总资产高 {-raw_asset_residual:.2f} 元，"
      "负残差已按 0 处理并以已知分项作为初始权益"
    )
  source_quality_flags = [
    flag
    for flag in reconciliation_flags
    if flag
    not in {
      "NON_TRADING_ASSET_RESIDUAL_PRESERVED",
      "INITIAL_COMPONENTS_EXCEED_REPORTED_TOTAL",
    }
  ]
  if source_quality_flags:
    quality_messages.append(
      "初始资产快照质量标记：" + "、".join(source_quality_flags)
    )
  data_quality = "PARTIAL" if quality_messages else "OK"
  return {
    "data_quality": data_quality,
    "data_quality_message": "；".join(quality_messages)
    if quality_messages
    else "历史回放与期末清算完整",
    "skipped_stock_codes": [str(item.get("stock_code", "") or "") for item in skipped],
    "methodology": {
      "forced_liquidation": "回放结束时仅清算回放新增的 T 批次；合法性校验失败时不伪造成交",
      "capital_utilization": (
        "逐批按实际买入资金加权：min(100%, 4小时/持有小时)；"
        "卖出等待越长，资金利用率越低"
      ),
      "capital_utilization_reference_hours": CAPITAL_UTILIZATION_REFERENCE_HOURS,
      "initial_assets": (
        "初始总权益按可用资金、回放持仓市值和恒定非交易资产残差构成；"
        "非交易资产不进入可用现金、持仓或成交撮合。负残差不作为负资产伪造，"
        "而是钳制为 0 并保留质量标记。"
      ),
      "initial_asset_reconciliation": asset_reconciliation,
      "price_limits": (
        "历史行情中的原生涨跌停价优先；缺失时，仅在证券主数据和交易日规则可确认时，"
        "按前收盘价派生，否则保持严格风控拒绝。"
        f"来源统计：原生 {native_limit_events}、派生 {derived_limit_events}、"
        f"缺失 {missing_limit_events} 个行情事件。"
      ),
      "price_limit_policy": price_limit_policy,
      "price_limit_source_counts": price_limit_source_counts,
      "tick_read_audit": tick_read_audit,
    },
    "summary": {
      "initial_equity": initial_equity,
      "reported_initial_equity": reported_initial_equity,
      "non_trading_asset": non_trading_asset,
      "final_equity": final_equity,
      "t_net_profit": t_net_profit,
      "total_return_pct": total_return_pct,
      "passive_final_equity": passive_final,
      "passive_return_pct": passive_return_pct,
      "excess_return_pct": total_return_pct - passive_return_pct,
      "max_drawdown_pct": _number(broker_metrics.get("max_drawdown_pct")),
      "total_fees": total_fees,
      "turnover": turnover,
      "completed_cycles": len(completed),
      "open_cycles": len(open_cycles),
      "natural_exit_cycles": len(completed) - len(forced),
      "forced_exit_cycles": len(forced),
      "liquidation_failed_cycles": liquidation_failed_cycles,
      "winning_cycles": len(winning),
      "win_rate_pct": len(winning) / len(completed) * 100.0 if completed else 0.0,
      "capital_capacity": capital_capacity,
      "average_occupied_capital": usage["average_occupied_capital"],
      "peak_occupied_capital": usage["peak_occupied_capital"],
      "capital_occupancy_pct": capital_occupancy_pct,
      "capital_availability_pct": max(0.0, 100.0 - capital_occupancy_pct),
      "capital_turnover_times": capital_turnover_times,
      "capital_turnover_per_trading_day": capital_turnover_times / trading_day_count,
      "capital_utilization_pct": capital_utilization_pct,
      "average_holding_hours": average_holding_hours,
      "max_holding_hours": max_holding_hours,
      "capital_profit_per_occupied_day_pct": (
        t_net_profit / occupied_capital_days * 100.0
        if occupied_capital_days > 0
        else 0.0
      ),
    },
    "instruments": sorted(per_instrument.values(), key=lambda row: row["stock_code"]),
    "curve": curve,
    "cycles": cycles,
    "liquidation": liquidation,
  }
