"""Persistence operations for the account-scoped watchlist aggregate."""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.watchlist_group import WatchlistGroup
from quantx_infrastructure.models.watchlist_group_membership import (
  WatchlistGroupMembership,
)
from quantx_infrastructure.models.watchlist_item import WatchlistItem


class WatchlistRepository(BaseRepository[WatchlistItem]):
  """Repository with account predicates on every aggregate lookup."""

  model_class = WatchlistItem

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_by_account(self, account_id: str) -> List[WatchlistItem]:
    stmt = (
      select(WatchlistItem)
      .where(WatchlistItem.account_id == account_id)
      .options(
        selectinload(WatchlistItem.group_memberships).selectinload(
          WatchlistGroupMembership.group
        )
      )
      .execution_options(populate_existing=True)
      .order_by(WatchlistItem.display_order.asc(), WatchlistItem.created_at.asc())
    )
    result = await self.db.execute(stmt)
    return list(result.scalars().unique().all())

  async def find_by_account_and_stock(
    self, account_id: str, stock_code: str
  ) -> Optional[WatchlistItem]:
    stmt = (
      select(WatchlistItem)
      .where(
        WatchlistItem.account_id == account_id,
        WatchlistItem.stock_code == stock_code.strip().upper(),
      )
      .options(
        selectinload(WatchlistItem.group_memberships).selectinload(
          WatchlistGroupMembership.group
        )
      )
      .execution_options(populate_existing=True)
    )
    result = await self.db.execute(stmt)
    return result.scalars().unique().one_or_none()

  async def find_by_id_and_account(
    self, account_id: str, item_id: str
  ) -> Optional[WatchlistItem]:
    stmt = (
      select(WatchlistItem)
      .where(WatchlistItem.account_id == account_id, WatchlistItem.id == item_id)
      .options(
        selectinload(WatchlistItem.group_memberships).selectinload(
          WatchlistGroupMembership.group
        )
      )
      .execution_options(populate_existing=True)
    )
    result = await self.db.execute(stmt)
    return result.scalars().unique().one_or_none()

  async def next_display_order(self, account_id: str) -> int:
    stmt = select(func.max(WatchlistItem.display_order)).where(
      WatchlistItem.account_id == account_id
    )
    result = await self.db.execute(stmt)
    current = result.scalar_one_or_none()
    return int(current or 0) + 1

  async def find_groups_by_account(self, account_id: str) -> List[WatchlistGroup]:
    stmt = (
      select(WatchlistGroup)
      .where(WatchlistGroup.account_id == account_id)
      .options(
        selectinload(WatchlistGroup.memberships).selectinload(
          WatchlistGroupMembership.watchlist_item
        )
      )
      .execution_options(populate_existing=True)
      .order_by(WatchlistGroup.display_order.asc(), WatchlistGroup.created_at.asc())
    )
    result = await self.db.execute(stmt)
    return list(result.scalars().unique().all())

  async def find_group_by_id_and_account(
    self, account_id: str, group_id: str
  ) -> Optional[WatchlistGroup]:
    stmt = (
      select(WatchlistGroup)
      .where(WatchlistGroup.account_id == account_id, WatchlistGroup.id == group_id)
      .options(
        selectinload(WatchlistGroup.memberships).selectinload(
          WatchlistGroupMembership.watchlist_item
        )
      )
      .execution_options(populate_existing=True)
    )
    result = await self.db.execute(stmt)
    return result.scalars().unique().one_or_none()

  async def find_groups_by_ids_and_account(
    self, account_id: str, group_ids: Sequence[str]
  ) -> List[WatchlistGroup]:
    if not group_ids:
      return []
    stmt = select(WatchlistGroup).where(
      WatchlistGroup.account_id == account_id,
      WatchlistGroup.id.in_(list(group_ids)),
    )
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_group_by_name(
    self, account_id: str, name: str
  ) -> Optional[WatchlistGroup]:
    stmt = select(WatchlistGroup).where(
      WatchlistGroup.account_id == account_id,
      func.lower(WatchlistGroup.name) == name.strip().lower(),
    )
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()

  async def next_group_display_order(self, account_id: str) -> int:
    stmt = select(func.max(WatchlistGroup.display_order)).where(
      WatchlistGroup.account_id == account_id
    )
    result = await self.db.execute(stmt)
    current = result.scalar_one_or_none()
    return int(current or 0) + 1

  async def next_group_item_order(self, group_id: str) -> int:
    stmt = select(func.max(WatchlistGroupMembership.display_order)).where(
      WatchlistGroupMembership.group_id == group_id
    )
    result = await self.db.execute(stmt)
    current = result.scalar_one_or_none()
    return int(current or 0) + 1

  async def find_memberships_by_group(
    self, group_id: str
  ) -> List[WatchlistGroupMembership]:
    stmt = (
      select(WatchlistGroupMembership)
      .where(WatchlistGroupMembership.group_id == group_id)
      .options(selectinload(WatchlistGroupMembership.watchlist_item))
      .order_by(
        WatchlistGroupMembership.display_order.asc(),
        WatchlistGroupMembership.created_at.asc(),
        WatchlistGroupMembership.watchlist_item_id.asc(),
      )
    )
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_membership(
    self, group_id: str, item_id: str
  ) -> Optional[WatchlistGroupMembership]:
    return await self.db.get(
      WatchlistGroupMembership,
      {"group_id": group_id, "watchlist_item_id": item_id},
    )

  async def replace_item_memberships(
    self,
    item_id: str,
    memberships: Iterable[WatchlistGroupMembership],
  ) -> None:
    await self.db.execute(
      delete(WatchlistGroupMembership).where(
        WatchlistGroupMembership.watchlist_item_id == item_id
      )
    )
    for membership in memberships:
      self.db.add(membership)
    await self.db.flush()

  async def delete_item_memberships(self, item_id: str) -> int:
    result = await self.db.execute(
      delete(WatchlistGroupMembership).where(
        WatchlistGroupMembership.watchlist_item_id == item_id
      )
    )
    return int(result.rowcount or 0)

  async def delete_group_memberships(self, group_id: str) -> int:
    result = await self.db.execute(
      delete(WatchlistGroupMembership).where(
        WatchlistGroupMembership.group_id == group_id
      )
    )
    return int(result.rowcount or 0)

  async def save_item(self, item: WatchlistItem) -> WatchlistItem:
    """Add/update an item without committing the surrounding transaction."""
    self.db.add(item)
    await self.db.flush()
    return item
