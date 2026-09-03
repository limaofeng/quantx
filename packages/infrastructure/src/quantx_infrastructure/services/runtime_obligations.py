"""Durable trading obligations that prevent abandoning a strategy consumer."""

from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.agent_runtime import (
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.strategy_run_state import StrategyRunState
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord


def _owner_columns(
  model,
  *,
  owner_type: str,
  owner_id: str,
  environment: str,
  source: bool = False,
):
  type_name = "source_execution_owner_type" if source else "owner_type"
  id_name = "source_execution_owner_id" if source else "owner_id"
  environment_name = (
    "source_execution_environment" if source else "environment"
  )
  clauses = [
    getattr(model, type_name) == owner_type,
    getattr(model, id_name) == owner_id,
    getattr(model, environment_name) == environment,
  ]
  return and_(*clauses)


async def runtime_obligation_blocker(
  db: AsyncSession,
  execution_ref: ExecutionOwnerRef,
  environment: ExecutionEnvironment,
) -> str | None:
  """Read the same obligations even when the Engine has no in-memory runtime.

  ERROR and PAUSED describe execution, not settlement. Neither status releases
  an order, a protection obligation, or an unapplied broker report.  Both the
  owner and environment are explicit so a missing runtime cannot silently
  inspect another execution namespace.
  """
  if not isinstance(execution_ref, ExecutionOwnerRef):
    return "执行归属身份必须是强类型，拒绝释放运行时义务"
  try:
    canonical_environment = ExecutionEnvironment(environment).value
  except (TypeError, ValueError):
    return "执行环境无效，拒绝释放运行时义务"
  canonical_owner_type = execution_ref.owner_type.value
  canonical_owner_id = execution_ref.owner_id
  checks = (
    (
      select(PendingTradeOrder.client_order_id).where(
        _owner_columns(
          PendingTradeOrder,
          owner_type=canonical_owner_type,
          owner_id=canonical_owner_id,
          environment=canonical_environment,
        ),
        func.upper(PendingTradeOrder.status).notin_(
          ["FILLED", "CANCELLED", "CANCELED", "REJECTED", "RECONCILED_ZERO_FILL"]
        ),
      ),
      "仍有持久化委托等待券商终态回报",
    ),
    (
      select(StrategyRuntimeEvent.event_id).where(
        _owner_columns(
          StrategyRuntimeEvent,
          owner_type=canonical_owner_type,
          owner_id=canonical_owner_id,
          environment=canonical_environment,
        ),
        StrategyRuntimeEvent.application_status != "APPLIED",
      ),
      "仍有持久化券商回报尚未收敛",
    ),
    (
      select(TradeIntentRecord.id).where(
        _owner_columns(
          TradeIntentRecord,
          owner_type=canonical_owner_type,
          owner_id=canonical_owner_id,
          environment=canonical_environment,
        ),
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
        (
          _owner_columns(
            AutoExitPlanRecord,
            owner_type=canonical_owner_type,
            owner_id=canonical_owner_id,
            environment=canonical_environment,
            source=True,
          )
          if canonical_owner_type != "EXIT_PLAN"
          else and_(
            AutoExitPlanRecord.plan_id == canonical_owner_id,
            AutoExitPlanRecord.environment == canonical_environment,
          )
        ),
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
        _owner_columns(
          TTradeBatch,
          owner_type=canonical_owner_type,
          owner_id=canonical_owner_id,
          environment=canonical_environment,
          source=True,
        ),
        TTradeBatch.entry_filled_volume > TTradeBatch.exit_filled_volume,
      ),
      "仍有未平衡的做 T 批次",
    ),
  )
  for query, reason in checks:
    if await db.scalar(query.limit(1)) is not None:
      return reason
  custom = None
  if canonical_owner_type == "STRATEGY_RUN":
    custom = await db.scalar(
      select(StrategyRunState.custom_state).where(
        StrategyRunState.run_id == canonical_owner_id
      )
    )
  if custom:
    if custom.get("order_cash_reservations") or custom.get(
      "order_position_reservations"
    ):
      return "仍有持久化资金或持仓预留等待收敛"
    if custom.get("t_trade_paper_fill_outbox"):
      return "仍有模拟成交等待持久化收敛"
  return None
