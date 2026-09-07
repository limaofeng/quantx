"""Canonical row-lock ordering for one account/instrument exit-plan scope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from quantx_contracts import ExecutionOwnerRef, ExecutionOwnerType
from sqlalchemy import select

from quantx_infrastructure.models.agent_runtime import AccountExecutionControl
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.paper_execution import PaperExecutionAccountRecord
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.strategy_run_state import (
  StrategyRunPosition,
  StrategyRunState,
)
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  RESERVING_EXIT_PLAN_STATUSES,
  AutoExitPlanRepository,
)


@dataclass(frozen=True)
class LockedExitPlanScope:
  """Lock order: LIVE account control, position, reserving plans, target."""

  position: Optional[Position]
  plans: list[AutoExitPlanRecord]
  target_plan: Optional[AutoExitPlanRecord] = None

  def plan(self, plan_id: str) -> Optional[AutoExitPlanRecord]:
    if self.target_plan is not None and self.target_plan.plan_id == plan_id:
      return self.target_plan
    return next((item for item in self.plans if item.plan_id == plan_id), None)


async def _paper_position(
  db: Any,
  record: Optional[AutoExitPlanRecord],
  *,
  run_id: str,
  account_id: str,
  instrument_code: str,
):
  if run_id:
    position = await db.scalar(
      select(StrategyRunPosition)
      .where(
        StrategyRunPosition.run_id == run_id,
        StrategyRunPosition.instrument_code == instrument_code,
      )
      .with_for_update()
    )
    if position is None:
      return None
    custom = await db.scalar(
      select(StrategyRunState.custom_state).where(StrategyRunState.run_id == run_id)
    )
    buckets = dict(
      dict(dict(custom or {}).get("bucket_ledger_snapshot") or {}).get("instruments")
      or {}
    ).get(instrument_code, {})
    available = sum(
      max(0, int(bucket.get("available_volume", 0))) for bucket in buckets.values()
    )
    ledger_total = sum(
      max(0, int(bucket.get("total_volume", 0))) for bucket in buckets.values()
    )
    if ledger_total != int(position.long_volume or 0):
      available = 0
    return Position(
      account_id=account_id,
      stock_code=instrument_code,
      volume=int(position.long_volume or 0),
      can_use_volume=available,
      avg_price=float(position.long_avg_price or 0),
      market_value=float(position.market_value or 0),
      last_price=float(position.last_price or 0),
      updated_at=position.updated_at,
    )
  if record is not None:
    # A monitor-owned PAPER exit is a standalone simulation of the holdings
    # frozen when it was created. Broker changes cannot replenish it.
    template = dict(dict(record.plan_state or {}).get("template") or {})
    metadata = dict(template.get("metadata") or {})
    volume = max(
      0,
      int(metadata.get("position_volume_snapshot", record.protected_volume))
      - int(record.exited_volume or 0),
    )
    available = max(
      0,
      int(metadata.get("available_volume_snapshot", 0))
      - int(record.exited_volume or 0),
    )
    return Position(
      account_id=account_id,
      stock_code=instrument_code,
      volume=volume,
      can_use_volume=min(volume, available),
      avg_price=record.entry_avg_price,
      updated_at=record.updated_at,
      created_at=record.created_at,
    )
  # Only initial creation may sample the broker's holdings. This is not a
  # LIVE claim and is never used to re-seed an existing PAPER plan or run.
  return await db.scalar(
    select(Position).where(
      Position.account_id == account_id,
      Position.stock_code == instrument_code,
    )
  )


async def lock_exit_plan_scope(
  db: Any,
  *,
  account_id: str,
  instrument_code: str,
  target_plan_id: Optional[str] = None,
  execution_mode: Optional[str] = None,
  strategy_run_id: Optional[str] = None,
  execution_ref: Optional[ExecutionOwnerRef] = None,
) -> LockedExitPlanScope:
  """Lock the holding and every reserving plan in a deterministic order."""

  repo = AutoExitPlanRepository(db)
  initial = await repo.find_by_id(target_plan_id) if target_plan_id else None
  mode = execution_mode or (
    str(initial.environment).lower() if initial is not None else "live"
  )
  mode = mode.lower()
  run_id = strategy_run_id or (
    str(initial.strategy_run_id or "") if initial is not None else ""
  )
  if (
    execution_ref is None
    and initial is not None
    and initial.source_execution_owner_type == "T_ASSISTANT_EXECUTION"
  ):
    execution_ref = ExecutionOwnerRef(
      "T_ASSISTANT_EXECUTION", initial.source_execution_owner_id
    )
  if (
    execution_ref is not None
    and initial is not None
    and (
      initial.source_execution_owner_type != execution_ref.owner_type.value
      or initial.source_execution_owner_id != execution_ref.owner_id
      or initial.source_execution_environment != mode.upper()
    )
  ):
    raise ValueError("退出计划来源执行归属冲突")
  if (
    execution_ref is not None
    and execution_ref.owner_type is ExecutionOwnerType.T_ASSISTANT_EXECUTION
  ):
    if mode != "paper" or run_id:
      raise ValueError("T exit persistence requires isolated PAPER scope")
    execution = await db.scalar(
      select(TAssistantExecutionRecord)
      .where(TAssistantExecutionRecord.execution_id == execution_ref.owner_id)
      .with_for_update()
      .execution_options(populate_existing=True)
    )
    paper = await db.scalar(
      select(PaperExecutionAccountRecord)
      .where(PaperExecutionAccountRecord.execution_id == execution_ref.owner_id)
      .with_for_update()
      .execution_options(populate_existing=True)
    )
    if (
      execution is None
      or paper is None
      or execution.environment != "PAPER"
      or paper.environment != "PAPER"
      or execution.account_id != account_id
      or paper.account_id != account_id
    ):
      raise ValueError("PAPER exit source/account scope conflict")
    positions = dict(paper.broker_checkpoint.get("material", {}).get("positions", {}))
    raw = dict(positions.get(instrument_code) or {})
    buckets = dict(
      paper.bucket_checkpoint.get("instruments", {}).get(instrument_code) or {}
    )
    total = sum(int(bucket.get("total_volume", 0)) for bucket in buckets.values())
    if total != int(raw.get("long_volume", 0)):
      raise ValueError("PAPER exit bucket/position mismatch")
    position = Position(
      account_id=account_id,
      stock_code=instrument_code,
      volume=total,
      can_use_volume=sum(
        int(bucket.get("available_volume", 0)) for bucket in buckets.values()
      ),
      avg_price=float(raw.get("long_avg_price", 0)),
      market_value=float(raw.get("market_value", 0)),
      last_price=float(raw.get("last_price", 0)),
      updated_at=paper.snapshot_as_of,
    )
    plans = list(
      (
        await db.scalars(
          select(AutoExitPlanRecord)
          .where(
            AutoExitPlanRecord.account_id == account_id,
            AutoExitPlanRecord.instrument_code == instrument_code,
            AutoExitPlanRecord.environment == "PAPER",
            AutoExitPlanRecord.source_execution_owner_type == "T_ASSISTANT_EXECUTION",
            AutoExitPlanRecord.source_execution_owner_id == execution_ref.owner_id,
            AutoExitPlanRecord.status.in_(RESERVING_EXIT_PLAN_STATUSES),
          )
          .order_by(AutoExitPlanRecord.created_at, AutoExitPlanRecord.plan_id)
          .with_for_update()
          .execution_options(populate_existing=True)
        )
      ).all()
    )
    target = next((plan for plan in plans if plan.plan_id == target_plan_id), None)
    if target is None and target_plan_id:
      target = await repo.find_by_id(target_plan_id, for_update=True)
    if target is not None and (
      target.account_id != account_id
      or target.instrument_code != instrument_code
      or target.source_execution_owner_type != execution_ref.owner_type.value
      or target.source_execution_owner_id != execution_ref.owner_id
      or target.environment != "PAPER"
      or target.strategy_run_id is not None
    ):
      raise ValueError("PAPER exit plan scope conflict")
    return LockedExitPlanScope(position, plans, target)
  if mode == "live":
    # Plan creation/resizing and order enqueue claim the same LIVE inventory.
    # The account gate precedes position/plan locks in every writer.
    await db.get(AccountExecutionControl, account_id, with_for_update=True)
    position = await db.scalar(
      select(Position)
      .where(
        Position.account_id == account_id,
        Position.stock_code == instrument_code,
      )
      .with_for_update()
    )
  elif mode == "paper":
    position = await _paper_position(
      db,
      initial,
      run_id=run_id,
      account_id=account_id,
      instrument_code=instrument_code,
    )
  else:
    raise ValueError("退出容量只支持 live 或 paper")
  plans = await repo.find_reserving(
    account_id=account_id,
    instrument_code=instrument_code,
    for_update=True,
    execution_mode=mode,
    strategy_run_id=run_id or None,
    plan_id=target_plan_id,
  )
  target = (
    next((item for item in plans if item.plan_id == target_plan_id), None)
    if target_plan_id
    else None
  )
  if target_plan_id and target is None:
    # Terminal plans no longer reserve capacity. Lock them only after the
    # position and the complete reserving set so every writer keeps one order.
    target = await repo.find_by_id(target_plan_id, for_update=True)
  if target is not None and (
    target.account_id != account_id
    or target.instrument_code != instrument_code
    or str(target.environment or "").upper() != mode.upper()
    or (mode == "paper" and str(target.strategy_run_id or "") != run_id)
  ):
    raise ValueError("退出计划与库存执行环境不一致")
  return LockedExitPlanScope(position=position, plans=plans, target_plan=target)


async def lock_exit_plan_scope_for_plan(
  db: Any,
  plan_id: str,
) -> LockedExitPlanScope:
  """Discover a plan's immutable scope, then acquire its canonical locks."""

  record = await AutoExitPlanRepository(db).find_by_id(plan_id)
  if record is None:
    return LockedExitPlanScope(position=None, plans=[], target_plan=None)
  return await lock_exit_plan_scope(
    db,
    account_id=str(record.account_id),
    instrument_code=str(record.instrument_code),
    target_plan_id=plan_id,
  )


__all__ = [
  "LockedExitPlanScope",
  "lock_exit_plan_scope",
  "lock_exit_plan_scope_for_plan",
]
