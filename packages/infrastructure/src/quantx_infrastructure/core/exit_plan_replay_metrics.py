"""Metrics for single-instrument exit-plan historical replays."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Iterable, Mapping, Optional


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


def _value(value: Any) -> str:
  return str(getattr(value, "value", value) or "")


def _json_value(value: Any) -> Any:
  """Convert replay evidence into values accepted by PostgreSQL JSON."""

  if isinstance(value, (date, datetime)):
    return value.isoformat()
  if isinstance(value, Mapping):
    return {str(key): _json_value(item) for key, item in value.items()}
  if isinstance(value, (list, tuple)):
    return [_json_value(item) for item in value]
  return value


def _return_pct(value: float, initial: float) -> float:
  return (value / initial - 1.0) * 100.0 if initial > 0 else 0.0


def _sell_fee(price: float, volume: int, parameters: Mapping[str, Any]) -> float:
  amount = max(0.0, price) * max(0, volume)
  commission = max(
    _number(parameters.get("minimum_commission"), 5.0),
    amount * _number(parameters.get("commission_rate"), 0.0003),
  )
  return commission + amount * (
    _number(parameters.get("stamp_tax_rate"), 0.0005)
    + _number(parameters.get("transfer_fee_rate"), 0.00001)
  )


def _trade_event(trade: Any, index: int) -> Dict[str, Any]:
  metadata = dict(getattr(trade, "metadata", {}) or {})
  costs = dict(metadata.get("costs") or {})
  fees = _number(getattr(trade, "commission", 0.0))
  if not fees:
    fees = sum(
      _number(costs.get(key)) for key in ("commission", "stamp_tax", "transfer_fee")
    )
  return {
    "sequence": index + 1,
    "timestamp": getattr(trade, "trade_time", None),
    "event_type": "SELL_FILL",
    "rule_id": str(metadata.get("exit_rule_id") or ""),
    "rule_type": str(metadata.get("exit_rule_type") or ""),
    "reason": str(metadata.get("exit_reason") or metadata.get("reason") or ""),
    "price": _number(getattr(trade, "price", 0.0)),
    "volume": _integer(getattr(trade, "volume", 0)),
    "fees": fees,
    "remaining_volume": None,
    "details": metadata,
  }


def _curve_dates(curve: Iterable[Mapping[str, Any]]) -> list[date]:
  return sorted(
    {
      timestamp.date()
      for point in curve
      if isinstance((timestamp := point.get("timestamp")), datetime)
    }
  )


def _horizon_results(
  curve: list[Dict[str, Any]],
  *,
  exit_time: Optional[datetime],
  exit_price: float,
) -> list[Dict[str, Any]]:
  horizons = (1, 3, 5, 10)
  if exit_time is None or exit_price <= 0:
    return [
      {
        "trading_days": days,
        "available": False,
        "market_price": None,
        "return_after_exit_pct": None,
      }
      for days in horizons
    ]
  dates = [item for item in _curve_dates(curve) if item > exit_time.date()]
  result = []
  for days in horizons:
    if len(dates) < days:
      result.append(
        {
          "trading_days": days,
          "available": False,
          "market_price": None,
          "return_after_exit_pct": None,
        }
      )
      continue
    target_date = dates[days - 1]
    points = [
      point
      for point in curve
      if isinstance(point.get("timestamp"), datetime)
      and point["timestamp"].date() == target_date
    ]
    market_price = _number(points[-1].get("market_price")) if points else 0.0
    result.append(
      {
        "trading_days": days,
        "available": market_price > 0,
        "market_price": market_price or None,
        "return_after_exit_pct": (
          (market_price / exit_price - 1.0) * 100.0 if market_price > 0 else None
        ),
      }
    )
  return result


def build_exit_plan_replay_metrics(runtime: Any) -> Dict[str, Any]:
  parameters = dict(runtime.context.parameters or {})
  broker = runtime.broker
  volume = _integer(parameters.get("replay_entry_volume"))
  initial_equity = _number(
    parameters.get("initial_total_asset"), runtime.context.initial_capital
  )
  raw_curve = list(getattr(broker, "replay_curve", []) or [])
  first_hold = (
    _number(raw_curve[0].get("passive_equity")) if raw_curve else initial_equity
  )
  first_price = first_hold / volume if volume > 0 else 0.0
  slippage = _number(parameters.get("slippage_rate"), 0.0001)
  immediate_price = max(0.0, first_price * (1.0 - slippage))
  immediate_value = max(
    0.0,
    immediate_price * volume - _sell_fee(immediate_price, volume, parameters),
  )
  curve: list[Dict[str, Any]] = []
  for point in raw_curve:
    plan_value = _number(point.get("equity"))
    hold_value = _number(point.get("passive_equity"))
    curve.append(
      {
        "timestamp": point.get("timestamp"),
        "plan_value": plan_value,
        "hold_value": hold_value,
        "immediate_sell_value": immediate_value,
        "market_price": hold_value / volume if volume > 0 else 0.0,
        "plan_return_pct": _return_pct(plan_value, initial_equity),
        "hold_return_pct": _return_pct(hold_value, initial_equity),
        "immediate_sell_return_pct": _return_pct(immediate_value, initial_equity),
      }
    )
  trades = [
    trade
    for trade in list(getattr(broker, "trades", []) or [])
    if _value(getattr(trade, "trade_type", "")).upper() in {"SELL", "SELL_SHORT"}
  ]
  events = [_trade_event(trade, index) for index, trade in enumerate(trades)]
  plans = list(runtime.exit_plan_book.plans.values())
  plan = plans[0] if plans else None
  remaining = int(plan.remaining_volume) if plan is not None else volume
  sold_volume = max(0, volume - remaining)
  exit_price = _number(getattr(plan, "exit_avg_price", 0.0)) if plan else 0.0
  exit_time = events[-1]["timestamp"] if events else None
  total_fees = sum(_number(item.get("fees")) for item in events)
  final_plan = _number(curve[-1].get("plan_value")) if curve else initial_equity
  final_hold = _number(curve[-1].get("hold_value")) if curve else initial_equity
  final_immediate = immediate_value or initial_equity
  plan_return = _return_pct(final_plan, initial_equity)
  hold_return = _return_pct(final_hold, initial_equity)
  immediate_return = _return_pct(final_immediate, initial_equity)
  delta_hold = plan_return - hold_return
  if not curve:
    conclusion_code = "NO_MARKET_DATA"
    conclusion = "区间内没有可用 Tick，无法形成卖出效果比较。"
  elif not events:
    conclusion_code = "PLAN_NOT_TRIGGERED"
    conclusion = "区间内计划未形成卖出成交，期末仍按市值计价。"
  elif delta_hold > 0.05:
    conclusion_code = "PLAN_OUTPERFORMED_HOLD"
    conclusion = f"本区间计划较继续持有高 {delta_hold:.2f} 个百分点。"
  elif delta_hold < -0.05:
    conclusion_code = "PLAN_UNDERPERFORMED_HOLD"
    conclusion = f"本区间计划较继续持有低 {abs(delta_hold):.2f} 个百分点。"
  else:
    conclusion_code = "PLAN_MATCHED_HOLD"
    conclusion = "本区间计划与继续持有的期末收益接近。"
  audit = dict(parameters.get("replay_tick_read_audit") or {})
  issues = list(audit.get("issues") or [])
  data_quality = "OK" if curve and not issues else "BLOCKED"
  data_quality_message = (
    "使用完整 Tick 与盘口深度执行公共退出链路"
    if data_quality == "OK"
    else (
      "；".join(str(item.get("reason") or item) for item in issues)
      or "历史 Tick 不完整"
    )
  )
  return _json_value({
    "schema_version": 1,
    "data_quality": data_quality,
    "data_quality_message": data_quality_message,
    "plan_snapshot": parameters.get("exit_plan_replay_template"),
    "origin": parameters.get("exit_plan_replay_origin"),
    "actual_sell_references": list(parameters.get("actual_sell_references") or []),
    "summary": {
      "initial_equity": initial_equity,
      "plan_final_value": final_plan,
      "hold_final_value": final_hold,
      "immediate_sell_final_value": final_immediate,
      "plan_return_pct": plan_return,
      "hold_return_pct": hold_return,
      "immediate_sell_return_pct": immediate_return,
      "excess_vs_hold_pct": delta_hold,
      "excess_vs_immediate_pct": plan_return - immediate_return,
      "sold_volume": sold_volume,
      "remaining_volume": remaining,
      "exit_price": exit_price or None,
      "exit_time": exit_time,
      "total_fees": total_fees,
      "max_drawdown_pct": _number(getattr(broker, "max_drawdown", 0.0)) * 100.0,
      "conclusion_code": conclusion_code,
      "conclusion": conclusion,
    },
    "curve": curve,
    "events": events,
    "post_exit_horizons": _horizon_results(
      curve, exit_time=exit_time, exit_price=exit_price
    ),
  })
