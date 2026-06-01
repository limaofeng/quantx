"""
Order & Trade Resolver
处理委托和成交相关的 GraphQL 查询
"""

from typing import List, Optional

from models import Order, Trade
from services.order_service import OrderService
from services.trade_service import TradeService

DEFAULT_ACCOUNT_ID = "300000013250"


class OrderResolver:
  """委托与成交 Resolver"""

  @staticmethod
  async def get_today_orders(account_id: Optional[str] = None) -> List[Order]:
    """获取当日委托"""
    account = account_id or DEFAULT_ACCOUNT_ID
    service = OrderService(account)
    return await service.get_today_orders(account)

  @staticmethod
  async def get_history_orders(
    account_id: Optional[str], start_date: str, end_date: str
  ) -> List[Order]:
    """获取历史委托"""
    account = account_id or DEFAULT_ACCOUNT_ID
    service = OrderService(account)
    return await service.get_history_orders(account, start_date, end_date)

  @staticmethod
  async def get_order(
    order_id: int, account_id: str = "300000013250"
  ) -> Optional[Order]:
    """获取单个委托(智能查询)"""
    service = OrderService(account_id)
    return await service.get_order_by_id(order_id)

  @staticmethod
  async def get_today_trades(account_id: str = "300000013250") -> List[Trade]:
    """获取当日成交"""
    service = TradeService(account_id)
    return await service.get_today_trades(account_id)

  @staticmethod
  async def get_history_trades(
    account_id: str, start_date: str, end_date: str
  ) -> List[Trade]:
    """获取历史成交"""
    service = TradeService(account_id)
    return await service.get_history_trades(account_id, start_date, end_date)

  @staticmethod
  async def get_trade(
    trade_id: str, account_id: str = "300000013250"
  ) -> Optional[Trade]:
    """获取单个成交"""
    service = TradeService(account_id)
    return await service.get_trade_by_id(trade_id)
