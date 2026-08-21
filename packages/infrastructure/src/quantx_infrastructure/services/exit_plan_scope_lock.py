"""Canonical row-lock ordering for one account/instrument exit-plan scope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select

from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  AutoExitPlanRepository,
)


@dataclass(frozen=True)
class LockedExitPlanScope:
  """Rows locked in the only supported order: position, plans, target."""

  position: Optional[Position]
  plans: list[AutoExitPlanRecord]
  target_plan: Optional[AutoExitPlanRecord] = None

  def plan(self, plan_id: str) -> Optional[AutoExitPlanRecord]:
    if self.target_plan is not None and self.target_plan.plan_id == plan_id:
      return self.target_plan
    return next((item for item in self.plans if item.plan_id == plan_id), None)


async def lock_exit_plan_scope(
  db: Any,
  *,
  account_id: str,
  instrument_code: str,
  target_plan_id: Optional[str] = None,
) -> LockedExitPlanScope:
  """Lock the holding and every reserving plan in a deterministic order."""

  position = await db.scalar(
    select(Position)
    .where(Position.account_id == account_id)
    .where(Position.stock_code == instrument_code)
    .with_for_update()
  )
  repo = AutoExitPlanRepository(db)
  plans = await repo.find_reserving(
    account_id=account_id,
    instrument_code=instrument_code,
    for_update=True,
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
