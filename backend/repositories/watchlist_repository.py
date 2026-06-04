"""
Repository for account watchlist items.
"""

from typing import List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.relational_base import BaseRepository
from models.watchlist_item import WatchlistItem


class WatchlistRepository(BaseRepository[WatchlistItem]):
  model_class = WatchlistItem

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_by_account(self, account_id: str) -> List[WatchlistItem]:
    stmt = (
      select(WatchlistItem)
      .where(WatchlistItem.account_id == account_id)
      .order_by(WatchlistItem.display_order.asc(), WatchlistItem.created_at.asc())
    )
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_by_account_and_stock(
    self, account_id: str, stock_code: str
  ) -> Optional[WatchlistItem]:
    stmt = select(WatchlistItem).where(
      WatchlistItem.account_id == account_id,
      WatchlistItem.stock_code == stock_code.upper(),
    )
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()

  async def next_display_order(self, account_id: str) -> int:
    stmt = select(func.max(WatchlistItem.display_order)).where(
      WatchlistItem.account_id == account_id
    )
    result = await self.db.execute(stmt)
    current = result.scalar_one_or_none()
    return int(current or 0) + 1

  async def upsert_item(self, item: WatchlistItem) -> WatchlistItem:
    merged = await self.db.merge(item)
    await self.db.commit()
    await self.db.refresh(merged)
    return merged

  async def delete_by_account_and_stock(self, account_id: str, stock_code: str) -> bool:
    stmt = delete(WatchlistItem).where(
      WatchlistItem.account_id == account_id,
      WatchlistItem.stock_code == stock_code.upper(),
    )
    result = await self.db.execute(stmt)
    await self.db.commit()
    return bool(result.rowcount)

  async def delete_by_account(self, account_id: str) -> int:
    stmt = delete(WatchlistItem).where(WatchlistItem.account_id == account_id)
    result = await self.db.execute(stmt)
    await self.db.commit()
    return int(result.rowcount or 0)

  async def replace_account_items(
    self, account_id: str, items: List[WatchlistItem]
  ) -> List[WatchlistItem]:
    await self.delete_by_account(account_id)
    saved: List[WatchlistItem] = []
    for item in items:
      self.db.add(item)
      saved.append(item)
    await self.db.commit()
    for item in saved:
      await self.db.refresh(item)
    return saved
