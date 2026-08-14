"""
统一数据提供者服务
负责数据源选择、容错处理和缓存管理
"""

import asyncio
import logging
from datetime import datetime, time, timedelta
from threading import Lock
from typing import Any, Dict, List, Optional

from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.tick import Tick
from quantx_infrastructure.services.divid_factor_service import DividFactorService
from quantx_infrastructure.services.holiday_service import HolidayService
from quantx_infrastructure.services.trading_time_service import TradingTimeService

from .adapter_manager import adapter_manager
from quantx_infrastructure.core.utils import time_utils


class CacheConfig:
  """缓存配置管理类"""

  def __init__(self, trading_time_service: TradingTimeService):
    # 默认配置：3秒TTL，适合量化交易
    self.default_ttl = 3
    self.trading_time_service = trading_time_service

    # 数据类型特定配置
    self.data_types_config = {
      "latest_price": {
        "trading": 1,  # 交易时间1秒，需要高实时性
        "non_trading": 30,  # 非交易时间30秒，可以稍长
      },
      "positions": {
        "trading": 3,  # 交易时间3秒
        "non_trading": 60,  # 非交易时间60秒，持仓变化较少
      },
    }

  async def is_trading_hours(self) -> bool:
    """判断当前是否为交易时间"""
    return await self.trading_time_service.is_trading_hours("SH")

  async def get_ttl(self, data_type: str) -> int:
    """获取指定数据类型的TTL"""
    if data_type not in self.data_types_config:
      return self.default_ttl

    config = self.data_types_config[data_type]
    is_trading = await self.is_trading_hours()

    if is_trading and "trading" in config:
      return config["trading"]
    elif not is_trading and "non_trading" in config:
      return config["non_trading"]
    else:
      return self.default_ttl


