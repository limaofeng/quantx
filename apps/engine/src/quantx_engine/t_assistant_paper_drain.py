"""Flush-only PAPER buy drain; source lifecycle changes belong to the supervisor."""

from dataclasses import dataclass
from datetime import UTC, datetime

from quantx_domain.trading.t_assistant_execution import TAssistantExecutionEvent
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.services.paper_broker_matching import (
  PaperBrokerMatching,
  _json,
)
from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)
from sqlalchemy import select

_TERMINAL = frozenset({"FILLED", "CANCELLED", "REJECTED", "EXPIRED"})
_UNSUBMITTED = frozenset({"ALLOCATION_PENDING", "EXECUTION_READY", "AWAITING_APPROVAL"})


@dataclass(frozen=True)
class PaperEntryDrainResult:
  """Order IDs include real TTL expiry caused by the cancellation clock advance."""

  has_unsettled_buy_work: bool
  cancelled_intent_ids: tuple[str, ...]
  cancelled_order_ids: tuple[str, ...]
  receipt_event_ids: tuple[str, ...]
  unsettled_intent_ids: tuple[str, ...]
  unsettled_order_ids: tuple[str, ...]


async def drain_paper_entry_work(
  db, *, execution_id: str, now: datetime, reason: str
) -> PaperEntryDrainResult:
  """Caller owns its transaction and head/execution lock prefix.

  Acquiring the same head -> execution -> account order also makes direct calls
  safe. Only has_unsettled_buy_work=False permits the caller's STOPPED transition; failures
  propagate and roll back this complete drain frame. Existing fills/protective
  SELL plans are handled exclusively by the public receipt convergence.
  """
  if not db.in_transaction():
    raise ValueError("PAPER_DRAIN_CALLER_TRANSACTION_REQUIRED")
  if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
    raise ValueError("PAPER_DRAIN_AWARE_TIME_REQUIRED")
  if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
    raise ValueError("PAPER_DRAIN_REASON_REQUIRED")
  now = now.astimezone(UTC)
  async with db.begin_nested():
    probe = await db.get(
      TAssistantExecutionRecord, execution_id, populate_existing=True
    )
    if probe is None or probe.environment != "PAPER":
      raise ValueError("PAPER_DRAIN_EXECUTION_SCOPE_INVALID")
    head = await db.get(
      TTradeGlobalConfig, probe.config_id, with_for_update=True, populate_existing=True
    )
    execution = await db.get(
      TAssistantExecutionRecord,
      execution_id,
      with_for_update=True,
      populate_existing=True,
    )
    if head is None or head.account_id != execution.account_id:
      raise ValueError("PAPER_DRAIN_CONFIG_SCOPE_INVALID")
    account = await db.get(
      PaperExecutionAccountRecord,
      execution_id,
      with_for_update=True,
      populate_existing=True,
    )
    ledger = PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence())
    orders = await _orders(db, execution_id)
    if account is None:
      if orders:
        raise ValueError("PAPER_DRAIN_ACCOUNT_REQUIRED")
    else:
      snapshot = await ledger.get_snapshot(execution_id=execution_id)
      if snapshot["as_of"] > now:
        raise ValueError("PAPER_DRAIN_TIME_NOT_CAUSAL")
      matcher = PaperBrokerMatching.restore(
        scope_execution_id=execution_id, checkpoint=snapshot["broker_checkpoint"]
      )
      matching = {
        key: order
        for key, order in matcher._broker.orders.items()
        if order.request.order_type.value == "BUY"
      }
      if set(matching) != {order.order_id for order in orders} or any(
        _json(matching[order.order_id]) != order.response_payload for order in orders
      ):
        raise ValueError("PAPER_DRAIN_ORDER_CHECKPOINT_CONFLICT")
    intents = await _intents(db, execution_id)
    if any(intent.account_id != execution.account_id for intent in intents):
      raise ValueError("PAPER_DRAIN_INTENT_SCOPE_INVALID")
    cancelled_intents, cancelled_orders, receipts = [], [], []
    repository = TAssistantExecutionRepository(db)
    for intent in intents:
      if intent.status not in _UNSUBMITTED:
        continue
      submitted = await db.scalar(
        select(PaperExecutionOrderRecord.order_id)
        .where(
          PaperExecutionOrderRecord.execution_id == execution_id,
          PaperExecutionOrderRecord.intent_id == intent.id,
        )
        .limit(1)
      )
      if submitted is not None:
        # A durable receipt must resolve this, never a fabricated local outcome.
        continue
      intent.status = "CANCELLED"
      intent.updated_at = now.replace(tzinfo=None)
      await db.flush()
      await _audit(
        repository, execution_id, intent.id, now, reason, order_id=None, event_id=None
      )
      cancelled_intents.append(intent.id)
    for order in orders:
      await db.refresh(order)
      if order.status in _TERMINAL:
        continue
      if order.owner_type != "T_ASSISTANT_EXECUTION" or order.owner_id != execution_id:
        raise ValueError("PAPER_DRAIN_ORDER_SCOPE_INVALID")
      receipt = await ledger.cancel(
        execution_id=execution_id,
        event_key=f"source-drain:{order.order_id}",
        order_id=order.order_id,
        now=now,
      )
      # Advancing the broker clock can also expire another BUY. Every returned
      # terminal BUY has a real receipt and is audited once; never cancel it again.
      for affected_id in receipt.result_payload["order_ids"]:
        affected = await db.get(
          PaperExecutionOrderRecord, affected_id, populate_existing=True
        )
        if affected.side == "BUY" and affected.status in _TERMINAL:
          await _audit(
            repository,
            execution_id,
            affected.intent_id,
            now,
            reason,
            order_id=affected.order_id,
            event_id=receipt.event_id,
          )
          cancelled_orders.append(affected.order_id)
      receipts.append(receipt.event_id)
    remaining_intents = tuple(intent.id for intent in await _intents(db, execution_id))
    remaining_orders = tuple(
      order.order_id for order in await _orders(db, execution_id)
    )
    return PaperEntryDrainResult(
      bool(remaining_intents or remaining_orders),
      tuple(cancelled_intents),
      tuple(cancelled_orders),
      tuple(receipts),
      remaining_intents,
      remaining_orders,
    )


