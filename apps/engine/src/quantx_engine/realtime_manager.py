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
import math
from datetime import datetime, time, timedelta
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.core.data.unified_subscription_manager import (
  unified_subscription_manager,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.models.market_depth import MarketDepth
from quantx_infrastructure.models.realtime_price import RealTimePrice
from quantx_infrastructure.models.tick import Tick
from quantx_infrastructure.services.divid_factor_service import DividFactorService
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
)
from quantx_infrastructure.services.trading_time_service import TradingTimeService

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
    self.tick_minute_klines: Dict[str, Dict[str, Any]] = {}
    self.previous_daily_close_cache: Dict[str, Dict[str, Any]] = {}
    self.tick_generated_kline_save_interval_seconds = max(
      0.0,
      float(
        getattr(settings, "realtime_generated_kline_save_interval_seconds", 10.0)
        or 0.0
      ),
    )
    self.historical_market_data_service = HistoricalMarketDataService()
    self.divid_factor_service = DividFactorService()
    self.trading_time_service = TradingTimeService()
    self._started_loop: Optional[asyncio.AbstractEventLoop] = None

  def _previous_daily_close_cache_key(
    self, stock_code: str, tick_time: datetime
  ) -> str:
    tick_date = time_utils.to_shanghai(tick_time).date()
    return f"{stock_code}:{tick_date.isoformat()}"

  async def start(self):
    """启动实时数据管理器"""
    loop = asyncio.get_running_loop()
    if self._started_loop is loop:
      return

    # 设置主事件循环到统一订阅管理器
    self.subscription_manager.set_main_loop(loop)
    self._started_loop = loop
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
      self._started_loop = None

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

      subscribed = await self.subscription_manager.subscribe(
        stock_code=stock_code,
        callback=data_callback,
        subscriber_id=self.subscriber_id,
        period="tick",
      )
      if not subscribed:
        self.tick_subscribers[stock_code].discard(queue)
        if not self.tick_subscribers[stock_code]:
          del self.tick_subscribers[stock_code]
        raise RuntimeError(f"底层tick订阅失败: {stock_code}")

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
          tick_snapshot = await self._normalize_tick_pre_close(
            stock_code, tick_snapshot
          )
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

  async def publish_tick_backfill(
    self, stock_code: str, ticks: List[Tick], source: str = "gap_fill"
  ) -> int:
    """Push downloaded historical tick gap data to active GraphQL subscribers."""
    if not ticks or stock_code not in self.tick_subscribers:
      return 0

    delivered = 0
    for tick in sorted(ticks, key=lambda item: item.time):
      dead_queues = set()
      for queue in self.tick_subscribers[stock_code].copy():
        success = await self._safe_queue_put(
          queue, tick, f"tick_backfill_{stock_code}"
        )
        if success:
          delivered += 1
        else:
          dead_queues.add(queue)

      for queue in dead_queues:
        self.tick_subscribers[stock_code].discard(queue)

    if delivered:
      if source == "warm_cache_initial":
        logger.info("推送tick热缓存初始化数据: %s, ticks=%s", stock_code, delivered)
      else:
        logger.info("推送tick补全数据: %s, ticks=%s", stock_code, delivered)

    return delivered

  async def publish_kline_backfill(
    self,
    stock_code: str,
    period: str,
    klines: List[KLine],
    source: str = "gap_fill",
  ) -> int:
    """Push downloaded 1m K-line gap data to active GraphQL subscribers."""
    key = f"{stock_code}_{period}"
    if not klines or key not in self.kline_subscribers:
      return 0

    delivered = 0
    for kline in sorted(klines, key=lambda item: item.time):
      dead_queues = set()
      for queue in self.kline_subscribers[key].copy():
        success = await self._safe_queue_put(
          queue, kline, f"kline_backfill_{key}"
        )
        if success:
          delivered += 1
        else:
          dead_queues.add(queue)

      for queue in dead_queues:
        self.kline_subscribers[key].discard(queue)

    if delivered:
      if source == "warm_cache_initial":
        logger.info(
          "推送K线热缓存初始化数据: %s %s, klines=%s",
          stock_code,
          period,
          delivered,
        )
      else:
        logger.info("推送K线补全数据: %s %s, klines=%s", stock_code, period, delivered)

    return delivered

  def _tick_price(self, tick: Tick) -> Optional[float]:
    for value in [
      getattr(tick, "last_price", None),
      getattr(tick, "open", None),
      getattr(tick, "high", None),
      getattr(tick, "low", None),
    ]:
      try:
        price = float(value)
      except (TypeError, ValueError):
        continue
      if price > 0:
        return price
    return None

  def _minute_start(self, value: datetime) -> datetime:
    return time_utils.to_shanghai(value).replace(second=0, microsecond=0)

  def _is_intraday_1m_minute(self, value: datetime) -> bool:
    minute = self._minute_start(value)
    minutes = minute.hour * 60 + minute.minute
    return (
      9 * 60 + 15 <= minutes <= 9 * 60 + 25
      or 9 * 60 + 30 <= minutes <= 11 * 60 + 30
      or 13 * 60 <= minutes <= 15 * 60
    )

  async def _get_previous_daily_close(
    self, stock_code: str, tick_time: datetime
  ) -> Optional[float]:
    tick_date = time_utils.to_shanghai(tick_time).date()
    cache_key = self._previous_daily_close_cache_key(stock_code, tick_time)
    cached = self.previous_daily_close_cache.get(cache_key)
    if cached is not None:
      return cached.get("close")

    market = stock_code.split(".")[-1] if "." in stock_code else "SH"
    try:
      previous_trading_date = await self.trading_time_service.get_previous_trading_day(
        market, tick_date
      )
      start_time = datetime.combine(previous_trading_date, time.min)
      end_time = start_time + timedelta(days=1)
      klines = await self.historical_market_data_service.get_kline_data(
        stock_code=stock_code,
        period="1d",
        start_time=start_time,
        end_time=end_time,
        limit=None,
        order="asc",
        dividend_type="none",
      )
    except Exception as exc:
      logger.warning("查询昨日收盘日K失败: %s, %s", stock_code, exc)
      self.previous_daily_close_cache[cache_key] = {"close": None}
      return None

    previous_close = None
    for kline in klines:
      try:
        kline_date = time_utils.to_shanghai(kline.time).date()
        close = float(getattr(kline, "close", 0.0) or 0.0)
      except Exception:
        continue
      if kline_date == previous_trading_date and close > 0:
        previous_close = close
        break

    if previous_close is None:
      logger.warning(
        "时间序列库缺少上一交易日日K: %s, trading_date=%s",
        stock_code,
        previous_trading_date.isoformat(),
      )
    else:
      previous_close = await self._front_adjust_previous_daily_close(
        stock_code,
        previous_close,
        previous_trading_date,
        tick_date,
      )

    self.previous_daily_close_cache[cache_key] = {"close": previous_close}
    return previous_close

  async def _front_adjust_previous_daily_close(
    self,
    stock_code: str,
    previous_close: float,
    previous_trading_date,
    tick_date,
  ) -> float:
    try:
      factors = await asyncio.wait_for(
        self.divid_factor_service.get_divid_factors(
          stock_code=stock_code,
          start_time=datetime.combine(
            previous_trading_date + timedelta(days=1), time.min
          ),
          end_time=datetime.combine(tick_date, time.max),
          limit=None,
        ),
        timeout=1.0,
      )
    except Exception as exc:
      logger.warning("查询前复权因子失败: %s, %s", stock_code, exc)
      return previous_close

    adjust_factor = 1.0
    for factor in factors:
      try:
        factor_date = time_utils.to_shanghai(factor.time).date()
        dr = float(getattr(factor, "dr", 0.0) or 0.0)
      except Exception:
        continue
      if previous_trading_date < factor_date <= tick_date and dr > 0:
        adjust_factor /= dr

    return previous_close * adjust_factor

  async def _normalize_tick_pre_close(self, stock_code: str, tick: Tick) -> Tick:
    previous_close = await self._get_previous_daily_close(stock_code, tick.time)
    if previous_close and previous_close > 0:
      tick.last_close = previous_close
      return tick

    try:
      native_pre_close = float(getattr(tick, "last_close", 0.0) or 0.0)
    except (TypeError, ValueError):
      native_pre_close = 0.0
    if native_pre_close <= 0:
      return tick

    cache_key = self._previous_daily_close_cache_key(stock_code, tick.time)
    self.previous_daily_close_cache[cache_key] = {
      "close": native_pre_close,
      "source": "tick",
    }
    logger.info(
      "昨日收盘价使用tick兜底: %s, tick_time=%s, last_close=%s",
      stock_code,
      tick.time,
      native_pre_close,
    )
    return tick

  def _build_tick_generated_kline(self, stock_code: str, tick: Tick) -> Optional[KLine]:
    price = self._tick_price(tick)
    if price is None or not self._is_intraday_1m_minute(tick.time):
      return None

    minute = self._minute_start(tick.time)
    state = self.tick_minute_klines.get(stock_code)
    tick_volume = max(0.0, float(getattr(tick, "volume", 0.0) or 0.0))
    tick_amount = max(0.0, float(getattr(tick, "amount", 0.0) or 0.0))

    if state and minute < state["minute"]:
      return None

    if not state or minute > state["minute"]:
      observed_tick_volume = max(0.0, float(getattr(tick, "tickvol", 0.0) or 0.0))
      if state:
        base_volume = max(0.0, float(state.get("last_cumulative_volume", 0.0)))
        base_amount = max(0.0, float(state.get("last_cumulative_amount", 0.0)))
      else:
        base_volume = max(0.0, tick_volume - observed_tick_volume)
        base_amount = max(0.0, tick_amount - observed_tick_volume * price)

      kline = KLine(
        stock_code=stock_code,
        period="1m",
        time=minute,
        open=price,
        high=price,
        low=price,
        close=price,
        pre_close=float(getattr(tick, "last_close", 0.0) or 0.0),
        volume=max(0.0, tick_volume - base_volume),
        amount=max(0.0, tick_amount - base_amount),
        settelement_price=0.0,
        open_interest=int(getattr(tick, "open_int", 0) or 0),
        suspend_flag=int(getattr(tick, "stock_status", 0) or 0),
      )
      state = {
        "base_amount": base_amount,
        "base_volume": base_volume,
        "kline": kline,
        "last_saved_at": None,
        "last_saved_minute": None,
        "last_cumulative_amount": tick_amount,
        "last_cumulative_volume": tick_volume,
        "minute": minute,
      }
      self.tick_minute_klines[stock_code] = state
      return kline

    kline = state["kline"]
    kline.high = max(float(kline.high), price)
    kline.low = min(float(kline.low), price)
    kline.close = price
    kline.pre_close = float(
      getattr(tick, "last_close", kline.pre_close) or kline.pre_close
    )
    kline.volume = max(0.0, tick_volume - float(state.get("base_volume", 0.0)))
    kline.amount = max(0.0, tick_amount - float(state.get("base_amount", 0.0)))
    kline.open_interest = int(getattr(tick, "open_int", kline.open_interest) or 0)
    kline.suspend_flag = int(getattr(tick, "stock_status", kline.suspend_flag) or 0)
    state["last_cumulative_amount"] = tick_amount
    state["last_cumulative_volume"] = tick_volume
    return kline

  def _copy_kline(self, kline: KLine) -> KLine:
    return KLine(
      stock_code=kline.stock_code,
      period=kline.period,
      time=kline.time,
      open=kline.open,
      high=kline.high,
      low=kline.low,
      close=kline.close,
      pre_close=kline.pre_close,
      volume=kline.volume,
      amount=kline.amount,
      settelement_price=kline.settelement_price,
      open_interest=kline.open_interest,
      suspend_flag=kline.suspend_flag,
    )

  async def _safe_save_tick_generated_kline(self, kline: KLine) -> None:
    try:
      await asyncio.to_thread(self.historical_market_data_service.save_kline, kline)
    except Exception as exc:
      logger.warning(
        "保存tick生成1m K线失败: %s %s %s, %s",
        kline.stock_code,
        kline.period,
        kline.time,
        exc,
      )

  def _should_save_tick_generated_kline(
    self, stock_code: str, tick: Tick, kline: KLine
  ) -> bool:
    state = self.tick_minute_klines.get(stock_code)
    if not state:
      return True

    if state.get("last_saved_minute") != kline.time:
      return True

    last_saved_at = state.get("last_saved_at")
    if last_saved_at is None:
      return True

    if self.tick_generated_kline_save_interval_seconds <= 0:
      return True

    tick_time = self._minute_start(tick.time)
    try:
      tick_time = time_utils.to_shanghai(tick.time)
    except Exception:
      pass

    return (
      tick_time - last_saved_at
    ).total_seconds() >= self.tick_generated_kline_save_interval_seconds

  def _mark_tick_generated_kline_save_attempt(
    self, stock_code: str, tick: Tick, kline: KLine
  ) -> None:
    state = self.tick_minute_klines.get(stock_code)
    if not state:
      return

    try:
      state["last_saved_at"] = time_utils.to_shanghai(tick.time)
    except Exception:
      state["last_saved_at"] = self._minute_start(tick.time)
    state["last_saved_minute"] = kline.time

  async def _publish_kline_to_subscribers(self, key: str, kline: KLine) -> None:
    if key not in self.kline_subscribers:
      return

    dead_queues = set()
    for queue in self.kline_subscribers[key].copy():
      success = await self._safe_queue_put(queue, kline, f"kline_{key}")
      if not success:
        dead_queues.add(queue)

    for queue in dead_queues:
      self.kline_subscribers[key].discard(queue)
      logger.warning(f"K线队列失效，移除订阅者: {key}")

  async def _handle_tick_generated_1m(self, stock_code: str, tick: Tick) -> None:
    previous_state = self.tick_minute_klines.get(stock_code)
    previous_minute = previous_state.get("minute") if previous_state else None
    previous_kline = (
      self._copy_kline(previous_state["kline"])
      if previous_state and previous_state.get("kline") is not None
      else None
    )

    kline = self._build_tick_generated_kline(stock_code, tick)
    if kline is None:
      return

    kline_snapshot = self._copy_kline(kline)
    from .warm_cache import intraday_warm_cache

    intraday_warm_cache.store_kline(kline_snapshot)
    await self._publish_kline_to_subscribers(f"{stock_code}_1m", kline_snapshot)

    save_candidates: List[KLine] = []
    if previous_kline is not None and previous_minute is not None:
      if kline_snapshot.time > previous_minute:
        save_candidates.append(previous_kline)

    if self._should_save_tick_generated_kline(stock_code, tick, kline_snapshot):
      self._mark_tick_generated_kline_save_attempt(stock_code, tick, kline_snapshot)
      save_candidates.append(kline_snapshot)

    for candidate in save_candidates:
      await self._safe_save_tick_generated_kline(candidate)

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

      subscribed = await self.subscription_manager.subscribe(
        stock_code=stock_code,
        callback=data_callback,
        subscriber_id=self.subscriber_id,
        period=period,
      )
      if not subscribed:
        self.kline_subscribers[key].discard(queue)
        if not self.kline_subscribers[key]:
          del self.kline_subscribers[key]
        raise RuntimeError(f"底层K线订阅失败: {stock_code} {period}")

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
    from quantx_infrastructure.models.market_depth import MarketDepthLevel

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
      tick_data = await self._normalize_tick_pre_close(stock_code, tick_data)
      from .warm_cache import (
        intraday_warm_cache,
      )

      intraday_warm_cache.store_tick(tick_data)
      from quantx_infrastructure.services.latest_market_quote_cache import (
        latest_market_quote_cache,
      )

      latest_market_quote_cache.stage_tick(tick_data)

      await self._handle_tick_generated_1m(stock_code, tick_data)

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

      raw_klines = kline_list if isinstance(kline_list, list) else [kline_list]
      trading_date = time_utils.today()
      deduped: Dict[datetime, KLine] = {}
      invalid_count = 0
      for raw_kline in raw_klines:
        if not isinstance(raw_kline, dict):
          invalid_count += 1
          continue
        try:
          timestamp = float(raw_kline.get("time"))
        except (TypeError, ValueError):
          invalid_count += 1
          continue
        if not math.isfinite(timestamp) or timestamp <= 0:
          invalid_count += 1
          continue

        try:
          kline_data = KLine.from_xtquant(
            stock_code,
            period,
            {**raw_kline, "time": timestamp},
          )
          prices = [
            float(kline_data.open),
            float(kline_data.high),
            float(kline_data.low),
            float(kline_data.close),
          ]
          volume = float(kline_data.volume)
          amount = float(kline_data.amount)
        except (TypeError, ValueError, OverflowError):
          invalid_count += 1
          continue
        if (
          any(not math.isfinite(value) or value <= 0 for value in prices)
          or not math.isfinite(volume)
          or volume < 0
          or not math.isfinite(amount)
          or amount < 0
          or kline_data.high < max(kline_data.open, kline_data.close)
          or kline_data.low > min(kline_data.open, kline_data.close)
        ):
          invalid_count += 1
          continue

        kline_time = time_utils.to_shanghai(kline_data.time)
        if period == "1m" and kline_time.date() != trading_date:
          continue
        deduped[kline_time] = kline_data

      if invalid_count:
        logger.warning(
          "忽略无效XTQuant K线: %s %s invalid=%s total=%s",
          stock_code,
          period,
          invalid_count,
          len(raw_klines),
        )
      if not deduped:
        return

      klines = [deduped[item] for item in sorted(deduped)]
      if period == "1m":
        from .warm_cache import (
          intraday_warm_cache,
        )

        for kline_data in klines:
          intraday_warm_cache.store_kline(kline_data)

      # 首帧可能包含当日完整分钟序列，必须逐根推送；后续单根增量沿用同一路径。
      for kline_data in klines:
        await self._publish_kline_to_subscribers(key, kline_data)

    except Exception as e:
      logger.error(f"处理XTQuant K线数据回调失败: {stock_code} {period}, {e}")


# 全局实时数据管理器实例
realtime_manager = RealTimeDataManager()
