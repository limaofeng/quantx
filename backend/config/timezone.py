"""Process-wide timezone bootstrap.

This module intentionally has no dependency on Settings to avoid import cycles.
Call it as early as possible after dotenv files are loaded.
"""

import os
import time
from typing import Optional
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Shanghai"


def configure_process_timezone(timezone_name: Optional[str] = None) -> str:
  """Configure the process-local timezone and return the active timezone name."""
  active_timezone = (
    timezone_name
    or os.getenv("TIMEZONE")
    or os.getenv("TZ")
    or os.getenv("TRADING_TIMEZONE")
    or DEFAULT_TIMEZONE
  )

  # Validate early so a typo does not silently leave the process in server-local time.
  ZoneInfo(active_timezone)

  os.environ["TZ"] = active_timezone
  os.environ["TIMEZONE"] = active_timezone
  os.environ["TRADING_TIMEZONE"] = active_timezone

  if hasattr(time, "tzset"):
    time.tzset()

  return active_timezone
