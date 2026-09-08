import asyncio
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import pyarrow as pa
import pytest
from quantx_infrastructure.services import market_data_persistence_verification as v

from tests.infrastructure.test_market_data_persistence_verification import (
  END_EXCLUSIVE_MS,
  START_MS,
  _summary,
)


def batch(code, offsets=(0, 2), period="1m"):
  return v.ExpectedBarKeyBatch(
    code, period, tuple((START_MS + t * 1000, None) for t in offsets)
  )


def rows(values):
  return pa.table(
    {
      "stock_code": [c for c, t in values],
      "time": pa.array(
        [
          datetime.fromtimestamp((START_MS + t * 1000) / 1000, timezone.utc)
          for c, t in values
        ],
        type=pa.timestamp("ms", tz="UTC"),
      ),
    }
  )


class Reader:
  def __init__(self, table):
    self.table = table
    self.schema = table.schema
    self.closed = False

  def __iter__(self):
    return iter(self.table.to_batches())

  def close(self):
    self.closed = True


class Connection:
  def __init__(self, pages):
    self.pages = list(pages)
    self.calls = []
    self.readers = []

  @contextmanager
  def get_client(self):
    yield self

  def query(self, **kwargs):
    self.calls.append(kwargs)
    page = self.pages.pop(0)
    if isinstance(page, Exception):
      raise page
    if page is None:
      return None
    reader = Reader(page)
    self.readers.append(reader)
    return reader


def test_grouped_pages_keep_individual_ranges_and_extra_rows():
  conn = Connection(
    [rows([("A", 0), ("A", 1)]), rows([("A", 2), ("B", 1)]), rows([("B", 3)])]
  )
  result = v._read_expected_key_group_once(
    batches=[batch("A"), batch("B", (1, 3))], connection=conn, page_rows=2
  )
  assert result == {"records_verified": 4, "existing_rows_observed": 1}
  assert len(conn.calls) == 3 and all(r.closed for r in conn.readers)
  assert conn.calls[1]["query_parameters"]["after_code"] == "A"
  assert conn.calls[2]["query_parameters"]["after_code"] == "B"
  assert all("LIMIT 2" in c["query"] and "OFFSET" not in c["query"] for c in conn.calls)
  assert "$code_0" in conn.calls[0]["query"] and "$code_1" in conn.calls[0]["query"]


@pytest.mark.parametrize(
  "page,error",
  [
    (rows([("A", 0), ("A", 0)]), "did not advance"),
    (rows([("C", 0)]), "unrequested code"),
    (rows([("A", 9)]), "escaped key bounds"),
    (rows([("A", 2)]), "missing an uploaded key"),
    (rows([("A", 0), ("A", 2), ("B", 1), ("B", 3)]), "exceeded page bound"),
    (
      pa.table({"time": pa.array([], type=pa.timestamp("ms", tz="UTC"))}),
      "missing required columns",
    ),
    (None, "no reader"),
    (TimeoutError(), "query failed"),
  ],
)
def test_group_read_fail_closed(page, error):
  conn = Connection([page])
  with pytest.raises(v.MarketDataPersistenceVerificationError, match=error):
    v._read_expected_key_group_once(
      batches=[batch("A"), batch("B", (1, 3))], connection=conn, page_rows=2
    )
  assert all(r.closed for r in conn.readers)


@pytest.mark.parametrize(
  "batches",
  [
    [],
    [batch("A"), batch("A")],
    [batch("A"), batch("B", period="1d")],
    [batch(str(i)) for i in range(21)],
    [batch("A", range(2001))],
    [batch(str(i), range(2000)) for i in range(6)],
    [batch("A", (2, 1))],
    [v.ExpectedBarKeyBatch("A", "1m", ((True, None),))],
    [v.ExpectedBarKeyBatch("A", "1m", ((START_MS, 0),))],
  ],
)
def test_group_limits_and_invalid_keys(batches):
  with pytest.raises(ValueError):
    v._read_expected_key_group_once(
      batches=batches, connection=Connection([]), page_rows=2000
    )


