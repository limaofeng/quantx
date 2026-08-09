"""上市公司公告仓储。"""

from typing import Iterable, List, Optional, Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.stock_disclosure import (
  AnnouncementSyncRun,
  StockAnnouncement,
  StockRepurchaseEvent,
)


class AnnouncementRepository(BaseRepository[StockAnnouncement]):
  model_class = StockAnnouncement

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_announcements(
    self,
    stock_code: str,
    limit: int = 20,
  ) -> List[StockAnnouncement]:
    stmt = (
      select(StockAnnouncement)
      .where(StockAnnouncement.stock_code == stock_code.upper())
      .order_by(
        desc(StockAnnouncement.announce_date),
        desc(StockAnnouncement.created_at),
      )
      .limit(max(1, limit))
    )
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_repurchase_events(
    self,
    stock_code: str,
    limit: int = 5,
  ) -> List[StockRepurchaseEvent]:
    stmt = (
      select(StockRepurchaseEvent)
      .where(StockRepurchaseEvent.stock_code == stock_code.upper())
      .order_by(
        desc(StockRepurchaseEvent.latest_announce_date),
        desc(StockRepurchaseEvent.created_at),
      )
      .limit(max(1, limit))
    )
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def latest_sync_run(
    self,
    stock_code: str,
  ) -> Optional[AnnouncementSyncRun]:
    stmt = (
      select(AnnouncementSyncRun)
      .where(AnnouncementSyncRun.stock_code == stock_code.upper())
      .order_by(desc(AnnouncementSyncRun.started_at))
      .limit(1)
    )
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()

  async def upsert_announcements(
    self,
    items: Sequence[StockAnnouncement],
  ) -> int:
    return await self._merge_all(items)

  async def upsert_repurchase_events(
    self,
    items: Sequence[StockRepurchaseEvent],
  ) -> int:
    return await self._merge_all(items)

  async def save_sync_run(self, run: AnnouncementSyncRun) -> AnnouncementSyncRun:
    merged = await self.db.merge(run)
    await self.db.commit()
    await self.db.refresh(merged)
    return merged

  async def _merge_all(self, items: Iterable[object]) -> int:
    deduped = {}
    for item in items:
      item_id = getattr(item, "id", None)
      key = (item.__class__, item_id) if item_id else (item.__class__, id(item))
      deduped[key] = item

    count = 0
    for item in deduped.values():
      await self.db.merge(item)
      count += 1
    if count:
      await self.db.commit()
    return count
