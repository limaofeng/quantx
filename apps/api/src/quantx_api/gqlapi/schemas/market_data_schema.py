from datetime import date, datetime
from typing import List, Optional

import strawberry

from ..resolvers.market_data import MarketDataResolver
from ..types import (
  DividendType,
  IntradayWarmCacheStatus,
  KLineData,
  KLinePage,
  KLinePageInput,
  KLinePeriod,
  StockQuote,
  TickData,
)


@strawberry.type(description="市场数据相关查询")
class MarketDataQuery:
  @strawberry.field(description="批量读取 Engine 热缓存中的最新行情")
  async def latest_market_quotes(
    self,
    stock_list: List[str],
  ) -> List[StockQuote]:
    if len(stock_list) > 100:
      raise ValueError("单次最多查询 100 个股票代码")
    return await MarketDataResolver.get_latest_market_quotes(stock_list)

  @strawberry.field(description="获取历史K线数据")
  async def klines(
    self,
    stock_code: str,
    period: KLinePeriod = KLinePeriod.MIN_1,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
    dividend_type: DividendType = DividendType.NONE,
    order: str = "desc",
  ) -> List[KLineData]:
    return await MarketDataResolver.get_klines(
      stock_code=stock_code,
      period=period.value,
      start_time=start_time,
      end_time=end_time,
      limit=limit,
      dividend_type=dividend_type.value,
      order=order,
    )

  @strawberry.field(description="分页获取历史K线数据")
  async def klines_page(self, page: KLinePageInput) -> KLinePage:
    return await MarketDataResolver.get_klines_page(
      stock_code=page.stock_code,
      period=page.period.value,
      cursor=page.cursor,
      limit=page.limit,
      dividend_type=page.dividend_type.value,
      direction=page.direction,
      order=page.order,
    )

  @strawberry.field(description="获取历史Tick数据")
  async def ticks(
    self,
    stock_code: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
    order: str = "desc",
  ) -> List[TickData]:
    return await MarketDataResolver.get_ticks(
      stock_code=stock_code,
      start_time=start_time,
      end_time=end_time,
      limit=limit,
      order=order,
    )

  @strawberry.field(description="查看日内热缓存状态")
  async def intraday_warm_cache_status(
    self,
    symbols: Optional[List[str]] = None,
  ) -> List[IntradayWarmCacheStatus]:
    return await MarketDataResolver.get_intraday_warm_cache_status(symbols)

  @strawberry.field(description="获取交易日历（指定区间内的交易日列表）")
  async def trading_calendar(
    self,
    market: str = "SH",
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
  ) -> List[date]:
    return await MarketDataResolver.get_trading_calendar(
      market=market,
      start_date=start_date,
      end_date=end_date,
    )
