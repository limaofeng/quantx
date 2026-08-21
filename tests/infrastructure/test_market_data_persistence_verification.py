import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pytest
from quantx_contracts import historical_bar_key
from quantx_infrastructure.services import (
  market_data_persistence_verification as verification,
)

START_MS = 1_699_977_600_000
END_EXCLUSIVE_MS = START_MS + 24 * 60 * 60 * 1000


def _digest(*keys: str) -> str:
  return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


def _summary(
  *,
  code: str,
  period: str,
  times: list[int],
  ordinals: list[int] | None = None,
) -> dict:
  keys = [
    historical_bar_key(
      code=code,
      period=period,
      time_ms=time_ms,
      tick_ordinal=(ordinals[index] if ordinals is not None else None),
    )
    for index, time_ms in enumerate(times)
  ]
  return {
    "code": code,
    "period": period,
    "row_count": len(times),
    "min_time": times[0] if times else None,
    "max_time": times[-1] if times else None,
    "key_sha256": _digest(*keys),
  }


def _tick_reader(rows: list[tuple[int, int]], *, batch_rows: int = 1024):
  times = [
    datetime.fromtimestamp(source_ms / 1000, timezone.utc)
    + timedelta(microseconds=ordinal)
    for source_ms, ordinal in rows
  ]
  table = pa.table(
    {
      "time": pa.array(times, type=pa.timestamp("us", tz="UTC")),
      "source_time_ms": pa.array(
        [source_ms for source_ms, _ in rows], type=pa.int64()
      ),
      "tick_ordinal": pa.array([ordinal for _, ordinal in rows], type=pa.int64()),
    }
  )
  return pa.RecordBatchReader.from_batches(
    table.schema,
    table.to_batches(max_chunksize=batch_rows),
  )


def _kline_reader(times_ms: list[int]):
  values = [datetime.fromtimestamp(value / 1000, timezone.utc) for value in times_ms]
  table = pa.table(
    {"time": pa.array(values, type=pa.timestamp("ms", tz="UTC"))}
  )
  return pa.RecordBatchReader.from_batches(table.schema, table.to_batches())


def _malformed_tick_reader(source_ms: int):
  storage_time = datetime.fromtimestamp(source_ms / 1000, timezone.utc)
  table = pa.table(
    {
      "time": pa.array([storage_time], type=pa.timestamp("us", tz="UTC")),
      "source_time_ms": pa.array([str(source_ms)], type=pa.string()),
      "tick_ordinal": pa.array([0], type=pa.int64()),
    }
  )
  return pa.RecordBatchReader.from_batches(table.schema, table.to_batches())


class FakeClient:
  def __init__(self, responses):
    self.responses = list(responses)
    self.calls: list[dict] = []

  def query(self, query, language="sql", mode="all", **kwargs):
    self.calls.append(
      {
        "query": query,
        "language": language,
        "mode": mode,
        **kwargs,
      }
    )
    if not self.responses:
      raise AssertionError("unexpected Influx query")
    response = self.responses.pop(0)
    if isinstance(response, Exception):
      raise response
    return response()


class FakeConnection:
  def __init__(self, responses):
    self.client = FakeClient(responses)
    self.acquisitions = 0

  @contextmanager
  def get_client(self):
    self.acquisitions += 1
    yield self.client


@pytest.mark.asyncio
async def test_tick_readback_uses_parameterized_bounded_arrow_pages() -> None:
  code = "600000.SH"
  source_ms = START_MS + 34_200_123
  rows = [
    (source_ms, 0),
    (source_ms, 1),
    (source_ms + 1, 0),
    (source_ms + 2, 0),
  ]
  expected = _summary(
    code=code,
    period="tick",
    times=[item[0] for item in rows],
    ordinals=[item[1] for item in rows],
  )
  connection = FakeConnection(
    [
      lambda: _tick_reader(rows[:2], batch_rows=1),
      lambda: _tick_reader(rows[2:], batch_rows=1),
      lambda: _tick_reader([]),
    ]
  )

  result = await verification.verify_persisted_bar_summaries(
    code_summaries=[expected],
    start_ms=START_MS,
    end_exclusive_ms=END_EXCLUSIVE_MS,
    connection=connection,
    max_attempts=1,
    retry_delays=(),
    page_rows=2,
  )

  assert result["records_verified"] == 4
  assert result["code_summaries"] == [expected]
  assert result["attempts_by_group"] == {f"{code}/tick": 1}
  assert len(connection.client.calls) == 3
  first, second, last = connection.client.calls
  assert first["mode"] == "reader"
  assert first["language"] == "sql"
  assert first["query_parameters"] == {"stock_code": code, "period": "tick"}
  assert "FROM ticks" in first["query"]
  assert "stock_code = $stock_code" in first["query"]
  assert "period = $period" in first["query"]
  assert "ORDER BY time ASC LIMIT 2" in first["query"]
  assert "AND time > '" not in first["query"]
  assert "AND time > '" in second["query"]
  assert "LIMIT 1" in last["query"]


@pytest.mark.asyncio
async def test_kline_readback_recomputes_canonical_key_digest() -> None:
  times = [START_MS, START_MS + 60_000]
  expected = _summary(code="000001.SZ", period="1m", times=times)
  connection = FakeConnection([lambda: _kline_reader(times)])

  result = await verification.verify_persisted_bar_summaries(
    code_summaries=[expected],
    start_ms=START_MS,
    end_exclusive_ms=END_EXCLUSIVE_MS,
    connection=connection,
    max_attempts=1,
    retry_delays=(),
  )

  assert result["code_summaries"] == [expected]
  assert "FROM kline_1m" in connection.client.calls[0]["query"]


