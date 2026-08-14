"""Persistence operations for limit-up radar lifecycle events."""

from datetime import date
from typing import Iterable, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.limit_up_radar_event import LimitUpRadarEvent


class LimitUpRadarEventRepository:
  def __init__(self, db: AsyncSession):
    self.db = db

  async def append_many(self, events: Iterable[LimitUpRadarEvent]) -> int:
    values = list(events)
    if not values:
      return 0
    self.db.add_all(values)
    await self.db.commit()
    return len(values)

  async def list_for_date(self, trade_date: date) -> List[LimitUpRadarEvent]:
    result = await self.db.execute(
      select(LimitUpRadarEvent)
      .where(LimitUpRadarEvent.trade_date == trade_date)
      .order_by(
        LimitUpRadarEvent.occurred_at.asc(),
        LimitUpRadarEvent.event_id.asc(),
      )
    )
    return list(result.scalars().all())
