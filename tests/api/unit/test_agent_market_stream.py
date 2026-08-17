from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from quantx_api import agent_api
from quantx_contracts import (
  AgentEnvelope,
  AgentMessageType,
  MarketBatchKind,
  MarketControlType,
  MarketStreamBatch,
  MarketStreamControl,
)


class FakeWebSocket:
  def __init__(self, batch: MarketStreamBatch) -> None:
    self.scope = {"subprotocols": ["quantx.market.v1"]}
    self.batch = batch
    self.sent_text = []
    self.closed = []
    self.accepted = ""

  async def accept(self, *, subprotocol):
    self.accepted = subprotocol

  async def receive_text(self):
    return AgentEnvelope(
      message_type=AgentMessageType.AUTH,
      payload={
        "device_id": "device-1",
        "access_token": "token",
        "capabilities": ["market-data"],
      },
    ).model_dump_json()

  async def send_text(self, payload):
    self.sent_text.append(payload)

  async def receive(self):
    return {"type": "websocket.receive", "bytes": self.batch.to_bytes()}

  async def close(self, *, code, reason=""):
    self.closed.append((code, reason))


class FailingStore:
  def __init__(self) -> None:
    self.stream_id = ""
    self.offline = []

  async def cleanup_legacy_whole_controls(self):
    return 0

  async def mark_syncing(self, stream_id, *, reason):
    del reason
    self.stream_id = stream_id

  async def write_batch(self, _batch, _payload):
    raise ConnectionError("redis unavailable")

  async def mark_offline(self, stream_id, *, reason):
    self.offline.append((stream_id, reason))
    return True


@pytest.mark.asyncio
async def test_redis_failure_sends_resync_without_ack(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  batch = MarketStreamBatch(
    stream_id="placeholder",
    sequence=1,
    kind=MarketBatchKind.SNAPSHOT,
    captured_at=datetime.now(timezone.utc),
    instrument_count=1,
    data={"600000.SH": {"lastPrice": 10.0}},
  )
  websocket = FakeWebSocket(batch)
  store = FailingStore()

  async def authenticate(_envelope):
    return SimpleNamespace(
      device=SimpleNamespace(id="device-1"),
      expires_at=agent_api.utcnow() + timedelta(minutes=5),
    )

  async def is_market_device(_device_id):
    return True

  async def ensure_device_active(_device_id):
    return None

  original_registry = agent_api._market_connections
  monkeypatch.setattr(agent_api, "_authenticate", authenticate)
  monkeypatch.setattr(agent_api, "_ensure_device_active", ensure_device_active)
  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "is_market_device",
    is_market_device,
  )
  monkeypatch.setattr(agent_api, "market_stream_store", store)
  monkeypatch.setattr(
    agent_api,
    "_market_connections",
    agent_api._MarketConnectionRegistry(),
  )

  # Substitute the server-generated stream id into the single data frame.
  async def receive():
    batch.stream_id = store.stream_id
    return {"type": "websocket.receive", "bytes": batch.to_bytes()}

  websocket.receive = receive
  try:
    await agent_api.agent_market_websocket(websocket)
  finally:
    agent_api._market_connections = original_registry

  controls = []
  for payload in websocket.sent_text[1:]:
    try:
      controls.append(MarketStreamControl.model_validate_json(payload))
    except Exception:
      pass
  assert [control.type for control in controls] == [
    MarketControlType.START,
    MarketControlType.RESYNC,
  ]
  assert all(control.type is not MarketControlType.ACK for control in controls)
  assert websocket.closed[-1][0] == 1011
  assert store.offline
