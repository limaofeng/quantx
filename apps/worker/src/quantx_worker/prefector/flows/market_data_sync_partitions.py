"""Bound historical transfers and reject unexplained source coverage gaps."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import zip_longest
from typing import Any

MAX_BAR_DATE_SPAN_DAYS = {"tick": 7, "1m": 31, "1d": 3_700}
InstrumentLifetimes = dict[str, tuple[date | None, date | None]]
ESTIMATED_RECORDS_PER_DAY = {"tick": 20_000, "1m": 300, "1d": 1}
ESTIMATED_RECORD_BYTES = {"tick": 1024, "1m": 512, "1d": 512}
REQUEST_BYTE_BUDGET = 256 * 1024 * 1024


@dataclass(frozen=True)
class MarketPartition:
  codes: list[str]
  periods: list[str]
  start: str
  end: str
  days: list[date]


def iter_market_partitions(
  codes: list[str],
  days: list[date],
  start: str,
  end: str,
  periods: list[str],
  lifetimes: InstrumentLifetimes,
  *,
  max_delivery_partitions: int | None = None,
) -> Iterator[MarketPartition]:
  """Round-robin periods, retaining only one bounded partition per period."""

  def partitions(period: str):
    windows = (
      ((day.strftime("%Y%m%d"), day.strftime("%Y%m%d")) for day in days)
      if period == "tick"
      else market_date_windows(start, end, [period])
    )
    for first, last in windows:
      first_day = datetime.strptime(first, "%Y%m%d").date()
      last_day = datetime.strptime(last, "%Y%m%d").date()
      window_days = [day for day in days if first_day <= day <= last_day]
      span = (last_day - first_day).days + 1
      per_code = span * ESTIMATED_RECORDS_PER_DAY[period] + 1
      size = min(
        10 if period == "tick" else 300,
        500_000 // per_code,
        REQUEST_BYTE_BUDGET // (per_code * ESTIMATED_RECORD_BYTES[period]),
      )
      if max_delivery_partitions is not None:
        size = min(size, max_delivery_partitions // span)
      if size < 1:
        raise ValueError("单标的行情分区超出记录或字节预算")
      batch = []
      for code in codes:
        if not any(instrument_active_on(code, day, lifetimes) for day in window_days):
          continue
        batch.append(code)
        if len(batch) == size:
          yield MarketPartition(batch, [period], first, last, window_days)
          batch = []
      if batch:
        yield MarketPartition(batch, [period], first, last, window_days)

  for group in zip_longest(*(partitions(period) for period in periods)):
    for partition in group:
      if partition is not None:
        yield partition


def market_date_windows(start: str, end: str, periods: list[str]):
  first = datetime.strptime(start, "%Y%m%d").date()
  last = datetime.strptime(end, "%Y%m%d").date()
  if last < first or not periods:
    raise ValueError("行情同步日期范围或周期无效")
  limit = min(MAX_BAR_DATE_SPAN_DAYS[period] for period in periods)
  while first <= last:
    window_end = min(last, first + timedelta(days=limit - 1))
    yield first.strftime("%Y%m%d"), window_end.strftime("%Y%m%d")
    first = window_end + timedelta(days=1)


def instrument_active_on(code: str, day: date, lifetimes: InstrumentLifetimes) -> bool:
  opened, expired = lifetimes.get(code, (None, None))
  return (opened is None or opened <= day) and (expired is None or day <= expired)


def validate_market_partition(
  transfer: dict[str, Any],
  codes: list[str],
  periods: list[str],
  start: str,
  end: str,
  *,
  trading_days: list[date],
  lifetimes: InstrumentLifetimes,
) -> None:
  """Check every requested pair and active trading day, not just total rows.

  A provider's no-rows response is not suspension evidence. Unknown empty days
  remain gaps; listing boundaries are the only exclusions made here. Positive
  day coverage proves presence, not completeness of every minute or Tick.
  """
  summaries = transfer.get("code_summaries") or []
  expected = {(code, period) for code in codes for period in periods}
  actual = {(item["code"], item["period"]) for item in summaries}
  if actual != expected or len(summaries) != len(expected):
    raise RuntimeError("行情同步缺少完整的标的/周期入库摘要")
  empty = [
    f"{item['code']}/{item['period']}" for item in summaries if item["row_count"] <= 0
  ]
  if empty:
    raise RuntimeError(
      f"行情源未返回数据: {start}..{end} {', '.join(empty)}; "
      f"request_id={transfer.get('request_id')}。请检查历史可用范围或停牌情况。"
    )
  if sum(item["row_count"] for item in summaries) != transfer["records_received"]:
    raise RuntimeError("行情同步摘要行数与接收记录数不一致")

  coverage = {
    (item["instrument_code"], item["period"], item["trading_date"])
    for item in transfer.get("day_coverage") or []
    if item["point_count"] > 0
  }
  gap_count = 0
  examples: list[str] = []
  for code in codes:
    for day in trading_days:
      if not instrument_active_on(code, day, lifetimes):
        continue
      for period in periods:
        if (code, period, day.isoformat()) not in coverage:
          gap_count += 1
          if len(examples) < 10:
            examples.append(f"{code}/{period}/{day.isoformat()}")
  if gap_count:
    raise RuntimeError(
      f"行情同步交易日覆盖不完整: gaps={gap_count} "
      f"examples={', '.join(examples)} request_id={transfer.get('request_id')}"
    )
