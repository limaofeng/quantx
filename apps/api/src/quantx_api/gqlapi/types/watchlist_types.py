"""GraphQL types for the account watchlist and its groups."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

import strawberry
from quantx_infrastructure.models.watchlist_group import (
  WatchlistGroup as WatchlistGroupModel,
)
from quantx_infrastructure.models.watchlist_group_membership import (
  WatchlistGroupMembership as WatchlistGroupMembershipModel,
)
from quantx_infrastructure.models.watchlist_item import (
  WatchlistItem as WatchlistItemModel,
)


@strawberry.type(description="自选股分组")
class WatchlistGroup:
  id: strawberry.ID = strawberry.field(description="分组ID")
  account_id: str = strawberry.field(description="资金账号")
  name: str = strawberry.field(description="分组名称")
  display_order: int = strawberry.field(description="展示排序")
  item_count: int = strawberry.field(description="组内自选数量")
  created_at: Optional[datetime] = strawberry.field(description="创建时间")
  updated_at: Optional[datetime] = strawberry.field(description="更新时间")

  @staticmethod
  def from_model(model: WatchlistGroupModel) -> "WatchlistGroup":
    memberships = list(getattr(model, "memberships", None) or [])
    return WatchlistGroup(
      id=strawberry.ID(model.id),
      account_id=model.account_id,
      name=model.name,
      display_order=int(model.display_order or 0),
      item_count=len(memberships),
      created_at=model.created_at,
      updated_at=model.updated_at,
    )


@strawberry.type(description="自选股分组归属及组内排序")
class WatchlistGroupMembership:
  group_id: strawberry.ID = strawberry.field(description="分组ID")
  display_order: int = strawberry.field(description="组内展示排序")

  @staticmethod
  def from_model(model: WatchlistGroupMembershipModel) -> "WatchlistGroupMembership":
    return WatchlistGroupMembership(
      group_id=strawberry.ID(model.group_id),
      display_order=int(model.display_order or 0),
    )


@strawberry.type(description="自选股")
class WatchlistItem:
  id: strawberry.ID = strawberry.field(description="自选记录ID")
  account_id: str = strawberry.field(description="资金账号")
  stock_code: str = strawberry.field(description="证券代码")
  instrument_name: Optional[str] = strawberry.field(description="证券名称")
  display_order: int = strawberry.field(description="展示排序")
  groups: List[WatchlistGroup] = strawberry.field(description="所属分组")
  group_memberships: List[WatchlistGroupMembership] = strawberry.field(
    description="所属分组及组内排序"
  )
  note: Optional[str] = strawberry.field(description="备注")
  created_at: Optional[datetime] = strawberry.field(description="创建时间")
  updated_at: Optional[datetime] = strawberry.field(description="更新时间")

  @staticmethod
  def from_model(model: WatchlistItemModel) -> "WatchlistItem":
    memberships = list(getattr(model, "group_memberships", None) or [])
    ordered_memberships = sorted(
      memberships,
      key=lambda membership: (
        int(getattr(membership, "display_order", 0) or 0),
        str(getattr(membership, "group_id", "")),
      ),
    )
    groups: list[WatchlistGroup] = []
    for membership in memberships:
      group = getattr(membership, "group", None)
      if group is not None:
        groups.append(WatchlistGroup.from_model(group))
    return WatchlistItem(
      id=strawberry.ID(model.id),
      account_id=model.account_id,
      stock_code=model.stock_code,
      instrument_name=model.instrument_name,
      display_order=int(model.display_order or 0),
      groups=groups,
      group_memberships=[
        WatchlistGroupMembership.from_model(membership)
        for membership in ordered_memberships
      ],
      note=model.note,
      created_at=model.created_at,
      updated_at=model.updated_at,
    )


@strawberry.input(description="保存自选股输入")
class SaveWatchlistItemInput:
  stock_code: str = strawberry.field(description="证券代码")
  group_ids: List[strawberry.ID] = strawberry.field(description="精确设置的分组ID列表")
  account_id: Optional[str] = strawberry.field(default=None, description="资金账号")
  instrument_name: Optional[str] = strawberry.field(
    default=None, description="证券名称"
  )
  note: Optional[str] = strawberry.field(default=None, description="备注")


@strawberry.input(description="创建自选股分组输入")
class CreateWatchlistGroupInput:
  name: str = strawberry.field(description="分组名称")
  account_id: Optional[str] = strawberry.field(default=None, description="资金账号")
  initial_stock_codes: List[str] = strawberry.field(
    default_factory=list, description="创建时加入主自选的证券代码"
  )


@strawberry.input(description="重命名自选股分组输入")
class RenameWatchlistGroupInput:
  group_id: strawberry.ID = strawberry.field(description="分组ID")
  name: str = strawberry.field(description="新分组名称")
  account_id: Optional[str] = strawberry.field(default=None, description="资金账号")


@strawberry.input(description="删除自选股分组输入")
class DeleteWatchlistGroupInput:
  group_id: strawberry.ID = strawberry.field(description="分组ID")
  account_id: Optional[str] = strawberry.field(default=None, description="资金账号")


@strawberry.input(description="自选股主集合排序输入")
class ReorderWatchlistItemsInput:
  item_ids: List[strawberry.ID] = strawberry.field(description="完整排序后的自选记录ID")
  account_id: Optional[str] = strawberry.field(default=None, description="资金账号")


@strawberry.input(description="自选股分组排序输入")
class ReorderWatchlistGroupsInput:
  group_ids: List[strawberry.ID] = strawberry.field(description="完整排序后的分组ID")
  account_id: Optional[str] = strawberry.field(default=None, description="资金账号")


@strawberry.input(description="自选股分组内排序输入")
class ReorderWatchlistGroupItemsInput:
  group_id: strawberry.ID = strawberry.field(description="分组ID")
  item_ids: List[strawberry.ID] = strawberry.field(description="完整排序后的自选记录ID")
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
  group: Optional[WatchlistGroup] = strawberry.field(
    default=None, description="单个分组"
  )
  groups: List[WatchlistGroup] = strawberry.field(
    default_factory=list, description="分组列表"
  )
