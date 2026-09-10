"""Engine preflight through the actual local HTTP client, without market IO."""

import asyncio
from datetime import datetime

import httpx
import pytest
from quantx_engine.strategy_manager import StrategyManager
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)


@pytest.mark.parametrize(
  "kind", ["tick", "daily", "aggregate", "empty", "after_end", "failure", "cancel"]
)
async def test_presence_uses_one_bounded_page_and_closes_client(monkeypatch, kind):
  StrategyManager._instance = None
  manager = StrategyManager()
  requests = []

  async def respond(request):
    requests.append(request)
    if kind == "failure":
      return httpx.Response(503)
    if kind == "cancel":
      raise asyncio.CancelledError()
    stamp = (
      "2026-07-31T00:00:00+08:00" if kind == "daily" else "2026-07-31T09:30:00+08:00"
    )
    if kind == "after_end":
      stamp = "2026-07-31T16:00:00+08:00"
    rows = (
      []
      if kind == "empty"
      else [
        {
          "time": stamp,
          "stock_code": "600887.SH",
          "period": request.url.params["period"],
        }
      ]
    )
    return httpx.Response(
      200,
      json={
        "records": rows,
        "next_after": stamp if rows else None,
        "exhausted": not rows,
      },
    )

  client = LocalMarketDataClient(transport=httpx.MockTransport(respond), token="test")
  monkeypatch.setattr(
    "quantx_engine.strategy_manager.LocalMarketDataClient", lambda: client
  )
  start, end = datetime(2026, 7, 31, 9, 30), datetime(2026, 7, 31, 15, 30)
  try:
    if kind == "cancel":
      with pytest.raises(asyncio.CancelledError):
        await manager._has_tick_data("600887.SH", start, end)
    else:
      result = (
        await manager._has_kline_data(
          "600887.SH", "1d" if kind == "daily" else "5m", start, end
        )
        if kind in {"daily", "aggregate"}
        else await manager._has_tick_data("600887.SH", start, end)
      )
      assert result is (kind in {"tick", "daily", "aggregate"})
    assert client.client.is_closed
    assert len(requests) == 1
    query = requests[0].url.params
    assert query["page_size"] == "1"
    assert query["trading_date"] == "2026-07-31"
    assert query["instrument"] == "600887.SH"
    assert query["period"] == (
      "1d" if kind == "daily" else "1m" if kind == "aggregate" else "tick"
    )
    if kind == "daily":
      assert "after" not in query
    else:
      assert (
        datetime.fromisoformat(query["after"]).isoformat()
        == "2026-07-31T09:29:59.999999+08:00"
      )
  finally:
    StrategyManager._instance = None
    await client.close()
