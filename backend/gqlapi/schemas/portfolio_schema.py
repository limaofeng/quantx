from typing import List, Optional

import strawberry

from ..resolvers.account import AccountResolver
from ..resolvers.closed_position_cycles import ClosedPositionCycleResolver
from ..resolvers.daily_asset_snapshots import DailyAssetSnapshotResolver
from ..resolvers.positions import PositionResolver
from ..security import authorized_account_id
from ..types.portfolio_types import (
  Account,
  ClosedPositionCyclePage,
  DailyAssetSnapshot,
  PortfolioSummary,
  Position,
)


@strawberry.type(description="持仓账户相关查询")
class PortfolioQuery:
  @strawberry.field(description="获取持仓列表")
  async def positions(
    self, info: strawberry.types.Info, account_id: Optional[str] = None
  ) -> List[Position]:
    return await PositionResolver.get_positions(authorized_account_id(info, account_id))

  @strawberry.field(description="获取单个持仓信息")
  async def position(
    self,
    info: strawberry.types.Info,
    stock_code: str,
    account_id: Optional[str] = None,
  ) -> Optional[Position]:
    return await PositionResolver.get_position(
      stock_code, authorized_account_id(info, account_id)
    )

  @strawberry.field(description="获取账户信息")
  async def account(
    self, info: strawberry.types.Info, account_id: str
  ) -> Optional[Account]:
    return await AccountResolver.get_account_async(
      authorized_account_id(info, account_id)
    )

  @strawberry.field(description="获取当前账户信息")
  async def current_account(self, info: strawberry.types.Info) -> Optional[Account]:
    return await AccountResolver.get_account_async(authorized_account_id(info))

  @strawberry.field(description="获取持仓表现汇总")
  async def portfolio_summary(
    self,
    info: strawberry.types.Info,
    account_id: Optional[str] = None,
  ) -> PortfolioSummary:
    """获取持仓表现汇总信息"""
    from ..resolvers.portfolio_summary import PortfolioSummaryResolver

    return await PortfolioSummaryResolver.get_portfolio_summary(
      authorized_account_id(info, account_id)
    )

  @strawberry.field(description="获取每日收盘资产快照")
  async def daily_asset_snapshots(
    self,
    info: strawberry.types.Info,
    account_id: Optional[str] = None,
    strategy_run_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 366,
  ) -> List[DailyAssetSnapshot]:
    return await DailyAssetSnapshotResolver.get_daily_asset_snapshots(
      account_id=authorized_account_id(info, account_id),
      strategy_run_id=strategy_run_id,
      scope_type=scope_type,
      start_date=start_date,
      end_date=end_date,
      limit=limit,
    )

  @strawberry.field(description="分页获取已清仓持仓周期")
  async def closed_position_cycles(
    self,
    info: strawberry.types.Info,
    account_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
  ) -> ClosedPositionCyclePage:
    return await ClosedPositionCycleResolver.get_page(
      authorized_account_id(info, account_id), start_date, end_date, limit, offset
    )
