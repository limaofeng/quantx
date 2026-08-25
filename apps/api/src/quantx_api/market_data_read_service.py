"""API read facade: database truth plus transient Engine intraday snapshots."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from quantx_infrastructure.core.data.tick_identity import merge_ticks_losslessly
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.models.tick import Tick
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
)
from quantx_infrastructure.services.latest_market_quote_cache import (
  latest_market_quote_cache,
)
from quantx_infrastructure.services.position_service import PositionService
from quantx_infrastructure.services.runtime_market_query_bridge import (
  runtime_market_query_bridge,
)

logger = logging.getLogger(__name__)


def _datetime(value: Any) -> datetime | None:
  if value is None or isinstance(value, datetime):
    return value
  if isinstance(value, str):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
  return None


def _time_key(value: Any) -> str:
  parsed = _datetime(value)
  if parsed is None:
    return ""
  return time_utils.to_shanghai(parsed).isoformat()


def _model(model_type, payload: dict[str, Any]):
  value = dict(payload)
  if "time" in value:
    value["time"] = _datetime(value["time"])
  return model_type(**value)


class ApiMarketDataReadService:
  def __init__(self) -> None:
    self.historical = HistoricalMarketDataService()
    self.positions = PositionService()

  async def _runtime_items(
    self,
    operation: str,
    payload: dict[str, Any],
  ) -> list[dict[str, Any]]:
    try:
      response = await runtime_market_query_bridge.query(operation, payload)
    except Exception as exc:
      logger.debug("Engine intraday query unavailable: %s", exc.__class__.__name__)
      return []
    return list((response or {}).get("items") or [])

  async def get_klines(
    self,
    *,
    stock_code: str,
    period: str = "1m",
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
    dividend_type: str = "none",
    order: str = "desc",
  ) -> list[KLine]:
    historical_read = self.historical.get_kline_data(
      stock_code=stock_code,
      period=period,
      start_time=start_time,
      end_time=end_time,
      limit=None,
      dividend_type=dividend_type,
      order="asc",
    )
    if period == "1m" and dividend_type == "none":
      historical, rows = await asyncio.gather(
        historical_read,
        self._runtime_items(
          "warm_klines",
          {
            "stock_code": stock_code,
            "start_time": start_time.isoformat() if start_time else None,
            "end_time": end_time.isoformat() if end_time else None,
          },
        ),
      )
      warm = [_model(KLine, row) for row in rows]
    else:
      historical = await historical_read
      warm = []
    merged = {
      _time_key(item.time): item
      for item in [*historical, *warm]
      if _time_key(item.time)
    }
    values = sorted(merged.values(), key=lambda item: _time_key(item.time))
    if (order or "desc").lower() == "desc":
      values.reverse()
    return values[:limit] if limit is not None and limit > 0 else values

  async def get_ticks(
    self,
    *,
    stock_code: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = 6000,
    dividend_type: str = "none",
    order: str = "desc",
  ) -> list[Tick]:
    historical_read = self.historical.get_tick_data(
      stock_code=stock_code,
      start_time=start_time,
      end_time=end_time,
      limit=None,
      dividend_type=dividend_type,
      order="asc",
    )
    if dividend_type == "none":
      historical, rows = await asyncio.gather(
        historical_read,
        self._runtime_items(
          "warm_ticks",
          {
            "stock_code": stock_code,
            "start_time": start_time.isoformat() if start_time else None,
            "end_time": end_time.isoformat() if end_time else None,
          },
        ),
      )
      warm = [_model(Tick, row) for row in rows]
    else:
      historical = await historical_read
      warm = []
    values = merge_ticks_losslessly(historical, warm)
    if (order or "desc").lower() == "desc":
      values.reverse()
    return values[:limit] if limit is not None and limit > 0 else values

  async def get_latest_prices(
    self,
    stock_codes: list[str],
  ) -> dict[str, Tick]:
    if not stock_codes:
      return {}
    ticks = await latest_market_quote_cache.get_ticks(stock_codes)
    return {str(tick.stock_code): tick for tick in ticks}

  async def get_market_index_snapshots(
    self,
    stock_codes: list[str],
  ) -> list[tuple[str, Tick | None, KLine | None]]:
    """Read all dashboard index fallbacks with two backend round trips."""
    symbols = list(
      dict.fromkeys(
        str(code or "").strip().upper() for code in stock_codes if code
      )
    )
    if not symbols:
      return []
    end = time_utils.now()
    start = end - timedelta(days=45)
    ticks, daily_frames = await asyncio.gather(
      latest_market_quote_cache.get_ticks(symbols),
      asyncio.to_thread(
        self.historical.kline_repo.find_daily_batch,
        symbols,
        start,
        end,
        use_cache=False,
      ),
    )
    tick_by_symbol = {str(tick.stock_code).upper(): tick for tick in ticks}
    daily_by_symbol: dict[str, KLine] = {}
    for symbol, frame in daily_frames.items():
      if frame.empty:
        continue
      latest = frame.sort_values("time").iloc[-1].to_dict()
      daily_by_symbol[str(symbol).upper()] = _model(KLine, latest)
    return [
      (symbol, tick_by_symbol.get(symbol), daily_by_symbol.get(symbol))
      for symbol in symbols
    ]

  async def get_latest_price(self, stock_code: str) -> Tick | None:
    return (await self.get_latest_prices([stock_code])).get(stock_code)

  async def get_position(
    self,
    stock_code: str,
    with_latest_price: bool = False,
  ):
    position = await self.positions.get_position_by_stock(stock_code)
    if position is not None and with_latest_price:
      tick = await self.get_latest_price(stock_code)
      if tick is not None:
        position.last_price = tick.last_price
    return position

  async def get_bars(
    self,
    *,
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    interval: str,
  ) -> list[KLine]:
    return await self.get_klines(
      stock_code=symbol,
      period=interval,
      start_time=start_date,
      end_time=end_date,
      order="asc",
    )


market_data_read_service = ApiMarketDataReadService()
