"""Result aggregation for account-level T-assistant historical replays."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from math import isfinite
from typing import Any, Dict, Iterable, List, Mapping, Optional

CAPITAL_UTILIZATION_REFERENCE_HOURS = 4.0
OPPORTUNITY_DIAGNOSTICS_SCHEMA_VERSION = 2
_READY_DENOMINATOR_CODE = "READY_INSTRUMENT_SECONDS"
_EXCURSION_UNAVAILABLE_REASON = (
  "当前持久化链缺少权威成交费用账本、成交后的完整因果行情路径和固定窗口交易时段策略，"
  "不能可靠计算费用后 MFE、MAE 或固定窗口收益。"
)
_PERFORMANCE_REQUIRED_DATA_CODES = [
  "AUTHORITATIVE_EXECUTION_FEE_LEDGER",
  "COMPLETE_POST_FILL_CAUSAL_MARKET_PATH",
  "FIXED_WINDOW_SESSION_POLICY",
]


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


def _rollout_evidence(
  parameters: Mapping[str, Any],
  *,
  trading_dates: Iterable[Any],
  data_quality: str,
  tick_read_audit: Mapping[str, Any],
  skipped_instruments: Iterable[Any],
) -> Dict[str, Any]:
  """Return immutable replay facts consumed by the V3 execution gate.

  The payload is intentionally derived only from the completed runtime's
  persisted parameters and replay curve.  A generic replay cannot label
  itself formal evidence: the replay service accepts ``V3_CAUSAL_20D`` only
  for an exact SH 20-day window with an in-window declared abnormal day.
  Missing or malformed facts remain present as explicit ``False`` values so
  older results fail closed instead of being reinterpreted as acceptance.
  """

  actual_dates = sorted(
    {
      item.isoformat()
      for item in trading_dates
      if hasattr(item, "isoformat") and len(str(item.isoformat())) == 10
    }
  )
  acceptance = str(parameters.get("replay_acceptance") or "").strip().upper()
  raw_abnormal = parameters.get("replay_abnormal_dates")
  actual_date_set = set(actual_dates)
  abnormal_items = raw_abnormal if isinstance(raw_abnormal, list) else []
  abnormal_dates = sorted(
    {
      str(item or "").strip()
      for item in abnormal_items
      if str(item or "").strip() in actual_date_set
    }
  )
  normal_dates = sorted(set(actual_dates) - set(abnormal_dates))
  issues = list(tick_read_audit.get("issues") or [])
  strict_causal = (
    acceptance == "V3_CAUSAL_20D"
    and len(actual_dates) == 20
    and bool(normal_dates)
    and bool(abnormal_dates)
    and str(data_quality).upper() == "OK"
    and _integer(tick_read_audit.get("verified_windows")) > 0
    and not issues
    and not list(skipped_instruments)
  )
  return {
    "schema_version": 1,
    "strict_causal": strict_causal,
    "trading_dates": actual_dates,
    "market_scenario_coverage": {
      "normal_trading_dates": normal_dates,
      "abnormal_trading_dates": abnormal_dates,
    },
    "replay_acceptance": acceptance or None,
    "tick_read_issues": len(issues),
    "skipped_instrument_count": len(list(skipped_instruments)),
  }


def _opportunity_diagnostics_unavailable(
  reason: str,
  *,
  reason_code: str,
  strategy_run_id: Optional[str],
) -> Dict[str, Any]:
  return {
    "schema_version": OPPORTUNITY_DIAGNOSTICS_SCHEMA_VERSION,
    "available": False,
    "reason_code": reason_code,
    "reason": reason,
    "scope": {
      "strategy_run_id": strategy_run_id,
      "stock_code": None,
      "start_time": None,
      "end_time": None,
    },
    "merged_versions": False,
    "warnings": [],
    "partitions": [],
    "version_groups": [],
  }


def _normalize_opportunity_diagnostics(
  diagnostics: Optional[Mapping[str, Any]],
  *,
  expected_strategy_run_id: Optional[str],
  unavailable_reason: Optional[str],
) -> Dict[str, Any]:
  normalized_run_id = str(expected_strategy_run_id or "").strip() or None
  if not isinstance(diagnostics, Mapping) or not diagnostics:
    return _opportunity_diagnostics_unavailable(
      unavailable_reason or "回放结果尚未附加按策略运行聚合的 V3 机会诊断。",
      reason_code="NOT_ATTACHED",
      strategy_run_id=normalized_run_id,
    )
  if diagnostics.get("available") is not True:
    return _opportunity_diagnostics_unavailable(
      str(diagnostics.get("reason") or unavailable_reason or "V3 机会诊断不可用。"),
      reason_code=str(diagnostics.get("reason_code") or "DIAGNOSTICS_UNAVAILABLE"),
      strategy_run_id=normalized_run_id,
    )

  scope = dict(diagnostics.get("scope") or {})
  scoped_run_id = str(scope.get("strategy_run_id") or "").strip() or None
  if normalized_run_id and scoped_run_id != normalized_run_id:
    return _opportunity_diagnostics_unavailable(
      "V3 机会诊断未绑定当前策略运行，已拒绝合并以避免跨回放污染。",
      reason_code="STRATEGY_RUN_SCOPE_MISMATCH",
      strategy_run_id=normalized_run_id,
    )

  merged_versions = diagnostics.get("merged_versions")
  raw_warnings = diagnostics.get("warnings")
  raw_partitions = diagnostics.get("partitions")
  if (
    not isinstance(merged_versions, bool)
    or not isinstance(raw_warnings, list)
    or not isinstance(raw_partitions, list)
  ):
    return _opportunity_diagnostics_unavailable(
      "V3 机会诊断缺少版本分区契约，已拒绝按旧扁平口径混算。",
      reason_code="VERSION_PARTITION_CONTRACT_MISSING",
      strategy_run_id=normalized_run_id or scoped_run_id,
    )
  partitions: list[dict[str, Any]] = []
  for raw_partition in raw_partitions:
    if not isinstance(raw_partition, Mapping):
      return _opportunity_diagnostics_unavailable(
        "V3 机会诊断版本分区格式无效。",
        reason_code="INVALID_VERSION_PARTITION",
        strategy_run_id=normalized_run_id or scoped_run_id,
      )
    partition = dict(raw_partition)
    denominator = dict(partition.get("denominator") or {})
    if str(denominator.get("code") or "") != _READY_DENOMINATOR_CODE:
      return _opportunity_diagnostics_unavailable(
        "V3 机会诊断分母不是 READY 标的时长，已拒绝使用原始 Tick 数等错误口径。",
        reason_code="UNSUPPORTED_DENOMINATOR",
        strategy_run_id=normalized_run_id or scoped_run_id,
      )
    ready_seconds = _number(denominator.get("ready_instrument_seconds"), -1.0)
    if not isfinite(ready_seconds) or ready_seconds < 0.0:
      return _opportunity_diagnostics_unavailable(
        "V3 机会诊断缺少有效的 READY 标的时长。",
        reason_code="INVALID_READY_DURATION",
        strategy_run_id=normalized_run_id or scoped_run_id,
      )
    performance = dict(partition.get("post_candidate_performance") or {})
    if performance.get("available") is not True:
      performance = {
        "available": False,
        "reason_code": str(
          performance.get("reason_code")
          or "POST_FILL_CAUSAL_PATH_AND_COST_LEDGER_UNAVAILABLE"
        ),
        "reason": str(performance.get("reason") or _EXCURSION_UNAVAILABLE_REASON),
        "sample_count": 0,
        "net_mfe_pct": None,
        "net_mae_pct": None,
        "fixed_window_returns": [],
        "required_data_codes": list(
          performance.get("required_data_codes") or _PERFORMANCE_REQUIRED_DATA_CODES
        ),
      }
    partitions.append(
      {
        "policy_version": str(partition.get("policy_version") or ""),
        "feature_schema_version": str(partition.get("feature_schema_version") or ""),
        "profile_version": partition.get("profile_version"),
        "denominator": {
          "code": _READY_DENOMINATOR_CODE,
          "label": str(denominator.get("label") or "READY 标的时长（秒）"),
          "ready_instrument_seconds": ready_seconds,
        },
        "funnel": [dict(item) for item in partition.get("funnel") or []],
        "blockers": [dict(item) for item in partition.get("blockers") or []],
        "score_distribution": [
          dict(item) for item in partition.get("score_distribution") or []
        ],
        "fsm_dwell": [dict(item) for item in partition.get("fsm_dwell") or []],
        "fsm_transitions": [
          dict(item) for item in partition.get("fsm_transitions") or []
        ],
        "candidate_outcomes": [
          dict(item) for item in partition.get("candidate_outcomes") or []
        ],
        "post_candidate_performance": performance,
      }
    )
  raw_version_groups = diagnostics.get("version_groups")
  if not isinstance(raw_version_groups, list):
    return _opportunity_diagnostics_unavailable(
      "V3 机会诊断缺少版本分组。",
      reason_code="VERSION_GROUPS_MISSING",
      strategy_run_id=normalized_run_id or scoped_run_id,
    )
  partition_coordinates = {
    (
      item["policy_version"],
      item["feature_schema_version"],
      item["profile_version"],
    )
    for item in partitions
  }
  if any(
    not item["policy_version"] or not item["feature_schema_version"]
    for item in partitions
  ):
    return _opportunity_diagnostics_unavailable(
      "V3 机会诊断版本坐标不完整。",
      reason_code="INVALID_VERSION_COORDINATE",
      strategy_run_id=normalized_run_id or scoped_run_id,
    )
  if not merged_versions and (
    len(partition_coordinates) != len(partitions)
    or any(item["policy_version"] == "MIXED" for item in partitions)
  ):
    return _opportunity_diagnostics_unavailable(
      "V3 机会诊断默认结果未按唯一版本坐标隔离。",
      reason_code="VERSION_PARTITION_MIXED",
      strategy_run_id=normalized_run_id or scoped_run_id,
    )
  if merged_versions and len(partitions) > 1:
    return _opportunity_diagnostics_unavailable(
      "V3 机会诊断显式合并时返回了多个分区。",
      reason_code="INVALID_MERGED_PARTITIONS",
      strategy_run_id=normalized_run_id or scoped_run_id,
    )
  if (
    merged_versions
    and len(raw_version_groups) > 1
    and "MIXED_SIGNAL_VERSIONS_EXPLICITLY_MERGED" not in raw_warnings
  ):
    return _opportunity_diagnostics_unavailable(
      "V3 机会诊断合并不同版本时缺少显式警告。",
      reason_code="MIXED_VERSION_WARNING_MISSING",
      strategy_run_id=normalized_run_id or scoped_run_id,
    )

  return {
    "schema_version": OPPORTUNITY_DIAGNOSTICS_SCHEMA_VERSION,
    "available": True,
    "reason_code": None,
    "reason": None,
    "scope": {
      "strategy_run_id": scoped_run_id or normalized_run_id,
      "stock_code": scope.get("stock_code"),
      "start_time": _timestamp(scope.get("start_time")),
      "end_time": _timestamp(scope.get("end_time")),
    },
    "merged_versions": merged_versions,
    "warnings": [str(item) for item in raw_warnings],
    "partitions": partitions,
    "version_groups": [dict(item) for item in raw_version_groups],
  }


def attach_t_trade_opportunity_diagnostics(
  replay_metrics: Mapping[str, Any],
  diagnostics: Optional[Mapping[str, Any]],
  *,
  expected_strategy_run_id: Optional[str] = None,
  unavailable_reason: Optional[str] = None,
) -> Dict[str, Any]:
  """Return replay metrics with a validated, run-scoped V3 diagnostics payload."""

  result = dict(replay_metrics)
  result["opportunity_diagnostics"] = _normalize_opportunity_diagnostics(
    diagnostics,
    expected_strategy_run_id=expected_strategy_run_id,
    unavailable_reason=unavailable_reason,
  )
  return result


def attach_t_trade_phase_one_baseline(
  replay_metrics: Mapping[str, Any],
  baseline: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
  """Attach the frozen AND-rule control without inventing unavailable results."""

  result = dict(replay_metrics)
  if not isinstance(baseline, Mapping) or baseline.get("available") is not True:
    normalized_baseline: Dict[str, Any] = {
      "schema_version": 1,
      "available": False,
      "reason_code": "PHASE_ONE_BASELINE_NOT_COLLECTED",
      "reason": "本次回放未启用一期固定 AND 规则影子比较器。",
    }
  else:
    normalized_baseline = dict(baseline)
  result["phase_one_baseline"] = normalized_baseline

  diagnostics = dict(result.get("opportunity_diagnostics") or {})
  v3_ready_seconds = 0.0
  v3_candidates = 0
  v3_performance_samples = 0
  v3_performance_available = False
  if diagnostics.get("available") is True:
    for partition in list(diagnostics.get("partitions") or []):
      partition = dict(partition or {})
      denominator = dict(partition.get("denominator") or {})
      v3_ready_seconds += max(
        0.0,
        _number(denominator.get("ready_instrument_seconds")),
      )
      for stage in list(partition.get("funnel") or []):
        if str(dict(stage or {}).get("code") or "") == "CANDIDATE":
          v3_candidates += max(0, _integer(dict(stage or {}).get("count")))
      performance = dict(partition.get("post_candidate_performance") or {})
      if performance.get("available") is True:
        v3_performance_available = True
        v3_performance_samples += max(0, _integer(performance.get("sample_count")))

  baseline_ready_seconds = 0.0
  baseline_candidates = 0
  baseline_reference_samples = 0
  baseline_fee_available = False
  common_ready_seconds = 0.0
  common_v3_candidates = 0
  common_phase_one_candidates = 0
  common_comparison_available = False
  if normalized_baseline.get("available") is True:
    baseline_ready_seconds = max(
      0.0,
      _number(dict(normalized_baseline.get("denominator") or {}).get("value")),
    )
    baseline_candidates = sum(
      max(0, _integer(value))
      for value in dict(normalized_baseline.get("candidate_edges") or {}).values()
    )
    reference = dict(normalized_baseline.get("candidate_reference_performance") or {})
    baseline_reference_samples = max(0, _integer(reference.get("candidate_count")))
    baseline_fee_available = (
      dict(normalized_baseline.get("fee_adjusted_performance") or {}).get("available")
      is True
    )
    common = dict(normalized_baseline.get("common_ready_comparison") or {})
    common_ready_seconds = max(
      0.0,
      _number(dict(common.get("denominator") or {}).get("value")),
    )
    common_v3_candidates = sum(
      max(0, _integer(value))
      for value in dict(common.get("v3_candidate_edges") or {}).values()
    )
    common_phase_one_candidates = sum(
      max(0, _integer(value))
      for value in dict(common.get("phase_one_candidate_edges") or {}).values()
    )
    common_comparison_available = bool(
      common.get("available") is True and common_ready_seconds > 0
    )
  v3_own_rate = _candidate_rate_per_ready_hour(v3_candidates, v3_ready_seconds)
  baseline_own_rate = _candidate_rate_per_ready_hour(
    baseline_candidates,
    baseline_ready_seconds,
  )
  common_v3_rate = _candidate_rate_per_ready_hour(
    common_v3_candidates,
    common_ready_seconds,
  )
  common_phase_one_rate = _candidate_rate_per_ready_hour(
    common_phase_one_candidates,
    common_ready_seconds,
  )
  result["v3_vs_phase_one"] = {
    "available": bool(
      diagnostics.get("available") is True
      and normalized_baseline.get("available") is True
    ),
    "units": {
      "ready_time": "INSTRUMENT_SECONDS",
      "candidate": "RUN_SCOPED_CANDIDATES",
    },
    "v3": {
      "ready_instrument_seconds": v3_ready_seconds,
      "candidate_count": v3_candidates,
      "candidate_rate_per_ready_instrument_hour": v3_own_rate,
      "post_fill_performance_available": v3_performance_available,
      "post_fill_performance_sample_count": v3_performance_samples,
    },
    "phase_one": {
      "data_ready_instrument_seconds": baseline_ready_seconds,
      "candidate_count": baseline_candidates,
      "candidate_rate_per_ready_instrument_hour": baseline_own_rate,
      "candidate_reference_sample_count": baseline_reference_samples,
      "fee_adjusted_performance_available": baseline_fee_available,
    },
    "common_ready": {
      "available": common_comparison_available,
      "ready_instrument_seconds": common_ready_seconds,
      "v3_candidate_count": common_v3_candidates,
      "phase_one_candidate_count": common_phase_one_candidates,
      "v3_candidate_rate_per_ready_instrument_hour": common_v3_rate,
      "phase_one_candidate_rate_per_ready_instrument_hour": common_phase_one_rate,
      "candidate_rate_delta_per_ready_instrument_hour": (
        common_v3_rate - common_phase_one_rate
        if common_v3_rate is not None and common_phase_one_rate is not None
        else None
      ),
      "warning": (
        None
        if common_comparison_available
        else "没有一期与 V3 同时 READY 的连续暴露时段，禁止比较候选频率。"
      ),
    },
    "fee_adjusted_comparison_available": bool(
      v3_performance_available and baseline_fee_available
    ),
    "warning": (
      None
      if v3_performance_available and baseline_fee_available
      else "任一侧缺少权威费用后结果，禁止比较收益优劣。"
    ),
  }
  return result


def _candidate_rate_per_ready_hour(
  candidate_count: int,
  ready_seconds: float,
) -> Optional[float]:
  if ready_seconds <= 0:
    return None
  return max(0, int(candidate_count)) * 3600.0 / ready_seconds


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


def build_t_trade_replay_metrics(
  runtime: Any,
  *,
  opportunity_diagnostics: Optional[Mapping[str, Any]] = None,
  diagnostics_unavailable_reason: Optional[str] = None,
) -> Dict[str, Any]:
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
  configured_reconciliation = dict(params.get("initial_asset_reconciliation") or {})
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
    quality_messages.append(f"{len(tick_read_issues)} 个 Tick 读取窗口未通过完整性校验")
  non_trading_asset = max(0.0, _number(asset_reconciliation.get("non_trading_asset")))
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
    quality_messages.append("初始资产快照质量标记：" + "、".join(source_quality_flags))
  data_quality = "PARTIAL" if quality_messages else "OK"
  rollout_evidence = _rollout_evidence(
    params,
    trading_dates=trading_dates,
    data_quality=data_quality,
    tick_read_audit=tick_read_audit,
    skipped_instruments=skipped,
  )
  metrics = {
    "data_quality": data_quality,
    "data_quality_message": "；".join(quality_messages)
    if quality_messages
    else "历史回放与期末清算完整",
    "skipped_stock_codes": [str(item.get("stock_code", "") or "") for item in skipped],
    "rollout_evidence": rollout_evidence,
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
  metrics_with_diagnostics = attach_t_trade_opportunity_diagnostics(
    metrics,
    opportunity_diagnostics,
    expected_strategy_run_id=str(getattr(runtime, "run_id", "") or "") or None,
    unavailable_reason=diagnostics_unavailable_reason,
  )
  baseline_accumulator = getattr(runtime, "t_trade_phase_one_baseline", None)
  baseline_snapshot = None
  snapshot = getattr(baseline_accumulator, "snapshot", None)
  if callable(snapshot):
    baseline_snapshot = snapshot()
  return attach_t_trade_phase_one_baseline(
    metrics_with_diagnostics,
    baseline_snapshot,
  )


__all__ = [
  "OPPORTUNITY_DIAGNOSTICS_SCHEMA_VERSION",
  "attach_t_trade_opportunity_diagnostics",
  "attach_t_trade_phase_one_baseline",
  "build_t_trade_replay_metrics",
]
