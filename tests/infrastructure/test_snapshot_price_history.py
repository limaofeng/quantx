from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import pytest
from quantx_infrastructure.repositories.divid_factor_repository import (
  divid_factor_codes_sha256,
  divid_factor_rows_sha256,
)
from quantx_infrastructure.services.data_exchange_reference import (
  reference_factor_coverage,
)
from quantx_infrastructure.services.divid_factor_evidence import (
  DividFactorEvidence,
  current_rows_match_evidence,
  parse_divid_factor_evidence,
)
from quantx_infrastructure.services.snapshot_price_history import (
  adjust_price_frame,
  covered_codes,
  load_snapshot_price_history,
)
from sqlalchemy.dialects import postgresql


def _factor_payload(codes, start="20250101", end="20250131"):
  return {
    "operation": "divid_factors",
    "source": "qmt-get-divid-factors-v1",
    "stock_list": sorted(codes),
    "start_time": start,
    "end_time": end,
  }


def _factor_ingestion(codes, rows, start="20250101", end="20250131"):
  codes = sorted(codes)
  rows_by_code = {code: [] for code in codes}
  for row in rows:
    rows_by_code[row[0]].append(row)
  digest = divid_factor_rows_sha256(rows)
  return {
    "operation": "divid_factors",
    "records_received": len(rows),
    "records_saved": len(rows),
    "replacement_audit": {
      "audit_schema_version": 2,
      "prior_count": 0,
      "deleted_count": 0,
      "inserted_count": len(rows),
      "verified_count": len(rows),
      "stock_count": len(codes),
      "stock_codes_sha256": divid_factor_codes_sha256(codes),
      "start_ex_date": start,
      "end_ex_date": end,
      "source_sha256": digest,
      "persisted_sha256": digest,
      "code_audits": {
        code: {
          "record_count": len(rows_by_code[code]),
          "source_sha256": divid_factor_rows_sha256(rows_by_code[code]),
          "persisted_sha256": divid_factor_rows_sha256(rows_by_code[code]),
        }
        for code in codes
      },
    },
  }


def _factor_row(
  code: str,
  ex_date: str,
  *,
  interest: str = "0",
  dr: str = "1",
):
  return (
    code,
    datetime.strptime(ex_date, "%Y%m%d"),
    ex_date,
    Decimal(interest),
    Decimal("0"),
    Decimal("0"),
    Decimal("0"),
    Decimal("0"),
    Decimal("0"),
    Decimal(dr),
  )


def test_reference_factor_coverage_distinguishes_stale_invalid_and_changed_rows():
  code = "600000.SH"
  rows = [_factor_row(code, "20250115")]
  proof = {
    "request_id": "reference-audit",
    "request_payload": _factor_payload([code]),
    "status": "COMPLETED",
    "expected_chunks": 1,
    "received_chunks": 1,
    "completed_at": datetime(2025, 2, 1),
    "ingestion_result": _factor_ingestion([code], rows),
  }
  def coverage(value=proof, current=rows, day=date(2025, 1, 31)):
    return reference_factor_coverage(value, current, code=code, day=day)

  assert coverage()["status"] == "VERIFIED"
  assert coverage(day=date(2025, 2, 1)) == {
    "status": "UNVERIFIED",
    "reason": "COVERAGE_END_BEFORE_AS_OF",
    "audited_end_date": "2025-01-31",
  }
  assert coverage(None)["reason"] == "AUDIT_MISSING"
  assert coverage({**proof, "received_chunks": 0})["reason"] == "AUDIT_INVALID"
  assert coverage(current=[])["reason"] == "CURRENT_ROWS_MISMATCH"
  assert coverage(current=[_factor_row(code, "20250115", dr="2")])["reason"] == (
    "CURRENT_ROWS_MISMATCH"
  )


def test_corporate_action_adjustment_is_prefix_invariant_and_keeps_raw_price():
  raw = pd.DataFrame(
    {
      "time": pd.date_range("2025-01-01", periods=4),
      "open": [10, 5, 5, 2.5],
      "close": [10, 5, 5, 2.5],
      "high": [10, 5, 5, 2.5],
      "low": [10, 5, 5, 2.5],
    }
  )
  factors = [(datetime(2025, 1, 2), 2), (datetime(2025, 1, 4), 2)]
  adjusted = adjust_price_frame(raw, factors)
  assert adjusted.close.tolist() == [10, 10, 10, 10]
  assert adjusted.raw_close.tolist() == raw.close.tolist()
  pd.testing.assert_frame_equal(
    adjusted.iloc[:3], adjust_price_frame(raw.iloc[:3], factors)
  )
  with pytest.raises(ValueError):
    adjust_price_frame(raw, [(datetime(2025, 1, 2), 0)])


