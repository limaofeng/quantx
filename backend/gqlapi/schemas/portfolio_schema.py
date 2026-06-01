from typing import List, Optional

import strawberry

from ..resolvers.account import AccountResolver
from ..resolvers.positions import PositionResolver
from ..resolvers.daily_asset_snapshots import DailyAssetSnapshotResolver
from ..types.portfolio_types import (
  Account,
  DailyAssetSnapshot,
  PortfolioSummary,
  Position,
)


@strawberry.type(description="持仓账户相关查询")
class PortfolioQuery:
  @strawberry.field(description="获取持仓列表")
  async def positions(self) -> List[Position]:
    return await PositionResolver.get_positions()

  @strawberry.field(description="获取单个持仓信息")
  async def position(self, stock_code: str) -> Optional[Position]:
    return await PositionResolver.get_position(stock_code)

  @strawberry.field(description="获取账户信息")
  def account(self, account_id: str) -> Optional[Account]:
    return AccountResolver.get_account(account_id)

  @strawberry.field(description="获取当前账户信息")
  def current_account(self) -> Account:
    return AccountResolver.get_current_account()

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
