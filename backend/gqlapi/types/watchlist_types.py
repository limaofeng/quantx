from datetime import datetime
from typing import List, Optional

import strawberry

from models.watchlist_item import WatchlistItem as WatchlistItemModel


@strawberry.type(description="自选股")
class WatchlistItem:
  id: str = strawberry.field(description="自选记录ID")
  account_id: str = strawberry.field(description="资金账号")
  stock_code: str = strawberry.field(description="证券代码")
  instrument_name: Optional[str] = strawberry.field(description="证券名称")
  display_order: int = strawberry.field(description="展示排序")
  group_name: Optional[str] = strawberry.field(description="分组")
  note: Optional[str] = strawberry.field(description="备注")
  created_at: Optional[datetime] = strawberry.field(description="创建时间")
  updated_at: Optional[datetime] = strawberry.field(description="更新时间")

  @staticmethod
  def from_model(model: WatchlistItemModel) -> "WatchlistItem":
    return WatchlistItem(
      id=model.id,
      account_id=model.account_id,
      stock_code=model.stock_code,
      instrument_name=model.instrument_name,
      display_order=int(model.display_order or 0),
      group_name=model.group_name,
      note=model.note,
      created_at=model.created_at,
      updated_at=model.updated_at,
    )


@strawberry.input(description="添加自选股输入")
class AddWatchlistItemInput:
  stock_code: str = strawberry.field(description="证券代码")
  account_id: Optional[str] = strawberry.field(default=None, description="资金账号")
  instrument_name: Optional[str] = strawberry.field(
    default=None, description="证券名称"
  )
  display_order: Optional[int] = strawberry.field(default=None, description="排序")
  group_name: Optional[str] = strawberry.field(default=None, description="分组")
  note: Optional[str] = strawberry.field(default=None, description="备注")


@strawberry.input(description="自选股排序输入")
class ReorderWatchlistInput:
  symbols: List[str] = strawberry.field(description="排序后的证券代码列表")
  account_id: Optional[str] = strawberry.field(default=None, description="资金账号")


@strawberry.type(description="自选股变更结果")
class WatchlistMutationResult:
  success: bool = strawberry.field(description="是否成功")
  message: str = strawberry.field(description="结果说明")
  item: Optional[WatchlistItem] = strawberry.field(
    default=None, description="单条自选股"
  )
  items: List[WatchlistItem] = strawberry.field(
    default_factory=list, description="自选股列表"
  )
