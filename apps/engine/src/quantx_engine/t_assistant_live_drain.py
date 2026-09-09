"""Drain a LIVE source without inventing broker cancellations or acknowledgments."""

from dataclasses import dataclass
from datetime import UTC, datetime

from quantx_application.t_trade_v3.execution_use_cases import (
  TAssistantExecutionLifecycle,
)
from quantx_domain.trading.t_assistant_execution import (
  TAssistantExecutionEvent,
  TAssistantExecutionStatus,
)
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import select

_UNSUBMITTED = frozenset(
  {"ALLOCATION_PENDING", "AWAITING_APPROVAL", "EXECUTION_READY", "APPROVED", "PENDING"}
)
_TERMINAL = frozenset({"FILLED", "CANCELLED", "REJECTED", "EXPIRED"})


@dataclass(frozen=True)
class LiveEntryDrainResult:
  cancelled_intent_ids: tuple[str, ...]
  retained_intent_ids: tuple[str, ...]
  retained_client_order_ids: tuple[str, ...]


async def drain_live_entry_work(
  db, *, execution_id: str, now: datetime, reason: str
) -> LiveEntryDrainResult:
  """Caller commits this frame; no network, broker, or ExitPlan mutation.

  Head/execution locks fence new routing. Any durable order identity is retained,
  even when its local status looks terminal: only receipt convergence can resolve
  broker obligations. This result deliberately does not grant STOPPED or successor
  readiness. Repeated calls recheck facts and remain safe after restart.
  """
  if not db.in_transaction():
    raise ValueError("LIVE_DRAIN_CALLER_TRANSACTION_REQUIRED")
  if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
    raise ValueError("LIVE_DRAIN_AWARE_TIME_REQUIRED")
  if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
    raise ValueError("LIVE_DRAIN_REASON_REQUIRED")
  now = now.astimezone(UTC)
  async with db.begin_nested():
    probe = await db.get(TAssistantExecutionRecord, execution_id)
    if probe is None or probe.environment != "LIVE":
      raise ValueError("LIVE_DRAIN_EXECUTION_SCOPE_INVALID")
    head = await db.get(
      TTradeGlobalConfig, probe.config_id, with_for_update=True, populate_existing=True
    )
    record = await db.get(
      TAssistantExecutionRecord,
      execution_id,
      with_for_update=True,
      populate_existing=True,
    )
    if (
      head is None
      or head.account_id != record.account_id
      or record.environment != "LIVE"
    ):
      raise ValueError("LIVE_DRAIN_EXECUTION_SCOPE_INVALID")
    repository = TAssistantExecutionRepository(db)
    execution = await repository.get_domain(execution_id)
    if execution.status not in {
      TAssistantExecutionStatus.RUNNING,
      TAssistantExecutionStatus.WARMING,
      TAssistantExecutionStatus.DRAINING,
      TAssistantExecutionStatus.RECONCILE_REQUIRED,
    }:
      raise ValueError("LIVE_DRAIN_EXECUTION_STATUS_INVALID")
    if now < execution.readiness.as_of or (
      execution.started_at and now < execution.started_at
    ):
      raise ValueError("LIVE_DRAIN_FUTURE_EVIDENCE")

    async def owned(model):
      return list(
        (
          await db.scalars(
            select(model)
            .where(
              model.owner_type == "T_ASSISTANT_EXECUTION",
              model.owner_id == execution_id,
            )
            .order_by(*model.__table__.primary_key.columns)
            .with_for_update()
            .execution_options(populate_existing=True)
          )
        ).all()
      )

    intents = await owned(TradeIntentRecord)
    pending = await owned(PendingTradeOrder)
    correlations = await owned(OrderCorrelation)
    outbox = await owned(TradeCommandOutbox)
    for row in [*intents, *pending, *correlations, *outbox]:
      if row.account_id != execution.account_id or row.environment != "LIVE":
        raise ValueError("LIVE_DRAIN_FACT_SCOPE_INVALID")
      for value in (row.created_at, row.updated_at):
        if (
          value is not None
          and (value.replace(tzinfo=UTC) if value.tzinfo is None else value) > now
        ):
          raise ValueError("LIVE_DRAIN_FUTURE_EVIDENCE")
    if any(row.direction != "BUY" for row in intents) or any(
      row.side != "BUY" for row in pending
    ):
      raise ValueError("LIVE_DRAIN_SOURCE_DIRECTION_INVALID")

    intent_ids = {row.id for row in intents}
    durable_intents = {row.intent_id for row in [*pending, *correlations]}
    if not durable_intents.issubset(intent_ids):
      raise ValueError("LIVE_DRAIN_ORDER_INTENT_MISSING")
    # An outbox without its atomic pending/correlation companion is ambiguous.
    # Do not cancel any local candidate until recovery repairs this invariant.
    known_clients = {row.client_order_id for row in [*pending, *correlations]}
    if any(row.client_order_id not in known_clients for row in outbox):
      raise ValueError("LIVE_DRAIN_OUTBOX_BINDING_MISSING")
    cancelled, retained = [], []

    for intent in sorted(intents, key=lambda row: row.id):
      if (
        intent.id in durable_intents
        or intent.order_id
        or (intent.executed_volume or 0) > 0
      ):
        retained.append(intent.id)
      elif intent.status in _UNSUBMITTED:
        intent.status = "CANCELLED"
        intent.updated_at = now.replace(tzinfo=None)
        cancelled.append(intent.id)
        await repository.append_event(
          TAssistantExecutionEvent(
            execution_id,
            f"live-drain-intent:{intent.id}",
            "LIVE_ENTRY_DRAINED",
            now,
            {
              "intent_id": intent.id,
              "reason": reason,
              "outcome": "CANCELLED_UNSUBMITTED",
            },
          )
        )
      elif intent.status not in _TERMINAL:
        retained.append(intent.id)
    if execution.status not in {
      TAssistantExecutionStatus.DRAINING,
      TAssistantExecutionStatus.RECONCILE_REQUIRED,
    }:
      await TAssistantExecutionLifecycle(repository).transition(
        execution,
        target=TAssistantExecutionStatus.DRAINING,
        at=now,
        has_unsettled_buy_work=bool(retained or known_clients),
        event_type="LIVE_EXECUTION_DRAIN_REQUESTED",
        payload={
          "reason": reason,
          "cancelled_intent_ids": cancelled,
          "retained_intent_ids": retained,
          "retained_client_order_ids": sorted(known_clients),
        },
      )
    await db.flush()
    return LiveEntryDrainResult(
      tuple(cancelled), tuple(retained), tuple(sorted(known_clients))
    )
