"""Converge committed PAPER facts into the public intent, T batch and exit plan.

Called inside PaperExecutionLedger's savepoint, after its facts are flushed.
This adapter uses the same public ExitPlan aggregate as other execution owners.
"""

from __future__ import annotations

from datetime import UTC
from decimal import ROUND_HALF_UP, Decimal

from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.trading.exit_plan import ExitPlan, ExitPlanBook, ExitPlanTemplate
from sqlalchemy import select

from quantx_infrastructure.models.agent_runtime import TTradeBatch
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionEventRecord,
  PaperExecutionFillRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.paper_broker_matching import (
  PAPER_TRADING_COST_POLICY,
  _json,
)
from quantx_infrastructure.services.t_trade_batch_metrics import (
  extract_t_trade_cost_snapshot,
)

_TERMINAL = {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}


def _stored_amount(value):
  # PostgreSQL NUMERIC uses ties away from zero, unlike Decimal's default.
  return Decimal(str(value)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def _naive(value):
  return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _source(row, execution):
  return (
    row.environment == "PAPER"
    and row.account_id == execution.account_id
    and row.source_execution_owner_type == "T_ASSISTANT_EXECUTION"
    and row.source_execution_owner_id == execution.execution_id
    and row.source_execution_environment == "PAPER"
    and row.strategy_run_id is None
  )


class PaperReceiptConvergence:
  """Flush-only public receipt sink; callers retain transaction ownership."""

  async def __call__(self, db, execution_id, result):
    execution = await db.get(
      TAssistantExecutionRecord,
      execution_id,
      with_for_update=True,
      populate_existing=True,
    )
    account = await db.get(
      PaperExecutionAccountRecord,
      execution_id,
      with_for_update=True,
      populate_existing=True,
    )
    if (
      execution is None
      or execution.environment != "PAPER"
      or account is None
      or account.account_id != execution.account_id
      or account.environment != "PAPER"
    ):
      raise ValueError("PAPER_RECEIPT_SCOPE_CONFLICT")
    event = await db.scalar(
      select(PaperExecutionEventRecord).where(
        PaperExecutionEventRecord.execution_id == execution_id,
        PaperExecutionEventRecord.revision == account.revision,
      )
    )
    if (
      event is None
      or event.resulting_snapshot_hash != account.snapshot_hash
      or event.result_payload.get("orders") != _json(result.orders)
      or event.result_payload.get("trades") != _json(result.trades)
    ):
      raise ValueError("PAPER_RECEIPT_FACT_CONFLICT")
    fills = list(
      (
        await db.scalars(
          select(PaperExecutionFillRecord)
          .where(
            PaperExecutionFillRecord.execution_id == execution_id,
            PaperExecutionFillRecord.event_id == event.event_id,
          )
          .order_by(PaperExecutionFillRecord.fill_id)
        )
      ).all()
    )
    if {fill.fill_id for fill in fills} != {trade.trade_id for trade in result.trades}:
      raise ValueError("PAPER_RECEIPT_FILL_CONFLICT")
    trades = {trade.trade_id: trade for trade in result.trades}
    for fill in fills:
      trade = trades[fill.fill_id]
      if (
        fill.order_id != trade.order_id
        or fill.volume != trade.volume
        or fill.price != _stored_amount(trade.price)
        or fill.fee != _stored_amount(trade.commission)
        or _naive(fill.occurred_at) != _naive(trade.trade_time)
        or fill.trade_payload != _json(trade)
      ):
        raise ValueError("PAPER_RECEIPT_FILL_CONFLICT")
    batches = {}
    for response in result.orders:
      order = await db.get(
        PaperExecutionOrderRecord, response.order_id, populate_existing=True
      )
      if (
        order is None
        or order.execution_id != execution_id
        or order.last_event_id != event.event_id
        or order.response_payload != _json(response)
      ):
        raise ValueError("PAPER_RECEIPT_ORDER_CONFLICT")
      intent = await db.get(
        TradeIntentRecord, order.intent_id, with_for_update=True, populate_existing=True
      )
      if (
        intent is None
        or intent.environment != "PAPER"
        or intent.account_id != execution.account_id
        or intent.owner_type != order.owner_type
        or intent.owner_id != order.owner_id
        or intent.instrument_code != order.instrument_code
        or intent.direction != order.side
      ):
        raise ValueError("PAPER_RECEIPT_INTENT_CONFLICT")
      order_fills = [fill for fill in fills if fill.order_id == order.order_id]
      if order.side == "BUY":
        batch, plan_id = await self._entry(db, execution, order, intent, order_fills)
      else:
        batch, plan_id = await self._exit(
          db, execution, event, order, intent, order_fills
        )
      await self._intent(db, order, intent)
      batches[batch.batch_id] = (batch, plan_id)
    for batch, plan_id in batches.values():
      await self._batch(db, execution, batch, plan_id, event.occurred_at)
    await db.flush()

  async def _entry(self, db, execution, order, intent, fills):
    metadata = dict(intent.intent_metadata or {})
    template = ExitPlanTemplate.from_dict(metadata["exit_plan_template"])
    costs = extract_t_trade_cost_snapshot(metadata)
    if (
      template.costs != PAPER_TRADING_COST_POLICY
      or costs is None
      or any(
        getattr(costs, field) != value
        for field, value in PAPER_TRADING_COST_POLICY.to_dict().items()
      )
    ):
      raise ValueError("PAPER_RECEIPT_MATCHING_COST_POLICY_CONFLICT")
    owner = ExecutionOwnerRef("T_ASSISTANT_EXECUTION", execution.execution_id)
    batch_id = metadata["t_batch_id"]
    if (
      order.owner_type != "T_ASSISTANT_EXECUTION"
      or order.owner_id != execution.execution_id
      or metadata.get("t_trade_role") != "entry"
      or template.run_id
      or template.source_type != "T_TRADE_BATCH"
      or template.source_id != batch_id
      or template.plan_id != metadata.get("exit_plan_id")
      or template.account_id != execution.account_id
      or template.instrument_code != order.instrument_code
      or template.bucket != intent.bucket
      or template.metadata.get("source_execution_ref") != owner.to_dict()
    ):
      raise ValueError("PAPER_RECEIPT_ENTRY_TEMPLATE_CONFLICT")
    batch = await db.get(
      TTradeBatch, batch_id, with_for_update=True, populate_existing=True
    )
    if batch is None:
      batch = TTradeBatch(
        batch_id=batch_id,
        account_id=execution.account_id,
        instrument_code=order.instrument_code,
        source_execution_owner_type=owner.owner_type.value,
        source_execution_owner_id=owner.owner_id,
        source_execution_environment="PAPER",
        environment="PAPER",
        strategy_run_id=None,
        entry_intent_id=intent.id,
        entry_client_order_id=order.order_id,
        target_volume=order.volume,
        status="ENTRY_QUEUED",
        metrics_origin="RULE_ESTIMATE",
        policy_version=template.config_version,
        commission_rate=costs.commission_rate,
        minimum_commission=costs.minimum_commission,
        stamp_tax_rate=costs.stamp_tax_rate,
        transfer_fee_rate=costs.transfer_fee_rate,
      )
      db.add(batch)
      await db.flush()
    if (
      not _source(batch, execution)
      or batch.instrument_code != order.instrument_code
      or batch.entry_intent_id != intent.id
    ):
      raise ValueError("PAPER_RECEIPT_BATCH_CONFLICT")
    service = AutoExitPlanService()
    for fill in fills:
      await service.register_execution_entry_fill(
        execution_ref=owner,
        environment=ExecutionEnvironment.PAPER,
        exit_plan_template=template.to_dict(),
        volume=fill.volume,
        price=float(fill.price),
        trade_time=fill.occurred_at,
        event_business_key=f"paper-fill:{execution.execution_id}:{fill.fill_id}",
        db=db,
        commit=False,
      )
    return batch, template.plan_id

  async def _exit(self, db, execution, event, order, intent, fills):
    plan_row = await db.get(
      AutoExitPlanRecord, order.owner_id, with_for_update=True, populate_existing=True
    )
    if (
      order.owner_type != "EXIT_PLAN"
      or plan_row is None
      or not _source(plan_row, execution)
      or plan_row.source_type != "T_TRADE_BATCH"
      or plan_row.instrument_code != order.instrument_code
      or plan_row.bucket != intent.bucket
    ):
      raise ValueError("PAPER_RECEIPT_EXIT_SCOPE_CONFLICT")
    batch = await db.get(
      TTradeBatch, plan_row.source_id, with_for_update=True, populate_existing=True
    )
    if (
      batch is None
      or not _source(batch, execution)
      or batch.instrument_code != order.instrument_code
    ):
      raise ValueError("PAPER_RECEIPT_BATCH_CONFLICT")
    key = f"paper-exit:{execution.execution_id}:{event.event_id}:{order.order_id}"
    applied = await db.scalar(
      select(AutoExitPlanEvent.event_id).where(AutoExitPlanEvent.business_key == key)
    )
    if applied is None:
      plan = ExitPlan.from_dict(plan_row.plan_state)
      if plan.pending_intent_id != intent.id:
        raise ValueError("PAPER_RECEIPT_EXIT_PENDING_CONFLICT")
      book = ExitPlanBook([plan])
      # The terminal cumulative barrier must be installed before the final fill
      # clears pending state. Both transitions persist as one public aggregate.
      book.apply_order_event(
        plan_id=plan.plan_id,
        intent_id=intent.id,
        order_id=order.order_id,
        status=order.status,
        cumulative_filled_volume=order.filled_volume,
      )
      for fill in fills:
        if fill.volume > plan.remaining_volume:
          raise ValueError("PAPER_RECEIPT_EXIT_OVERFILL")
        book.apply_exit_fill(
          plan_id=plan.plan_id,
          intent_id=intent.id,
          volume=fill.volume,
          price=float(fill.price),
        )
      if order.status in _TERMINAL and order.filled_volume == 0:
        book.apply_order_event(
          plan_id=plan.plan_id, intent_id=intent.id, status="RECONCILED_ZERO_FILL"
        )
      await AutoExitPlanService().persist_execution_plan_state(
        execution_ref=ExecutionOwnerRef(
          "T_ASSISTANT_EXECUTION", execution.execution_id
        ),
        environment=ExecutionEnvironment.PAPER,
        plan_state=plan.to_dict(),
        expected_state_version=plan_row.state_version,
        evaluated_at=event.occurred_at,
        event_type="PAPER_EXIT_RECEIPT_APPLIED",
        event_business_key=key,
        db=db,
        commit=False,
      )
    batch.exit_intent_id, batch.exit_client_order_id = intent.id, order.order_id
    return batch, plan_row.plan_id

  async def _intent(self, db, order, intent):
    facts = list(
      (
        await db.scalars(
          select(PaperExecutionFillRecord)
          .join(
            PaperExecutionOrderRecord,
            PaperExecutionOrderRecord.order_id == PaperExecutionFillRecord.order_id,
          )
          .where(
            PaperExecutionOrderRecord.execution_id == order.execution_id,
            PaperExecutionOrderRecord.intent_id == intent.id,
          )
        )
      ).all()
    )
    volume = sum(fill.volume for fill in facts)
    amount = sum((fill.price * fill.volume for fill in facts), Decimal(0))
    intent.order_id = order.order_id
    intent.status = (
      "ROUTED" if order.status in {"PENDING", "SUBMITTED"} else order.status
    )
    intent.executed_volume = volume
    intent.executed_price = float(amount / volume) if volume else None
    intent.executed_time = (
      _naive(max(fill.occurred_at for fill in facts)) if facts else None
    )
    intent.risk_decision_id = order.risk_evidence.get("risk_decision_id")

  async def _batch(self, db, execution, batch, plan_id, now):
    orders = list(
      (
        await db.scalars(
          select(PaperExecutionOrderRecord).where(
            PaperExecutionOrderRecord.execution_id == execution.execution_id,
            (PaperExecutionOrderRecord.intent_id == batch.entry_intent_id)
            | (
              (PaperExecutionOrderRecord.owner_type == "EXIT_PLAN")
              & (PaperExecutionOrderRecord.owner_id == plan_id)
            ),
          )
        )
      ).all()
    )
    facts = list(
      (
        await db.scalars(
          select(PaperExecutionFillRecord).where(
            PaperExecutionFillRecord.execution_id == execution.execution_id,
            PaperExecutionFillRecord.order_id.in_([order.order_id for order in orders]),
          )
        )
      ).all()
    )
    buy_ids = {order.order_id for order in orders if order.side == "BUY"}
    entry, exits = (
      [fill for fill in facts if fill.order_id in buy_ids],
      [fill for fill in facts if fill.order_id not in buy_ids],
    )
    for prefix, fills in (("entry", entry), ("exit", exits)):
      volume = sum(fill.volume for fill in fills)
      amount = sum((fill.price * fill.volume for fill in fills), Decimal(0))
      setattr(batch, f"{prefix}_filled_volume", volume)
      setattr(batch, f"{prefix}_avg_price", float(amount / volume) if volume else 0)
    if batch.exit_filled_volume > batch.entry_filled_volume:
      raise ValueError("PAPER_RECEIPT_BATCH_OVERFILL")
    active_buy = any(
      order.side == "BUY" and order.status not in _TERMINAL for order in orders
    )
    active_sell = any(
      order.side == "SELL" and order.status not in _TERMINAL for order in orders
    )
    remaining = batch.entry_filled_volume - batch.exit_filled_volume
    if active_buy:
      batch.status = "ENTRY_PARTIAL" if entry else "ENTRY_QUEUED"
    elif not entry:
      batch.status = (
        "ENTRY_EXPIRED"
        if all(order.status == "EXPIRED" for order in orders)
        else "ENTRY_REJECTED"
      )
    elif not remaining:
      batch.status = "CLOSED"
    elif active_sell:
      batch.status = "EXIT_PARTIAL" if exits else "EXIT_TRIGGERED"
    else:
      batch.status = "EXIT_PARTIAL" if exits else "OPEN"
    batch.entry_filled_at = (
      _naive(min(fill.occurred_at for fill in entry)) if entry else None
    )
    batch.last_exit_filled_at = (
      _naive(max(fill.occurred_at for fill in exits)) if exits else None
    )
    batch.closed_at = batch.last_exit_filled_at if batch.status == "CLOSED" else None
    batch.terminal_at = (
      _naive(now)
      if batch.status in {"CLOSED", "ENTRY_REJECTED", "ENTRY_EXPIRED"}
      else None
    )
    batch.version += 1