async def test_group_retry_does_not_duplicate_counts():
  conn = Connection(
    [
      rows([("A", 0), ("A", 1), ("B", 1), ("B", 3)]),
      rows([("A", 0), ("A", 1), ("A", 2), ("B", 1), ("B", 3)]),
    ]
  )
  groups = [batch("A"), batch("B", (1, 3))]

  async def keys():
    for item in groups:
      yield item

  sleeps = []

  async def sleep(delay):
    sleeps.append(delay)

  result = await v.verify_persisted_bar_summaries(
    code_summaries=[
      _summary(code=b.code, period=b.period, times=[k[0] for k in b.keys])
      for b in groups
    ],
    expected_key_batches=keys(),
    start_ms=START_MS,
    end_exclusive_ms=END_EXCLUSIVE_MS,
    connection=conn,
    max_attempts=2,
    retry_delays=(0.1,),
    sleep=sleep,
  )
  assert result["records_verified"] == 4 and result["existing_rows_observed"] == 1
  assert result["attempts_by_group"] == {"A/1m": 2, "B/1m": 2}
  assert sleeps == [0.1]


async def test_cancel_closes_reader_and_prevents_next_page():
  entered = threading.Event()
  release = threading.Event()
  conn = Connection([rows([("A", 0)])])
  original = conn.query

  def blocking(**kwargs):
    entered.set()
    assert release.wait(2)
    return original(**kwargs)

  conn.query = blocking
  task = asyncio.create_task(
    v._await_readback(
      v._read_expected_key_group_once,
      batches=[batch("A"), batch("B")],
      connection=conn,
      page_rows=1,
    )
  )
  assert await asyncio.to_thread(entered.wait, 2)
  task.cancel()
  release.set()
  with pytest.raises(asyncio.CancelledError):
    await task
  assert len(conn.calls) == 1 and all(r.closed for r in conn.readers)


def test_full_group_and_expected_key_bounds_are_accepted():
  groups = [batch(f"A{i:02}", range(500)) for i in range(20)]
  values = [(b.code, i) for b in groups for i in range(500)]
  conn = Connection([rows(values[n : n + 2000]) for n in range(0, len(values), 2000)])
  assert (
    v._read_expected_key_group_once(batches=groups, connection=conn, page_rows=2000)[
      "records_verified"
    ]
    == 10000
  )
  assert len(conn.calls) == 5


async def test_ingestion_claim_released_only_after_cancelled_reader_closes():
  from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion

  from tests.infrastructure.test_market_data_transfer_ingestion import (
    AtomicRequestStore,
  )

  entered = threading.Event()
  release = threading.Event()
  conn = Connection([rows([("A", 0)])])
  original = conn.query

  def blocking(**kwargs):
    entered.set()
    assert release.wait(2)
    return original(**kwargs)

  conn.query = blocking
  store = AtomicRequestStore()
  original_release = store.release_market_data_request_claim

  async def release_claim(*args, **kwargs):
    assert conn.readers and all(r.closed for r in conn.readers)
    return await original_release(*args, **kwargs)

  store.release_market_data_request_claim = release_claim

  async def ingest(_store, _request):
    return await v._await_readback(
      v._read_expected_key_group_once,
      batches=[batch("A"), batch("B")],
      connection=conn,
      page_rows=1,
    )

  task = asyncio.create_task(
    ingestion.claim_ingest_and_finish_market_data_request(
      store, "request-1", ingest_request=ingest
    )
  )
  assert await asyncio.to_thread(entered.wait, 2)
  task.cancel()
  release.set()
  with pytest.raises(asyncio.CancelledError):
    await task
  assert store.status == "UPLOADED" and store.release_count == 1
  assert "COMPLETED" not in store.transitions
