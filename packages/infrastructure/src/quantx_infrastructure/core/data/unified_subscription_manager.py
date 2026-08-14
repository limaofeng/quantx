"""
统一数据订阅管理器 - 解决重复订阅问题

该模块提供统一的 XTQuant 数据订阅管理，避免多个组件重复订阅同一股票代码。
主要功能：
- tick数据全推订阅：所有tick数据使用subscribe_whole_quote统一订阅
- 其他周期单股订阅：K线等数据继续使用subscribe_quote
- 无缝切换：新股票加入时无缝切换全推订阅
- 智能过滤：只分发已订阅股票的数据
- 回调管理：支持多个组件注册回调到同一订阅
- 错误处理：统一处理 XTQuant 连接和订阅错误

注意：
- 订阅通过 (subscriber_id, stock_code, period) 三元组标识
- 取消订阅使用相同的三元组参数
"""

import asyncio
import logging
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Dict, Optional, Set

from quantx_infrastructure.core.data.remote_market_data import RemoteMarketDataRegistry

logger = logging.getLogger(__name__)


@dataclass
class SubscriptionInfo:
  """单股订阅信息（用于非tick数据）"""

  stock_code: str
  period: str
  remote_subscription_id: int
  callbacks: Set[Callable]
  subscriber_ids: Set[str]  # 跟踪哪些组件在使用这个订阅


@dataclass
class TickSubscriptionInfo:
  """tick订阅信息（单个股票的回调管理）"""

  stock_code: str
  callbacks: Set[Callable]
  subscriber_ids: Set[str]