@pytest.mark.asyncio
async def test_legal_empty_summary_queries_influx_and_confirms_no_point() -> None:
  expected = _summary(code="600000.SH", period="tick", times=[], ordinals=[])
  connection = FakeConnection([lambda: _tick_reader([])])

  result = await verification.verify_persisted_bar_summaries(
    code_summaries=[expected],
    start_ms=START_MS,
    end_exclusive_ms=END_EXCLUSIVE_MS,
    connection=connection,
    max_attempts=1,
    retry_delays=(),
  )

  assert result["records_verified"] == 0
  assert len(connection.client.calls) == 1
  assert "LIMIT 1" in connection.client.calls[0]["query"]


@pytest.mark.asyncio
async def test_visibility_mismatch_is_retried_then_can_converge() -> None:
  source_ms = START_MS + 34_200_123
  expected = _summary(
    code="600000.SH",
    period="tick",
    times=[source_ms],
    ordinals=[0],
  )
  connection = FakeConnection(
    [lambda: _tick_reader([]), lambda: _tick_reader([(source_ms, 0)])]
  )

  result = await verification.verify_persisted_bar_summaries(
    code_summaries=[expected],
    start_ms=START_MS,
    end_exclusive_ms=END_EXCLUSIVE_MS,
    connection=connection,
    max_attempts=2,
    retry_delays=(0,),
  )

  assert result["attempts_by_group"] == {"600000.SH/tick": 2}
  assert len(connection.client.calls) == 2


@pytest.mark.asyncio
async def test_persistent_mismatch_is_retryable_and_bounded() -> None:
  source_ms = START_MS + 34_200_123
  expected = _summary(
    code="600000.SH",
    period="tick",
    times=[source_ms],
    ordinals=[0],
  )
  connection = FakeConnection([lambda: _tick_reader([]), lambda: _tick_reader([])])

  with pytest.raises(verification.MarketDataPersistenceMismatchError):
    await verification.verify_persisted_bar_summaries(
      code_summaries=[expected],
      start_ms=START_MS,
      end_exclusive_ms=END_EXCLUSIVE_MS,
      connection=connection,
      max_attempts=2,
      retry_delays=(0,),
    )

  assert len(connection.client.calls) == 2


@pytest.mark.asyncio
async def test_same_bounds_but_different_tick_keys_fail_the_digest_audit() -> None:
  source_ms = START_MS + 34_200_123
  expected = _summary(
    code="600000.SH",
    period="tick",
    times=[source_ms, source_ms],
    ordinals=[0, 1],
  )
  connection = FakeConnection(
    [lambda: _tick_reader([(source_ms, 0), (source_ms, 2)])]
  )

  with pytest.raises(
    verification.MarketDataPersistenceMismatchError,
    match="does not match Agent summary",
  ):
    await verification.verify_persisted_bar_summaries(
      code_summaries=[expected],
      start_ms=START_MS,
      end_exclusive_ms=END_EXCLUSIVE_MS,
      connection=connection,
      max_attempts=1,
      retry_delays=(),
    )


@pytest.mark.asyncio
async def test_query_failure_is_never_treated_as_an_empty_result() -> None:
  expected = _summary(code="600000.SH", period="tick", times=[], ordinals=[])
  connection = FakeConnection([RuntimeError("flight unavailable")] * 2)

  with pytest.raises(verification.MarketDataPersistenceQueryError, match="query failed"):
    await verification.verify_persisted_bar_summaries(
      code_summaries=[expected],
      start_ms=START_MS,
      end_exclusive_ms=END_EXCLUSIVE_MS,
      connection=connection,
      max_attempts=2,
      retry_delays=(0,),
    )

  assert len(connection.client.calls) == 2


@pytest.mark.asyncio
async def test_one_extra_persisted_key_is_enough_to_reject_the_group() -> None:
  expected_time = START_MS
  expected = _summary(code="600000.SH", period="1d", times=[expected_time])
  connection = FakeConnection(
    [lambda: _kline_reader([expected_time, expected_time + 60_000])]
  )

  with pytest.raises(verification.MarketDataPersistenceMismatchError):
    await verification.verify_persisted_bar_summaries(
      code_summaries=[expected],
      start_ms=START_MS,
      end_exclusive_ms=END_EXCLUSIVE_MS,
      connection=connection,
      max_attempts=1,
      retry_delays=(),
      page_rows=2,
    )

  assert "LIMIT 2" in connection.client.calls[0]["query"]


@pytest.mark.asyncio
async def test_missing_reader_schema_is_a_query_failure_not_an_empty_group() -> None:
  expected = _summary(code="600000.SH", period="tick", times=[], ordinals=[])
  connection = FakeConnection([lambda: _kline_reader([])])

  with pytest.raises(verification.MarketDataPersistenceQueryError, match="missing required"):
    await verification.verify_persisted_bar_summaries(
      code_summaries=[expected],
      start_ms=START_MS,
      end_exclusive_ms=END_EXCLUSIVE_MS,
      connection=connection,
      max_attempts=1,
      retry_delays=(),
    )


@pytest.mark.asyncio
async def test_invalid_reader_field_type_is_a_query_failure() -> None:
  source_ms = START_MS + 34_200_123
  expected = _summary(
    code="600000.SH",
    period="tick",
    times=[source_ms],
    ordinals=[0],
  )
  connection = FakeConnection([lambda: _malformed_tick_reader(source_ms)])

  with pytest.raises(
    verification.MarketDataPersistenceQueryError,
    match="non-integer source_time_ms",
  ):
    await verification.verify_persisted_bar_summaries(
      code_summaries=[expected],
      start_ms=START_MS,
      end_exclusive_ms=END_EXCLUSIVE_MS,
      connection=connection,
      max_attempts=1,
      retry_delays=(),
    )