def test_sparse_factor_coverage_requires_complete_durable_evidence():
  def evidence(first, last):
    return DividFactorEvidence(
      request_id=f"{first}-{last}",
      stock_code="000001.SZ",
      start_date=datetime.strptime(first, "%Y%m%d").date(),
      end_date=datetime.strptime(last, "%Y%m%d").date(),
      completed_at=datetime(2025, 2, 1),
      record_count=0,
      content_sha256=divid_factor_rows_sha256([]),
    )

  bounds = {"000001.SZ": (date(2025, 1, 1), date(2025, 1, 31))}
  assert covered_codes([], bounds) == set()
  assert covered_codes(
    [evidence("20250101", "20250115"), evidence("20250116", "20250131")],
    bounds,
  ) == {"000001.SZ"}
  assert (
    covered_codes(
      [evidence("20250101", "20250115"), evidence("20250117", "20250131")],
      bounds,
    )
    == set()
  )


def test_exact_factor_evidence_rejects_legacy_and_current_digest_drift():
  row = (
    "000001.SZ",
    datetime(2025, 1, 15),
    "20250115",
    Decimal("1"),
    Decimal("0"),
    Decimal("0"),
    Decimal("0"),
    Decimal("0"),
    Decimal("0"),
    Decimal("1.01"),
  )
  payload = _factor_payload(["000001.SZ"])
  common = {
    "request_id": "request-1",
    "request_payload": payload,
    "status": "COMPLETED",
    "expected_chunks": 1,
    "received_chunks": 1,
    "completed_at": datetime(2025, 2, 1),
  }
  assert parse_divid_factor_evidence(**common, ingestion_result=None) is None
  schema_v1 = _factor_ingestion(["000001.SZ"], [row])
  schema_v1["replacement_audit"].pop("code_audits")
  schema_v1["replacement_audit"]["audit_schema_version"] = 1
  assert parse_divid_factor_evidence(**common, ingestion_result=schema_v1) is None

  evidence_items = parse_divid_factor_evidence(
    **common,
    ingestion_result=_factor_ingestion(["000001.SZ"], [row]),
  )

  assert evidence_items is not None
  evidence = evidence_items[0]
  assert current_rows_match_evidence(evidence, [row])
  assert not current_rows_match_evidence(evidence, [])


@pytest.mark.asyncio
async def test_snapshot_loader_accepts_exact_audited_empty_factor_window():
  payload = _factor_payload(["000001.SZ"])
  ingestion = _factor_ingestion(["000001.SZ"], [])

  class Result:
    def __init__(self, rows):
      self.rows = rows

    def all(self):
      return self.rows

  class Session:
    def __init__(self):
      self.statements = []

    async def execute(self, statement):
      self.statements.append(statement)
      if len(self.statements) == 1:
        return Result([])
      if len(self.statements) == 2:
        return Result(
          [
            (
              "request-empty",
              payload,
              "COMPLETED",
              1,
              1,
              datetime(2025, 2, 1),
              ingestion,
            )
          ]
        )
      return Result([])

  session = Session()
  session_closed = False

  async def db_factory():
    nonlocal session_closed
    try:
      yield session
    finally:
      session_closed = True

  frame = pd.DataFrame(
    {
      "time": pd.date_range("2025-01-01", periods=2, tz="Asia/Shanghai"),
      "open": [10, 11],
      "high": [10, 11],
      "low": [10, 11],
      "close": [10, 11],
    }
  )

  result = await load_snapshot_price_history({"000001.SZ": frame}, db_factory)

  assert result["000001.SZ"]["close"].tolist() == [10, 11]
  assert len(session.statements) == 3
  assert "pg_advisory_xact_lock_shared" in str(session.statements[0])
  assert session_closed is True


def test_per_code_evidence_survives_an_overlapping_rewrite_of_another_code():
  original_a = _factor_row("000001.SZ", "20250115", interest="1")
  current_a = _factor_row("000001.SZ", "20250115", interest="2")
  current_b = _factor_row("000002.SZ", "20250115", interest="3")
  original_items = parse_divid_factor_evidence(
    request_id="original",
    request_payload=_factor_payload(["000001.SZ", "000002.SZ"]),
    status="COMPLETED",
    expected_chunks=1,
    received_chunks=1,
    completed_at=datetime(2025, 2, 1),
    ingestion_result=_factor_ingestion(
      ["000001.SZ", "000002.SZ"],
      [original_a, current_b],
    ),
  )
  replacement_items = parse_divid_factor_evidence(
    request_id="replacement",
    request_payload=_factor_payload(["000001.SZ"]),
    status="COMPLETED",
    expected_chunks=1,
    received_chunks=1,
    completed_at=datetime(2025, 2, 2),
    ingestion_result=_factor_ingestion(["000001.SZ"], [current_a]),
  )

  assert original_items is not None
  assert replacement_items is not None
  original_by_code = {item.stock_code: item for item in original_items}
  assert not current_rows_match_evidence(
    original_by_code["000001.SZ"], [current_a, current_b]
  )
  assert current_rows_match_evidence(
    original_by_code["000002.SZ"], [current_a, current_b]
  )
  assert current_rows_match_evidence(replacement_items[0], [current_a, current_b])


