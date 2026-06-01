"""Time helpers with a consistent Asia/Shanghai default."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from config.settings import settings

_TZ = ZoneInfo(getattr(settings, "timezone", None) or "Asia/Shanghai")


def now() -> datetime:
  """Return current time in Asia/Shanghai as a naive datetime."""
  return datetime.now(_TZ).replace(tzinfo=None)


def today() -> date:
  """Return current date in Asia/Shanghai."""
  return now().date()


def now_aware() -> datetime:
  """Return current time in Asia/Shanghai as timezone-aware."""
  return datetime.now(_TZ)


def to_shanghai(dt: datetime, *, keep_tz: bool = False) -> datetime:
  """Convert a datetime to Asia/Shanghai (naive by default)."""
  if dt.tzinfo is None:
    localized = dt.replace(tzinfo=_TZ)
  else:
    localized = dt.astimezone(_TZ)
  return localized if keep_tz else localized.replace(tzinfo=None)


def to_utc(dt: datetime) -> datetime:
  """Convert a datetime to UTC (timezone-aware)."""
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=_TZ)
  return dt.astimezone(timezone.utc)
