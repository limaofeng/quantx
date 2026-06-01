"""
Broker 基类 - 定义统一的交易接口
"""

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class OrderType(Enum):
  """订单类型"""

  BUY = "BUY"
  SELL = "SELL"
  BUY_TO_COVER = "BUY_TO_COVER"  # 买入平空
  SELL_SHORT = "SELL_SHORT"  # 卖出开空


class OrderStatus(Enum):
  """订单状态"""

  PENDING = "PENDING"  # 待提交
  SUBMITTED = "SUBMITTED"  # 已提交
  PARTIAL_FILLED = "PARTIAL_FILLED"  # 部分成交
  FILLED = "FILLED"  # 全部成交
  CANCELLED = "CANCELLED"  # 已撤销
  REJECTED = "REJECTED"  # 被拒绝
  EXPIRED = "EXPIRED"  # 已过期


class PriceType(Enum):
  """价格类型"""

  LIMIT = "LIMIT"  # 限价单
  MARKET = "MARKET"  # 市价单
  STOP = "STOP"  # 止损单
  STOP_LIMIT = "STOP_LIMIT"  # 限价止损单


@dataclass
class OrderRequest:
  """订单请求"""

  instrument_code: str
  order_type: OrderType
  price_type: PriceType
  volume: int
  price: float = 0.0
  stop_price: float = 0.0
  strategy_id: Optional[str] = None
  metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderResponse:
  """订单响应"""

  order_id: str
  request: OrderRequest
  status: OrderStatus
  submit_time: datetime
  filled_volume: int = 0
  filled_amount: float = 0.0
  avg_price: float = 0.0
  commission: float = 0.0
  slippage: float = 0.0
  last_update_time: Optional[datetime] = None
  error_message: Optional[str] = None


@dataclass
class Position:
  """持仓信息"""

  instrument_code: str
  long_volume: int = 0  # 多头持仓
  short_volume: int = 0  # 空头持仓
  available_volume: int = 0  # A股T+1可卖数量
  frozen_volume: int = 0  # 已冻结待卖数量
  today_buy_volume: int = 0  # 当日买入数量
  long_avg_price: float = 0.0  # 多头均价
  short_avg_price: float = 0.0  # 空头均价
  market_value: float = 0.0  # 市值
  pnl: float = 0.0  # 盈亏
  pnl_percent: float = 0.0  # 盈亏比例
  last_price: float = 0.0  # 最新价格


@dataclass
class AccountInfo:
  """账户信息"""

  account_id: str
  total_asset: float  # 总资产
  cash: float  # 可用资金
  frozen_cash: float  # 冻结资金
  market_value: float  # 持仓市值
  total_pnl: float  # 总盈亏
  daily_pnl: float  # 当日盈亏
  positions: Dict[str, Position] = field(default_factory=dict)
  last_update_time: datetime = field(default_factory=datetime.now)


@dataclass
class TradeRecord:
  """成交记录"""

  trade_id: str
  order_id: str
  instrument_code: str
  trade_type: OrderType
  price: float
  volume: int
  amount: float
  commission: float
  trade_time: datetime
  metadata: Dict[str, Any] = field(default_factory=dict)


