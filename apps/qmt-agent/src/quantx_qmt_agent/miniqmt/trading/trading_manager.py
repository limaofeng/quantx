"""
XTQuant 交易接口封装
提供统一的交易下单接口
"""

import asyncio
import logging
import os
import queue
import threading
import time
from typing import Any, Dict, List, Optional

import pandas as pd
from xtquant import xtconstant
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount, XtAsset, XtOrder, XtPosition, XtTrade

from quantx_qmt_agent.credentials import state_directory
from quantx_qmt_agent.endpoints import masked_account_id
from quantx_qmt_agent.qmt_types import (
  AccountType,
  OrderPriceType,
  OrderStatus,
  OrderType,
  PriceType,
)

logger = logging.getLogger(__name__)

path = os.environ.get("QMT_USERDATA_PATH", "").strip()
MAX_DURABLE_CALLBACK_BACKLOG = 4096
MAX_CONTROL_CALLBACK_TASKS = 128
CALLBACK_DRAIN_TIMEOUT_SECONDS = 2.0
_CALLBACK_WRITER_SENTINEL = object()


class TradingConnectionError(Exception):
  """交易连接异常"""

  pass


class InvalidOrderError(Exception):
  """无效订单异常"""

  pass


class XTTradingManager:
  """XTQuant 交易管理器"""

  def __init__(self, account_id: str, account_type: AccountType = AccountType.STOCK):
    if not path:
      raise TradingConnectionError("QMT_USERDATA_PATH is not configured")
    self.account_id = account_id
    self.is_connected = False
    self.account_status_rpc_succeeded = False
    self.session_id = None
    self.account_type = account_type
    self.xttrader = None
    self.acc = StockAccount(self.account_id, self.account_type.value)
    self._native_started = False
    self.trading_service = None
    self._callback_queue: queue.Queue[Any] = queue.Queue(
      maxsize=MAX_DURABLE_CALLBACK_BACKLOG
    )
    self._callback_state_lock = threading.Lock()
    self._callback_pipeline_healthy = True
    self._callback_pipeline_error = ""
    self._callback_failure_generation = 0
    self._callback_recovery_pending = False
    self._callback_recovery_generation: int | None = None
    self._callback_write_inflight = False
    self._callback_accepting = True
    self._callback_close_lock = threading.Lock()
    self._callback_writer_sentinel_enqueued = False
    self._callback_writer_stopped = threading.Event()
    self._callback_writer_thread = threading.Thread(
      target=self._durable_callback_writer,
      daemon=True,
      name="XTTradingCallbackWriter",
    )
    self._callback_writer_thread.start()
    self._control_callback_slots = threading.BoundedSemaphore(
      MAX_CONTROL_CALLBACK_TASKS
    )
    # 初始化事件循环 (用于处理异步回调)
    self.event_loop = None
    self.event_loop_thread = None
    self._init_event_loop()
    self._init_connection()

  def _init_event_loop(self):
    """初始化事件循环 (在独立线程中运行)"""
    try:
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
    except Exception as exc:
      logger.error("初始化事件循环失败: error=%s", exc.__class__.__name__)
      self.event_loop = None

  def _init_connection(self):
    """初始化交易连接"""
    try:
      # 连接到XTQuant交易服务
      session_id = int(time.time())
      self.session_id = session_id
      self.xttrader = XtQuantTrader(path, session_id)
      # 创建交易回调类对象，并声明接收回调
      callback = MiniQMTTraderCallback(self)
      self.xttrader.register_callback(callback)
      # 启动交易线程
      self.xttrader.start()
      self._native_started = True
      if not self.reconnect():
        logger.error("XTQuant交易连接失败")
    except Exception as exc:
      logger.error("XTQuant交易连接失败: error=%s", exc.__class__.__name__)
      self.is_connected = False

  def reconnect(self) -> bool:
    """Reconnect the already-started native client without allocating one more.

    XtQuantAsyncClient.init() consumes a process-global native Writer slot.
    Recreating a trader for every retry eventually exhausts that fixed pool, so
    retries must reuse the single initialized client for this account.
    """
    if self.is_connected:
      return True
    trader = self.xttrader
    if trader is None or not self._native_started:
      return False
    self.account_status_rpc_succeeded = False
    self._last_connection_health_status = None
    try:
      connect_result = trader.connect()
    except Exception as exc:
      logger.warning(
        "XTQuant交易重连失败: account=%s error=%s",
        masked_account_id(self.account_id),
        exc.__class__.__name__,
      )
      self.is_connected = False
      return False
    self.is_connected = connect_result == 0
    if self.is_connected:
      logger.info(
        "XTQuant交易连接成功, account=%s",
        masked_account_id(self.account_id),
      )
    return self.is_connected

  def callback_pipeline_healthy(self) -> bool:
    with self._callback_state_lock:
      return self._callback_pipeline_healthy

  def callback_pipeline_error(self) -> str:
    with self._callback_state_lock:
      return self._callback_pipeline_error

  def callback_failure_generation(self) -> int:
    """Return the monotonic generation of observed callback durability gaps."""

    with self._callback_state_lock:
      return max(0, int(getattr(self, "_callback_failure_generation", 0)))

  def enqueue_durable_callback(self, kind: str, value: Any) -> bool:
    with self._callback_state_lock:
      if not self._callback_accepting:
        self._mark_callback_pipeline_failed_locked("REPORT_CALLBACK_AFTER_CLOSE")
        return False
    service = self.trading_service
    if service is None:
      self._mark_callback_pipeline_failed("REPORT_SINK_UNAVAILABLE")
      return False
    mark_observed = getattr(service, "mark_callback_observed", None)
    if callable(mark_observed):
      try:
        mark_observed()
      except Exception:
        self._mark_callback_pipeline_failed("REPORT_MUTATION_FENCE_FAILED")
        logger.exception("XTTrading callback mutation fence failed")
        return False
    try:
      prepared = service.prepare_callback(kind, value)
    except Exception:
      self._mark_callback_pipeline_failed("REPORT_NORMALIZATION_FAILED")
      logger.exception(
        "XTTrading callback normalization failed: kind=%s",
        kind,
      )
      return False
    with self._callback_state_lock:
      # Native shutdown can race normalization. Only callbacks inserted before
      # accepting flips false belong ahead of the drain sentinel.
      if not self._callback_accepting:
        self._mark_callback_pipeline_failed_locked("REPORT_CALLBACK_AFTER_CLOSE")
        return False
      try:
        self._callback_queue.put_nowait((service, prepared))
      except queue.Full:
        self._mark_callback_pipeline_failed_locked("REPORT_QUEUE_OVERFLOW")
        logger.error(
          "XTTrading durable callback queue overflow: capacity=%s",
          MAX_DURABLE_CALLBACK_BACKLOG,
        )
        return False
    return True

  def mark_callback_pipeline_reconciled(
    self,
    expected_failure_generation: int,
  ) -> bool:
    """Clear only the callback gap covered by an acknowledged snapshot."""

    expected_generation = max(0, int(expected_failure_generation))
    with self._callback_state_lock:
      current_generation = max(
        0,
        int(getattr(self, "_callback_failure_generation", 0)),
      )
      if expected_generation != current_generation:
        return False
      if self._callback_pipeline_healthy:
        return True
      if self._callback_backlog_empty_locked():
        self._callback_pipeline_healthy = True
        self._callback_pipeline_error = ""
        self._callback_recovery_pending = False
        self._callback_recovery_generation = None
        return True
      self._callback_recovery_pending = True
      self._callback_recovery_generation = expected_generation
      return False

  def _mark_callback_pipeline_failed(self, reason: str) -> None:
    with self._callback_state_lock:
      self._mark_callback_pipeline_failed_locked(reason)

  def _mark_callback_pipeline_failed_locked(self, reason: str) -> None:
    self._callback_failure_generation = (
      max(0, int(getattr(self, "_callback_failure_generation", 0))) + 1
    )
    self._callback_pipeline_healthy = False
    self._callback_pipeline_error = reason[:128]
    self._callback_recovery_pending = False
    self._callback_recovery_generation = None

  def _callback_backlog_empty_locked(self) -> bool:
    with self._callback_queue.mutex:
      unfinished = self._callback_queue.unfinished_tasks
    return unfinished == 0 and not self._callback_write_inflight

  def _durable_callback_writer(self) -> None:
    try:
      while True:
        item = self._callback_queue.get()
        if item is _CALLBACK_WRITER_SENTINEL:
          self._callback_queue.task_done()
          self._recover_callback_pipeline_if_drained()
          return
        service, prepared = item
        retry_delay = 0.05
        with self._callback_state_lock:
          self._callback_write_inflight = True
        while True:
          try:
            service.persist_prepared_callback(prepared)
          except Exception:
            self._mark_callback_pipeline_failed("REPORT_PERSISTENCE_FAILED")
            logger.exception("XTTrading durable callback persistence failed")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 2.0)
            continue
          break
        with self._callback_state_lock:
          self._callback_write_inflight = False
        # Only durable success retires an accepted callback. Shutdown timeout
        # and persistence failures intentionally leave unfinished_tasks set.
        self._callback_queue.task_done()
        self._recover_callback_pipeline_if_drained()
    finally:
      self._callback_writer_stopped.set()

  def _recover_callback_pipeline_if_drained(self) -> None:
    with self._callback_state_lock:
      recovery_generation = getattr(
        self,
        "_callback_recovery_generation",
        None,
      )
      if (
        self._callback_recovery_pending
        and recovery_generation
        == max(0, int(getattr(self, "_callback_failure_generation", 0)))
        and self._callback_backlog_empty_locked()
      ):
        self._callback_pipeline_healthy = True
        self._callback_pipeline_error = ""
        self._callback_recovery_pending = False
        self._callback_recovery_generation = None

  def _stop_durable_callback_writer(
    self,
    *,
    timeout: float = CALLBACK_DRAIN_TIMEOUT_SECONDS,
  ) -> bool:
    """Stop accepting callbacks and durably drain every accepted envelope."""

    # Focused legacy harnesses can construct a manager without running
    # __init__. Production managers always own the complete callback pipeline.
    if not hasattr(self, "_callback_queue") or not hasattr(
      self, "_callback_state_lock"
    ):
      return True
    close_lock = getattr(self, "_callback_close_lock", None)
    if close_lock is None:
      close_lock = threading.Lock()
      self._callback_close_lock = close_lock
    deadline = time.monotonic() + max(0.0, timeout)
    with close_lock:
      with self._callback_state_lock:
        self._callback_accepting = False
        sentinel_enqueued = self._callback_writer_sentinel_enqueued
      writer = getattr(self, "_callback_writer_thread", None)
      if writer is None or not writer.is_alive():
        with self._callback_state_lock:
          drained = self._callback_backlog_empty_locked()
        if not drained:
          self._mark_callback_pipeline_failed("REPORT_DRAIN_TIMEOUT")
        return drained

      if not sentinel_enqueued:
        remaining = max(0.0, deadline - time.monotonic())
        try:
          self._callback_queue.put(
            _CALLBACK_WRITER_SENTINEL,
            timeout=remaining,
          )
        except queue.Full:
          self._mark_callback_pipeline_failed("REPORT_DRAIN_TIMEOUT")
          return False
        with self._callback_state_lock:
          self._callback_writer_sentinel_enqueued = True

      writer.join(timeout=max(0.0, deadline - time.monotonic()))
      if writer.is_alive():
        self._mark_callback_pipeline_failed("REPORT_DRAIN_TIMEOUT")
        return False
      with self._callback_state_lock:
        drained = self._callback_backlog_empty_locked()
      if not drained:
        self._mark_callback_pipeline_failed("REPORT_DRAIN_TIMEOUT")
      return drained

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
        price: 固定价限价委托价格
        price_type: 价格类型

    Returns:
        Dict: 下单结果
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      if price_type != PriceType.FIX_PRICE:
        raise InvalidOrderError("仅支持 FIX_PRICE 固定价限价委托")
      if price <= 0:
        raise InvalidOrderError("固定价限价委托必须指定正数价格")
      inside_price_type = xtconstant.FIX_PRICE

      order_id = self.xttrader.order_stock(
        account=self.acc,
        stock_code=stock_code,
        order_type=order_type.value,
        order_volume=order_volume,
        price_type=inside_price_type,
        price=price,
        strategy_name="",
        order_remark=order_remark,
      )

      if order_id > 0:
        result = {
          "success": True,
          "order_id": order_id,
          "message": "下单成功",
        }
        logger.info("下单请求已被 XTTrading 接受")
        return result
      else:
        return {"success": False, "message": f"下单失败, order_id: {order_id}"}

    except Exception as exc:
      logger.error("下单失败: error=%s", exc.__class__.__name__)
      return {"success": False, "message": f"下单异常: {exc.__class__.__name__}"}

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
        logger.info("委托已完成: status=%s", latest_status.name)
        return latest_status
      logger.info("委托仍在处理中: status=%s", latest_status.name)
      time.sleep(1)

    logger.warning("等待委托完成超时: status=%s", latest_status.name)
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

    except Exception as exc:
      logger.error("撤单失败: error=%s", exc.__class__.__name__)
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

    except Exception as exc:
      logger.error("获取持仓失败: error=%s", exc.__class__.__name__)
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

    except Exception as exc:
      logger.error("获取持仓失败: error=%s", exc.__class__.__name__)
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

    except Exception as exc:
      logger.error("获取订单失败: error=%s", exc.__class__.__name__)
      raise

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

    except Exception as exc:
      logger.error("获取订单失败: error=%s", exc.__class__.__name__)
      raise

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
        export_directory = state_directory() / "exports"
        export_directory.mkdir(parents=True, exist_ok=True)
        file_path = str(export_directory / f"history_orders_{self.account_id}.csv")

      result = self.xttrader.export_data(
        self.acc, file_path, "orders", "20250601", None, {}
      )

      if result.get("code") == 0:
        logger.info("历史订单下载成功")
        return True
      else:
        logger.error("历史订单下载失败")
        return False

    except Exception as exc:
      logger.error("下载历史订单失败: error=%s", exc.__class__.__name__)
      return False

  def get_history_orders(self, start_date: str, end_date: str) -> pd.DataFrame:
    """
    获取历史订单信息
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      return self.xttrader.query_data(self.acc, start_date, end_date)

    except Exception as exc:
      logger.error("获取历史订单失败: error=%s", exc.__class__.__name__)
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

    except Exception as exc:
      logger.error("获取成交失败: error=%s", exc.__class__.__name__)
      raise

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

    except Exception as exc:
      logger.error("获取账户信息失败: error=%s", exc.__class__.__name__)
      return {}

  def is_account_status_ok(self) -> bool:
    """检查当前交易账户状态是否正常"""
    try:
      return self.query_account_status() == xtconstant.ACCOUNT_STATUS_OK

    except Exception as exc:
      logger.error("检查账户状态失败: error=%s", exc.__class__.__name__)
      return False

  def is_connection_healthy(self) -> bool:
    """Probe the native RPC transport without conflating account readiness.

    ``query_account_status`` can succeed while miniQMT reports a transitional,
    closed, or failed account state.  Reconnecting a responsive transport for
    those account-level states resets login and reconciliation continuously.
    Keep the transport and let the readiness gate observe the cached account
    status independently.
    """
    try:
      previous = getattr(self, "_last_connection_health_status", object())
      status = self._query_account_status()
      if not self._account_status_ready(status) and status != previous:
        logger.warning(
          "XTTrading account status is not trading-ready: "
          "reason_code=XTTRADING_ACCOUNT_STATUS_NOT_READY status=%s",
          status,
        )
      return True
    except Exception as exc:
      logger.error("检查交易连接状态失败: error=%s", exc.__class__.__name__)
      return False

  def is_account_status_ready(self) -> bool:
    """Return cached account readiness after the registry transport probe."""
    sentinel = object()
    status = getattr(self, "_last_connection_health_status", sentinel)
    if status is sentinel:
      try:
        status = self._query_account_status()
      except Exception as exc:
        logger.error("检查账户就绪状态失败: error=%s", exc.__class__.__name__)
        return False
    return self._account_status_ready(status)

  def query_account_status(self) -> int | None:
    """Freshly query the native account status for snapshot authority."""

    return self._query_account_status()

  @staticmethod
  def _account_status_ready(status: int | None) -> bool:
    return status in {
      xtconstant.ACCOUNT_STATUS_OK,
      xtconstant.ACCOUNT_STATUS_CLOSED,
    }

  def _query_account_status(self) -> int | None:
    self.account_status_rpc_succeeded = False
    self._last_connection_health_status = None
    if not self.is_connected:
      raise TradingConnectionError("交易连接未建立")

    expected_account_id = str(self.acc.account_id).strip()
    expected_account_type = str(self.acc.account_type).strip().upper()
    statuses = self.xttrader.query_account_status()
    self.account_status_rpc_succeeded = True
    for account_status in statuses or []:
      account_id = str(getattr(account_status, "account_id", "")).strip()
      account_type = str(getattr(account_status, "account_type", "")).strip().upper()
      if account_id != expected_account_id or account_type != expected_account_type:
        continue
      try:
        status = int(
          getattr(account_status, "status", xtconstant.ACCOUNT_STATUS_INVALID)
        )
        self._last_connection_health_status = status
        return status
      except (TypeError, ValueError):
        self._last_connection_health_status = None
        return None
    self._last_connection_health_status = None
    return None

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
      if self.xttrader is not None and getattr(self, "_native_started", True):
        close_method = getattr(self.xttrader, "stop", None)
        if not callable(close_method):
          close_method = getattr(self.xttrader, "disconnect", None)
        if callable(close_method):
          close_method()
        else:
          logger.warning("XTQuant交易对象缺少可用的关闭方法")
    except Exception as exc:
      logger.error("关闭XTQuant交易连接失败: error=%s", exc.__class__.__name__)
    finally:
      self.is_connected = False
      self.session_id = None
      self._native_started = False
      logger.info("XTQuant交易连接已关闭")

      # Native callbacks must be stopped before the sentinel is appended.
      # Anything accepted before this point is durably persisted in FIFO order;
      # a timeout leaves the callback gap latched for full reconciliation.
      if not self._stop_durable_callback_writer(timeout=CALLBACK_DRAIN_TIMEOUT_SECONDS):
        logger.error("XTTrading durable callback drain did not complete")

      # 停止事件循环 even when the native client reports a shutdown error.
      try:
        if self.event_loop:
          self.event_loop.call_soon_threadsafe(self.event_loop.stop)
          if self.event_loop_thread and self.event_loop_thread.is_alive():
            self.event_loop_thread.join(timeout=2)
          if not self.event_loop.is_running() and not self.event_loop.is_closed():
            self.event_loop.close()
          self.event_loop = None
          self.event_loop_thread = None
          logger.info("事件循环已停止")
      except Exception as exc:
        logger.error("停止XTQuant事件循环失败: error=%s", exc.__class__.__name__)

  # ==================== 回调事件处理方法 ====================

  async def handle_connection_event(self, connected: bool):
    """处理连接状态变更事件"""
    self.is_connected = connected
    logger.info("连接状态更新: connected=%s", connected)

  async def handle_account_status_event(self, status):
    """处理账户状态变更事件"""
    try:
      event_account_id = str(getattr(status, "account_id", "")).strip()
      event_account_type = str(
        getattr(status, "account_type", "")
      ).strip().upper()
      expected_account_id = str(self.acc.account_id).strip()
      expected_account_type = str(self.acc.account_type).strip().upper()
      if (
        event_account_id == expected_account_id
        and event_account_type == expected_account_type
      ):
        try:
          self._last_connection_health_status = int(
            getattr(status, "status", xtconstant.ACCOUNT_STATUS_INVALID)
          )
        except (TypeError, ValueError):
          self._last_connection_health_status = None
      logger.info(
        "账户状态更新: event=%s status=%s",
        status.__class__.__name__,
        getattr(self, "_last_connection_health_status", None),
      )
    except Exception as exc:
      logger.error("处理账户状态事件失败: error=%s", exc.__class__.__name__)

  async def handle_asset_update_event(self, asset):
    """
    处理资产变动事件 (核心方法)

    Args:
      asset: XtAsset 对象
    """
    logger.info("资产更新已接收")
    if self.trading_service:
      await self.trading_service.handle_asset_update(asset)

  async def handle_position_update_event(self, position):
    """
    处理持仓变动事件 (核心方法)

    Args:
      position: XtPosition 对象
    """
    logger.info("持仓更新已接收: instrument=%s", position.stock_code)
    if self.trading_service:
      await self.trading_service.handle_position_update(position)

  async def handle_order_event(self, order):
    """
    处理委托回报事件

    Args:
      order: XtOrder 对象
    """
    logger.info("委托更新已接收: status=%s", order.order_status)
    if self.trading_service:
      await self.trading_service.handle_order_callback(order)

  async def handle_trade_event(self, trade):
    """
    处理成交回报事件

    Args:
      trade: XtTrade 对象
    """
    logger.info("成交更新已接收")
    if self.trading_service:
      await self.trading_service.handle_trade_callback(trade)

  async def handle_order_error_event(self, order_error):
    """
    处理委托失败事件

    Args:
      order_error: XtOrderError 对象
    """
    logger.error(
      "委托失败事件已接收: error_code=%s",
      getattr(order_error, "error_id", "UNKNOWN"),
    )
    if self.trading_service:
      await self.trading_service.handle_order_error_callback(order_error)

  async def handle_cancel_error_event(self, cancel_error):
    """
    处理撤单失败事件

    Args:
      cancel_error: XtCancelError 对象
    """
    logger.error("撤单失败事件已接收")
    if self.trading_service:
      await self.trading_service.handle_cancel_error_callback(cancel_error)

  async def handle_async_order_response(self, response):
    """
    处理异步下单响应

    Args:
      response: XtOrderResponse 对象
    """
    try:
      logger.info("异步委托响应已接收")
      # TODO: 更新订单状态
    except Exception as exc:
      logger.error("处理异步委托响应失败: error=%s", exc.__class__.__name__)

  async def handle_async_cancel_response(self, response):
    """
    处理异步撤单响应

    Args:
      response: XtCancelOrderResponse 对象
    """
    try:
      logger.info("异步撤单响应已接收")
      # TODO: 更新订单状态
    except Exception as exc:
      logger.error("处理异步撤单响应失败: error=%s", exc.__class__.__name__)

  async def query_bank_info(self):
    """
    查询银行账户信息
    """
    try:
      if not self.is_connected:
        raise TradingConnectionError("交易连接未建立")

      bank_info = self.xttrader.query_bank_info(self.acc)
      return bank_info

    except Exception as exc:
      logger.error("获取银行账户信息失败: error=%s", exc.__class__.__name__)
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
      loop = getattr(self.trading_manager, "event_loop", None)
      if loop and loop.is_running() and not loop.is_closed():
        slots = getattr(self.trading_manager, "_control_callback_slots", None)
        if slots is None:
          slots = threading.BoundedSemaphore(MAX_CONTROL_CALLBACK_TASKS)
          self.trading_manager._control_callback_slots = slots
        if not slots.acquire(blocking=False):
          self.trading_manager._mark_callback_pipeline_failed(
            "CONTROL_CALLBACK_OVERFLOW"
          )
          close = getattr(coro, "close", None)
          if callable(close):
            close()
          return
        future = asyncio.run_coroutine_threadsafe(coro, loop)

        def completed(result) -> None:
          try:
            result.result()
          except Exception:
            self.trading_manager._mark_callback_pipeline_failed(
              "CONTROL_CALLBACK_FAILED"
            )
            logger.exception("XTTrading control callback failed")
          finally:
            slots.release()

        future.add_done_callback(completed)
      else:
        logger.warning("事件循环未初始化,无法提交异步任务")
        close = getattr(coro, "close", None)
        if callable(close):
          close()
    except Exception as exc:
      logger.error("提交异步任务失败: error=%s", exc.__class__.__name__)
      close = getattr(coro, "close", None)
      if callable(close):
        close()

  def _submit_durable_callback(self, kind: str, value: Any) -> None:
    self.trading_manager.enqueue_durable_callback(kind, value)

  # ==================== 连接状态回调 ====================

  def on_connected(self):
    """连接成功回调"""
    logger.info("交易连接已建立")
    self.trading_manager.is_connected = True
    self._submit_async_task(
      self.trading_manager.handle_connection_event(connected=True)
    )

  def on_disconnected(self):
    """连接断开回调"""
    logger.warning("交易连接已断开")
    self.trading_manager.is_connected = False
    self.trading_manager.account_status_rpc_succeeded = False
    self._submit_async_task(
      self.trading_manager.handle_connection_event(connected=False)
    )

  def on_account_status(self, status):
    """
    账户状态变更回调

    Args:
      status: XtAccountStatus 对象
    """
    logger.info("账户状态变更: event=%s", status.__class__.__name__)
    service = getattr(self.trading_manager, "trading_service", None)
    mark_observed = getattr(service, "mark_status_observed", None)
    if not callable(mark_observed):
      mark_observed = getattr(service, "mark_callback_observed", None)
    if callable(mark_observed):
      # Fence a status transition before the asynchronous cache update.  A
      # snapshot assembled immediately before this callback must not commit.
      mark_observed()
    self._submit_async_task(self.trading_manager.handle_account_status_event(status))

  # ==================== 资产和持仓回调 ====================

  def on_stock_asset(self, asset):
    """
    资产变动推送 (核心回调)

    Args:
      asset: XtAsset 对象
    """
    logger.info("资产变动已接收")
    self._submit_durable_callback("asset", asset)

  def on_stock_position(self, position):
    """
    持仓变动推送 (核心回调)

    Args:
      position: XtPosition 对象
    """
    logger.info("持仓变动已接收: instrument=%s", position.stock_code)
    self._submit_durable_callback("position", position)

  # ==================== 订单和成交回调 ====================

  def on_stock_order(self, order):
    """
    委托回报推送

    Args:
      order: XtOrder 对象
    """
    logger.info("委托回调已接收: status=%s", order.order_status)
    self._submit_durable_callback("order", order)

  def on_stock_trade(self, trade):
    """
    成交变动推送

    Args:
      trade: XtTrade 对象
    """
    logger.info("成交回调已接收")
    self._submit_durable_callback("trade", trade)

  # ==================== 错误处理回调 ====================

  def on_order_error(self, order_error):
    """
    委托失败推送

    Args:
      order_error: XtOrderError 对象
    """
    logger.error(
      "委托失败回调已接收: error_code=%s",
      getattr(order_error, "error_id", "UNKNOWN"),
    )
    self._submit_durable_callback("order_error", order_error)

  def on_cancel_error(self, cancel_error):
    """
    撤单失败推送

    Args:
      cancel_error: XtCancelError 对象
    """
    logger.error("撤单失败回调已接收")
    self._submit_durable_callback("cancel_error", cancel_error)

  # ==================== 异步响应回调 ====================

  def on_order_stock_async_response(self, response):
    """
    异步下单回报推送

    Args:
      response: XtOrderResponse 对象
    """
    logger.info("异步委托回调已接收")
    self._submit_async_task(self.trading_manager.handle_async_order_response(response))

  def on_cancel_order_stock_async_response(self, response):
    """
    异步撤单回报推送

    Args:
      response: XtCancelOrderResponse 对象
    """
    logger.info("异步撤单回调已接收")
    self._submit_async_task(self.trading_manager.handle_async_cancel_response(response))

  # ==================== 扩展功能回调 (融资融券/转账) ====================

  def on_smt_appointment_async_response(self, response):
    """
    约券异步回报 (融券场景)

    Args:
      response: XtSmtAppointmentResponse 对象
    """
    logger.info("约券回报已接收")
    # 暂不实现,预留接口

  def on_bank_transfer_async_response(self, response):
    """
    银证转账异步回报

    Args:
      response: XtBankTransferResponse 对象
    """
    logger.info("银证转账回报已接收")
    # 暂不实现,预留接口

  def on_ctp_internal_transfer_async_response(self, response):
    """
    CTP内部转账异步回报

    Args:
      response: XtBankTransferResponse 对象
    """
    logger.info("CTP内部转账回报已接收")
    # 暂不实现,预留接口
