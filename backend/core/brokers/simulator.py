"""
模拟 Broker - 使用实时数据流的模拟交易
"""

import asyncio
import random
from datetime import datetime
from typing import Any, Dict, Optional

from .backtest import BacktestBroker
from .base import OrderRequest, OrderResponse, OrderStatus, OrderType, PriceType
from core.utils import time_utils


class SimulatorBroker(BacktestBroker):
  """模拟交易 Broker - 使用实时数据但不产生真实委托"""

  def __init__(
    self,
    account_id: str = "paper",
    initial_capital: float = 1000000.0,
    commission_rate: float = 0.0003,
    slippage_rate: float = 0.0002,
    min_commission: float = 5.0,
    delay_mean: float = 0.5,  # 平均延迟（秒）
    delay_std: float = 0.2,  # 延迟标准差
    partial_fill_prob: float = 0.1,  # 部分成交概率
    enable_risk_control: bool = True,
    max_order_amount: float = 1000000.0,
    max_position_value: float = 5000000.0,
  ):
    super().__init__(
      account_id, initial_capital, commission_rate, slippage_rate, min_commission
    )

    # 模拟参数
    self.delay_mean = delay_mean
    self.delay_std = delay_std
    self.partial_fill_prob = partial_fill_prob

    # 风控配置
    self.enable_risk_control = enable_risk_control
    self.max_order_amount = max_order_amount
    self.max_position_value = max_position_value

    # 实时数据订阅
    self.subscribed_instruments: set = set()
    self.realtime_prices: Dict[str, float] = {}

    self.logger.name = "SimulatorBroker"

  async def connect(self) -> bool:
    """连接到模拟交易系统"""
    self.logger.info(
      f"模拟 Broker 初始化，初始资金: {self.initial_capital}, "
      f"延迟: {self.delay_mean}±{self.delay_std}s"
    )
    return True

  async def subscribe_realtime_data(self, instrument_code: str) -> None:
    """
    订阅实时数据
    Args:
        instrument_code: 标的代码
    """
    self.subscribed_instruments.add(instrument_code)
    self.logger.info(f"订阅实时数据: {instrument_code}")

  async def unsubscribe_realtime_data(self, instrument_code: str) -> None:
    """
    取消订阅实时数据
    Args:
        instrument_code: 标的代码
    """
    self.subscribed_instruments.discard(instrument_code)
    self.logger.info(f"取消订阅实时数据: {instrument_code}")

  async def place_order(self, request: OrderRequest) -> OrderResponse:
    """下单（带模拟延迟）"""
    # 添加随机延迟模拟网络延迟
    delay = max(0, random.gauss(self.delay_mean, self.delay_std))
    await asyncio.sleep(delay)

    # 创建订单
    order_id = self.generate_order_id()
    order = OrderResponse(
      order_id=order_id,
      request=request,
      status=OrderStatus.PENDING,
      submit_time=time_utils.now(),
    )

    # 基本验证
    if request.volume <= 0:
      order.status = OrderStatus.REJECTED
      order.error_message = "订单数量必须大于0"
      self.orders[order_id] = order
      return order

    # 风控检查
    if self.enable_risk_control:
      risk_res = await self._risk_check(request)
      if not risk_res["passed"]:
        order.status = OrderStatus.REJECTED
        order.error_message = risk_res["reason"]
        self.orders[order_id] = order
        return order

    # 检查资金/持仓
    if not await self._validate_order(request):
      order.status = OrderStatus.REJECTED
      order.error_message = "资金或持仓不足"
      self.orders[order_id] = order
      return order

    # 设置订单状态为已提交
    order.status = OrderStatus.SUBMITTED
    self.orders[order_id] = order

    # 异步处理订单执行
    asyncio.create_task(self._process_order_async(order))

    await self.emit_order_update(order)
    return order

  async def _process_order_async(self, order: OrderResponse) -> None:
    """异步处理订单执行"""
    request = order.request
    instrument_code = request.instrument_code

    # 等待价格更新
    max_wait = 30  # 最多等待30秒
    wait_time = 0

    while wait_time < max_wait:
      # 获取当前价格
      current_price = self.realtime_prices.get(
        instrument_code
      ) or self.current_prices.get(instrument_code)

      if not current_price:
        await asyncio.sleep(1)
        wait_time += 1
        continue

      # 判断是否应该成交
      should_execute = False

      if request.price_type == PriceType.MARKET:
        should_execute = True
      elif request.price_type == PriceType.LIMIT:
        if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
          should_execute = current_price <= request.price
        else:
          should_execute = current_price >= request.price

      if should_execute:
        # 模拟成交延迟
        exec_delay = max(0, random.gauss(0.2, 0.1))
        await asyncio.sleep(exec_delay)

        # 决定是否部分成交
        if random.random() < self.partial_fill_prob and request.volume > 100:
          # 部分成交
          filled_volume = random.randint(100, request.volume - 100)
          await self._execute_partial_trade(order, current_price, filled_volume)
        else:
          # 全部成交
          await self._execute_trade_with_slippage(order, current_price)

        return

      await asyncio.sleep(0.5)
      wait_time += 0.5

    # 超时未成交
    if order.status == OrderStatus.SUBMITTED:
      order.status = OrderStatus.EXPIRED
      order.last_update_time = time_utils.now()
      await self.emit_order_update(order)

  async def _execute_trade_with_slippage(
    self, order: OrderResponse, base_price: float
  ) -> None:
    """执行成交（带滑点模拟）"""
    request = order.request

    # 计算滑点
    slippage_factor = random.uniform(0, self.slippage_rate * 2)  # 随机滑点
    slippage_amount = base_price * slippage_factor

    if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      execution_price = base_price + slippage_amount
    else:
      execution_price = base_price - slippage_amount

    # 执行成交
    await self._execute_trade(order, execution_price, request.volume)

  async def _execute_partial_trade(
    self, order: OrderResponse, price: float, filled_volume: int
  ) -> None:
    """执行部分成交"""
    request = order.request
    instrument_code = request.instrument_code

    # 计算成交金额和手续费
    amount = price * filled_volume
    commission = max(amount * self.commission_rate, self.min_commission)

    # 更新订单状态
    order.status = OrderStatus.PARTIAL_FILLED
    order.filled_volume += filled_volume
    order.filled_amount += amount

    # 计算加权平均价格
    if order.filled_volume > 0:
      order.avg_price = order.filled_amount / order.filled_volume

    order.commission += commission
    order.last_update_time = time_utils.now()

    # 生成成交记录
    trade = self._create_trade_record(
      order.order_id,
      instrument_code,
      request.order_type,
      price,
      filled_volume,
      amount,
      commission,
      metadata=dict(request.metadata or {}),
    )
    self.trades.append(trade)

    # 更新账户
    if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      self.cash -= amount + commission
      self._update_position_buy(instrument_code, filled_volume, price)
    else:
      self.cash += amount - commission
      self._update_position_sell(instrument_code, filled_volume, price)

    # 发送回调
    await self.emit_order_update(order)
    await self.emit_trade_update(trade)

    self.logger.info(
      f"部分成交: {instrument_code} {request.order_type.value} "
      f"{filled_volume}/{request.volume}股 @ {price:.2f}"
    )

    # 继续处理剩余数量
    if order.filled_volume < request.volume:
      asyncio.create_task(self._process_remaining_order(order))

  async def _process_remaining_order(self, order: OrderResponse) -> None:
    """处理剩余未成交数量"""
    # 等待一段时间后继续尝试成交
    await asyncio.sleep(random.uniform(1, 3))

    request = order.request
    remaining_volume = request.volume - order.filled_volume

    # 获取最新价格
    current_price = self.realtime_prices.get(
      request.instrument_code
    ) or self.current_prices.get(request.instrument_code)

    if current_price:
      # 执行剩余数量
      await self._execute_trade(order, current_price, remaining_volume)

  def _create_trade_record(
    self,
    order_id: str,
    instrument_code: str,
    trade_type: OrderType,
    price: float,
    volume: int,
    amount: float,
    commission: float,
    metadata: Optional[Dict[str, Any]] = None,
  ) -> Any:
    """创建成交记录"""
    from .base import TradeRecord

    return TradeRecord(
      trade_id=self.generate_trade_id(),
      order_id=order_id,
      instrument_code=instrument_code,
      trade_type=trade_type,
      price=price,
      volume=volume,
      amount=amount,
      commission=commission,
      trade_time=time_utils.now(),
      metadata=dict(metadata or {}),
    )

  async def update_realtime_price(
    self, instrument_code: str, price: float, timestamp: Optional[datetime] = None
  ) -> None:
    """
    更新实时价格（模拟交易专用）
    Args:
        instrument_code: 标的代码
        price: 最新价格
        timestamp: 时间戳
    """
    self.realtime_prices[instrument_code] = price
    self.current_prices[instrument_code] = price
    self.current_time = timestamp or time_utils.now()

    # 更新持仓市值
    if instrument_code in self.positions:
      position = self.positions[instrument_code]
      position.last_price = price
      position.market_value = (position.long_volume - position.short_volume) * price

      # 计算盈亏
      if position.long_volume > 0:
        position.pnl = (price - position.long_avg_price) * position.long_volume
      if position.short_volume > 0:
        position.pnl += (position.short_avg_price - price) * position.short_volume

      if position.market_value != 0:
        position.pnl_percent = position.pnl / abs(position.market_value)

    # 更新权益曲线
    await self._update_equity_curve()

  async def _risk_check(self, request: OrderRequest) -> Dict[str, Any]:
    """风控检查 (模拟)"""
    # 如果价格是0，先尝试获取当前价格
    price = request.price
    if price <= 0:
      price = self.realtime_prices.get(request.instrument_code) or self.current_prices.get(request.instrument_code, 0.0)

    order_amount = price * request.volume

    # 1. 检查单笔金额限制
    if order_amount > self.max_order_amount:
      return {
        "passed": False,
        "reason": f"单笔金额 {order_amount:.2f} 超过限制 {self.max_order_amount:.2f}",
      }

    # 2. 检查持仓限制
    if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      position = self.positions.get(request.instrument_code)
      if position:
        new_value = position.market_value + order_amount
        if new_value > self.max_position_value:
          return {
            "passed": False,
            "reason": f"持仓市值 {new_value:.2f} 超过限制 {self.max_position_value:.2f}",
          }

    # 3. 检查账户资金
    if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      required_cash = order_amount * (1 + self.commission_rate)
      if required_cash > self.cash:
        return {
          "passed": False,
          "reason": f"可用资金不足: 需要 {required_cash:.2f}, 可用 {self.cash:.2f}",
        }

    return {"passed": True, "reason": ""}

  def get_simulation_statistics(self) -> Dict[str, Any]:
    """获取模拟统计信息"""
    base_stats = self.get_statistics()
    performance = self.get_performance_metrics()

    return {
      **base_stats,
      **performance,
      "mode": "paper_trading",
      "subscribed_instruments": list(self.subscribed_instruments),
      "realtime_prices_count": len(self.realtime_prices),
      "delay_config": {"mean": self.delay_mean, "std": self.delay_std},
      "partial_fill_probability": self.partial_fill_prob,
    }
