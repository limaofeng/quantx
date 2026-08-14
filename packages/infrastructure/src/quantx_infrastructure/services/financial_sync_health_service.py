"""Read-model for financial synchronization health."""

from datetime import date, datetime, time, timedelta
from typing import Any

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.repositories.financial_sync_run_repository import (
  FinancialSyncRunRepository,
)


def _previous_weekday(value: date) -> date:
  candidate = value - timedelta(days=1)
  while candidate.weekday() >= 5:
    candidate -= timedelta(days=1)
  return candidate


def _expected_sync_date(now: datetime) -> date:
  if now.weekday() >= 5:
    return _previous_weekday(now.date())
  if now.time() < time(10, 30):
    return _previous_weekday(now.date())
  return now.date()


async def financial_sync_health(db, *, now: datetime | None = None) -> dict[str, Any]:
  current = now or time_utils.now()
  repo = FinancialSyncRunRepository(db)
  latest = await repo.find_latest()
  latest_success = await repo.find_latest_success()
  if latest is None:
    return {
      "status": "NEVER_RUN",
      "last_completed_at": None,
      "last_success_at": None,
      "requested_codes": 0,
      "synced_codes": 0,
      "empty_codes": 0,
      "statement_rows": 0,
      "metric_rows": 0,
      "is_stale": True,
      "warnings": ["尚无财务同步运行记录"],
    }

  completed_at = latest.completed_at
  is_stale = (
    completed_at is None
    or completed_at.date() < _expected_sync_date(current)
  )
  warnings: list[str] = []
  if latest.warnings:
    warnings.append(str(latest.warnings))
  if is_stale:
    warnings.append(
      f"最近财务同步未覆盖预期日期 {_expected_sync_date(current).isoformat()}"
    )

  raw_status = str(latest.status or "failed").lower()
  status = {
    "running": "RUNNING",
    "success": "SUCCESS",
    "partial_failure": "PARTIAL_FAILURE",
    "failed": "FAILED",
  }.get(raw_status, "FAILED")
  if status == "SUCCESS" and is_stale:
    status = "STALE"
  return {
    "status": status,
    "last_completed_at": completed_at,
    "last_success_at": (
      latest_success.completed_at if latest_success is not None else None
    ),
    "requested_codes": int(latest.requested_codes or 0),
    "synced_codes": int(latest.synced_codes or 0),
    "empty_codes": int(latest.empty_codes or 0),
    "statement_rows": int(latest.statement_rows or 0),
    "metric_rows": int(latest.metric_rows or 0),
    "is_stale": is_stale,
    "warnings": warnings,
  }
