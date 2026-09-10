"""
历史市场数据服务
处理K线数据和历史价格数据的业务逻辑
"""

import asyncio
import logging
import math
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Dict, List, Optional, Union

import pandas as pd
from quantx_contracts import HISTORICAL_TICK_ORDINALS_PER_MILLISECOND

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.models.tick import Tick
from quantx_infrastructure.repositories.kline_repository import KLineRepository
from quantx_infrastructure.repositories.tick_repository import TickRepository
from quantx_infrastructure.services.divid_factor_service import DividFactorService
from quantx_infrastructure.services.instrument_service import InstrumentService

from .historical_price_transforms import HistoricalPriceTransforms


class HistoricalTickPaginationError(ValueError):
  """Raised when a historical Tick stream cannot prove completeness."""


def _identity_value_is_missing(value: object) -> bool:
  """Recognize nulls introduced by supported historical-store codecs.

  Influx Arrow returns an absent nullable ``int64`` field as ``None``.  Its
  pandas bridge represents the same absence as ``float('nan')`` before the
  row is constructed as a ``Tick``.  Neither value carries source identity;
  it must be reported as missing rather than as a lossy numeric conversion.
  """

  if value is None or type(value).__name__ in {"NAType", "NaTType"}:
    return True
  if isinstance(value, str) and not value.strip():
    return True
  try:
    return bool(math.isnan(value))
  except (TypeError, ValueError, OverflowError):
    return False


def _strict_source_identity(tick: Tick) -> tuple[int, int]:
  source_value = getattr(tick, "source_time_ms", None)
  ordinal_value = getattr(tick, "tick_ordinal", None)
  source_missing = _identity_value_is_missing(source_value) or (
    not isinstance(source_value, bool) and source_value == 0
  )
  ordinal_missing = _identity_value_is_missing(ordinal_value)
  if source_missing:
    # Legacy Influx points predate the explicit source-identity fields, but
    # their primary timestamp is still a lossless keyset cursor.  Accept only
    # the unambiguous legacy shape (missing source and absent/zero ordinal),
    # derive the pair from storage time, and write it onto the in-memory Tick so
    # downstream strict validators observe the same authoritative identity.
    if not ordinal_missing and ordinal_value != 0:
      raise HistoricalTickPaginationError(
        "historical Tick source identity is partially missing"
      )
    raw_time = getattr(tick, "time", None)
    if hasattr(raw_time, "to_pydatetime"):
      raw_time = raw_time.to_pydatetime()
    if not isinstance(raw_time, datetime):
      raise HistoricalTickPaginationError(
        "historical Tick storage time is missing or not a datetime"
      )
    try:
      from quantx_infrastructure.core.data.tick_identity import (
        tick_source_time_ms,
        tick_storage_time,
      )

      source_time_ms = tick_source_time_ms(tick)
      actual_time = time_utils.to_utc(raw_time)
      base_time = time_utils.to_utc(tick_storage_time(source_time_ms, 0))
      tick_ordinal = int((actual_time - base_time).total_seconds() * 1_000_000)
    except (TypeError, ValueError, OverflowError, OSError) as exc:
      raise HistoricalTickPaginationError(
        "historical legacy Tick storage identity cannot be derived"
      ) from exc
    if not 0 <= tick_ordinal < HISTORICAL_TICK_ORDINALS_PER_MILLISECOND:
      raise HistoricalTickPaginationError(
        "historical legacy Tick storage identity is out of range"
      )
    tick.source_time_ms = source_time_ms
    tick.tick_ordinal = tick_ordinal
    return source_time_ms, tick_ordinal
  if ordinal_missing:
    raise HistoricalTickPaginationError(
      "historical Tick source identity is partially missing"
    )
  if isinstance(source_value, bool) or isinstance(ordinal_value, bool):
    raise HistoricalTickPaginationError(
      "historical Tick source identity contains a boolean value"
    )
  try:
    source_time_ms = int(source_value)
    tick_ordinal = int(ordinal_value)
  except (TypeError, ValueError, OverflowError) as exc:
    raise HistoricalTickPaginationError(
      "historical Tick source identity is not integral"
    ) from exc
  if (
    source_time_ms <= 0
    or tick_ordinal < 0
    or tick_ordinal >= HISTORICAL_TICK_ORDINALS_PER_MILLISECOND
  ):
    raise HistoricalTickPaginationError(
      "historical Tick source identity is missing or out of range"
    )
  if source_value != source_time_ms or ordinal_value != tick_ordinal:
    raise HistoricalTickPaginationError(
      "historical Tick source identity is not losslessly represented"
    )
  return source_time_ms, tick_ordinal


