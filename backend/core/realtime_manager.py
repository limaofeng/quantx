"""
实时数据管理器 - 为 GraphQL WebSocket 订阅提供实时数据服务

职责:
1. 管理来自 GraphQL 客户端的实时数据订阅请求
2. 通过统一订阅管理器与 XTQuant 数据源交互
3. 将数据转换为 GraphQL 标准格式
4. 通过 WebSocket 向多个客户端推送实时数据
5. 优化多客户端并发访问性能

不负责:
- 底层数据源连接管理(统一订阅管理器)
- 数据存储和持久化
- 业务逻辑处理
"""

import asyncio
import logging
from datetime import datetime
from typing import AsyncIterator, Dict, Set

from core.data.unified_subscription_manager import unified_subscription_manager
from models.kline import KLine
from models.market_depth import MarketDepth
from models.realtime_price import RealTimePrice
from models.tick import Tick
from core.utils import time_utils

logger = logging.getLogger(__name__)


class RealTimeDataManager:
  """
  实时数据管理器 - GraphQL WebSocket 订阅的实时数据服务(单例)

  特点:
  - 智能订阅合并: 多客户端订阅同一股票时共享底层数据流
  - 队列优化: 背压控制和自动清理防止内存泄漏
  - 连接统计: 监控每个连接的消息发送情况
  - 缓存首帧: 后加入的订阅者立即获取最新快照
  """

  def __init__(self):
    # GraphQL订阅者管理
    self.tick_subscribers: Dict[str, Set[asyncio.Queue]] = {}
    self.kline_subscribers: Dict[str, Set[asyncio.Queue]] = {}

    # 统一订阅管理器
    self.subscription_manager = unified_subscription_manager
    self.subscriber_id = "graphql_realtime_manager"

    # 股票名称缓存，减少重复查询
    self.stock_name_cache: Dict[str, str] = {}

    # 性能优化相关
    self.max_queue_size = 1000  # 最大队列大小，超出则丢弃旧数据
    self.batch_size = 10  # 批量处理大小
    self.connection_stats: Dict[str, Dict] = {}  # 连接统计信息

    # 智能合并相关
    self.subscription_ref_count: Dict[str, int] = {}  # 订阅引用计数

  async def start(self):
    """启动实时数据管理器"""
    # 设置主事件循环到统一订阅管理器
    self.subscription_manager.set_main_loop()
    logger.info("实时数据管理器已启动")

  async def stop(self):
    """停止实时数据管理器并清理所有订阅"""
    try:
      # 取消所有通过统一管理器的订阅
      await self.subscription_manager.unsubscribe_all(self.subscriber_id)

      # 清理事件循环引用,防止系统重启或事件循环重新创建时持有过期引用
      if hasattr(self.subscription_manager, "_main_loop"):
        self.subscription_manager._main_loop = None

      # 清理本地订阅者队列
      self.tick_subscribers.clear()
      self.kline_subscribers.clear()

      logger.info("实时数据管理器已停止")
    except Exception as e:
      logger.error(f"停止实时数据管理器时发生错误: {e}")

  def _create_optimized_queue(self, buffer_size: int = None) -> asyncio.Queue:
    """创建带背压控制的优化队列"""
    queue_size = buffer_size or self.max_queue_size
    return asyncio.Queue(maxsize=queue_size)

  def _update_connection_stats(
    self, connection_id: str, operation: str, success: bool = True
  ):
    """更新连接统计信息"""
    if connection_id not in self.connection_stats:
      self.connection_stats[connection_id] = {
        "messages_sent": 0,
        "messages_failed": 0,
        "last_activity": time_utils.now(),
        "start_time": time_utils.now(),
      }

    stats = self.connection_stats[connection_id]
    stats["last_activity"] = time_utils.now()

    if operation == "send":
      if success:
        stats["messages_sent"] += 1
      else:
        stats["messages_failed"] += 1

  async def _safe_queue_put(
    self, queue: asyncio.Queue, data: any, connection_id: str = None
  ) -> bool:
    """安全地向队列放入数据，支持背压控制"""
    try:
      # 非阻塞放入，如果队列满了则丢弃最旧的数据
      if queue.full():
        try:
          queue.get_nowait()  # 移除最旧的数据
          logger.warning(f"队列满，丢弃旧数据: {connection_id}")
        except asyncio.QueueEmpty:
          pass

      queue.put_nowait(data)
      if connection_id:
        self._update_connection_stats(connection_id, "send", True)
      return True

    except asyncio.QueueFull:
      logger.warning(f"队列仍然满，无法放入数据: {connection_id}")
      if connection_id:
        self._update_connection_stats(connection_id, "send", False)
      return False

  def get_connection_stats(self) -> Dict[str, Dict]:
    """获取连接统计信息"""
    return self.connection_stats.copy()

  def cleanup_stale_connections(self, max_idle_minutes: int = 30):
    """清理过期的连接统计"""
    cutoff_time = time_utils.now()
    cutoff_time = cutoff_time.replace(minute=cutoff_time.minute - max_idle_minutes)

    stale_connections = [
      conn_id
      for conn_id, stats in self.connection_stats.items()
      if stats["last_activity"] < cutoff_time
    ]

    for conn_id in stale_connections:
      del self.connection_stats[conn_id]
      logger.info(f"清理过期连接统计: {conn_id}")

    return len(stale_connections)

  async def subscribe_tick(self, stock_code: str) -> AsyncIterator[Tick]:
    """
    为 GraphQL 客户端订阅股票实时tick数据

    返回 RealTimePrice 格式的异步迭代器，用于 GraphQL WebSocket 推送
    """
    # 创建队列来接收数据
    queue = asyncio.Queue()

    # 添加到订阅列表
    if stock_code not in self.tick_subscribers:
      self.tick_subscribers[stock_code] = set()
    self.tick_subscribers[stock_code].add(queue)

    # 如果这是第一个订阅者，通过统一管理器订阅
    if len(self.tick_subscribers[stock_code]) == 1:

      async def data_callback(data):
        """数据回调处理"""
        await self._handle_xt_tick_data(stock_code, data)

      await self.subscription_manager.subscribe(
        stock_code=stock_code,
        callback=data_callback,
        subscriber_id=self.subscriber_id,
        period="tick",
      )

    # 统一管理器缓存首帧推送，确保后加入订阅者立即收到最新数据
    try:
      if len(self.tick_subscribers[stock_code]) > 1:
        latest_tick_raw = self.subscription_manager.get_latest_tick(stock_code)
        if latest_tick_raw:
          latest_tick = (
            latest_tick_raw[-1]
            if isinstance(latest_tick_raw, list)
            else latest_tick_raw
          )
          tick_snapshot = Tick.from_xtquant(stock_code, latest_tick)
          await self._safe_queue_put(
            queue, tick_snapshot, f"tick_snapshot_{stock_code}"
          )
    except Exception as snapshot_err:
      logger.warning(f"推送缓存tick首帧失败: {stock_code}, {snapshot_err}")

    try:
      logger.info(f"新增GraphQL tick订阅: {stock_code}")
      while True:
        try:
          price_data = await asyncio.wait_for(queue.get(), timeout=1.0)
          yield price_data
        except asyncio.TimeoutError:
          # 超时后继续循环,允许检查取消状态
          continue
    except asyncio.CancelledError:
      logger.info(f"取消GraphQL tick订阅: {stock_code}")
    finally:
      # 清理订阅
      if stock_code in self.tick_subscribers:
        self.tick_subscribers[stock_code].discard(queue)
        if not self.tick_subscribers[stock_code]:
          # 如果没有更多订阅者，取消统一管理器订阅
          await self.subscription_manager.unsubscribe(
            self.subscriber_id, stock_code, "tick"
          )
          del self.tick_subscribers[stock_code]

  async def subscribe_price(self, stock_code: str) -> AsyncIterator[RealTimePrice]:
    """
    为 GraphQL 客户端订阅股票实时价格

    返回专门的 RealTimePrice 领域模型，包含计算的涨跌信息
    """
    async for tick_data in self.subscribe_tick(stock_code):
      # 从 tick 数据转换为实时价格
      price_data = RealTimePrice.from_tick(tick_data)
      yield price_data

  async def subscribe_kline(
    self, stock_code: str, period: str = "1m"
  ) -> AsyncIterator[KLine]:
    """
    为 GraphQL 客户端订阅K线数据

    返回 KLineData 格式的异步迭代器，用于 GraphQL WebSocket 推送
    """
    key = f"{stock_code}_{period}"

    # 创建队列来接收数据
    queue = asyncio.Queue()

    # 添加到订阅列表
    if key not in self.kline_subscribers:
      self.kline_subscribers[key] = set()
    self.kline_subscribers[key].add(queue)

    # 如果这是第一个订阅者，通过统一管理器订阅
    if len(self.kline_subscribers[key]) == 1:

      async def data_callback(data):
        """数据回调处理"""
        await self._handle_xt_kline_data(stock_code, period, data)

      await self.subscription_manager.subscribe(
        stock_code=stock_code,
        callback=data_callback,
        subscriber_id=self.subscriber_id,
        period=period,
      )

    try:
      logger.info(f"新增GraphQL K线订阅: {stock_code} {period}")
      while True:
        # 使用超时检查取消状态，避免无限等待
        try:
          kline_data = await asyncio.wait_for(queue.get(), timeout=1.0)
          yield kline_data
        except asyncio.TimeoutError:
          # 超时后继续循环，允许检查取消状态
          continue
    except (asyncio.CancelledError, GeneratorExit):
      logger.info(f"取消GraphQL K线订阅: {stock_code} {period}")
      raise  # 重新抛出以确保正常清理
    finally:
      # 清理订阅
      if key in self.kline_subscribers:
        self.kline_subscribers[key].discard(queue)
        if not self.kline_subscribers[key]:
          # 如果没有更多订阅者，取消统一管理器订阅
          await self.subscription_manager.unsubscribe(
            self.subscriber_id, stock_code, period
          )
          del self.kline_subscribers[key]

  async def subscribe_depth(self, stock_code: str) -> AsyncIterator[MarketDepth]:
    """
    为 GraphQL 客户端订阅市场深度数据

    基于 tick 数据流提取深度信息，避免重复订阅底层数据源
    """
    from models.market_depth import MarketDepthLevel

    async for tick_data in self.subscribe_tick(stock_code):
      # 从 tick 数据中提取市场深度信息
      bid_levels = []
      ask_levels = []

      # 买盘档位（按价格从高到低排序）
      for i in range(min(5, len(tick_data.bid_price), len(tick_data.bid_vol))):
        if tick_data.bid_price[i] > 0 and tick_data.bid_vol[i] > 0:
          bid_levels.append(
            MarketDepthLevel(
              price=round(tick_data.bid_price[i], 2), volume=int(tick_data.bid_vol[i])
            )
          )

      # 卖盘档位（按价格从低到高排序）
      for i in range(min(5, len(tick_data.ask_price), len(tick_data.ask_vol))):
        if tick_data.ask_price[i] > 0 and tick_data.ask_vol[i] > 0:
          ask_levels.append(
            MarketDepthLevel(
              price=round(tick_data.ask_price[i], 2), volume=int(tick_data.ask_vol[i])
            )
          )

      # 构建市场深度数据
      depth_data = MarketDepth(
        stock_code=tick_data.stock_code,
        time=tick_data.time,
        bid_levels=bid_levels,
        ask_levels=ask_levels,
      )

      yield depth_data

  def _get_stock_name(self, stock_code: str) -> str:
    """获取股票名称"""
    if stock_code in self.stock_name_cache:
      return self.stock_name_cache[stock_code]

    try:
      # 使用统一管理器的数据管理器获取股票详情
      data_manager = self.subscription_manager.data_manager
      instrument_detail = data_manager.get_instrument_detail(stock_code)
      if instrument_detail and "InstrumentName" in instrument_detail:
        name = instrument_detail["InstrumentName"]
        self.stock_name_cache[stock_code] = name
        return name
    except Exception as e:
      logger.warning(f"获取股票名称失败: {stock_code}, {e}")

    # 返回股票代码作为默认值
    return stock_code

  async def _handle_xt_tick_data(self, stock_code: str, data: dict):
    """处理来自XTQuant的实时数据回调并转换为Tick领域模型"""
    try:
      # 解析XTQuant返回的数据格式
      if not data or stock_code not in data:
        logger.warning(f"收到空数据或不包含股票代码: {stock_code}")
        return

      tick_list = data[stock_code]
      if not tick_list:
        return

      # 取最新的tick数据
      latest_tick = tick_list[-1] if isinstance(tick_list, list) else tick_list

      # 构建Tick领域模型对象
      tick_data = Tick.from_xtquant(stock_code, latest_tick)

      # 推送给所有订阅者（使用优化的队列放入方法）
      if stock_code in self.tick_subscribers:
        dead_queues = set()
        for queue in self.tick_subscribers[stock_code].copy():
          success = await self._safe_queue_put(queue, tick_data, f"tick_{stock_code}")
          if not success:
            dead_queues.add(queue)

        # 清理失效的队列
        for queue in dead_queues:
          self.tick_subscribers[stock_code].discard(queue)
          logger.warning(f"tick队列失效，移除订阅者: {stock_code}")

    except Exception as e:
      import traceback

      traceback.print_exc()
      logger.error(f"处理XTQuant tick数据回调失败: {stock_code}, {e}")

  async def _handle_xt_kline_data(self, stock_code: str, period: str, data: dict):
    """处理来自XTQuant的K线数据回调并转换为KLine领域模型"""
    try:
      key = f"{stock_code}_{period}"

      if not data or stock_code not in data:
        logger.warning(f"收到空K线数据或不包含股票代码: {stock_code}")
        return

      kline_list = data[stock_code]
      if not kline_list:
        return

      # 取最新的K线数据
      latest_kline = kline_list[-1] if isinstance(kline_list, list) else kline_list

      # 构建KLine领域模型对象
      kline_data = KLine.from_xtquant(stock_code, period, latest_kline)

      # 推送给所有订阅者（使用优化的队列放入方法）
      if key in self.kline_subscribers:
        dead_queues = set()
        for queue in self.kline_subscribers[key].copy():
          success = await self._safe_queue_put(queue, kline_data, f"kline_{key}")
          if not success:
            dead_queues.add(queue)

        # 清理失效的队列
        for queue in dead_queues:
          self.kline_subscribers[key].discard(queue)
          logger.warning(f"K线队列失效，移除订阅者: {key}")

    except Exception as e:
      logger.error(f"处理XTQuant K线数据回调失败: {stock_code} {period}, {e}")


# 全局实时数据管理器实例
realtime_manager = RealTimeDataManager()
