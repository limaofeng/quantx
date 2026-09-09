"""Identical intent material for API preview and Engine confirmation verification."""

import hashlib
import json
from typing import Any

from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord


def intent_subject_payload(
  record: TradeIntentRecord, *, status: str | None = None
) -> dict[str, Any]:
  metadata = dict(record.intent_metadata or {})
  # The strategy-backed approval path still uses this slot because the Engine
  # validates the consumed challenge audit from the intent snapshot.  Owner
  # identity is always taken from durable columns and supplied explicitly by
  # the caller; metadata is excluded from owner resolution.
  metadata.pop("mobile_trade_approval_challenge_v1", None)
  if record.owner_type == "T_ASSISTANT_EXECUTION":
    # Final transport staging follows confirmation. Economic fields above and
    # all producer metadata remain bound; the final command validates its scope.
    metadata.pop("risk_increase_order_request", None)
  return {
    "id": record.id,
    "run_id": record.strategy_run_id,
    "owner_type": record.owner_type,
    "owner_id": record.owner_id,
    "environment": record.environment,
    "account_id": record.account_id,
    "instrument_code": record.instrument_code,
    "direction": record.direction,
    "bucket": record.bucket,
    "reason": record.reason,
    "status": record.status if status is None else status,
    "confidence": record.confidence,
    "target_amount": record.target_amount,
    "target_position_pct": record.target_position_pct,
    "target_volume": record.target_volume,
    "limit_price_hint": record.limit_price_hint,
    "metadata": metadata,
  }


def intent_fingerprint(record: TradeIntentRecord, *, status: str | None = None) -> str:
  encoded = json.dumps(
    intent_subject_payload(record, status=status),
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
    default=str,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()
