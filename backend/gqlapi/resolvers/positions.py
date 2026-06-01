from typing import List, Optional

from core.data import market_data_service

from ..types.portfolio_types import Position


class PositionResolver:
  @staticmethod
  async def get_positions() -> List[Position]:
    """
    获取持仓列表
    """
    stock_positions = await market_data_service.get_positions(with_latest_price=True)

    # 过滤掉零持仓
    stock_positions = [pos for pos in stock_positions if pos.volume > 0]

    return [Position.from_model(pos_data) for pos_data in stock_positions]

  @staticmethod
  async def get_position(stock_code: str) -> Optional[Position]:
    # 使用 DataProvider 获取持仓数据
    stock_position = await market_data_service.get_position(
      stock_code, with_latest_price=False
    )
    return Position.from_model(stock_position) if stock_position else None
