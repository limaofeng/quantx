"""Auditable dividend-factor coverage derived from durable database requests."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from .models import DividendFactorCoverageReport
from .normalization import as_datetime

DIVIDEND_FACTOR_SOURCE = "qmt-get-divid-factors-v1"


class DividendFactorCoverageError(RuntimeError):
  """Required sparse factor coverage has not been proven."""

  def __init__(self, report: DividendFactorCoverageReport) -> None:
    self.report = report
    missing_preview = ", ".join(report.uncovered_codes[:5])
    if len(report.uncovered_codes) > 5:
      missing_preview += ", ..."
    message = (
      "复权因子回填覆盖不足: "
      f"{len(report.covered_codes)}/{len(report.requested_codes)} 个标的覆盖 "
      f"{report.requested_start.date()}..{report.requested_end.date()}"
    )
    if missing_preview:
      message += f"；未覆盖: {missing_preview}"
    super().__init__(message)


def build_dividend_factor_coverage_report(
  evidence: pd.DataFrame | Iterable[dict[str, Any]] | None,
  *,
  requested_codes: Iterable[str],
  requested_start: date | datetime,
  requested_end: date | datetime,
) -> DividendFactorCoverageReport:
  """Prove per-code inclusive date coverage from completed durable requests.

  Factor rows are sparse, so an empty ``divid_factors`` result cannot prove that
  a symbol had no corporate action. A completed ``market_data_request`` does:
  the v1 ingestion path validates the transfer and atomically replaces the exact
  ``stock_codes × ex_date window`` before marking the request completed.
  """

  start = as_datetime(requested_start)
  end = as_datetime(requested_end)
  if end < start:
    raise ValueError("复权因子覆盖结束日期不能早于开始日期")
  requested = tuple(
    sorted({str(code).strip().upper() for code in requested_codes if str(code).strip()})
  )
  requested_set = set(requested)
  frame = _evidence_frame(evidence)
  intervals: dict[str, list[tuple[datetime, datetime]]] = {
    code: [] for code in requested
  }
  valid_request_ids: set[str] = set()
  completed_times: list[datetime] = []
  invalid_count = 0

  for row in frame.to_dict(orient="records"):
    parsed = _valid_evidence_row(row)
    if parsed is None:
      invalid_count += 1
      continue
    request_id, row_start, row_end, codes, completed_at = parsed
    selected = requested_set.intersection(codes)
    if not selected or row_end < start or row_start > end:
      continue
    clipped = (max(start, row_start), min(end, row_end))
    for code in selected:
      intervals[code].append(clipped)
    valid_request_ids.add(request_id)
    completed_times.append(completed_at)

  covered = tuple(
    code for code in requested if _covers_window(intervals[code], start=start, end=end)
  )
  uncovered = tuple(code for code in requested if code not in set(covered))
  warnings: list[str] = []
  if not valid_request_ids:
    warnings.append("没有可用的已完成 qmt-get-divid-factors-v1 数据库请求证据")
  if invalid_count:
    warnings.append(f"{invalid_count} 条复权因子请求证据无效，未计入覆盖")
  if uncovered:
    warnings.append(f"{len(uncovered)} 个标的缺少完整复权因子窗口证明")

  return DividendFactorCoverageReport(
    requested_start=start,
    requested_end=end,
    requested_codes=requested,
    covered_codes=covered,
    uncovered_codes=uncovered,
    evidence_request_ids=tuple(sorted(valid_request_ids)),
    latest_completed_at=max(completed_times) if completed_times else None,
    invalid_evidence_count=invalid_count,
    warnings=tuple(warnings),
  )


def _evidence_frame(
  evidence: pd.DataFrame | Iterable[dict[str, Any]] | None,
) -> pd.DataFrame:
  if evidence is None:
    return pd.DataFrame()
  if isinstance(evidence, pd.DataFrame):
    return evidence.copy()
  return pd.DataFrame(list(evidence))


def _valid_evidence_row(
  row: dict[str, Any],
) -> tuple[str, datetime, datetime, set[str], datetime] | None:
  if str(row.get("status") or "").upper() != "COMPLETED":
    return None
  if str(row.get("source") or "") != DIVIDEND_FACTOR_SOURCE:
    return None
  request_id = str(row.get("request_id") or "").strip()
  if not request_id:
    return None
  try:
    expected_chunks = int(row.get("expected_chunks") or 0)
    received_chunks = int(row.get("received_chunks") or 0)
  except (TypeError, ValueError):
    return None
  if expected_chunks <= 0 or received_chunks != expected_chunks:
    return None
  row_start = _parse_date(row.get("start_date"))
  row_end = _parse_date(row.get("end_date"))
  completed_at = _parse_datetime(row.get("completed_at"))
  codes = _parse_codes(row.get("stock_codes"))
  if (
    row_start is None
    or row_end is None
    or row_end < row_start
    or completed_at is None
    or not codes
  ):
    return None
  return request_id, row_start, row_end, codes, completed_at


def _parse_date(value: Any) -> datetime | None:
  compact = str(value or "").strip().replace("-", "")
  if len(compact) != 8 or not compact.isdigit():
    return None
  try:
    return datetime.strptime(compact, "%Y%m%d")
  except ValueError:
    return None


def _parse_datetime(value: Any) -> datetime | None:
  if value is None or value == "":
    return None
  try:
    timestamp = pd.Timestamp(value)
  except (TypeError, ValueError):
    return None
  if pd.isna(timestamp):
    return None
  if timestamp.tzinfo is not None:
    timestamp = timestamp.tz_convert("UTC").tz_localize(None)
  return timestamp.to_pydatetime()


def _parse_codes(value: Any) -> set[str]:
  if isinstance(value, str):
    try:
      value = json.loads(value)
    except json.JSONDecodeError:
      return set()
  if not isinstance(value, (list, tuple, set)):
    return set()
  return {str(code).strip().upper() for code in value if str(code).strip()}


def _covers_window(
  intervals: list[tuple[datetime, datetime]],
  *,
  start: datetime,
  end: datetime,
) -> bool:
  cursor = start
  for interval_start, interval_end in sorted(intervals):
    if interval_end < cursor:
      continue
    if interval_start > cursor:
      return False
    cursor = max(cursor, interval_end + timedelta(days=1))
    if cursor > end:
      return True
  return cursor > end
