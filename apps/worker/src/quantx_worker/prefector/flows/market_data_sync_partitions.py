"""Bound historical transfers and reject unexplained source coverage gaps."""

from datetime import date, datetime, timedelta
from typing import Any

MAX_BAR_DATE_SPAN_DAYS = {"tick": 7, "1m": 31, "1d": 3_700}
InstrumentLifetimes = dict[str, tuple[date | None, date | None]]


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


def plan_tick_partitions(codes, days, start, end, periods):
  first = datetime.strptime(start, "%Y%m%d").date()
  last = datetime.strptime(end, "%Y%m%d").date()
  if not days or days != sorted(set(days)) or min(days) < first or max(days) > last:
    raise ValueError("Tick 同步交易日历为空或超出请求区间")
  # One code/day limits payload size and identifies provider history gaps exactly.
  # Other selected periods use the same scope, avoiding duplicate overlapping reads.
  if "tick" not in periods or not codes or len(codes) != len(set(codes)):
    raise ValueError("Tick 同步标的或周期无效")
  return [
    ([code], day.strftime("%Y%m%d"), day.strftime("%Y%m%d"))
    for day in days
    for code in sorted(codes)
  ]


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
