"""
实盘 Broker - 对接 XTQuant 真实交易
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.trading_service import TradingService

from core.utils import time_utils
from .base import (
  AccountInfo,
  BrokerBase,
  OrderRequest,
  OrderResponse,
  OrderStatus,
  OrderType,
  Position,
  PriceType,
  TradeRecord,
)


class LiveBroker(BrokerBase):
  """实盘交易 Broker - 对接真实交易系统"""

  def __init__(
    self,
    account_id: str,
    initial_capital: float = 1000000.0,
    enable_risk_control: bool = True,
    max_order_amount: float = 100000.0,  # 单笔最大金额
    max_position_value: float = 500000.0,  # 单个标的最大持仓
  ):
    super().__init__(account_id, initial_capital)

    # 风控参数
    self.enable_risk_control = enable_risk_control
    self.max_order_amount = max_order_amount
    self.max_position_value = max_position_value

    # 交易服务
    self.trading_service: Optional[TradingService] = None

    # 订单映射（内部ID -> 外部ID）
    self.order_id_mapping: Dict[str, str] = {}
    self.external_to_internal: Dict[str, str] = {}

    # 连接状态
    self.is_connected = False

    self.logger = logging.getLogger("LiveBroker")

  async def connect(self) -> bool:
    """连接到真实交易系统"""
    try:
      self.trading_service = TradingService()

      # 验证账户
      account = await self.trading_service.get_account_info(realtime=True)
      if not account:
        self.logger.error("无法获取账户信息")
        return False

      self.is_connected = True
      self.logger.info(
        f"实盘 Broker 连接成功，账户: {account.account_id}, "
        f"可用资金: {account.cash:.2f}"
      )

      # 启动订单状态监控
      asyncio.create_task(self._monitor_orders())

      return True

    except Exception as e:
      self.logger.error(f"连接失败: {e}")
      return False

  async def disconnect(self) -> None:
    """断开连接"""
    self.is_connected = False
    self.logger.info("实盘 Broker 断开连接")

  async def place_order(self, request: OrderRequest) -> OrderResponse:
    """下单"""
    if not self.is_connected:
      return self._create_rejected_order(request, "未连接到交易系统")

    # 风控检查
    if self.enable_risk_control:
      risk_check = await self._risk_check(request)
      if not risk_check["passed"]:
        return self._create_rejected_order(request, risk_check["reason"])

    # 创建内部订单
    internal_order_id = self.generate_order_id()
    order = OrderResponse(
      order_id=internal_order_id,
      request=request,
      status=OrderStatus.PENDING,
      submit_time=time_utils.now(),
    )

    try:
      # 转换订单类型
      xt_order_type = self._convert_order_type(request.order_type)
      xt_price_type = self._convert_price_type(request.price_type)

      # 调用交易服务下单
      external_order_id = await self.trading_service.place_order(
        stock_code=request.instrument_code,
        order_type=xt_order_type,
        order_volume=request.volume,
        price_type=xt_price_type,
        price=request.price,
        strategy_name=request.metadata.get("strategy_name", ""),
        order_remark=request.metadata.get("remark", ""),
      )

      # 保存订单映射
      self.order_id_mapping[internal_order_id] = external_order_id
      self.external_to_internal[external_order_id] = internal_order_id

      # 更新订单状态
      order.status = OrderStatus.SUBMITTED
      order.last_update_time = time_utils.now()

      self.orders[internal_order_id] = order
      await self.emit_order_update(order)

      self.logger.info(
        f"下单成功: {request.instrument_code} {request.order_type.value} "
        f"{request.volume}股 @ {request.price:.2f}, "
        f"订单ID: {internal_order_id} -> {external_order_id}"
      )

      return order

    except Exception as e:
      self.logger.error(f"下单失败: {e}")
      order.status = OrderStatus.REJECTED
      order.error_message = str(e)
      self.orders[internal_order_id] = order
      return order

  async def cancel_order(self, order_id: str) -> bool:
    """撤单"""
    if not self.is_connected:
      return False

    # 获取外部订单ID
    external_order_id = self.order_id_mapping.get(order_id)
    if not external_order_id:
      self.logger.error(f"找不到订单映射: {order_id}")
      return False

    try:
      # 调用交易服务撤单
      result = await self.trading_service.cancel_order(external_order_id)

      if result:
        # 更新内部订单状态
        if order_id in self.orders:
          order = self.orders[order_id]
          order.status = OrderStatus.CANCELLED
          order.last_update_time = time_utils.now()
          await self.emit_order_update(order)

        self.logger.info(f"撤单成功: {order_id} -> {external_order_id}")
        return True
      else:
        self.logger.warning(f"撤单失败: {order_id} -> {external_order_id}")
        return False

    except Exception as e:
      self.logger.error(f"撤单异常: {e}")
      return False

  async def get_order(self, order_id: str) -> Optional[OrderResponse]:
    """查询订单"""
    # 先查本地缓存
    if order_id in self.orders:
      return self.orders[order_id]

    # 查询外部系统
    if not self.is_connected:
      return None

    external_order_id = self.order_id_mapping.get(order_id)
    if external_order_id:
      try:
        external_order = await self.trading_service.order_service.get_order_by_id(
          external_order_id
        )
        if external_order:
          return self._convert_external_order(external_order, order_id)
      except Exception as e:
        self.logger.error(f"查询订单失败: {e}")

    return None

  async def get_position(self, instrument_code: str = None) -> Dict[str, Position]:
    """查询持仓"""
    if not self.is_connected:
      return {}

    try:
      # 获取所有持仓
      positions = await self.trading_service.position_service.get_positions()

      result = {}
      for pos in positions:
        if instrument_code and pos.instrument_code != instrument_code:
          continue

        position = Position(
          instrument_code=pos.instrument_code,
          long_volume=pos.volume,
          available_volume=getattr(pos, "available_volume", pos.volume),
          frozen_volume=max(0, pos.volume - getattr(pos, "available_volume", pos.volume)),
          long_avg_price=pos.avg_price,
          market_value=pos.market_value,
          pnl=pos.profit_loss,
          pnl_percent=pos.profit_loss_ratio * 100 if pos.profit_loss_ratio else 0,
          last_price=pos.current_price,
        )
        result[pos.instrument_code] = position

      return result

    except Exception as e:
      self.logger.error(f"查询持仓失败: {e}")
      return {}

  async def get_account(self) -> AccountInfo:
    """查询账户信息"""
    if not self.is_connected:
      return AccountInfo(
        account_id=self.account_id,
        total_asset=0,
        cash=0,
        frozen_cash=0,
        market_value=0,
        total_pnl=0,
        daily_pnl=0,
      )

    try:
      account = await self.trading_service.get_account_info(realtime=True)

      # 获取持仓
      positions = await self.get_position()

      return AccountInfo(
        account_id=account.account_id,
        total_asset=account.total_asset,
        cash=account.cash,
        frozen_cash=account.frozen_cash,
        market_value=account.market_value,
        total_pnl=account.total_asset - self.initial_capital,
        daily_pnl=0,  # 需要额外计算
        positions=positions,
        last_update_time=time_utils.now(),
      )

    except Exception as e:
      self.logger.error(f"查询账户失败: {e}")
      return AccountInfo(
        account_id=self.account_id,
        total_asset=0,
        cash=0,
        frozen_cash=0,
        market_value=0,
        total_pnl=0,
        daily_pnl=0,
      )

  async def get_trades(
    self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
  ) -> List[TradeRecord]:
    """查询成交记录"""
    if not self.is_connected:
      return []

    # 暂时返回本地缓存的成交记录
    trades = self.trades

    if start_time:
      trades = [t for t in trades if t.trade_time >= start_time]
    if end_time:
      trades = [t for t in trades if t.trade_time <= end_time]

    return trades

  async def _risk_check(self, request: OrderRequest) -> Dict[str, Any]:
    """风控检查"""
    # 计算订单金额
    order_amount = request.price * request.volume

    # 检查单笔金额限制
    if order_amount > self.max_order_amount:
      return {
        "passed": False,
        "reason": f"单笔金额 {order_amount:.2f} 超过限制 {self.max_order_amount:.2f}",
      }

    # 检查持仓限制
    if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      positions = await self.get_position(request.instrument_code)
      current_position = positions.get(request.instrument_code)

      if current_position:
        new_value = current_position.market_value + order_amount
        if new_value > self.max_position_value:
          return {
            "passed": False,
            "reason": f"持仓市值 {new_value:.2f} 超过限制 {self.max_position_value:.2f}",
          }

    # 检查账户资金
    account = await self.get_account()
    if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      required_cash = order_amount * 1.003  # 包含手续费
      if required_cash > account.cash:
        return {
          "passed": False,
          "reason": f"可用资金不足: 需要 {required_cash:.2f}, 可用 {account.cash:.2f}",
        }

    if request.order_type == OrderType.SELL:
      positions = await self.get_position(request.instrument_code)
      current_position = positions.get(request.instrument_code)
      available_volume = current_position.available_volume if current_position else 0
      if available_volume < request.volume:
        return {
          "passed": False,
          "reason": f"可用持仓不足: {available_volume} < {request.volume}",
        }

    return {"passed": True, "reason": ""}

  async def _monitor_orders(self) -> None:
    """监控订单状态"""
    while self.is_connected:
      try:
        # 查询所有活动订单
        for internal_id, order in self.orders.items():
          if order.status in [OrderStatus.SUBMITTED, OrderStatus.PARTIAL_FILLED]:
            external_id = self.order_id_mapping.get(internal_id)
            if external_id:
              # 查询外部订单状态
              await self._update_order_status(internal_id, external_id)

        await asyncio.sleep(1)  # 每秒检查一次

      except Exception as e:
        self.logger.error(f"订单监控异常: {e}")
        await asyncio.sleep(5)

  async def _update_order_status(self, internal_id: str, external_id: str) -> None:
    """更新订单状态"""
    try:
      # 查询外部订单
      external_order = await self.trading_service.order_service.get_order_by_id(
        external_id
      )

      if external_order:
        internal_order = self.orders[internal_id]
        old_status = internal_order.status

        # 转换状态
        new_status = self._convert_order_status(external_order.status)

        if new_status != old_status:
          internal_order.status = new_status
          internal_order.filled_volume = external_order.filled_volume
          internal_order.avg_price = external_order.avg_price
          internal_order.last_update_time = time_utils.now()

          # 如果成交，生成成交记录
          if new_status == OrderStatus.FILLED:
            await self._create_trade_from_order(internal_order, external_order)

          await self.emit_order_update(internal_order)

    except Exception as e:
      self.logger.error(f"更新订单状态失败: {e}")

  async def _create_trade_from_order(
    self, internal_order: OrderResponse, external_order: Any
  ) -> None:
    """从订单创建成交记录"""
    trade = TradeRecord(
      trade_id=self.generate_trade_id(),
      order_id=internal_order.order_id,
      instrument_code=internal_order.request.instrument_code,
      trade_type=internal_order.request.order_type,
      price=external_order.avg_price,
      volume=external_order.filled_volume,
      amount=external_order.avg_price * external_order.filled_volume,
      commission=external_order.commission or 0,
      trade_time=external_order.filled_time or time_utils.now(),
      metadata=dict(internal_order.request.metadata or {}),
    )

    self.trades.append(trade)
    await self.emit_trade_update(trade)

  def _convert_order_type(self, order_type: OrderType) -> Any:
    """转换订单类型到 XTQuant"""
    from miniqmt.trading.trading_manager import OrderType as XTOrderType

    mapping = {
      OrderType.BUY: XTOrderType.BUY,
      OrderType.SELL: XTOrderType.SELL,
    }
    return mapping.get(order_type, XTOrderType.BUY)

  def _convert_price_type(self, price_type: PriceType) -> Any:
    """转换价格类型到 XTQuant"""
    from models.enums import PriceType as XTPriceType

    mapping = {
      PriceType.LIMIT: XTPriceType.LIMIT,
      PriceType.MARKET: XTPriceType.MARKET_CONVERT_5_LIMIT,
    }
    return mapping.get(price_type, XTPriceType.MARKET_CONVERT_5_LIMIT)

  def _convert_order_status(self, external_status: str) -> OrderStatus:
    """转换外部订单状态"""
    status_mapping = {
      "PENDING": OrderStatus.PENDING,
      "SUBMITTED": OrderStatus.SUBMITTED,
      "PARTIAL_FILLED": OrderStatus.PARTIAL_FILLED,
      "FILLED": OrderStatus.FILLED,
      "CANCELLED": OrderStatus.CANCELLED,
      "REJECTED": OrderStatus.REJECTED,
    }
    return status_mapping.get(external_status, OrderStatus.PENDING)

  def _convert_external_order(
    self, external_order: Any, internal_id: str
  ) -> OrderResponse:
    """转换外部订单到内部格式"""
    # 这里需要根据实际的外部订单格式进行转换
    return OrderResponse(
      order_id=internal_id,
      request=OrderRequest(
        instrument_code=external_order.instrument_code,
        order_type=OrderType.BUY,  # 需要转换
        price_type=PriceType.LIMIT,  # 需要转换
        volume=external_order.volume,
        price=external_order.price,
      ),
      status=self._convert_order_status(external_order.status),
      submit_time=external_order.create_time,
      filled_volume=external_order.filled_volume,
      avg_price=external_order.avg_price,
      last_update_time=external_order.update_time,
    )

  def _create_rejected_order(self, request: OrderRequest, reason: str) -> OrderResponse:
    """创建被拒绝的订单"""
    order = OrderResponse(
      order_id=self.generate_order_id(),
      request=request,
      status=OrderStatus.REJECTED,
      submit_time=time_utils.now(),
      error_message=reason,
    )
    self.orders[order.order_id] = order
    return order
