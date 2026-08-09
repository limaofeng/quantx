"""Exchange-local clock helpers kept injectable at application boundaries."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def now() -> datetime:
  return datetime.now(SHANGHAI).replace(tzinfo=None)


def today() -> date:
  return now().date()


def utcnow() -> datetime:
  """Return naive UTC for compatibility with persisted TIMESTAMP columns."""
  return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
  """Normalize a datetime to the naive-UTC persistence convention."""
  if value.tzinfo is None:
    return value
  return value.astimezone(timezone.utc).replace(tzinfo=None)
