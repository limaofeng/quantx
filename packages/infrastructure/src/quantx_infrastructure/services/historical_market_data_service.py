"""
历史市场数据服务
处理K线数据和历史价格数据的业务逻辑
"""

import asyncio
import logging
import math
import re
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
  if (
    _identity_value_is_missing(source_value)
    or _identity_value_is_missing(ordinal_value)
  ):
    raise HistoricalTickPaginationError(
      "historical Tick source identity is missing"
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

    return time_utils.to_utc(
      tick_storage_time(source_time_ms, tick_ordinal)
    )
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


class HistoricalMarketDataService:
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

  def _normalize_time(self, value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
      return None
    if isinstance(value, pd.Timestamp):
      value = value.to_pydatetime()
    if not isinstance(value, datetime):
      return None
    if value.tzinfo is None:
      return value
    from quantx_infrastructure.core.utils import time_utils

    return time_utils.to_shanghai(value)

  def _resample_klines(
    self,
    klines: List[KLine],
    stock_code: str,
    period: str,
    freq: str,
  ) -> List[KLine]:
    if not klines:
      return []

    records = []
    for kline in klines:
      records.append(
        {
          "time": kline.time,
          "open": kline.open,
          "high": kline.high,
          "low": kline.low,
          "close": kline.close,
          "volume": kline.volume,
          "amount": getattr(kline, "amount", 0.0),
          "pre_close": getattr(kline, "pre_close", 0.0),
        }
      )

    frame = pd.DataFrame(records)
    if frame.empty:
      return []

    frame["time"] = pd.to_datetime(frame["time"])
    frame = frame.sort_values("time").set_index("time")

    agg_dict = {
      "open": "first",
      "high": "max",
      "low": "min",
      "close": "last",
      "volume": "sum",
      "amount": "sum",
    }

    resampled = frame.resample(freq).agg(agg_dict).dropna()
    if resampled.empty:
      return []

    resampled["pre_close"] = resampled["close"].shift(1)
    first_pre_close = frame["pre_close"].iloc[0] if not frame.empty else 0.0
    resampled.iloc[0, resampled.columns.get_loc("pre_close")] = first_pre_close

    resampled = resampled.reset_index()
    results: List[KLine] = []
    for _, row in resampled.iterrows():
      results.append(
        KLine(
          stock_code=stock_code,
          period=period,
          time=row["time"],
          open=float(row["open"]),
          high=float(row["high"]),
          low=float(row["low"]),
          close=float(row["close"]),
          pre_close=float(row["pre_close"]) if pd.notna(row["pre_close"]) else 0.0,
          volume=float(row["volume"]),
          amount=float(row["amount"]) if pd.notna(row["amount"]) else 0.0,
          settelement_price=0.0,
          open_interest=0,
          suspend_flag=0,
        )
      )

    return results

  async def _apply_dividend_adjustment_async(
    self, klines: List[KLine], stock_code: str, dividend_type: str
  ) -> List[KLine]:
    if not klines:
      return []

    if dividend_type not in ["front", "back", "front_ratio", "back_ratio"]:
      return klines

    dividend_type = "front" if dividend_type == "front_ratio" else dividend_type
    dividend_type = "back" if dividend_type == "back_ratio" else dividend_type

    times = [
      self._normalize_time(kline.time)
      for kline in klines
      if kline.time is not None
    ]
    if not times:
      return klines

    start_time = min(times)
    end_time = max(times)

    factors = await self.divid_factor_service_async.get_divid_factors(
      stock_code=stock_code,
      start_time=start_time,
      end_time=end_time,
      limit=None,
    )

    if not factors:
      self.logger.debug(
        "未找到复权因子，返回原始K线: %s, %s~%s",
        stock_code,
        start_time,
        end_time,
      )
      return klines

    factor_rows = [
      {"time": factor.time, "dr": factor.dr}
      for factor in factors
      if factor.time is not None and factor.dr
    ]
    if not factor_rows:
      self.logger.debug(
        "复权因子无有效数据，返回原始K线: %s, %s~%s",
        stock_code,
        start_time,
        end_time,
      )
      return klines

    factor_df = pd.DataFrame(factor_rows).sort_values("time")
    factor_df = factor_df[
      pd.to_datetime(factor_df["time"]) <= pd.Timestamp(end_time)
    ]
    factor_df["dr"] = pd.to_numeric(factor_df["dr"], errors="coerce").fillna(1.0)
    factor_df = factor_df[factor_df["dr"] > 0]
    if factor_df.empty:
      return klines

    factor_df["cum_factor"] = factor_df["dr"].cumprod()
    total_factor = float(factor_df["cum_factor"].iloc[-1])

    kline_df = pd.DataFrame(
      {"time": [self._normalize_time(k.time) for k in klines]}
    )
    kline_df = kline_df.sort_values("time")

    aligned = pd.merge_asof(
      kline_df,
      factor_df[["time", "cum_factor"]],
      on="time",
      direction="backward",
    )

    aligned["cum_factor"] = aligned["cum_factor"].fillna(1.0)
    if dividend_type == "front":
      # QMT dr = pre-action raw close / ex-right reference price.
      # Front adjustment restates history on the latest price basis: only
      # corporate actions strictly after a bar may scale that bar.
      aligned["adjust_factor"] = aligned["cum_factor"] / total_factor
    else:
      # Back adjustment keeps the earliest price basis and applies only
      # factors already effective at the bar timestamp.
      aligned["adjust_factor"] = aligned["cum_factor"]

    adjust_factors = dict(zip(aligned["time"], aligned["adjust_factor"]))

    adjusted = []
    for kline in klines:
      factor = adjust_factors.get(self._normalize_time(kline.time), 1.0)
      adjusted.append(
        KLine(
          stock_code=kline.stock_code,
          period=kline.period,
          time=kline.time,
          open=kline.open * factor,
          high=kline.high * factor,
          low=kline.low * factor,
          close=kline.close * factor,
          pre_close=kline.pre_close * factor,
          volume=kline.volume,
          amount=kline.amount,
          settelement_price=kline.settelement_price,
          open_interest=kline.open_interest,
          suspend_flag=kline.suspend_flag,
        )
      )

    return adjusted

  async def _build_adjust_factors_async(
    self, times: List[datetime], stock_code: str, dividend_type: str
  ) -> Optional[pd.Series]:
    if not times:
      return None

    normalized_times = [self._normalize_time(t) for t in times]
    time_df = pd.DataFrame({"time": list(normalized_times)}).reset_index()
    valid_mask = time_df["time"].notna()
    if not valid_mask.any():
      return None

    valid_times = time_df.loc[valid_mask, "time"].tolist()
    start_time = min(valid_times)
    end_time = max(valid_times)

    factors = await self.divid_factor_service_async.get_divid_factors(
      stock_code=stock_code,
      start_time=start_time,
      end_time=end_time,
      limit=None,
    )

    if not factors:
      self.logger.debug("未找到复权因子，跳过Tick复权: %s", stock_code)
      return None

    factor_rows = [
      {"time": factor.time, "dr": factor.dr}
      for factor in factors
      if factor.time is not None and factor.dr
    ]
    if not factor_rows:
      self.logger.debug("复权因子无有效数据，跳过Tick复权: %s", stock_code)
      return None

    factor_df = pd.DataFrame(factor_rows).sort_values("time")
    factor_df = factor_df[
      pd.to_datetime(factor_df["time"]) <= pd.Timestamp(end_time)
    ]
    factor_df["dr"] = pd.to_numeric(factor_df["dr"], errors="coerce").fillna(1.0)
    factor_df = factor_df[factor_df["dr"] > 0]
    if factor_df.empty:
      return None

    factor_df["cum_factor"] = factor_df["dr"].cumprod()
    total_factor = float(factor_df["cum_factor"].iloc[-1])

    valid_df = time_df.loc[valid_mask].sort_values("time")

    aligned = pd.merge_asof(
      valid_df,
      factor_df[["time", "cum_factor"]],
      on="time",
      direction="backward",
    )

    aligned["cum_factor"] = aligned["cum_factor"].fillna(1.0)
    if dividend_type == "front":
      aligned["adjust_factor"] = aligned["cum_factor"] / total_factor
    else:
      aligned["adjust_factor"] = aligned["cum_factor"]

    result = pd.Series([None] * len(time_df), index=time_df["index"])
    result.loc[aligned["index"]] = aligned["adjust_factor"].values
    return result.sort_index()

  async def _apply_tick_dividend_adjustment_async(
    self,
    ticks: Union[List[Tick], pd.DataFrame],
    stock_code: str,
    dividend_type: str,
  ) -> Union[List[Tick], pd.DataFrame]:
    if not ticks:
      return ticks

    if dividend_type not in ["front", "back", "front_ratio", "back_ratio"]:
      return ticks

    dividend_type = "front" if dividend_type == "front_ratio" else dividend_type
    dividend_type = "back" if dividend_type == "back_ratio" else dividend_type

    price_fields = [
      "last_price",
      "open",
      "high",
      "low",
      "last_close",
      "last_settlement_price",
      "settlement_price",
    ]

    if isinstance(ticks, pd.DataFrame):
      if ticks.empty or "time" not in ticks.columns:
        return ticks

      factors = await self._build_adjust_factors_async(
        list(ticks["time"]), stock_code, dividend_type
      )
      if factors is None:
        self.logger.debug("未找到复权因子，返回原始Tick: %s", stock_code)
        return ticks

      adjusted = ticks.copy()
      adjusted["_adjust_factor"] = pd.Series(factors).fillna(1.0).values

      for col in price_fields:
        if col in adjusted.columns:
          adjusted[col] = adjusted[col] * adjusted["_adjust_factor"]

      for col in adjusted.columns:
        if re.match(r"^(ask|bid)\\d+$", col):
          adjusted[col] = adjusted[col] * adjusted["_adjust_factor"]

      if "ask_price" in adjusted.columns:
        adjusted["ask_price"] = adjusted.apply(
          lambda r: [p * r["_adjust_factor"] for p in (r["ask_price"] or [])], axis=1
        )
      if "bid_price" in adjusted.columns:
        adjusted["bid_price"] = adjusted.apply(
          lambda r: [p * r["_adjust_factor"] for p in (r["bid_price"] or [])], axis=1
        )

      adjusted.drop(columns=["_adjust_factor"], inplace=True)
      return adjusted

    times = [t.time for t in ticks]
    factors = await self._build_adjust_factors_async(times, stock_code, dividend_type)
    if factors is None:
      self.logger.debug("未找到复权因子，返回原始Tick: %s", stock_code)
      return ticks
    factors = pd.Series(factors).fillna(1.0).values

    for tick, factor in zip(ticks, factors):
      for field in price_fields:
        value = getattr(tick, field, None)
        if value is not None:
          setattr(tick, field, value * factor)

      for list_field in ["ask_price", "bid_price"]:
        values = getattr(tick, list_field, None)
        if values:
          setattr(tick, list_field, [v * factor for v in values])

    return ticks

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
      klines = await asyncio.to_thread(
        self.kline_repo.find_all,
        measurement=f"kline_{period}",
        filters={"stock_code": stock_code},
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        order_by=order_by,
        as_frame=False,
        use_chunking=period != "1d",
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

  def bulk_save_klines(self, period: str, klines: pd.DataFrame) -> int:
    """保存K线数据"""
    return self.kline_repo.bulk_save(
      measurement=f"kline_{period}", records=klines, batch_size=5000
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
