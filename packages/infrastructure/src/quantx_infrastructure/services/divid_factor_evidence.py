"""Exact, current database evidence for sparse dividend-factor windows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from quantx_infrastructure.repositories.divid_factor_repository import (
  divid_factor_codes_sha256,
  divid_factor_rows_sha256,
)

DIVID_FACTOR_SOURCE = "qmt-get-divid-factors-v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class DividFactorEvidence:
  """One code slice from a self-consistent authoritative replacement audit."""

  request_id: str
  stock_code: str
  start_date: date
  end_date: date
  completed_at: datetime
  record_count: int
  content_sha256: str


def parse_divid_factor_evidence(
  *,
  request_id: Any,
  request_payload: Any,
  status: Any,
  expected_chunks: Any,
  received_chunks: Any,
  completed_at: Any,
  ingestion_result: Any,
) -> tuple[DividFactorEvidence, ...] | None:
  """Accept only schema-v2 per-code audits with self-consistent metadata."""

  payload = _json_object(request_payload)
  ingestion = _json_object(ingestion_result)
  if (
    str(status or "").upper() != "COMPLETED"
    or payload is None
    or ingestion is None
    or payload.get("operation") != "divid_factors"
    or payload.get("source") != DIVID_FACTOR_SOURCE
    or ingestion.get("operation") != "divid_factors"
  ):
    return None
  expected = _nonnegative_int(expected_chunks)
  received = _nonnegative_int(received_chunks)
  if expected is None or expected <= 0 or received != expected:
    return None
  request_key = str(request_id or "").strip()
  completed = _datetime(completed_at)
  if not request_key or completed is None:
    return None

  payload_codes = payload.get("stock_list")
  if not isinstance(payload_codes, list):
    return None
  codes = tuple(
    sorted(
      str(code).strip().upper() for code in payload_codes if str(code or "").strip()
    )
  )
  if not codes or list(codes) != payload_codes or len(codes) != len(set(codes)):
    return None
  start = _date(payload.get("start_time"))
  end = _date(payload.get("end_time"))
  if start is None or end is None or end < start:
    return None

  audit = ingestion.get("replacement_audit")
  if (
    not isinstance(audit, dict)
    or type(audit.get("audit_schema_version")) is not int
    or audit.get("audit_schema_version") != 2
  ):
    return None
  counts = {
    key: _nonnegative_int(container.get(key))
    for container, keys in (
      (ingestion, ("records_received", "records_saved")),
      (
        audit,
        (
          "prior_count",
          "deleted_count",
          "inserted_count",
          "verified_count",
          "stock_count",
        ),
      ),
    )
    for key in keys
  }
  if any(value is None for value in counts.values()):
    return None
  if counts["prior_count"] != counts["deleted_count"]:
    return None
  if counts["stock_count"] != len(codes):
    return None
  if audit.get("stock_codes_sha256") != divid_factor_codes_sha256(codes):
    return None
  if audit.get("start_ex_date") != start.strftime("%Y%m%d") or audit.get(
    "end_ex_date"
  ) != end.strftime("%Y%m%d"):
    return None
  persisted_count = counts["inserted_count"]
  if not (
    counts["records_received"]
    == counts["records_saved"]
    == persisted_count
    == counts["verified_count"]
  ):
    return None
  source_digest = str(audit.get("source_sha256") or "")
  persisted_digest = str(audit.get("persisted_sha256") or "")
  if _SHA256.fullmatch(source_digest) is None or source_digest != persisted_digest:
    return None
  code_audits = audit.get("code_audits")
  if not isinstance(code_audits, dict) or set(code_audits) != set(codes):
    return None
  result = []
  per_code_total = 0
  for code in codes:
    item = code_audits.get(code)
    if not isinstance(item, dict):
      return None
    record_count = _nonnegative_int(item.get("record_count"))
    code_source_digest = str(item.get("source_sha256") or "")
    code_persisted_digest = str(item.get("persisted_sha256") or "")
    if (
      record_count is None
      or _SHA256.fullmatch(code_source_digest) is None
      or code_source_digest != code_persisted_digest
    ):
      return None
    per_code_total += record_count
    result.append(
      DividFactorEvidence(
        request_id=request_key,
        stock_code=code,
        start_date=start,
        end_date=end,
        completed_at=completed,
        record_count=record_count,
        content_sha256=code_source_digest,
      )
    )
  if per_code_total != persisted_count:
    return None
  return tuple(result)


def current_rows_match_evidence(
  evidence: DividFactorEvidence,
  rows: Iterable[Sequence[Any]],
) -> bool:
  """Verify the current authoritative table still equals the recorded audit."""

  try:
    selected = [
      row
      for row in rows
      if str(row[0]).strip().upper() == evidence.stock_code
      and evidence.start_date <= _row_ex_date(row[2]) <= evidence.end_date
    ]
    return len(selected) == evidence.record_count and (
      divid_factor_rows_sha256(selected) == evidence.content_sha256
    )
  except (TypeError, ValueError):
    return False


def _json_object(value: Any) -> dict[str, Any] | None:
  if isinstance(value, str):
    try:
      value = json.loads(value)
    except (TypeError, ValueError):
      return None
  return value if isinstance(value, dict) else None


def _nonnegative_int(value: Any) -> int | None:
  if isinstance(value, bool) or not isinstance(value, int) or value < 0:
    return None
  return value


def _date(value: Any) -> date | None:
  compact = str(value or "").strip().replace("-", "")
  if len(compact) != 8 or not compact.isdigit():
    return None
  try:
    return datetime.strptime(compact, "%Y%m%d").date()
  except ValueError:
    return None


def _datetime(value: Any) -> datetime | None:
  if isinstance(value, datetime):
    return value
  if not value:
    return None
  try:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
  except ValueError:
    return None


def _row_ex_date(value: Any) -> date:
  parsed = _date(value)
  if parsed is None:
    raise ValueError("复权因子数据库行包含非法 ex_date")
  return parsed
