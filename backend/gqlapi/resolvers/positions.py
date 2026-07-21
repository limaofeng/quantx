from typing import List, Optional

from core.data import market_data_service
from services.position_service import PositionService

from ..types.portfolio_types import Position


class PositionResolver:
  @staticmethod
  async def get_positions(account_id: Optional[str] = None) -> List[Position]:
    """
    获取持仓列表
    """
    stock_positions = await PositionService().get_positions(account_id=account_id)
    stock_positions = [pos for pos in stock_positions if pos.volume > 0]
    prices = await market_data_service.get_latest_prices(
      [position.stock_code for position in stock_positions]
    ) if stock_positions else {}
    return [
      Position.from_model(
        position,
        last_price=getattr(prices.get(position.stock_code), "last_price", None),
      )
      for position in stock_positions
    ]

  @staticmethod
  async def get_position(
    stock_code: str, account_id: Optional[str] = None
  ) -> Optional[Position]:
    if account_id:
      stock_position = await PositionService().get_position_by_account_stock(
        account_id, stock_code
      )
    else:
      stock_position = await market_data_service.get_position(
        stock_code, with_latest_price=False
      )
    if stock_position is None:
      return None
    tick = await market_data_service.get_latest_price(stock_code)
    return Position.from_model(
      stock_position, last_price=getattr(tick, "last_price", None)
    )
