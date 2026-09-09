import re
import time
from datetime import datetime

import pytest
from quantx_infrastructure.services import market_data_persistence_verification as v

from tests.infrastructure.test_market_data_grouped_readback import (
  Connection,
  batch,
  rows,
)
from tests.infrastructure.test_market_data_persistence_verification import (
  START_MS,
  _summary,
)
from tests.infrastructure.test_market_data_readback_concurrency import verify

SCAN_ERROR = "Query would scan 432 Parquet files, exceeding the file limit"


class ScanLimitedConnection(Connection):
  """Reject wide scans, then return actual points within the requested bounds."""

  def __init__(self, missing=None):
    super().__init__([])
    self.missing = missing

  def query(self, **kwargs):
    sql = kwargs["query"]
    start, end = [
      datetime.fromisoformat(value.replace("Z", "+00:00"))
      for value in re.findall(r"time (?:>=|<) '([^']+)'", sql)[:2]
    ]
    if (end - start).total_seconds() > 1.1:
      self.pages.append(RuntimeError(SCAN_ERROR))
    else:
      values = []
      for code in ("A", "B"):
        for offset in (0, 1, 2, 3):
          point = rows([(code, offset)])["time"][0].as_py()
          if start <= point < end and (code, offset) != self.missing:
            values.append((code, offset))
      self.pages.append(rows(values))
    return super().query(**kwargs)


async def test_scan_split_preserves_all_expected_keys_and_bounds():
  conn = ScanLimitedConnection()
  result = await verify(
    [batch("A", (0, 1, 2, 3)), batch("B", (0, 1, 2, 3))], connection=conn
  )
  assert result["records_verified"] == 8
  assert len(conn.calls) == 3
  assert all(0 < call["timeout"] <= 60 for call in conn.calls)
  assert conn.calls[-1]["timeout"] <= conn.calls[0]["timeout"]
  assert all(reader.closed for reader in conn.readers)
  # Global time pruning precedes the individual OR ranges on every page.
  assert all(
    "WHERE period = $period AND time >=" in call["query"] for call in conn.calls
  )


async def test_split_never_accepts_missing_key():
  with pytest.raises(v.MarketDataPersistenceMismatchError):
    await verify(
      [batch("A", (0, 1, 2, 3)), batch("B", (0, 1, 2, 3))],
      connection=ScanLimitedConnection(missing=("B", 3)),
      max_attempts=1,
      retry_delays=(),
    )


async def test_minimal_capacity_failure_blocks_without_retry():
  conn = Connection([RuntimeError(SCAN_ERROR + " secret-provider-details")])
  with pytest.raises(v.MarketDataPersistenceBlockedError) as caught:
    await verify([batch("A", (0,))], connection=conn)
  assert len(conn.calls) == 1
  assert caught.value.reason_code == "DEPENDENCY_QUERY_CAPACITY_BLOCKED"
  assert caught.value.diagnostic["ranges"][0]["keys"] == 1
  assert len(caught.value.diagnostic["query_sha256"]) == 64
  assert caught.value.diagnostic["first_failure"]["page_after"] is None
  assert caught.value.diagnostic["first_failure"]["page_rows"] == 2000
  assert "secret-provider-details" not in str(caught.value.diagnostic)


def test_same_timestamp_splits_codes_and_tick_ordinals_without_losing_keys():
  groups = (batch("A", (0,)), batch("B", (0,)))
  assert v._split_expected_keys(groups) == ((groups[0],), (groups[1],))
  tick = v.ExpectedBarKeyBatch("T", "tick", ((1000, 0), (1000, 1), (1000, 2)))
  children = v._split_expected_keys((tick,))
  assert children is not None
  assert (
    tuple(key for child in children for item in child for key in item.keys) == tick.keys
  )
  assert v._split_expected_keys((groups[0],)) is None


@pytest.mark.parametrize("limit", ["nodes", "depth", "deadline"])
def test_budget_stops_before_next_query(monkeypatch, limit):
  conn = Connection([RuntimeError(SCAN_ERROR)])
  budget = v.ReadbackBudget()
  if limit == "nodes":
    monkeypatch.setattr(v, "MARKET_DATA_READBACK_MAX_SPLIT_NODES", 1)
  elif limit == "depth":
    monkeypatch.setattr(v, "MARKET_DATA_READBACK_MAX_SPLIT_DEPTH", 0)
  else:
    budget.deadline = time.monotonic() - 1
  with pytest.raises(v.MarketDataPersistenceBlockedError):
    v._read_expected_keys_bounded(
      batches=(batch("A"), batch("B")), connection=conn, page_rows=2000, budget=budget
    )
  assert len(conn.calls) == (0 if limit == "deadline" else 1)


def test_provider_failure_is_sanitized_and_not_misclassified():
  error = v._query_failure(
    RuntimeError("connection failed password=secret"), "SELECT 1"
  )
  assert type(error) is v.MarketDataPersistenceQueryError
  assert "secret" not in str(error)


def test_empty_source_scan_splits_without_claiming_uploaded_rows():
  conn = Connection([RuntimeError(SCAN_ERROR), rows([]), rows([])])
  result = v._read_empty_group_bounded(
    expected=_summary(code="A", period="1m", times=[]),
    start_ms=START_MS,
    end_exclusive_ms=START_MS + 2,
    connection=conn,
    page_rows=2000,
    budget=v.ReadbackBudget(),
  )
  assert result["row_count"] == 0
  assert len(conn.calls) == 3
  assert all(reader.closed for reader in conn.readers)


async def test_empty_source_capacity_failure_is_not_retried():
  async def keys():
    if False:
      yield

  conn = Connection([RuntimeError(SCAN_ERROR)])
  with pytest.raises(v.MarketDataPersistenceBlockedError):
    await v.verify_persisted_bar_summaries(
      code_summaries=[_summary(code="A", period="1m", times=[])],
      expected_key_batches=keys(),
      start_ms=START_MS,
      end_exclusive_ms=START_MS + 1,
      connection=conn,
    )
  assert len(conn.calls) == 1
