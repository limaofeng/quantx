"""Trading window used by general Worker preparation tasks."""

import os
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from quantx_infrastructure.services.trading_time_service import TradingDateHelper

SHANGHAI = ZoneInfo("Asia/Shanghai")
CRITICAL_WINDOW_START = time(9, 15)
CRITICAL_WINDOW_END = time(16, 30)


def _aware_shanghai(value: datetime | None) -> datetime:
  current = value or datetime.now(timezone.utc)
  if current.tzinfo is None:
    current = current.replace(tzinfo=timezone.utc)
  return current.astimezone(SHANGHAI)


async def _maybe_await(value: Any) -> Any:
  return await value if hasattr(value, "__await__") else value


def _full_live_runtime() -> bool:
  profile = os.environ.get("RUNTIME_PROFILE", "")
  mode = os.environ.get("QMT_AGENT_MODE", "")
  return profile.strip().lower() == "full" and mode.strip().lower() == "live"


async def is_critical_trading_window(
  now: datetime | None = None,
  *,
  trading_dates: Any | None = None,
) -> bool:
  """Return true only for an actual Shanghai trading day in 09:15–16:30."""

  local = _aware_shanghai(now)
  if not (CRITICAL_WINDOW_START <= local.time() <= CRITICAL_WINDOW_END):
    return False
  helper = trading_dates or TradingDateHelper()
  return bool(await _maybe_await(helper.is_trading_date("SH", local.date())))
