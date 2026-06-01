"""
交易事件管理器

提供事件发布订阅机制,支持订单、成交、持仓、账户变动的实时推送。
采用单例模式,全局唯一实例。
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from typing import AsyncIterator, Dict, List, Optional, Set

from core.utils import time_utils
from .types import (
  OrderEvent,
  TradingEventType,
  TradingEventUnion,
)

logger = logging.getLogger(__name__)


class TradingEventManager:
  """
  交易事件管理器 - 单例模式 (个人量化软件专用)

  特点:
  - 事件驱动架构: 基于异步队列的发布订阅
  - 灵活过滤: 支持按事件类型、股票代码、策略名称过滤
  - 性能优化: 背压控制、订阅者隔离、自动清理
  - 可扩展: 易于添加新的事件类型
  - 简化设计: 无多账户概念,专注个人量化交易场景
  """

  _instance = None

  def __new__(cls):
    if cls._instance is None:
      cls._instance = super().__new__(cls)
      cls._instance._initialized = False
    return cls._instance

  def __init__(self):
    if self._initialized:
      return

    # 订阅者管理: event_type -> set of queues
    self.subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)

    # 全局订阅者 (订阅所有事件)
    self.global_subscribers: Set[asyncio.Queue] = set()

    # 订阅者元数据: queue -> metadata
    self.subscriber_metadata: Dict[asyncio.Queue, Dict] = {}

    # 性能配置
    self.max_queue_size = 1000  # 最大队列大小
    self.cleanup_interval = 300  # 清理间隔 (秒)

    # 统计信息
    self.stats = {
      "events_published": 0,
      "events_dropped": 0,
      "active_subscribers": 0,
    }

    self._initialized = True
    logger.info("TradingEventManager 初始化完成")

  async def publish(
    self,
    event_type: TradingEventType,
    event: TradingEventUnion,
  ) -> None:
    """
    发布交易事件

    Args:
        event_type: 事件类型
        event: 事件对象
    """
    try:
      # 更新统计
      self.stats["events_published"] += 1

      # 获取需要推送的订阅者
      target_queues = set()

      # 添加全局订阅者
      target_queues.update(self.global_subscribers)

      # 添加订阅了该事件类型的订阅者
      if event_type.value in self.subscribers:
        target_queues.update(self.subscribers[event_type.value])

      # 推送到所有目标订阅者
      dropped_count = 0
      for queue in target_queues:
        # 检查是否需要过滤
        metadata = self.subscriber_metadata.get(queue, {})
        if not self._should_deliver(event, metadata):
          continue

        # 尝试推送到队列
        success = await self._safe_queue_put(queue, event)
        if not success:
          dropped_count += 1

      if dropped_count > 0:
        self.stats["events_dropped"] += dropped_count
        logger.warning(
          f"事件 {event_type.value} 有 {dropped_count} 个订阅者队列满,已丢弃旧数据"
        )

    except Exception as e:
      logger.error(f"发布事件失败: {e}", exc_info=True)

  async def subscribe(
    self,
    event_types: Optional[List[TradingEventType]] = None,
    stock_codes: Optional[List[str]] = None,
    strategy_names: Optional[List[str]] = None,
  ) -> AsyncIterator[TradingEventUnion]:
    """
    订阅交易事件 (个人量化软件专用)

    Args:
        event_types: 订阅的事件类型列表 (None 表示订阅所有)
        stock_codes: 股票代码过滤列表 (用于自选股监控)
        strategy_names: 策略名称过滤列表 (用于策略监控)

    Yields:
        交易事件对象
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=self.max_queue_size)

    # 保存订阅者元数据
    metadata = {
      "event_types": event_types,
      "stock_codes": stock_codes,
      "strategy_names": strategy_names,
      "created_at": time_utils.now(),
      "last_activity": time_utils.now(),
    }
    self.subscriber_metadata[queue] = metadata

    try:
      # 注册订阅者
      if event_types is None:
        # 订阅所有事件
        self.global_subscribers.add(queue)
      else:
        # 订阅指定事件类型
        for event_type in event_types:
          self.subscribers[event_type.value].add(queue)

      self.stats["active_subscribers"] = len(self.subscriber_metadata)
      logger.info(
        f"新增订阅者: event_types={event_types}, filters={{stock_codes={stock_codes}, strategy_names={strategy_names}}}"
      )

      # 持续推送事件
      while True:
        event = await queue.get()

        # 更新活跃时间
        metadata["last_activity"] = time_utils.now()

        yield event

    except asyncio.CancelledError:
      logger.info("订阅被取消")
      raise
    except Exception as e:
      logger.error(f"订阅处理出错: {e}", exc_info=True)
      raise
    finally:
      # 清理订阅者
      self._remove_subscriber(queue, event_types)

  def _should_deliver(
    self,
    event: TradingEventUnion,
    metadata: Dict,
  ) -> bool:
    """
    判断是否应该推送事件给订阅者 (个人量化软件专用)

    Args:
        event: 事件对象
        metadata: 订阅者元数据

    Returns:
        是否应该推送
    """
    # 股票代码过滤 (用于自选股监控)
    if metadata.get("stock_codes"):
      stock_code = None
      if isinstance(event, OrderEvent):
        stock_code = event.order.stock_code

      if stock_code and stock_code not in metadata["stock_codes"]:
        return False

    # 策略名称过滤 (用于策略监控)
    if metadata.get("strategy_names"):
      strategy_name = None
      if isinstance(event, OrderEvent):
        strategy_name = event.order.strategy_name

      if strategy_name and strategy_name not in metadata["strategy_names"]:
        return False

    return True

  async def _safe_queue_put(
    self,
    queue: asyncio.Queue,
    event: TradingEventUnion,
  ) -> bool:
    """
    安全地向队列推送数据,支持背压控制

    Args:
        queue: 目标队列
        event: 事件对象

    Returns:
        是否推送成功
    """
    try:
      if queue.full():
        # 队列满,丢弃最旧的数据
        try:
          queue.get_nowait()
          logger.warning("订阅者队列满,丢弃旧数据")
        except asyncio.QueueEmpty:
          pass

      queue.put_nowait(event)
      return True
    except Exception as e:
      logger.error(f"推送到队列失败: {e}")
      return False

  def _remove_subscriber(
    self,
    queue: asyncio.Queue,
    event_types: Optional[List[TradingEventType]],
  ) -> None:
    """
    移除订阅者

    Args:
        queue: 订阅者队列
        event_types: 订阅的事件类型
    """
    try:
      # 从全局订阅者移除
      self.global_subscribers.discard(queue)

      # 从特定事件类型订阅者移除
      if event_types:
        for event_type in event_types:
          if event_type.value in self.subscribers:
            self.subscribers[event_type.value].discard(queue)

      # 移除元数据
      self.subscriber_metadata.pop(queue, None)

      self.stats["active_subscribers"] = len(self.subscriber_metadata)
      logger.info("订阅者已移除")

    except Exception as e:
      logger.error(f"移除订阅者失败: {e}")

  async def cleanup_stale_subscribers(self, max_idle_minutes: int = 30) -> int:
    """
    清理过期的订阅者

    Args:
        max_idle_minutes: 最大空闲时间 (分钟)

    Returns:
        清理的订阅者数量
    """
    from datetime import timedelta

    cutoff_time = time_utils.now() - timedelta(minutes=max_idle_minutes)
    stale_queues = []

    for queue, metadata in self.subscriber_metadata.items():
      if metadata.get("last_activity", time_utils.now()) < cutoff_time:
        stale_queues.append((queue, metadata.get("event_types")))

    for queue, event_types in stale_queues:
      self._remove_subscriber(queue, event_types)

    if stale_queues:
      logger.info(f"清理了 {len(stale_queues)} 个过期订阅者")

    return len(stale_queues)

  def get_stats(self) -> Dict:
    """获取统计信息"""
    return {
      **self.stats,
      "subscribers_by_type": {
        event_type: len(queues) for event_type, queues in self.subscribers.items()
      },
      "global_subscribers": len(self.global_subscribers),
    }


# 全局单例实例
trading_event_manager = TradingEventManager()
