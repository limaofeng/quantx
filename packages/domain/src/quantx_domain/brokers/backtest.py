"""
回测 Broker - 内存撮合引擎，用于历史数据回测
"""

import logging
from datetime import date, datetime
from math import isfinite
from typing import Any, Dict, List, Optional

import numpy as np

from quantx_domain import clock as time_utils
from quantx_domain.trading.market_rules import MarketDataSnapshot

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


class BacktestBroker(BrokerBase):
  """回测 Broker - 模拟交易执行"""

  def __init__(
    self,
    account_id: str = "backtest",
    initial_capital: float = 1000000.0,
    commission_rate: float = 0.0003,
    slippage_rate: float = 0.0001,
    min_commission: float = 5.0,
    stamp_tax_rate: float = 0.0005,
    transfer_fee_rate: float = 0.00001,
    participation_cap_pct: float = 0.05,
    book_depth_participation_pct: float = 0.25,
    strict_book_depth: bool = False,
    no_queue_credit: bool = False,
    defer_new_orders_until_next_quote: bool = False,
  ):
    super().__init__(account_id, initial_capital)

    # 回测参数
    self.commission_rate = commission_rate
    self.slippage_rate = slippage_rate
    self.min_commission = min_commission
    self.stamp_tax_rate = stamp_tax_rate
    self.transfer_fee_rate = transfer_fee_rate
    self.participation_cap_pct = max(
      0.001, min(1.0, float(participation_cap_pct or 0.05))
    )
    self.book_depth_participation_pct = max(
      0.001, min(1.0, float(book_depth_participation_pct or 0.25))
    )
    self.strict_book_depth = bool(strict_book_depth)
    self.no_queue_credit = bool(no_queue_credit)
    self.defer_new_orders_until_next_quote = bool(
      defer_new_orders_until_next_quote
    )

    # 账户状态
    self.cash = initial_capital
    self.frozen_cash = 0.0
    self.positions: Dict[str, Position] = {}

    # 市场数据
    self.current_prices: Dict[str, float] = {}
    self.market_snapshots: Dict[str, MarketDataSnapshot] = {}
    self.current_time: Optional[datetime] = None
    self.current_trading_date: Optional[date] = None

    # 待处理订单
    self.pending_orders: List[OrderResponse] = []

    # 绩效统计
    self.equity_curve: List[float] = [initial_capital]
    self.daily_returns: List[float] = []
    self.max_drawdown = 0.0
    self.peak_equity = initial_capital
    self.total_trades = 0
    self.winning_trades = 0
    self.losing_trades = 0
    self.constraint_statistics: Dict[str, Any] = {
      "limit_up_buy_blocked": 0,
      "limit_down_sell_blocked": 0,
      "suspended_blocked": 0,
      "partial_fills": 0,
      "full_fills": 0,
      "liquidity_capped_orders": 0,
      "book_depth_capped_orders": 0,
      "missing_book_depth_blocked": 0,
      "no_queue_credit_blocked": 0,
      "cash_capped_orders": 0,
      "expired_orders": 0,
      "unfilled_volume": 0,
      "fake_fill_penalty": 0.0,
      "ghost_dca_invested": 0.0,
      "ghost_dca_units": 0.0,
      "ghost_dca_last_price": 0.0,
    }
    self.passive_cash = initial_capital
    self.passive_volumes: Dict[str, int] = {}
    self.passive_prices: Dict[str, float] = {}
    self.non_trading_asset_value = 0.0
    self.initial_asset_reconciliation: Dict[str, Any] = {
      "schema_version": 1,
      "reported_total_asset": float(initial_capital),
      "available_cash": float(initial_capital),
      "position_market_value": 0.0,
      "raw_residual": 0.0,
      "non_trading_asset": 0.0,
      "effective_initial_equity": float(initial_capital),
      "negative_residual_clamped": False,
      "policy": "PRESERVE_POSITIVE_RESIDUAL_CLAMP_NEGATIVE_TO_ZERO",
      "quality_flags": [],
    }
    self.replay_curve: List[Dict[str, Any]] = []

    self.logger = logging.getLogger("BacktestBroker")

  async def connect(self) -> bool:
    """连接（回测模式始终返回成功）"""
    self.logger.info(f"回测 Broker 初始化，初始资金: {self.initial_capital}")
    return True

  def configure_initial_portfolio(
    self,
    *,
    cash: float,
    total_asset: float,
    positions: Dict[str, Position],
  ) -> None:
    """Configure the tradable portfolio and a constant non-trading asset residual.

    The residual reconciles reported total assets with available cash and the
    position rows supplied to the backtest.  It is deliberately excluded from
    order cash and positions, but included in both active and passive equity.
    """

    raw_cash = float(cash or 0.0)
    raw_total_asset = float(total_asset or 0.0)
    position_values = [
      float(position.market_value or 0.0) for position in positions.values()
    ]
    if not all(
      isfinite(value) for value in (raw_cash, raw_total_asset, *position_values)
    ):
      raise ValueError("Initial portfolio assets must be finite numbers")
    self.cash = max(0.0, raw_cash)
    reported_total_asset = max(0.0, raw_total_asset)
    position_market_value = sum(max(0.0, value) for value in position_values)
    component_total = self.cash + position_market_value
    raw_residual = reported_total_asset - component_total
    self.non_trading_asset_value = max(0.0, raw_residual)
    self.initial_capital = component_total + self.non_trading_asset_value
    quality_flags = []
    if raw_residual > 0.01:
      quality_flags.append("NON_TRADING_ASSET_RESIDUAL_PRESERVED")
    elif raw_residual < -0.01:
      quality_flags.append("INITIAL_COMPONENTS_EXCEED_REPORTED_TOTAL")
    self.initial_asset_reconciliation = {
      "schema_version": 1,
      "reported_total_asset": reported_total_asset,
      "available_cash": self.cash,
      "position_market_value": position_market_value,
      "raw_residual": raw_residual,
      "non_trading_asset": self.non_trading_asset_value,
      "effective_initial_equity": self.initial_capital,
      "negative_residual_clamped": raw_residual < 0.0,
      "policy": "PRESERVE_POSITIVE_RESIDUAL_CLAMP_NEGATIVE_TO_ZERO",
      "quality_flags": quality_flags,
    }
    self.equity_curve = [self.initial_capital]
    self.peak_equity = self.initial_capital
    self.passive_cash = self.cash
    self.passive_volumes = {
      code: max(0, int(position.long_volume or 0))
      for code, position in positions.items()
    }
    self.passive_prices = {
      code: float(position.last_price or position.long_avg_price or 0.0)
      for code, position in positions.items()
    }

  async def disconnect(self) -> None:
    """断开连接"""
    self.logger.info("回测 Broker 断开连接")

  async def place_order(self, request: OrderRequest) -> OrderResponse:
    """下单"""
    order_id = self.generate_order_id()

    # 创建订单响应
    order = OrderResponse(
      order_id=order_id,
      request=request,
      status=OrderStatus.PENDING,
      submit_time=self.current_time or time_utils.now(),
    )

    # 基本验证
    if request.volume <= 0:
      order.status = OrderStatus.REJECTED
      order.error_message = "订单数量必须大于0"
      self.orders[order_id] = order
      return order

    # 检查资金/持仓
    if not await self._validate_order(request):
      order.status = OrderStatus.REJECTED
      order.error_message = "资金或持仓不足"
      self.orders[order_id] = order
      return order

    # 普通回测维持原有市价单语义；严格 Tick 回放必须等下一笔行情，
    # 防止策略读取当前 Tick 后生成的订单反过来成交在同一 Tick。
    if (
      request.price_type == PriceType.MARKET
      and not self.defer_new_orders_until_next_quote
    ):
      await self._execute_market_order(order)
    else:
      # 限价单加入待处理队列
      order.status = OrderStatus.SUBMITTED
      self.pending_orders.append(order)

    self.orders[order_id] = order
    await self.emit_order_update(order)

    return order

  async def cancel_order(self, order_id: str) -> bool:
    """撤单"""
    if order_id not in self.orders:
      return False

    order = self.orders[order_id]

    if order.status in [
      OrderStatus.PENDING,
      OrderStatus.SUBMITTED,
      OrderStatus.PARTIAL_FILLED,
    ]:
      order.status = OrderStatus.CANCELLED
      order.last_update_time = self.current_time or time_utils.now()

      # 从待处理队列移除
      self.pending_orders = [o for o in self.pending_orders if o.order_id != order_id]

      await self.emit_order_update(order)
      return True

    return False

  async def get_order(self, order_id: str) -> Optional[OrderResponse]:
    """查询订单"""
    return self.orders.get(order_id)

  async def get_position(self, instrument_code: str = None) -> Dict[str, Position]:
    """查询持仓"""
    if instrument_code:
      position = self.positions.get(instrument_code)
      return {instrument_code: position} if position else {}
    return self.positions.copy()

  async def get_account(self) -> AccountInfo:
    """查询账户信息"""
    # 计算总资产
    market_value = sum(pos.market_value for pos in self.positions.values())
    total_asset = self.cash + market_value + self.non_trading_asset_value

    # 计算总盈亏
    total_pnl = total_asset - self.initial_capital
    daily_pnl = 0.0  # 简化处理

    return AccountInfo(
      account_id=self.account_id,
      total_asset=total_asset,
      cash=self.cash,
      frozen_cash=self.frozen_cash,
      market_value=market_value,
      total_pnl=total_pnl,
      daily_pnl=daily_pnl,
      positions=self.positions.copy(),
      last_update_time=self.current_time or time_utils.now(),
    )

  async def get_trades(
    self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
  ) -> List[TradeRecord]:
    """查询成交记录"""
    trades = self.trades

    if start_time:
      trades = [t for t in trades if t.trade_time >= start_time]
    if end_time:
      trades = [t for t in trades if t.trade_time <= end_time]

    return trades

  async def update_market_data(
    self,
    instrument_code: str,
    price: float,
    timestamp: Optional[datetime] = None,
    *,
    market_data: Optional[MarketDataSnapshot] = None,
  ) -> None:
    """
    更新市场数据（回测专用）
    Args:
        instrument_code: 标的代码
        price: 最新价格
        timestamp: 时间戳
    """
    self.current_prices[instrument_code] = price
    self.current_time = timestamp or time_utils.now()
    self._update_ghost_dca(price)
    if market_data is None:
      market_data = MarketDataSnapshot(
        instrument_code=instrument_code,
        timestamp=self.current_time,
        price=price,
        close=price,
        source="price",
      )
    self.market_snapshots[instrument_code] = market_data
    self._settle_if_new_day(self.current_time.date())
    await self._expire_pending_orders()

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

    # 处理待成交的限价单
    await self._process_pending_orders(instrument_code, price)

    # 更新权益曲线
    await self._update_equity_curve()
    self._record_replay_curve()

  async def advance_time(self, timestamp: datetime) -> None:
    """Advance replay time without inventing a quote or a fill.

    Approval-delay and end-of-window events can occur between market ticks.
    They still need deterministic T+1 settlement and order expiry, but must not
    receive liquidity credit from the last observed order book.
    """

    if not isinstance(timestamp, datetime):
      raise TypeError("BacktestBroker timestamp must be a datetime")
    if self.current_time is not None:
      try:
        moved_backwards = timestamp < self.current_time
      except TypeError as exc:
        raise ValueError(
          "BacktestBroker cannot mix timezone-aware and timezone-naive timestamps"
        ) from exc
      if moved_backwards:
        raise ValueError("BacktestBroker time cannot move backwards")
    self.current_time = timestamp
    self._settle_if_new_day(timestamp.date())
    await self._expire_pending_orders()

  async def refresh_performance_snapshot(self) -> None:
    """Record account performance after an order placed on the final quote.

    End-of-window liquidation happens after the last market-data callback.  It
    must therefore refresh the account curve explicitly, without replaying the
    same quote (which would also re-run pending-order and ghost-DCA logic).
    """

    await self._update_equity_curve()
    self._record_replay_curve()

  async def _validate_order(self, request: OrderRequest) -> bool:
    """验证订单"""
    if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      # 买入验证
      amount = request.volume * request.price
      costs = self._calculate_costs(amount, request.order_type)
      required_cash = amount + costs["total"]
      available_cash = self.cash
      if self.defer_new_orders_until_next_quote:
        available_cash -= self._reserved_pending_buy_cash()
      if required_cash > available_cash:
        self.logger.warning(
          f"资金不足: 需要 {required_cash}, 可用 {max(0.0, available_cash)}"
        )
        return False

    else:
      # 卖出验证
      position = self.positions.get(request.instrument_code)
      available_volume = position.available_volume if position else 0
      if self.defer_new_orders_until_next_quote:
        available_volume -= self._reserved_pending_sell_volume(
          request.instrument_code
        )
      if not position or available_volume < request.volume:
        self.logger.warning(
          f"持仓不足: {request.instrument_code} 需要 {request.volume}, "
          f"可用 {available_volume}"
        )
        return False

    return True

  def _reserved_pending_buy_cash(self) -> float:
    reserved = 0.0
    for order in self.pending_orders:
      request = order.request
      if request.order_type not in [OrderType.BUY, OrderType.BUY_TO_COVER]:
        continue
      remaining = max(0, int(request.volume) - int(order.filled_volume or 0))
      amount = remaining * max(0.0, float(request.price or 0.0))
      reserved += amount + self._calculate_costs(amount, request.order_type)["total"]
    return reserved

  def _reserved_pending_sell_volume(self, instrument_code: str) -> int:
    return sum(
      max(0, int(order.request.volume) - int(order.filled_volume or 0))
      for order in self.pending_orders
      if order.request.instrument_code == instrument_code
      and order.request.order_type
      not in [OrderType.BUY, OrderType.BUY_TO_COVER]
    )

  async def _execute_market_order(self, order: OrderResponse) -> None:
    """执行市价单"""
    request = order.request
    instrument_code = request.instrument_code

    # 获取当前价格
    current_price = self.current_prices.get(instrument_code)
    if not current_price:
      order.status = OrderStatus.REJECTED
      order.error_message = f"无法获取 {instrument_code} 的当前价格"
      return
    market_data = self.market_snapshots.get(instrument_code)
    if self._is_blocked_by_limit(request, market_data):
      self._record_limit_block(request, market_data)
      order.status = OrderStatus.REJECTED
      order.error_message = "涨跌停或停牌约束下无法成交"
      return

    # 计算滑点
    slippage_amount = current_price * self.slippage_rate
    if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      execution_price = current_price + slippage_amount  # 买入价格更高
    else:
      execution_price = current_price - slippage_amount  # 卖出价格更低

    # 执行成交
    await self._execute_trade(order, execution_price, request.volume)

  async def _execute_trade(
    self, order: OrderResponse, price: float, volume: int
  ) -> None:
    """执行成交"""
    request = order.request
    instrument_code = request.instrument_code

    # 计算成交金额和完整 A 股交易成本
    amount = price * volume
    previous_costs = self._calculate_costs(order.filled_amount, request.order_type)
    cumulative_costs = self._calculate_costs(
      order.filled_amount + amount, request.order_type
    )
    costs = {
      key: max(0.0, cumulative_costs[key] - previous_costs[key])
      for key in cumulative_costs
    }
    commission = costs["total"]

    # 更新订单状态（支持部分成交）
    previous_amount = order.filled_amount
    order.filled_volume += volume
    order.filled_amount += amount
    order.avg_price = (
      (previous_amount + amount) / order.filled_volume
      if order.filled_volume > 0
      else price
    )
    order.commission += commission
    order.status = (
      OrderStatus.FILLED
      if order.filled_volume >= request.volume
      else OrderStatus.PARTIAL_FILLED
    )
    if order.status == OrderStatus.FILLED:
      self.constraint_statistics["full_fills"] += 1
    else:
      self.constraint_statistics["partial_fills"] += 1
    order.slippage = self.calculate_slippage(request.price, price, request.order_type)
    order.last_update_time = self.current_time or time_utils.now()

    # 生成成交记录
    trade = TradeRecord(
      trade_id=self.generate_trade_id(),
      order_id=order.order_id,
      instrument_code=instrument_code,
      trade_type=request.order_type,
      price=price,
      volume=volume,
      amount=amount,
      commission=commission,
      trade_time=self.current_time or time_utils.now(),
      metadata={**dict(request.metadata or {}), "costs": costs},
    )
    self.trades.append(trade)

    # 更新账户和持仓
    if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      # 买入
      self.cash -= amount + commission
      self._update_position_buy(instrument_code, volume, price)
    else:
      # 卖出
      self.cash += amount - commission
      self._update_position_sell(instrument_code, volume, price)

    # 更新统计
    self.total_trades += 1

    # 发送回调
    await self.emit_order_update(order)
    await self.emit_trade_update(trade)

    self.logger.info(
      f"成交: {instrument_code} {request.order_type.value} "
      f"{volume}股 @ {price:.2f}, 手续费: {commission:.2f}"
    )

  def _update_position_buy(
    self, instrument_code: str, volume: int, price: float
  ) -> None:
    """更新持仓（买入）"""
    if instrument_code not in self.positions:
      self.positions[instrument_code] = Position(instrument_code=instrument_code)

    position = self.positions[instrument_code]

    # 更新加权平均价格
    total_value = position.long_volume * position.long_avg_price + volume * price
    position.long_volume += volume
    position.today_buy_volume += volume
    position.long_avg_price = (
      total_value / position.long_volume if position.long_volume > 0 else 0
    )
    position.last_price = price
    position.market_value = position.long_volume * price

  def _calculate_costs(self, amount: float, order_type: OrderType) -> Dict[str, float]:
    amount = float(amount or 0.0)
    if amount <= 0:
      return {
        "commission": 0.0,
        "transfer_fee": 0.0,
        "stamp_tax": 0.0,
        "total": 0.0,
      }
    commission = max(amount * self.commission_rate, self.min_commission)
    transfer_fee = amount * self.transfer_fee_rate
    stamp_tax = (
      amount * self.stamp_tax_rate
      if order_type not in [OrderType.BUY, OrderType.BUY_TO_COVER]
      else 0.0
    )
    return {
      "commission": commission,
      "transfer_fee": transfer_fee,
      "stamp_tax": stamp_tax,
      "total": commission + transfer_fee + stamp_tax,
    }

  def _update_position_sell(
    self, instrument_code: str, volume: int, price: float
  ) -> None:
    """更新持仓（卖出）"""
    if instrument_code not in self.positions:
      return

    position = self.positions[instrument_code]
    position.long_volume -= volume
    position.available_volume = max(0, position.available_volume - volume)

    # 计算盈亏
    pnl = (price - position.long_avg_price) * volume
    if pnl > 0:
      self.winning_trades += 1
    else:
      self.losing_trades += 1

    # 如果持仓为0，移除持仓记录
    if position.long_volume <= 0:
      del self.positions[instrument_code]
    else:
      position.last_price = price
      position.market_value = position.long_volume * price

  async def _process_pending_orders(self, instrument_code: str, price: float) -> None:
    """处理待成交的限价单"""
    market_data = self.market_snapshots.get(instrument_code)
    orders_to_remove = []
    strict_book = (
      self._build_strict_book(market_data) if self.strict_book_depth else None
    )

    for order in self.pending_orders:
      if order.request.instrument_code != instrument_code:
        continue

      request = order.request
      if self._is_blocked_by_limit(request, market_data):
        self._record_limit_block(request, market_data)
        continue

      # 检查限价单是否应该成交
      should_execute = False
      if request.price_type == PriceType.LIMIT:
        should_execute = self._limit_touched(request, price, market_data)
      elif request.price_type == PriceType.MARKET:
        should_execute = True

      if should_execute:
        remaining = order.request.volume - order.filled_volume
        if self.strict_book_depth:
          fill_volume, execution_price = self._consume_strict_book(
            remaining,
            market_data,
            request,
            strict_book,
            order=order,
          )
        else:
          fill_volume = self._determine_fill_volume(remaining, market_data, request)
          execution_price = order.request.price
        if fill_volume <= 0:
          self.constraint_statistics["unfilled_volume"] += remaining
          continue
        if fill_volume < remaining:
          self.constraint_statistics["liquidity_capped_orders"] += 1
          self.constraint_statistics["unfilled_volume"] += remaining - fill_volume
        await self._execute_trade(order, execution_price, fill_volume)
        if order.filled_volume >= order.request.volume:
          orders_to_remove.append(order)

    for order in orders_to_remove:
      if order in self.pending_orders:
        self.pending_orders.remove(order)

  def _settle_if_new_day(self, trading_date: date) -> None:
    if self.current_trading_date == trading_date:
      return
    self.current_trading_date = trading_date
    for position in self.positions.values():
      position.available_volume = max(0, position.long_volume - position.frozen_volume)
      position.today_buy_volume = 0

  def _limit_touched(
    self,
    request: OrderRequest,
    fallback_price: float,
    market_data: Optional[MarketDataSnapshot],
  ) -> bool:
    high = (
      market_data.high
      if market_data and market_data.high is not None
      else fallback_price
    )
    low = (
      market_data.low if market_data and market_data.low is not None else fallback_price
    )
    if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      return low <= request.price
    return high >= request.price

  def _is_blocked_by_limit(
    self, request: OrderRequest, market_data: Optional[MarketDataSnapshot]
  ) -> bool:
    if not market_data:
      return False
    if (
      getattr(market_data, "suspended", False)
      or getattr(market_data, "is_trading", True) is False
    ):
      return True
    if (
      request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]
      and market_data.limit_up is not None
      and market_data.price >= market_data.limit_up
      and (
        request.price_type == PriceType.MARKET
        or request.price >= market_data.limit_up
      )
    ):
      if not self.no_queue_credit:
        return True
      return self._visible_executable_volume(request, market_data) <= 0
    if (
      request.order_type == OrderType.SELL
      and market_data.limit_down is not None
      and market_data.price <= market_data.limit_down
      and (
        request.price_type == PriceType.MARKET
        or request.price <= market_data.limit_down
      )
    ):
      if not self.no_queue_credit:
        return True
      return self._visible_executable_volume(request, market_data) <= 0
    return False

  def _record_limit_block(
    self, request: OrderRequest, market_data: Optional[MarketDataSnapshot]
  ) -> None:
    if not market_data:
      return
    if (
      getattr(market_data, "suspended", False)
      or getattr(market_data, "is_trading", True) is False
    ):
      self.constraint_statistics["suspended_blocked"] += 1
      return
    if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      self.constraint_statistics["limit_up_buy_blocked"] += 1
    elif request.order_type == OrderType.SELL:
      self.constraint_statistics["limit_down_sell_blocked"] += 1
    if self.no_queue_credit and self._visible_executable_volume(
      request, market_data
    ) <= 0:
      self.constraint_statistics["no_queue_credit_blocked"] += 1

  def _visible_executable_volume(
    self,
    request: OrderRequest,
    market_data: MarketDataSnapshot,
  ) -> int:
    is_buy = request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]
    prices = list((market_data.ask_price if is_buy else market_data.bid_price) or [])
    volumes = list((market_data.ask_vol if is_buy else market_data.bid_vol) or [])
    total = 0.0
    for raw_price, raw_volume in zip(prices[:5], volumes[:5]):
      level_price = float(raw_price or 0.0)
      level_volume = max(0.0, float(raw_volume or 0.0))
      if level_price <= 0 or level_volume <= 0:
        continue
      if request.price_type == PriceType.MARKET:
        executable = True
      elif is_buy:
        executable = level_price <= float(request.price or 0.0)
      else:
        executable = level_price >= float(request.price or 0.0)
      if executable:
        total += level_volume
    return max(0, int(total))

  def _build_strict_book(
    self,
    market_data: Optional[MarketDataSnapshot],
  ) -> Optional[Dict[str, List[List[float]]]]:
    if market_data is None:
      return None
    book: Dict[str, List[List[float]]] = {}
    for side, raw_prices, raw_volumes in (
      ("ask", market_data.ask_price, market_data.ask_vol),
      ("bid", market_data.bid_price, market_data.bid_vol),
    ):
      prices = list(raw_prices or [])
      volumes = list(raw_volumes or [])
      if len(prices) < 5 or len(volumes) < 5:
        book[side] = []
        continue
      book[side] = [
        [
          float(level_price or 0.0),
          float(
            max(0, int(float(level_volume or 0.0) * self.book_depth_participation_pct))
          ),
        ]
        for level_price, level_volume in zip(prices[:5], volumes[:5])
      ]
    return book

  def _consume_strict_book(
    self,
    remaining_volume: int,
    market_data: Optional[MarketDataSnapshot],
    request: OrderRequest,
    book: Optional[Dict[str, List[List[float]]]],
    *,
    order: OrderResponse,
  ) -> tuple[int, float]:
    """Consume this quote's shared visible five-level liquidity."""

    remaining = max(0, int(remaining_volume or 0))
    if remaining <= 0 or market_data is None or book is None:
      self.constraint_statistics["missing_book_depth_blocked"] += 1
      return 0, 0.0
    is_buy = request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]
    side = "ask" if is_buy else "bid"
    levels = book.get(side) or []
    if len(levels) != 5:
      self.constraint_statistics["missing_book_depth_blocked"] += 1
      return 0, 0.0

    requested = remaining
    filled = 0
    fill_amount = 0.0
    cash_before = float(self.cash)
    available_sell = 0
    if not is_buy:
      position = self.positions.get(request.instrument_code)
      available_sell = max(0, int(position.available_volume if position else 0))

    for level in levels:
      if filled >= requested:
        break
      level_price = float(level[0] or 0.0)
      available = max(0, int(level[1] or 0))
      if level_price <= 0 or available <= 0:
        continue
      if request.price_type == PriceType.MARKET:
        executable = True
      elif is_buy:
        executable = level_price <= float(request.price or 0.0)
      else:
        executable = level_price >= float(request.price or 0.0)
      if not executable:
        continue

      take = min(requested - filled, available)
      if not is_buy:
        take = min(take, max(0, available_sell - filled))
      else:
        take = self._affordable_level_volume(
          order,
          level_price=level_price,
          requested_volume=take,
          prior_fill_amount=fill_amount,
          available_cash=cash_before,
        )
        if take <= 0:
          self.constraint_statistics["cash_capped_orders"] += 1
          break
      level[1] = float(available - take)
      filled += take
      fill_amount += take * level_price

    if filled < requested:
      self.constraint_statistics["book_depth_capped_orders"] += 1
    if filled <= 0:
      if self._visible_executable_volume(request, market_data) <= 0:
        self.constraint_statistics["no_queue_credit_blocked"] += 1
      return 0, 0.0
    return filled, fill_amount / filled

  def _affordable_level_volume(
    self,
    order: OrderResponse,
    *,
    level_price: float,
    requested_volume: int,
    prior_fill_amount: float,
    available_cash: float,
  ) -> int:
    """Return the largest volume that cannot drive shared cash negative."""

    previous_costs = self._calculate_costs(
      order.filled_amount, order.request.order_type
    )["total"]

    def required(volume: int) -> float:
      additional = prior_fill_amount + volume * level_price
      cumulative_costs = self._calculate_costs(
        order.filled_amount + additional,
        order.request.order_type,
      )["total"]
      return additional + max(0.0, cumulative_costs - previous_costs)

    low, high = 0, max(0, int(requested_volume))
    while low < high:
      middle = (low + high + 1) // 2
      if required(middle) <= available_cash + 1e-9:
        low = middle
      else:
        high = middle - 1
    return low

  def _determine_fill_volume(
    self,
    remaining_volume: int,
    market_data: Optional[MarketDataSnapshot],
    request: Optional[OrderRequest] = None,
  ) -> int:
    remaining = int(remaining_volume or 0)
    if remaining <= 0:
      return 0
    if market_data and request:
      if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
        prices = list(market_data.ask_price or [])
        volumes = list(market_data.ask_vol or [])
        executable = sum(
          max(0.0, float(volume or 0.0))
          for price, volume in zip(prices, volumes)
          if float(price or 0.0) > 0
          and float(price or 0.0) <= float(request.price or 0.0)
        )
      else:
        prices = list(market_data.bid_price or [])
        volumes = list(market_data.bid_vol or [])
        executable = sum(
          max(0.0, float(volume or 0.0))
          for price, volume in zip(prices, volumes)
          if float(price or 0.0) >= float(request.price or 0.0)
        )
      if prices and volumes:
        available = int(executable * self.book_depth_participation_pct)
        if available < remaining:
          self.constraint_statistics["book_depth_capped_orders"] += 1
        return max(0, min(remaining, available))
    if not market_data or not market_data.volume or market_data.volume <= 0:
      return remaining
    available = int(max(0, market_data.volume * self.participation_cap_pct))
    if available <= 0:
      return 0
    return max(0, min(remaining, available))

  async def _expire_pending_orders(self) -> None:
    """Expire short-lived simulated orders before evaluating the next quote."""

    if not self.current_time:
      return
    now_ms = int(self.current_time.timestamp() * 1000)
    expired: List[OrderResponse] = []
    for order in list(self.pending_orders):
      raw_expiry = (order.request.metadata or {}).get("order_expire_at_ms")
      try:
        expire_at_ms = int(raw_expiry or 0)
      except (TypeError, ValueError):
        expire_at_ms = 0
      if expire_at_ms <= 0 or now_ms < expire_at_ms:
        continue
      order.status = OrderStatus.EXPIRED
      order.last_update_time = self.current_time
      order.error_message = "订单超过策略有效期，已按回测规则过期"
      self.constraint_statistics["expired_orders"] += 1
      expired.append(order)
      await self.emit_order_update(order)
    if expired:
      expired_ids = {order.order_id for order in expired}
      self.pending_orders = [
        order for order in self.pending_orders if order.order_id not in expired_ids
      ]

  async def _update_equity_curve(self) -> None:
    """更新权益曲线"""
    account = await self.get_account()
    equity = account.total_asset

    self.equity_curve.append(equity)

    # 更新最大回撤
    if equity > self.peak_equity:
      self.peak_equity = equity

    drawdown = (self.peak_equity - equity) / self.peak_equity
    self.max_drawdown = max(self.max_drawdown, drawdown)

    # 计算日收益率
    if len(self.equity_curve) > 1:
      daily_return = (equity - self.equity_curve[-2]) / self.equity_curve[-2]
      self.daily_returns.append(daily_return)

  def _record_replay_curve(self) -> None:
    if not self.current_time:
      return
    for code in self.passive_volumes:
      if code in self.current_prices:
        self.passive_prices[code] = self.current_prices[code]
    equity = self.cash + sum(
      position.market_value for position in self.positions.values()
    ) + self.non_trading_asset_value
    passive_equity = self.passive_cash + self.non_trading_asset_value + sum(
      volume * self.passive_prices.get(code, 0.0)
      for code, volume in self.passive_volumes.items()
    )
    point = {
      "timestamp": self.current_time,
      "equity": equity,
      "passive_equity": passive_equity,
    }
    minute_key = self.current_time.replace(second=0, microsecond=0)
    if self.replay_curve:
      previous_time = self.replay_curve[-1].get("timestamp")
      if previous_time and previous_time.replace(second=0, microsecond=0) == minute_key:
        self.replay_curve[-1] = point
        return
    self.replay_curve.append(point)

  def _update_ghost_dca(self, price: float) -> None:
    if price <= 0:
      return
    budget = self.initial_capital * 0.001
    stats = self.constraint_statistics
    if stats["ghost_dca_invested"] + budget > self.initial_capital:
      stats["ghost_dca_last_price"] = price
      return
    stats["ghost_dca_invested"] += budget
    stats["ghost_dca_units"] += budget / price
    stats["ghost_dca_last_price"] = price

  def get_constraint_statistics(self) -> Dict[str, Any]:
    stats = dict(self.constraint_statistics)
    invested = float(stats.get("ghost_dca_invested", 0.0) or 0.0)
    units = float(stats.get("ghost_dca_units", 0.0) or 0.0)
    last_price = float(stats.get("ghost_dca_last_price", 0.0) or 0.0)
    ghost_value = units * last_price
    ghost_return = (ghost_value - invested) / invested if invested > 0 else 0.0
    stats["ghost_dca_value"] = ghost_value
    stats["ghost_dca_return"] = ghost_return
    return stats

  def get_performance_metrics(self) -> Dict[str, Any]:
    """获取绩效指标"""
    if not self.equity_curve:
      return {}

    final_equity = self.equity_curve[-1]
    total_return = (final_equity - self.initial_capital) / self.initial_capital

    # 计算夏普比率
    sharpe_ratio = 0.0
    if self.daily_returns:
      returns_array = np.array(self.daily_returns)
      if returns_array.std() > 0:
        sharpe_ratio = (returns_array.mean() / returns_array.std()) * np.sqrt(252)

    # 计算胜率
    win_rate = 0.0
    if self.total_trades > 0:
      win_rate = self.winning_trades / self.total_trades

    return {
      "initial_capital": self.initial_capital,
      "final_equity": final_equity,
      "total_return": total_return,
      "total_return_pct": total_return * 100,
      "max_drawdown": self.max_drawdown,
      "max_drawdown_pct": self.max_drawdown * 100,
      "sharpe_ratio": sharpe_ratio,
      "total_trades": self.total_trades,
      "winning_trades": self.winning_trades,
      "losing_trades": self.losing_trades,
      "win_rate": win_rate,
      "win_rate_pct": win_rate * 100,
      "equity_curve_length": len(self.equity_curve),
      "constraint_statistics": self.get_constraint_statistics(),
    }
