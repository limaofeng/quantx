"""Repository for exit-plan replay lifecycle projections."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.exit_plan_replay_projection import (
  ExitPlanReplayProjection,
)

ACTIVE_EXIT_PLAN_REPLAY_STATUSES = ("PENDING", "RUNNING", "PAUSED")


class ExitPlanReplayProjectionRepository:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def get(
    self, run_id: str, *, for_update: bool = False
  ) -> Optional[ExitPlanReplayProjection]:
    stmt = select(ExitPlanReplayProjection).where(
      ExitPlanReplayProjection.run_id == run_id
    )
    if for_update:
      stmt = stmt.with_for_update()
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()

  async def list_by_account(
    self, account_id: str, limit: int
  ) -> List[ExitPlanReplayProjection]:
    result = await self.db.execute(
      select(ExitPlanReplayProjection)
      .where(ExitPlanReplayProjection.account_id == account_id)
      .order_by(
        ExitPlanReplayProjection.created_at.desc(),
        ExitPlanReplayProjection.run_id.desc(),
      )
      .limit(limit)
    )
    return list(result.scalars().all())

  async def has_active(self, account_id: str) -> bool:
    result = await self.db.execute(
      select(ExitPlanReplayProjection.run_id)
      .where(
        ExitPlanReplayProjection.account_id == account_id,
        ExitPlanReplayProjection.status.in_(ACTIVE_EXIT_PLAN_REPLAY_STATUSES),
      )
      .limit(1)
    )
    return result.scalar_one_or_none() is not None
