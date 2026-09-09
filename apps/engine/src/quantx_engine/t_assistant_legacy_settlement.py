"""Read legacy order settlement; never cancel or transfer an obligation."""

from dataclasses import dataclass
from datetime import UTC, datetime

from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.enums import OrderStatus, OrderType
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import select

_TERMINAL = {
  OrderStatus.PART_CANCEL: "CANCELLED",
  OrderStatus.CANCELED: "CANCELLED",
  OrderStatus.SUCCEEDED: "FILLED",
  OrderStatus.JUNK: "REJECTED",
}


@dataclass(frozen=True)
class LegacyOrderSettlement:
  client_order_id: str
  blocker: str | None
  filled_volume: int | None = None
  runtime_event_ids: tuple[str, ...] = ()
  evidence_kind: str | None = None


async def read_legacy_order_settlement(
  db, *, account_id: str, run_id: str, client_order_id: str, now: datetime
) -> LegacyOrderSettlement:
  """One transaction's evidence, not a reusable zero-debt/cutover certificate.

  The completion caller must fence ENTRY and inspect every owned intent,
  pending/outbox/correlation and account inbox in the same transaction. This
  reader accepts broker receipts or an exact never-delivered local terminal
  command. Missing evidence remains unresolved; a timeout is not cancellation.
  No source lifecycle, ExitPlan, batch, delivery state or receipt is modified.
  """
  if (
    not db.in_transaction()
    or not isinstance(now, datetime)
    or now.tzinfo is None
    or now.utcoffset() is None
    or any(
      not isinstance(value, str) or not value.strip()
      for value in (account_id, run_id, client_order_id)
    )
  ):
    raise ValueError("LEGACY_T_SETTLEMENT_SCOPE_REQUIRED")
  now = now.astimezone(UTC)

  def blocked(reason):
    return LegacyOrderSettlement(client_order_id, reason)

  def owned(row):
    return (
      row is not None
      and row.owner_type == "STRATEGY_RUN"
      and row.owner_id == run_id
      and row.environment == "LIVE"
      and (not hasattr(row, "account_id") or row.account_id == account_id)
    )

  def causal(row):
    return all(
      value is None
      or (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC))
      <= now
      for value in (
        getattr(row, name, None)
        for name in (
          "created_at",
          "updated_at",
          "applied_at",
          "last_source_event_at",
          "time",
        )
      )
    )

  async def rows(model):
    return list(
      await db.scalars(
        select(model)
        .where(model.client_order_id == client_order_id)
        .order_by(*model.__table__.primary_key.columns)
        .with_for_update()
        .execution_options(populate_existing=True)
      )
    )

  pending = await db.get(
    PendingTradeOrder, client_order_id, with_for_update=True, populate_existing=True
  )
  correlations, commands, events = (
    await rows(OrderCorrelation),
    await rows(TradeCommandOutbox),
    await rows(StrategyRuntimeEvent),
  )
  if not owned(pending) or len(correlations) != 1:
    return blocked("LEGACY_T_SETTLEMENT_BINDING_REQUIRED")
  correlation = correlations[0]
  intent = (
    await db.get(
      TradeIntentRecord, pending.intent_id, with_for_update=True, populate_existing=True
    )
    if pending.intent_id
    else None
  )
  if (
    not all(owned(row) for row in [correlation, intent, *commands, *events])
    or any(
      getattr(correlation, field) != getattr(pending, field)
      for field in (
        "intent_id",
        "broker_order_id",
        "strategy_run_id",
        "strategy_order_id",
        "batch_id",
        "bucket",
        "t_trade_role",
      )
    )
    or pending.strategy_run_id != run_id
    or intent.direction != pending.side
    or intent.instrument_code != pending.instrument_code
  ):
    return blocked("LEGACY_T_SETTLEMENT_OWNER_CONFLICT")
  if not all(causal(row) for row in [pending, correlation, intent, *commands, *events]):
    return blocked("LEGACY_T_SETTLEMENT_FUTURE_FACT")
  if any(
    row.application_status != "APPLIED" or row.applied_at is None for row in events
  ):
    return blocked("LEGACY_T_SETTLEMENT_RUNTIME_BACKLOG")
  if any(
    row.delivery_status
    not in {"ACKNOWLEDGED", "CANCELLED", "EXPIRED", "RECONCILE_REQUIRED"}
    for row in commands
  ):
    return blocked("LEGACY_T_SETTLEMENT_COMMAND_UNRESOLVED")
  broker_id = str(pending.broker_order_id or "")
  if not broker_id:
    parent = None
    if pending.t_order_parent_client_id:
      parent = await db.get(
        PendingTradeOrder,
        pending.t_order_parent_client_id,
        with_for_update=True,
        populate_existing=True,
      )
      if (
        not owned(parent)
        or not causal(parent)
        or parent.t_order_attempt + 1 != pending.t_order_attempt
        or parent.client_order_id == pending.client_order_id
        or pending.t_order_original_created_at is None
        or any(
          getattr(parent, name) != getattr(pending, name)
          for name in (
            "intent_id",
            "instrument_code",
            "side",
            "batch_id",
            "bucket",
            "t_trade_role",
            "t_order_original_created_at",
          )
        )
      ):
        return blocked("LEGACY_T_SETTLEMENT_PARENT_CONFLICT")
    elif pending.t_order_attempt != 0:
      return blocked("LEGACY_T_SETTLEMENT_PARENT_CONFLICT")
    return _local_undelivered_settlement(
      pending, intent, commands, events, now=now, has_prior_attempt=parent is not None
    )
  if not broker_id.isdecimal():
    return blocked("LEGACY_T_SETTLEMENT_BROKER_PROOF_REQUIRED")
  order = await db.get(
    Order, int(broker_id), with_for_update=True, populate_existing=True
  )
  trades = list(
    await db.scalars(
      select(Trade)
      .where(Trade.order_id == int(broker_id))
      .order_by(Trade.id)
      .with_for_update()
      .execution_options(populate_existing=True)
    )
  )
  side = {"BUY": OrderType.BUY, "SELL": OrderType.SELL}.get(pending.side)
  if (
    order is None
    or side is None
    or order.type != side
    or order.account_id != account_id
    or order.stock_code != pending.instrument_code
    or order.volume != pending.volume
    or order.volume <= 0
    or any(
      row.account_id != account_id
      or row.stock_code != pending.instrument_code
      or row.order_type != int(side)
      or row.volume <= 0
      for row in trades
    )
  ):
    return blocked("LEGACY_T_SETTLEMENT_BROKER_SCOPE_CONFLICT")
  if not all(causal(row) for row in [order, *trades]):
    return blocked("LEGACY_T_SETTLEMENT_FUTURE_FACT")
  status = _TERMINAL.get(order.status)
  if status is None or pending.status not in {status, "RECONCILED_ZERO_FILL"}:
    return blocked("LEGACY_T_SETTLEMENT_ORDER_NOT_TERMINAL")
  volume = sum(row.volume for row in trades)
  if (
    not 0 <= volume <= order.volume
    or order.traded_volume != volume
    or (order.status == OrderStatus.SUCCEEDED and volume != order.volume)
    or (order.status == OrderStatus.JUNK and volume != 0)
    or (pending.status == "RECONCILED_ZERO_FILL" and volume != 0)
  ):
    return blocked("LEGACY_T_SETTLEMENT_FILL_GAP")
  # A persisted broker projection alone does not establish that the original
  # owner applied the terminal event. Check the same normalized receipt fields
  # used by its public report consumer, without trusting an ACK or local status.
  from .report_processor import _normalized_order_status, _reported_cumulative_fill

  terminal = []
  received = {}
  durable_trades = {row.id: row.volume for row in trades}
  for event in events:
    if str(event.broker_order_id or "") != broker_id:
      return blocked("LEGACY_T_SETTLEMENT_RECEIPT_CONFLICT")
    report = event.payload.get("report") if isinstance(event.payload, dict) else None
    if not isinstance(report, dict) or (
      report.get("account_id") != account_id
      or str(report.get("order_id") or report.get("broker_order_id") or "") != broker_id
      or (report.get("stock_code") or report.get("instrument_code"))
      != pending.instrument_code
      or str(report.get("order_type") or report.get("side") or "").upper()
      not in {str(int(side)), side.name, f"ORDER_{side.name}"}
    ):
      return blocked("LEGACY_T_SETTLEMENT_RECEIPT_CONFLICT")
    if event.event_type == "ORDER":
      event_status = _normalized_order_status(
        report.get("order_status") or report.get("status")
      )
      reported = _reported_cumulative_fill(report)
      if reported is not None and reported > volume:
        return blocked("LEGACY_T_SETTLEMENT_FILL_GAP")
      if event_status == status and reported == volume:
        terminal.append(event.event_id)
    elif event.event_type == "TRADE":
      raw = report.get("traded_volume", report.get("volume"))
      identity = str(
        report.get("execution_id")
        or report.get("traded_id")
        or report.get("trade_id")
        or ""
      )
      if type(raw) is not int or raw <= 0 or not identity or identity in received:
        return blocked("LEGACY_T_SETTLEMENT_RECEIPT_CONFLICT")
      received[identity] = raw
  if not terminal or received != durable_trades:
    return blocked("LEGACY_T_SETTLEMENT_RECEIPTS_INCOMPLETE")
  return LegacyOrderSettlement(
    client_order_id,
    None,
    volume,
    tuple(event.event_id for event in events),
    "BROKER_RECEIPTS",
  )


