from typing import List, Optional

import strawberry

from ..resolvers.account import AccountResolver
from ..resolvers.positions import PositionResolver
from ..resolvers.daily_asset_snapshots import DailyAssetSnapshotResolver
from ..resolvers.closed_position_cycles import ClosedPositionCycleResolver
from ..types.portfolio_types import (
  Account,
  DailyAssetSnapshot,
  PortfolioSummary,
  Position,
  ClosedPositionCyclePage,
)


@strawberry.type(description="持仓账户相关查询")
class PortfolioQuery:
  @strawberry.field(description="获取持仓列表")
  async def positions(self, account_id: Optional[str] = None) -> List[Position]:
    return await PositionResolver.get_positions(account_id)

  @strawberry.field(description="获取单个持仓信息")
  async def position(
    self, stock_code: str, account_id: Optional[str] = None
  ) -> Optional[Position]:
    return await PositionResolver.get_position(stock_code, account_id)

  @strawberry.field(description="获取账户信息")
  async def account(self, account_id: str) -> Optional[Account]:
    return await AccountResolver.get_account_async(account_id)

  @strawberry.field(description="获取当前账户信息")
  async def current_account(self) -> Optional[Account]:
    return await AccountResolver.get_current_account_async()

  @strawberry.field(description="获取持仓表现汇总")
  async def portfolio_summary(
    self, account_id: Optional[str] = None
  ) -> PortfolioSummary:
    """获取持仓表现汇总信息"""
    from ..resolvers.portfolio_summary import PortfolioSummaryResolver

    return await PortfolioSummaryResolver.get_portfolio_summary(account_id)

  @strawberry.field(description="获取每日收盘资产快照")
  async def daily_asset_snapshots(
    self,
    account_id: Optional[str] = None,
    strategy_run_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 366,
  ) -> List[DailyAssetSnapshot]:
    return await DailyAssetSnapshotResolver.get_daily_asset_snapshots(
      account_id=account_id,
      strategy_run_id=strategy_run_id,
      scope_type=scope_type,
      start_date=start_date,
      end_date=end_date,
      limit=limit,
    )

  @strawberry.field(description="分页获取已清仓持仓周期")
  async def closed_position_cycles(
    self,
    account_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
  ) -> ClosedPositionCyclePage:
    return await ClosedPositionCycleResolver.get_page(
      account_id, start_date, end_date, limit, offset
    )
