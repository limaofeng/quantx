"""Retire provably expired unsubmitted LIVE candidates; retain broker obligations."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quantx_domain.trading.t_assistant_execution import (
  TAssistantExecutionEvent,
  stable_manifest_hash,
)
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.services.live_entry_dispatch_review import (
  validate_staged_live_entry,
)
from quantx_infrastructure.services.t_allocation_serialization import allocation_time
from quantx_infrastructure.services.trade_intent_intake import (
  trade_intent_initial_material,
)
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified


@dataclass(frozen=True)
class LiveEntryRecoveryResult:
  expired: tuple[str, ...]
  cancelled: tuple[str, ...]
  retained: tuple[str, ...]


async def recover_live_entry_work(db, *, execution_id, now):
  if (
    not db.in_transaction()
    or not isinstance(now, datetime)
    or now.tzinfo is None
    or now.utcoffset() is None
  ):
    raise ValueError("LIVE_ENTRY_RECOVERY_TRANSACTION_AND_TIME_REQUIRED")
  now = now.astimezone(UTC)
  async with db.begin_nested():
    probe = await db.get(TAssistantExecutionRecord, execution_id)
    if probe is None or probe.environment != "LIVE":
      raise ValueError("LIVE_ENTRY_RECOVERY_SOURCE_INVALID")
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
      or head.account_id != source.account_id
      or source.environment != "LIVE"
    ):
      raise ValueError("LIVE_ENTRY_RECOVERY_SOURCE_INVALID")
    intents = list(
      await db.scalars(
        select(TradeIntentRecord)
        .where(
          TradeIntentRecord.owner_type == "T_ASSISTANT_EXECUTION",
          TradeIntentRecord.owner_id == execution_id,
          TradeIntentRecord.environment == "LIVE",
          TradeIntentRecord.direction == "BUY",
          TradeIntentRecord.status.in_(
            ["ALLOCATION_PENDING", "AWAITING_APPROVAL", "EXECUTION_READY"]
          ),
        )
        .order_by(TradeIntentRecord.id)
        .with_for_update()
        .execution_options(populate_existing=True)
      )
    )
    await assert_live_entry_order_bindings(
      db, execution_id=execution_id, account_id=source.account_id
    )
    expired, cancelled, retained = [], [], []
    for intent in intents:
      if intent.account_id != source.account_id:
        raise ValueError("LIVE_ENTRY_RECOVERY_SCOPE_INVALID")
      has_order = bool(intent.order_id or intent.executed_volume)
      for model in (PendingTradeOrder, OrderCorrelation):
        has_order = (
          has_order
          or await db.scalar(select(model).where(model.intent_id == intent.id).limit(1))
          is not None
        )
      if has_order:
        retained.append(intent.id)
        continue
      if (
        max(allocation_time(intent.created_at), allocation_time(intent.updated_at))
        > now
      ):
        raise ValueError("LIVE_ENTRY_RECOVERY_FUTURE_INTENT")
      cycle = await db.get(TAssistantDecisionCycleRecord, intent.allocation_cycle_id)
      if (
        cycle is None
        or cycle.execution_id != execution_id
        or cycle.status != "PROPOSALS_COMMITTED"
        or stable_manifest_hash(cycle.output_manifest) != cycle.output_manifest_hash
        or allocation_time(cycle.created_at) > now
      ):
        raise ValueError("LIVE_ENTRY_RECOVERY_CYCLE_INVALID")
      refs = [
        v
        for v in cycle.output_manifest["accepted_intents"]
        if v["intent_id"] == intent.id
      ]
      if len(refs) != 1 or refs[0]["intake_hash"] != stable_manifest_hash(
        trade_intent_initial_material(intent)
      ):
        raise ValueError("LIVE_ENTRY_RECOVERY_INTAKE_CHANGED")
      metadata = intent.intent_metadata
      try:
        created = datetime.fromisoformat(metadata["intent_created_at"])
        source_ms, ttl = metadata["source_time_ms"], metadata["approval_ttl_ms"]
        if (
          created.tzinfo is None
          or type(source_ms) is not int
          or source_ms < 0
          or type(ttl) is not int
          or ttl <= 0
        ):
          raise ValueError("invalid clocks")
        deadline = min(
          created, datetime.fromtimestamp(source_ms / 1000, UTC)
        ) + timedelta(milliseconds=ttl)
      except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("LIVE_ENTRY_RECOVERY_TTL_INVALID") from exc
      target, reason = (
        ("EXPIRED", "T_INTENT_EXPIRED") if now >= deadline else (None, None)
      )
      request = metadata.get("risk_increase_order_request")
      if target is None and request is not None:
        try:
          await validate_staged_live_entry(
            db,
            intent=intent,
            volume=request["volume"],
            limit_price=Decimal(request["limit_price"]),
            now=now,
          )
        except ValueError as exc:
          if str(exc) != "LIVE_ENTRY_STAGED_REVIEW_EXPIRED":
            raise
          target, reason = "CANCELLED", "LIVE_ENTRY_REVIEW_REBUILD_REQUIRED"
      if target is None:
        continue
      batch = None
      if intent.admission_batch_id:
        batch = await db.get(
          AccountRiskIncreaseAdmissionBatch,
          intent.admission_batch_id,
          with_for_update=True,
          populate_existing=True,
        )
        if (
          batch is None
          or batch.account_id != source.account_id
          or batch.environment != "LIVE"
        ):
          raise ValueError("LIVE_ENTRY_RECOVERY_ADMISSION_SCOPE_INVALID")
        if batch.status == "COMMITTED" or (
          batch.status == "PREPARED"
          and batch.processing_lease_until
          and allocation_time(batch.processing_lease_until) > now
        ):
          retained.append(intent.id)
          continue
      previous = intent.status
      if batch is not None and batch.status == "PREPARED":
        batch.status = (
          "EXPIRED" if allocation_time(batch.expires_at) <= now else "SUPERSEDED"
        )
        batch.terminal_reason = reason
        batch.processing_owner = batch.processing_fence_token = (
          batch.processing_lease_until
        ) = None
      intent.status = target
      intent.updated_at = now.replace(tzinfo=None)
      flag_modified(intent, "updated_at")
      await TAssistantExecutionRepository(db).append_event(
        TAssistantExecutionEvent(
          execution_id,
          f"live-entry-retired:{intent.id}",
          "LIVE_ENTRY_RETIRED",
          now,
          {
            "intent_id": intent.id,
            "previous_status": previous,
            "outcome": target,
            "reason": reason,
            "allocation_decision_id": intent.allocation_decision_id,
            "admission_batch_id": intent.admission_batch_id,
          },
        )
      )
      (expired if target == "EXPIRED" else cancelled).append(intent.id)
    await db.flush()
    return LiveEntryRecoveryResult(tuple(expired), tuple(cancelled), tuple(retained))


async def recover_account_live_entries(db, *, account_id, now):
  owners = list(
    await db.scalars(
      select(TradeIntentRecord.owner_id)
      .where(
        TradeIntentRecord.account_id == account_id,
        TradeIntentRecord.owner_type == "T_ASSISTANT_EXECUTION",
        TradeIntentRecord.environment == "LIVE",
        TradeIntentRecord.direction == "BUY",
        TradeIntentRecord.status.in_(
          ["ALLOCATION_PENDING", "AWAITING_APPROVAL", "EXECUTION_READY"]
        ),
      )
      .distinct()
      .order_by(TradeIntentRecord.owner_id)
    )
  )
  return tuple(
    [await recover_live_entry_work(db, execution_id=owner, now=now) for owner in owners]
  )


async def assert_live_entry_order_bindings(db, *, execution_id, account_id):
  """An ambiguous durable order prevents any local retirement of its source."""
  clients, outbox_clients = set(), set()
  for model in (PendingTradeOrder, OrderCorrelation, TradeCommandOutbox):
    records = await db.scalars(
      select(model).where(
        model.owner_type == "T_ASSISTANT_EXECUTION", model.owner_id == execution_id
      )
    )
    for row in records:
      if model is not TradeCommandOutbox and not row.intent_id:
        raise ValueError("LIVE_ENTRY_RECOVERY_ORDER_INTENT_MISSING")
      if row.account_id != account_id or row.environment != "LIVE":
        raise ValueError("LIVE_ENTRY_RECOVERY_ORDER_SCOPE_INVALID")
      (outbox_clients if model is TradeCommandOutbox else clients).add(
        row.client_order_id
      )
  if outbox_clients - clients:
    raise ValueError("LIVE_ENTRY_RECOVERY_ORPHAN_OUTBOX")