class MarketDataService:
  """统一市场数据服务"""

  REALTIME_CONNECT_TIMEOUT_SECONDS = 4.0
  REALTIME_PRICE_TIMEOUT_SECONDS = 4.0
  REALTIME_POSITION_TIMEOUT_SECONDS = 5.0

  def __init__(self):
    self.logger = logging.getLogger(__name__)

    self.account_id = "300000013250"  # 默认账户ID

    # 使用全局适配器管理器，避免重复实例化
    self.adapter_manager = adapter_manager

    self.holiday_service = HolidayService()
    self.trading_time_service = TradingTimeService()
    self.divid_factor_service = DividFactorService()

    # 缓存相关
    self.price_cache: Dict[str, Dict] = {}
    self.position_cache: Dict[str, Dict] = {}  # 持仓数据缓存
    self.previous_daily_close_cache: Dict[str, Dict[str, Any]] = {}
    self.cache_lock = Lock()

    # 新的灵活缓存配置系统
    self.cache_config = CacheConfig(self.trading_time_service)

    # 初始化和连接状态
    self._is_initialized = False
    self._is_connected = False
    self._last_connect_attempt: Optional[datetime] = None
    self._connect_retry_interval = 60  # 60秒重试间隔

  def _previous_daily_close_cache_key(
    self, stock_code: str, tick_time: datetime
  ) -> str:
    tick_date = time_utils.to_shanghai(tick_time).date()
    return f"{stock_code}:{tick_date.isoformat()}"

  async def initialize(self) -> bool:
    """初始化数据提供者"""
    if self._is_initialized:
      self.logger.debug("数据提供者已经初始化，跳过重复初始化")
      return True

    try:
      # 使用适配器管理器初始化所有适配器
      success = await self.adapter_manager.initialize_all()
      self._is_connected = self.adapter_manager.realtime_adapter.is_connected

      if self._is_connected:
        self.logger.info("实时数据源连接成功")
      else:
        self.logger.warning("实时数据源连接失败，将使用历史数据降级")

      self._is_initialized = True
      return success
    except Exception as e:
      self.logger.error(f"数据提供者初始化失败: {e}")
      self._is_initialized = False
      return False

  async def get_latest_price(self, stock_code: str) -> Optional[Tick]:
    """获取单个股票的最新价格"""
    prices = await self.get_latest_prices([stock_code])
    return prices.get(stock_code)

  async def get_latest_prices(self, stock_codes: List[str]) -> Dict[str, Tick]:
    """
    获取最新价格数据，支持降级机制

    Args:
        stock_codes: 股票代码列表

    Returns:
        Dict: 股票代码到价格数据的映射，格式类似 xtquant 的 get_full_tick 返回值
    """
    try:
      # 先尝试从缓存获取
      cached_data = await self._get_cached_prices(stock_codes)
      missing_codes = [code for code in stock_codes if code not in cached_data]

      if not missing_codes:
        return cached_data

      # 尝试从实时数据源获取
      realtime_data = await self._get_realtime_prices(missing_codes)

      # 如果实时数据获取失败，降级到历史数据
      if not realtime_data:
        self.logger.warning("实时数据获取失败，降级到历史数据")
        historical_data = await self._get_historical_prices(missing_codes)
        result_data = {**cached_data, **historical_data}
      else:
        # 更新缓存
        self._update_cache(realtime_data)
        result_data = {**cached_data, **realtime_data}

      return result_data

    except Exception as e:
      self.logger.error(f"获取最新价格失败: {e}")
      # 最后的降级：尝试历史数据
      try:
        return await self._get_historical_prices(stock_codes)
      except Exception as fallback_error:
        self.logger.error(f"历史数据降级也失败: {fallback_error}")
        return {}

  async def get_klines(
    self,
    stock_code: str,
    period: str = "1m",
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
    dividend_type: str = "none",
    order: str = "desc",
  ):
    if limit is not None and limit <= 0:
      return []

    now = time_utils.now()
    effective_end_time = end_time or now
    if effective_end_time > now:
      effective_end_time = now

    if period == "1m":
      return await self.adapter_manager.realtime_adapter.get_klines(
        instrument_code=stock_code,
        period=period,
        start_time=start_time,
        end_time=effective_end_time,
        limit=limit,
        order=order,
        dividend_type=dividend_type,
      )

    historical_adapter = self.adapter_manager.historical_adapter
    return await historical_adapter.get_klines(
      instrument_code=stock_code,
      period=period,
      start_time=start_time,
      end_time=end_time,
      limit=limit,
      order=order,
      dividend_type=dividend_type,
    )

  async def get_ticks(
    self,
    stock_code: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = 6000,
    order: str = "desc",
    dividend_type: str = "none",
  ) -> List[Tick]:
    if limit is not None and limit <= 0:
      return []
    now = time_utils.now()
    effective_end_time = end_time or now
    if effective_end_time > now:
      effective_end_time = now
    query_date = effective_end_time.date()
    query_contains_today = (
      query_date == now.date()
      and (start_time is None or start_time.date() <= now.date())
      and await self.trading_time_service.is_trading_day("SH", query_date)
    )
    adapter = (
      self.adapter_manager.realtime_adapter
      if query_contains_today
      else await self.adapter_manager.get_adapter_for_time_range(
        start_time=start_time,
        end_time=effective_end_time,
      )
    )
    return await adapter.get_ticks(
      instrument_code=stock_code,
      start_time=start_time,
      end_time=effective_end_time,
      dividend_type=dividend_type,
      limit=limit,
      order=order,
    )

  async def _get_realtime_prices(self, stock_codes: List[str]) -> Dict[str, Tick]:
    """读取远端行情桥接已接收的最新价格。"""
    try:
      # 检查连接状态，必要时重连
      if not await self._ensure_realtime_connection():
        return {}

      subscription_manager = (
        self.adapter_manager.realtime_adapter.subscription_manager
      )
      full_tick_data = {
        code: subscription_manager.latest_tick_data[code]
        for code in stock_codes
        if code in subscription_manager.latest_tick_data
      }

      latest_ticks = {}
      for code, tick_data in full_tick_data.items():
        tick = Tick.from_xtquant(code, tick_data)
        if tick is not None:
          latest_ticks[code] = await self._normalize_tick_pre_close(code, tick)

      return latest_ticks

    except asyncio.TimeoutError:
      self.logger.warning(
        "实时价格查询超时(%s秒): %s",
        self.REALTIME_PRICE_TIMEOUT_SECONDS,
        stock_codes[:10],
      )
      return {}
    except Exception as e:
      import traceback

      traceback.print_exc()
      self.logger.error(f"实时数据获取失败: {e}")
      return {}

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
      klines = await self.adapter_manager.historical_adapter.get_klines(
        instrument_code=stock_code,
        period="1d",
        start_time=start_time,
        end_time=end_time,
        limit=None,
        order="asc",
        dividend_type="none",
      )
    except Exception as exc:
      self.logger.warning("查询昨日收盘日K失败: %s, %s", stock_code, exc)
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
      self.logger.warning(
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
      self.logger.warning("查询前复权因子失败: %s, %s", stock_code, exc)
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
    self.logger.info(
      "昨日收盘价使用tick兜底: %s, tick_time=%s, last_close=%s",
      stock_code,
      tick.time,
      native_pre_close,
    )
    return tick

  async def _get_historical_prices(self, stock_codes: List[str]) -> Dict[str, Tick]:
    """从历史数据源获取价格，返回标准Tick对象"""
    try:
      historical_adapter = self.adapter_manager.historical_adapter
      return await historical_adapter.get_latest_ticks(stock_codes)

    except Exception as e:
      self.logger.error(f"历史数据获取失败: {e}")
      return {}

  def _create_default_tick(self, stock_code: str) -> Tick:
    """创建默认的Tick对象"""
    return Tick(
      stock_code=stock_code,
      period="tick",
      time=time_utils.now(),
      last_price=0.0,
      open=0.0,
      high=0.0,
      low=0.0,
      last_close=0.0,
      amount=0.0,
      volume=0.0,
      pvolume=0.0,
      tickvol=0.0,
      stock_status=0,
      open_int=0,
      last_settlement_price=0.0,
      settlement_price=0.0,
      transaction_num=0,
      ask_price=[0.0] * 5,
      bid_price=[0.0] * 5,
      ask_vol=[0.0] * 5,
      bid_vol=[0.0] * 5,
    )

  async def _ensure_realtime_connection(self) -> bool:
    """确保实时数据连接可用"""
    now = time_utils.now()

    # 如果已连接，直接返回
    if self._is_connected:
      return True

    # 检查重试间隔
    if (
      self._last_connect_attempt
      and (now - self._last_connect_attempt).total_seconds()
      < self._connect_retry_interval
    ):
      return False

    # 尝试重连
    self._last_connect_attempt = now
    try:
      self._is_connected = await asyncio.wait_for(
        self.adapter_manager.realtime_adapter.connect(),
        timeout=self.REALTIME_CONNECT_TIMEOUT_SECONDS,
      )
      if self._is_connected:
        self.logger.info("实时数据源重连成功")
      return self._is_connected
    except asyncio.TimeoutError:
      self._is_connected = False
      self.logger.warning(
        "实时数据源连接超时(%s秒)，本次请求降级",
        self.REALTIME_CONNECT_TIMEOUT_SECONDS,
      )
      return False
    except Exception as e:
      self.logger.error(f"实时数据源重连失败: {e}")
      return False

  async def _get_cached_prices(self, stock_codes: List[str]) -> Dict[str, Tick]:
    """从缓存获取价格数据"""
    # TTL 计算可能访问交易日历/数据库，不能在 threading.Lock 内 await，
    # 否则并发 GraphQL 请求会同步阻塞事件循环。
    ttl = await self.cache_config.get_ttl("latest_price")
    with self.cache_lock:
      result = {}
      now = time_utils.now()

      for code in stock_codes:
        if code in self.price_cache:
          cache_entry = self.price_cache[code]
          cache_time = cache_entry.get("cache_time")

          if cache_time and (now - cache_time).total_seconds() < ttl:
            result[code] = cache_entry["data"]

      return result

  def _update_cache(self, data: Dict[str, Tick]) -> None:
    """更新价格缓存"""
    with self.cache_lock:
      now = time_utils.now()
      for code, price_data in data.items():
        self.price_cache[code] = {"data": price_data, "cache_time": now}

  def clear_cache(self) -> None:
    """清空缓存"""
    with self.cache_lock:
      self.price_cache.clear()
      self.position_cache.clear()
      self.previous_daily_close_cache.clear()

  async def _get_realtime_positions(self) -> List[Position]:
    """持仓真源由 Agent 回报经 Engine 收敛到 PostgreSQL。"""
    return []

  async def _get_realtime_position(self, stock_code: str) -> Optional[Position]:
    """单标的持仓同样只读取 Engine 已收敛的数据库状态。"""
    del stock_code
    return None

  async def _get_historical_positions(self) -> List[Position]:
    """从数据库获取持仓列表"""
    try:
      # 使用持仓服务获取数据库中的持仓数据
      from quantx_infrastructure.services.position_service import PositionService

      position_service = PositionService()

      positions = await position_service.get_positions()
      return positions

    except Exception as e:
      self.logger.error(f"数据库持仓数据获取失败: {e}")
      return []

  async def _get_historical_position(self, stock_code: str) -> Optional[Position]:
    """从数据库获取单个持仓"""
    try:
      # 使用持仓服务获取数据库中的持仓数据
      from quantx_infrastructure.services.position_service import PositionService

      position_service = PositionService()

      position = await position_service.get_position_by_stock(stock_code)
      return position

    except Exception as e:
      self.logger.error(f"数据库单个持仓数据获取失败: {stock_code}, {e}")
      return None

  async def _get_cached_positions(self, cache_key: str) -> Optional[List[Any]]:
    """从缓存获取持仓数据"""
    # TTL 计算可能访问交易日历/数据库，不能在 threading.Lock 内 await，
    # 否则并发请求会把 uvicorn 主事件循环钉住。
    ttl = await self.cache_config.get_ttl("positions")
    with self.cache_lock:
      if cache_key in self.position_cache:
        cache_entry = self.position_cache[cache_key]
        cache_time = cache_entry.get("cache_time")

        if cache_time and (time_utils.now() - cache_time).total_seconds() < ttl:
          return cache_entry["data"]

      return None

  def _update_position_cache(self, cache_key: str, data: List[Any]) -> None:
    """更新持仓缓存"""
    with self.cache_lock:
      self.position_cache[cache_key] = {"data": data, "cache_time": time_utils.now()}

  async def get_positions(self, with_latest_price: bool = False) -> List[Position]:
    """
    获取持仓列表，支持降级机制

    Args:
        with_latest_price: 是否获取最新价格信息，默认False

    Returns:
        List: 持仓数据列表，如果with_latest_price=True则包含最新价格
    """
    try:
      # 先尝试从缓存获取
      cache_key = f"positions_{self.account_id}"
      cached_data = await self._get_cached_positions(cache_key)
      if cached_data:
        positions = cached_data
      else:
        # 尝试从实时数据源获取
        positions = await self._get_realtime_positions()

        # 如果实时数据获取失败，降级到历史数据
        if not positions:
          self.logger.warning("实时持仓数据获取失败，降级到数据库数据")
          positions = await self._get_historical_positions()

        # 更新缓存
        if positions:
          self._update_position_cache(cache_key, positions)

      # 如果需要获取最新价格
      if with_latest_price and positions:
        stock_codes = [pos.stock_code for pos in positions if pos.volume > 0]
        latest_prices = await self.get_latest_prices(stock_codes)
        # 为每个持仓设置最新价格；实时行情超时或缺失时保留原值。
        for pos in positions:
          latest_tick = latest_prices.get(pos.stock_code)
          if latest_tick is not None:
            pos.last_price = latest_tick.last_price

      return positions

    except Exception as e:
      self.logger.error(f"获取持仓列表失败: {e}")
      # 最后的降级：尝试数据库数据
      try:
        return await self._get_historical_positions()
      except Exception as fallback_error:
        self.logger.error(f"数据库持仓数据降级也失败: {fallback_error}")
        return []

  async def get_position(
    self,
    stock_code: str,
    with_latest_price: bool = False,
  ) -> Optional[Position]:
    """
    获取单个持仓，支持降级机制

    Args:
        stock_code: 股票代码
        with_latest_price: 是否获取最新价格信息，默认False

    Returns:
        持仓数据或None
    """
    try:
      # 先尝试从缓存获取
      cache_key = f"position_{self.account_id}_{stock_code}"
      cached_data = await self._get_cached_positions(cache_key)
      if cached_data:
        position = cached_data[0] if cached_data else None
      else:
        # 尝试从实时数据源获取
        position = await self._get_realtime_position(stock_code)

        # 如果实时数据获取失败，降级到历史数据
        if not position:
          self.logger.warning(f"实时持仓数据获取失败，降级到数据库数据: {stock_code}")
          position = await self._get_historical_position(stock_code)

        # 更新缓存
        if position:
          self._update_position_cache(cache_key, [position])

      # 如果需要获取最新价格
      if with_latest_price and position and position.volume > 0:
        latest_price = await self.get_latest_price(stock_code)
        position.last_price = latest_price.last_price if latest_price else 0

      return position

    except Exception as e:
      self.logger.error(f"获取单个持仓失败: {stock_code}, {e}")
      # 最后的降级：尝试数据库数据
      try:
        return await self._get_historical_position(stock_code)
      except Exception as fallback_error:
        self.logger.error(f"数据库持仓数据降级也失败: {stock_code}, {fallback_error}")
        return None

  async def get_market_data(self, stock_codes: List[str]) -> Dict[str, Any]:
    """
    获取完整市场数据（扩展方法）
    可以根据需要添加更多市场数据获取功能
    """
    # 目前复用 get_latest_prices 的逻辑
    return await self.get_latest_prices(stock_codes)

  async def shutdown(self) -> None:
    """关闭数据提供者"""
    try:
      await self.adapter_manager.shutdown_all()
      from .unified_subscription_manager import unified_subscription_manager

      await unified_subscription_manager.shutdown()
      self.clear_cache()
      self.logger.info("数据提供者已关闭")
    except Exception as e:
      self.logger.error(f"关闭数据提供者失败: {e}")

  async def get_statistics(self) -> Dict[str, Any]:
    """获取统计信息"""
    with self.cache_lock:
      price_cache_count = len(self.price_cache)
      position_cache_count = len(self.position_cache)

    return {
      "is_initialized": self._is_initialized,
      "is_connected": self._is_connected,
      "price_cache_count": price_cache_count,
      "position_cache_count": position_cache_count,
      "cache_config": {
        "default_ttl": self.cache_config.default_ttl,
        "is_trading_hours": await self.cache_config.is_trading_hours(),
        "current_price_ttl": await self.cache_config.get_ttl("latest_price"),
        "current_position_ttl": await self.cache_config.get_ttl("positions"),
      },
      "realtime_stats": self.adapter_manager.realtime_adapter.get_statistics(),
    }


# 全局实例
market_data_service = MarketDataService()
