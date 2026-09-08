"""Audited source responses for isolated market-sync orchestration tests."""

from datetime import date, datetime, timedelta


def trading_days(start: str, end: str) -> list[date]:
  first = datetime.strptime(start, "%Y%m%d").date()
  last = datetime.strptime(end, "%Y%m%d").date()
  return [
    day
    for offset in range((last - first).days + 1)
    if (day := first + timedelta(days=offset)).weekday() < 5
  ]


def completed_transfer(payload: dict, request_id: str) -> dict:
  days = trading_days(payload["start_time"], payload["end_time"])
  count = len(days) * len(payload["stock_list"]) * len(payload["periods"])
  return {
    "status": "completed",
    "request_id": request_id,
    "records_received": count,
    "records_saved": count,
    "code_summaries": [
      {"code": code, "period": period, "row_count": len(days)}
      for period in payload["periods"]
      for code in payload["stock_list"]
    ],
    "day_coverage": [
      {
        "instrument_code": code,
        "period": period,
        "trading_date": day.isoformat(),
        "point_count": 1,
      }
      for period in payload["periods"]
      for code in payload["stock_list"]
      for day in days
    ],
  }
