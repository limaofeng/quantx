"""
交易时间服务层
提供统一的交易时间判断逻辑，整合节假日检查和交易时间段检查
"""

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from services.holiday_service import HolidayService
from core.utils import time_utils


class TradingTimeService:
  """交易时间服务 - 统一处理所有交易时间相关的判断逻辑"""

  def __init__(self):
    self.holiday_service = HolidayService()
    # 交易日缓存（按日缓存，自动清理过期数据）
    self._trading_day_cache = {}  # {market_date: bool}
    # 缓存保留天数（保留今天前后的数据）
    self._cache_retention_days = 7

    # 默认交易时间配置（可以后续从配置文件读取）
    self.trading_hours_config = {
      "SH": [  # 上海证券交易所
        (time(9, 30), time(11, 30)),  # 上午9:30-11:30
        (time(13, 0), time(15, 0)),  # 下午13:00-15:00
      ],
      "SZ": [  # 深圳证券交易所
        (time(9, 30), time(11, 30)),  # 上午9:30-11:30
        (time(13, 0), time(15, 0)),  # 下午13:00-15:00
      ],
      "default": [  # 默认交易时间
        (time(9, 30), time(11, 30)),  # 上午9:30-11:30
        (time(13, 0), time(15, 0)),  # 下午13:00-15:00
      ],
    }

  async def is_trading_day(
    self, market: str = "SH", check_date: Optional[date] = None
  ) -> bool:
    """
    检查指定日期是否为交易日

    Args:
        market: 市场代码，默认为"SH"（上海）
        check_date: 要检查的日期，默认为今天

    Returns:
        bool: 是否为交易日
    """
    if check_date is None:
      check_date = time_utils.today()

    # 自动清理过期缓存
    self._auto_cleanup_cache()

    # 生成缓存key
    cache_key = f"{market}_{check_date}"

    # 检查缓存
    if cache_key in self._trading_day_cache:
      return self._trading_day_cache[cache_key]

    # 检查是否为工作日（周一到周五）
    if check_date.weekday() >= 5:  # 周六(5)、周日(6)
      result = False
    else:
      # 检查是否为节假日
      is_holiday = await self.holiday_service.is_holiday(market, check_date)
      result = not is_holiday

    # 缓存结果
    self._trading_day_cache[cache_key] = result
    return result

  async def is_trading_hours(
    self, market: str = "SH", check_datetime: Optional[datetime] = None
  ) -> bool:
    """
    检查指定时间是否为交易时间

    Args:
        market: 市场代码，默认为"SH"（上海）
        check_datetime: 要检查的时间，默认为当前时间

    Returns:
        bool: 是否为交易时间
    """
    if check_datetime is None:
      check_datetime = time_utils.now()

    # 首先检查是否为交易日
    is_trading_day = await self.is_trading_day(market, check_datetime.date())
    if not is_trading_day:
      return False

    # 检查是否在交易时间段内
    trading_hours = self.get_trading_hours(market)
    current_time = check_datetime.time()

    return any(start <= current_time <= end for start, end in trading_hours)

  def get_trading_hours(self, market: str = "SH") -> List[Tuple[time, time]]:
    """
    获取指定市场的交易时间段

    Args:
        market: 市场代码

    Returns:
        List[Tuple[time, time]]: 交易时间段列表
    """
    return self.trading_hours_config.get(market, self.trading_hours_config["default"])

  async def get_next_trading_day(
    self, market: str = "SH", from_date: Optional[date] = None
  ) -> date:
    """
    获取下一个交易日

    Args:
        market: 市场代码，默认为"SH"（上海）
        from_date: 起始日期，默认为今天

    Returns:
        date: 下一个交易日
    """
    if from_date is None:
      from_date = time_utils.today()

    current_date = from_date + timedelta(days=1)
    max_days = 30  # 最多检查30天，避免无限循环

    for _ in range(max_days):
      if await self.is_trading_day(market, current_date):
        return current_date
      current_date += timedelta(days=1)

    raise ValueError(f"未能在{max_days}天内找到下一个交易日")

  async def get_previous_trading_day(
    self, market: str = "SH", from_date: Optional[date] = None
  ) -> date:
    """
    获取上一个交易日

    Args:
        market: 市场代码，默认为"SH"（上海）
        from_date: 起始日期，默认为今天

    Returns:
        date: 上一个交易日
    """
    if from_date is None:
      from_date = time_utils.today()

    current_date = from_date - timedelta(days=1)
    max_days = 30  # 最多检查30天，避免无限循环

    for _ in range(max_days):
      if await self.is_trading_day(market, current_date):
        return current_date
      current_date -= timedelta(days=1)

    raise ValueError(f"未能在{max_days}天内找到上一个交易日")

  def _auto_cleanup_cache(self):
    """自动清理过期的缓存数据"""
    if not self._trading_day_cache:
      return

    today = time_utils.today()
    cutoff_date = today - timedelta(days=self._cache_retention_days)
    future_cutoff = today + timedelta(days=self._cache_retention_days)

    # 清理过期的缓存条目
    keys_to_remove = []
    for cache_key in self._trading_day_cache:
      try:
        # 解析缓存key中的日期 (格式: market_YYYY-MM-DD)
        _, date_str = cache_key.rsplit("_", 1)
        cache_date = date.fromisoformat(date_str)

        # 删除过于久远的历史数据和过于遥远的未来数据
        if cache_date < cutoff_date or cache_date > future_cutoff:
          keys_to_remove.append(cache_key)
      except (ValueError, AttributeError):
        # 如果日期解析失败，删除这个异常的缓存条目
        keys_to_remove.append(cache_key)

    # 执行清理
    for key in keys_to_remove:
      self._trading_day_cache.pop(key, None)

  def get_cache_info(self) -> Dict[str, Any]:
    """
    获取缓存信息（用于调试和监控）

    Returns:
        Dict: 缓存统计信息
    """
    return {
      "cache_size": len(self._trading_day_cache),
      "cache_keys": list(self._trading_day_cache.keys()),
      "retention_days": self._cache_retention_days,
    }

  def set_trading_hours(self, market: str, trading_hours: List[Tuple[time, time]]):
    """
    设置指定市场的交易时间

    Args:
        market: 市场代码
        trading_hours: 交易时间段列表
    """
    self.trading_hours_config[market] = trading_hours
    # 交易时间设置不影响交易日判断，无需清除缓存


