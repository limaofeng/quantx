"""
XTQuant 交易接口封装
提供统一的交易下单接口
"""

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from xtquant import xtconstant
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount, XtAsset, XtOrder, XtPosition, XtTrade

from models.enums import AccountType, OrderPriceType, OrderStatus, OrderType, PriceType
from core.utils import time_utils

logger = logging.getLogger(__name__)

path = r"F:\长城策略交易系统\userdata_mini"


class TradingConnectionError(Exception):
  """交易连接异常"""

  pass


class InvalidOrderError(Exception):
  """无效订单异常"""

  pass


class XTTradingManager:
  """XTQuant 交易管理器"""

  def __init__(self, account_id: str, account_type: AccountType = AccountType.STOCK):
    self.account_id = account_id
    self.is_connected = False
    self.session_id = None
    self.account_type = account_type
    # 初始化事件循环 (用于处理异步回调)
    self.event_loop = None
    self.event_loop_thread = None
    self._init_event_loop()
    self._init_connection()

  def _init_event_loop(self):
    """初始化事件循环 (在独立线程中运行)"""
    try:
      import asyncio
      import threading

      def run_event_loop(loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

      self.event_loop = asyncio.new_event_loop()
      self.event_loop_thread = threading.Thread(
        target=run_event_loop,
        args=(self.event_loop,),
        daemon=True,
        name="XTTradingEventLoop",
      )
      self.event_loop_thread.start()
      logger.info("事件循环初始化成功")
    except Exception as e:
      logger.error(f"初始化事件循环失败: {e}")
      self.event_loop = None

  def _init_connection(self):
    """初始化交易连接"""
    try:
      # 连接到XTQuant交易服务
      session_id = int(time.time())
      self.session_id = session_id
      self.xttrader = XtQuantTrader(path, session_id)
      self.acc = StockAccount(self.account_id, self.account_type.value)
      # 创建交易回调类对象，并声明接收回调
      callback = MiniQMTTraderCallback(self)
      self.xttrader.register_callback(callback)
      # 启动交易线程
      self.xttrader.start()
      # 建立交易连接，返回0表示连接成功
      connect_result = self.xttrader.connect()
      if connect_result == 0:
        self.is_connected = True
        logger.info(f"XTQuant交易连接成功, session_id: {session_id}")
      else:
        logger.error("XTQuant交易连接失败")
        self.is_connected = False
    except Exception as e:
      logger.error(f"XTQuant交易连接失败: {e}")
      # 打印堆栈信息
      import traceback

      traceback.print_exc()
      self.is_connected = False

  def query_new_purchase_limit(self) -> Dict[str, Any]:
    """
    查询新股申购额度
    """
    return self.xttrader.query_new_purchase_limit(self.acc)

  def place_order(
    self,
    stock_code: str,
    order_type: OrderType,
    order_volume: int,
    price_type: PriceType = PriceType.LATEST_PRICE,
    price: float = 0,
    strategy_name: str = "",
    order_remark: str = "",
  ) -> Dict[str, Any]:
    """
    下单

    Args:
        stock_code: 股票代码
        order_type: 订单类型（买入/卖出）
        quantity: 数量
        price: 价格（市价单可传0）
        price_type: 价格类型

    Returns:
        Dict: 下单结果
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      inside_price_type = price_type.value
      if price_type == PriceType.MARKET_CONVERT_5_LIMIT:
        if stock_code.endswith(".SH"):
          inside_price_type = xtconstant.MARKET_SH_CONVERT_5_CANCEL
        elif stock_code.endswith(".SZ"):
          inside_price_type = xtconstant.MARKET_SZ_CONVERT_5_CANCEL

      order_id = self.xttrader.order_stock(
        account=self.acc,
        stock_code=stock_code,
        order_type=order_type.value,
        order_volume=order_volume,
        price_type=inside_price_type,
        price=price if price_type == PriceType.FIX_PRICE else 0.0,
        strategy_name=strategy_name,
        order_remark=order_remark,
      )

      if order_id > 0:
        result = {
          "success": True,
          "order_id": order_id,
          "message": "下单成功",
        }
        logger.info(f"下单成功: {result}")
        return result
      else:
        return {"success": False, "message": f"下单失败, order_id: {order_id}"}

    except Exception as e:
      logger.error(f"下单失败: {e}")
      return {"success": False, "message": f"下单异常: {str(e)}"}

  def wait_for_order_completion(
    self,
    order_id: int,
    timeout: int = 30,
    wait_statuses: List[OrderStatus] = [
      OrderStatus.SUCCEEDED,
      OrderStatus.PART_SUCC,
      OrderStatus.CANCELED,
      OrderStatus.JUNK,
    ],
  ) -> OrderStatus:
    """
    等待订单完成（成交或取消）

    Args:
        order_id: 订单ID
        timeout: 超时时间（秒）

    Returns:
        bool: 是否完成
    """
    start_time = time.time()
    latest_status = OrderStatus.UNKNOWN
    while time.time() - start_time < timeout:
      order: XtOrder = self.get_order(order_id)

      if order is None:
        raise InvalidOrderError(f"订单 {order_id} 不存在")

      latest_status = OrderStatus(order.order_status)
      if latest_status in wait_statuses:
        logger.info(f"订单 {order_id} 已完成，状态: {latest_status.name}")
        return latest_status
      logger.info(f"订单 {order_id} 状态: {latest_status.name}, 等待中...")
      time.sleep(1)

    logger.warning(f"等待订单 {order_id} 完成超时, 当前状态: {latest_status.name}")
    raise TimeoutError(f"等待订单 {order_id} 完成超时, 当前状态: {latest_status.name}")

  def cancel_order(self, order_id: int) -> bool:
    """
    撤单

    Args:
        order_id: 订单ID

    Returns:
        Dict: 撤单结果
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      result = self.xttrader.cancel_order_stock(self.acc, order_id)
      return result == 0

    except Exception as e:
      logger.error(f"撤单失败: {e}")
      return False

  def get_positions(self) -> List[XtPosition]:
    """
    获取持仓信息

    Returns:
        DataFrame: 持仓数据
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      return self.xttrader.query_stock_positions(self.acc)

    except Exception as e:
      logger.error(f"获取持仓失败: {e}")
      return []

  def query_positions_snapshot(self) -> List[XtPosition]:
    """Return one complete broker snapshot or raise; never blur failure into []."""
    if not self.is_connected:
      raise TradingConnectionError("交易连接未建立")
    positions = self.xttrader.query_stock_positions(self.acc)
    if positions is None:
      raise TradingConnectionError("miniQMT 持仓查询未返回完整结果")
    return list(positions)

  def get_position(self, stock_code: str) -> Optional[XtPosition]:
    """
    获取单个持仓信息

    Args:
        stock_code: 股票代码

    Returns:
        Optional[XtPosition]: 持仓信息
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      position = self.xttrader.query_stock_position(self.acc, stock_code)
      return position

    except Exception as e:
      logger.error(f"获取持仓失败: {e}")
      return None

  def get_orders(self, cancelable_only=False) -> List[XtOrder]:
    """
    获取订单信息

    Returns:
        DataFrame: 订单数据
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      return self.xttrader.query_stock_orders(self.acc, cancelable_only)

    except Exception as e:
      logger.error(f"获取订单失败: {e}")
      raise e

  def get_order(self, order_id: int) -> Optional[XtOrder]:
    """
    获取单个订单信息

    Args:
        order_id: 订单ID

    Returns:
        Optional[XtOrder]: 订单信息
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      return self.xttrader.query_stock_order(self.acc, order_id)

    except Exception as e:
      logger.error(f"获取订单失败: {e}")
      raise e

  def download_history_orders(
    self, start_date: str = None, end_date: str = None, file_path: str = None
  ) -> bool:
    """
    下载历史订单信息到本地文件

    Args:
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        file_path: 保存文件路径

    Returns:
        bool: 是否下载成功
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      if file_path is None:
        file_path = (
          rf"F:\Workspace\quantx\backend\logs\history_orders_{self.account_id}.csv"
        )

      result = self.xttrader.export_data(
        self.acc, file_path, "orders", "20250601", None, {}
      )

      print(result)

      if result.get("code") == 0:
        logger.info(f"历史订单下载成功, 文件路径: {file_path}")
        return True
      else:
        logger.error(f"历史订单下载失败, 错误信息: {result.get('message')}")
        return False

    except Exception as e:
      logger.error(f"下载历史订单失败: {e}")
      return False

  def get_history_orders(self, start_date: str, end_date: str) -> pd.DataFrame:
    """
    获取历史订单信息
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      return self.xttrader.query_data(self.acc, start_date, end_date)

    except Exception as e:
      logger.error(f"获取历史订单失败: {e}")
      return pd.DataFrame()

  def get_trades(self) -> List[XtTrade]:
    """
    获取成交信息

    Returns:
        DataFrame: 成交数据
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      trades = self.xttrader.query_stock_trades(self.acc)
      return trades

    except Exception as e:
      logger.error(f"获取成交失败: {e}")
      raise e

  def get_account_info(self) -> Dict[str, Any]:
    """
    获取账户信息

    Returns:
        Dict: 账户信息
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      stock_asset: XtAsset = self.xttrader.query_stock_asset(self.acc)

      if stock_asset:
        return {
          "account_id": self.account_id,
          "total_asset": stock_asset.total_asset,  # 总资产
          "cash": stock_asset.cash,  # 现金
          "market_value": stock_asset.market_value,  # 市值
          "frozen_cash": stock_asset.frozen_cash,  # 可用资金
        }

      return {}

    except Exception as e:
      logger.error(f"获取账户信息失败: {e}")
      return {}

  def is_account_status_ok(self) -> bool:
    """检查当前交易账户状态是否正常"""
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      statuses = self.xttrader.query_account_status()
      expected_account_id = self.acc.account_id
      expected_account_type = self.acc.account_type

      for account_status in statuses or []:
        if (
          getattr(account_status, "account_id", None) == expected_account_id
          and getattr(account_status, "account_type", None) == expected_account_type
        ):
          return getattr(account_status, "status", None) == xtconstant.ACCOUNT_STATUS_OK

      return False

    except Exception as e:
      logger.error(f"检查账户状态失败: {e}")
      return False

  def buy_stock(
    self, stock_code: str, quantity: int, price: float = 0
  ) -> Dict[str, Any]:
    """
    买入股票（便捷方法）

    Args:
        stock_code: 股票代码
        quantity: 数量
        price: 价格（0表示市价）

    Returns:
        Dict: 下单结果
    """
    price_type = OrderPriceType.MARKET if price == 0 else OrderPriceType.LIMIT
    return self.place_order(stock_code, OrderType.BUY, quantity, price, price_type)

  def sell_stock(
    self, stock_code: str, quantity: int, price: float = 0
  ) -> Dict[str, Any]:
    """
    卖出股票（便捷方法）

    Args:
        stock_code: 股票代码
        quantity: 数量
        price: 价格（0表示市价）

    Returns:
        Dict: 下单结果
    """
    price_type = OrderPriceType.MARKET if price == 0 else OrderPriceType.LIMIT
    return self.place_order(stock_code, OrderType.SELL, quantity, price, price_type)

  def close_connection(self):
    """关闭交易连接"""
    try:
      if getattr(self, "xttrader", None):
        close_method = getattr(self.xttrader, "disconnect", None)
        if not callable(close_method):
          close_method = getattr(self.xttrader, "stop", None)

        if callable(close_method):
          close_method()
        else:
          logger.warning("XTQuant交易对象缺少可用的关闭方法")

        self.is_connected = False
        self.session_id = None
        logger.info("XTQuant交易连接已关闭")

      # 停止事件循环
      if self.event_loop:
        self.event_loop.call_soon_threadsafe(self.event_loop.stop)
        if self.event_loop_thread and self.event_loop_thread.is_alive():
          self.event_loop_thread.join(timeout=2)
        if not self.event_loop.is_running() and not self.event_loop.is_closed():
          self.event_loop.close()
        self.event_loop = None
        self.event_loop_thread = None
        logger.info("事件循环已停止")
    except Exception as e:
      logger.error(f"关闭XTQuant交易连接失败: {e}")

  # ==================== 回调事件处理方法 ====================

  async def handle_connection_event(self, connected: bool):
    """处理连接状态变更事件"""
    try:
      self.is_connected = connected
      logger.info(f"连接状态更新: {'已连接' if connected else '已断开'}")
    except Exception as e:
      logger.error(f"处理连接事件失败: {e}")

  async def handle_account_status_event(self, status):
    """处理账户状态变更事件"""
    try:
      logger.info(f"账户状态更新: {status}")
      # TODO: 可以在此发布事件到事件管理器
    except Exception as e:
      logger.error(f"处理账户状态事件失败: {e}")

  async def handle_asset_update_event(self, asset):
    """
    处理资产变动事件 (核心方法)

    Args:
      asset: XtAsset 对象
    """
    try:
      logger.info(
        f"资产更新 - 总资产: {asset.total_asset}, "
        f"现金: {asset.cash}, 市值: {asset.market_value}"
      )
      # 委托给 TradingService 处理
      if hasattr(self, "trading_service") and self.trading_service:
        await self.trading_service.handle_asset_update(asset)
    except Exception as e:
      logger.error(f"处理资产变动事件失败: {e}")

  async def handle_position_update_event(self, position):
    """
    处理持仓变动事件 (核心方法)

    Args:
      position: XtPosition 对象
    """
    try:
      logger.info(
        f"持仓更新 - {position.stock_code}: "
        f"数量={position.volume}, 可用={position.can_use_volume}"
      )
      # 委托给 TradingService 处理
      if hasattr(self, "trading_service") and self.trading_service:
        await self.trading_service.handle_position_update(position)
    except Exception as e:
      logger.error(f"处理持仓变动事件失败: {e}")

  async def handle_order_event(self, order):
    """
    处理委托回报事件

    Args:
      order: XtOrder 对象
    """
    try:
      logger.info(f"委托更新 - 订单ID: {order.order_id}, 状态: {order.order_status}")
      # 委托给 TradingService 处理
      if hasattr(self, "trading_service") and self.trading_service:
        await self.trading_service.handle_order_callback(order)
    except Exception as e:
      logger.error(f"处理委托事件失败: {e}")

  async def handle_trade_event(self, trade):
    """
    处理成交回报事件

    Args:
      trade: XtTrade 对象
    """
    try:
      logger.info(
        f"成交更新 - 订单ID: {trade.order_id}, "
        f"成交价: {trade.traded_price}, 成交量: {trade.traded_volume}"
      )
      # 委托给 TradingService 处理
      if hasattr(self, "trading_service") and self.trading_service:
        await self.trading_service.handle_trade_callback(trade)
    except Exception as e:
      logger.error(f"处理成交事件失败: {e}")

  async def handle_order_error_event(self, order_error):
    """
    处理委托失败事件

    Args:
      order_error: XtOrderError 对象
    """
    try:
      logger.error(
        f"委托失败 - 订单ID: {order_error.order_id}, 错误: {order_error.error_msg}"
      )
      # 委托给 TradingService 处理
      if hasattr(self, "trading_service") and self.trading_service:
        await self.trading_service.handle_order_error_callback(order_error)
    except Exception as e:
      logger.error(f"处理委托失败事件失败: {e}")

  async def handle_cancel_error_event(self, cancel_error):
    """
    处理撤单失败事件

    Args:
      cancel_error: XtCancelError 对象
    """
    try:
      logger.error(
        f"撤单失败 - 订单ID: {cancel_error.order_id}, 错误: {cancel_error.error_msg}"
      )
      # TODO: 发布撤单错误事件
    except Exception as e:
      logger.error(f"处理撤单失败事件失败: {e}")

  async def handle_async_order_response(self, response):
    """
    处理异步下单响应

    Args:
      response: XtOrderResponse 对象
    """
    try:
      logger.info(f"异步委托响应 - 序列号: {response.seq}, 订单ID: {response.order_id}")
      # TODO: 更新订单状态
    except Exception as e:
      logger.error(f"处理异步委托响应失败: {e}")

  async def handle_async_cancel_response(self, response):
    """
    处理异步撤单响应

    Args:
      response: XtCancelOrderResponse 对象
    """
    try:
      logger.info(f"异步撤单响应 - 订单ID: {response.order_id}")
      # TODO: 更新订单状态
    except Exception as e:
      logger.error(f"处理异步撤单响应失败: {e}")
      
  async def query_bank_info():
    """
    查询银行账户信息
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      bank_info = self.xttrader.query_bank_info(self.acc)
      return bank_info

    except Exception as e:
      logger.error(f"获取银行账户信息失败: {e}")
      return None


class MiniQMTTraderCallback(XtQuantTraderCallback):
  """
  XTQuant 交易回调处理类

  负责接收 XTQuant 的实时交易事件,并通过异步队列转发到业务层处理
  """

  def __init__(self, trading_manager):
    """
    初始化回调处理器

    Args:
      trading_manager: XTTradingManager 实例,用于访问事件循环和处理方法
    """
    self.trading_manager = trading_manager
    logger.info("MiniQMTTraderCallback 初始化完成")

  def _submit_async_task(self, coro):
    """
    将协程提交到事件循环中执行 (线程安全)

    Args:
      coro: 协程对象
    """
    try:
      if (
        hasattr(self.trading_manager, "event_loop") and self.trading_manager.event_loop
      ):
        import asyncio

        asyncio.run_coroutine_threadsafe(coro, self.trading_manager.event_loop)
      else:
        logger.warning("事件循环未初始化,无法提交异步任务")
    except Exception as e:
      logger.error(f"提交异步任务失败: {e}")

  # ==================== 连接状态回调 ====================

  def on_connected(self):
    """连接成功回调"""
    logger.info(f"{time_utils.now()} 交易连接已建立")
    self._submit_async_task(
      self.trading_manager.handle_connection_event(connected=True)
    )

  def on_disconnected(self):
    """连接断开回调"""
    logger.warning(f"{time_utils.now()} 交易连接已断开")
    self._submit_async_task(
      self.trading_manager.handle_connection_event(connected=False)
    )

  def on_account_status(self, status):
    """
    账户状态变更回调

    Args:
      status: XtAccountStatus 对象
    """
    logger.info(f"{time_utils.now()} 账户状态变更: {status}")
    self._submit_async_task(self.trading_manager.handle_account_status_event(status))

  # ==================== 资产和持仓回调 ====================

  def on_stock_asset(self, asset):
    """
    资产变动推送 (核心回调)

    Args:
      asset: XtAsset 对象
    """
    logger.info(
      f"{time_utils.now()} 资产变动 - 总资产: {asset.total_asset}, "
      f"现金: {asset.cash}, 市值: {asset.market_value}"
    )
    self._submit_async_task(self.trading_manager.handle_asset_update_event(asset))

  def on_stock_position(self, position):
    """
    持仓变动推送 (核心回调)

    Args:
      position: XtPosition 对象
    """
    logger.info(
      f"{time_utils.now()} 持仓变动 - {position.stock_code}: "
      f"数量={position.volume}, 可用={position.can_use_volume}"
    )
    self._submit_async_task(self.trading_manager.handle_position_update_event(position))

  # ==================== 订单和成交回调 ====================

  def on_stock_order(self, order):
    """
    委托回报推送

    Args:
      order: XtOrder 对象
    """
    logger.info(
      f"{time_utils.now()} 委托回调 - 订单ID: {order.order_id}, "
      f"备注: {order.order_remark}, 状态: {order.order_status}"
    )
    self._submit_async_task(self.trading_manager.handle_order_event(order))

  def on_stock_trade(self, trade):
    """
    成交变动推送

    Args:
      trade: XtTrade 对象
    """
    logger.info(
      f"{time_utils.now()} 成交回调 - 订单ID: {trade.order_id}, "
      f"成交价: {trade.traded_price}, 成交量: {trade.traded_volume}, "
      f"备注: {trade.order_remark}"
    )
    self._submit_async_task(self.trading_manager.handle_trade_event(trade))

  # ==================== 错误处理回调 ====================

  def on_order_error(self, order_error):
    """
    委托失败推送

    Args:
      order_error: XtOrderError 对象
    """
    logger.error(
      f"{time_utils.now()} 委托失败 - 订单ID: {order_error.order_id}, "
      f"错误代码: {order_error.error_id}, 错误信息: {order_error.error_msg}, "
      f"备注: {order_error.order_remark}"
    )
    self._submit_async_task(self.trading_manager.handle_order_error_event(order_error))

  def on_cancel_error(self, cancel_error):
    """
    撤单失败推送

    Args:
      cancel_error: XtCancelError 对象
    """
    logger.error(
      f"{time_utils.now()} 撤单失败 - 订单ID: {cancel_error.order_id}, "
      f"错误信息: {cancel_error.error_msg}"
    )
    self._submit_async_task(
      self.trading_manager.handle_cancel_error_event(cancel_error)
    )

  # ==================== 异步响应回调 ====================

  def on_order_stock_async_response(self, response):
    """
    异步下单回报推送

    Args:
      response: XtOrderResponse 对象
    """
    logger.info(
      f"{time_utils.now()} 异步委托回调 - 序列号: {response.seq}, "
      f"订单ID: {response.order_id}, 备注: {response.order_remark}"
    )
    self._submit_async_task(self.trading_manager.handle_async_order_response(response))

  def on_cancel_order_stock_async_response(self, response):
    """
    异步撤单回报推送

    Args:
      response: XtCancelOrderResponse 对象
    """
    logger.info(f"{time_utils.now()} 异步撤单回调 - 订单ID: {response.order_id}")
    self._submit_async_task(self.trading_manager.handle_async_cancel_response(response))

  # ==================== 扩展功能回调 (融资融券/转账) ====================

  def on_smt_appointment_async_response(self, response):
    """
    约券异步回报 (融券场景)

    Args:
      response: XtSmtAppointmentResponse 对象
    """
    logger.info(f"{time_utils.now()} 约券回报: {response}")
    # 暂不实现,预留接口

  def on_bank_transfer_async_response(self, response):
    """
    银证转账异步回报

    Args:
      response: XtBankTransferResponse 对象
    """
    logger.info(f"{time_utils.now()} 银证转账回报: {response}")
    # 暂不实现,预留接口

  def on_ctp_internal_transfer_async_response(self, response):
    """
    CTP内部转账异步回报

    Args:
      response: XtBankTransferResponse 对象
    """
    logger.info(f"{time_utils.now()} CTP内部转账回报: {response}")
    # 暂不实现,预留接口
