import asyncio
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
from quantx_infrastructure.core.data.market_stream_transport import (
  MarketStreamFreshnessLease,
  MarketStreamState,
)
from quantx_infrastructure.core.data.whole_quote_hub import WholeQuoteHub
from quantx_qmt_agent.broker import _LocalMarketStreamer


class InMemorySubscription:
  def __init__(self) -> None:
    self.queue: asyncio.Queue[bytes] = asyncio.Queue()

  async def wait_for_message(self, timeout=0.0):
    del timeout
    try:
      return self.queue.get_nowait()
    except asyncio.QueueEmpty:
      return None

  async def messages(self):
    while True:
      yield await self.queue.get()

  async def close(self):
    return None


class InMemoryMarketStore:
  def __init__(self) -> None:
    self.api_state = None
    self.engine_watermark = None
    self.latest = {}
    self.subscription = InMemorySubscription()
    self.freshness = None
    self.state_history = []

  async def cleanup_legacy_whole_controls(self):
    return 0

  async def allocate_generation(self):
    return 1

  async def mark_syncing(self, stream_id, *, generation, reason):
    assert generation == 1
    self.latest = {}
    self.api_state = MarketStreamState(
      status="SYNCING",
      stream_id=stream_id,
      generation=generation,
      updated_at=datetime.now(timezone.utc),
      reason=reason,
    )
    self.freshness = None
    self.state_history.append(self.api_state)

  async def write_batch(self, batch, payload):
    current = self.api_state
    expected = 1 if current.sequence == 0 else current.sequence + 1
    if batch.sequence != expected:
      raise ValueError("sequence gap")
    if current.sequence == 0 and batch.kind is not MarketBatchKind.SNAPSHOT:
      raise ValueError("snapshot required")
    if batch.kind is MarketBatchKind.SNAPSHOT:
      self.latest = {}
    self.latest.update(batch.data)
    self.api_state = MarketStreamState(
      status=(
        "SYNCING"
        if batch.kind is MarketBatchKind.SNAPSHOT
        else "READY"
      ),
      stream_id=batch.stream_id,
      generation=current.generation,
      sequence=batch.sequence,
      captured_at=batch.captured_at,
      updated_at=datetime.now(timezone.utc),
      instrument_count=len(self.latest),
    )
    self.freshness = MarketStreamFreshnessLease(
      stream_id=batch.stream_id,
      sequence=batch.sequence,
    )
    self.state_history.append(self.api_state)
    await self.subscription.queue.put(payload)
    return self.api_state

  async def mark_offline(self, stream_id, *, reason):
    if self.api_state is None or self.api_state.stream_id != stream_id:
      return False
    self.api_state = MarketStreamState(
      status="OFFLINE",
      stream_id=stream_id,
      sequence=self.api_state.sequence,
      captured_at=self.api_state.captured_at,
      updated_at=datetime.now(timezone.utc),
      instrument_count=len(self.latest),
      reason=reason,
    )
    self.freshness = None
    self.state_history.append(self.api_state)
    return True

  async def open_subscription(self):
    return self.subscription

  async def state(self):
    return self.api_state

  async def state_with_freshness(self):
    return self.api_state, self.freshness

  async def load_snapshot(self):
    if self.api_state is None or self.api_state.status != "READY":
      return None
    return self.api_state, dict(self.latest)

  async def write_engine_state(self, **state):
    self.engine_watermark = state


class AlwaysClosed:
  async def is_trading_hours(self, *_args):
    return False


class PipelineWebSocket:
  def __init__(self, batches) -> None:
    self.scope = {"subprotocols": ["quantx.market.v1"]}
    self.batches = batches
    self.sent_controls = []
    self.index = 0

  async def accept(self, *, subprotocol):
    assert subprotocol == "quantx.market.v1"

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
    try:
      control = MarketStreamControl.model_validate_json(payload)
    except Exception:
      return
    self.sent_controls.append(control)
    if control.type is MarketControlType.START:
      for batch in self.batches:
        batch.stream_id = control.stream_id

  async def receive(self):
    if self.index >= len(self.batches):
      return {"type": "websocket.disconnect", "code": 1000}
    batch = self.batches[self.index]
    self.index += 1
    return {"type": "websocket.receive", "bytes": batch.to_bytes()}

  async def close(self, *, code, reason=""):
    del code, reason


