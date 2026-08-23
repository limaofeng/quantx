from __future__ import annotations

import pytest
import quantx_infrastructure.services.watchlist_service as watchlist_module
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.watchlist_group import WatchlistGroup
from quantx_infrastructure.models.watchlist_group_membership import (
  WatchlistGroupMembership,
)
from quantx_infrastructure.models.watchlist_item import WatchlistItem
from quantx_infrastructure.services.watchlist_service import WatchlistService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def watchlist_database(monkeypatch: pytest.MonkeyPatch):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          WatchlistItem.__table__,
          WatchlistGroup.__table__,
          WatchlistGroupMembership.__table__,
        ],
      )
    )

  async def get_database():
    async with session_factory() as session:
      yield session

  monkeypatch.setattr(watchlist_module, "get_async_db", get_database)
  yield
  await engine.dispose()


@pytest.mark.asyncio
async def test_groups_are_many_to_many_and_empty_groups_are_preserved(
  watchlist_database,
):
  service = WatchlistService()
  primary = await service.create_group(
    account_id="acct-a",
    name="核心",
    initial_stock_codes=["600000.SH", "000001.SZ"],
  )
  secondary = await service.create_group(account_id="acct-a", name="波段")

  saved = await service.save_item(
    account_id="acct-a",
    stock_code="600000.SH",
    group_ids=[primary.id, secondary.id],
  )
  assert [membership.group_id for membership in saved.group_memberships] == [
    primary.id,
    secondary.id,
  ]
  groups = await service.get_groups("acct-a")
  assert [group.name for group in groups] == ["核心", "波段"]
  assert [len(group.memberships) for group in groups] == [2, 1]

  with pytest.raises(ValueError, match="already exists"):
    await service.create_group(account_id="acct-a", name=" 核心 ")


@pytest.mark.asyncio
async def test_group_names_are_trimmed_and_limited_to_80_characters(watchlist_database):
  service = WatchlistService()
  name = "x" * 80
  group = await service.create_group(account_id="acct-a", name=f" {name} ")
  assert group.name == name

  with pytest.raises(ValueError, match="80"):
    await service.create_group(account_id="acct-a", name="x" * 81)

  with pytest.raises(ValueError, match="80"):
    await service.rename_group(account_id="acct-a", group_id=group.id, name="y" * 81)


@pytest.mark.asyncio
async def test_delete_group_keeps_main_item_and_remove_clears_memberships(
  watchlist_database,
):
  service = WatchlistService()
  group = await service.create_group(
    account_id="acct-a", name="临时", initial_stock_codes=["600000.SH"]
  )
  assert await service.delete_group(account_id="acct-a", group_id=group.id)
  assert [item.stock_code for item in await service.get_watchlist("acct-a")] == [
    "600000.SH"
  ]
  assert await service.get_groups("acct-a") == []

  another = await service.create_group(
    account_id="acct-a", name="保留", initial_stock_codes=["600000.SH"]
  )
  assert await service.remove_item("600000.SH", "acct-a")
  assert await service.get_watchlist("acct-a") == []
  assert (await service.get_groups("acct-a"))[0].memberships == []
  assert another.id


@pytest.mark.asyncio
async def test_cross_account_group_id_is_rejected_before_item_write(watchlist_database):
  service = WatchlistService()
  group = await service.create_group(account_id="acct-a", name="A")

  with pytest.raises(ValueError, match="belong to the requested account"):
    await service.save_item(
      account_id="acct-b", stock_code="600000.SH", group_ids=[group.id]
    )
  assert await service.get_watchlist("acct-b") == []


@pytest.mark.asyncio
async def test_reorders_require_complete_current_collection(watchlist_database):
  service = WatchlistService()
  first = await service.save_item(account_id="acct-a", stock_code="600000.SH")
  second = await service.save_item(account_id="acct-a", stock_code="000001.SZ")
  with pytest.raises(ValueError, match="complete current collection"):
    await service.reorder_items(account_id="acct-a", item_ids=[first.id])

  ordered = await service.reorder_items(
    account_id="acct-a", item_ids=[second.id, first.id]
  )
  assert [item.id for item in ordered] == [second.id, first.id]
  assert [item.display_order for item in ordered] == [1, 2]


@pytest.mark.asyncio
async def test_group_item_reorder_round_trips_membership_order(watchlist_database):
  service = WatchlistService()
  group = await service.create_group(
    account_id="acct-a",
    name="排序",
    initial_stock_codes=["600000.SH", "000001.SZ"],
  )
  current_ids = [membership.watchlist_item_id for membership in group.memberships]

  reordered = await service.reorder_group_items(
    account_id="acct-a",
    group_id=group.id,
    item_ids=list(reversed(current_ids)),
  )

  assert [membership.watchlist_item_id for membership in reordered.memberships] == list(
    reversed(current_ids)
  )
  assert [membership.display_order for membership in reordered.memberships] == [1, 2]
