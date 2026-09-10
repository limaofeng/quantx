"""API historical queries through bounded local data and factor HTTP reads."""

import asyncio
import json
import logging
from contextlib import aclosing
from datetime import timedelta
from types import SimpleNamespace

from quantx_contracts.development_reference import CalendarRequest
from quantx_contracts.divid_factor_read import DividFactorRead
from quantx_contracts.market_data_service import HistoryRead

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.kline import KLine

from .historical_price_transforms import HistoricalPriceTransforms
from .local_historical_tick_reader import LocalHistoricalTickReader
from .local_market_data_client import LocalMarketDataClient

INTRADAY = {"5m": "5min", "15m": "15min", "30m": "30min", "60m": "60min", "1h": "60min"}
DAILY = {"1w": "W", "1mon": "M", "1q": "Q", "1hy": "2Q", "1y": "A"}
MAX_ROWS = 200_000
MAX_BYTES = 64 * 1024 * 1024


class LocalHistoricalMarketReader(HistoricalPriceTransforms):
  def __init__(self):
    self.logger = logging.getLogger(__name__)
    self.divid_factor_service_async = self

  async def get_divid_factors(self, *, stock_code, start_time, end_time, limit=None):
    client = LocalMarketDataClient()
    try:
      result = await client.read_divid_factors(
        DividFactorRead(
          instrument=stock_code,
          start_date=start_time.date(),
          end_date=end_time.date(),
        )
      )
      return [
        SimpleNamespace(time=time_utils.to_shanghai(row.time), dr=row.dr)
        for row in result.records
      ]
    finally:
      await client.close()

  def _window(self, start, end, days):
    end = time_utils.to_shanghai(end or time_utils.now(), keep_tz=True)
    start = (
      time_utils.to_shanghai(start, keep_tz=True)
      if start
      else end - timedelta(days=days)
    )
    if end < start or (end.date() - start.date()).days >= 366:
      raise ValueError(
        "historical query requires an ordered window of at most 366 dates"
      )
    return start, end

  async def _trading_days(self, client, start, end):
    holidays = set()
    for year in range(start.year, end.year + 1):
      snapshot = await client.read_calendar(CalendarRequest(year=year))
      holidays.update(item.date for item in snapshot.holidays)
    return [
      start.date() + timedelta(days=i)
      for i in range((end.date() - start.date()).days + 1)
      if (start.date() + timedelta(days=i)).weekday() < 5
      and start.date() + timedelta(days=i) not in holidays
    ]

  def _check_result_budget(self, rows, size):
    if len(rows) > MAX_ROWS or size > MAX_BYTES:
      raise ValueError("historical query result budget exhausted")

  async def get_kline_data(
    self,
    stock_code,
    period="1m",
    start_time=None,
    end_time=None,
    limit=None,
    dividend_type="none",
    order="asc",
  ):
    if period not in {"1m", "1d", *INTRADAY, *DAILY}:
      raise ValueError("unsupported historical KLine period")
    days = (
      3
      if period == "1m"
      else 7
      if period in {"5m", "15m"}
      else 365
      if period in DAILY
      else 30
    )
    start, end = self._window(start_time, end_time, days)
    base = "1d" if period in DAILY or period == "1d" else "1m"
    rows, size = [], 0
    client = LocalMarketDataClient()
    try:
      async with asyncio.timeout(60):
        for day in await self._trading_days(client, start, end):
          query = HistoryRead(
            instrument=stock_code, period=base, trading_date=day, page_size=1000
          )
          lower, _ = query.bounds()
          if start > lower:
            query.after = start - timedelta(microseconds=1)
          while True:
            page = await client.read_history(query)
            if not page.records:
              break
            for record in page.records:
              if record["time"] > end:
                break
              size += len(json.dumps(record, default=str).encode())
              rows.append(
                KLine(
                  **{
                    **record,
                    "time": time_utils.to_shanghai(record["time"], keep_tz=True),
                  }
                )
              )
              self._check_result_budget(rows, size)
            if page.records[-1]["time"] > end:
              break
            query.after = page.next_after
    finally:
      await client.close()
    if period in INTRADAY or period in DAILY:
      rows = self._resample_klines(rows, stock_code, period, (INTRADAY | DAILY)[period])
    rows = await self._apply_dividend_adjustment_async(rows, stock_code, dividend_type)
    if (order or "asc").lower() == "desc":
      rows.reverse()
    return rows[:limit] if limit is not None and limit > 0 else rows

  async def get_tick_data(
    self,
    stock_code,
    start_time=None,
    end_time=None,
    limit=None,
    dividend_type="none",
    order="asc",
  ):
    start, end = self._window(start_time, end_time, 1)
    rows, size = [], 0
    async with asyncio.timeout(60):
      calendar_client = LocalMarketDataClient()
      try:
        days = await self._trading_days(calendar_client, start, end)
      finally:
        await calendar_client.close()
      async with aclosing(
        LocalHistoricalTickReader().iter_tick_pages(
          stock_code=stock_code,
          start_time=start,
          end_time=end,
          page_size=1000,
          max_pages=200,
          max_source_ticks=MAX_ROWS,
          trading_dates=days,
        )
      ) as pages:
        async for page in pages:
          for tick in page:
            size += len(json.dumps(vars(tick), default=str).encode())
            rows.append(tick)
            self._check_result_budget(rows, size)
    rows = await self._apply_tick_dividend_adjustment_async(
      rows, stock_code, dividend_type
    )
    if (order or "asc").lower() == "desc":
      rows.reverse()
    return rows[:limit] if limit is not None and limit > 0 else rows
