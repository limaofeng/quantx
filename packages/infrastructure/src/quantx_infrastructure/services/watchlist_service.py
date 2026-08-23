"""Atomic business operations for the account watchlist aggregate."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import List, Optional, TypeVar

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import NO_VALUE

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.watchlist_group import WatchlistGroup
from quantx_infrastructure.models.watchlist_group_membership import (
  WatchlistGroupMembership,
)
from quantx_infrastructure.models.watchlist_item import WatchlistItem
from quantx_infrastructure.repositories.watchlist_repository import WatchlistRepository

DEFAULT_ACCOUNT_ID = "300000013250"
MAX_GROUP_NAME_LENGTH = 80
T = TypeVar("T")


def normalize_stock_code(stock_code: str) -> str:
  return (stock_code or "").strip().upper()


def normalize_group_name(name: str) -> str:
  return (name or "").strip()


def _unique_normalized(values: Iterable[str]) -> list[str]:
  result: list[str] = []
  seen: set[str] = set()
  for value in values:
    normalized = str(value).strip()
    if normalized and normalized not in seen:
      result.append(normalized)
      seen.add(normalized)
  return result


class WatchlistService:
  """The service is the transaction boundary for all watchlist mutations."""

  def _account_id(self, account_id: Optional[str]) -> str:
    resolved = (account_id or DEFAULT_ACCOUNT_ID).strip()
    if not resolved:
      raise ValueError("account_id is required")
    return resolved

  async def _run(self, operation: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async for db in get_async_db():
      try:
        return await operation(db)
      except IntegrityError as exc:
        await db.rollback()
        raise ValueError("watchlist data conflicts with an existing record") from exc
      except Exception:
        await db.rollback()
        raise
    raise RuntimeError("database session unavailable")

  async def get_watchlist(self, account_id: Optional[str] = None) -> List[WatchlistItem]:
    resolved_account_id = self._account_id(account_id)

    async def operation(db: AsyncSession) -> List[WatchlistItem]:
      return await WatchlistRepository(db).find_by_account(resolved_account_id)

    return await self._run(operation)

  async def get_groups(self, account_id: Optional[str] = None) -> List[WatchlistGroup]:
    resolved_account_id = self._account_id(account_id)

    async def operation(db: AsyncSession) -> List[WatchlistGroup]:
      return await WatchlistRepository(db).find_groups_by_account(resolved_account_id)

    return await self._run(operation)

  async def _validate_groups(
    self,
    repo: WatchlistRepository,
    account_id: str,
    group_ids: Sequence[str],
  ) -> list[str]:
    normalized = _unique_normalized(group_ids)
    groups = await repo.find_groups_by_ids_and_account(account_id, normalized)
    if len(groups) != len(normalized):
      # Do not reveal whether an ID belongs to another account.  Most
      # importantly, this validation runs before any aggregate write.
      raise ValueError("all group_ids must belong to the requested account")
    return normalized

  async def _set_item_groups(
    self,
    repo: WatchlistRepository,
    item: WatchlistItem,
    group_ids: Sequence[str],
  ) -> None:
    loaded_memberships = inspect(item).attrs.group_memberships.loaded_value
    old_memberships = (
      [] if loaded_memberships is NO_VALUE else list(loaded_memberships or [])
    )
    old_orders = {
      membership.group_id: int(membership.display_order or 0)
      for membership in old_memberships
    }
    memberships: list[WatchlistGroupMembership] = []
    for group_id in group_ids:
      order = old_orders.get(group_id)
      if order is None:
        order = await repo.next_group_item_order(group_id)
      memberships.append(
        WatchlistGroupMembership.create(
          group_id=group_id,
          watchlist_item_id=item.id,
          display_order=order,
        )
      )
    # Delete loaded ORM rows one by one before inserting replacement rows.  A
    # bulk DELETE leaves stale composite-key instances in SQLAlchemy's identity
    # map and can silently suppress a replacement membership.
    for membership in old_memberships:
      await repo.db.delete(membership)
    if old_memberships:
      await repo.db.flush()
    for membership in memberships:
      repo.db.add(membership)
    await repo.db.flush()

  async def save_item(
    self,
    *,
    account_id: Optional[str],
    stock_code: str,
    group_ids: Sequence[str] = (),
    instrument_name: Optional[str] = None,
    note: Optional[str] = None,
  ) -> WatchlistItem:
    resolved_account_id = self._account_id(account_id)
    normalized_code = normalize_stock_code(stock_code)
    if not normalized_code:
      raise ValueError("stock_code is required")

    async def operation(db: AsyncSession) -> WatchlistItem:
      repo = WatchlistRepository(db)
      # Validate all IDs before creating or updating the main item.  This is
      # what makes a cross-account group request all-or-nothing.
      normalized_group_ids = await self._validate_groups(
        repo, resolved_account_id, group_ids
      )
      existing = await repo.find_by_account_and_stock(
        resolved_account_id, normalized_code
      )
      if existing is None:
        item = WatchlistItem.create(
          account_id=resolved_account_id,
          stock_code=normalized_code,
          display_order=await repo.next_display_order(resolved_account_id),
          instrument_name=instrument_name,
          note=note,
        )
        await repo.save_item(item)
      else:
        item = existing
        if instrument_name is not None:
          item.instrument_name = instrument_name
        if note is not None:
          item.note = note
        await db.flush()

      await self._set_item_groups(repo, item, normalized_group_ids)
      await db.commit()
      saved = await repo.find_by_account_and_stock(
        resolved_account_id, normalized_code
      )
      if saved is None:  # pragma: no cover - defensive against a broken DB
        raise RuntimeError("watchlist item disappeared after save")
      return saved

    return await self._run(operation)

  async def remove_item(
    self, stock_code: str, account_id: Optional[str] = None
  ) -> bool:
    resolved_account_id = self._account_id(account_id)
    normalized_code = normalize_stock_code(stock_code)
    if not normalized_code:
      return False

    async def operation(db: AsyncSession) -> bool:
      repo = WatchlistRepository(db)
      item = await repo.find_by_account_and_stock(resolved_account_id, normalized_code)
      if item is None:
        return False
      # Explicit deletion keeps the cascade semantics correct on test/dev
      # databases where SQLite foreign keys may not be enabled.
      for membership in list(item.group_memberships or []):
        await db.delete(membership)
      await db.delete(item)
      await db.commit()
      return True

    return await self._run(operation)

  async def create_group(
    self,
    *,
    account_id: Optional[str],
    name: str,
    initial_stock_codes: Sequence[str] = (),
  ) -> WatchlistGroup:
    resolved_account_id = self._account_id(account_id)
    normalized_name = normalize_group_name(name)
    if not normalized_name:
      raise ValueError("group name is required")
    if len(normalized_name) > MAX_GROUP_NAME_LENGTH:
      raise ValueError("group name must be between 1 and 80 characters")
    stock_codes = _unique_normalized(
      normalize_stock_code(code) for code in initial_stock_codes
    )

    async def operation(db: AsyncSession) -> WatchlistGroup:
      repo = WatchlistRepository(db)
      if await repo.find_group_by_name(resolved_account_id, normalized_name):
        raise ValueError("a group with this name already exists")
      group = WatchlistGroup.create(
        account_id=resolved_account_id,
        name=normalized_name,
        display_order=await repo.next_group_display_order(resolved_account_id),
      )
      db.add(group)
      await db.flush()

      for index, stock_code in enumerate(stock_codes, start=1):
        item = await repo.find_by_account_and_stock(
          resolved_account_id, stock_code
        )
        if item is None:
          item = WatchlistItem.create(
            account_id=resolved_account_id,
            stock_code=stock_code,
            display_order=await repo.next_display_order(resolved_account_id),
          )
          await repo.save_item(item)
        db.add(
          WatchlistGroupMembership.create(
            group_id=group.id,
            watchlist_item_id=item.id,
            display_order=index,
          )
        )
      await db.flush()
      await db.commit()
      saved = await repo.find_group_by_id_and_account(resolved_account_id, group.id)
      if saved is None:  # pragma: no cover - defensive against a broken DB
        raise RuntimeError("watchlist group disappeared after create")
      return saved

    return await self._run(operation)

  async def rename_group(
    self, *, account_id: Optional[str], group_id: str, name: str
  ) -> WatchlistGroup:
    resolved_account_id = self._account_id(account_id)
    normalized_name = normalize_group_name(name)
    if not normalized_name:
      raise ValueError("group name is required")
    if len(normalized_name) > MAX_GROUP_NAME_LENGTH:
      raise ValueError("group name must be between 1 and 80 characters")

    async def operation(db: AsyncSession) -> WatchlistGroup:
      repo = WatchlistRepository(db)
      group = await repo.find_group_by_id_and_account(resolved_account_id, group_id)
      if group is None:
        raise ValueError("watchlist group not found")
      duplicate = await repo.find_group_by_name(resolved_account_id, normalized_name)
      if duplicate is not None and duplicate.id != group.id:
        raise ValueError("a group with this name already exists")
      group.name = normalized_name
      await db.commit()
      saved = await repo.find_group_by_id_and_account(resolved_account_id, group.id)
      if saved is None:  # pragma: no cover
        raise RuntimeError("watchlist group disappeared after rename")
      return saved

    return await self._run(operation)

  async def delete_group(
    self, *, account_id: Optional[str], group_id: str
  ) -> bool:
    resolved_account_id = self._account_id(account_id)

    async def operation(db: AsyncSession) -> bool:
      repo = WatchlistRepository(db)
      group = await repo.find_group_by_id_and_account(resolved_account_id, group_id)
      if group is None:
        return False
      for membership in list(group.memberships or []):
        await db.delete(membership)
      await db.delete(group)
      await db.commit()
      return True

    return await self._run(operation)

  @staticmethod
  def _require_complete_order(
    requested: Sequence[str], available: Sequence[str], label: str
  ) -> list[str]:
    requested_ids = [str(value).strip() for value in requested]
    available_ids = [str(value) for value in available]
    if len(requested_ids) != len(set(requested_ids)):
      raise ValueError(f"{label} must not contain duplicates")
    if set(requested_ids) != set(available_ids):
      raise ValueError(f"{label} must contain the complete current collection")
    return requested_ids

  async def reorder_items(
    self, *, account_id: Optional[str], item_ids: Sequence[str]
  ) -> List[WatchlistItem]:
    resolved_account_id = self._account_id(account_id)

    async def operation(db: AsyncSession) -> List[WatchlistItem]:
      repo = WatchlistRepository(db)
      items = await repo.find_by_account(resolved_account_id)
      ordered_ids = self._require_complete_order(
        item_ids, [item.id for item in items], "item_ids"
      )
      by_id = {item.id: item for item in items}
      for index, item_id in enumerate(ordered_ids, start=1):
        by_id[item_id].display_order = index
      await db.flush()
      await db.commit()
      return await repo.find_by_account(resolved_account_id)

    return await self._run(operation)

  async def reorder_groups(
    self, *, account_id: Optional[str], group_ids: Sequence[str]
  ) -> List[WatchlistGroup]:
    resolved_account_id = self._account_id(account_id)

    async def operation(db: AsyncSession) -> List[WatchlistGroup]:
      repo = WatchlistRepository(db)
      groups = await repo.find_groups_by_account(resolved_account_id)
      ordered_ids = self._require_complete_order(
        group_ids, [group.id for group in groups], "group_ids"
      )
      by_id = {group.id: group for group in groups}
      for index, group_id in enumerate(ordered_ids, start=1):
        by_id[group_id].display_order = index
      await db.flush()
      await db.commit()
      return await repo.find_groups_by_account(resolved_account_id)

    return await self._run(operation)

  async def reorder_group_items(
    self,
    *,
    account_id: Optional[str],
    group_id: str,
    item_ids: Sequence[str],
  ) -> WatchlistGroup:
    resolved_account_id = self._account_id(account_id)

    async def operation(db: AsyncSession) -> WatchlistGroup:
      repo = WatchlistRepository(db)
      group = await repo.find_group_by_id_and_account(resolved_account_id, group_id)
      if group is None:
        raise ValueError("watchlist group not found")
      memberships = await repo.find_memberships_by_group(group.id)
      ordered_ids = self._require_complete_order(
        item_ids,
        [membership.watchlist_item_id for membership in memberships],
        "item_ids",
      )
      by_id = {membership.watchlist_item_id: membership for membership in memberships}
      for index, item_id in enumerate(ordered_ids, start=1):
        by_id[item_id].display_order = index
      await db.flush()
      await db.commit()
      saved = await repo.find_group_by_id_and_account(resolved_account_id, group.id)
      if saved is None:  # pragma: no cover
        raise RuntimeError("watchlist group disappeared after reorder")
      return saved

    return await self._run(operation)
