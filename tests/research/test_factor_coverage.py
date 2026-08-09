from datetime import datetime

import pandas as pd
from quantx_research.data import build_dividend_factor_coverage_report


def test_factor_coverage_can_be_proven_by_adjacent_completed_windows() -> None:
  evidence = pd.DataFrame(
    [
      {
        "request_id": "first",
        "source": "qmt-get-divid-factors-v1",
        "status": "COMPLETED",
        "start_date": "20240101",
        "end_date": "20240103",
        "stock_codes": ["000001.SZ", "000002.SZ"],
        "expected_chunks": 1,
        "received_chunks": 1,
        "completed_at": datetime(2024, 1, 4),
      },
      {
        "request_id": "second",
        "source": "qmt-get-divid-factors-v1",
        "status": "COMPLETED",
        "start_date": "20240104",
        "end_date": "20240105",
        "stock_codes": ["000001.SZ"],
        "expected_chunks": 1,
        "received_chunks": 1,
        "completed_at": datetime(2024, 1, 6),
      },
      {
        "request_id": "third",
        "source": "qmt-get-divid-factors-v1",
        "status": "COMPLETED",
        "start_date": "20231201",
        "end_date": "20240131",
        "stock_codes": ["000002.SZ"],
        "expected_chunks": 2,
        "received_chunks": 2,
        "completed_at": datetime(2024, 2, 1),
      },
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


def test_factor_coverage_rejects_wrong_source_and_incomplete_transfer() -> None:
  evidence = pd.DataFrame(
    [
      {
        "request_id": "wrong-source",
        "source": "legacy",
        "status": "COMPLETED",
        "start_date": "20240101",
        "end_date": "20240105",
        "stock_codes": ["000001.SZ"],
        "expected_chunks": 1,
        "received_chunks": 1,
        "completed_at": datetime(2024, 1, 6),
      },
      {
        "request_id": "missing-chunk",
        "source": "qmt-get-divid-factors-v1",
        "status": "COMPLETED",
        "start_date": "20240101",
        "end_date": "20240105",
        "stock_codes": ["000002.SZ"],
        "expected_chunks": 2,
        "received_chunks": 1,
        "completed_at": datetime(2024, 1, 6),
      },
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
