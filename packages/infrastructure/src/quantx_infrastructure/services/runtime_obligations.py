"""Durable trading obligations that prevent abandoning a strategy consumer."""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.agent_runtime import (
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.strategy_run_state import StrategyRunState
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord


async def runtime_obligation_blocker(db: AsyncSession, run_id: str) -> str | None:
  """Read the same obligations even when the Engine has no in-memory runtime.

  ERROR and PAUSED describe execution, not settlement. Neither status releases
  an order, a protection obligation, or an unapplied broker report.
  """
  checks = (
    (
      select(PendingTradeOrder.client_order_id).where(
        PendingTradeOrder.strategy_run_id == run_id,
        func.upper(PendingTradeOrder.status).notin_(
          ["FILLED", "CANCELLED", "CANCELED", "REJECTED", "RECONCILED_ZERO_FILL"]
        ),
      ),
      "仍有持久化委托等待券商终态回报",
    ),
    (
      select(StrategyRuntimeEvent.event_id).where(
        StrategyRuntimeEvent.strategy_run_id == run_id,
        StrategyRuntimeEvent.application_status != "APPLIED",
      ),
      "仍有持久化券商回报尚未收敛",
    ),
    (
      select(TradeIntentRecord.id).where(
        TradeIntentRecord.strategy_run_id == run_id,
        func.upper(TradeIntentRecord.status).notin_(
          [
            "FILLED",
            "CANCELLED",
            "CANCELED",
            "REJECTED",
            "EXPIRED",
            "FAILED",
            "SUPPRESSED",
            "RECONCILED_ZERO_FILL",
          ]
        ),
      ),
      "仍有持久化交易意图等待审批或执行收敛",
    ),
    (
      select(AutoExitPlanRecord.plan_id).where(
        AutoExitPlanRecord.strategy_run_id == run_id,
        or_(
          AutoExitPlanRecord.pending_client_order_id.is_not(None),
          (
            (AutoExitPlanRecord.remaining_volume > 0)
            & AutoExitPlanRecord.status.notin_(["COMPLETED", "CANCELLED"])
          ),
        ),
      ),
      "仍有未解除的退出保护义务",
    ),
    (
      select(TTradeBatch.batch_id).where(
        TTradeBatch.strategy_run_id == run_id,
        TTradeBatch.entry_filled_volume > TTradeBatch.exit_filled_volume,
      ),
      "仍有未平衡的做 T 批次",
    ),
  )
  for query, reason in checks:
    if await db.scalar(query.limit(1)) is not None:
      return reason
  custom = await db.scalar(
    select(StrategyRunState.custom_state).where(StrategyRunState.run_id == run_id)
  )
  if custom:
    if custom.get("order_cash_reservations") or custom.get(
      "order_position_reservations"
    ):
      return "仍有持久化资金或持仓预留等待收敛"
    if custom.get("t_trade_paper_fill_outbox"):
      return "仍有模拟成交等待持久化收敛"
  return None
