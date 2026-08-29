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
    stmt = select(TTradeReplayProjection).where(TTradeReplayProjection.run_id == run_id)
    if for_update:
      stmt = stmt.with_for_update()
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()

  async def reset_for_rerun(
    self,
    *,
    run_id: str,
    account_id: str,
  ) -> TTradeReplayProjection:
    """Start a fresh lifecycle generation for an existing replay run."""

    row = await self.get(run_id, for_update=True)
    if row is None:
      row = TTradeReplayProjection(
        run_id=run_id,
        account_id=account_id,
        status="PENDING",
        progress_pct=0.0,
        phase="VALIDATING_PORTFOLIO",
        phase_progress_pct=0.0,
        phase_message="初始组合已冻结，正在准备历史行情",
        data_preparation={},
        processed_until=None,
        revision=1,
      )
      self.db.add(row)
      await self.db.flush()
      return row
    if str(row.account_id or "").strip() != str(account_id or "").strip():
      raise ValueError("回放运行不属于指定账户")
    row.status = "PENDING"
    row.progress_pct = 0.0
    row.phase = "VALIDATING_PORTFOLIO"
    row.phase_progress_pct = 0.0
    row.phase_message = "初始组合已冻结，正在准备历史行情"
    row.data_preparation = {}
    row.processed_until = None
    row.revision = int(row.revision or 0) + 1
    await self.db.flush()
    return row

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
