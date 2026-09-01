"""Auditable dividend-factor coverage derived from durable database requests."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from .models import DividendFactorCoverageReport
from .normalization import as_datetime

DIVIDEND_FACTOR_SOURCE = "qmt-get-divid-factors-v1"
DIVIDEND_FACTOR_EVIDENCE_SCHEMA_VERSION = 2
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class _ValidatedEvidence:
  request_id: str
  start: datetime
  end: datetime
  stock_code: str
  completed_at: datetime
  record_count: int
  content_sha256: str


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
  a symbol had no corporate action. Only a schema-v2 per-code replacement audit
  whose digest still equals the current 10-column database rows is accepted.
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
  canonical_evidence: list[dict[str, Any]] = []
  invalid_count = 0

  for row in frame.to_dict(orient="records"):
    parsed = _valid_evidence_row(row)
    if parsed is None:
      invalid_count += 1
      continue
    if parsed.stock_code not in requested_set:
      continue
    if parsed.end < start or parsed.start > end:
      continue
    intervals[parsed.stock_code].append(
      (max(start, parsed.start), min(end, parsed.end))
    )
    valid_request_ids.add(parsed.request_id)
    completed_times.append(parsed.completed_at)
    canonical_evidence.append(
      {
        "request_id": parsed.request_id,
        "stock_code": parsed.stock_code,
        "start": parsed.start.strftime("%Y%m%d"),
        "end": parsed.end.strftime("%Y%m%d"),
        "completed_at": parsed.completed_at.isoformat(),
        "record_count": parsed.record_count,
        "content_sha256": parsed.content_sha256,
      }
    )

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
    evidence_schema_version=(
      DIVIDEND_FACTOR_EVIDENCE_SCHEMA_VERSION if canonical_evidence else None
    ),
    verified_code_window_count=len(canonical_evidence),
    evidence_content_sha256=(
      hashlib.sha256(
        json.dumps(
          sorted(
            canonical_evidence,
            key=lambda item: (
              item["request_id"],
              item["stock_code"],
              item["start"],
              item["end"],
            ),
          ),
          ensure_ascii=True,
          separators=(",", ":"),
        ).encode("utf-8")
      ).hexdigest()
      if canonical_evidence
      else None
    ),
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
) -> _ValidatedEvidence | None:
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
  if (
    type(row.get("audit_schema_version")) is not int
    or row.get("audit_schema_version") != DIVIDEND_FACTOR_EVIDENCE_SCHEMA_VERSION
    or row.get("current_matches") is not True
  ):
    return None
  record_count = _nonnegative_int(row.get("record_count"))
  current_record_count = _nonnegative_int(row.get("current_record_count"))
  content_sha256 = str(row.get("content_sha256") or "")
  current_content_sha256 = str(row.get("current_content_sha256") or "")
  if (
    record_count is None
    or current_record_count != record_count
    or _SHA256.fullmatch(content_sha256) is None
    or current_content_sha256 != content_sha256
  ):
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
    or len(codes) != 1
  ):
    return None
  return _ValidatedEvidence(
    request_id=request_id,
    start=row_start,
    end=row_end,
    stock_code=next(iter(codes)),
    completed_at=completed_at,
    record_count=record_count,
    content_sha256=content_sha256,
  )


def _nonnegative_int(value: Any) -> int | None:
  if isinstance(value, bool) or not isinstance(value, int) or value < 0:
    return None
  return value


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
