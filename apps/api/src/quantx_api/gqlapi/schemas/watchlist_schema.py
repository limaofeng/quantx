"""GraphQL schema for the account watchlist aggregate."""

from __future__ import annotations

from typing import List, Optional

import strawberry

from quantx_api.gqlapi.resolvers.watchlist import WatchlistResolver
from quantx_api.gqlapi.security import authorized_account_id
from quantx_api.gqlapi.types.watchlist_types import (
  CreateWatchlistGroupInput,
  DeleteWatchlistGroupInput,
  RenameWatchlistGroupInput,
  ReorderWatchlistGroupItemsInput,
  ReorderWatchlistGroupsInput,
  ReorderWatchlistItemsInput,
  SaveWatchlistItemInput,
  WatchlistGroup,
  WatchlistItem,
  WatchlistMutationResult,
)


@strawberry.type(description="自选股查询")
class WatchlistQuery:
  @strawberry.field(description="获取全部自选股列表")
  async def watchlist(
    self, info: strawberry.types.Info, account_id: Optional[str] = None
  ) -> List[WatchlistItem]:
    return await WatchlistResolver.get_watchlist(
      authorized_account_id(info, account_id)
    )

  @strawberry.field(description="获取自选股分组列表")
  async def watchlist_groups(
    self, info: strawberry.types.Info, account_id: Optional[str] = None
  ) -> List[WatchlistGroup]:
    return await WatchlistResolver.get_watchlist_groups(
      authorized_account_id(info, account_id)
    )


@strawberry.type(description="自选股变更")
class WatchlistMutation:
  @strawberry.mutation(description="保存自选股并精确设置所属分组")
  async def save_watchlist_item(
    self,
    info: strawberry.types.Info,
    input: SaveWatchlistItemInput,
  ) -> WatchlistMutationResult:
    return await WatchlistResolver.save_watchlist_item(
      account_id=authorized_account_id(info, input.account_id),
      stock_code=input.stock_code,
      group_ids=[str(group_id) for group_id in input.group_ids],
      instrument_name=input.instrument_name,
      note=input.note,
    )

  @strawberry.mutation(description="删除主自选中的证券并清理全部分组关系")
  async def remove_watchlist_item(
    self,
    info: strawberry.types.Info,
    stock_code: str,
    account_id: Optional[str] = None,
  ) -> WatchlistMutationResult:
    return await WatchlistResolver.remove_watchlist_item(
      stock_code,
      authorized_account_id(info, account_id),
    )

  @strawberry.mutation(description="创建自选股分组")
  async def create_watchlist_group(
    self,
    info: strawberry.types.Info,
    input: CreateWatchlistGroupInput,
  ) -> WatchlistMutationResult:
    return await WatchlistResolver.create_watchlist_group(
      account_id=authorized_account_id(info, input.account_id),
      name=input.name,
      initial_stock_codes=input.initial_stock_codes,
    )

  @strawberry.mutation(description="重命名自选股分组")
  async def rename_watchlist_group(
    self,
    info: strawberry.types.Info,
    input: RenameWatchlistGroupInput,
  ) -> WatchlistMutationResult:
    return await WatchlistResolver.rename_watchlist_group(
      account_id=authorized_account_id(info, input.account_id),
      group_id=str(input.group_id),
      name=input.name,
    )

  @strawberry.mutation(description="删除自选股分组但保留主自选")
  async def delete_watchlist_group(
    self,
    info: strawberry.types.Info,
    input: DeleteWatchlistGroupInput,
  ) -> WatchlistMutationResult:
    return await WatchlistResolver.delete_watchlist_group(
      account_id=authorized_account_id(info, input.account_id),
      group_id=str(input.group_id),
    )

  @strawberry.mutation(description="更新主自选完整排序")
  async def reorder_watchlist_items(
    self,
    info: strawberry.types.Info,
    input: ReorderWatchlistItemsInput,
  ) -> WatchlistMutationResult:
    return await WatchlistResolver.reorder_watchlist_items(
      account_id=authorized_account_id(info, input.account_id),
      item_ids=[str(item_id) for item_id in input.item_ids],
    )

  @strawberry.mutation(description="更新自选股分组完整排序")
  async def reorder_watchlist_groups(
    self,
    info: strawberry.types.Info,
    input: ReorderWatchlistGroupsInput,
  ) -> WatchlistMutationResult:
    return await WatchlistResolver.reorder_watchlist_groups(
      account_id=authorized_account_id(info, input.account_id),
      group_ids=[str(group_id) for group_id in input.group_ids],
    )

  @strawberry.mutation(description="更新分组内自选股完整排序")
  async def reorder_watchlist_group_items(
    self,
    info: strawberry.types.Info,
    input: ReorderWatchlistGroupItemsInput,
  ) -> WatchlistMutationResult:
    return await WatchlistResolver.reorder_watchlist_group_items(
      account_id=authorized_account_id(info, input.account_id),
      group_id=str(input.group_id),
      item_ids=[str(item_id) for item_id in input.item_ids],
    )
