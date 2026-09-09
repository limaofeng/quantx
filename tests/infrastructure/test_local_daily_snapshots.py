"""Batch daily snapshots through the HTTP boundary and bounded Flight reader."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from quantx_contracts.daily_snapshot_read import DailySnapshotRead
from quantx_infrastructure.services.local_history_reader import (
  HistoryReadInvalid,
  LocalHistoryReader,
)
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)
from quantx_market_data.api import create_app

from tests.infrastructure.test_local_history_reader import Connection

STAMP = datetime(2026, 9, 9, tzinfo=timezone.utc)


def request(**kwargs):
  return DailySnapshotRead(
    **{
      "instruments": ["600000.SH", "000001.SZ", "399001.SZ"],
      "start": STAMP - timedelta(days=45),
      "end": STAMP,
    }
    | kwargs
  )


def row(code="000001.SZ", stamp=STAMP, close=10):
  return {
    "stock_code": code,
    "period": "1d",
    "time": stamp,
    "open": close,
    "high": close,
    "low": close,
    "close": close,
    "pre_close": 9.9,
    "volume": 100,
    "amount": 1000,
  }


async def test_api_facade_roundtrip_batches_once_without_historical_repository(
  monkeypatch,
):
  from quantx_api import market_data_read_service as module

  connection = Connection(
    [
      [
        row(),
        row("600000.SH", STAMP - timedelta(days=1), 11),
        row("600000.SH", close=12),
      ]
    ]
  )
  reader = LocalHistoryReader(connection)
  app = create_app(store=object(), token="internal", reader=reader)
  async with app.router.lifespan_context(app):
    client = LocalMarketDataClient(token="internal", transport=httpx.ASGITransport(app))
    monkeypatch.setattr(module, "LocalMarketDataClient", lambda: client)
    monkeypatch.setattr(module.time_utils, "now", lambda: STAMP)
    ticks = AsyncMock(return_value=[])
    monkeypatch.setattr(module.latest_market_quote_cache, "get_ticks", ticks)
    service = module.ApiMarketDataReadService.__new__(module.ApiMarketDataReadService)
    # No historical service or repository exists on this instance.
    values = await service.get_market_index_snapshots(
      ["600000.sh", "000001.SZ", "399001.SZ", "600000.SH"]
    )
    assert [item[0] for item in values] == request().instruments
    assert values[0][2].close == 12 and values[1][2].close == 10
    assert values[2] == ("399001.SZ", None, None)
    ticks.assert_awaited_once_with(request().instruments)
    assert client.client.is_closed
  assert len(connection.calls) == connection.closed == 1
  call = connection.calls[0]
  assert "time >= '2026-07-26T00:00:00+00:00'" in call["query"]
  assert "time <= '2026-09-09T00:00:00+00:00'" in call["query"]
  assert "stock_code IN ($code_0, $code_1, $code_2)" in call["query"]
  assert call["query"].endswith("LIMIT 187")
  assert call["query_parameters"] == {
    "code_0": "600000.SH",
    "code_1": "000001.SZ",
    "code_2": "399001.SZ",
  }


@pytest.mark.parametrize(
  "rows",
  [
    [row(), row()],
    [row(stamp=STAMP - timedelta(hours=1)), row()],
    [row("600000.SH"), row()],
    [row("999999.SH")],
    [row(stamp=STAMP + timedelta(seconds=1))],
    [row(stamp=STAMP - timedelta(days=46))],
    [row() | {"period": "1m"}],
    [row() | {"close": float("nan")}],
    [row() | {"close": None}],
  ],
)
async def test_invalid_storage_never_returns_partial_snapshot(rows):
  connection = Connection([rows])
  with pytest.raises((ValueError, HistoryReadInvalid)):
    await LocalHistoryReader(connection).read_latest_daily(request())
  assert connection.closed == len(connection.calls) == 1


@pytest.mark.parametrize(
  "rows",
  [
    [row()] * 187,
    [row() | {"blob": "x" * (2 * 1024 * 1024)}],
  ],
)
async def test_storage_budget_closes_reader_before_row_conversion(rows):
  connection = Connection([rows])
  with pytest.raises(HistoryReadInvalid, match="budget"):
    await LocalHistoryReader(connection).read_latest_daily(request())
  assert connection.closed == 1


@pytest.mark.parametrize(
  "kwargs",
  [
    {"instruments": []},
    {"instruments": ["000001.SZ"] * 2},
    {"instruments": [f"{i:06}.SZ" for i in range(33)]},
    {"instruments": ["600000.SH' OR TRUE"]},
    {"start": STAMP - timedelta(days=61)},
    {"start": STAMP + timedelta(seconds=1)},
    {"end": STAMP.replace(tzinfo=None)},
  ],
)
def test_bad_window_rejected_before_storage(kwargs):
  with pytest.raises(ValueError):
    request(**kwargs)


async def test_auth_and_history_capacity_are_shared():
  reader = LocalHistoryReader(Connection([[]]))
  app = create_app(store=object(), token="internal", reader=reader)
  async with app.router.lifespan_context(app):
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
      path = "/market-data/internal/v1/history/latest-daily"
      payload = request().model_dump(mode="json")
      assert (await client.post(path, json=payload)).status_code == 401
      client.headers["Authorization"] = "Bearer internal"
      async with reader._slot:
        assert (await client.post(path, json=payload)).status_code == 429
      result = await client.post(path, json=payload)
      assert result.status_code == 200 and result.json()["records"] == []


async def test_client_rejects_mismatched_empty_response():
  async def changed(http_request):
    return httpx.Response(
      200,
      json={
        "request": request(instruments=["600000.SH"]).model_dump(mode="json"),
        "records": [],
      },
    )

  client = LocalMarketDataClient(
    token="internal", transport=httpx.MockTransport(changed)
  )
  try:
    with pytest.raises(ValueError, match="scope mismatch"):
      await client.read_latest_daily(request())
  finally:
    await client.close()
