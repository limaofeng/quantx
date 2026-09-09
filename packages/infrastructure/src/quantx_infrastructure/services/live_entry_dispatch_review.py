"""Prove a staged LIVE request still has exact, fresh review evidence."""

from datetime import datetime
from decimal import Decimal

from quantx_domain.brokers.base import OrderType, PriceType
from quantx_domain.trading.risk_checker import RiskAction
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from sqlalchemy import select

from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
)
from quantx_infrastructure.services.live_entry_execution_review import (
  LiveEntryReviewResult,
)
from quantx_infrastructure.services.t_allocation_serialization import allocation_time


async def validate_staged_live_entry(db, *, intent, volume, limit_price, now):
  if not db.in_transaction():
    raise ValueError("LIVE_ENTRY_DISPATCH_TRANSACTION_REQUIRED")
  if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
    raise ValueError("LIVE_ENTRY_DISPATCH_AWARE_TIME_REQUIRED")
  request = dict(intent.intent_metadata or {}).get("risk_increase_order_request")
  if not isinstance(request, dict):
    raise ValueError("LIVE_ENTRY_STAGED_REQUEST_REQUIRED")
  key = dict(request.get("request_metadata") or {}).get("live_entry_review_event_key")
  event = await db.scalar(
    select(TAssistantExecutionEventRecord).where(
      TAssistantExecutionEventRecord.execution_id == intent.owner_id,
      TAssistantExecutionEventRecord.event_key == key,
    )
  )
  payload = dict(event.payload or {}) if event else {}
  evidence = payload.get("input")
  if (
    event is None
    or event.event_type != "LIVE_ENTRY_REVIEWED"
    or payload.get("intent_id") != intent.id
    or payload.get("outcome") != "REVIEWED"
    or payload.get("request") != request
    or not isinstance(evidence, dict)
    or key != f"live-entry-review:{intent.id}:{stable_manifest_hash(evidence)}"
    or evidence.get("allocation_decision_id") != intent.allocation_decision_id
    or evidence.get("allocation_version") != intent.allocation_version
    or request.get("intent_id") != intent.id
    or request.get("owner_id") != intent.owner_id
    or request.get("owner_type") != "T_ASSISTANT_EXECUTION"
    or request.get("environment") != "LIVE"
    or request.get("account_id") != intent.account_id
    or request.get("instrument_code") != intent.instrument_code
    or request.get("volume") != volume
    or Decimal(str(request.get("limit_price"))) != limit_price
  ):
    raise ValueError("LIVE_ENTRY_STAGED_REVIEW_CONFLICT")
  try:
    gate = evidence["gate"]
    tick = gate["latest_tick"]
    clocks = (
      gate["evaluated_at_ms"],
      tick["received_at_ms"],
      tick["sample"]["source_time_ms"],
    )
    max_age = gate["policy"]["quote_max_age_ms"]
    expiry = min(gate["intent_expires_at_ms"], gate["candidate"]["expires_at_ms"])
    current_ms = int(now.timestamp() * 1000)
    if (
      not all(type(value) is int for value in (*clocks, max_age, expiry))
      or max_age <= 0
    ):
      raise ValueError("invalid review clocks")
  except (KeyError, TypeError, ValueError) as exc:
    raise ValueError("LIVE_ENTRY_STAGED_REVIEW_INVALID") from exc
  if max(clocks) > current_ms or allocation_time(event.occurred_at) > now:
    raise ValueError("LIVE_ENTRY_STAGED_REVIEW_FUTURE")
  if current_ms >= expiry or current_ms - min(clocks) > max_age:
    raise ValueError("LIVE_ENTRY_STAGED_REVIEW_EXPIRED")
  return request


async def revalidate_live_entry_dispatch(
  db, *, intent, volume, limit_price, now, fresh_review
):
  request = await validate_staged_live_entry(
    db, intent=intent, volume=volume, limit_price=limit_price, now=now
  )
  if fresh_review is None:
    raise ValueError("LIVE_ENTRY_FRESH_REVIEW_REQUIRED")
  result = await fresh_review(
    execution_id=intent.owner_id, intent_id=intent.id, now=now
  )
  if (
    not isinstance(result, LiveEntryReviewResult)
    or result.outcome != "REVIEWED"
    or result.request is None
    or result.risk is None
    or not result.risk.allowed
    or result.risk.action not in {RiskAction.ALLOW, RiskAction.CAP}
    or result.request.order_type is not OrderType.BUY
    or result.request.price_type is not PriceType.LIMIT
    or result.user_id != request.get("user_id")
    or result.request.environment.value != "LIVE"
    or result.request.execution_ref.owner_type.value != "T_ASSISTANT_EXECUTION"
    or result.request.execution_ref.owner_id != intent.owner_id
    or result.request.instrument_code != intent.instrument_code
    or Decimal(str(result.request.price)) != limit_price
    or result.request.volume < volume
    or result.risk.final_volume < volume
  ):
    raise ValueError("LIVE_ENTRY_FRESH_REVIEW_REJECTED")
  return result
