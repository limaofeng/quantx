"""Historical computation reads through HTTP, preserving Tick source identity."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from quantx_infrastructure.services import local_historical_tick_reader as module
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalTickPaginationError,
)
from quantx_infrastructure.services.local_history_reader import LocalHistoryReader
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)
from quantx_market_data.api import create_app

from tests.infrastructure.test_local_history_reader import STAMP, Connection, tick


def stream(**kwargs):
  return module.LocalHistoricalTickReader().iter_tick_pages(
    **{
      "stock_code": "600000.SH",
      "start_time": STAMP,
      "end_time": STAMP + timedelta(seconds=1),
      "page_size": 2,
      "max_pages": 3,
      "max_source_ticks": 10,
    }
    | kwargs
  )


async def collect(pages):
  return [page async for page in pages]


async def test_profile_reader_http_roundtrip_keeps_short_pages_and_depth(monkeypatch):
  from quantx_worker.prefector.flows.t_trade_instrument_profile_flow import (
    _iter_profile_tick_pages,
  )

  connection = Connection(
    [[tick(0) | {"ask1": 10.1, "ask2": 10.2, "bid1": 10.0}], [tick(1)], []]
  )
  app = create_app(
    store=object(), token="internal", reader=LocalHistoryReader(connection)
  )
  async with app.router.lifespan_context(app):
    client = LocalMarketDataClient(token="internal", transport=httpx.ASGITransport(app))
    monkeypatch.setattr(module, "LocalMarketDataClient", lambda: client)
    # The existing Flow window helper provides naive Shanghai bounds.
    pages = await collect(
      _iter_profile_tick_pages(
        service=module.LocalHistoricalTickReader(),
        stock_code="600000.SH",
        start_time=STAMP.replace(tzinfo=None) + timedelta(hours=8),
        end_time=STAMP.replace(tzinfo=None) + timedelta(hours=8, seconds=1),
        page_size=2,
        max_pages=3,
        max_source_ticks=10,
      )
    )
    assert len(pages) == 1
    assert [value.tick_ordinal for value in pages[0]] == [0, 1]
    assert pages[0][0].ask_price == [10.1, 10.2]
    assert pages[0][0].bid_price == [10.0]
    assert pages[0][1].time == STAMP + timedelta(microseconds=1)
    assert client.client.is_closed
  assert connection.closed == len(connection.calls) == 3


async def test_wire_pages_reassemble_without_resetting_output_budget(monkeypatch):
  rows = []
  for index in range(2001):
    value = tick(index % 1000)
    value["source_time_ms"] += index // 1000
    value["time"] += timedelta(milliseconds=index // 1000)
    rows.append(value)
  connection = Connection([rows[:1000], rows[1000:2000], rows[2000:], []])
  app = create_app(
    store=object(), token="internal", reader=LocalHistoryReader(connection)
  )
  async with app.router.lifespan_context(app):
    client = LocalMarketDataClient(token="internal", transport=httpx.ASGITransport(app))
    monkeypatch.setattr(module, "LocalMarketDataClient", lambda: client)
    pages = await collect(stream(page_size=2001, max_pages=1, max_source_ticks=2001))
    assert len(pages) == 1 and len(pages[0]) == 2001
    assert pages[0][-1].source_time_ms == int(STAMP.timestamp() * 1000) + 2
    assert pages[0][-1].tick_ordinal == 0
    assert client.client.is_closed
  assert connection.closed == len(connection.calls) == 4
  assert all(call["query"].endswith("LIMIT 1000") for call in connection.calls)


@pytest.mark.parametrize(
  "case", ["full_final", "extra_row", "source_cap", "outside_end"]
)
async def test_original_budget_and_final_probe(monkeypatch, case):
  extra = [] if case == "full_final" else [tick(2)]
  if case == "outside_end":
    extra = [
      tick(0)
      | {
        "time": STAMP + timedelta(milliseconds=1),
        "source_time_ms": int(STAMP.timestamp() * 1000) + 1,
      }
    ]
  connection = Connection([[tick(0), tick(1)], extra])
  app = create_app(
    store=object(), token="internal", reader=LocalHistoryReader(connection)
  )
  async with app.router.lifespan_context(app):
    client = LocalMarketDataClient(token="internal", transport=httpx.ASGITransport(app))
    monkeypatch.setattr(module, "LocalMarketDataClient", lambda: client)
    pages = stream(
      max_pages=1,
      max_source_ticks=1 if case == "source_cap" else 10,
      end_time=STAMP if case == "outside_end" else STAMP + timedelta(seconds=1),
    )
    if case in {"extra_row", "source_cap"}:
      with pytest.raises(HistoricalTickPaginationError, match="budget"):
        await collect(pages)
    else:
      result = await collect(pages)
      assert [value.tick_ordinal for value in result[0]] == [0, 1]
    assert client.client.is_closed


@pytest.mark.parametrize("change", ["identity", "cursor"])
async def test_bad_http_content_is_integrity_failure_not_insufficient_history(
  monkeypatch, change
):
  value = tick(0)
  if change == "identity":
    value["tick_ordinal"] = 1
  value["time"] = value["time"].isoformat()

  async def response(request):
    return httpx.Response(
      200,
      json={
        "records": [value],
        "next_after": None if change == "cursor" else value["time"],
        "exhausted": False,
      },
    )

  client = LocalMarketDataClient(
    token="internal", transport=httpx.MockTransport(response)
  )
  monkeypatch.setattr(module, "LocalMarketDataClient", lambda: client)
  with pytest.raises(HistoricalTickPaginationError):
    await collect(stream())
  assert client.client.is_closed


@pytest.mark.parametrize("profile_wrapper", [False, True])
async def test_consumer_close_releases_http_client(monkeypatch, profile_wrapper):
  connection = Connection([[tick(0), tick(1)]])
  app = create_app(
    store=object(), token="internal", reader=LocalHistoryReader(connection)
  )
  async with app.router.lifespan_context(app):
    client = LocalMarketDataClient(token="internal", transport=httpx.ASGITransport(app))
    monkeypatch.setattr(module, "LocalMarketDataClient", lambda: client)
    pages = stream()
    if profile_wrapper:
      from quantx_worker.prefector.flows.t_trade_instrument_profile_flow import (
        _iter_profile_tick_pages,
      )

      pages = _iter_profile_tick_pages(
        service=module.LocalHistoricalTickReader(),
        stock_code="600000.SH",
        start_time=STAMP.replace(tzinfo=None) + timedelta(hours=8),
        end_time=STAMP.replace(tzinfo=None) + timedelta(hours=8, seconds=1),
        page_size=2,
        max_pages=3,
        max_source_ticks=10,
      )
    assert len(await anext(pages)) == 2
    await pages.aclose()
    assert client.client.is_closed and len(connection.calls) == 1


async def test_cancelled_http_read_closes_client(monkeypatch):
  started = asyncio.Event()

  async def wait(request):
    started.set()
    await asyncio.Event().wait()

  client = LocalMarketDataClient(token="internal", transport=httpx.MockTransport(wait))
  monkeypatch.setattr(module, "LocalMarketDataClient", lambda: client)
  task = asyncio.create_task(collect(stream()))
  await asyncio.wait_for(started.wait(), 1)
  task.cancel()
  with pytest.raises(asyncio.CancelledError):
    await task
  assert client.client.is_closed
