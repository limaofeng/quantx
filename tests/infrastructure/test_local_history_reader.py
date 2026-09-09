"""Local read boundaries, cursor integrity, and cancellation resource ownership."""

import asyncio
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pyarrow as pa
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from quantx_contracts.market_data_service import HistoryRead
from quantx_infrastructure.services.local_history_reader import (
  HistoryReadBusy,
  HistoryReadInvalid,
  LocalHistoryReader,
)
from quantx_market_data.api import create_app

STAMP = datetime(2026, 9, 9, 1, 30, tzinfo=timezone.utc)


def request(**kwargs):
  return HistoryRead(
    **dict(instrument="600000.SH", period="tick", trading_date="2026-09-09") | kwargs
  )


def tick(ordinal=0):
  return {
    "stock_code": "600000.SH",
    "period": "tick",
    "time": STAMP + timedelta(microseconds=ordinal),
    "source_time_ms": int(STAMP.timestamp() * 1000),
    "tick_ordinal": ordinal,
    "last_price": 10.0,
  }


class Connection:
  def __init__(self, pages):
    self.pages = iter(pages)
    self.calls = []
    self.closed = 0

  @contextmanager
  def get_client(self, *, timeout):
    assert 0 < timeout <= 10
    yield self

  def query(self, **kwargs):
    self.calls.append(kwargs)
    rows = next(self.pages)
    batches = [pa.RecordBatch.from_pylist(rows)] if rows else []
    connection = self

    class Reader:
      def __iter__(self):
        return iter(batches)

      def close(self):
        connection.closed += 1

    return Reader()


async def test_tick_cursor_preserves_same_millisecond_and_requires_empty_probe():
  connection = Connection([[tick(0)], [tick(1)], []])
  reader = LocalHistoryReader(connection)
  first = await reader.read(request(page_size=2))
  assert not first.exhausted
  second = await reader.read(request(page_size=2, after=first.next_after))
  assert second.records[0]["tick_ordinal"] == 1
  assert not second.exhausted
  final = await reader.read(request(page_size=2, after=second.next_after))
  assert final.exhausted and final.next_after is None
  sql = connection.calls[1]["query"]
  assert "time >= '2026-09-08T16:00:00+00:00'" in sql
  assert "time < '2026-09-09T16:00:00+00:00'" in sql
  assert "time > '2026-09-09T01:30:00.000000+00:00'" in sql
  assert sql.endswith("ORDER BY time ASC LIMIT 2")
  assert connection.calls[1]["query_parameters"] == {
    "stock_code": "600000.SH",
    "period": "tick",
  }
  assert all(0 < call["timeout"] <= 10 for call in connection.calls)
  assert connection.closed == 3


@pytest.mark.parametrize(
  "rows",
  [
    [tick(), tick()],
    [tick(1), tick()],
    [tick() | {"stock_code": "000001.SZ"}],
    [tick() | {"time": STAMP + timedelta(days=1)}],
    [tick() | {"tick_ordinal": 1}],
  ],
)
async def test_bad_source_never_returns_a_page(rows):
  connection = Connection([rows])
  with pytest.raises((HistoryReadInvalid, ValueError)):
    await LocalHistoryReader(connection).read(request())
  assert connection.closed == 1


async def test_backend_row_cap_violation_rejected_before_rows_converted():
  connection = Connection([[tick(), tick(1)]])
  with pytest.raises(HistoryReadInvalid, match="budget"):
    await LocalHistoryReader(connection).read(request(page_size=1))
  assert connection.closed == 1


async def test_backend_byte_budget_closes_reader_and_does_not_retry():
  connection = Connection([[tick() | {"unexpected_blob": "x" * (4 * 1024 * 1024)}]])
  with pytest.raises(HistoryReadInvalid, match="budget"):
    await LocalHistoryReader(connection).read(request())
  assert connection.closed == len(connection.calls) == 1


@pytest.mark.parametrize("period", ["1m", "1d"])
async def test_kline_reads_one_partition_without_aggregation(period):
  row = {"time": STAMP, "stock_code": "600000.SH", "period": period, "close": 10.0}
  connection = Connection([[row]])
  result = await LocalHistoryReader(connection).read(request(period=period))
  assert result.records == [row]
  assert f"FROM kline_{period}" in connection.calls[0]["query"]


@pytest.mark.parametrize(
  "kwargs",
  [
    {"after": "2026-09-08T15:59:59Z"},
    {"after": "2026-09-09T16:00:00Z"},
    {"after": "2026-09-09T01:30:00"},
    {"period": "5m"},
    {"page_size": 2001},
    {"instrument": "600000.SH' OR TRUE"},
    {"trading_date": "0001-01-01"},
    {"trading_date": "9999-12-31"},
  ],
)
def test_read_scope_rejected_before_storage(kwargs):
  with pytest.raises(ValidationError):
    request(**kwargs)


async def test_cancelled_read_keeps_capacity_until_sdk_thread_exits():
  started, release = threading.Event(), threading.Event()
  reader = LocalHistoryReader()

  def blocked(_):
    started.set()
    assert release.wait(5)

  reader._read = blocked
  task = asyncio.create_task(reader.read(request()))
  try:
    assert await asyncio.to_thread(started.wait, 3)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    with pytest.raises(HistoryReadBusy):
      await reader.read(request())
    assert not task.done()
  finally:
    release.set()
  with pytest.raises(asyncio.CancelledError):
    await task
  assert not reader._slot.locked()


@pytest.mark.parametrize("environment", ["testing", "production"])
async def test_non_development_default_reader_keeps_existing_storage(
  environment, monkeypatch
):
  from quantx_infrastructure.config.settings import settings

  monkeypatch.setattr(settings, "environment", environment)
  app = create_app(store=object(), token="secret")
  async with app.router.lifespan_context(app):
    assert type(app.state.reader) is LocalHistoryReader


async def test_http_read_is_authenticated_local_and_has_no_request_mutations():
  store = SimpleNamespace(create_market_data_request=AsyncMock())
  connection = Connection([[]])
  app = create_app(store=store, token="secret", reader=LocalHistoryReader(connection))
  async with app.router.lifespan_context(app):
    async with AsyncClient(
      transport=ASGITransport(app), base_url="http://test"
    ) as client:
      params = {
        "instrument": "600000.SH",
        "period": "tick",
        "trading_date": "2026-09-09",
      }
      endpoint = "/market-data/internal/v1/history"
      assert (await client.get(endpoint, params=params)).status_code == 401
      headers = {"Authorization": "Bearer secret"}
      result = await client.get(endpoint, params=params, headers=headers)
      assert result.status_code == 200, result.text
      assert result.json() == {"records": [], "next_after": None, "exhausted": True}
      invalid = await client.get(
        endpoint, params=params | {"page_size": 2001}, headers=headers
      )
      assert invalid.status_code == 422
      # Exhausted fake source raises StopIteration: the API must not leak it.
      failed = await client.get(endpoint, params=params, headers=headers)
      assert failed.status_code == 503
      assert failed.json()["detail"] == "HISTORY_READ_UNAVAILABLE"
  store.create_market_data_request.assert_not_called()
