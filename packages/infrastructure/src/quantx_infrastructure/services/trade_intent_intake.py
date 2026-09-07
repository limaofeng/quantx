"""Canonical standard TradeIntent record projection shared by all intake adapters."""

import math
from typing import Any, Dict, Optional

from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef, ExecutionOwnerType

_TRADE_INTENT_OWNER_METADATA_KEYS = frozenset(
  {
    "owner_type",
    "owner_id",
    "environment",
    "execution_environment",
    "execution_owner_type",
    "execution_owner_id",
    "source_execution_owner_type",
    "source_execution_owner_id",
    "strategy_run_id",
  }
)


def trade_intent_record_data(
  intent, *, status: str, environment: ExecutionEnvironment
) -> Dict[str, Any]:
  raw_metadata = dict(getattr(intent, "metadata", {}) or {})
  metadata = {
    key: value
    for key, value in raw_metadata.items()
    if str(key).strip().lower() not in _TRADE_INTENT_OWNER_METADATA_KEYS
  }
  execution_ref = getattr(intent, "execution_ref", None)
  if not isinstance(execution_ref, ExecutionOwnerRef):
    raise ValueError("交易意图必须携带强类型执行归属")
  owner_type = execution_ref.owner_type.value
  owner_id = execution_ref.owner_id
  if execution_ref.owner_type is ExecutionOwnerType.STRATEGY_RUN:
    strategy_run_id = owner_id
  else:
    strategy_run_id = None
  origin = getattr(intent, "origin", None)
  origin_type = _enum_value(
    getattr(origin, "origin_type", execution_ref.owner_type.value)
  )
  if execution_ref.owner_type is ExecutionOwnerType.MANUAL_COMMAND:
    metadata.setdefault("manual_action_type", getattr(origin, "action_type", ""))
    metadata.setdefault(
      "liquidation_group_id",
      getattr(origin, "liquidation_group_id", None),
    )
  elif origin is not None:
    metadata.setdefault("plan_id", getattr(origin, "plan_id", None))
  metadata.setdefault("origin_type", origin_type)
  metadata.setdefault(
    "execution_mode", _enum_value(getattr(intent, "execution_mode", "AUTO"))
  )
  metadata.setdefault("approval_ttl_ms", getattr(intent, "approval_ttl_ms", None))
  metadata.setdefault("expiry_policy", dict(getattr(intent, "expiry_policy", {}) or {}))
  metadata.setdefault(
    "max_price_deviation_bps",
    getattr(intent, "max_price_deviation_bps", None),
  )
  created_at = getattr(intent, "created_at", None)
  if created_at is not None and hasattr(created_at, "isoformat"):
    metadata.setdefault("intent_created_at", created_at.isoformat())
  return {
    "id": str(getattr(intent, "intent_id", "") or ""),
    "strategy_run_id": strategy_run_id,
    "owner_type": owner_type,
    "owner_id": owner_id,
    "environment": ExecutionEnvironment(environment).value,
    "idempotency_key": str(
      raw_metadata.get("idempotency_key")
      or f"intent:{owner_type}:{owner_id}:{getattr(intent, 'intent_id', '')}"
    ),
    "account_id": str(metadata.get("account_id") or "").strip() or None,
    "strategy_id": str(getattr(intent, "strategy_id", "") or ""),
    "instrument_code": str(getattr(intent, "instrument_code", "") or ""),
    "direction": _enum_value(getattr(intent, "direction", "")),
    "bucket": str(getattr(intent, "bucket", "") or "core"),
    "reason": str(getattr(intent, "reason", "") or ""),
    "priority": _enum_value(getattr(intent, "priority", "NORMAL")),
    "intent_type": _enum_value(getattr(intent, "intent_type", None)),
    "confidence": _finite_float(getattr(intent, "confidence", 1.0)),
    "target_amount": _finite_float(getattr(intent, "target_amount", None)),
    "target_position_pct": _finite_float(getattr(intent, "target_position_pct", None)),
    "target_volume": getattr(intent, "target_volume", None),
    "limit_price_hint": _finite_float(getattr(intent, "limit_price_hint", None)),
    "trace_id": getattr(intent, "trace_id", None),
    "status": status,
    "metadata": metadata,
    "notes": metadata.get("notes"),
  }


def db_trade_intent_payload(data: Dict[str, Any]) -> Dict[str, Any]:
  allowed = {
    "id",
    "strategy_run_id",
    "owner_type",
    "owner_id",
    "environment",
    "idempotency_key",
    "account_id",
    "strategy_id",
    "instrument_code",
    "direction",
    "bucket",
    "reason",
    "priority",
    "intent_type",
    "confidence",
    "target_amount",
    "target_position_pct",
    "target_volume",
    "limit_price_hint",
    "trace_id",
    "risk_decision_id",
    "order_id",
    "status",
    "executed_price",
    "executed_volume",
    "executed_time",
    "metadata",
    "notes",
  }
  payload = {key: data.get(key) for key in allowed if key in data}
  payload.setdefault("metadata", {})
  return payload


def _enum_value(value: Any) -> Optional[str]:
  return None if value is None else str(getattr(value, "value", value))


def _finite_float(value: Any) -> float | None:
  """Match Float persistence so an exact DB round trip keeps the intake hash."""
  if value is None:
    return None
  result = float(value)
  if isinstance(value, bool) or not math.isfinite(result):
    raise ValueError("TRADE_INTENT_NUMERIC_VALUE_INVALID")
  return result


T_INTENT_PRODUCER_METADATA_KEYS = (
  "source_execution_ref",
  "instrument_code",
  "candidate_id",
  "candidate_fingerprint",
  "policy_version",
  "feature_schema_version",
  "source_time_ms",
  "tick_ordinal",
  "opportunity_score",
  "requested_entry_amount",
  "t_trade_role",
  "t_batch_id",
  "exit_plan_id",
  "exit_plan_template",
  "origin_type",
  "plan_id",
  "execution_mode",
  "approval_ttl_ms",
  "expiry_policy",
  "max_price_deviation_bps",
  "intent_created_at",
)
_T_INTENT_MATERIAL_FIELDS = (
  "id",
  "strategy_run_id",
  "owner_type",
  "owner_id",
  "environment",
  "idempotency_key",
  "account_id",
  "strategy_id",
  "instrument_code",
  "direction",
  "bucket",
  "reason",
  "priority",
  "intent_type",
  "confidence",
  "target_amount",
  "target_position_pct",
  "target_volume",
  "limit_price_hint",
  "trace_id",
  "allocation_cycle_id",
)


def trade_intent_material_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
  """Immutable producer evidence; execution annotations are a separate concern."""
  material = {key: payload.get(key) for key in _T_INTENT_MATERIAL_FIELDS}
  for key in ("confidence", "target_amount", "target_position_pct", "limit_price_hint"):
    material[key] = _finite_float(material[key])
  metadata = dict(payload.get("metadata") or {})
  material.update(
    metadata={key: metadata.get(key) for key in T_INTENT_PRODUCER_METADATA_KEYS},
    status="ALLOCATION_PENDING",
    allocation_version=0,
  )
  return material


def trade_intent_initial_material(record: Any) -> Dict[str, Any]:
  """Recover original standard T intent evidence without later risk/order traces."""
  payload = {key: getattr(record, key) for key in _T_INTENT_MATERIAL_FIELDS}
  payload["metadata"] = record.intent_metadata
  return trade_intent_material_from_payload(payload)