class TradingDateHelper:
  """Helper for trading date operations (T+1, calendar, and checks)."""

  def __init__(self, trading_time_service: Optional[TradingTimeService] = None):
    self.trading_time_service = trading_time_service or TradingTimeService()

  async def is_trading_date(
    self, market: str = "SH", check_date: Optional[date] = None
  ) -> bool:
    """Check whether a date is a trading day."""
    return await self.trading_time_service.is_trading_day(market, check_date)

  async def get_next_trading_date(
    self, market: str = "SH", from_date: Optional[date] = None
  ) -> date:
    """Get the next trading date (T+1) after the given date."""
    return await self.trading_time_service.get_next_trading_day(market, from_date)

  async def get_trading_calendar(
    self,
    market: str = "SH",
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
  ) -> List[date]:
    """Get trading dates within the given range (inclusive)."""
    if start_date is None:
      start_date = time_utils.today()
    if end_date is None:
      end_date = start_date
    if end_date < start_date:
      return []

    years = range(start_date.year, end_date.year + 1)
    holiday_dates: Set[date] = set()
    try:
      for year in years:
        holidays = await self.trading_time_service.holiday_service.get_holidays(
          market, year
        )
        holiday_dates.update(holiday.date for holiday in holidays)
    except Exception:
      # 回退到逐日判断，保持旧行为。
      holiday_dates = set()
      use_bulk_holidays = False
    else:
      use_bulk_holidays = True

    trading_dates: List[date] = []
    current_date = start_date
    total_days = (end_date - start_date).days + 1

    for _ in range(total_days):
      if use_bulk_holidays:
        is_trading_date = (
          current_date.weekday() < 5 and current_date not in holiday_dates
        )
        self.trading_time_service._trading_day_cache[
          f"{market}_{current_date}"
        ] = is_trading_date
      else:
        is_trading_date = await self.is_trading_date(market, current_date)

      if is_trading_date:
        trading_dates.append(current_date)
      current_date += timedelta(days=1)

    return trading_dates
