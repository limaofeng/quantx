from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

import strawberry

from models import OrderPriceType, OrderStatus, OrderType
from core.utils import time_utils

if TYPE_CHECKING:
  from .trading_types import Trade


@strawberry.enum(description="交易事件类型 (个人量化软件专用)")
class TradingEventType(str, Enum):
  """交易事件类型枚举 - 4种核心事件"""

  # 订单事件
  ORDER_CREATED = "ORDER_CREATED"  # 订单创建
  ORDER_FILLED = "ORDER_FILLED"  # 订单成交
  ORDER_CANCELLED = "ORDER_CANCELLED"  # 订单撤销
  ORDER_REJECTED = "ORDER_REJECTED"  # 订单被拒绝


@strawberry.type(description="订单信息")
class Order:
  id: str = strawberry.field(description="订单编号")
  sysid: str = strawberry.field(description="柜台合同编号")
  stock_code: str = strawberry.field(description="股票代码")
  @strawberry.field(description="股票名称")
  def stock_name(self) -> str:
    return getattr(self, "instrument_name", "") or getattr(self, "stock_name", "Unknown")
  type: OrderType = strawberry.field(description="委托类型")
  volume: int = strawberry.field(description="委托数量")
  price_type: OrderPriceType = strawberry.field(description="报价类型")
  price: float = strawberry.field(description="委托价格")
  traded_volume: int = strawberry.field(description="成交数量")
  traded_price: float = strawberry.field(description="成交均价")
  status: OrderStatus = strawberry.field(description="订单状态")
  status_msg: Optional[str] = strawberry.field(description="状态信息")
  strategy_name: Optional[str] = strawberry.field(description="策略名称")
  @strawberry.field(description="订单备注")
  def order_remark(self) -> Optional[str]:
    return getattr(self, "remark", "") or getattr(self, "order_remark", "")
  time: datetime = strawberry.field(description="报单时间")

  @strawberry.field(description="成交明细列表")
  async def trades(
    self, account_id: Optional[str] = None
  ) -> List["Trade"]:
    """懒加载成交明细 - 只在前端请求时查询"""
    from services.trade_service import TradeService

    resolved_account_id = account_id or getattr(self, "account_id", None)
    if not resolved_account_id:
      return []
    service = TradeService(resolved_account_id)
    return await service.get_trades_by_order_id(int(self.id))


@strawberry.type(description="成交记录信息")
class Trade:
  @strawberry.field(description="账号类型")
  def account_type(self) -> int:
    value = getattr(self, "account_type", None)
    if value is None:
      return -1
    return value.to_int() if hasattr(value, "to_int") else int(value)

  account_id: str = strawberry.field(description="资金账号")
  stock_code: str = strawberry.field(description="证券代码")

  @strawberry.field(description="证券名称")
  def stock_name(self) -> str:
    from miniqmt.utils.helpers import get_stock_name

    return get_stock_name(self.stock_code) or self.stock_code

  order_type: int = strawberry.field(description="委托类型")

  @strawberry.field(description="成交编号")
  def traded_id(self) -> str:
    return str(getattr(self, "id", ""))

  @strawberry.field(description="成交时间(时间戳)")
  def traded_time(self) -> int:
    value = getattr(self, "time", None)
    if isinstance(value, datetime):
      return int(value.timestamp())
    if isinstance(value, (int, float)):
      return int(value)
    return int(time_utils.now().timestamp())

  @strawberry.field(description="成交均价")
  def traded_price(self) -> float:
    return float(getattr(self, "price", 0.0) or 0.0)

  @strawberry.field(description="成交数量")
  def traded_volume(self) -> int:
    return int(getattr(self, "volume", 0) or 0)

  @strawberry.field(description="成交金额")
  def traded_amount(self) -> float:
    return float(getattr(self, "amount", 0.0) or 0.0)

  order_id: int = strawberry.field(description="订单编号")
  order_sysid: str = strawberry.field(description="柜台合同编号")
  strategy_name: Optional[str] = strawberry.field(description="策略名称")
  order_remark: Optional[str] = strawberry.field(description="委托备注")
  direction: Optional[int] = strawberry.field(description="多空方向")
  offset_flag: Optional[int] = strawberry.field(description="交易操作")


@strawberry.input(description="订单输入参数")
class OrderInput:
  account_id: Optional[str] = strawberry.field(description="资金账号", default=None)
  stock_code: str = strawberry.field(description="股票代码")
  type: str = strawberry.field(description="委托类型: BUY/SELL")
  price_type: str = strawberry.field(description="报价类型: LIMIT/MARKET/BEST")
  volume: int = strawberry.field(description="委托数量")
  price: float = strawberry.field(description="委托价格")
  strategy_name: Optional[str] = strawberry.field(description="策略名称")
  order_remark: Optional[str] = strawberry.field(description="订单备注")


@strawberry.input(description="撤单输入参数")
class CancelOrderInput:
  account_id: Optional[str] = strawberry.field(description="资金账号", default=None)
  order_id: int = strawberry.field(description="订单ID")
  user_id: str = strawberry.field(description="用户ID", default="default")


@strawberry.type(description="下单结果")
class OrderMutationResult:
  success: bool = strawberry.field(description="是否成功")
  message: str = strawberry.field(description="结果消息")
  order_id: Optional[int] = strawberry.field(description="订单ID", default=None)
  order: Optional["Order"] = strawberry.field(description="订单详情", default=None)


@strawberry.type(description="撤单结果")
class CancelOrderResult:
  success: bool = strawberry.field(description="是否成功")
  message: str = strawberry.field(description="结果消息")
  order_id: Optional[int] = strawberry.field(description="订单ID", default=None)


@strawberry.type(description="订单事件 (个人量化软件专用)")
class OrderEvent:
  """订单事件 - 4种核心事件类型"""

  event_type: TradingEventType = strawberry.field(description="事件类型")
  order: Order = strawberry.field(description="订单信息")
  time: datetime = strawberry.field(description="事件时间戳")
  changes: Optional[str] = strawberry.field(description="变更描述")
