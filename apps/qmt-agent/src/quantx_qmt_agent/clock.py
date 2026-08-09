"""Timezone-safe clock utilities owned by the QMT agent."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def now() -> datetime:
  return datetime.now(SHANGHAI_TZ)


def now_aware() -> datetime:
  return now()


def to_shanghai(value: datetime) -> datetime:
  if value.tzinfo is None:
    return value.replace(tzinfo=SHANGHAI_TZ)
  return value.astimezone(SHANGHAI_TZ)
