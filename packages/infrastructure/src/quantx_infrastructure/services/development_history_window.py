"""One configurable admission policy for Worker and API history dispatch."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from quantx_contracts.history_download_settings import (
  HistoryDownloadMode,
  HistoryDownloadPolicy,
)

from quantx_infrastructure.database.connection import AsyncSessionLocal
from quantx_infrastructure.repositories.history_download_settings_repository import (
  HistoryDownloadSettingsRepository,
)
from quantx_infrastructure.services.holiday_service import HolidayService

logger = logging.getLogger(__name__)


async def policy_window_open(
  policy: HistoryDownloadPolicy, now: datetime | None = None
) -> bool:
  current = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(
    ZoneInfo("Asia/Shanghai")
  )
  if policy.mode == HistoryDownloadMode.ALWAYS:
    return True
  if any(window.contains(current.strftime("%H:%M")) for window in policy.windows):
    return True
  if not policy.non_trading_days_allowed:
    return False
  if current.weekday() >= 5:
    return True
  holidays = await HolidayService().get_holidays("SH", current.year)
  return current.date() in {item.date for item in holidays}


async def history_window_open(now: datetime | None = None) -> bool:
  try:
    async with AsyncSessionLocal() as db:
      config = await HistoryDownloadSettingsRepository(db).get()
    return await policy_window_open(config.policy, now)
  except Exception as exc:
    # Configuration/calendar failures must never silently broaden admission.
    logger.warning("History download policy unavailable: %s", type(exc).__name__)
    return False