def _storage_time_utc(source_time_ms: int, tick_ordinal: int) -> datetime:
  try:
    from quantx_infrastructure.core.data.tick_identity import tick_storage_time

    return time_utils.to_utc(tick_storage_time(source_time_ms, tick_ordinal))
  except (OverflowError, ValueError) as exc:
    raise HistoricalTickPaginationError(
      "historical Tick source identity cannot produce a storage timestamp"
    ) from exc


def _validate_storage_time(
  tick: Tick,
  source_key: tuple[int, int],
) -> None:
  raw_time = getattr(tick, "time", None)
  if hasattr(raw_time, "to_pydatetime"):
    raw_time = raw_time.to_pydatetime()
  if not isinstance(raw_time, datetime):
    raise HistoricalTickPaginationError(
      "historical Tick storage time is missing or not a datetime"
    )
  try:
    actual_time = time_utils.to_utc(raw_time)
  except (TypeError, ValueError, OverflowError, OSError) as exc:
    raise HistoricalTickPaginationError(
      "historical Tick storage time cannot be normalized"
    ) from exc
  expected_time = _storage_time_utc(*source_key)
  if actual_time != expected_time:
    raise HistoricalTickPaginationError(
      "historical Tick storage time does not match source identity"
    )


class HistoricalMarketDataService(HistoricalPriceTransforms):
  """历史市场数据服务类"""

  MAX_TICK_PAGE_SIZE = 10_000
  DEFAULT_TICK_MAX_PAGES = 1_024
  DEFAULT_TICK_MAX_SOURCE_ROWS = 2_000_000

  _BASE_PERIODS = {"1m", "1d"}
  _INTRADAY_AGG_MAP = {
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "60m": "60min",
    "1h": "60min",
  }
  _DAILY_AGG_MAP = {
    "1w": "W",
    "1mon": "M",
    "1q": "Q",
    "1hy": "2Q",
    "1y": "A",
  }

  def __init__(self):
    self.logger = logging.getLogger(__name__)
    self.stock_service = InstrumentService()
    self.kline_repo = KLineRepository()
    self.tick_repo = TickRepository()
    self.divid_factor_service_async = DividFactorService()

  async def get_kline_data(
    self,
    stock_code: str,
    period: str = "1m",
    start_time: datetime = None,
    end_time: datetime = None,
    regenerate: bool = False,
    limit: int = None,
    dividend_type: str = "none",
    order: str = "asc",
  ) -> List[KLine]:
    """获取K线数据（异步）"""
    from datetime import timedelta

    from quantx_infrastructure.core.utils import time_utils

    if start_time is None and end_time is None:
      now = time_utils.now()
      if period == "1m":
        start_time = now - timedelta(days=3)
      elif period in ("5m", "15m"):
        start_time = now - timedelta(days=7)
      elif period in ("30m", "60m", "1h"):
        start_time = now - timedelta(days=30)
      elif period == "1d":
        start_time = now - timedelta(days=30)  # 从 180 天改为 30 天
      elif period in ("1w", "1mon", "1q", "1hy", "1y"):
        start_time = now - timedelta(days=365)
      else:
        start_time = now - timedelta(days=30)

    order = (order or "asc").lower()
    order_by = "time DESC" if order == "desc" else "time ASC"

    if period in self._BASE_PERIODS:
      from quantx_infrastructure.config.settings import settings

      klines = await asyncio.to_thread(
        self.kline_repo.find_all,
        measurement=f"kline_{period}",
        filters={"stock_code": stock_code},
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        order_by=order_by,
        as_frame=False,
        use_chunking=period != "1d" or settings.environment == "development",
      )
      return await self._apply_dividend_adjustment_async(
        klines, stock_code, dividend_type
      )

    if period in self._INTRADAY_AGG_MAP:
      base_klines = await asyncio.to_thread(
        self.kline_repo.find_all,
        measurement="kline_1m",
        filters={"stock_code": stock_code},
        start_time=start_time,
        end_time=end_time,
        order_by=order_by,
        as_frame=False,
        chunk_hours=24 * 7,
      )
      aggregated = self._resample_klines(
        base_klines,
        stock_code=stock_code,
        period=period,
        freq=self._INTRADAY_AGG_MAP[period],
      )
      aggregated = await self._apply_dividend_adjustment_async(
        aggregated, stock_code, dividend_type
      )
      if order == "desc":
        aggregated = list(reversed(aggregated))
      if limit:
        aggregated = aggregated[:limit]
      return aggregated

    if period in self._DAILY_AGG_MAP:
      base_klines = await asyncio.to_thread(
        self.kline_repo.find_all,
        measurement="kline_1d",
        filters={"stock_code": stock_code},
        start_time=start_time,
        end_time=end_time,
        order_by=order_by,
        as_frame=False,
        use_chunking=False,
      )
      aggregated = self._resample_klines(
        base_klines,
        stock_code=stock_code,
        period=period,
        freq=self._DAILY_AGG_MAP[period],
      )
      aggregated = await self._apply_dividend_adjustment_async(
        aggregated, stock_code, dividend_type
      )
      if order == "desc":
        aggregated = list(reversed(aggregated))
      if limit:
        aggregated = aggregated[:limit]
      return aggregated

    self.logger.warning(f"不支持的K线周期: {period}")
    return []

  async def get_tick_data(
    self,
    stock_code: str,
    start_time: datetime = None,
    end_time: datetime = None,
    dividend_type: str = "none",
    as_frame: bool = False,
    limit: int = None,
    order: str = "asc",
    offset: int = 0,
  ) -> Union[List[Tick], pd.DataFrame]:
    """获取Tick数据（异步）"""
    from datetime import timedelta

    from quantx_infrastructure.core.data.tick_identity import tick_query_end_time
    from quantx_infrastructure.core.utils import time_utils

    # 如果未指定时间范围，默认查最近1天（避免全表扫描）
    if start_time is None and end_time is None:
      now = time_utils.now()
      start_time = now - timedelta(days=1)

    order = (order or "asc").lower()
    order_by = "time DESC" if order == "desc" else "time ASC"
    ticks = await asyncio.to_thread(
      self.tick_repo.find_all,
      filters={"stock_code": stock_code},
      start_time=start_time,
      end_time=tick_query_end_time(end_time),
      order_by=order_by,
      as_frame=as_frame,
      limit=limit,
      offset=max(0, int(offset or 0)),
    )
    if dividend_type and dividend_type != "none":
      adjusted = await self._apply_tick_dividend_adjustment_async(
        ticks, stock_code, dividend_type
      )
      if adjusted is not None:
        return adjusted
    return ticks

  async def iter_tick_pages(
    self,
    *,
    stock_code: str,
    start_time: datetime,
    end_time: datetime,
    page_size: int = MAX_TICK_PAGE_SIZE,
    max_pages: int = DEFAULT_TICK_MAX_PAGES,
    max_source_ticks: int = DEFAULT_TICK_MAX_SOURCE_ROWS,
  ) -> AsyncIterator[List[Tick]]:
    """Stream historical Tick pages using a strict source-identity cursor.

    This path is intentionally separate from ``get_tick_data``.  The latter
    remains an offset-oriented compatibility API, while profile materializing
    must prove that every source row was visited.  A terminal empty query is
    always performed after the last non-empty page, including a short page;
    this prevents a backend-side page cap from being mistaken for end of
    history.
    """

    requested_page_size = int(page_size)
    if requested_page_size <= 0 or requested_page_size > self.MAX_TICK_PAGE_SIZE:
      raise ValueError("historical Tick page size must be between 1 and 10000")
    page_limit = int(max_pages)
    if page_limit <= 0:
      raise ValueError("historical Tick max_pages must be positive")
    source_limit = int(max_source_ticks)
    if source_limit <= 0:
      raise ValueError("historical Tick max_source_ticks must be positive")

    cursor: Optional[tuple[int, int]] = None
    previous_key: Optional[tuple[int, int]] = None
    page_count = 0
    source_count = 0
    while True:
      # The final empty probe is not a data page.  It is required to prove
      # completeness when the last data page happens to be full.
      if page_count >= page_limit:
        page = await self._read_source_identity_page(
          stock_code=stock_code,
          start_time=start_time,
          end_time=end_time,
          after=cursor,
          limit=requested_page_size,
        )
        if page:
          raise HistoricalTickPaginationError(
            "historical Tick page limit reached before the source was exhausted"
          )
        return

      page = await self._read_source_identity_page(
        stock_code=stock_code,
        start_time=start_time,
        end_time=end_time,
        after=cursor,
        limit=requested_page_size,
      )
      if not page:
        return
      if len(page) > requested_page_size:
        raise HistoricalTickPaginationError(
          "historical Tick source returned more rows than the requested page"
        )

      page_previous = previous_key
      for tick in page:
        key = _strict_source_identity(tick)
        _validate_storage_time(tick, key)
        if page_previous is not None and key <= page_previous:
          raise HistoricalTickPaginationError(
            "historical Tick page is duplicated, unordered, or non-progressing"
          )
        page_previous = key
      if page_previous is None:
        raise HistoricalTickPaginationError("historical Tick page has no source rows")

      source_count += len(page)
      if source_count > source_limit:
        raise HistoricalTickPaginationError(
          "historical Tick source row limit reached before completeness was proven"
        )
      page_count += 1
      previous_key = page_previous
      cursor = page_previous
      yield page

  async def _read_source_identity_page(
    self,
    *,
    stock_code: str,
    start_time: datetime,
    end_time: datetime,
    after: Optional[tuple[int, int]],
    limit: int,
  ) -> List[Tick]:
    reader = getattr(self.tick_repo, "find_source_identity_page", None)
    if not callable(reader):
      raise HistoricalTickPaginationError(
        "Tick repository does not provide strict source-identity pagination"
      )
    try:
      return await asyncio.to_thread(
        reader,
        stock_code=stock_code,
        start_time=start_time,
        end_time=end_time,
        after=after,
        limit=limit,
      )
    except HistoricalTickPaginationError:
      raise
    except ValueError as exc:
      raise HistoricalTickPaginationError(
        "historical Tick repository query could not prove page integrity"
      ) from exc

  def clear_klines(self, period: str, stock_code: str = None) -> int:
    """清除K线数据"""
    return self.kline_repo.delete(
      measurement=f"kline_{period}",
      filters={"stock_code": stock_code} if stock_code else None,
    )

  def save_kline(self, kline: KLine) -> KLine:
    """保存单条K线数据"""
    return self.kline_repo.save(kline)

  def bulk_save_klines(
    self, period: str, klines: pd.DataFrame, *, batch_size: int = 5000
  ) -> int:
    """保存K线数据；调用方可传入已验证且受字节预算约束的写入批量。"""
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
      raise ValueError("K-line write batch_size must be a positive integer")
    return self.kline_repo.bulk_save(
      measurement=f"kline_{period}", records=klines, batch_size=batch_size
    )

  def save_tick(self, tick: Tick) -> Tick:
    """保存单条Tick数据"""
    return self.tick_repo.save(tick)

  def bulk_save_ticks(self, ticks: pd.DataFrame) -> int:
    """保存Tick数据"""
    return self.tick_repo.bulk_save(measurement="ticks", records=ticks, batch_size=5000)

  async def get_latest_ticks(
    self,
    stock_list: List[str],
  ) -> Dict[str, Tick]:
    """
    获取股票列表的最新tick数据（从历史数据中获取最新的）
    这个方法供 MarketDataService 在数据降级时调用

    Args:
        stock_list: 股票代码列表，如 ['000001.SZ', '600000.SH']
        force_refresh: 是否强制刷新缓存，默认False（暂时忽略）
        timeout: 请求超时时间（秒），可选（暂时忽略）

    Returns:
        Dict[str, Tick]: 股票代码到Tick对象的映射

    Raises:
        ValueError: 当stock_list为空或包含无效代码时
    """
    # 参数验证
    if not stock_list:
      raise ValueError("股票代码列表不能为空")

    return self.tick_repo.get_full_tick(stock_list)
