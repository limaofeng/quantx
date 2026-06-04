"""
交易服务
处理交易相关的业务逻辑，包括下单、撤单、持仓管理等核心交易功能
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List

from database.connection import get_async_db
from miniqmt import XTDataManagerRegistry, XTTradingManagerRegistry
from miniqmt.trading.trading_manager import OrderType
from models.account import Account
from models.enums import AccountType, InstrumentType, OrderStatus, PriceType
from models.instrument import Instrument
from models.order import Order
from models.position import Position
from repositories.account_repository import AccountRepository
from services.historical_market_data_service import HistoricalMarketDataService
from services.instrument_service import InstrumentService
from services.order_service import OrderService
from services.position_service import PositionService
from core.utils import time_utils

logger = logging.getLogger(__name__)
DEFAULT_ACCOUNT_ID = "300000013250"


class TradingStatus:
  PENDING = "PENDING"
  PARTIAL_FILLED = "PARTIAL_FILLED"
  FILLED = "FILLED"
  CANCELLED = "CANCELLED"
  REJECTED = "REJECTED"
  EXPIRED = "EXPIRED"


class TradingError(Exception):
  """交易异常基类"""

  pass


class InsufficientBalanceError(TradingError):
  """余额不足异常"""

  pass


class InsufficientPositionError(TradingError):
  """持仓不足异常"""

  pass


class InvalidOrderError(TradingError):
  """无效订单异常"""

  pass


class TradingService:
  """交易服务类 - 提供完整的交易业务功能"""

  def __init__(
    self,
    account_id: str = DEFAULT_ACCOUNT_ID,
    account_type: AccountType = AccountType.STOCK,
  ):
    self.account_id = account_id or DEFAULT_ACCOUNT_ID
    self.account_type = account_type
    self.order_service = OrderService(self.account_id)
    self.position_service = PositionService()
    self.market_data_service = HistoricalMarketDataService()
    self.instrument_service = InstrumentService()
    data_manager_registry = XTDataManagerRegistry()
    trading_registry = XTTradingManagerRegistry()
    self.trading_manager = trading_registry.get_manager(self.account_id)
    self.data_manager = data_manager_registry.get_manager()

    # 注入 TradingService 到 TradingManager (用于回调处理)
    self.trading_manager.trading_service = self

  async def get_account_info(self, realtime: bool = False) -> Account:
    """
    获取账户信息
    Args:
        realtime: 是否实时获取账户信息，默认为False（可从缓存获取）

    Returns:
        Account: 账户信息
    """
    async for db in get_async_db():
      account_repo = AccountRepository(db)
      account = await account_repo.find_by_account_id(
        self.account_id, self.account_type
      )
      if not account:
        raise TradingError("未找到账户信息")

      if realtime:
        latest_account = self.trading_manager.get_account_info()
        account.total_asset = latest_account.get("total_asset", account.total_asset)
        account.cash = latest_account.get("cash", account.cash)
        account.market_value = latest_account.get("market_value", account.market_value)
        account.frozen_cash = latest_account.get("frozen_cash", account.frozen_cash)
        # 保存最新账户信息
        await account_repo.save(account)

    return account

  async def place_order(
    self,
    stock_code: str,
    order_type: OrderType = OrderType.BUY,
    order_volume: int = 100,
    price_type: PriceType = PriceType.MARKET_CONVERT_5_LIMIT,
    price: float = 0,
    strategy_name: str = "",
    order_remark: str = "",
    close_position: bool = False,
  ) -> str:
    """
    下单 - 核心交易功能

    Args:
        stock_code: 股票代码
        order_type: 订单类型 (BUY/SELL)
        order_volume: 订单数量
        price_type: 价格类型
        price: 价格

    Returns:
        Dict: 下单结果
    """

    try:
      # 1. 获取账户信息
      account = await self.get_account_info(realtime=True)

      # 2. 获取股票信息
      stock_info = await self._get_stock_info(stock_code)

      # 3. 参数验证
      await self._validate_order_request(
        order_type,
        order_volume,
        price_type,
        price,
        stock_info,
        account,
        close_position=close_position,
      )

      # 4. 风险检查
      await self._risk_check(order_volume, price, stock_info, price_type)

      # 5. 资金/持仓检查
      await self._check_trading_capacity(
        order_type, order_volume, price, stock_info, account
      )

      # 6. 调用 MiniQMT 下单接口立即执行（如果是市价单或条件满足）
      order_id = await self._try_execute_order(
        stock_code=stock_code,
        order_type=order_type,
        order_volume=order_volume,
        price_type=price_type,
        price=price,
        strategy_name=strategy_name,
        order_remark=order_remark,
      )

      return {
        "success": order_id != -1,
        "order_id": order_id,
        "message": "订单提交成功",
      }

    except Exception as e:
      logger.error(f"下单失败 - 错误: {str(e)}")
      return {"success": False, "error": str(e), "message": "下单失败"}

  async def check_order_status(
    self, order_id: int, wait_time: int = 5
  ) -> Dict[str, Any]:
    """
    检查订单状态

    Args:
        order_id: 订单ID
        wait_time: 等待时间（秒）

    Returns:
        Dict: 订单状态
    """
    try:
      self.trading_manager.wait_for_order_completion(order_id, timeout=wait_time)

      order = self.trading_manager.get_order(order_id)
      if not order:
        raise InvalidOrderError(f"订单 {order_id} 不存在")

      return {
        "success": True,
        "order_id": order.order_id,
        "status": OrderStatus(order.order_status),
        "filled_rate": order.traded_price,
        "traded_volume": order.traded_volume,
        "remaining_volume": order.order_volume - order.traded_volume,
        "message": "订单状态获取成功",
      }

    except Exception as e:
      import traceback

      traceback.print_exc()
      logger.error(f"获取订单状态失败 - 订单: {order_id}, 错误: {str(e)}")
      return {"success": False, "error": str(e), "message": "获取订单状态失败"}

  async def cancel_order(self, user_id: str, order_id: int) -> Dict[str, Any]:
    """
    撤单

    Args:
        user_id: 用户ID
        order_id: 订单ID

    Returns:
        Dict: 撤单结果
    """
    try:
      # 验证订单归属
      order = await self.trading_manager.get_order(order_id)
      if not order:
        raise InvalidOrderError(f"订单 {order_id} 不存在")

      # 检查订单状态
      if order.status in [
        OrderStatus.CANCELED,
        OrderStatus.SUCCEEDED,
        OrderStatus.JUNK,
        OrderStatus.UNKNOWN,
      ]:
        raise InvalidOrderError(f"订单状态 {order.status} 不允许撤单")

      # 执行撤单
      cancelled = await self.trading_manager.cancel_order(order_id)

      if not cancelled:
        raise InvalidOrderError(f"订单 {order_id} 撤单失败")

      return {"success": True, "order_id": order_id, "message": "撤单成功"}

    except Exception as e:
      logger.error(f"撤单失败 - 用户: {user_id}, 订单: {order_id}, 错误: {str(e)}")
      return {"success": False, "error": str(e), "message": "撤单失败"}

  async def get_trading_summary(self, user_id: str) -> Dict[str, Any]:
    """
    获取交易汇总信息

    Args:
        user_id: 用户ID

    Returns:
        Dict: 交易汇总
    """
    try:
      # 获取持仓信息
      positions = await self.position_service.get_positions()

      # 获取今日订单
      today_orders = await self._get_today_orders(user_id)

      # 计算持仓市值
      total_market_value = await self._calculate_total_market_value(positions)

      # 计算今日盈亏
      today_pnl = await self._calculate_today_pnl(positions, today_orders)

      # 统计订单状态
      order_stats = self._calculate_order_statistics(today_orders)

      return {
        "user_id": user_id,
        "total_market_value": total_market_value,
        "today_pnl": today_pnl,
        "positions_count": len(positions),
        "today_orders_count": len(today_orders),
        "order_statistics": order_stats,
        "positions": [
          pos.to_dict() if hasattr(pos, "to_dict") else pos for pos in positions
        ],
        "recent_orders": [order.to_dict() for order in today_orders[:10]],
      }

    except Exception as e:
      logger.error(f"获取交易汇总失败 - 用户: {user_id}, 错误: {str(e)}")
      raise

  async def execute_strategy_orders(
    self, strategy_id: str, orders: List[Dict[str, Any]]
  ) -> Dict[str, Any]:
    """
    批量执行策略订单

    Args:
        strategy_id: 策略ID
        orders: 订单列表

    Returns:
        Dict: 执行结果
    """
    results = []
    success_count = 0

    for order_data in orders:
      order_data["strategy_id"] = strategy_id
      result = await self.place_order(
        stock_code=order_data["stock_code"],
        order_type=order_data["order_type"],
        order_volume=order_data["quantity"],
        price_type=order_data.get("price_type", PriceType.LIMIT),
        price=order_data.get("price", 0),
      )
      results.append(result)
      if result["success"]:
        success_count += 1

    return {
      "strategy_id": strategy_id,
      "total_orders": len(orders),
      "success_count": success_count,
      "failed_count": len(orders) - success_count,
      "results": results,
    }

  # 私有方法 - 业务逻辑实现
  async def _validate_order_request(
    self,
    order_type: OrderType,
    order_volume: float,
    price_type: PriceType,
    price: float,
    stock_info: Instrument,
    account: Account,
    close_position: bool = False,
  ) -> None:
    """验证订单请求参数"""
    if order_type not in [OrderType.BUY, OrderType.SELL]:
      raise InvalidOrderError("订单类型必须是 BUY 或 SELL")

    # 使用配置验证数量
    if not self._validate_order_volume(
      order_volume,
      stock_info,
      order_type=order_type,
      close_position=close_position,
    ):
      raise InvalidOrderError(f"订单数量不符合规则: {order_volume}")

    if price_type == PriceType.FIX_PRICE and price <= 0:
      raise InvalidOrderError("固定价格订单必须指定有效价格")

  async def _get_stock_info(self, stock_code: str) -> Dict[str, Any]:
    """获取股票信息"""
    stock = await self.instrument_service.find_by_id(stock_code)
    if not stock:
      raise InvalidOrderError(f"股票代码 {stock_code} 不存在")
    return stock

  async def _risk_check(
    self,
    order_volume: int,
    price: float,
    stock_info: Instrument,
    price_type: PriceType,
  ) -> None:
    """风险检查"""
    # 检查交易时间
    if not await self._is_trading_time(stock_info):
      raise TradingError("当前不在交易时间")

    if price_type != PriceType.FIX_PRICE:
      return

    # 检查限价单价格限制；市价类委托可传 price=0。
    up_stop_price = stock_info.up_stop_price
    down_stop_price = stock_info.down_stop_price
    order_price = Decimal(str(price))

    if order_price > up_stop_price:
      raise TradingError(f"订单价格超过涨停价: {order_price} > {up_stop_price}")
    if order_price < down_stop_price:
      raise TradingError(f"订单价格低于跌停价: {order_price} < {down_stop_price}")

  async def _check_trading_capacity(
    self,
    order_type: OrderType,
    order_volume: int,
    price: float,
    stock_info: Instrument,
    account: Account,
  ) -> None:
    """检查交易能力（资金/持仓）"""
    if order_type == OrderType.BUY:
      # 买入检查资金
      required_amount = order_volume * price
      # 计算手续费
      commission = self._calculate_commission(
        Decimal(str(required_amount)), OrderType.BUY
      )
      total_required = required_amount + float(commission)

      available_balance = account.cash  # 直接使用账户现金
      if available_balance < total_required:
        raise InsufficientBalanceError(
          f"可用资金不足: {available_balance} < {total_required} (含手续费)"
        )

    elif order_type == OrderType.SELL and stock_info.type != InstrumentType.TRR:
      # 卖出检查持仓
      available_volume = await self._get_available_position(stock_info.id)
      if available_volume < order_volume:
        raise InsufficientPositionError(
          f"可用持仓不足: {available_volume} < {order_volume}"
        )

  async def _create_trading_order(
    self, stock_code: str, order_type: OrderType, order_volume: int, price: float
  ) -> Order:
    """创建交易订单"""
    order_input = {
      "stock_code": stock_code,
      "order_type": order_type,
      "quantity": order_volume,
      "price": price,
    }

    return await self.order_service.create_order(order_input)

  async def _try_execute_order(
    self,
    stock_code: str,
    order_type: OrderType = OrderType.BUY,
    order_volume: int = 100,
    price_type: PriceType = PriceType.MARKET_CONVERT_5_LIMIT,
    price: float = 0,
    strategy_name: str = "",
    order_remark: str = "",
  ) -> int:
    """尝试执行订单"""
    result = self.trading_manager.place_order(
      stock_code=stock_code,
      order_type=order_type,
      order_volume=order_volume,
      price_type=price_type,
      price=price,
      strategy_name=strategy_name,
      order_remark=order_remark,
    )
    return result.get("order_id", -1)

  async def _get_user_order(self, user_id: str, order_id: int) -> Order:
    """获取用户订单"""
    order = await self.order_service.get_order_by_id(order_id)
    if not order:
      raise InvalidOrderError(f"订单 {order_id} 不存在")
    if order.user_id != user_id:
      raise InvalidOrderError("无权限操作此订单")
    return order

  async def _get_today_orders(self, user_id: str) -> List[Order]:
    """获取今日订单"""
    today = time_utils.now().date()
    orders = await self.order_service.get_orders(user_id)
    return [order for order in orders if order.created_at.date() == today]

  async def _calculate_total_market_value(self, positions: List[Position]) -> float:
    """计算总市值"""
    total_value = 0.0
    for position in positions:
      if hasattr(position, "stock_code") and hasattr(position, "quantity"):
        current_price = await self.market_data_service.get_current_price(
          position.stock_code
        )
        total_value += position.quantity * current_price
    return total_value

  async def _calculate_today_pnl(
    self, positions: List[Position], orders: List[Order]
  ) -> float:
    """计算今日盈亏"""
    # 简化的盈亏计算
    return 0.0

  def _calculate_order_statistics(self, orders: List[Order]) -> Dict[str, int]:
    """计算订单统计"""
    stats = {"pending": 0, "completed": 0, "cancelled": 0, "partial_filled": 0}
    for order in orders:
      status = order.status.lower()
      if status in stats:
        stats[status] += 1
    return stats

  async def _is_trading_time(self, stock_info: Instrument) -> bool:
    """检查是否在交易时间"""
    now = time_utils.now()
    return self._is_in_trading_hours(now.hour, now.minute, stock_info)

  async def _get_available_balance(self) -> float:
    """获取可用资金"""
    # 直接返回账户现金
    account = await self.get_account_info(realtime=False)
    return account.cash

  async def _get_available_position(self, stock_code: str) -> int:
    """获取可用持仓数量"""
    position = self.trading_manager.get_position(stock_code)
    if not position:
      return 0
    return position.can_use_volume

  # 内部工具方法
  def _validate_order_volume(
    self,
    volume: int,
    stock_info: Instrument,
    *,
    order_type: OrderType = None,
    close_position: bool = False,
  ) -> bool:
    """验证订单数量"""
    min_order_volume = stock_info.min_market_order_volume
    max_order_volume = stock_info.max_market_order_volume
    lot_size = 100

    if stock_info.min_market_order_volume == 1:
      min_order_volume = 10

    if stock_info.type == InstrumentType.TRR:
      lot_size = 10  # 国债逆回购最小交易单位是10

    if order_type == OrderType.SELL and close_position:
      return 0 < volume <= max_order_volume

    return min_order_volume <= volume <= max_order_volume and volume % lot_size == 0

  def _calculate_commission(self, amount: Decimal, order_type: str) -> Decimal:
    """计算手续费"""
    # 佣金
    commission = max(amount * self.config.COMMISSION_RATE, self.config.MIN_COMMISSION)

    # 印花税 (仅卖出收取)
    stamp_tax = (
      amount * self.config.STAMP_TAX_RATE
      if order_type == OrderType.SELL
      else Decimal("0")
    )

    # 过户费
    transfer_fee = amount * self.config.TRANSFER_FEE_RATE

    total_fee = commission + stamp_tax + transfer_fee
    return total_fee

  def _is_in_trading_hours(
    self, hour: int, minute: int, stock_info: Instrument
  ) -> bool:
    """检查是否在交易时间"""
    # 上午交易时间 9:30-11:30
    morning_start = 9 * 60 + 30
    morning_end = 11 * 60 + 30

    # 下午交易时间 13:00-15:00
    afternoon_start = 13 * 60
    afternoon_end = 15 * 60

    current_minutes = hour * 60 + minute

    if stock_info.type == InstrumentType.TRR:
      # 国债逆回购交易时间
      afternoon_end = 15 * 60 + 30  # 国债逆回购下午交易到15:30

    return (morning_start <= current_minutes <= morning_end) or (
      afternoon_start <= current_minutes <= afternoon_end
    )

  # ==================== 回调处理方法 (由 TradingManager 调用) ====================

  async def handle_asset_update(self, asset):
    """
    处理资产变动回调

    Args:
      asset: XtAsset 对象
    """
    try:
      from database.connection import get_async_db
      from repositories.account_repository import AccountRepository

      async for db in get_async_db():
        account_repo = AccountRepository(db)
        account = await account_repo.find_by_account_id(
          self.account_id, self.account_type
        )
        if account:
          # 更新账户资产信息
          account.total_asset = asset.total_asset
          account.cash = asset.cash
          account.market_value = asset.market_value
          account.frozen_cash = asset.frozen_cash
          await account_repo.save(account)
          logger.info(f"资产已同步到数据库 - 总资产: {asset.total_asset}")
    except Exception as e:
      logger.error(f"同步资产到数据库失败: {e}")

  async def handle_position_update(self, position):
    """
    处理持仓变动回调

    Args:
      position: XtPosition 对象
    """
    try:
      from database.connection import get_async_db
      from repositories.position_repository import PositionRepository

      async for db in get_async_db():
        position_repo = PositionRepository(db)
        # 查找或创建持仓记录
        existing_position = await position_repo.find_by_stock_code(
          position.stock_code,
          account_id=self.account_id,
          account_type=self.account_type,
        )

        if existing_position:
          # 更新持仓信息
          existing_position.volume = position.volume
          existing_position.can_use_volume = position.can_use_volume
          existing_position.open_price = position.open_price
          existing_position.market_value = position.market_value
          await position_repo.save(existing_position)
          logger.info(f"持仓已更新 - {position.stock_code}: 数量={position.volume}")
        else:
          # 创建新持仓记录
          # TODO: 需要根据实际的 Position 模型结构创建
          logger.info(f"检测到新持仓 - {position.stock_code}")
    except Exception as e:
      logger.error(f"同步持仓到数据库失败: {e}")

  async def handle_order_callback(self, order):
    """
    处理委托回报回调

    Args:
      order: XtOrder 对象
    """
    try:
      from core.events import TradingEventType, trading_event_manager
      from core.events.types import OrderEvent
      from database.connection import get_async_db
      from repositories.order_repository import OrderRepository

      # 1. 同步订单状态到数据库
      async for db in get_async_db():
        order_repo = OrderRepository(db)
        existing_order = await order_repo.find_by_order_id(order.order_id)

        if existing_order:
          # 更新订单状态
          existing_order.order_status = OrderStatus(order.order_status)
          existing_order.traded_volume = order.traded_volume
          existing_order.traded_price = order.traded_price
          await order_repo.save(existing_order)

          # 2. 发布订单事件
          event = OrderEvent(
            event_type=TradingEventType.ORDER_CREATED,
            order=existing_order,
            timestamp=time_utils.now(),
            changes=f"订单状态变更为: {order.order_status}",
          )
          await trading_event_manager.publish(TradingEventType.ORDER_CREATED, event)
          logger.info(f"订单事件已发布 - 订单ID: {order.order_id}")
    except Exception as e:
      logger.error(f"处理委托回报失败: {e}")

  async def handle_trade_callback(self, trade):
    """
    处理成交回报回调

    Args:
      trade: XtTrade 对象
    """
    try:
      from core.events import TradingEventType, trading_event_manager
      from core.events.types import OrderEvent
      from database.connection import get_async_db
      from repositories.order_repository import OrderRepository

      # 1. 更新订单成交信息
      async for db in get_async_db():
        order_repo = OrderRepository(db)
        order = await order_repo.find_by_order_id(trade.order_id)

        if order:
          # 更新成交信息
          order.traded_volume = trade.traded_volume
          order.traded_price = trade.traded_price
          order.order_status = OrderStatus(trade.order_status)
          await order_repo.save(order)

          # 2. 发布成交事件
          event = OrderEvent(
            event_type=TradingEventType.ORDER_FILLED,
            order=order,
            timestamp=time_utils.now(),
            changes=f"订单成交 - 成交价: {trade.traded_price}, 成交量: {trade.traded_volume}",
          )
          await trading_event_manager.publish(TradingEventType.ORDER_FILLED, event)
          logger.info(
            f"成交事件已发布 - 订单ID: {trade.order_id}, 成交量: {trade.traded_volume}"
          )
    except Exception as e:
      logger.error(f"处理成交回报失败: {e}")

  async def handle_order_error_callback(self, order_error):
    """
    处理委托失败回调

    Args:
      order_error: XtOrderError 对象
    """
    try:
      from core.events import TradingEventType, trading_event_manager
      from core.events.types import OrderEvent
      from database.connection import get_async_db
      from repositories.order_repository import OrderRepository

      # 1. 更新订单状态为失败
      async for db in get_async_db():
        order_repo = OrderRepository(db)
        order = await order_repo.find_by_order_id(order_error.order_id)

        if order:
          order.order_status = OrderStatus.JUNK
          order.status_msg = order_error.error_msg
          await order_repo.save(order)

          # 2. 发布订单拒绝事件
          event = OrderEvent(
            event_type=TradingEventType.ORDER_REJECTED,
            order=order,
            timestamp=time_utils.now(),
            changes=f"订单被拒绝 - {order_error.error_msg}",
          )
          await trading_event_manager.publish(TradingEventType.ORDER_REJECTED, event)
          logger.error(
            f"订单拒绝事件已发布 - 订单ID: {order_error.order_id}, "
            f"错误: {order_error.error_msg}"
          )
    except Exception as e:
      logger.error(f"处理委托失败回调失败: {e}")
