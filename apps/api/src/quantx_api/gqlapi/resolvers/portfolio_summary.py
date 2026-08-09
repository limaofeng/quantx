from typing import List, Optional

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.services.daily_asset_snapshot_service import (
  DailyAssetSnapshotService,
)

from ..types.portfolio_types import Account, PortfolioSummary, Position
from .account import AccountResolver
from .positions import PositionResolver


class PortfolioSummaryResolver:
  @staticmethod
  async def get_portfolio_summary(account_id: Optional[str] = None) -> PortfolioSummary:
    """获取持仓表现汇总信息"""
    try:
      if account_id is None:
        current_account = await AccountResolver.get_current_account_async()
        if current_account is None:
          raise ValueError("当前未连接资金账户")
        account_id = current_account.id

      account_info = await AccountResolver.get_account_async(account_id)
      if account_info is None:
        raise Exception(f"无法获取账户 {account_id} 的信息")

      positions = await PositionResolver.get_positions(account_id)

      # 计算汇总数据
      summary_data = PortfolioSummaryResolver._calculate_summary(
        account_id, account_info, positions
      )
      latest_snapshot = await DailyAssetSnapshotService().get_latest_account_snapshot(
        account_id
      )
      if latest_snapshot is not None:
        summary_data["today_profit_loss"] = (
          round(float(latest_snapshot.daily_pnl_cny), 2)
          if latest_snapshot.daily_pnl_cny is not None
          else None
        )
        summary_data["today_profit_loss_percent"] = (
          round(float(latest_snapshot.daily_return_pct), 2)
          if latest_snapshot.daily_return_pct is not None
          else None
        )

      return PortfolioSummary(**summary_data)

    except Exception:
      # 返回空数据而不是抛出异常，便于前端处理
      return PortfolioSummary(
        account_id=account_id or "unknown",
        account_name=f"账户{account_id or 'unknown'}",
        total_asset=0.0,
        total_market_value=0.0,
        cash=0.0,
        cash_ratio=100.0,
        total_profit_loss=0.0,
        total_profit_loss_percent=0.0,
        today_profit_loss=None,
        today_profit_loss_percent=None,
        position_count=0,
        profit_position_count=0,
        loss_position_count=0,
        top_holdings=[],
        update_time=time_utils.now(),
      )

  @staticmethod
  def _calculate_summary(
    account_id: str, account_info: Account, positions: List[Position]
  ) -> dict:
    """计算持仓汇总数据"""

    # 基础资产信息
    total_asset = account_info.total_asset or 0
    cash = account_info.cash or 0
    position_market_value = sum(
      float(position.market_value or 0) for position in positions
    )
    total_market_value = account_info.market_value or position_market_value

    # 计算现金占比
    cash_ratio = (cash / total_asset * 100) if total_asset > 0 else 100.0

    # 总盈亏
    total_profit_loss = account_info.total_profit_loss or 0
    total_profit_loss_percent = account_info.profit_loss_percent or 0

    # 持仓统计
    position_count = len(positions)
    profit_position_count = 0
    loss_position_count = 0

    # 计算重要持仓和盈亏统计
    position_objects = []

    for position in positions:
      market_value = float(position.market_value or 0)
      profit_loss = position.profit_loss

      if profit_loss is not None:
        if profit_loss > 0:
          profit_position_count += 1
        elif profit_loss < 0:
          loss_position_count += 1

      if market_value > 0:
        position.market_value_percent = (
          (market_value / total_market_value * 100) if total_market_value > 0 else 0
        )
        position_objects.append({"position": position, "market_value": market_value})

    # 按市值排序，取前10大持仓
    position_objects.sort(key=lambda x: x["market_value"], reverse=True)
    top_holdings = [item["position"] for item in position_objects[:10]]

    return {
      "account_id": account_id,
      "account_name": account_info.account_name,
      "total_asset": round(total_asset, 2),
      "total_market_value": round(total_market_value, 2),
      "cash": round(cash, 2),
      "cash_ratio": round(cash_ratio, 2),
      "total_profit_loss": round(total_profit_loss, 2),
      "total_profit_loss_percent": round(total_profit_loss_percent, 2),
      "today_profit_loss": None,
      "today_profit_loss_percent": None,
      "position_count": position_count,
      "profit_position_count": profit_position_count,
      "loss_position_count": loss_position_count,
      "top_holdings": top_holdings,
      "update_time": account_info.update_time or time_utils.now(),
    }
