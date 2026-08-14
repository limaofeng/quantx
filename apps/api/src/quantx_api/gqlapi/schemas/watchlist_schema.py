from typing import List, Optional

import strawberry

from quantx_api.gqlapi.resolvers.watchlist import WatchlistResolver
from quantx_api.gqlapi.security import authorized_account_id
from quantx_api.gqlapi.types.watchlist_types import (
  AddWatchlistItemInput,
  ReorderWatchlistInput,
  WatchlistItem,
  WatchlistMutationResult,
)


@strawberry.type(description="自选股查询")
class WatchlistQuery:
  @strawberry.field(description="获取自选股列表")
  async def watchlist(
    self, info: strawberry.types.Info, account_id: Optional[str] = None
  ) -> List[WatchlistItem]:
    return await WatchlistResolver.get_watchlist(
      authorized_account_id(info, account_id)
    )


@strawberry.type(description="自选股变更")
class WatchlistMutation:
  @strawberry.mutation(description="添加或更新自选股")
  async def add_watchlist_item(
    self,
    info: strawberry.types.Info,
    input: AddWatchlistItemInput,
  ) -> WatchlistMutationResult:
    account_id = authorized_account_id(info, input.account_id)
    return await WatchlistResolver.add_watchlist_item(
      account_id=account_id,
      stock_code=input.stock_code,
      instrument_name=input.instrument_name,
      display_order=input.display_order,
      group_name=input.group_name,
      note=input.note,
    )

  @strawberry.mutation(description="删除自选股")
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

  @strawberry.mutation(description="替换自选股列表")
  async def replace_watchlist(
    self,
    info: strawberry.types.Info,
    symbols: List[str],
    account_id: Optional[str] = None,
  ) -> WatchlistMutationResult:
    return await WatchlistResolver.replace_watchlist(
      symbols,
      authorized_account_id(info, account_id),
    )

  @strawberry.mutation(description="更新自选股排序")
  async def reorder_watchlist(
    self,
    info: strawberry.types.Info,
    input: ReorderWatchlistInput,
  ) -> WatchlistMutationResult:
    return await WatchlistResolver.reorder_watchlist(
      input.symbols,
      authorized_account_id(info, input.account_id),
    )
