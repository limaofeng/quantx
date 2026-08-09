from typing import List, Optional

from quantx_infrastructure.services.position_service import PositionService

from ..types.portfolio_types import Position


class PositionResolver:
  @staticmethod
  async def get_positions(account_id: Optional[str] = None) -> List[Position]:
    """
    获取持仓列表
    """
    stock_positions = await PositionService().get_positions(account_id=account_id)
    stock_positions = [pos for pos in stock_positions if pos.volume > 0]
    return [
      Position.from_model(position)
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
      stock_position = await PositionService().get_position_by_stock(stock_code)
    if stock_position is None:
      return None
    return Position.from_model(stock_position)
