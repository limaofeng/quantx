"""Actual Arrow field comparisons and bounded Flight query behavior."""
# ruff: noqa: F811

import asyncio
import re
import threading
from contextlib import contextmanager
from datetime import datetime

import pandas as pd
import pyarrow as pa
import pytest
from quantx_infrastructure.services import market_data_content_verification as content
from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_transfer_ingestion import (
  ManifestStore,
  _kline_row,
  _payload,
  _summary,
  _tick_row,
  _write_chunk,
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


async def stub_content_verifier(expected_batches):
  """Explicit content substitute for existing state-machine-only test fixtures."""
  count = sum([len(frame) async for frame in expected_batches])
  return {
    "schema_version": 1,
    "records_verified": count,
    "fields_verified": count,
    "source_sha256": "a" * 64,
    "persisted_sha256": "a" * 64,
  }


def normalized(rows):
  period, code = rows[0]["period"], rows[0]["code"]
  frame = pd.DataFrame(
    [{k: v for k, v in row.items() if k not in {"code", "period"}} for row in rows]
  )
  return ingestion.normalize_market_data_storage(period, {code: frame})


async def frames(*values):
  for value in values:
    yield value


class Connection:
  def __init__(self, rows, *, capacity=False, partial_capacity=False):
    self.rows, self.capacity, self.partial_capacity = rows, capacity, partial_capacity
    self.calls, self.closed = [], 0

  @contextmanager
  def get_client(self, *, timeout):
    assert 0 < timeout <= 10
    yield self

  def query(self, **kwargs):
    self.calls.append(kwargs)
    sql = kwargs["query"]
    start = datetime.fromisoformat(re.search(r"time >= '([^']+)'", sql)[1])
    end = datetime.fromisoformat(re.search(r"time <= '([^']+)'", sql)[1])
    after = re.search(r"time > '([^']+)'", sql)
    after = datetime.fromisoformat(after[1]) if after else None
    rows = [
      row
      for row in self.rows
      if start <= row["time"] <= end and (after is None or row["time"] > after)
    ]
    if self.capacity and len(rows) > 1:
      raise RuntimeError("Query would scan 432 Parquet files, exceeding the file limit")
    limit = int(re.search(r"LIMIT (\d+)", sql)[1])
    rows = rows[:limit]
    parent = self

    class Reader:
      def __iter__(self):
        if rows:
          yield pa.RecordBatch.from_pylist(
            rows[:1] if parent.partial_capacity and len(rows) > 1 else rows
          )
        if parent.partial_capacity and len(rows) > 1:
          raise RuntimeError(
            "Query would scan 432 Parquet files, exceeding the file limit"
          )

      def close(self):
        parent.closed += 1

    return Reader()


TICK_FIELDS = [
  "last_price",
  "open",
  "high",
  "low",
  "last_close",
  "amount",
  "volume",
  "pvolume",
  "tickvol",
  "stock_status",
  "open_int",
  "last_settlement_price",
  "settlement_price",
  "transaction_num",
  "price_tick",
  "source_time_ms",
  "tick_ordinal",
] + [
  f"{prefix}{i}" for prefix in ("ask", "bid", "ask_vol", "bid_vol") for i in range(1, 6)
]


@pytest.mark.parametrize("field", TICK_FIELDS)
async def test_every_owned_tick_field_detects_changed_value(field):
  expected = normalized([_tick_row()])
  rows = expected.to_dict("records")
  rows[0][field] += 0.125
  connection = Connection(rows)
  with pytest.raises(
    content.MarketDataPersistenceMismatchError, match="field mismatch"
  ):
    await content.verify_persisted_bar_content(frames(expected), connection=connection)
  assert connection.closed == 1


@pytest.mark.parametrize(
  "field",
  [
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "settelement_price",
    "open_interest",
    "suspend_flag",
    "up_stop_price",
    "down_stop_price",
  ],
)
async def test_daily_fields_and_limits_detect_changed_value(field):
  expected = normalized(
    [{**_kline_row(period="1d"), "upperLimit": 11.5, "lowerLimit": 9.5}]
  )
  rows = expected.to_dict("records")
  rows[0][field] += 1
  with pytest.raises(content.MarketDataPersistenceMismatchError):
    await content.verify_persisted_bar_content(
      frames(expected), connection=Connection(rows)
    )


@pytest.mark.parametrize("value", [None, float("nan"), True])
async def test_missing_nonfinite_and_boolean_values_are_not_numeric_matches(value):
  expected = normalized([_tick_row()])
  rows = expected.to_dict("records")
  rows[0]["last_price"] = value
  with pytest.raises(content.MarketDataPersistenceMismatchError):
    await content.verify_persisted_bar_content(
      frames(expected), connection=Connection(rows)
    )


async def test_extra_keys_paginate_and_source_and_persisted_hashes_match(monkeypatch):
  expected = normalized([_tick_row(ordinal=0), _tick_row(ordinal=2)])
  actual = normalized([_tick_row(ordinal=i) for i in range(3)]).to_dict("records")
  monkeypatch.setattr(content, "CONTENT_PAGE_ROWS", 1)
  connection = Connection(actual)
  result = await content.verify_persisted_bar_content(
    frames(expected), connection=connection
  )
  assert result["records_verified"] == 2 and result["fields_verified"] == 2 * len(
    TICK_FIELDS
  )
  assert result["source_sha256"] == result["persisted_sha256"]
  assert len(connection.calls) == connection.closed == 3
  assert all(
    c["query_parameters"] == {"stock_code": "600000.SH", "period": "tick"}
    for c in connection.calls
  )


@pytest.mark.parametrize("partial", [False, True])
async def test_capacity_splits_share_budget_without_hashing_failed_prefix_twice(
  partial,
):
  expected = normalized([_tick_row(ordinal=i) for i in range(4)])
  rows = expected.to_dict("records")
  normal = await content.verify_persisted_bar_content(
    frames(expected), connection=Connection(rows)
  )
  connection = Connection(rows, capacity=not partial, partial_capacity=partial)
  split = await content.verify_persisted_bar_content(
    frames(expected), connection=connection
  )
  assert split == normal
  assert len(connection.calls) == 7


async def test_shared_page_budget_is_not_reset_for_next_source_batch(monkeypatch):
  expected = normalized([_tick_row()])
  monkeypatch.setattr(content, "CONTENT_MAX_PAGES", 1)
  connection = Connection(expected.to_dict("records"))
  with pytest.raises(content.MarketDataPersistenceBlockedError, match="PAGE_BUDGET"):
    await content.verify_persisted_bar_content(
      frames(expected, expected), connection=connection
    )
  assert len(connection.calls) == 1


def test_writer_uses_same_scalar_and_book_normalization(monkeypatch):
  captured = []

  class Service:
    def bulk_save_ticks(self, frame):
      captured.append(frame)
      return len(frame)

  monkeypatch.setattr(ingestion, "HistoricalMarketDataService", Service)
  row = {
    **_tick_row(),
    "lastPrice": 10.12349,
    "volume": 123.456,
    "askPrice": [10.12349] * 5,
  }
  ingestion._save_market_data_period_sync(
    period="tick", market_data={"600000.SH": pd.DataFrame([row])}
  )
  stored = captured[0].iloc[0]
  assert stored["last_price"] == 10.123
  assert stored["volume"] == 123.46
  assert stored["ask1"] == 10.12349
  assert "ask_price" not in captured[0]


async def test_repeated_cancellation_joins_actual_reader_thread(monkeypatch):
  expected = normalized([_tick_row()])
  entered, release = threading.Event(), threading.Event()
  connection = Connection(expected.to_dict("records"))
  query = connection.query

  def blocked(**kwargs):
    entered.set()
    assert release.wait(5)
    return query(**kwargs)

  connection.query = blocked
  task = asyncio.create_task(
    content.verify_persisted_bar_content(frames(expected), connection=connection)
  )
  try:
    assert await asyncio.to_thread(entered.wait, 2)
    for _ in range(3):
      task.cancel()
      await asyncio.sleep(0)
      assert not task.done()
  finally:
    release.set()
    with pytest.raises(asyncio.CancelledError):
      await asyncio.wait_for(task, 2)
  assert connection.closed == 1


@pytest.mark.parametrize("period", ["tick", "1m", "1d"])
@pytest.mark.parametrize("changed", [False, True])
async def test_ingestion_and_recovery_require_content_even_when_key_proof_passes(
  tmp_path, monkeypatch, period, changed
):
  row = _tick_row() if period == "tick" else _kline_row(period=period)
  if period == "1d":
    row.update(upperLimit=11.5, lowerLimit=9.5)
  store = ManifestStore(
    payload=_payload(periods=[period]),
    manifest=[_write_chunk(tmp_path, [row, _summary([row], period=period)])],
  )
  actual = normalized([row]).to_dict("records")
  if changed:
    actual[0]["last_price" if period == "tick" else "close"] += 1
  connection = Connection(actual)

  async def verify_fields(batches):
    return await content.verify_persisted_bar_content(batches, connection=connection)

  monkeypatch.setattr(ingestion, "verify_persisted_bar_content", verify_fields)

  async def keys(**kwargs):
    assert len([b async for b in kwargs["expected_key_batches"]]) == 1
    summaries = [
      {
        k: item[k]
        for k in ("code", "period", "row_count", "min_time", "max_time", "key_sha256")
      }
      for item in kwargs["code_summaries"]
    ]
    return {
      "status": "verified",
      "records_verified": 1,
      "groups_verified": 1,
      "code_summaries": summaries,
    }

  async def save(**kwargs):
    return {"status": "success", "saved_count": 1}

  for operation in (
    lambda: ingestion.ingest_uploaded_bar_request(
      store, "request-1", save_period=save, verify_persistence=keys
    ),
    lambda: ingestion.verify_uploaded_bar_request(
      store, "request-1", verify_persistence=keys
    ),
  ):
    if changed:
      with pytest.raises(content.MarketDataPersistenceMismatchError):
        await operation()
    else:
      result = await operation()
      assert result["content_verification"]["records_verified"] == 1
      assert result["content_verification"]["fields_verified"] > 0


@pytest.mark.parametrize(
  "failure,reason",
  [
    (
      "Query would scan 432 Parquet files, exceeding the file limit",
      "DEPENDENCY_QUERY_CAPACITY_BLOCKED",
    ),
    ("unauthorized token=do-not-expose", "DEPENDENCY_AUTH_BLOCKED"),
  ],
)
async def test_single_key_capacity_and_auth_are_permanent_shared_blocks(
  failure, reason
):
  expected = normalized([_tick_row()])
  connection = Connection([])

  def fail(**kwargs):
    raise RuntimeError(failure)

  connection.query = fail
  with pytest.raises(content.MarketDataPersistenceBlockedError) as error:
    await content.verify_persisted_bar_content(frames(expected), connection=connection)
  assert error.value.reason_code == reason
  assert "token" not in str(error.value)


async def test_result_byte_limit_closes_reader(monkeypatch):
  expected = normalized([_tick_row()])
  connection = Connection(expected.to_dict("records"))
  monkeypatch.setattr(content, "CONTENT_PAGE_BYTES", 1)
  with pytest.raises(content.MarketDataPersistenceBlockedError, match="RESULT_BUDGET"):
    await content.verify_persisted_bar_content(frames(expected), connection=connection)
  assert connection.closed == 1


@pytest.mark.parametrize("changed", [False, True])
async def test_worker_completion_is_gated_by_actual_field_readback(
  workers, tmp_path, monkeypatch, changed
):
  (store, _), _ = workers
  row = _tick_row()
  store.manifest = [_write_chunk(tmp_path, [row, _summary([row])])]
  actual = normalized([row]).to_dict("records")
  if changed:
    actual[0]["last_price"] += 1
  connection = Connection(actual)

  async def verify_fields(batches):
    return await content.verify_persisted_bar_content(batches, connection=connection)

  monkeypatch.setattr(ingestion, "verify_persisted_bar_content", verify_fields)

  async def keys(**kwargs):
    summaries = [
      {
        k: item[k]
        for k in ("code", "period", "row_count", "min_time", "max_time", "key_sha256")
      }
      for item in kwargs["code_summaries"]
    ]
    return {
      "status": "verified",
      "records_verified": 1,
      "groups_verified": 1,
      "code_summaries": summaries,
    }

  async def save(**kwargs):
    return {"status": "success", "saved_count": 1}

  async def ingest(st, request_id, *, progress):
    return await ingestion.ingest_uploaded_bar_request(
      st, request_id, progress=progress, save_period=save, verify_persistence=keys
    )

  assert await store.acquire()
  result = await ingestion.claim_ingest_and_finish_market_data_request(
    store, "request-1", ingest_request=ingest
  )
  saved = await store.market_data_request("request-1")
  if changed:
    assert result["status"] == "retryable"
    assert result["reason"] == "PERSISTED_DATA_NOT_VISIBLE"
    assert saved["status"] != "COMPLETED"
    assert saved["ingestion_progress"]["phase"] == "READBACK"
  else:
    assert saved["status"] == "COMPLETED"
    assert saved["ingestion_progress"]["phase"] == "VERIFIED"
    assert saved["ingestion_result"]["content_verification"]["records_verified"] == 1
