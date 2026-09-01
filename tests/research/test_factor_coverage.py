from datetime import datetime

import pandas as pd
from quantx_research.data import build_dividend_factor_coverage_report


def evidence_row(
  *,
  request_id: str,
  stock_code: str,
  start_date: str,
  end_date: str,
  completed_at: datetime,
  source: str = "qmt-get-divid-factors-v1",
  expected_chunks: int = 1,
  received_chunks: int = 1,
  audit_schema_version: int = 2,
  current_matches: bool = True,
  digest: str = "a" * 64,
) -> dict:
  return {
    "request_id": request_id,
    "source": source,
    "status": "COMPLETED",
    "start_date": start_date,
    "end_date": end_date,
    "stock_codes": [stock_code],
    "expected_chunks": expected_chunks,
    "received_chunks": received_chunks,
    "completed_at": completed_at,
    "audit_schema_version": audit_schema_version,
    "record_count": 0,
    "content_sha256": digest,
    "current_record_count": 0,
    "current_content_sha256": digest,
    "current_matches": current_matches,
  }


def test_factor_coverage_can_be_proven_by_adjacent_completed_windows() -> None:
  evidence = pd.DataFrame(
    [
      evidence_row(
        request_id="first",
        stock_code="000001.SZ",
        start_date="20240101",
        end_date="20240103",
        completed_at=datetime(2024, 1, 4),
      ),
      evidence_row(
        request_id="first",
        stock_code="000002.SZ",
        start_date="20240101",
        end_date="20240103",
        completed_at=datetime(2024, 1, 4),
      ),
      evidence_row(
        request_id="second",
        stock_code="000001.SZ",
        start_date="20240104",
        end_date="20240105",
        completed_at=datetime(2024, 1, 6),
      ),
      evidence_row(
        request_id="third",
        stock_code="000002.SZ",
        start_date="20231201",
        end_date="20240131",
        completed_at=datetime(2024, 2, 1),
        expected_chunks=2,
        received_chunks=2,
      ),
    ]
  )

  report = build_dividend_factor_coverage_report(
    evidence,
    requested_codes=["000001.SZ", "000002.SZ"],
    requested_start=datetime(2024, 1, 1),
    requested_end=datetime(2024, 1, 5),
  )

  assert report.is_complete
  assert report.coverage_ratio == 1.0
  assert report.evidence_request_ids == ("first", "second", "third")
  assert report.uncovered_codes == ()
  assert report.evidence_schema_version == 2
  assert report.verified_code_window_count == 4
  assert len(report.evidence_content_sha256 or "") == 64
  restored = type(report).from_dict(report.to_dict())
  assert restored == report


def test_factor_coverage_rejects_wrong_source_and_incomplete_transfer() -> None:
  evidence = pd.DataFrame(
    [
      evidence_row(
        request_id="wrong-source",
        stock_code="000001.SZ",
        start_date="20240101",
        end_date="20240105",
        completed_at=datetime(2024, 1, 6),
        source="legacy",
      ),
      evidence_row(
        request_id="missing-chunk",
        stock_code="000002.SZ",
        start_date="20240101",
        end_date="20240105",
        completed_at=datetime(2024, 1, 6),
        expected_chunks=2,
        received_chunks=1,
      ),
    ]
  )

  report = build_dividend_factor_coverage_report(
    evidence,
    requested_codes=["000001.SZ", "000002.SZ"],
    requested_start=datetime(2024, 1, 1),
    requested_end=datetime(2024, 1, 5),
  )

  assert not report.is_complete
  assert report.uncovered_codes == ("000001.SZ", "000002.SZ")
  assert report.invalid_evidence_count == 2
  assert report.evidence_request_ids == ()
  assert report.evidence_schema_version is None
  assert report.verified_code_window_count == 0
  assert report.evidence_content_sha256 is None


def test_factor_coverage_rejects_legacy_audit_and_current_row_drift() -> None:
  evidence = pd.DataFrame(
    [
      evidence_row(
        request_id="legacy-audit",
        stock_code="000001.SZ",
        start_date="20240101",
        end_date="20240105",
        completed_at=datetime(2024, 1, 6),
        audit_schema_version=1,
      ),
      evidence_row(
        request_id="drifted-current-rows",
        stock_code="000002.SZ",
        start_date="20240101",
        end_date="20240105",
        completed_at=datetime(2024, 1, 6),
        current_matches=False,
      ),
    ]
  )

  report = build_dividend_factor_coverage_report(
    evidence,
    requested_codes=["000001.SZ", "000002.SZ"],
    requested_start=datetime(2024, 1, 1),
    requested_end=datetime(2024, 1, 5),
  )

  assert not report.is_complete
  assert report.invalid_evidence_count == 2
  assert report.uncovered_codes == ("000001.SZ", "000002.SZ")
