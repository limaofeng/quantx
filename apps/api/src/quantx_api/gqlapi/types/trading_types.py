from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

import strawberry
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models import OrderPriceType, OrderStatus, OrderType

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


@strawberry.enum(description="移动端手动委托方向")
class ManualOrderSide(str, Enum):
  BUY = "BUY"
  SELL = "SELL"


@strawberry.enum(description="移动端手动委托报价类型")
class ManualOrderPriceType(str, Enum):
  LIMIT = "LIMIT"
  BEST = "BEST"


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
    from quantx_infrastructure.services.trade_service import TradeService

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
    return str(getattr(self, "instrument_name", "") or self.stock_code)

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
  idempotency_key: Optional[str] = strawberry.field(
    description="调用方生成的业务幂等键",
    default=None,
  )


@strawberry.input(description="移动端手动委托预览输入")
class ManualOrderPreviewInput:
  instrument_code: str = strawberry.field(description="带市场后缀的证券代码")
  side: ManualOrderSide = strawberry.field(description="BUY 或 SELL")
  price_type: ManualOrderPriceType = strawberry.field(description="LIMIT 或 BEST")
  volume: int = strawberry.field(description="请求委托数量")
  idempotency_key: str = strawberry.field(description="调用方生成的业务幂等键")
  account_id: Optional[str] = strawberry.field(description="资金账号", default=None)
  limit_price: Optional[float] = strawberry.field(
    description="LIMIT 必填；BEST 必须为空",
    default=None,
  )


@strawberry.input(description="移动端手动委托确认输入")
class ManualOrderConfirmationInput:
  challenge_id: str = strawberry.field(description="预览返回的确认挑战 ID")
  confirmation_token: str = strawberry.field(description="预览返回的一次性确认凭据")


@strawberry.type(description="移动端手动委托服务器预览")
class ManualOrderPreview:
  challenge_id: str
  confirmation_token: str
  account_id: str
  instrument_code: str
  side: ManualOrderSide
  price_type: ManualOrderPriceType
  volume: int
  limit_price: Optional[float]
  reference_price: float
  estimated_amount: float
  estimated_fees: Optional[float]
  available_cash: float
  available_volume: Optional[int]
  idempotency_key: str
  execution_mode: str
  quote_timestamp: datetime
  challenge_expires_at: datetime
  warnings: List[str]


@strawberry.type(description="移动端手动委托预览结果")
class ManualOrderPreviewResult:
  success: bool
  code: str
  message: str
  preview: Optional[ManualOrderPreview] = None


@strawberry.type(description="移动端手动委托确认结果；成功只表示命令已排队")
class ManualOrderConfirmationResult:
  success: bool
  code: str
  message: str
  challenge_id: Optional[str] = None
  client_order_id: Optional[str] = None
  status: Optional[str] = None


@strawberry.input(description="撤单输入参数")
class CancelOrderInput:
  account_id: Optional[str] = strawberry.field(description="资金账号", default=None)
  order_id: int = strawberry.field(description="订单ID")
  idempotency_key: Optional[str] = strawberry.field(
    description="调用方生成的撤单幂等键",
    default=None,
  )


@strawberry.type(description="下单结果")
class OrderMutationResult:
  success: bool = strawberry.field(description="是否成功")
  message: str = strawberry.field(description="结果消息")
  order_id: Optional[int] = strawberry.field(description="订单ID", default=None)
  client_order_id: Optional[str] = strawberry.field(
    description="服务端幂等订单ID",
    default=None,
  )
  status: Optional[str] = strawberry.field(description="命令状态", default=None)
  order: Optional["Order"] = strawberry.field(description="订单详情", default=None)


@strawberry.type(description="撤单结果")
class CancelOrderResult:
  success: bool = strawberry.field(description="是否成功")
  message: str = strawberry.field(description="结果消息")
  order_id: Optional[int] = strawberry.field(description="订单ID", default=None)
  client_order_id: Optional[str] = strawberry.field(
    description="撤单命令幂等ID",
    default=None,
  )
  status: Optional[str] = strawberry.field(description="命令状态", default=None)


@strawberry.type(description="订单事件 (个人量化软件专用)")
class OrderEvent:
  """订单事件 - 4种核心事件类型"""

  event_type: TradingEventType = strawberry.field(description="事件类型")
  order: Order = strawberry.field(description="订单信息")
  time: datetime = strawberry.field(description="事件时间戳")
  changes: Optional[str] = strawberry.field(description="变更描述")
