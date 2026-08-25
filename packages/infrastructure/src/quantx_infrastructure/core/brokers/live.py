"""
实盘 Broker - 对接 XTQuant 真实交易
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from quantx_domain.brokers.base import (
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

from quantx_infrastructure.core.utils import time_utils


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
    super().__init__(account_id, float(initial_capital))

    # 风控参数
    self.enable_risk_control = enable_risk_control
    self.max_order_amount = max_order_amount
    self.max_position_value = max_position_value

    # 交易服务
    self.trading_service: Optional[Any] = None

    # 订单映射（内部ID -> 外部ID）
    self.order_id_mapping: Dict[str, str] = {}
    self.external_to_internal: Dict[str, str] = {}

    # 连接状态
    self.is_connected = False
    self._monitor_task: Optional[asyncio.Task] = None

    self.logger = logging.getLogger("LiveBroker")

  async def connect(self) -> bool:
    """连接到真实交易系统"""
    try:
      from quantx_infrastructure.services.trading_service import TradingService

      self.trading_service = TradingService(
        account_id=self.account_id,
        execution_mode="live",
      )

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

      # 启动订单状态监控，并由 Broker 显式拥有其生命周期。
      if self._monitor_task is None or self._monitor_task.done():
        self._monitor_task = asyncio.create_task(
          self._monitor_orders(),
          name=f"live-broker-monitor:{self.account_id}",
        )

      return True

    except Exception as e:
      self.logger.error(f"连接失败: {e}")
      return False

  async def disconnect(self) -> None:
    """断开连接"""
    self.is_connected = False
    monitor_task = self._monitor_task
    self._monitor_task = None
    if monitor_task is not None and monitor_task is not asyncio.current_task():
      if not monitor_task.done():
        monitor_task.cancel()
      await asyncio.gather(monitor_task, return_exceptions=True)
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
      result = await self.trading_service.place_order(
        stock_code=request.instrument_code,
        order_type=xt_order_type,
        order_volume=request.volume,
        price_type=xt_price_type,
        price=request.price,
        strategy_name=request.metadata.get("strategy_name", ""),
        order_remark=request.metadata.get("remark", ""),
        idempotency_key=str(
          request.metadata.get("idempotency_key")
          or request.metadata.get("trace_id")
          or internal_order_id
        ),
        execution_context={
          **dict(request.metadata or {}),
          "strategy_order_id": internal_order_id,
        },
      )
      if not isinstance(result, dict) or not result.get("success"):
        message = result.get("message") if isinstance(result, dict) else "下单失败"
        raise RuntimeError(message or "下单失败")
      client_order_id = str(result.get("client_order_id") or "")
      if not client_order_id:
        raise RuntimeError("交易服务未返回 client_order_id")

      # 保存订单映射
      self.order_id_mapping[internal_order_id] = client_order_id
      self.external_to_internal[client_order_id] = internal_order_id

      # 排队和投递均不等于券商已报；等待 Engine 消费真实 order_report。
      order.status = OrderStatus.PENDING
      order.last_update_time = time_utils.now()

      self.orders[internal_order_id] = order
      await self.emit_order_update(order)

      self.logger.info(
        f"下单成功: {request.instrument_code} {request.order_type.value} "
        f"{request.volume}股 @ {request.price:.2f}, "
        f"订单ID: {internal_order_id} -> client:{client_order_id}"
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

    client_order_id = self.order_id_mapping.get(order_id)
    if not client_order_id:
      self.logger.error(f"找不到订单映射: {order_id}")
      return False

    try:
      result = await self.trading_service.cancel_pending_order(
        client_order_id=client_order_id,
      )

      if result.get("success"):
        self.logger.info(
          "撤单请求已接受: %s -> client:%s",
          order_id,
          result.get("client_order_id") or client_order_id,
        )
        return True
      self.logger.warning(
        "撤单请求失败: %s -> client:%s (%s)",
        order_id,
        client_order_id,
        result.get("message", ""),
      )
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

    client_order_id = self.order_id_mapping.get(order_id)
    if client_order_id:
      try:
        external_order = await self.trading_service.order_for_client_order(
          client_order_id
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
      positions = await self.trading_service.position_service.get_positions(
        account_id=self.account_id
      )

      result = {}
      for pos in positions:
        if instrument_code and pos.stock_code != instrument_code:
          continue

        volume = int(pos.volume or 0)
        available_volume = int(pos.can_use_volume or 0)
        avg_price = float(pos.avg_price or 0.0)
        market_value = float(pos.market_value or 0.0)
        last_price = float(getattr(pos, "last_price", 0.0) or 0.0)
        if last_price <= 0 and volume > 0:
          last_price = market_value / volume
        pnl = (last_price - avg_price) * volume if volume > 0 else 0.0
        cost = avg_price * volume

        position = Position(
          instrument_code=pos.stock_code,
          long_volume=volume,
          available_volume=available_volume,
          frozen_volume=int(pos.frozen_volume or max(0, volume - available_volume)),
          today_buy_volume=max(0, volume - int(pos.yesterday_volume or 0)),
          long_avg_price=avg_price,
          market_value=market_value,
          pnl=pnl,
          pnl_percent=(pnl / cost * 100.0) if cost > 0 else 0.0,
          last_price=last_price,
        )
        result[pos.stock_code] = position

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
      total_asset = float(account.total_asset or 0.0)

      return AccountInfo(
        account_id=account.account_id,
        total_asset=total_asset,
        cash=float(account.cash or 0.0),
        frozen_cash=float(account.frozen_cash or 0.0),
        market_value=float(account.market_value or 0.0),
        total_pnl=total_asset - self.initial_capital,
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
            client_order_id = self.order_id_mapping.get(internal_id)
            if client_order_id:
              # 查询外部订单状态
              await self._update_order_status(internal_id, client_order_id)

        await asyncio.sleep(1)  # 每秒检查一次

      except Exception as e:
        self.logger.error(f"订单监控异常: {e}")
        await asyncio.sleep(5)

  async def _update_order_status(
    self,
    internal_id: str,
    client_order_id: str,
  ) -> None:
    """更新订单状态"""
    try:
      # 查询外部订单
      external_order = await self.trading_service.order_for_client_order(
        client_order_id
      )

      if external_order:
        internal_order = self.orders[internal_id]
        old_status = internal_order.status
        old_filled_volume = int(internal_order.filled_volume or 0)

        # 转换状态
        new_status = self._convert_order_status(external_order.status)
        filled_volume = int(getattr(external_order, "traded_volume", 0) or 0)
        avg_price = float(getattr(external_order, "traded_price", 0.0) or 0.0)

        if new_status != old_status or filled_volume != old_filled_volume:
          internal_order.status = new_status
          internal_order.filled_volume = filled_volume
          internal_order.filled_amount = avg_price * filled_volume
          internal_order.avg_price = avg_price
          internal_order.last_update_time = time_utils.now()

          # 委托回报中的累计成交量仅转换为增量成交事件，避免重复记账。
          incremental_volume = max(0, filled_volume - old_filled_volume)
          if incremental_volume > 0:
            await self._create_trade_from_order(
              internal_order,
              price=avg_price,
              volume=incremental_volume,
              trade_time=getattr(external_order, "time", None),
            )

          await self.emit_order_update(internal_order)

    except Exception as e:
      self.logger.error(f"更新订单状态失败: {e}")

  async def _create_trade_from_order(
    self,
    internal_order: OrderResponse,
    *,
    price: float,
    volume: int,
    trade_time: Optional[datetime] = None,
  ) -> None:
    """从订单创建成交记录"""
    trade = TradeRecord(
      trade_id=self.generate_trade_id(),
      order_id=internal_order.order_id,
      instrument_code=internal_order.request.instrument_code,
      trade_type=internal_order.request.order_type,
      price=price,
      volume=volume,
      amount=price * volume,
      commission=0.0,
      trade_time=trade_time or time_utils.now(),
      metadata=dict(internal_order.request.metadata or {}),
    )

    self.trades.append(trade)
    await self.emit_trade_update(trade)

  def _convert_order_type(self, order_type: OrderType) -> Any:
    """转换订单类型到 XTQuant"""
    from quantx_infrastructure.models.enums import OrderType as XTOrderType

    mapping = {
      OrderType.BUY: XTOrderType.BUY,
      OrderType.SELL: XTOrderType.SELL,
    }
    return mapping.get(order_type, XTOrderType.BUY)

  def _convert_price_type(self, price_type: PriceType) -> Any:
    """转换价格类型到 XTQuant"""
    from quantx_infrastructure.models.enums import PriceType as XTPriceType

    mapping = {
      PriceType.LIMIT: XTPriceType.LIMIT,
      PriceType.MARKET: XTPriceType.MARKET_CONVERT_5_LIMIT,
    }
    return mapping.get(price_type, XTPriceType.MARKET_CONVERT_5_LIMIT)

  def _convert_order_status(self, external_status: Any) -> OrderStatus:
    """转换外部订单状态"""
    from quantx_infrastructure.models.enums import OrderStatus as ExternalOrderStatus

    if hasattr(external_status, "name"):
      status_name = str(external_status.name)
    else:
      try:
        status_name = ExternalOrderStatus(external_status).name
      except (TypeError, ValueError):
        status_name = str(external_status or "").split(".")[-1].upper()
    status_mapping = {
      "UNREPORTED": OrderStatus.PENDING,
      "WAIT_REPORTING": OrderStatus.SUBMITTED,
      "REPORTED": OrderStatus.SUBMITTED,
      "REPORTED_CANCEL": OrderStatus.SUBMITTED,
      "PARTSUCC_CANCEL": OrderStatus.PARTIAL_FILLED,
      "PART_SUCC": OrderStatus.PARTIAL_FILLED,
      "PART_CANCEL": OrderStatus.CANCELLED,
      "CANCELED": OrderStatus.CANCELLED,
      "SUCCEEDED": OrderStatus.FILLED,
      "JUNK": OrderStatus.REJECTED,
      "UNKNOWN": OrderStatus.PENDING,
    }
    return status_mapping.get(status_name, OrderStatus.PENDING)

  def _convert_external_order(
    self, external_order: Any, internal_id: str
  ) -> OrderResponse:
    """转换外部订单到内部格式"""
    from quantx_infrastructure.models.enums import OrderType as ExternalOrderType

    order_type = (
      OrderType.BUY
      if external_order.type == ExternalOrderType.BUY
      else OrderType.SELL
    )
    return OrderResponse(
      order_id=internal_id,
      request=OrderRequest(
        instrument_code=external_order.stock_code,
        order_type=order_type,
        price_type=PriceType.LIMIT,
        volume=external_order.volume,
        price=external_order.price,
      ),
      status=self._convert_order_status(external_order.status),
      submit_time=external_order.time,
      filled_volume=external_order.traded_volume,
      filled_amount=float(external_order.traded_price or 0.0)
      * int(external_order.traded_volume or 0),
      avg_price=external_order.traded_price,
      last_update_time=getattr(external_order, "updated_at", None)
      or external_order.time,
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
