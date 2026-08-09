import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import List, Optional

from quantx_infrastructure.services.engine_command_service import (
  engine_command_service,
)
from quantx_infrastructure.services.latest_market_quote_cache import (
  latest_market_quote_cache,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper

from quantx_api.market_data_read_service import (
  market_data_read_service as market_data_service,
)

from ..types import (
  IntradayWarmCacheStatus,
  KLineData,
  KLinePage,
  PageDirection,
  PageInfo,
  StockQuote,
  TickData,
)

logger = logging.getLogger(__name__)
trading_date_helper = TradingDateHelper()
TRADING_CALENDAR_TIMEOUT_SECONDS = 1.0



class MarketDataResolver:
  @staticmethod
  async def get_latest_market_quotes(stock_codes: List[str]) -> List[StockQuote]:
    ticks = await latest_market_quote_cache.get_ticks(stock_codes)
    return [StockQuote.from_tick(tick) for tick in ticks]

  @staticmethod
  def _normalize_for_compare(
    left: Optional[datetime], right: Optional[datetime]
  ) -> tuple[Optional[datetime], Optional[datetime]]:
    if left is None or right is None:
      return left, right
    if (left.tzinfo is None) != (right.tzinfo is None):
      if left.tzinfo is not None:
        left = left.replace(tzinfo=None)
      if right.tzinfo is not None:
        right = right.replace(tzinfo=None)
    return left, right

  @staticmethod
  def _exclude_cursor(klines: List, cursor: Optional[datetime]) -> List:
    if cursor is None:
      return klines
    filtered = []
    for kline in klines:
      kline_time, cursor_time = MarketDataResolver._normalize_for_compare(
        kline.time, cursor
      )
      if kline_time != cursor_time:
        filtered.append(kline)
    return filtered

  @staticmethod
  async def _has_kline_after(
    stock_code: str,
    period: str,
    time: Optional[datetime],
    dividend_type: str,
  ) -> bool:
    if time is None:
      return False
    probe = await market_data_service.get_klines(
      stock_code=stock_code,
      period=period,
      start_time=time,
      limit=2,
      dividend_type=dividend_type,
      order="asc",
    )
    for kline in probe:
      kline_time, cursor_time = MarketDataResolver._normalize_for_compare(
        kline.time, time
      )
      if kline_time and cursor_time and kline_time > cursor_time:
        return True
    return False

  @staticmethod
  async def _has_kline_before(
    stock_code: str,
    period: str,
    time: Optional[datetime],
    dividend_type: str,
  ) -> bool:
    if time is None:
      return False
    probe = await market_data_service.get_klines(
      stock_code=stock_code,
      period=period,
      end_time=time,
      limit=2,
      dividend_type=dividend_type,
      order="desc",
    )
    for kline in probe:
      kline_time, cursor_time = MarketDataResolver._normalize_for_compare(
        kline.time, time
      )
      if kline_time and cursor_time and kline_time < cursor_time:
        return True
    return False

  @staticmethod
  async def get_klines(
    stock_code: str,
    period: str = "1m",
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
    dividend_type: str = "none",
    order: str = "desc",
  ) -> List[KLineData]:
    if limit is not None and limit <= 0:
      return []
    logger.debug(
      "get_klines: stock_code=%s, period=%s, start_time=%s, end_time=%s, "
      "limit=%s, dividend_type=%s, order=%s",
      stock_code,
      period,
      start_time,
      end_time,
      limit,
      dividend_type,
      order,
    )
    klines = await market_data_service.get_klines(
      stock_code=stock_code,
      period=period,
      start_time=start_time,
      end_time=end_time,
      limit=limit,
      dividend_type=dividend_type,
      order=order,
    )

    return [KLineData.from_kline(kline) for kline in klines]

  @staticmethod
  async def get_klines_page(
    stock_code: str,
    period: str = "1m",
    cursor: Optional[datetime] = None,
    limit: int = 200,
    dividend_type: str = "none",
    direction: PageDirection = PageDirection.PREV,
    order: str = "desc",
  ) -> KLinePage:
    if limit <= 0:

      return KLinePage(
        items=[],
        page_info=PageInfo(
          has_next_page=False,
          has_previous_page=False,
          start_cursor=None,
          end_cursor=None,
        ),
      )

    fetch_limit = limit + 1
    has_previous_page = False
    has_next_page = False
    order = (order or "desc").lower()
    if order not in {"asc", "desc"}:
      order = "desc"
    fetch_order = "desc" if direction == PageDirection.PREV else "asc"

    # 默认时间范围由 historical_market_data_service.get_kline_data 根据 period 自动设置
    if direction == PageDirection.PREV:
      # 向前翻页（获取更旧的数据）
      klines = await market_data_service.get_klines(
        stock_code=stock_code,
        period=period,
        end_time=cursor,
        limit=fetch_limit,
        dividend_type=dividend_type,
        order=fetch_order,
      )
    else:
      # 向后翻页（获取更新的数据）
      klines = await market_data_service.get_klines(
        stock_code=stock_code,
        period=period,
        start_time=cursor,
        limit=fetch_limit,
        dividend_type=dividend_type,
        order=fetch_order,
      )

    klines = MarketDataResolver._exclude_cursor(klines, cursor)

    has_more = len(klines) > limit
    if has_more:
      klines = klines[:limit]

    if (fetch_order == "desc" and order == "asc") or (
      fetch_order == "asc" and order == "desc"
    ):
      klines = list(reversed(klines))

    if klines:
      times = [kline.time for kline in klines if kline.time is not None]
      oldest_time = min(times) if times else None
      newest_time = max(times) if times else None
    else:
      oldest_time = cursor
      newest_time = cursor

    # 优化后的分页判断逻辑
    has_newer = False
    has_older = False

    if cursor is None:
        # 初次查询（请求最新数据）
        has_newer = False # 必然没有更新的
        has_older = has_more # 往下拿到的数据，如果超出了限制，就有更老的记录
    else:
        if direction == PageDirection.PREV:
            # 此时往历史方向查
            has_older = has_more # 从当前向过去要的数据
            has_newer = await MarketDataResolver._has_kline_after(
                stock_code=stock_code,
                period=period,
                time=newest_time,
                dividend_type=dividend_type,
            )
        else:
            # 此时往未来的方向查
            has_newer = has_more # 从当前向未来要到的数据
            has_older = await MarketDataResolver._has_kline_before(
                stock_code=stock_code,
                period=period,
                time=oldest_time,
                dividend_type=dividend_type,
            )

    if order == "desc":
      has_next_page = has_older
      has_previous_page = has_newer
    else:
      has_next_page = has_newer
      has_previous_page = has_older

    items = [KLineData.from_kline(kline) for kline in klines]
    start_cursor = items[0].time.isoformat() if items else None
    end_cursor = items[-1].time.isoformat() if items else None

    return KLinePage(
      items=items,
      page_info=PageInfo(
        has_next_page=has_next_page,
        has_previous_page=has_previous_page,
        start_cursor=start_cursor,
        end_cursor=end_cursor,
      ),
    )

  @staticmethod
  async def get_ticks(
    stock_code: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = 6000,
    dividend_type: str = "none",
    order: str = "desc",
  ) -> List[TickData]:
    ticks = await market_data_service.get_ticks(
      stock_code=stock_code,
      start_time=start_time,
      end_time=end_time,
      limit=limit or 6000,
      order=order,
      dividend_type=dividend_type,
    )

    return [TickData.from_tick(tick) for tick in ticks]

  @staticmethod
  async def get_trading_calendar(
    market: str = "SH",
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
  ) -> List[date]:
    try:
      return await asyncio.wait_for(
        trading_date_helper.get_trading_calendar(
          market=market,
          start_date=start_date,
          end_date=end_date,
        ),
        timeout=TRADING_CALENDAR_TIMEOUT_SECONDS,
      )
    except asyncio.TimeoutError:
      logger.warning(
        "交易日历查询超时，使用工作日保守降级: market=%s, start=%s, end=%s",
        market,
        start_date,
        end_date,
      )
      start = start_date or date.today()
      end = end_date or start
      if end < start:
        return []
      days = []
      current = start
      while current <= end:
        if current.weekday() < 5:
          days.append(current)
        current += timedelta(days=1)
      return days

  @staticmethod
  async def get_intraday_warm_cache_status(
    symbols: Optional[List[str]] = None,
  ) -> List[IntradayWarmCacheStatus]:
    receipt = await engine_command_service.request(
      "WARM_CACHE_STATUS",
      {"symbols": symbols},
      aggregate_id="intraday-warm-cache",
      idempotency_key=f"warm-cache-status:{uuid.uuid4()}",
    )
    if receipt.status == "FAILED":
      raise RuntimeError(receipt.error or "Engine 热缓存查询失败")
    if receipt.status != "SUCCEEDED":
      raise RuntimeError(f"Engine 热缓存查询超时: {receipt.message_id}")
    return [
      IntradayWarmCacheStatus.from_status(row)
      for row in (receipt.result or {}).get("items", [])
    ]
