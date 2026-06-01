"""
订单服务
处理订单相关的业务逻辑
"""

from datetime import datetime
from typing import List, Optional

from xtquant.xttype import XtOrder

from database.connection import get_async_db
from database.relational_base import BulkSaveResult
from miniqmt.manager_registry import XTTradingManagerRegistry
from miniqmt.utils.helpers import get_stock_name
from models import Order
from models.enums import AccountType, OrderPriceType, OrderStatus, OrderType
from repositories.order_repository import OrderRepository

trading_registry = XTTradingManagerRegistry()


class OrderService:
  """订单服务类"""

  def __init__(self, account_id: str = "300000013250"):
    self.trading_manager = trading_registry.get_manager(account_id)

  async def get_today_orders(self, account_id: str) -> List[Order]:
    """从 trading_manager 获取当日委托"""
    if not self.trading_manager:
      return []

    xt_orders = self.trading_manager.get_orders()
    return [self._convert_xt_order(xt_order) for xt_order in xt_orders]

  async def get_history_orders(
    self, account_id: str, start_date: str, end_date: str
  ) -> List[Order]:
    """从数据库获取历史委托"""
    async for db in get_async_db():
      order_repo = OrderRepository(db)
      return await order_repo.find_all_by_date_range(account_id, start_date, end_date)

  async def get_order_by_id(self, order_id: int) -> Optional[Order]:
    """智能查询: 先查当日(trading_manager)，再查历史(database)"""
    if self.trading_manager:
      try:
        xt_order = self.trading_manager.get_order(order_id)
        if xt_order:
          return self._convert_xt_order(xt_order)
      except Exception:
        pass

    async for db in get_async_db():
      order_repo = OrderRepository(db)
      return await order_repo.find_by_id(order_id)

  async def get_orders(self, user_id: str = "default", limit: int = 100) -> List[Order]:
    """获取用户订单列表"""
    async for db in get_async_db():
      order_repo = OrderRepository(db)
      orders_from_db = await order_repo.find_all_by_user(user_id, 0, limit)

      return orders_from_db

  async def save_orders(self, orders: List[Order]) -> BulkSaveResult:
    """批量保存订单"""
    async for db in get_async_db():
      order_repo = OrderRepository(db)
      return await order_repo.bulk_save(orders)

  def _convert_xt_order(self, xt_order: XtOrder) -> Order:
    """XtOrder → Order Model"""
    stock_name = get_stock_name(xt_order.stock_code)

    return Order(
      id=xt_order.order_id,
      account_id=xt_order.account_id,
      account_type=AccountType.from_int(xt_order.account_type),
      stock_code=xt_order.stock_code,
      sysid=xt_order.order_sysid,
      time=datetime.fromtimestamp(xt_order.order_time),
      type=OrderType(xt_order.order_type),
      volume=xt_order.order_volume,
      price_type=OrderPriceType(xt_order.price_type),
      price=xt_order.price,
      traded_volume=xt_order.traded_volume,
      traded_price=xt_order.traded_price,
      status=OrderStatus(xt_order.order_status),
      status_msg=xt_order.status_msg,
      strategy_name=xt_order.strategy_name,
      remark=xt_order.order_remark,
      instrument_name=stock_name,
    )