@pytest.mark.asyncio
async def test_fake_xtdata_flows_through_market_websocket_redis_and_engine(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  callbacks = []

  class FakeXTData:
    def get_stock_list_in_sector(self, sector):
      return {
        "沪深A股": ["600000.SH"],
        "沪深指数": ["000001.SH"],
      }[sector]

    def get_instrument_detail_list(self, _codes, iscomplete=False):
      assert iscomplete is True
      return {"600000.SH": {"UpStopPrice": 11.0, "PriceTick": 0.01}}

    def subscribe_whole_quote(self, codes, callback):
      assert codes == ["SH", "SZ"]
      callbacks.append(callback)
      return 1

    def get_full_tick(self, _codes):
      return {
        "000001.SH": {"lastPrice": 3500.0, "time": 1_000},
        "600000.SH": {"lastPrice": 10.0, "time": 1_000},
      }

    def unsubscribe_quote(self, _subscription_id):
      return None

  streamer = _LocalMarketStreamer(FakeXTData())
  raw_events = []
  assert streamer.subscribe_whole_market(raw_events.append)
  callbacks[0](
    {
      "000001.SH": {"lastPrice": 3501.0, "time": 2_000},
      "510300.SH": {"lastPrice": 4.5, "time": 2_000},
      "600000.SH": {"lastPrice": 10.2, "time": 2_000},
    }
  )
  assert raw_events
  assert set(raw_events[0]) == {"000001.SH", "600000.SH"}

  snapshot = streamer.whole_market_snapshot()
  delta = streamer.prepare_whole_market_data(raw_events[0])
  batches = [
    MarketStreamBatch(
      stream_id="pending",
      sequence=1,
      kind=MarketBatchKind.SNAPSHOT,
      captured_at=datetime.now(timezone.utc),
      instrument_count=len(snapshot),
      data=snapshot,
    ),
    MarketStreamBatch(
      stream_id="pending",
      sequence=2,
      kind=MarketBatchKind.DELTA,
      captured_at=datetime.now(timezone.utc),
      instrument_count=0,
      data={},
    ),
    MarketStreamBatch(
      stream_id="pending",
      sequence=3,
      kind=MarketBatchKind.DELTA,
      captured_at=datetime.now(timezone.utc),
      instrument_count=len(delta),
      data=delta,
    ),
  ]
  store = InMemoryMarketStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysClosed())
  websocket = PipelineWebSocket(batches)

  async def authenticate(_envelope):
    return SimpleNamespace(
      device=SimpleNamespace(id="device-1"),
      expires_at=agent_api.utcnow() + timedelta(minutes=5),
    )

  async def allowed(_device_id):
    return True

  async def active(_device_id):
    return None

  original_registry = agent_api._market_connections
  monkeypatch.setattr(agent_api, "_authenticate", authenticate)
  monkeypatch.setattr(agent_api, "_ensure_device_active", active)
  monkeypatch.setattr(agent_api.agent_connection_hub, "is_market_device", allowed)
  monkeypatch.setattr(agent_api, "market_stream_store", store)
  monkeypatch.setattr(
    agent_api,
    "_market_connections",
    agent_api._MarketConnectionRegistry(),
  )

  await hub.start()
  try:
    await agent_api.agent_market_websocket(websocket)
    for _ in range(20):
      if hub.sequence == 3:
        break
      await asyncio.sleep(0)
    assert hub.sequence == 3
    assert hub.latest("600000.SH")["lastPrice"] == 10.2
    assert [control.type for control in websocket.sent_controls] == [
      MarketControlType.START,
      MarketControlType.ACK,
      MarketControlType.ACK,
      MarketControlType.ACK,
    ]
    assert [state.status for state in store.state_history] == [
      "SYNCING",
      "SYNCING",
      "READY",
      "READY",
      "OFFLINE",
    ]
    assert store.state_history[1].sequence == 1
    assert store.state_history[2].sequence == 2
    assert store.engine_watermark["sequence"] == 3
  finally:
    await hub.stop()
    streamer.unsubscribe_whole_market()
    agent_api._market_connections = original_registry


@pytest.mark.asyncio
async def test_snapshot_ack_then_disconnect_never_makes_store_ready(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  snapshot = MarketStreamBatch(
    stream_id="pending",
    sequence=1,
    kind=MarketBatchKind.SNAPSHOT,
    captured_at=datetime.now(timezone.utc),
    instrument_count=1,
    data={"600000.SH": {"lastPrice": 10.0, "time": 1_000}},
  )
  store = InMemoryMarketStore()
  websocket = PipelineWebSocket([snapshot])

  async def authenticate(_envelope):
    return SimpleNamespace(
      device=SimpleNamespace(id="device-1"),
      expires_at=agent_api.utcnow() + timedelta(minutes=5),
    )

  async def allowed(_device_id):
    return True

  async def active(_device_id):
    return None

  original_registry = agent_api._market_connections
  monkeypatch.setattr(agent_api, "_authenticate", authenticate)
  monkeypatch.setattr(agent_api, "_ensure_device_active", active)
  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "is_market_device",
    allowed,
  )
  monkeypatch.setattr(agent_api, "market_stream_store", store)
  monkeypatch.setattr(
    agent_api,
    "_market_connections",
    agent_api._MarketConnectionRegistry(),
  )

  try:
    await agent_api.agent_market_websocket(websocket)
    assert [control.type for control in websocket.sent_controls] == [
      MarketControlType.START,
      MarketControlType.ACK,
    ]
    assert [state.status for state in store.state_history] == [
      "SYNCING",
      "SYNCING",
      "OFFLINE",
    ]
    assert store.state_history[1].sequence == 1
    assert store.api_state.status == "OFFLINE"
    assert store.api_state.sequence == 1
    assert store.freshness is None
    assert await store.load_snapshot() is None
  finally:
    agent_api._market_connections = original_registry