class BrokerBase(ABC):
  """Broker 抽象基类"""

  def __init__(self, account_id: str, initial_capital: float = 1000000.0):
    self.account_id = account_id
    self.initial_capital = initial_capital
    self.logger = logging.getLogger(f"Broker-{self.__class__.__name__}")

    # 回调函数
    self.on_order_callbacks: List[Callable[[OrderResponse], None]] = []
    self.on_trade_callbacks: List[Callable[[TradeRecord], None]] = []

    # 订单和成交记录
    self.orders: Dict[str, OrderResponse] = {}
    self.trades: List[TradeRecord] = []

  @abstractmethod
  async def connect(self) -> bool:
    """连接到交易系统"""
    pass

  @abstractmethod
  async def disconnect(self) -> None:
    """断开连接"""
    pass

  @abstractmethod
  async def place_order(self, request: OrderRequest) -> OrderResponse:
    """
    下单
    Args:
        request: 订单请求
    Returns:
        订单响应
    """
    pass

  @abstractmethod
  async def cancel_order(self, order_id: str) -> bool:
    """
    撤单
    Args:
        order_id: 订单ID
    Returns:
        是否成功
    """
    pass

  @abstractmethod
  async def get_order(self, order_id: str) -> Optional[OrderResponse]:
    """
    查询订单
    Args:
        order_id: 订单ID
    Returns:
        订单信息
    """
    pass

  @abstractmethod
  async def get_position(self, instrument_code: str = None) -> Dict[str, Position]:
    """
    查询持仓
    Args:
        instrument_code: 标的代码，None表示查询所有
    Returns:
        持仓信息字典
    """
    pass

  @abstractmethod
  async def get_account(self) -> AccountInfo:
    """
    查询账户信息
    Returns:
        账户信息
    """
    pass

  @abstractmethod
  async def get_trades(
    self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
  ) -> List[TradeRecord]:
    """
    查询成交记录
    Args:
        start_time: 开始时间
        end_time: 结束时间
    Returns:
        成交记录列表
    """
    pass

  def subscribe_order_updates(self, callback: Callable[[OrderResponse], None]) -> None:
    """订阅订单状态更新"""
    self.on_order_callbacks.append(callback)

  def subscribe_trade_updates(self, callback: Callable[[TradeRecord], None]) -> None:
    """订阅成交回报"""
    self.on_trade_callbacks.append(callback)

  async def emit_order_update(self, order: OrderResponse) -> None:
    """发送订单更新"""
    for callback in self.on_order_callbacks:
      try:
        callback(order)
      except Exception as e:
        self.logger.error(f"订单回调失败: {e}")

  async def emit_trade_update(self, trade: TradeRecord) -> None:
    """发送成交更新"""
    for callback in self.on_trade_callbacks:
      try:
        callback(trade)
      except Exception as e:
        self.logger.error(f"成交回调失败: {e}")

  def generate_order_id(self) -> str:
    """生成订单ID"""
    return str(uuid.uuid4())

  def generate_trade_id(self) -> str:
    """生成成交ID"""
    return str(uuid.uuid4())

  def calculate_commission(self, amount: float, rate: float = 0.0003) -> float:
    """
    计算手续费
    Args:
        amount: 成交金额
        rate: 费率（默认万三）
    Returns:
        手续费
    """
    commission = amount * rate
    # 最低5元
    return max(commission, 5.0)

  def calculate_slippage(
    self, expected_price: float, actual_price: float, order_type: OrderType
  ) -> float:
    """
    计算滑点
    Args:
        expected_price: 期望价格
        actual_price: 实际成交价格
        order_type: 订单类型
    Returns:
        滑点（负数表示不利滑点）
    """
    if order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      # 买入时，实际价格高于期望价格为不利滑点
      return expected_price - actual_price
    else:
      # 卖出时，实际价格低于期望价格为不利滑点
      return actual_price - expected_price

  def get_statistics(self) -> Dict[str, Any]:
    """获取统计信息"""
    total_orders = len(self.orders)
    filled_orders = sum(
      1 for o in self.orders.values() if o.status == OrderStatus.FILLED
    )
    partial_filled = sum(
      1 for o in self.orders.values() if o.status == OrderStatus.PARTIAL_FILLED
    )
    cancelled_orders = sum(
      1 for o in self.orders.values() if o.status == OrderStatus.CANCELLED
    )
    rejected_orders = sum(
      1 for o in self.orders.values() if o.status == OrderStatus.REJECTED
    )

    total_commission = sum(o.commission for o in self.orders.values())
    total_slippage = sum(o.slippage for o in self.orders.values())

    return {
      "total_orders": total_orders,
      "filled_orders": filled_orders,
      "partial_filled_orders": partial_filled,
      "cancelled_orders": cancelled_orders,
      "rejected_orders": rejected_orders,
      "fill_rate": filled_orders / total_orders if total_orders > 0 else 0,
      "total_trades": len(self.trades),
      "total_commission": total_commission,
      "total_slippage": total_slippage,
    }
