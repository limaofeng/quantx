"""Consume exact LIVE confirmation evidence into a new allocation request.

No order or permission to bypass allocation is produced. The caller owns the
transaction; execution fencing serializes confirmation, allocation and drain.
"""

import hmac
from datetime import UTC, datetime, timedelta

from quantx_domain.trading.t_assistant_execution import TAssistantExecutionEvent
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from quantx_infrastructure.core.utils.time_utils import to_utc
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.services.exit_plan_authorization_service import (
  T_TRADE_ENTRY_APPROVAL_ACTION,
  T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY,
  build_t_trade_entry_exit_authorization_envelope,
  trade_confirmation_payload_fingerprint,
)
from quantx_infrastructure.services.t_allocation_serialization import allocation_time
from quantx_infrastructure.services.trade_confirmation_material import (
  intent_fingerprint,
)


def _event_key(intent_id):
  return f"live-entry-confirmed:{intent_id}"


async def entry_confirmation_event(db, intent):
  return await db.scalar(
    select(TAssistantExecutionEventRecord).where(
      TAssistantExecutionEventRecord.execution_id == intent.owner_id,
      TAssistantExecutionEventRecord.event_key == _event_key(intent.id),
    )
  )


def validate_entry_challenge(
  challenge, intent, *, now, actor_id=None, device_session_id=None, verify_material=True
):
  if challenge is None or challenge.consumed_at is None:
    raise ValueError("T_ENTRY_CONSUMED_CHALLENGE_REQUIRED")
  payload = dict(challenge.payload or {})
  expected = {
    "action": T_TRADE_ENTRY_APPROVAL_ACTION,
    "user_id": challenge.user_id,
    "device_session_id": challenge.device_session_id,
    "account_id": intent.account_id,
    "owner_type": "T_ASSISTANT_EXECUTION",
    "owner_id": intent.owner_id,
    "environment": "LIVE",
    "intent_id": intent.id,
  }
  if (
    intent.owner_type != "T_ASSISTANT_EXECUTION"
    or intent.environment != "LIVE"
    or intent.direction != "BUY"
    or intent.strategy_run_id
    or not challenge.user_id
    or not challenge.device_session_id
    or (actor_id is not None and challenge.user_id != actor_id)
    or (
      device_session_id is not None and challenge.device_session_id != device_session_id
    )
    or any(payload.get(key) != value for key, value in expected.items())
    or any(
      getattr(challenge, key) != value
      for key, value in expected.items()
      if key != "intent_id"
    )
  ):
    raise ValueError("T_ENTRY_CHALLENGE_CONTEXT_MISMATCH")
  if not hmac.compare_digest(
    str(challenge.payload_fingerprint or ""),
    trade_confirmation_payload_fingerprint(payload),
  ):
    raise ValueError("T_ENTRY_CHALLENGE_TAMPERED")
  consumed = to_utc(challenge.consumed_at)
  if consumed > now or consumed >= to_utc(challenge.expires_at):
    raise ValueError("T_ENTRY_CHALLENGE_TIME_INVALID")
  if not verify_material:
    return consumed
  if payload.get("intent_fingerprint") != intent_fingerprint(
    intent, status="AWAITING_APPROVAL"
  ):
    raise ValueError("T_ENTRY_CONFIRMED_MATERIAL_CHANGED")
  envelope = build_t_trade_entry_exit_authorization_envelope(intent)
  if payload.get(T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY) != {
    "subject": envelope.subject,
    "fingerprint": envelope.fingerprint,
  }:
    raise ValueError("T_ENTRY_EXIT_AUTHORIZATION_CHANGED")
  return consumed


async def confirmed_for_allocation(db, *, intent, snapshot, now):
  """Require newly read account/obligation facts after a durable confirmation."""
  event = await entry_confirmation_event(db, intent)
  if event is None:
    return False
  evidence = event.payload
  if (
    event.event_type != "LIVE_ENTRY_CONFIRMED"
    or evidence.get("intent_id") != intent.id
    or type(evidence.get("allocation_version")) is not int
    or evidence["allocation_version"] > intent.allocation_version
  ):
    raise ValueError("T_ENTRY_CONFIRMATION_EVIDENCE_INVALID")
  challenge = await db.get(TradeConfirmationChallenge, evidence.get("challenge_id"))
  validate_entry_challenge(challenge, intent, now=now)
  confirmed_at = allocation_time(event.occurred_at)
  if (
    confirmed_at > now
    or min(
      snapshot.cut.as_of,
      snapshot.cut.account_snapshot_as_of,
      snapshot.cut.obligations_as_of,
    )
    < confirmed_at
  ):
    raise ValueError("T_ENTRY_POST_CONFIRMATION_SNAPSHOT_REQUIRED")
  return True


