"""
Business service for account watchlists.
"""

from typing import Iterable, List, Optional

from database.connection import get_async_db
from models.watchlist_item import WatchlistItem
from repositories.watchlist_repository import WatchlistRepository


DEFAULT_ACCOUNT_ID = "300000013250"


def normalize_stock_code(stock_code: str) -> str:
  return (stock_code or "").strip().upper()


class WatchlistService:
  def _account_id(self, account_id: Optional[str]) -> str:
    return (account_id or DEFAULT_ACCOUNT_ID).strip()

  async def get_watchlist(self, account_id: Optional[str] = None) -> List[WatchlistItem]:
    resolved_account_id = self._account_id(account_id)
    async for db in get_async_db():
      repo = WatchlistRepository(db)
      return await repo.find_by_account(resolved_account_id)
    return []

  async def add_item(
    self,
    *,
    stock_code: str,
    account_id: Optional[str] = None,
    instrument_name: Optional[str] = None,
    display_order: Optional[int] = None,
    group_name: Optional[str] = None,
    note: Optional[str] = None,
  ) -> WatchlistItem:
    resolved_account_id = self._account_id(account_id)
    normalized_code = normalize_stock_code(stock_code)
    if not normalized_code:
      raise ValueError("stock_code is required")

    async for db in get_async_db():
      repo = WatchlistRepository(db)
      existing = await repo.find_by_account_and_stock(
        resolved_account_id, normalized_code
      )
      order = (
        int(display_order)
        if display_order is not None
        else (
          int(existing.display_order)
          if existing is not None
          else await repo.next_display_order(resolved_account_id)
        )
      )
      item = WatchlistItem.create(
        account_id=resolved_account_id,
        stock_code=normalized_code,
        instrument_name=instrument_name
        if instrument_name is not None
        else (existing.instrument_name if existing else None),
        display_order=order,
        group_name=group_name
        if group_name is not None
        else (existing.group_name if existing else None),
        note=note if note is not None else (existing.note if existing else None),
      )
      return await repo.upsert_item(item)
    raise RuntimeError("database session unavailable")

  async def remove_item(
    self, stock_code: str, account_id: Optional[str] = None
  ) -> bool:
    resolved_account_id = self._account_id(account_id)
    normalized_code = normalize_stock_code(stock_code)
    async for db in get_async_db():
      repo = WatchlistRepository(db)
      return await repo.delete_by_account_and_stock(resolved_account_id, normalized_code)
    return False

  async def replace_watchlist(
    self,
    symbols: Iterable[str],
    account_id: Optional[str] = None,
  ) -> List[WatchlistItem]:
    resolved_account_id = self._account_id(account_id)
    normalized_symbols = []
    seen = set()
    for symbol in symbols:
      code = normalize_stock_code(symbol)
      if not code or code in seen:
        continue
      seen.add(code)
      normalized_symbols.append(code)

    async for db in get_async_db():
      repo = WatchlistRepository(db)
      items = [
        WatchlistItem.create(
          account_id=resolved_account_id,
          stock_code=code,
          display_order=index + 1,
        )
        for index, code in enumerate(normalized_symbols)
      ]
      return await repo.replace_account_items(resolved_account_id, items)
    return []

  async def reorder_watchlist(
    self,
    ordered_symbols: Iterable[str],
    account_id: Optional[str] = None,
  ) -> List[WatchlistItem]:
    resolved_account_id = self._account_id(account_id)
    existing = await self.get_watchlist(resolved_account_id)
    existing_by_code = {item.stock_code: item for item in existing}
    ordered_codes = []
    seen = set()
    for symbol in ordered_symbols:
      code = normalize_stock_code(symbol)
      if code and code in existing_by_code and code not in seen:
        ordered_codes.append(code)
        seen.add(code)
    for item in existing:
      if item.stock_code not in seen:
        ordered_codes.append(item.stock_code)
        seen.add(item.stock_code)

    async for db in get_async_db():
      repo = WatchlistRepository(db)
      saved: List[WatchlistItem] = []
      for index, code in enumerate(ordered_codes):
        current = existing_by_code[code]
        item = WatchlistItem.create(
          account_id=resolved_account_id,
          stock_code=code,
          instrument_name=current.instrument_name,
          display_order=index + 1,
          group_name=current.group_name,
          note=current.note,
        )
        saved.append(await repo.upsert_item(item))
      return saved
    return []
