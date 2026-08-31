"""Availability of completed A-share bars, independent of storage labels."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_INTRADAY_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "1h": 60}


def exchange_local_time(timestamp: datetime) -> datetime:
  if not isinstance(timestamp, datetime):
    raise ValueError("BAR_TIMESTAMP_REQUIRED:行情缺少有效时间")
  if timestamp.tzinfo is not None:
    return timestamp.astimezone(_SHANGHAI).replace(tzinfo=None)
  return timestamp


@dataclass(frozen=True)
class BarTiming:
  period: str
  label_time: datetime
  available_at: datetime


def resolve_bar_timing(bar: Any, *, alignment: str = "end") -> BarTiming:
  """A daily date label is usable at 15:00; intraday labels name start/end.

  ``available_at`` is the exchange-local decision time. The original bar and
  its storage label remain unchanged. Unknown periods/alignment fail closed.
  """
  period = str(getattr(bar, "period", "") or "").lower()
  label = exchange_local_time(getattr(bar, "time", None))
  if alignment not in {"start", "end"}:
    raise ValueError("BAR_TIME_ALIGNMENT_INVALID:K线时间对齐必须为 start 或 end")
  if period == "1d":
    available = max(label, datetime.combine(label.date(), time(15)))
  elif period in _INTRADAY_MINUTES:
    available = label + (
      timedelta(minutes=_INTRADAY_MINUTES[period])
      if alignment == "start"
      else timedelta(0)
    )
  else:
    raise ValueError(f"BAR_PERIOD_UNSUPPORTED:未定义周期 {period!r} 的完整K线可用时间")
  return BarTiming(period=period, label_time=label, available_at=available)


def bar_query_start(window_start: datetime, period: str, *, alignment: str) -> datetime:
  """Include labels before a window whose completed bars belong inside it."""
  start = exchange_local_time(window_start)
  if alignment not in {"start", "end"}:
    raise ValueError("BAR_TIME_ALIGNMENT_INVALID:K线时间对齐必须为 start 或 end")
  if period not in {"1d", *_INTRADAY_MINUTES}:
    raise ValueError(f"BAR_PERIOD_UNSUPPORTED:未定义周期 {period!r} 的完整K线可用时间")
  if period == "1d":
    return datetime.combine(start.date(), time())
  if alignment == "start" and period in _INTRADAY_MINUTES:
    return start - timedelta(minutes=_INTRADAY_MINUTES[period])
  return start
