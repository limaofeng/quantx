"""Repository for durable T-trade replay lifecycle projections."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.t_trade_replay_projection import (
  TTradeReplayProjection,
)

ACTIVE_REPLAY_STATUSES = ("PENDING", "RUNNING", "PAUSED")


class TTradeReplayProjectionRepository:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def get(
    self,
    run_id: str,
    *,
    for_update: bool = False,
  ) -> Optional[TTradeReplayProjection]:
    stmt = select(TTradeReplayProjection).where(
      TTradeReplayProjection.run_id == run_id
    )
    if for_update:
      stmt = stmt.with_for_update()
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()

  async def list_by_account(
    self,
    account_id: str,
    limit: int,
  ) -> List[TTradeReplayProjection]:
    result = await self.db.execute(
      select(TTradeReplayProjection)
      .where(TTradeReplayProjection.account_id == account_id)
      .order_by(
        TTradeReplayProjection.created_at.desc(),
        TTradeReplayProjection.run_id.desc(),
      )
      .limit(limit)
    )
    return list(result.scalars().all())

  async def has_active(self, account_id: str) -> bool:
    result = await self.db.execute(
      select(TTradeReplayProjection.run_id)
      .where(
        TTradeReplayProjection.account_id == account_id,
        TTradeReplayProjection.status.in_(ACTIVE_REPLAY_STATUSES),
      )
      .limit(1)
    )
    return result.scalar_one_or_none() is not None
