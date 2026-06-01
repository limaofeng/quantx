"""Decision trace records for StrategyInput-to-broker auditability."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class DecisionTrace:
  trace_id: str
  run_id: str
  strategy_id: str
  instrument_code: str
  timestamp: datetime = field(default_factory=datetime.now)
  input_summary: Dict[str, Any] = field(default_factory=dict)
  environment: Dict[str, Any] = field(default_factory=dict)
  risk_caps: Dict[str, Any] = field(default_factory=dict)
  position_profile: Dict[str, Any] = field(default_factory=dict)
  execution_profile: Dict[str, Any] = field(default_factory=dict)
  output_summary: Dict[str, Any] = field(default_factory=dict)
  state_patch: Dict[str, Any] = field(default_factory=dict)
  trade_intents: List[Dict[str, Any]] = field(default_factory=list)
  order_draft: Dict[str, Any] = field(default_factory=dict)
  order_request: Dict[str, Any] = field(default_factory=dict)
  risk_decision: Dict[str, Any] = field(default_factory=dict)
  broker_report: Dict[str, Any] = field(default_factory=dict)
  tags: List[str] = field(default_factory=list)
  reason: str = ""

  @classmethod
  def from_decision(
    cls,
    *,
    run_id: str,
    strategy_id: str,
    instrument_code: str,
    input_summary: Optional[Dict[str, Any]] = None,
    environment: Optional[Dict[str, Any]] = None,
    risk_caps: Optional[Dict[str, Any]] = None,
    position_profile: Optional[Dict[str, Any]] = None,
    execution_profile: Optional[Dict[str, Any]] = None,
    output_summary: Optional[Dict[str, Any]] = None,
    state_patch: Optional[Dict[str, Any]] = None,
    trade_intents: Optional[List[Dict[str, Any]]] = None,
    order_draft: Optional[Dict[str, Any]] = None,
    order_request: Optional[Dict[str, Any]] = None,
    risk_decision: Optional[Dict[str, Any]] = None,
    broker_report: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    reason: str = "",
  ) -> "DecisionTrace":
    return cls(
      trace_id=trace_id or str(uuid.uuid4()),
      run_id=run_id,
      strategy_id=str(strategy_id),
      instrument_code=instrument_code,
      input_summary=dict(input_summary or {}),
      environment=dict(environment or {}),
      risk_caps=dict(risk_caps or {}),
      position_profile=dict(position_profile or {}),
      execution_profile=dict(execution_profile or {}),
      output_summary=dict(output_summary or {}),
      state_patch=dict(state_patch or {}),
      trade_intents=list(trade_intents or []),
      order_draft=dict(order_draft or {}),
      order_request=dict(order_request or {}),
      risk_decision=dict(risk_decision or {}),
      broker_report=dict(broker_report or {}),
      tags=list(tags or []),
      reason=reason,
    )

  def to_dict(self) -> Dict[str, Any]:
    return {
      "_type": "decision_trace",
      "trace_id": self.trace_id,
      "run_id": self.run_id,
      "strategy_id": self.strategy_id,
      "instrument_code": self.instrument_code,
      "timestamp": self.timestamp.isoformat(),
      "input_summary": self.input_summary,
      "environment": self.environment,
      "risk_caps": self.risk_caps,
      "position_profile": self.position_profile,
      "execution_profile": self.execution_profile,
      "output_summary": self.output_summary,
      "state_patch": self.state_patch,
      "trade_intents": self.trade_intents,
      "order_draft": self.order_draft,
      "order_request": self.order_request,
      "risk_decision": self.risk_decision,
      "broker_report": self.broker_report,
      "tags": self.tags,
      "reason": self.reason,
    }


class DecisionTraceLogger:
  """Memory-first trace logger with optional backtest JSONL sink."""

  def __init__(self, max_records: int = 1000) -> None:
    self.max_records = max(1, int(max_records or 1000))
    self.records: List[DecisionTrace] = []

  def record(self, trace: DecisionTrace) -> None:
    self.records.append(trace)
    if len(self.records) > self.max_records:
      self.records = self.records[-self.max_records :]

  def to_list(self) -> List[Dict[str, Any]]:
    return [trace.to_dict() for trace in self.records]

  def get(self, trace_id: str) -> Optional[DecisionTrace]:
    for trace in reversed(self.records):
      if trace.trace_id == trace_id:
        return trace
    return None


def _pick_keys(data: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
  return {
    key: data.get(key)
    for key in keys
    if key in data and data.get(key) is not None
  }


def _compact_market_context(market_context: Dict[str, Any]) -> Dict[str, Any]:
  data = dict(market_context or {})
  compact = _pick_keys(
    data,
    [
      "instrument_code",
      "trade_date",
      "timestamp",
      "market_state",
      "sector_state",
      "concept_heat_state",
      "liquidity_state",
      "breadth_state",
      "volume_structure",
      "context_score",
      "risk_tags",
      "data_quality",
      "state_changed_reason",
      "industry_state",
      "price",
      "close",
      "last_price",
      "source",
      "source_fingerprint",
    ],
  )
  metadata = _pick_keys(
    dict(data.get("metadata") or {}),
    ["source", "bar_key", "data_version", "source_fingerprint"],
  )
  if metadata:
    compact["metadata"] = metadata
  master = _pick_keys(
    dict(data.get("instrument_master") or {}),
    [
      "instrument_code",
      "trading_date",
      "exchange",
      "is_trading_day",
      "suspended",
      "is_st",
      "delist_risk",
      "limit_up",
      "limit_down",
      "data_quality",
      "risk_tags",
    ],
  )
  if master:
    compact["instrument_master"] = master
  return compact


def _compact_risk_caps(risk_caps: Dict[str, Any]) -> Dict[str, Any]:
  return _pick_keys(
    dict(risk_caps or {}),
    [
      "risk_mode",
      "kill_switch_active",
      "max_position_pct",
      "min_cash_buffer_pct",
      "allow_buy",
      "allow_sell",
      "allow_intraday_swing_buy",
      "only_reduce_position",
      "allow_locked_core_substitution",
      "t1_insufficient_action",
      "reason_codes",
      "risk_tags",
    ],
  )


def _compact_position_profile(position_profile: Dict[str, Any]) -> Dict[str, Any]:
  return _pick_keys(
    dict(position_profile or {}),
    [
      "profile",
      "min_position_pct",
      "max_position_pct",
      "target_cash_buffer_pct",
      "allow_core_buy",
      "allow_swing_buy",
      "allow_core_sell",
      "allow_swing_sell",
      "reason_tags",
      "current_position_pct",
      "instrument_code",
    ],
  )


def summarize_strategy_input(input_obj: Any) -> Dict[str, Any]:
  timestamp = getattr(input_obj, "timestamp", None)
  return {
    "input_id": getattr(input_obj, "input_id", None),
    "trace_id": getattr(input_obj, "trace_id", None),
    "run_id": getattr(input_obj, "run_id", None),
    "strategy_id": getattr(input_obj, "strategy_id", None),
    "instrument_code": getattr(input_obj, "instrument_code", None),
    "cadence": getattr(getattr(input_obj, "cadence", None), "value", getattr(input_obj, "cadence", None)),
    "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else timestamp,
    "market_context": _compact_market_context(
      dict(getattr(input_obj, "market_context", {}) or {})
    ),
    "risk_caps": _compact_risk_caps(
      dict(getattr(input_obj, "risk_caps", {}) or {})
    ),
    "position_profile": _compact_position_profile(
      dict(getattr(input_obj, "position_profile", {}) or {})
    ),
  }


def summarize_intent(intent: Any) -> Dict[str, Any]:
  return {
    "intent_id": getattr(intent, "intent_id", None),
    "trace_id": getattr(intent, "trace_id", None),
    "instrument_code": getattr(intent, "instrument_code", None),
    "direction": getattr(getattr(intent, "direction", None), "value", getattr(intent, "direction", None)),
    "bucket": getattr(intent, "bucket", None),
    "reason": getattr(intent, "reason", None),
    "priority": getattr(getattr(intent, "priority", None), "value", getattr(intent, "priority", None)),
    "target_amount": getattr(intent, "target_amount", None),
    "target_position_pct": getattr(intent, "target_position_pct", None),
    "target_volume": getattr(intent, "target_volume", None),
    "limit_price_hint": getattr(intent, "limit_price_hint", None),
    "metadata": dict(getattr(intent, "metadata", {}) or {}),
  }
