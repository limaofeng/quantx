"""Bounded local HTTP Tick stream for historical computation consumers."""

from dataclasses import fields
from datetime import timedelta
from math import ceil

import pandas as pd
from quantx_contracts.market_data_service import HistoryRead

from quantx_infrastructure.core.data.tick_identity import tick_query_end_time
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.models.tick import Tick
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalTickPaginationError,
  _strict_source_identity,
  _validate_storage_time,
)
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)


def _ticks(records):
  frame = pd.DataFrame(records)
  for field in fields(Tick):
    converter = field.metadata.get("converter")
    if converter is not None:
      frame = converter.convert_to_entity_attribute(frame, field.name)
  return [Tick(**row) for row in frame.to_dict(orient="records")]


class LocalHistoricalTickReader:
  """Own one HTTP client per consumed stream; never open a historical repository."""

  async def read_daily_klines(self, *, stock_code, trading_date):
    """Read a daily reference; two rows allow the consumer to reject duplicates."""
    client = LocalMarketDataClient()
    try:
      page = await client.read_history(
        HistoryRead(
          instrument=stock_code,
          period="1d",
          trading_date=trading_date,
          page_size=2,
        )
      )
      return [KLine(**row) for row in page.records]
    finally:
      await client.close()

  async def iter_tick_pages(
    self,
    *,
    stock_code,
    start_time,
    end_time,
    page_size=10_000,
    max_pages=1024,
    max_source_ticks=2_000_000,
  ):
    if (
      any(
        type(value) is not int or value <= 0
        for value in (page_size, max_pages, max_source_ticks)
      )
      or page_size > 10_000
    ):
      raise HistoricalTickPaginationError("invalid local Tick stream budget")
    start = time_utils.to_shanghai(start_time, keep_tz=True)
    end = time_utils.to_shanghai(end_time, keep_tz=True)
    if end < start or (end.date() - start.date()).days >= 366:
      raise HistoricalTickPaginationError(
        "local Tick stream requires an ordered window of at most 366 dates"
      )
    # Bounds refer to source milliseconds, including all ordinals at the end.
    start = start.replace(microsecond=start.microsecond // 1000 * 1000)
    end = tick_query_end_time(end)
    wire_size = min(page_size, 1000)
    wire_limit = max_pages * ceil(page_size / wire_size)
    data_reads = row_count = page_count = 0
    previous_key = None
    buffer = []
    current = start.date()
    client = LocalMarketDataClient()
    try:
      while current <= end.date():
        query = HistoryRead(
          instrument=stock_code,
          period="tick",
          trading_date=current,
          page_size=wire_size,
        )
        lower, _ = query.bounds()
        if start > lower:
          query.after = start - timedelta(microseconds=1)
        while True:
          page = await client.read_history(query)
          if not page.records:
            break
          reached_end = False
          selected = []
          for record in page.records:
            tick = Tick(**record)
            key = _strict_source_identity(tick)
            _validate_storage_time(tick, key)
            record["source_time_ms"], record["tick_ordinal"] = key
            if previous_key is not None and key <= previous_key:
              raise HistoricalTickPaginationError(
                "local Tick source identity did not advance"
              )
            previous_key = key
            if tick.time > end:
              reached_end = True
              break
            if tick.time < start:
              raise HistoricalTickPaginationError(
                "local Tick source precedes requested window"
              )
            selected.append(record)
          row_count += len(selected)
          if row_count > max_source_ticks:
            raise HistoricalTickPaginationError(
              "local Tick source row budget exhausted"
            )
          if selected:
            data_reads += 1
            if data_reads > wire_limit:
              raise HistoricalTickPaginationError(
                "local Tick HTTP page budget exhausted"
              )
            buffer.extend(_ticks(selected))
          while len(buffer) >= page_size:
            page_count += 1
            if page_count > max_pages:
              raise HistoricalTickPaginationError(
                "local Tick output page budget exhausted"
              )
            yield buffer[:page_size]
            del buffer[:page_size]
          if reached_end:
            break
          # Short pages are not exhaustion. Even a full final budget gets an
          # empty probe; another nonempty response fails the original budget.
          query.after = page.next_after
        if current == end.date():
          break
        current += timedelta(days=1)
      if buffer:
        if page_count >= max_pages:
          raise HistoricalTickPaginationError("local Tick output page budget exhausted")
        yield buffer
    except HistoricalTickPaginationError:
      raise
    except ValueError as exc:
      raise HistoricalTickPaginationError(
        "local Tick response integrity failed"
      ) from exc
    finally:
      await client.close()
