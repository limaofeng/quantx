"""
持仓服务
处理持仓相关的业务逻辑
"""

import logging
from hashlib import md5
from typing import Dict, List, Optional

from database.connection import get_async_db
from database.relational_base import BulkSaveResult
from models.position import Position
from repositories import InstrumentRepository
from repositories.position_repository import PositionRepository

logger = logging.getLogger(__name__)


class PositionService:
  """持仓服务类"""

  def __init__(self):
    pass

  async def get_positions(self) -> List[Position]:
    """获取用户持仓列表"""
    async for db in get_async_db():
      position_repo = PositionRepository(db)
      return await position_repo.find_all()

  async def get_position_by_stock(self, stock_code: str) -> Optional[Position]:
    """获取用户某股票的持仓"""
    async for db in get_async_db():
      instrument_repo = InstrumentRepository(db)
      stock = await instrument_repo.find_by_code(stock_code)
      if not stock:
        return None

      position_repo = PositionRepository(db)
      position_db = await position_repo.find_by_stock_code(stock.id)

      return position_db

  async def save_position(self, position: Position) -> Position:
    """创建或更新持仓"""
    async for db in get_async_db():
      position_repo = PositionRepository(db)

      if position.id is None:
        id = md5(
          f"{position.account_id}:{position.stock_code}".encode("utf-8")
        ).hexdigest()
        position.id = id
      position_repo = await position_repo.save(position)
      return position

  async def save_positions(self, positions: List[Position]) -> BulkSaveResult:
    """批量保存持仓数据"""
    async for db in get_async_db():
      position_repo = PositionRepository(db)

      # 统计清仓的数据
      closed_positions = [pos for pos in positions if pos.volume == 0]

      # 删除清仓的持仓
      if closed_positions:
        closed_ids = [pos.id for pos in closed_positions if pos.id]
        if closed_ids:
          await position_repo.bulk_delete_by_ids(closed_ids)
          logger.info(f"已删除 {len(closed_ids)} 个清仓持仓记录")

      # 只保存活跃持仓（volume > 0）
      active_positions = [pos for pos in positions if pos.volume > 0]
      result = await position_repo.bulk_save(active_positions)
      result.deleted_count = len(closed_positions)

      logger.info(
        f"持仓数据更新完成: 新增/更新 {result.saved_count} 个, "
        f"删除 {result.deleted_count} 个"
      )

      return result

  async def calculate_portfolio_summary(
    self, account_id: str, positions: List[Position]
  ) -> Dict:
    """计算持仓汇总数据"""

    # 基础统计
    position_count = len(positions)
    profit_position_count = 0
    loss_position_count = 0
    total_market_value = 0
    total_cost = 0

    # 带市值占比的持仓列表
    positions_with_percent = []

    for position in positions:
      market_value = float(position.market_value) if position.market_value else 0
      avg_price = float(position.avg_price) if position.avg_price else 0
      volume = position.volume or 0

      total_market_value += market_value

      # 计算成本
      if avg_price > 0 and volume > 0:
        cost = avg_price * volume
        total_cost += cost

    # 计算每个持仓的市值占比
    for position in positions:
      market_value = float(position.market_value) if position.market_value else 0

      # 计算市值占比
      market_value_percent = (
        (market_value / total_market_value * 100) if total_market_value > 0 else 0
      )

      # 计算盈亏（需要实时价格，这里先用模拟数据）
      avg_price = float(position.avg_price) if position.avg_price else 0
      volume = position.volume or 0

      # TODO: 这里需要获取实时价格
      last_price = avg_price * 1.02  # 模拟价格变动

      profit_loss = 0
      if last_price > 0 and avg_price > 0 and volume > 0:
        profit_loss = (last_price - avg_price) * volume

        if profit_loss > 0:
          profit_position_count += 1
        elif profit_loss < 0:
          loss_position_count += 1

      # 添加到结果列表
      if market_value > 0:  # 只包含有市值的持仓
        positions_with_percent.append(
          {
            "position": position,
            "market_value_percent": round(market_value_percent, 2),
            "last_price": last_price,
            "profit_loss": profit_loss,
            "market_value": market_value,
          }
        )

    # 按市值排序
    positions_with_percent.sort(key=lambda x: x["market_value"], reverse=True)

    # 计算总盈亏
    total_profit_loss = total_market_value - total_cost if total_cost > 0 else 0
    total_profit_loss_percent = (
      (total_profit_loss / total_cost * 100) if total_cost > 0 else 0
    )

    return {
      "position_count": position_count,
      "profit_position_count": profit_position_count,
      "loss_position_count": loss_position_count,
      "total_market_value": round(total_market_value, 2),
      "total_profit_loss": round(total_profit_loss, 2),
      "total_profit_loss_percent": round(total_profit_loss_percent, 2),
      "positions_with_percent": positions_with_percent,
    }
