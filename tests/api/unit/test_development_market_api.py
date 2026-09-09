import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from quantx_contracts import MarketBatchKind, MarketStreamBatch
from quantx_market_data import development_stream as api
from quantx_market_data.development_access import authorized
from quantx_market_data.development_history import router as history_router
from quantx_market_data.development_stream import router
from starlette.websockets import WebSocketDisconnect


def test_market_credential_is_production_only(monkeypatch):
  token = "a" * 32
  monkeypatch.setenv("QUANTX_MARKET_DATA_TOKEN", token)
  monkeypatch.setenv("ENV", "development")
  assert not authorized(f"Bearer {token}")
  monkeypatch.setenv("ENV", "production")
  assert authorized(f"Bearer {token}")
  assert not authorized(f"Bearer {token}x")


def test_history_requires_credential_without_touching_storage(monkeypatch):
  monkeypatch.setenv("ENV", "production")
  monkeypatch.setenv("QUANTX_MARKET_DATA_TOKEN", "a" * 32)
  app = FastAPI()
  app.include_router(router)
  app.include_router(history_router)
  with TestClient(app) as client:
    assert client.get("/market-data/v1/history/unknown").status_code == 403


def test_snapshot_preserves_capture_time_and_gap_forces_resync(monkeypatch):
  captured = datetime.now(timezone.utc)

  class Subscription:
    async def messages(self):
      yield MarketStreamBatch(
        stream_id="source",
        sequence=5,
        captured_at=captured,
        kind=MarketBatchKind.DELTA,
        instrument_count=0,
        data={},
      ).to_bytes()
      await asyncio.Event().wait()

    close = AsyncMock()

  monkeypatch.setattr(api, "authorized", lambda _: True)
  monkeypatch.setattr(
    api.market_stream_store, "open_subscription", AsyncMock(return_value=Subscription())
  )
  monkeypatch.setattr(
    api.market_stream_store,
    "load_selected_snapshot",
    AsyncMock(
      return_value=(
        SimpleNamespace(stream_id="source", sequence=3, captured_at=captured),
        {"600000.SH": {"time": 1}, "600001.SH": {"time": 2}},
      )
    ),
  )
  app = FastAPI()
  app.include_router(router)
  app.include_router(history_router)
  with TestClient(app) as client:
    with client.websocket_connect("/market-data/v1/stream") as socket:
      socket.send_json({"instruments": ["600000.SH"]})
      batch = MarketStreamBatch.from_bytes(socket.receive_bytes())
      assert batch.captured_at == captured
      assert set(batch.data) == {"600000.SH"}
      with pytest.raises(WebSocketDisconnect):
        socket.receive_bytes()
  assert not api._stream_connection.locked()


@pytest.mark.asyncio
async def test_disconnect_releases_connection_without_waiting_for_market(monkeypatch):
  class Subscription:
    async def messages(self):
      await asyncio.Event().wait()
      yield b""

    close = AsyncMock()

  subscription = Subscription()
  monkeypatch.setattr(api, "_stream_connection", asyncio.Lock())
  monkeypatch.setattr(api, "authorized", lambda _: True)
  monkeypatch.setattr(
    api.market_stream_store, "open_subscription", AsyncMock(return_value=subscription)
  )
  monkeypatch.setattr(
    api.market_stream_store,
    "load_selected_snapshot",
    AsyncMock(
      return_value=(
        SimpleNamespace(
          stream_id="source", sequence=1, captured_at=datetime.now(timezone.utc)
        ),
        {"600000.SH": {"time": 1}},
      )
    ),
  )
  socket = SimpleNamespace(
    headers={},
    accept=AsyncMock(),
    receive_json=AsyncMock(return_value={"instruments": ["600000.SH"]}),
    send_bytes=AsyncMock(),
    receive=AsyncMock(return_value={"type": "websocket.disconnect"}),
    close=AsyncMock(),
  )
  await asyncio.wait_for(api.stream(socket), timeout=1)
  assert not api._stream_connection.locked()
  subscription.close.assert_awaited_once()