@pytest.mark.asyncio
async def test_factor_application_is_independent_of_other_batch_history_bounds():
  factor_row = _factor_row("000001.SZ", "20250601", dr="2")
  payload = _factor_payload(
    ["000001.SZ", "000002.SZ"],
    start="20250101",
    end="20261231",
  )
  ingestion = _factor_ingestion(
    ["000001.SZ", "000002.SZ"],
    [factor_row],
    start="20250101",
    end="20261231",
  )
  request_row = (
    "request-bounds",
    payload,
    "COMPLETED",
    1,
    1,
    datetime(2026, 12, 31),
    ingestion,
  )

  class Result:
    def __init__(self, rows):
      self.rows = rows

    def all(self):
      return self.rows

  class Session:
    def __init__(self):
      self.statements = []

    async def execute(self, statement):
      self.statements.append(statement)
      if len(self.statements) == 1:
        return Result([])
      return Result([request_row] if len(self.statements) == 2 else [factor_row])

  def db_factory_for(session):
    async def factory():
      yield session

    return factory

  short_frame = pd.DataFrame(
    {
      "time": pd.date_range("2026-01-01", periods=2, tz="Asia/Shanghai"),
      "open": [10, 11],
      "high": [10, 11],
      "low": [10, 11],
      "close": [10, 11],
    }
  )
  long_frame = pd.DataFrame(
    {
      "time": pd.to_datetime(
        ["2025-01-01T00:00:00+08:00", "2026-01-02T00:00:00+08:00"]
      ),
      "open": [20, 21],
      "high": [20, 21],
      "low": [20, 21],
      "close": [20, 21],
    }
  )

  alone = await load_snapshot_price_history(
    {"000001.SZ": short_frame}, db_factory_for(Session())
  )
  batched = await load_snapshot_price_history(
    {"000001.SZ": short_frame, "000002.SZ": long_frame},
    db_factory_for(Session()),
  )

  pd.testing.assert_frame_equal(alone["000001.SZ"], batched["000001.SZ"])
  assert batched["000001.SZ"]["close"].tolist() == [10, 11]


@pytest.mark.asyncio
async def test_evidence_budget_filters_4097_unrelated_requests_before_limit():
  payload = _factor_payload(["000001.SZ"])
  ingestion = _factor_ingestion(["000001.SZ"], [])
  relevant = (
    "request-relevant",
    payload,
    "COMPLETED",
    1,
    1,
    datetime(2025, 2, 1),
    ingestion,
  )
  unrelated = (
    "request-unrelated",
    _factor_payload(["999999.SZ"]),
    "COMPLETED",
    1,
    1,
    datetime(2025, 2, 1),
    _factor_ingestion(["999999.SZ"], []),
  )

  class Result:
    def __init__(self, rows):
      self.rows = rows

    def all(self):
      return self.rows

  class Session:
    def __init__(self):
      self.statements = []

    async def execute(self, statement):
      self.statements.append(statement)
      if len(self.statements) == 1:
        return Result([])
      if len(self.statements) > 2:
        return Result([])
      compiled = statement.compile(dialect=postgresql.dialect())
      sql = str(compiled)
      if "?|" not in sql:
        return Result([unrelated] * 4097)
      assert sql.index("?|") < sql.upper().index("LIMIT")
      assert compiled.params["factor_evidence_codes"] == ["000001.SZ"]
      return Result([relevant])

  session = Session()

  async def db_factory():
    yield session

  frame = pd.DataFrame(
    {
      "time": pd.date_range("2025-01-01", periods=2, tz="Asia/Shanghai"),
      "open": [10, 11],
      "high": [10, 11],
      "low": [10, 11],
      "close": [10, 11],
    }
  )

  result = await load_snapshot_price_history({"000001.SZ": frame}, db_factory)

  assert result["000001.SZ"]["close"].tolist() == [10, 11]
  assert len(session.statements) == 3
  assert "pg_advisory_xact_lock_shared" in str(session.statements[0])
