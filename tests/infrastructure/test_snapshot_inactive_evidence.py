from datetime import date, datetime

import pytest
from quantx_infrastructure.services.snapshot_inactive_evidence import (
  load_snapshot_inactive_empty_proofs,
)


class _Rows:
  def __init__(self, rows):
    self.rows = rows

  def mappings(self):
    return self

  def all(self):
    return self.rows


@pytest.mark.asyncio
async def test_inactive_empty_proofs_use_one_strict_batched_query():
  executions = []
  session_events = []

  class Session:
    async def execute(self, statement, parameters):
      executions.append((str(statement), parameters))
      return _Rows(
        [
          {
            "instrument_code": "560650.SH",
            "trading_date": datetime(2026, 5, 20),
          },
          {
            "instrument_code": "000001.SZ",
            "trading_date": "2026-05-20",
          },
          {
            "instrument_code": "OUTSIDE.SH",
            "trading_date": date(2026, 5, 20),
          },
          {
            "instrument_code": "000001.SZ",
            "trading_date": "not-a-date",
          },
        ]
      )

  async def db_factory():
    session_events.append("open")
    try:
      yield Session()
    finally:
      session_events.append("close")

  proofs = await load_snapshot_inactive_empty_proofs(
    [
      ("560650.sh", date(2026, 5, 20)),
      ("000001.SZ", date(2026, 5, 20)),
      ("560650.SH", date(2026, 5, 20)),
    ],
    db_factory,
  )

  assert proofs == {
    ("560650.SH", date(2026, 5, 20)),
    ("000001.SZ", date(2026, 5, 20)),
  }
  assert session_events == ["open", "close"]
  assert len(executions) == 1
  sql = " ".join(executions[0][0].split())
  assert "FROM unnest(:candidate_codes, :candidate_dates)" in sql
  assert "evidence_request.status = 'COMPLETED'" in sql
  assert (
    "evidence_request.ingestion_result -> 'persistence_verification' "
    "->> 'status' = 'verified'"
  ) in sql
  assert "request_payload ->> 'start_time'" in sql
  assert "request_payload ->> 'end_time'" in sql
  assert "IN ('1m', '1d')" in sql
  assert "coverage.value ->> 'point_count' = '0'" in sql
  assert "summary.value ->> 'row_count' = '0'" in sql
  assert "summary.value ->> 'no_data_reason' = 'XT_DATA_NO_ROWS'" in sql
  assert "AND NOT EXISTS" in sql
  assert "contradictory_coverage.value ->> 'point_count'" in sql
  assert "contradictory_summary.value ->> 'row_count'" in sql
  assert "HAVING COUNT(DISTINCT period) = 2" in sql
  assert executions[0][1] == {
    "candidate_codes": ["000001.SZ", "560650.SH"],
    "candidate_dates": [date(2026, 5, 20), date(2026, 5, 20)],
  }


@pytest.mark.asyncio
async def test_inactive_empty_proofs_skip_database_when_there_are_no_candidates():
  opened = False

  async def db_factory():
    nonlocal opened
    opened = True
    yield object()

  assert await load_snapshot_inactive_empty_proofs([], db_factory) == set()
  assert opened is False
