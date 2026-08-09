from datetime import date, datetime, timezone

import pytest
from quantx_api.gqlapi.resolvers.market_data import MarketDataResolver
from quantx_api.gqlapi.resolvers.watchlist import WatchlistResolver
from quantx_engine import command_processor
from quantx_infrastructure.services.engine_command_service import (
  EngineCommandReceipt,
)


@pytest.mark.asyncio
async def test_watchlist_change_enqueues_engine_refresh(monkeypatch) -> None:
  captured = {}

  async def fake_enqueue(command_type, payload, **kwargs):
    captured.update(
      command_type=command_type,
      payload=payload,
      aggregate_id=kwargs["aggregate_id"],
    )
    return EngineCommandReceipt(
      message_id="message-1",
      command_type=command_type,
      aggregate_id=kwargs["aggregate_id"],
      status="PENDING",
    )

  monkeypatch.setattr(
    "quantx_api.gqlapi.resolvers.watchlist.engine_command_service.enqueue",
    fake_enqueue,
  )

  await WatchlistResolver._notify_engine("account-1")

  assert captured == {
    "command_type": "WARM_CACHE_REFRESH_SOURCES",
    "payload": {"account_id": "account-1"},
    "aggregate_id": "account-1",
  }


@pytest.mark.asyncio
async def test_warm_cache_status_is_read_from_engine(monkeypatch) -> None:
  async def fake_request(command_type, payload, **kwargs):
    assert command_type == "WARM_CACHE_STATUS"
    assert payload == {"symbols": ["600000.SH"]}
    assert kwargs["aggregate_id"] == "intraday-warm-cache"
    return EngineCommandReceipt(
      message_id="message-2",
      command_type=command_type,
      aggregate_id=kwargs["aggregate_id"],
      status="SUCCEEDED",
      result={
        "items": [
          {
            "stock_code": "600000.SH",
            "sources": ["watchlist"],
            "tick_subscribed": True,
            "kline_subscribed": True,
            "initialized_date": "2026-07-26",
            "initializing": False,
            "initialization_error": None,
            "last_tick_at": "2026-07-26T01:30:00+00:00",
            "last_kline_at": "2026-07-26T01:30:00+00:00",
            "tick_count": 10,
            "kline_count": 2,
          }
        ]
      },
    )

  monkeypatch.setattr(
    "quantx_api.gqlapi.resolvers.market_data.engine_command_service.request",
    fake_request,
  )

  rows = await MarketDataResolver.get_intraday_warm_cache_status(
    ["600000.SH"]
  )

  assert len(rows) == 1
  assert rows[0].initialized_date == date(2026, 7, 26)
  assert rows[0].last_tick_at == datetime(
    2026,
    7,
    26,
    1,
    30,
    tzinfo=timezone.utc,
  )
  assert rows[0].tick_count == 10


@pytest.mark.asyncio
async def test_engine_dispatch_owns_warm_cache_operations(monkeypatch) -> None:
  calls = []

  async def fake_refresh():
    calls.append("refresh")

  monkeypatch.setattr(
    command_processor.intraday_warm_cache,
    "refresh_source_symbols",
    fake_refresh,
  )
  monkeypatch.setattr(
    command_processor.intraday_warm_cache,
    "get_status",
    lambda symbols: [
      {
        "stock_code": symbols[0],
        "sources": ["watchlist"],
        "tick_subscribed": True,
        "kline_subscribed": True,
        "initialized_date": date(2026, 7, 26),
        "initializing": False,
        "initialization_error": None,
        "last_tick_at": None,
        "last_kline_at": None,
        "tick_count": 0,
        "kline_count": 0,
      }
    ],
  )

  assert await command_processor._dispatch(
    "WARM_CACHE_REFRESH_SOURCES",
    {"account_id": "account-1"},
  ) == {"success": True}
  status = await command_processor._dispatch(
    "WARM_CACHE_STATUS",
    {"symbols": ["600000.SH"]},
  )

  assert calls == ["refresh"]
  assert status["items"][0]["initialized_date"] == "2026-07-26"
