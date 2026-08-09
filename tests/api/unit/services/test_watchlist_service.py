import pytest
import quantx_infrastructure.services.watchlist_service as watchlist_service_module
from quantx_infrastructure.models.watchlist_item import WatchlistItem
from quantx_infrastructure.services.watchlist_service import (
  WatchlistService,
  normalize_stock_code,
)


class FakeWatchlistRepository:
  store = {}

  def __init__(self, db):
    self.db = db

  async def find_by_account(self, account_id: str):
    items = [
      item
      for (item_account_id, _), item in self.store.items()
      if item_account_id == account_id
    ]
    return sorted(items, key=lambda item: (item.display_order, item.stock_code))

  async def find_by_account_and_stock(self, account_id: str, stock_code: str):
    return self.store.get((account_id, stock_code.upper()))

  async def next_display_order(self, account_id: str) -> int:
    items = await self.find_by_account(account_id)
    return max([int(item.display_order or 0) for item in items] or [0]) + 1

  async def upsert_item(self, item: WatchlistItem):
    self.store[(item.account_id, item.stock_code)] = item
    return item

  async def delete_by_account_and_stock(self, account_id: str, stock_code: str):
    return self.store.pop((account_id, stock_code.upper()), None) is not None

  async def delete_by_account(self, account_id: str):
    keys = [key for key in self.store if key[0] == account_id]
    for key in keys:
      self.store.pop(key, None)
    return len(keys)

  async def replace_account_items(self, account_id: str, items):
    await self.delete_by_account(account_id)
    for item in items:
      self.store[(item.account_id, item.stock_code)] = item
    return items


@pytest.fixture(autouse=True)
def fake_watchlist_repository(monkeypatch):
  FakeWatchlistRepository.store = {}

  async def fake_get_async_db():
    yield object()

  monkeypatch.setattr(
    watchlist_service_module, "WatchlistRepository", FakeWatchlistRepository
  )
  monkeypatch.setattr(watchlist_service_module, "get_async_db", fake_get_async_db)


def test_normalize_stock_code():
  assert normalize_stock_code(" 600900.sh ") == "600900.SH"
  assert normalize_stock_code("") == ""


@pytest.mark.asyncio
async def test_watchlist_add_updates_existing_item_and_preserves_order():
  service = WatchlistService()

  first = await service.add_item(
    account_id="acct-1",
    stock_code=" 600900.sh ",
    instrument_name="长江电力",
  )
  updated = await service.add_item(
    account_id="acct-1",
    stock_code="600900.SH",
    instrument_name="长江电力A",
  )

  items = await service.get_watchlist("acct-1")

  assert first.id == updated.id
  assert [item.stock_code for item in items] == ["600900.SH"]
  assert items[0].instrument_name == "长江电力A"
  assert items[0].display_order == 1


@pytest.mark.asyncio
async def test_watchlist_replace_reorders_and_removes_items():
  service = WatchlistService()

  items = await service.replace_watchlist(
    ["600900.sh", "", "600900.SH", "002594.SZ"],
    account_id="acct-1",
  )

  assert [item.stock_code for item in items] == ["600900.SH", "002594.SZ"]
  assert [item.display_order for item in items] == [1, 2]

  reordered = await service.reorder_watchlist(
    ["002594.SZ", "600900.SH"],
    account_id="acct-1",
  )
  assert [item.stock_code for item in reordered] == ["002594.SZ", "600900.SH"]
  assert [item.display_order for item in reordered] == [1, 2]

  removed = await service.remove_item("600900.SH", account_id="acct-1")
  remaining = await service.get_watchlist("acct-1")

  assert removed is True
  assert [item.stock_code for item in remaining] == ["002594.SZ"]