async def confirm_live_entry(
  db,
  *,
  execution_id: str,
  intent_id: str,
  account_id: str,
  approval_audit: dict,
  now: datetime,
):
  if not db.in_transaction():
    raise ValueError("T_ENTRY_CALLER_TRANSACTION_REQUIRED")
  if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
    raise ValueError("T_ENTRY_AWARE_TIME_REQUIRED")
  now = allocation_time(now)
  audit = dict(approval_audit or {})
  if any(
    not isinstance(audit.get(key), str) or not audit[key].strip()
    for key in ("challenge_id", "actor_id", "device_session_id")
  ):
    raise ValueError("T_ENTRY_CONFIRMATION_AUDIT_REQUIRED")
  async with db.begin_nested():
    probe = await db.get(TAssistantExecutionRecord, execution_id)
    if probe is None:
      raise ValueError("T_ENTRY_SOURCE_INVALID")
    head = await db.get(
      TTradeGlobalConfig, probe.config_id, with_for_update=True, populate_existing=True
    )
    source = await db.get(
      TAssistantExecutionRecord,
      execution_id,
      with_for_update=True,
      populate_existing=True,
    )
    if (
      head is None
      or source.account_id != account_id
      or head.account_id != account_id
      or source.environment != "LIVE"
      or source.entry_authorization != "MANUAL_CONFIRM"
    ):
      raise ValueError("T_ENTRY_SOURCE_INVALID")
    intent = await db.get(
      TradeIntentRecord, intent_id, with_for_update=True, populate_existing=True
    )
    if (
      intent is None
      or intent.owner_id != execution_id
      or intent.account_id != account_id
    ):
      raise ValueError("T_ENTRY_INTENT_SCOPE_INVALID")
    challenge = await db.get(
      TradeConfirmationChallenge,
      audit["challenge_id"],
      with_for_update=True,
      populate_existing=True,
    )
    existing = await entry_confirmation_event(db, intent)
    validate_entry_challenge(
      challenge,
      intent,
      now=now,
      actor_id=audit["actor_id"],
      device_session_id=audit["device_session_id"],
      verify_material=existing is None,
    )
    if existing is not None:
      if (
        existing.event_type != "LIVE_ENTRY_CONFIRMED"
        or existing.payload.get("intent_id") != intent.id
        or existing.payload.get("challenge_id") != challenge.id
        or allocation_time(existing.occurred_at) > now
      ):
        raise ValueError("T_ENTRY_ALREADY_CONFIRMED")
      return {
        "intent_id": intent.id,
        "challenge_id": challenge.id,
        "outcome": "REALLOCATION_REQUESTED",
      }
    if (
      source.status != "RUNNING"
      or source.entry_readiness != "READY"
      or not head.enabled
      or head.desired_environment != "LIVE"
      or head.active_config_version_id != source.config_version_id
    ):
      raise ValueError("T_ENTRY_SOURCE_NOT_READY")
    if (
      intent.status != "AWAITING_APPROVAL"
      or not intent.allocation_cycle_id
      or not intent.allocation_decision_id
      or intent.allocation_version < 1
      or intent.order_id
      or intent.executed_volume
      or intent.admission_batch_id
    ):
      raise ValueError("T_ENTRY_NOT_AWAITING_CONFIRMATION")
    metadata = intent.intent_metadata
    created = datetime.fromisoformat(metadata["intent_created_at"])
    source_ms, ttl_ms = metadata["source_time_ms"], metadata["approval_ttl_ms"]
    if (
      created.tzinfo is None
      or type(source_ms) is not int
      or source_ms < 0
      or type(ttl_ms) is not int
      or ttl_ms <= 0
    ):
      raise ValueError("T_ENTRY_INTENT_TIME_INVALID")
    started = datetime.fromtimestamp(source_ms / 1000, UTC)
    if (
      now < created
      or now < allocation_time(intent.updated_at)
      or now < allocation_time(source.entry_readiness_as_of)
      or now >= min(created, started) + timedelta(milliseconds=ttl_ms)
    ):
      raise ValueError("T_ENTRY_INTENT_EXPIRED_OR_FUTURE")
    for model in (PendingTradeOrder, OrderCorrelation):
      if await db.scalar(select(model).where(model.intent_id == intent.id).limit(1)):
        raise ValueError("T_ENTRY_ORDER_ALREADY_EXISTS")
    await TAssistantExecutionRepository(db).append_event(
      TAssistantExecutionEvent(
        execution_id,
        _event_key(intent.id),
        "LIVE_ENTRY_CONFIRMED",
        now,
        {
          "intent_id": intent.id,
          "challenge_id": challenge.id,
          "allocation_version": intent.allocation_version,
          "previous_allocation_decision_id": intent.allocation_decision_id,
        },
      )
    )
    intent.status = "ALLOCATION_PENDING"
    intent.updated_at = now.replace(tzinfo=None)
    flag_modified(intent, "updated_at")
    await db.flush()
    return {
      "intent_id": intent.id,
      "challenge_id": challenge.id,
      "outcome": "REALLOCATION_REQUESTED",
    }
