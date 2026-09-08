"""Conservative admission for development-triggered native history work."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from quantx_infrastructure.services.holiday_service import HolidayService


async def history_window_open(now: datetime | None = None) -> bool:
  current = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(
    ZoneInfo("Asia/Shanghai")
  )
  try:
    # An empty calendar is unknown, not proof that every weekday is open.
    holidays = await HolidayService().get_holidays("SH", current.year)
    if not holidays:
      return False
    closed = current.weekday() >= 5 or current.date() in {
      item.date for item in holidays
    }
    return closed or current.time() < time(8, 30) or current.time() >= time(16)
  except Exception:
    return False
