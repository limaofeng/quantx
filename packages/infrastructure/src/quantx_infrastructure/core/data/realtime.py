"""
实时数据适配器 - 接入 WebSocket 实时数据流
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol

import pandas as pd

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.models.tick import Tick
from quantx_infrastructure.services.trading_time_service import TradingTimeService

from .adapter import DataAdapter, DataMode, DataSubscription
from .unified_subscription_manager import unified_subscription_manager


class IntradayWarmCache(Protocol):
  async def ensure_symbol(self, stock_code: str, source: str) -> None: ...

  def get_klines(
    self,
    stock_code: str,
    *,
    start_time: datetime,
    end_time: datetime,
  ) -> List[KLine]: ...

  def get_ticks(
    self,
    stock_code: str,
    *,
    start_time: datetime,
    end_time: datetime,
  ) -> List[Tick]: ...


_intraday_warm_cache: Optional[IntradayWarmCache] = None


def set_intraday_warm_cache(provider: Optional[IntradayWarmCache]) -> None:
  """Inject the Engine-owned cache without making infrastructure import Engine."""
  global _intraday_warm_cache
  _intraday_warm_cache = provider


class RealtimeDataAdapter(DataAdapter):
  """实时数据适配器 - 用于模拟和实盘交易"""

  CONNECT_TIMEOUT_SECONDS = 4.0

  def __init__(self):
    super().__init__(DataMode.REALTIME)
    # 使用统一订阅管理器
    self.subscription_manager = unified_subscription_manager
    self.subscriber_id = "realtime_data_adapter"
    self.price_cache: Dict[str, float] = {}
    self.trading_time_service = TradingTimeService()
    # 股票名称缓存
    self.stock_name_cache: Dict[str, str] = {}

  async def connect(self) -> bool:
    """连接远端行情事件桥接。"""
    try:
      data_manager = self.subscription_manager.data_manager
      if not data_manager.is_connected:
        data_manager._init_connection()
      self.is_connected = bool(data_manager.is_connected)
      if self.is_connected:
        self.logger.info("实时数据适配器已连接到远端行情事件桥接")
      else:
        self.logger.error("远端行情事件桥接不可用")

      return self.is_connected

    except asyncio.TimeoutError:
      self.is_connected = False
      self.logger.warning(
        "远端行情事件桥接超时(%s秒)，实时数据本次降级",
        self.CONNECT_TIMEOUT_SECONDS,
      )
      return False
    except Exception as e:
      self.logger.error(f"连接失败: {e}")
      return False

  async def disconnect(self) -> None:
    """断开连接"""
    try:
      # 取消所有订阅
      for sub_id in list(self.subscriptions.keys()):
        await self.unsubscribe(sub_id)

      # 通过统一管理器取消所有订阅
      await self.subscription_manager.unsubscribe_all(self.subscriber_id)

      self.is_connected = False
      self.logger.info("实时数据适配器已断开")
    except Exception as e:
      self.logger.error(f"断开连接失败: {e}")

  async def subscribe_kline(
    self,
    instrument_code: str,
    period: str = "1m",
    callback: Optional[Callable[[KLine], None]] = None,
  ) -> str:
    """订阅K线数据"""
    subscription_id = self.generate_subscription_id(instrument_code, "kline")

    subscription = DataSubscription(
      instrument_code=instrument_code,
      data_type="kline",
      period=period,
      callback=callback,
    )

    self.subscriptions[subscription_id] = subscription

    # 通过统一管理器订阅K线数据
    async def data_callback(data):
      """处理来自统一管理器的K线数据回调"""
      await self._handle_xt_kline_data(instrument_code, period, data)

    try:
      manager_handle = await self.subscription_manager.subscribe(
        stock_code=instrument_code,
        callback=data_callback,
        subscriber_id=self.subscriber_id,
        period=period,
      )
      if not manager_handle:
        self.subscriptions.pop(subscription_id, None)
        raise RuntimeError(f"底层K线订阅失败: {instrument_code} {period}")
      subscription.manager_handle = manager_handle
      self.logger.info(f"通过统一管理器订阅K线数据: {instrument_code} {period}")
    except Exception as e:
      self.subscriptions.pop(subscription_id, None)
      self.logger.error(f"订阅K线数据异常: {instrument_code} {period}, {e}")
      raise

    return subscription_id

  async def subscribe_tick(
    self, instrument_code: str, callback: Optional[Callable[[Tick], None]] = None
  ) -> str:
    """订阅Tick数据"""
    subscription_id = self.generate_subscription_id(instrument_code, "tick")

    subscription = DataSubscription(
      instrument_code=instrument_code,
      data_type="tick",
      callback=callback,
    )

    self.subscriptions[subscription_id] = subscription

    # 通过统一管理器订阅
    async def data_callback(data):
      """处理来自统一管理器的数据回调"""
      await self._handle_xt_tick_data(instrument_code, data)

    try:
      manager_handle = await self.subscription_manager.subscribe(
        stock_code=instrument_code,
        callback=data_callback,
        subscriber_id=self.subscriber_id,
        period="tick",
      )
      if not manager_handle:
        self.subscriptions.pop(subscription_id, None)
        raise RuntimeError(f"底层tick订阅失败: {instrument_code}")
      subscription.manager_handle = manager_handle
      self.logger.info(f"通过统一管理器订阅实时Tick数据: {instrument_code}")
    except Exception as e:
      self.subscriptions.pop(subscription_id, None)
      self.logger.error(f"订阅实时数据异常: {instrument_code}, {e}")
      raise

    return subscription_id

  async def unsubscribe(self, subscription_id: str) -> bool:
    """取消订阅"""
    if subscription_id not in self.subscriptions:
      return False

    subscription = self.subscriptions[subscription_id]
    instrument_code = subscription.instrument_code

    # 移除订阅记录
    del self.subscriptions[subscription_id]

    if subscription.manager_handle:
      try:
        await self.subscription_manager.unsubscribe(subscription.manager_handle)
        self.logger.info(f"通过统一管理器取消订阅: {instrument_code}")
      except Exception as e:
        self.logger.error(f"取消订阅失败: {instrument_code}, {e}")

    self.logger.info(f"取消订阅: {subscription_id}")
    return True

  async def get_klines(
    self,
    instrument_code: str,
    period: str,
    start_time: datetime,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = 1000,
    order: str = "asc",
    dividend_type: str = "none",
  ) -> List[KLine]:
    """获取K线数据。

    1m K线直接从 miniQMT 本地缓存读取；若缓存尾部落后，后台补全后通过
    GraphQL K线订阅推送。其它周期继续走历史服务。
    """
    order = (order or "asc").lower()
    now = time_utils.now()
    effective_end_time = end_time or now
    if effective_end_time > now:
      effective_end_time = now

    if period == "1m":
      return await self.get_realtime_klines_range(
        instrument_code=instrument_code,
        period=period,
        start_time=start_time
        or datetime.combine(effective_end_time.date(), datetime.min.time()),
        end_time=effective_end_time,
        limit=limit,
        order=order,
        dividend_type=dividend_type,
      )

    from quantx_infrastructure.services.historical_market_data_service import (
      HistoricalMarketDataService,
    )

    market_data_service = HistoricalMarketDataService()
    klines = await market_data_service.get_kline_data(
      stock_code=instrument_code,
      period=period,
      start_time=start_time,
      end_time=end_time,
      order=order,
      dividend_type=dividend_type,
    )

    if limit is not None and len(klines) > limit:
      klines = klines[:limit]

    return klines

  def _normalize_kline_time(self, kline: KLine) -> Optional[datetime]:
    kline_time = getattr(kline, "time", None)
    if kline_time is None:
      return None
    if kline_time.tzinfo is not None:
      return time_utils.to_shanghai(kline_time)
    return kline_time

  def _normalize_xt_time_value(self, value: Any) -> Optional[datetime]:
    if value is None:
      return None
    if hasattr(value, "item"):
      try:
        value = value.item()
      except Exception:
        pass
    if isinstance(value, pd.Timestamp):
      value = value.to_pydatetime()
    if isinstance(value, datetime):
      return time_utils.to_shanghai(value)
    if isinstance(value, str):
      raw = value.strip()
      if not raw:
        return None
      if raw.isdigit() and len(raw) == 14:
        return datetime.strptime(raw, "%Y%m%d%H%M%S")
      try:
        parsed = pd.to_datetime(raw)
        if pd.isna(parsed):
          return None
        return time_utils.to_shanghai(parsed.to_pydatetime())
      except Exception:
        return None
    if isinstance(value, (int, float)):
      raw = str(int(value))
      if len(raw) == 14:
        return datetime.strptime(raw, "%Y%m%d%H%M%S")
      if value > 10**12:
        return time_utils.to_shanghai(
          datetime.fromtimestamp(value / 1000, timezone.utc)
        )
      if value > 10**9:
        return time_utils.to_shanghai(datetime.fromtimestamp(value, timezone.utc))
    return None

  def _xt_time_value_to_ms(self, value: Any) -> Optional[int]:
    if value is None:
      return None
    if hasattr(value, "item"):
      try:
        value = value.item()
      except Exception:
        pass
    if isinstance(value, pd.Timestamp):
      value = value.to_pydatetime()
    if isinstance(value, datetime):
      return int(time_utils.to_utc(value).timestamp() * 1000)
    if isinstance(value, str):
      raw = value.strip()
      if not raw:
        return None
      if raw.isdigit():
        if len(raw) == 14:
          parsed = datetime.strptime(raw, "%Y%m%d%H%M%S")
          return int(time_utils.to_utc(parsed).timestamp() * 1000)
        numeric = int(raw)
        if numeric > 10**12:
          return numeric
      parsed = self._normalize_xt_time_value(raw)
      if parsed is not None:
        return int(time_utils.to_utc(parsed).timestamp() * 1000)
      return None
    if isinstance(value, (int, float)):
      raw = str(int(value))
      if len(raw) == 14:
        parsed = datetime.strptime(raw, "%Y%m%d%H%M%S")
        return int(time_utils.to_utc(parsed).timestamp() * 1000)
      if value > 10**12:
        return int(value)
      if value > 10**9:
        return int(value * 1000)
    return None

  def _frame_to_klines(
    self, instrument_code: str, period: str, frame: Optional[pd.DataFrame]
  ) -> List[KLine]:
    if frame is None or frame.empty:
      return []

    klines: List[KLine] = []
    for index, row in frame.iterrows():
      data = row.to_dict()
      source_time = data.get("time", index)
      timestamp_ms = self._xt_time_value_to_ms(source_time)
      if timestamp_ms is None:
        continue
      data["time"] = timestamp_ms
      if "preClose" not in data and "pre_close" in data:
        data["preClose"] = data.get("pre_close")
      if "settlementPrice" not in data and "settelementPrice" in data:
        data["settlementPrice"] = data.get("settelementPrice")
      if "openInt" not in data and "openInterest" in data:
        data["openInt"] = data.get("openInterest")
      try:
        klines.append(KLine.from_xtquant(instrument_code, period, data))
      except Exception as exc:
        self.logger.debug(f"跳过无效K线数据: {instrument_code} {period}, {exc}")

    klines.sort(key=lambda item: self._normalize_kline_time(item) or datetime.min)
    return klines

  def _latest_expected_1m_time(self, value: datetime) -> Optional[datetime]:
    value = time_utils.to_shanghai(value).replace(second=0, microsecond=0)
    minutes = value.hour * 60 + value.minute
    open_minutes = 9 * 60 + 30
    morning_close = 11 * 60 + 30
    afternoon_open = 13 * 60
    market_close = 15 * 60

    if minutes <= open_minutes:
      return None
    if minutes <= morning_close:
      return value - timedelta(minutes=1)
    if minutes < afternoon_open:
      return value.replace(hour=11, minute=30)
    if minutes <= market_close:
      return value - timedelta(minutes=1)
    return value.replace(hour=15, minute=0)

  async def get_realtime_klines_range(
    self,
    instrument_code: str,
    period: str,
    start_time: datetime,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
    order: str = "asc",
    dividend_type: str = "none",
  ) -> List[KLine]:
    """获取今日远端热缓存，并使用数据库历史数据补齐。"""
    try:
      if period != "1m":
        return []

      now = time_utils.now()
      effective_end_time = end_time or now
      if effective_end_time > now:
        effective_end_time = now
      start_time = time_utils.to_shanghai(start_time)
      effective_end_time = time_utils.to_shanghai(effective_end_time)
      if start_time >= effective_end_time:
        return []

      warm_klines: List[KLine] = []
      if _intraday_warm_cache is not None:
        try:
          await _intraday_warm_cache.ensure_symbol(
            instrument_code, source="chart_query"
          )
        except Exception as exc:
          self.logger.debug("登记热池失败: %s %s", instrument_code, exc)

        warm_klines = _intraday_warm_cache.get_klines(
          instrument_code,
          start_time=start_time,
          end_time=effective_end_time,
        )

      if not self.is_connected:
        await self.connect()

      from quantx_infrastructure.services.historical_market_data_service import (
        HistoricalMarketDataService,
      )

      try:
        historical = await HistoricalMarketDataService().get_kline_data(
          stock_code=instrument_code,
          period=period,
          start_time=start_time,
          end_time=effective_end_time,
          dividend_type=dividend_type,
          limit=None,
          order="asc",
        )
      except Exception as exc:
        # A transient history-store failure must not discard fresh, Engine-owned
        # intraday data that is already safe to serve.
        self.logger.warning(
          "历史K线补齐失败，使用热缓存降级: %s %s",
          instrument_code,
          exc,
        )
        historical = []

      merged = {}
      for kline in [*historical, *warm_klines]:
        kline_time = self._normalize_kline_time(kline)
        if kline_time is None:
          continue
        if start_time <= kline_time <= effective_end_time:
          merged[kline_time] = kline
      klines = list(merged.values())
      klines.sort(key=lambda kline: self._normalize_kline_time(kline) or datetime.min)

      if (order or "asc").lower() == "desc":
        klines = list(reversed(klines))
      if limit is not None and limit > 0:
        klines = klines[:limit]
      return klines

    except Exception as e:
      self.logger.error(f"实时区间K线获取失败: {instrument_code} {period}, {e}")
      return []

  async def get_ticks(
    self,
    instrument_code: str,
    start_time: datetime,
    end_time: Optional[datetime] = None,
    dividend_type: str = "none",
    limit: Optional[int] = 1000,
    order: str = "desc",
  ) -> List[Tick]:
    """获取Tick数据（今日实时 + 历史补全，按需合并）"""
    order = (order or "desc").lower()
    realtime_ticks: List[Tick] = []

    # 1) 结束时间不能晚于当前时间，避免查询未来区间。
    now = time_utils.now()
    if end_time is None or end_time > now:
      effective_end_time = now
    else:
      effective_end_time = end_time

    # 2) 若查询包含“今天”且今天是交易日：
    #    - 先取今日区间的实时数据（从今天 00:00 或 start_time 起）
    #    - 历史区间截断到昨天收盘，避免与今日实时重叠
    if effective_end_time.date() == now.date() and await self.trading_time_service.is_trading_day(
      "SH", now.date()
    ):
      today_start = max(datetime.combine(now.date(), datetime.min.time()), start_time or datetime.min)
      today_end = min(datetime.combine(now.date(), datetime.max.time()), effective_end_time)
      realtime_ticks = await self.get_realtime_ticks_range(
        instrument_code=instrument_code,
        start_time=today_start,
        end_time=today_end,
        limit=None,
        order=order,
      )
      effective_end_time = datetime.combine(now.date(), datetime.max.time()) - timedelta(days=1)

    # 3) 历史区间为空（start_time >= effective_end_time）时，直接返回实时数据（若有）。
    if start_time is not None and start_time >= effective_end_time:
      return self._apply_tick_limit(realtime_ticks, limit)

    # 4) desc 模式下优先用实时数据填充 limit，剩余再补历史。
    if order == "desc" and realtime_ticks and limit is not None:
      if len(realtime_ticks) >= limit:
        return realtime_ticks[:limit]
      limit_remaining = limit - len(realtime_ticks)
    else:
      limit_remaining = limit

    # 5) 拉取历史数据（截止到 effective_end_time；若有今日实时则为昨天收盘）。
    historical_ticks = await self._get_historical_ticks(
      instrument_code=instrument_code,
      start_time=start_time,
      end_time=effective_end_time,
      dividend_type=dividend_type,
      order=order,
      limit=limit_remaining
    )

    # 6) 合并去重（实时优先覆盖历史），再应用 limit。
    merged = self._merge_ticks(
      historical_ticks=historical_ticks,
      realtime_ticks=realtime_ticks,
      order=order,
      limit=limit
    )
    return merged

  def _normalize_tick_time(self, tick: Tick) -> Optional[datetime]:
    tick_time = getattr(tick, "time", None)
    if tick_time is None:
      return None
    if tick_time.tzinfo is not None:
      return time_utils.to_shanghai(tick_time)
    return tick_time

  def _merge_ticks(
    self,
    historical_ticks: List[Tick],
    realtime_ticks: List[Tick],
    order: str,
    limit: Optional[int],
  ) -> List[Tick]:
    combined: Dict[datetime, Tick] = {}

    for tick in historical_ticks:
      tick_time = self._normalize_tick_time(tick)
      if tick_time is None:
        continue
      combined[tick_time] = tick

    for tick in realtime_ticks:
      tick_time = self._normalize_tick_time(tick)
      if tick_time is None:
        continue
      combined[tick_time] = tick

    ticks = list(combined.values())
    ticks.sort(key=lambda tick: self._normalize_tick_time(tick) or datetime.min)

    if (order or "desc").lower() == "desc":
      ticks.reverse()

    if limit is not None and limit > 0:
      ticks = ticks[:limit]

    return ticks

  def _apply_tick_limit(self, ticks: List[Tick], limit: Optional[int]) -> List[Tick]:
    if limit is not None and limit > 0:
      return ticks[:limit]
    return ticks

  async def _get_historical_ticks(
    self,
    instrument_code: str,
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    dividend_type: str,
    order: str,
    limit: Optional[int],
  ) -> List[Tick]:
    from quantx_infrastructure.services.historical_market_data_service import (
      HistoricalMarketDataService,
    )

    market_data_service = HistoricalMarketDataService()
    ticks = await market_data_service.get_tick_data(
      stock_code=instrument_code,
      start_time=start_time,
      end_time=end_time,
      dividend_type=dividend_type,
      limit=limit,
      order=order,
    )

    if limit is not None and len(ticks) > limit:
      ticks = ticks[:limit]

    return ticks

  async def get_latest_price(self, instrument_code: str) -> Optional[float]:
    """获取最新价格"""
    # 先查缓存
    if instrument_code in self.price_cache:
      return self.price_cache[instrument_code]

    # 可以从XTQuant获取当前价格（如果需要的话）
    # 这里暂时返回缓存中的价格，实际使用中可以实现实时查询
    return None

  def _format_xt_time(self, value: Optional[datetime]) -> str:
    if value is None:
      return ""
    value = time_utils.to_shanghai(value)
    return value.strftime("%Y%m%d%H%M%S")

  async def get_realtime_ticks_range(
    self,
    instrument_code: str,
    start_time: datetime,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
    order: str = "asc",
  ) -> List[Tick]:
    """获取指定时间区间内的 tick 数据，不在查询路径触发下载。"""
    try:
      now = time_utils.now()
      if end_time is None or end_time > now:
        effective_end_time = now
      else:
        effective_end_time = end_time
      start_time = time_utils.to_shanghai(start_time)
      effective_end_time = time_utils.to_shanghai(effective_end_time)
      if start_time >= effective_end_time:
        return []

      warm_ticks: List[Tick] = []
      if _intraday_warm_cache is not None:
        try:
          await _intraday_warm_cache.ensure_symbol(
            instrument_code, source="tick_query"
          )
        except Exception as exc:
          self.logger.debug("登记tick热池失败: %s %s", instrument_code, exc)

        warm_ticks = _intraday_warm_cache.get_ticks(
          instrument_code,
          start_time=start_time,
          end_time=effective_end_time,
        )

      if not self.is_connected:
        await self.connect()

      if not self.is_connected:
        if warm_ticks:
          ticks = warm_ticks
          order = (order or "asc").lower()
          if order == "desc":
            ticks = list(reversed(ticks))
          if limit is not None and limit > 0:
            ticks = ticks[:limit]
          return ticks
        self.logger.warning("实时数据适配器未连接")
        return []

      ticks = list(warm_ticks)
      order = (order or "asc").lower()
      deduped = {
        tick_time: tick
        for tick in ticks
        if (tick_time := self._normalize_tick_time(tick)) is not None
      }
      ticks = list(deduped.values())
      ticks.sort(key=lambda tick: self._normalize_tick_time(tick) or datetime.min)
      if order == "desc":
        ticks.reverse()

      if limit is not None and limit > 0:
        ticks = ticks[:limit]

      return ticks

    except Exception as e:
      self.logger.error(f"实时区间tick获取失败: {instrument_code}, {e}")
      return []

  def _get_stock_name(self, stock_code: str) -> str:
    """获取股票名称"""
    if stock_code in self.stock_name_cache:
      return self.stock_name_cache[stock_code]

    try:
      # 通过统一管理器的数据管理器获取股票详情
      data_manager = self.subscription_manager.data_manager
      instrument_detail = data_manager.get_instrument_detail(stock_code)
      if instrument_detail and "InstrumentName" in instrument_detail:
        name = instrument_detail["InstrumentName"]
        self.stock_name_cache[stock_code] = name
        return name
    except Exception as e:
      self.logger.warning(f"获取股票名称失败: {stock_code}, {e}")

    # 返回股票代码作为默认值
    return stock_code

  async def _handle_xt_tick_data(self, instrument_code: str, data: Dict):
    """处理来自XTQuant的实时数据回调并转换为Tick格式"""
    try:
      # 解析XTQuant返回的数据格式
      if not data or instrument_code not in data:
        self.logger.warning(f"收到空数据或不包含股票代码: {instrument_code}")
        return

      tick_list = data[instrument_code]
      if not tick_list:
        return

      # 取最新的tick数据
      latest_tick = tick_list[-1] if isinstance(tick_list, list) else tick_list

      # 获取价格信息
      current_price = latest_tick.get("lastPrice", 0)

      # 使用统一领域模型转换，避免遗留 code/price 字段生成不完整 Tick。
      tick = Tick.from_xtquant(instrument_code, latest_tick)

      # 更新价格缓存
      self.price_cache[instrument_code] = current_price

      # 分发给所有相关订阅
      for subscription_id, subscription in self.subscriptions.items():
        if (
          subscription.instrument_code == instrument_code
          and subscription.data_type == "tick"
        ):
          await self.emit_tick_data(subscription_id, tick)

    except Exception as e:
      self.logger.error(f"处理XTQuant数据回调失败: {instrument_code}, {e}")

  async def _handle_xt_kline_data(self, instrument_code: str, period: str, data: Dict):
    """处理来自XTQuant的K线数据回调并转换为KLine格式"""
    try:
      if not data or instrument_code not in data:
        self.logger.warning(f"收到空K线数据或不包含股票代码: {instrument_code}")
        return

      kline_list = data[instrument_code]
      if not kline_list:
        return

      # 取最新的K线数据
      latest_kline = kline_list[-1] if isinstance(kline_list, list) else kline_list

      # 转换时间戳
      timestamp = (
        datetime.fromtimestamp(latest_kline.get("time", 0) / 1000)
        if latest_kline.get("time")
        else time_utils.now()
      )

      # 构建KLine对象
      kline = KLine(
        code=instrument_code,
        time=timestamp,
        open=latest_kline.get("open", 0),
        high=latest_kline.get("high", 0),
        low=latest_kline.get("low", 0),
        close=latest_kline.get("close", 0),
        volume=int(latest_kline.get("volume", 0)),
        amount=latest_kline.get("amount", 0),
      )

      # 分发给所有相关订阅
      for subscription_id, subscription in self.subscriptions.items():
        if (
          subscription.instrument_code == instrument_code
          and subscription.data_type == "kline"
          and subscription.period == period
        ):
          await self.emit_kline_data(subscription_id, kline)

    except Exception as e:
      self.logger.error(f"处理XTQuant K线数据回调失败: {instrument_code} {period}, {e}")

  async def _handle_xt_depth_data(self, instrument_code: str, data: Dict):
    """处理来自XTQuant的深度数据回调"""
    try:
      if not data or instrument_code not in data:
        self.logger.warning(f"收到空数据或不包含股票代码: {instrument_code}")
        return

      tick_list = data[instrument_code]
      if not tick_list:
        return

      # 取最新的tick数据
      latest_tick = tick_list[-1] if isinstance(tick_list, list) else tick_list

      # 获取买卖盘信息
      ask_prices = latest_tick.get("askPrice", [])
      bid_prices = latest_tick.get("bidPrice", [])
      ask_volumes = latest_tick.get("askVol", [])
      bid_volumes = latest_tick.get("bidVol", [])

      # 构建买卖盘数据
      bids = []
      asks = []

      for i in range(min(5, len(bid_prices), len(bid_volumes))):
        bids.append([bid_prices[i], int(bid_volumes[i])])

      for i in range(min(5, len(ask_prices), len(ask_volumes))):
        asks.append([ask_prices[i], int(ask_volumes[i])])

      # 构建深度数据字典
      depth_data = {
        "stock_code": instrument_code,
        "timestamp": datetime.fromtimestamp(latest_tick.get("time", 0) / 1000)
        if latest_tick.get("time")
        else time_utils.now(),
        "bids": bids,
        "asks": asks,
      }

      # 分发给所有相关订阅
      for subscription_id, subscription in self.subscriptions.items():
        if (
          subscription.instrument_code == instrument_code
          and subscription.data_type == "depth"
        ):
          if subscription.callback:
            if asyncio.iscoroutinefunction(subscription.callback):
              await subscription.callback(depth_data)
            else:
              subscription.callback(depth_data)

    except Exception as e:
      self.logger.error(f"处理XTQuant深度数据回调失败: {instrument_code}, {e}")

  async def subscribe_market_depth(
    self, instrument_code: str, callback: Optional[Callable[[Dict], None]] = None
  ) -> str:
    """订阅市场深度数据（扩展功能）"""
    subscription_id = self.generate_subscription_id(instrument_code, "depth")

    subscription = DataSubscription(
      instrument_code=instrument_code,
      data_type="depth",
      callback=callback,
    )

    self.subscriptions[subscription_id] = subscription

    # 市场深度数据从 tick 数据中提取，通过统一管理器订阅 tick
    async def data_callback(data):
      """处理来自统一管理器的深度数据回调"""
      await self._handle_xt_depth_data(instrument_code, data)

    try:
      manager_handle = await self.subscription_manager.subscribe(
        stock_code=instrument_code,
        callback=data_callback,
        subscriber_id=self.subscriber_id,
        period="tick",  # 深度数据从 tick 中提取
      )
      if not manager_handle:
        self.subscriptions.pop(subscription_id, None)
        raise RuntimeError(f"底层市场深度订阅失败: {instrument_code}")
      subscription.manager_handle = manager_handle
      self.logger.info(f"通过统一管理器订阅市场深度: {instrument_code}")
    except Exception as e:
      self.subscriptions.pop(subscription_id, None)
      self.logger.error(f"订阅市场深度异常: {instrument_code}, {e}")
      raise

    return subscription_id

  def get_cached_prices(self) -> Dict[str, float]:
    """获取所有缓存的价格"""
    return self.price_cache.copy()

  def clear_price_cache(self) -> None:
    """清除价格缓存"""
    self.price_cache.clear()
