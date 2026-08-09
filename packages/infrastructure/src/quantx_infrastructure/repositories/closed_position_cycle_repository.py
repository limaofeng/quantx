"""Data access for closed position lifecycle records."""

from datetime import date, datetime, time
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.closed_position_cycle import ClosedPositionCycle


class ClosedPositionCycleRepository(BaseRepository[ClosedPositionCycle]):
  model_class = ClosedPositionCycle

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_latest(
    self,
    account_id: str,
    stock_code: str,
    *,
    incomplete_only: bool = False,
  ) -> Optional[ClosedPositionCycle]:
    query = select(ClosedPositionCycle).where(
      ClosedPositionCycle.account_id == account_id,
      ClosedPositionCycle.stock_code == stock_code,
    )
    if incomplete_only:
      query = query.where(
        ClosedPositionCycle.pnl_quality == "INCOMPLETE_HISTORY"
      )
    result = await self.db.execute(
      query.order_by(ClosedPositionCycle.closed_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()

  async def find_page(
    self,
    account_id: str,
    start_date: Optional[date],
    end_date: Optional[date],
    limit: int,
    offset: int,
  ) -> Tuple[List[ClosedPositionCycle], int]:
    conditions = [ClosedPositionCycle.account_id == account_id]
    if start_date:
      conditions.append(
        ClosedPositionCycle.closed_at >= datetime.combine(start_date, time.min)
      )
    if end_date:
      conditions.append(
        ClosedPositionCycle.closed_at <= datetime.combine(end_date, time.max)
      )
    query = (
      select(ClosedPositionCycle)
      .where(*conditions)
      .order_by(ClosedPositionCycle.closed_at.desc())
      .offset(offset)
      .limit(limit)
    )
    count_query = select(func.count()).select_from(ClosedPositionCycle).where(
      *conditions
    )
    result = await self.db.execute(query)
    count_result = await self.db.execute(count_query)
    return list(result.scalars().all()), int(count_result.scalar_one())