async def _intents(db, execution_id):
  return list(
    (
      await db.scalars(
        select(TradeIntentRecord)
        .where(
          TradeIntentRecord.owner_type == "T_ASSISTANT_EXECUTION",
          TradeIntentRecord.owner_id == execution_id,
          TradeIntentRecord.environment == "PAPER",
          TradeIntentRecord.direction == "BUY",
          TradeIntentRecord.status.not_in(_TERMINAL),
        )
        .order_by(TradeIntentRecord.id)
        .with_for_update()
        .execution_options(populate_existing=True)
      )
    ).all()
  )


async def _orders(db, execution_id):
  return list(
    (
      await db.scalars(
        select(PaperExecutionOrderRecord)
        .where(
          PaperExecutionOrderRecord.execution_id == execution_id,
          PaperExecutionOrderRecord.environment == "PAPER",
          PaperExecutionOrderRecord.side == "BUY",
          PaperExecutionOrderRecord.status.not_in(_TERMINAL),
        )
        .order_by(PaperExecutionOrderRecord.order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
      )
    ).all()
  )


async def _audit(
  repository, execution_id, intent_id, now, reason, *, order_id, event_id
):
  await repository.append_event(
    TAssistantExecutionEvent(
      execution_id,
      f"paper-drain:{order_id or intent_id}",
      "PAPER_ENTRY_DRAINED",
      now,
      {
        "intent_id": intent_id,
        "order_id": order_id,
        "paper_event_id": event_id,
        "reason": reason,
        "action": "CANCEL_ORDER" if order_id else "CANCEL_UNSUBMITTED_INTENT",
      },
    )
  )