def _local_undelivered_settlement(
  pending, intent, commands, events, *, now, has_prior_attempt
):
  """Only an unclaimed command can use this branch; claimed sends need reconciliation."""

  def blocked(reason):
    return LegacyOrderSettlement(
      pending.client_order_id, f"LEGACY_T_SETTLEMENT_{reason}"
    )

  if len(commands) != 1:
    return blocked("LOCAL_COMMAND_REQUIRED")
  command = commands[0]
  payload = command.payload if isinstance(command.payload, dict) else {}
  if (
    payload.get("command_kind") != "PLACE_ORDER"
    or payload.get("execution_mode") != "live"
    or payload.get("client_order_id") != pending.client_order_id
    or payload.get("account_id") != pending.account_id
    or payload.get("instrument_code") != pending.instrument_code
    or payload.get("side") != pending.side
    or type(payload.get("volume")) is not int
    or payload["volume"] != pending.volume
  ):
    return blocked("LOCAL_COMMAND_BINDING_CONFLICT")
  if (
    command.delivered_at is not None
    or command.acknowledged_at is not None
    or command.attempts != 0
    or command.delivery_status not in {"CANCELLED", "EXPIRED"}
    or pending.status != command.delivery_status
    or pending.last_source_sequence != 0
    or pending.last_source_event_at is not None
    # Prior attempts can have fills in this shared intent. This proof is only
    # for the current never-delivered attempt; the caller must inspect parents.
    or (
      not has_prior_attempt
      and (
        (intent.executed_volume or 0) != 0
        or (intent.executed_price or 0) != 0
        or intent.executed_time is not None
        or intent.order_id not in {None, "", pending.strategy_order_id}
      )
    )
  ):
    return blocked("LOCAL_PRE_DELIVERY_PROOF_REQUIRED")
  if command.delivery_status == "EXPIRED":
    expires = command.expires_at
    if (
      expires is None
      or (
        expires.replace(tzinfo=UTC)
        if expires.tzinfo is None
        else expires.astimezone(UTC)
      )
      > now
    ):
      return blocked("LOCAL_EXPIRY_NOT_REACHED")
    if pending.status_reason != "command_expired_before_delivery" or not events:
      return blocked("LOCAL_EXPIRY_PROOF_REQUIRED")
  elif pending.status_reason != "cancelled before Agent delivery":
    return blocked("LOCAL_CANCEL_PROOF_REQUIRED")
  # API expiry creates an owner runtime event; it must have been applied and
  # must name this exact lifecycle command. A local cancel may have no event.
  for event in events:
    payload = event.payload if isinstance(event.payload, dict) else {}
    report, metadata = payload.get("report"), payload.get("metadata")
    if (
      event.event_type != "ORDER"
      or event.broker_order_id
      or not isinstance(report, dict)
      or not isinstance(metadata, dict)
      or metadata.get("command_message_id") != command.message_id
      or metadata.get("intent_id") != pending.intent_id
      or metadata.get("command_lifecycle_status") != command.delivery_status
      or report.get("client_order_id") != pending.client_order_id
      or report.get("account_id") != pending.account_id
      or report.get("stock_code") != pending.instrument_code
      or report.get("side") != pending.side
      or report.get("order_volume") != pending.volume
      or type(report.get("traded_volume")) is not int
      or report.get("traded_volume") != 0
      or report.get("order_status")
      not in {command.delivery_status, "RECONCILED_ZERO_FILL"}
      or report.get("status_msg") != pending.status_reason
    ):
      return blocked("LOCAL_RECEIPT_CONFLICT")
  return LegacyOrderSettlement(
    pending.client_order_id,
    None,
    0,
    tuple(event.event_id for event in events),
    "LOCAL_UNDELIVERED_COMMAND",
  )