class UnifiedDataSubscriptionManager:
  """
  统一数据订阅管理器

  解决多个组件向 XTQuant 重复订阅同一股票代码的问题。
  提供订阅去重、回调管理和自动清理功能。
  """

  _instance = None
  _lock = Lock()

  def __new__(cls):
    """单例模式，确保全局只有一个统一管理器实例"""
    if cls._instance is None:
      with cls._lock:
        if cls._instance is None:
          cls._instance = super().__new__(cls)
    return cls._instance

  def __init__(self):
    if hasattr(self, "_initialized"):
      return

    self._initialized = True

    # 主事件循环引用（用于跨线程调度异步回调）
    self._main_loop: Optional[asyncio.AbstractEventLoop] = None
    self._main_loop_lock = Lock()

    # XTQuant 数据管理器
    self.data_manager_registry = RemoteMarketDataRegistry()
    self._data_manager = None

    # === tick数据全推订阅管理 ===
    # 当前全推订阅ID
    self.whole_quote_subscription_id: Optional[int] = None
    # 当前全推订阅的股票列表（用于变化检测）
    self._subscribed_stocks: Set[str] = set()
    # tick订阅的股票信息：{stock_code: TickSubscriptionInfo}
    self.tick_subscriptions: Dict[str, TickSubscriptionInfo] = {}
    # tick订阅者管理：{subscriber_id: Set[stock_code]}
    self.tick_subscribers: Dict[str, Set[str]] = {}

    # === 数据缓存 ===
    # 最新tick数据缓存：{stock_code: latest_tick_data}
    self.latest_tick_data: Dict[str, Dict] = {}

    # === 其他周期单股订阅管理 ===
    # 非tick订阅信息：{subscription_key: SubscriptionInfo}
    # subscription_key 格式："{stock_code}_{period}"
    self.period_subscriptions: Dict[str, SubscriptionInfo] = {}
    # 非tick订阅者管理：{subscriber_id: Set[subscription_key]}
    self.period_subscribers: Dict[str, Set[str]] = {}

    # 线程锁，确保订阅操作的原子性
    self.lock = Lock()
    self._whole_quote_refresh_task: Optional[asyncio.Task] = None
    self._whole_quote_refresh_debounce_seconds = 0.05

  @property
  def data_manager(self):
    """按需获取 XTQuant 数据管理器，避免应用导入阶段连接 miniQMT。"""
    if self._data_manager is None:
      self._data_manager = self.data_manager_registry.get_manager()
    return self._data_manager

  def set_main_loop(
    self,
    loop: Optional[asyncio.AbstractEventLoop] = None,
  ) -> bool:
    """
    设置主事件循环引用（用于跨线程调度异步回调）

    Args:
        loop: 事件循环实例，如果为None则尝试获取当前运行的循环
    """
    if loop is None:
      try:
        loop = asyncio.get_running_loop()
      except RuntimeError:
        logger.warning("无法获取运行中的事件循环，异步回调可能无法正常工作")
        return False

    with self._main_loop_lock:
      if self._main_loop is loop:
        return True
      self._main_loop = loop

    logger.info("已设置主事件循环引用")
    return True

  def _active_main_loop(self) -> Optional[asyncio.AbstractEventLoop]:
    with self._main_loop_lock:
      loop = self._main_loop
    if loop is None or loop.is_closed():
      return None
    return loop

  def _get_subscription_key(self, stock_code: str, period: str = "tick") -> str:
    """生成订阅键（仅用于非tick数据）"""
    return f"{stock_code}_{period}"

  def _is_tick_period(self, period: str) -> bool:
    """判断是否为tick周期"""
    return period == "tick"

  async def subscribe(
    self, stock_code: str, callback: Callable, subscriber_id: str, period: str = "tick"
  ) -> bool:
    """
    订阅股票数据

    Args:
        stock_code: 股票代码
        callback: 数据回调函数（不能为None）
        subscriber_id: 订阅者ID（用于区分不同的组件）
        period: 数据周期

    Returns:
        是否订阅成功

    Raises:
        ValueError: 如果 callback 为 None
    """
    # 验证回调函数不能为None
    if callback is None:
      raise ValueError(
        f"callback 不能为 None: stock_code={stock_code}, subscriber_id={subscriber_id}"
      )

    if self._is_tick_period(period):
      return await self._subscribe_tick(stock_code, callback, subscriber_id)
    else:
      return await self._subscribe_period(stock_code, callback, subscriber_id, period)

  async def _subscribe_tick(
    self, stock_code: str, callback: Callable, subscriber_id: str
  ) -> bool:
    """订阅tick数据（使用全推）"""

    # 标记是否为新股票和是否需要立即推送
    is_new_stock = False
    should_push_cached_data = False

    with self.lock:
      # 检查是否为新股票
      is_new_stock = stock_code not in self.tick_subscriptions

      # 添加到tick订阅管理
      if is_new_stock:
        self.tick_subscriptions[stock_code] = TickSubscriptionInfo(
          stock_code=stock_code, callbacks=set(), subscriber_ids=set()
        )

      tick_info = self.tick_subscriptions[stock_code]
      tick_info.callbacks.add(callback)
      tick_info.subscriber_ids.add(subscriber_id)

      # 管理订阅者
      if subscriber_id not in self.tick_subscribers:
        self.tick_subscribers[subscriber_id] = set()
      self.tick_subscribers[subscriber_id].add(stock_code)

      # 检查是否有缓存数据需要立即推送
      if stock_code in self.latest_tick_data:
        should_push_cached_data = True

    # 如果有缓存数据，立即推送给新订阅者
    if should_push_cached_data:
      try:
        cached_data = {stock_code: self.latest_tick_data[stock_code]}
        if asyncio.iscoroutinefunction(callback):
          await callback(cached_data)
        else:
          callback(cached_data)
        logger.debug(f"立即推送缓存数据给新订阅者: {stock_code}")

      except Exception as e:
        logger.error(f"推送缓存数据失败: {stock_code}, {e}")

    # 只有新股票才刷新全推订阅（在锁外执行，避免死锁）
    if is_new_stock:
      refresh_ok = await self._refresh_whole_quote_subscription()
      if not refresh_ok:
        self._remove_tick_callback(stock_code, callback, subscriber_id)
        logger.error(f"新股票订阅失败，已回滚tick订阅: {stock_code}")
        return False
      logger.info(f"新股票订阅，触发全推刷新: {stock_code}")
    else:
      logger.info(f"复用现有全推订阅: {stock_code}")

    logger.info(f"订阅tick数据成功: {stock_code}, 订阅者: {subscriber_id}")
    return True

  def _remove_tick_callback(
    self, stock_code: str, callback: Callable, subscriber_id: str
  ) -> None:
    """移除一次未成功建立的 tick 订阅注册。"""
    with self.lock:
      tick_info = self.tick_subscriptions.get(stock_code)
      if tick_info is None:
        return

      tick_info.callbacks.discard(callback)
      tick_info.subscriber_ids.discard(subscriber_id)

      if subscriber_id in self.tick_subscribers:
        self.tick_subscribers[subscriber_id].discard(stock_code)
        if not self.tick_subscribers[subscriber_id]:
          del self.tick_subscribers[subscriber_id]

      if not tick_info.callbacks and not tick_info.subscriber_ids:
        del self.tick_subscriptions[stock_code]
        self.latest_tick_data.pop(stock_code, None)

  def get_latest_tick(self, stock_code: str) -> Optional[Dict]:
    """获取指定股票的最新tick原始数据快照"""
    with self.lock:
      return self.latest_tick_data.get(stock_code)

  async def _subscribe_period(
    self, stock_code: str, callback: Callable, subscriber_id: str, period: str
  ) -> bool:
    """订阅非tick数据（使用单股订阅）"""
    subscription_key = self._get_subscription_key(stock_code, period)

    with self.lock:
      # 检查是否已有相同的订阅
      if subscription_key in self.period_subscriptions:
        # 已有订阅，只添加回调和订阅者信息
        subscription_info = self.period_subscriptions[subscription_key]
        subscription_info.callbacks.add(callback)
        subscription_info.subscriber_ids.add(subscriber_id)
        logger.info(
          f"复用现有非tick订阅: {stock_code} {period}, 新增订阅者: {subscriber_id}"
        )

      else:
        # 新建订阅
        try:
          # 创建数据回调包装器
          def data_callback(data):
            """数据回调包装器"""
            self._schedule_period_data_callback(subscription_key, data)

          # 向 XTQuant 发起订阅
          xt_sub_id = self.data_manager.subscribe_quote(
            stock_code, period=period, count=-1, callback=data_callback
          )

          if xt_sub_id is not None:
            # 创建订阅信息
            subscription_info = SubscriptionInfo(
              stock_code=stock_code,
              period=period,
              remote_subscription_id=xt_sub_id,
              callbacks={callback},
              subscriber_ids={subscriber_id},
            )

            self.period_subscriptions[subscription_key] = subscription_info
            logger.info(
              f"新建XTQuant非tick订阅: {stock_code} {period}, "
              f"XTQuant订阅ID: {xt_sub_id}"
            )

          else:
            logger.error(f"XTQuant非tick订阅失败: {stock_code} {period}")
            return False

        except Exception as e:
          logger.error(f"创建XTQuant非tick订阅异常: {stock_code} {period}, {e}")
          return False

      # 更新订阅者信息
      if subscriber_id not in self.period_subscribers:
        self.period_subscribers[subscriber_id] = set()
      self.period_subscribers[subscriber_id].add(subscription_key)

    logger.info(f"非tick订阅成功: {stock_code} {period}, 订阅者: {subscriber_id}")
    return True

  async def _refresh_whole_quote_subscription(self) -> bool:
    """短暂合并同一波 tick 变更后刷新全推订阅。"""
    while True:
      loop = asyncio.get_running_loop()
      task = self._whole_quote_refresh_task
      if (
        task is None
        or task.done()
        or (hasattr(task, "get_loop") and task.get_loop() is not loop)
      ):
        task = loop.create_task(self._debounced_refresh_whole_quote_subscription())
        self._whole_quote_refresh_task = task

      success = await task
      if not success:
        return False

      with self.lock:
        current_stock_set = set(self.tick_subscriptions.keys())
        subscribed_stock_set = set(self._subscribed_stocks)

      if current_stock_set == subscribed_stock_set:
        return True

  async def _debounced_refresh_whole_quote_subscription(self) -> bool:
    await asyncio.sleep(self._whole_quote_refresh_debounce_seconds)
    return await self._refresh_whole_quote_subscription_now()

  async def _refresh_whole_quote_subscription_now(self) -> bool:
    """刷新全推订阅（智能变化检测）"""
    try:
      # 获取当前需要订阅的股票集合
      new_stock_set = set(self.tick_subscriptions.keys())

      # 如果没有tick订阅，取消全推订阅
      if not new_stock_set:
        if self.whole_quote_subscription_id is not None:
          self.data_manager.unsubscribe_whole_quote(self.whole_quote_subscription_id)
          self.whole_quote_subscription_id = None
          self._subscribed_stocks.clear()
          logger.info("取消全推订阅：无tick订阅")
        return True

      # 检查股票列表是否发生变化
      if new_stock_set == self._subscribed_stocks:
        logger.debug("股票列表无变化，跳过全推订阅刷新")
        return True

      # 股票列表有变化，执行刷新
      stock_codes = list(new_stock_set)
      logger.info(f"检测到股票列表变化，刷新全推订阅，股票数量: {len(stock_codes)}")

      # 创建新的全推订阅（传入股票代码列表）
      new_sub_id = self.data_manager.subscribe_whole_quote(
        stock_codes, callback=self._whole_quote_callback
      )

      if new_sub_id is not None:
        # 等待新订阅稳定
        await asyncio.sleep(0.1)

        # 取消旧订阅
        if self.whole_quote_subscription_id is not None:
          self.data_manager.unsubscribe_whole_quote(self.whole_quote_subscription_id)
          logger.info(f"取消旧全推订阅: {self.whole_quote_subscription_id}")

        # 更新订阅ID和已订阅股票集合
        self.whole_quote_subscription_id = new_sub_id
        self._subscribed_stocks = new_stock_set.copy()

        logger.info(
          f"全推订阅刷新成功，新订阅ID: {new_sub_id}, 股票数量: {len(stock_codes)}"
        )
        logger.debug(f"订阅股票列表: {stock_codes}")
        return True
      else:
        logger.error(f"全推订阅创建失败，股票列表: {stock_codes}")
        return False

    except Exception as e:
      logger.error(f"刷新全推订阅异常: {e}")
      return False

  def _invoke_sync_callback(self, callback: Callable, data: Dict, stock_code: str):
    try:
      callback(data)
    except Exception as e:
      logger.error(f"回调执行失败: {stock_code}, {e}")

  def _invoke_callback(self, callback: Callable, data: Dict, stock_code: str):
    """
    统一的回调调用方法，处理同步/异步回调

    Args:
        callback: 回调函数
        data: 要传递的数据
        stock_code: 股票代码（用于日志）
    """
    try:
      if asyncio.iscoroutinefunction(callback):
        # 异步回调：尝试在当前事件循环中创建任务
        try:
          asyncio.get_running_loop()
          asyncio.create_task(callback(data))
        except RuntimeError:
          # 没有运行的事件循环（XTQuant线程），使用保存的主循环
          main_loop = self._active_main_loop()
          if main_loop:
            asyncio.run_coroutine_threadsafe(callback(data), main_loop)
          else:
            logger.error(
              f"无法调度async回调: 主事件循环未设置或已关闭: {stock_code}. "
              f"请在启动时调用 set_main_loop()"
            )
      else:
        # 同步回调：若来自 XTQuant 工作线程，切回主事件循环执行。
        try:
          asyncio.get_running_loop()
        except RuntimeError:
          main_loop = self._active_main_loop()
          if main_loop:
            main_loop.call_soon_threadsafe(
              self._invoke_sync_callback,
              callback,
              data,
              stock_code,
            )
          else:
            logger.error(
              f"无法调度sync回调: 主事件循环未设置或已关闭: {stock_code}. "
              f"请在启动时调用 set_main_loop()"
            )
        else:
          self._invoke_sync_callback(callback, data, stock_code)
    except Exception as e:
      logger.error(f"回调执行失败: {stock_code}, {e}")

  def _whole_quote_callback(self, data: Dict):
    """全推数据回调，缓存数据并分发给已订阅的股票"""
    try:
      # 在锁保护下收集需要处理的数据和回调
      stocks_to_process = []

      with self.lock:
        for stock_code, tick_data in data.items():
          # 更新最新数据缓存（在锁保护下）
          self.latest_tick_data[stock_code] = tick_data

          # 只分发给已订阅的股票
          if stock_code in self.tick_subscriptions:
            tick_info = self.tick_subscriptions[stock_code]
            # 复制回调列表以避免在迭代时修改
            callbacks_to_invoke = list(tick_info.callbacks.copy())
            stocks_to_process.append(
              (stock_code, tick_data, tick_info, callbacks_to_invoke)
            )

      # 在锁外执行回调，避免死锁
      for stock_code, tick_data, tick_info, callbacks_to_invoke in stocks_to_process:
        for callback in callbacks_to_invoke:
          # 使用统一的回调调用方法
          self._invoke_callback(callback, {stock_code: tick_data}, stock_code)
    except Exception as e:
      logger.error(f"处理全推数据回调异常: {e}")

  def _schedule_period_data_callback(self, subscription_key: str, data) -> None:
    """把 XTQuant 非 tick 回调安全调度到主事件循环。"""
    try:
      loop = asyncio.get_running_loop()
      loop.create_task(self._handle_period_data_callback(subscription_key, data))
      return
    except RuntimeError:
      pass

    main_loop = self._active_main_loop()
    if main_loop:
      asyncio.run_coroutine_threadsafe(
        self._handle_period_data_callback(subscription_key, data),
        main_loop,
      )
      return

    logger.error(
      f"无法调度非tick回调: 主事件循环未设置或已关闭: {subscription_key}"
    )

  async def unsubscribe(
    self, subscriber_id: str, stock_code: str, period: str = "tick"
  ) -> bool:
    """
    取消订阅

    Args:
        subscriber_id: 订阅者ID
        stock_code: 股票代码
        period: 数据周期

    Returns:
        是否成功取消订阅
    """
    if self._is_tick_period(period):
      return await self._unsubscribe_tick(subscriber_id, stock_code)
    else:
      return await self._unsubscribe_period(subscriber_id, stock_code, period)

  async def _unsubscribe_tick(self, subscriber_id: str, stock_code: str) -> bool:
    """取消tick订阅"""
    should_remove_stock = False
    should_clean_cache = False

    with self.lock:
      if stock_code not in self.tick_subscriptions:
        logger.warning(f"tick订阅不存在: {stock_code}")
        return False

      tick_info = self.tick_subscriptions[stock_code]

      # 移除订阅者
      tick_info.subscriber_ids.discard(subscriber_id)

      # 清理订阅者管理
      if subscriber_id in self.tick_subscribers:
        self.tick_subscribers[subscriber_id].discard(stock_code)
        if not self.tick_subscribers[subscriber_id]:
          del self.tick_subscribers[subscriber_id]

      # 如果没有订阅者了，标记需要清理该股票
      if not tick_info.subscriber_ids:
        del self.tick_subscriptions[stock_code]
        should_remove_stock = True
        should_clean_cache = True
        logger.info(f"清理tick订阅: {stock_code}")

    # 清理缓存数据（如果股票完全移除）
    if should_clean_cache and stock_code in self.latest_tick_data:
      del self.latest_tick_data[stock_code]
      logger.debug(f"清理缓存数据: {stock_code}")

    # 只有股票完全移除时才刷新全推订阅（在锁外执行）
    if should_remove_stock:
      await self._refresh_whole_quote_subscription()
      logger.info(f"股票完全移除，触发全推刷新: {stock_code}")
    else:
      logger.info(f"股票仍有其他订阅者，无需刷新全推: {stock_code}")

    logger.info(f"取消tick订阅成功: {stock_code}, 订阅者: {subscriber_id}")
    return True

  async def _unsubscribe_period(
    self, subscriber_id: str, stock_code: str, period: str
  ) -> bool:
    """取消非tick订阅"""
    subscription_key = self._get_subscription_key(stock_code, period)

    with self.lock:
      if subscription_key not in self.period_subscriptions:
        logger.warning(f"非tick订阅不存在: {stock_code} {period}")
        return False

      subscription_info = self.period_subscriptions[subscription_key]

      # 移除订阅者
      subscription_info.subscriber_ids.discard(subscriber_id)

      # 清理订阅者管理
      if subscriber_id in self.period_subscribers:
        self.period_subscribers[subscriber_id].discard(subscription_key)
        if not self.period_subscribers[subscriber_id]:
          del self.period_subscribers[subscriber_id]

      # 如果没有订阅者了，取消 XTQuant 订阅
      if not subscription_info.subscriber_ids:
        try:
          self.data_manager.unsubscribe_quote(subscription_info.remote_subscription_id)
          del self.period_subscriptions[subscription_key]
          logger.info(
            f"取消XTQuant非tick订阅: {stock_code} {period}, "
            f"远端订阅ID: {subscription_info.remote_subscription_id}"
          )
        except Exception as e:
          logger.error(f"取消XTQuant非tick订阅失败: {stock_code} {period}, {e}")
          return False

    logger.info(f"取消非tick订阅成功: {stock_code} {period}, 订阅者: {subscriber_id}")
    return True

  async def unsubscribe_all(self, subscriber_id: str) -> bool:
    """
    取消某个订阅者的所有订阅

    Args:
        subscriber_id: 订阅者ID

    Returns:
        是否成功
    """
    success = True

    # 取消tick订阅
    if subscriber_id in self.tick_subscribers:
      stock_codes = self.tick_subscribers[subscriber_id].copy()
      for stock_code in stock_codes:
        if not await self._unsubscribe_tick(subscriber_id, stock_code):
          success = False

    # 取消非tick订阅
    if subscriber_id in self.period_subscribers:
      subscription_keys = self.period_subscribers[subscriber_id].copy()
      for subscription_key in subscription_keys:
        stock_code, period = subscription_key.split("_", 1)
        if not await self._unsubscribe_period(subscriber_id, stock_code, period):
          success = False

    return success

  async def shutdown(self) -> None:
    """关闭所有底层订阅并释放 XTQuant 数据连接。"""
    with self.lock:
      tick_subscriber_ids = list(self.tick_subscribers.keys())
      period_subscriber_ids = list(self.period_subscribers.keys())

    for subscriber_id in tick_subscriber_ids:
      await self.unsubscribe_all(subscriber_id)
    for subscriber_id in period_subscriber_ids:
      await self.unsubscribe_all(subscriber_id)

    with self.lock:
      period_subscriptions = list(self.period_subscriptions.values())
      whole_quote_subscription_id = self.whole_quote_subscription_id

    if whole_quote_subscription_id is not None:
      try:
        self.data_manager.unsubscribe_whole_quote(whole_quote_subscription_id)
      except Exception as exc:
        logger.warning(f"关闭全推订阅失败: {whole_quote_subscription_id}, {exc}")

    for subscription in period_subscriptions:
      try:
        self.data_manager.unsubscribe_quote(subscription.remote_subscription_id)
      except Exception as exc:
        logger.warning(
          f"关闭非tick订阅失败: {subscription.stock_code} {subscription.period}, {exc}"
        )

    with self.lock:
      self.whole_quote_subscription_id = None
      self._subscribed_stocks.clear()
      self.tick_subscriptions.clear()
      self.tick_subscribers.clear()
      self.period_subscriptions.clear()
      self.period_subscribers.clear()
      self.latest_tick_data.clear()
      self._whole_quote_refresh_task = None

    try:
      self.data_manager_registry.clear_all_managers()
      self._data_manager = None
    except Exception as exc:
      logger.warning(f"关闭 XTQuant 数据管理器失败: {exc}")

    with self._main_loop_lock:
      self._main_loop = None

  async def _handle_period_data_callback(self, subscription_key: str, data):
    """处理来自 XTQuant 的非tick数据回调"""
    try:
      if subscription_key not in self.period_subscriptions:
        logger.warning(f"收到数据但非tick订阅已不存在: {subscription_key}")
        return

      subscription_info = self.period_subscriptions[subscription_key]

      # 分发数据给所有注册的回调
      for callback in subscription_info.callbacks.copy():
        try:
          if asyncio.iscoroutinefunction(callback):
            await callback(data)
          else:
            callback(data)
        except Exception as e:
          logger.error(f"非tick回调执行失败: {subscription_key}, {e}")
          # 移除有问题的回调
          subscription_info.callbacks.discard(callback)

    except Exception as e:
      logger.error(f"处理非tick数据回调异常: {subscription_key}, {e}")

  def get_subscription_stats(self) -> Dict:
    """获取订阅统计信息"""
    with self.lock:
      tick_stats = {
        stock_code: {
          "stock_code": stock_code,
          "period": "tick",
          "subscription_type": "whole_quote",
          "callback_count": len(info.callbacks),
          "subscriber_count": len(info.subscriber_ids),
          "subscriber_ids": list(info.subscriber_ids),
        }
        for stock_code, info in self.tick_subscriptions.items()
      }

      period_stats = {
        key: {
          "stock_code": info.stock_code,
          "period": info.period,
          "subscription_type": "single_quote",
          "remote_subscription_id": info.remote_subscription_id,
          "callback_count": len(info.callbacks),
          "subscriber_count": len(info.subscriber_ids),
          "subscriber_ids": list(info.subscriber_ids),
        }
        for key, info in self.period_subscriptions.items()
      }

      return {
        "whole_quote_subscription_id": self.whole_quote_subscription_id,
        "total_tick_subscriptions": len(self.tick_subscriptions),
        "total_period_subscriptions": len(self.period_subscriptions),
        "total_tick_subscribers": len(self.tick_subscribers),
        "total_period_subscribers": len(self.period_subscribers),
        "tick_subscription_details": tick_stats,
        "period_subscription_details": period_stats,
      }

  def is_subscribed(self, stock_code: str, period: str = "tick") -> bool:
    """检查是否已订阅某个股票"""
    if self._is_tick_period(period):
      return stock_code in self.tick_subscriptions
    else:
      subscription_key = self._get_subscription_key(stock_code, period)
      return subscription_key in self.period_subscriptions


# 全局统一订阅管理器实例
unified_subscription_manager = UnifiedDataSubscriptionManager()
