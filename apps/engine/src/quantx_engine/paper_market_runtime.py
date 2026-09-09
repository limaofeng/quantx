"""Feed accepted books to isolated PAPER facts, including stopped producers."""

import logging

from quantx_contracts import ExecutionEnvironment
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionEventRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.services.paper_execution_ledger import (
  PaperExecutionLedger,
  _stored_time,
)
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)
from sqlalchemy import select

from quantx_engine.accepted_order_market import accepted_order_market

logger = logging.getLogger(__name__)
_ACTIVE_ORDERS = ("PENDING", "SUBMITTED", "PARTIAL_FILLED")


def accepted_paper_market(instrument_code, raw, *, now):
  return accepted_order_market(
    instrument_code, raw, now=now, environment=ExecutionEnvironment.PAPER
  )


class PaperMarketRuntime:
  def __init__(self, *, session_factory, clock):
    self.session_factory = session_factory
    self.clock = clock

  async def on_quote_batch(self, data, *, active_instruments, now):
    if not data:
      return 0
    async with self.session_factory() as db:
      accounts = list((await db.scalars(select(PaperExecutionAccountRecord))).all())
      if not accounts:
        return 0
      scopes = {
        account.execution_id: set(active_instruments.get(account.execution_id, ()))
        for account in accounts
      }
      for account in accounts:
        if account.execution_id in active_instruments:
          scopes[account.execution_id].update(
            account.broker_checkpoint["material"]["positions"]
          )
      orders = list(
        (
          await db.scalars(
            select(PaperExecutionOrderRecord).where(
              PaperExecutionOrderRecord.execution_id.in_(scopes),
              PaperExecutionOrderRecord.environment == "PAPER",
              PaperExecutionOrderRecord.status.in_(_ACTIVE_ORDERS),
            )
          )
        ).all()
      )
      plans = list(
        (
          await db.scalars(
            select(AutoExitPlanRecord).where(
              AutoExitPlanRecord.source_execution_owner_type == "T_ASSISTANT_EXECUTION",
              AutoExitPlanRecord.source_execution_owner_id.in_(scopes),
              AutoExitPlanRecord.environment == "PAPER",
              AutoExitPlanRecord.remaining_volume > 0,
            )
          )
        ).all()
      )
      for order in orders:
        scopes[order.execution_id].add(order.instrument_code)
      for plan in plans:
        scopes[plan.source_execution_owner_id].add(plan.instrument_code)
    applied = 0
    for execution_id, codes in sorted(scopes.items()):
      for code in sorted(codes & data.keys()):
        try:
          key, market = accepted_paper_market(code, data[code], now=now)
        except ValueError as exc:
          # No book is fabricated. Existing orders remain durable; fresh complete
          # evidence can resume them, while the final gate rejects stale evidence.
          logger.warning(
            "PAPER market blocked: execution=%s symbol=%s reason=%s",
            execution_id,
            code,
            str(exc),
          )
          continue
        async with self.session_factory() as db, db.begin():
          ledger = PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence())
          await ledger._account(execution_id, lock=True)
          existing = await db.scalar(
            select(PaperExecutionEventRecord).where(
              PaperExecutionEventRecord.execution_id == execution_id,
              PaperExecutionEventRecord.event_key == key,
            )
          )
          receipt = await ledger.process_quote(
            execution_id=execution_id,
            event_key=key,
            quote=market,
            accepted_at=_stored_time(existing.occurred_at)
            if existing is not None
            else self.clock(),
          )
        applied += not receipt.duplicate
    return applied
