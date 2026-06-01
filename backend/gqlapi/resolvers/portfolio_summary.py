from typing import Optional

from miniqmt.manager_registry import XTTradingManagerRegistry
from models.position import Position as PositionModel
from services.daily_asset_snapshot_service import DailyAssetSnapshotService

from ..types.portfolio_types import PortfolioSummary, Position
from core.utils import time_utils

registry = XTTradingManagerRegistry()


class PortfolioSummaryResolver:
  @staticmethod
  async def get_portfolio_summary(account_id: Optional[str] = None) -> PortfolioSummary:
    """获取持仓表现汇总信息"""
    try:
      # 使用默认账户如果未指定
      if account_id is None:
        account_id = "300000013250"  # 默认账户

      # 获取 trading manager
      trading_manager = registry.get_manager(account_id)

      if not trading_manager:
        raise Exception(f"无法获取账户 {account_id} 的交易管理器")

      # 获取账户信息
      account_info = trading_manager.get_account_info()
      if not account_info:
        raise Exception(f"无法获取账户 {account_id} 的信息")

      # 获取持仓信息
      positions = trading_manager.get_positions()
      if not positions:
        positions = []

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
  def _calculate_summary(account_id: str, account_info: dict, positions: list) -> dict:
    """计算持仓汇总数据"""

    # 基础资产信息
    total_asset = account_info.get("total_asset", 0)
    cash = account_info.get("cash", 0)
    total_market_value = account_info.get("market_value", 0)

    # 计算现金占比
    cash_ratio = (cash / total_asset * 100) if total_asset > 0 else 100.0

    # 总盈亏
    total_profit_loss = account_info.get("profit_loss", 0)
    total_profit_loss_percent = account_info.get("profit_loss_ratio", 0) * 100

    # 持仓统计
    position_count = len(positions)
    profit_position_count = 0
    loss_position_count = 0

    # 计算重要持仓和盈亏统计
    position_objects = []

    for position in positions:
      # 获取实时价格（这里需要实现获取实时价格的逻辑）
      last_price = position.get("last_price", 0)
      avg_price = position.get("avg_price", 0)
      volume = position.get("volume", 0)
      market_value = position.get("market_value", 0)

      # 计算盈亏
      profit_loss = 0

      if last_price > 0 and avg_price > 0 and volume > 0:
        profit_loss = (last_price - avg_price) * volume

        if profit_loss > 0:
          profit_position_count += 1
        elif profit_loss < 0:
          loss_position_count += 1

      # 添加到重要持仓列表
      if market_value > 0:  # 只包含有市值的持仓
        market_value_percent = (
          (market_value / total_market_value * 100) if total_market_value > 0 else 0
        )

        # 创建 PositionModel 对象用于转换
        position_model = PositionModel.from_dict(position)

        # 转换为 GraphQL Position 对象，包含市值占比
        position_obj = Position.from_model(
          position_model,
          last_price=last_price,
          market_value_percent=market_value_percent,
        )

        position_objects.append(
          {"position": position_obj, "market_value": market_value}
        )

    # 按市值排序，取前10大持仓
    position_objects.sort(key=lambda x: x["market_value"], reverse=True)
    top_holdings = [item["position"] for item in position_objects[:10]]

    return {
      "account_id": account_id,
      "account_name": f"账户{account_id}",
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
      "update_time": time_utils.now(),
    }
