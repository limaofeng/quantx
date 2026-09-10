"""Actual local client serialization for API range reads, aggregation and factors."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from quantx_infrastructure.services import local_historical_market_reader as module
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)

from tests.infrastructure.test_dividend_adjustment_semantics import _klines


@pytest.fixture
def source(monkeypatch):
  case = SimpleNamespace(rows=[], factors=[], queries=[], clients=[], failure=None)

  async def reply(request):
    case.queries.append(request)
    if case.failure == "cancel":
      raise asyncio.CancelledError()
    if case.failure == "error":
      return httpx.Response(503)
    query = request.url.params
    if request.url.path.endswith("/calendar"):
      return httpx.Response(
        200,
        json={
          **dict(query),
          "year": int(query["year"]),
          "holidays": [
            {"date": query["year"] + "-12-31", "description": "fixture holiday"}
          ],
        },
      )
    if request.url.path.endswith("divid-factors"):
      return httpx.Response(200, json={"request": dict(query), "records": case.factors})
    after = datetime.fromisoformat(query["after"]) if "after" in query else None
    rows = [
      row
      for row in case.rows
      if datetime.fromisoformat(row["time"])
      .astimezone(ZoneInfo("Asia/Shanghai"))
      .date()
      .isoformat()
      == query["trading_date"]
      and (after is None or datetime.fromisoformat(row["time"]) > after)
    ]
    rows = rows[:1]  # short nonempty page must not terminate the range
    return httpx.Response(
      200,
      json={
        "records": rows,
        "exhausted": not rows,
        "next_after": rows[-1]["time"] if rows else None,
      },
    )

  def client():
    value = LocalMarketDataClient(transport=httpx.MockTransport(reply), token="test")
    case.clients.append(value)
    return value

  monkeypatch.setattr(module, "LocalMarketDataClient", client)
  monkeypatch.setattr(
    "quantx_infrastructure.services.local_historical_tick_reader.LocalMarketDataClient",
    client,
  )
  return case


def daily_rows():
  return [
    {
      **vars(row),
      "time": row.time.replace(tzinfo=ZoneInfo("Asia/Shanghai")).isoformat(),
    }
    for row in _klines()
  ]


@pytest.mark.parametrize(
  "adjustment,expected",
  [("none", [55, 50, 100]), ("front", [55, 50, 50]), ("back", [110, 100, 100])],
)
async def test_daily_range_and_factor_reads_preserve_adjustment(
  source, adjustment, expected
):
  source.rows = daily_rows()
  source.factors = [
    {
      "stock_code": "000001.SZ",
      "time": "2024-01-02T00:00:00+08:00",
      "ex_date": "2024-01-02",
      **{
        key: "0"
        for key in (
          "interest",
          "stock_bonus",
          "stock_gift",
          "allot_num",
          "allot_price",
          "gugai",
        )
      },
      "dr": "2",
    }
  ]
  rows = await module.LocalHistoricalMarketReader().get_kline_data(
    "000001.SZ",
    "1d",
    datetime(2024, 1, 1),
    datetime(2024, 1, 3, 23),
    dividend_type=adjustment,
    order="desc",
  )
  assert [row.close for row in rows] == expected
  assert all(client.client.is_closed for client in source.clients)
  assert len([q for q in source.queries if q.url.path.endswith("/history")]) == 6


async def test_intraday_short_pages_aggregate_in_shanghai_and_limit_after_sort(source):
  base = daily_rows()[0]
  start = datetime(2024, 1, 1, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
  source.rows = [
    {
      **base,
      "period": "1m",
      "time": (start + timedelta(minutes=i)).astimezone(timezone.utc).isoformat(),
      "open": 10 + i,
      "high": 10 + i,
      "low": 10 + i,
      "close": 10 + i,
    }
    for i in range(6)
  ]
  rows = await module.LocalHistoricalMarketReader().get_kline_data(
    "000001.SZ", "5m", start, start + timedelta(minutes=5), limit=1, order="desc"
  )
  assert len(rows) == 1 and rows[0].close == 15
  assert rows[0].time.hour == 9 and rows[0].time.minute == 35
  assert len(source.queries) == 8
  assert all(c.client.is_closed for c in source.clients)


@pytest.mark.parametrize("kind", ["capacity", "error", "cancel"])
async def test_partial_results_are_not_returned_on_failure(source, monkeypatch, kind):
  source.rows = daily_rows()
  if kind == "capacity":
    monkeypatch.setattr(module, "MAX_ROWS", 1)
  else:
    source.failure = kind
  with pytest.raises(
    asyncio.CancelledError
    if kind == "cancel"
    else httpx.HTTPStatusError
    if kind == "error"
    else ValueError
  ):
    await module.LocalHistoricalMarketReader().get_kline_data(
      "000001.SZ", "1d", datetime(2024, 1, 1), datetime(2024, 1, 3)
    )
  assert all(c.client.is_closed for c in source.clients)


async def test_tick_short_pages_keep_same_millisecond_ordinals_and_depth(source):
  from tests.infrastructure.test_local_history_reader import STAMP, tick

  source.rows = [
    {**tick(i), "time": tick(i)["time"].isoformat(), "ask1": 10.1, "bid1": 10.0}
    for i in range(2)
  ]
  rows = await module.LocalHistoricalMarketReader().get_tick_data(
    "600000.SH",
    STAMP,
    STAMP,
    order="desc",
    limit=2,
  )
  assert [row.tick_ordinal for row in rows] == [1, 0]
  assert rows[0].ask_price == [10.1] and rows[0].bid_price == [10.0]
  assert len(source.queries) == 4
  assert all(c.client.is_closed for c in source.clients)


async def test_default_api_facade_uses_local_range_reader(source, monkeypatch):
  from unittest.mock import AsyncMock

  from quantx_api.market_data_read_service import ApiMarketDataReadService

  source.rows = daily_rows()
  facade = ApiMarketDataReadService()
  monkeypatch.setattr(facade, "_runtime_items", AsyncMock(return_value=[]))
  rows = await facade.get_klines(
    stock_code="000001.SZ",
    period="1d",
    start_time=datetime(2024, 1, 1),
    end_time=datetime(2024, 1, 3, 23),
    limit=2,
  )
  assert [row.close for row in rows] == [55, 50]
  assert len(source.queries) == 7
  assert all(c.client.is_closed for c in source.clients)


@pytest.mark.parametrize(
  "dates,expected",
  [
    (
      [
        datetime(2024, 1, 5),
        datetime(2024, 1, 6),
        datetime(2024, 1, 7),
        datetime(2024, 1, 8),
      ],
      [5, 8],
    ),
    ([datetime(2024, 12, 30), datetime(2024, 12, 31)], [30]),
  ],
)
async def test_range_skips_weekends_and_explicit_calendar_holidays(
  source, dates, expected
):
  base = daily_rows()[0]
  source.rows = [
    {**base, "time": value.replace(tzinfo=ZoneInfo("Asia/Shanghai")).isoformat()}
    for value in dates
  ]
  result = await module.LocalHistoricalMarketReader().get_kline_data(
    "000001.SZ", "1d", dates[0], dates[-1] + timedelta(hours=23)
  )
  assert [row.time.day for row in result] == expected
  queried = {
    q.url.params["trading_date"]
    for q in source.queries
    if q.url.path.endswith("/history")
  }
  assert len(queried) == len(expected)


async def test_mcp_kline_reads_local_history_and_returns_storage_time(
  source, monkeypatch
):
  from quantx_api.quantx_mcp.tools import MarketDataTools

  source.rows = daily_rows()
  monkeypatch.setattr(
    "quantx_api.quantx_mcp.tools.time_utils.now", lambda: datetime(2024, 1, 3, 23)
  )
  result = await MarketDataTools()._get_kline(
    {"symbol": "000001.SZ", "period": "1d", "count": 2}
  )
  assert result["status"] == "success" and result["count"] == 2
  assert [row["close"] for row in result["data"]] == [50, 55]
  assert [datetime.fromisoformat(row["datetime"]).day for row in result["data"]] == [
    2,
    3,
  ]
  assert all(c.client.is_closed for c in source.clients)
