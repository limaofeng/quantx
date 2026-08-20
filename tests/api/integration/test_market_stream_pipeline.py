import asyncio
from concurrent.futures import ThreadPoolExecutor
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
  validate_market_stream_capture_time,
)
from quantx_infrastructure.core.data.market_stream_transport import (
  MarketStreamFreshnessLease,
  MarketStreamState,
  _batch_source_times,
)
from quantx_infrastructure.core.data.whole_quote_hub import WholeQuoteHub
from quantx_qmt_agent import runtime as runtime_module
from quantx_qmt_agent.broker import _LocalMarketStreamer
from quantx_qmt_agent.runtime import AgentRuntime
from quantx_qmt_agent.whole_market_capture import WholeMarketCapture
from starlette.websockets import WebSocketState


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

  async def write_batch(self, batch, payload, *, received_at):
    validate_market_stream_capture_time(
      batch.captured_at,
      received_at=received_at,
    )
    _batch_source_times(batch, received_at=received_at)
    current = self.api_state
    expected = 1 if current.sequence == 0 else current.sequence + 1
    if batch.sequence != expected:
      raise ValueError("sequence gap")
    if current.sequence == 0 and batch.kind is not MarketBatchKind.SNAPSHOT:
      raise ValueError("snapshot required")
    if batch.kind is MarketBatchKind.SNAPSHOT:
      self.latest = {}
    self.latest.update(batch.data)
    if batch.kind is MarketBatchKind.SNAPSHOT:
      next_status = "SYNCING"
    elif current.status == "SYNCING" and current.sequence == 1:
      next_status = "SYNCING"
    elif current.status == "SYNCING" and current.sequence == 2:
      next_status = "READY"
    elif current.status == "READY" and current.sequence >= 3:
      next_status = "READY"
    else:
      raise ValueError("invalid market stream phase")
    self.api_state = MarketStreamState(
      status=next_status,
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


class _MarketDuplex:
  def __init__(self) -> None:
    self.agent_to_api: asyncio.Queue[object] = asyncio.Queue()
    self.api_to_agent: asyncio.Queue[str] = asyncio.Queue()
    self.release_sequence_two_ack = asyncio.Event()
    self.release_sequence_three_ack = asyncio.Event()


class _AgentMarketSocket:
  def __init__(self, duplex: _MarketDuplex) -> None:
    self.duplex = duplex
    self.closed = False

  async def send(self, payload) -> None:
    await self.duplex.agent_to_api.put(payload)

  async def recv(self) -> str:
    return await self.duplex.api_to_agent.get()

  async def close(self, **_kwargs) -> None:
    if self.closed:
      return
    self.closed = True
    await self.duplex.agent_to_api.put(None)


class _AgentSocketContext:
  def __init__(self, socket: _AgentMarketSocket) -> None:
    self.socket = socket

  async def __aenter__(self):
    return self.socket

  async def __aexit__(self, *_args):
    await self.socket.close()
    return False


class _ApiMarketSocket:
  def __init__(self, duplex: _MarketDuplex) -> None:
    self.duplex = duplex
    self.scope = {"subprotocols": ["quantx.market.v1"]}
    self.client_state = WebSocketState.CONNECTING
    self.application_state = WebSocketState.CONNECTING

  async def accept(self, *, subprotocol):
    assert subprotocol == "quantx.market.v1"
    self.client_state = WebSocketState.CONNECTED
    self.application_state = WebSocketState.CONNECTED

  async def receive_text(self):
    payload = await self.duplex.agent_to_api.get()
    assert isinstance(payload, str)
    return payload

  async def receive(self):
    payload = await self.duplex.agent_to_api.get()
    if payload is None:
      self.client_state = WebSocketState.DISCONNECTED
      return {"type": "websocket.disconnect", "code": 1000}
    assert isinstance(payload, bytes)
    return {"type": "websocket.receive", "bytes": payload}

  async def send_text(self, payload):
    control = None
    try:
      control = MarketStreamControl.model_validate_json(payload)
    except Exception:
      pass
    if (
      control is not None
      and control.type is MarketControlType.ACK
      and control.sequence == 2
    ):
      await self.duplex.release_sequence_two_ack.wait()
    if (
      control is not None
      and control.type is MarketControlType.ACK
      and control.sequence == 3
    ):
      await self.duplex.release_sequence_three_ack.wait()
    await self.duplex.api_to_agent.put(payload)

  async def close(self, *, code, reason=""):
    del code, reason
    self.application_state = WebSocketState.DISCONNECTED


async def _wait_until(predicate, *, timeout: float = 2.0) -> None:
  deadline = asyncio.get_running_loop().time() + timeout
  while not predicate():
    if asyncio.get_running_loop().time() >= deadline:
      raise AssertionError("condition was not reached before timeout")
    await asyncio.sleep(0)


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
      assert codes == ["000001.SH", "600000.SH"]
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
      "SYNCING",
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
async def test_delayed_sequence_two_ack_keeps_engine_closed_until_sequence_three(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class Broker:
    @staticmethod
    def prepare_whole_market_data(data):
      return data

  duplex = _MarketDuplex()
  agent_socket = _AgentMarketSocket(duplex)
  api_socket = _ApiMarketSocket(duplex)
  store = InMemoryMarketStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysClosed())

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.configuration = SimpleNamespace(
    device_id="device-1",
    api_url="http://api.test",
  )
  runtime.mode = "live"
  runtime.broker = Broker()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=64 * 1024 * 1024,
    estimated_tick_bytes=2048,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_subscription_ready = asyncio.Event()
  runtime._whole_market_subscription_ready.set()
  runtime._whole_market_native_reset = asyncio.Event()
  runtime._access_token = "token-1"
  runtime._access_token_expires_at = datetime.now(timezone.utc) + timedelta(
    hours=1
  )
  runtime._access_token_ready = asyncio.Event()
  runtime._control_hub_registered_once = asyncio.Event()
  runtime._control_hub_registered_once.set()
  runtime._whole_market_encode_executor = ThreadPoolExecutor(max_workers=1)
  runtime._market_stream_ready_since_monotonic = 0.0

  async def build_snapshot(_trading_date):
    return {
      "600000.SH": {"lastPrice": 10.0, "time": 1_000}
    }, runtime._whole_market_capture.capture_sequence

  runtime._build_whole_market_snapshot = build_snapshot

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
  monkeypatch.setattr(
    runtime_module.websockets,
    "connect",
    lambda *_args, **_kwargs: _AgentSocketContext(agent_socket),
  )

  await hub.start()
  api_task = asyncio.create_task(agent_api.agent_market_websocket(api_socket))
  agent_task = asyncio.create_task(runtime._run_whole_market_stream())
  try:
    await _wait_until(
      lambda: store.api_state is not None
      and store.api_state.sequence == 2
    )
    assert store.api_state.status == "SYNCING"
    assert not hub.is_ready
    assert runtime._market_stream_status == "SYNCING"

    for price in (10.1, 10.2, 10.3):
      runtime._whole_market_capture.capture(
        {"600000.SH": {"lastPrice": price, "time": int(price * 1_000)}}
      )
    assert runtime._whole_market_capture.queue_depth == 0

    duplex.release_sequence_two_ack.set()
    await _wait_until(
      lambda: store.api_state is not None
      and store.api_state.status == "READY"
      and store.api_state.sequence == 3
      and hub.is_ready
    )
    assert store.latest["600000.SH"]["lastPrice"] == 10.3
    assert hub.latest("600000.SH")["lastPrice"] == 10.3
    assert runtime._market_stream_status == "SYNCING"
    assert runtime._market_stream_ready_since_monotonic == 0.0

    duplex.release_sequence_three_ack.set()
    await _wait_until(lambda: runtime._market_stream_status == "READY")
    assert runtime._market_stream_ready_since_monotonic > 0

    runtime._whole_market_capture.capture(
      {"600000.SH": {"lastPrice": 10.4, "time": 10_400}}
    )
    await _wait_until(
      lambda: store.api_state is not None
      and store.api_state.sequence == 4
      and hub.sequence == 4
    )
    assert hub.latest("600000.SH")["lastPrice"] == 10.4
  finally:
    agent_task.cancel()
    await asyncio.gather(agent_task, return_exceptions=True)
    await agent_socket.close()
    await asyncio.gather(api_task, return_exceptions=True)
    await hub.stop()
    runtime._whole_market_encode_executor.shutdown(wait=True)
    agent_api._market_connections = original_registry


@pytest.mark.asyncio
async def test_sequence_three_commit_failure_never_makes_store_ready(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class FailConfirmationStore(InMemoryMarketStore):
    async def write_batch(self, batch, payload, *, received_at):
      if batch.sequence == 3:
        raise ConnectionError("sequence-three Redis failure")
      return await super().write_batch(
        batch,
        payload,
        received_at=received_at,
      )

  batches = [
    MarketStreamBatch(
      stream_id="pending",
      sequence=1,
      kind=MarketBatchKind.SNAPSHOT,
      captured_at=datetime.now(timezone.utc),
      instrument_count=1,
      data={"600000.SH": {"lastPrice": 10.0, "time": 1_000}},
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
      instrument_count=1,
      data={"600000.SH": {"lastPrice": 10.1, "time": 1_100}},
    ),
  ]
  store = FailConfirmationStore()
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
  try:
    await agent_api.agent_market_websocket(websocket)
  finally:
    agent_api._market_connections = original_registry

  controls = websocket.sent_controls
  assert [control.type for control in controls] == [
    MarketControlType.START,
    MarketControlType.ACK,
    MarketControlType.ACK,
    MarketControlType.RESYNC,
  ]
  assert [control.sequence for control in controls[1:]] == [1, 2, 2]
  assert all(state.status != "READY" for state in store.state_history)
  assert store.state_history[-1].status == "OFFLINE"
  assert store.state_history[-1].sequence == 2


@pytest.mark.asyncio
async def test_untrusted_websocket_tick_without_source_time_fails_closed(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  invalid_snapshot = MarketStreamBatch(
    stream_id="pending",
    sequence=1,
    kind=MarketBatchKind.SNAPSHOT,
    captured_at=datetime.now(timezone.utc),
    instrument_count=1,
    data={"600000.SH": {"lastPrice": 10.0}},
  )
  store = InMemoryMarketStore()
  websocket = PipelineWebSocket([invalid_snapshot])

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
  try:
    await agent_api.agent_market_websocket(websocket)
  finally:
    agent_api._market_connections = original_registry

  assert [control.type for control in websocket.sent_controls] == [
    MarketControlType.START,
    MarketControlType.RESYNC,
  ]
  assert websocket.sent_controls[-1].sequence == 0
  assert "valid source time" in websocket.sent_controls[-1].reason
  assert store.api_state.status == "OFFLINE"
  assert store.api_state.sequence == 0
  assert store.latest == {}
  assert store.freshness is None
  assert await store.load_snapshot() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("include_barrier", [False, True])
async def test_disconnect_before_sequence_three_never_makes_store_ready(
  monkeypatch: pytest.MonkeyPatch,
  include_barrier: bool,
) -> None:
  snapshot = MarketStreamBatch(
    stream_id="pending",
    sequence=1,
    kind=MarketBatchKind.SNAPSHOT,
    captured_at=datetime.now(timezone.utc),
    instrument_count=1,
    data={"600000.SH": {"lastPrice": 10.0, "time": 1_000}},
  )
  batches = [snapshot]
  if include_barrier:
    batches.append(
      MarketStreamBatch(
        stream_id="pending",
        sequence=2,
        kind=MarketBatchKind.DELTA,
        captured_at=datetime.now(timezone.utc),
        instrument_count=0,
        data={},
      )
    )
  store = InMemoryMarketStore()
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
    expected_sequences = [1, 2] if include_barrier else [1]
    assert [control.type for control in websocket.sent_controls] == [
      MarketControlType.START,
      *[MarketControlType.ACK for _ in expected_sequences],
    ]
    assert [
      control.sequence for control in websocket.sent_controls[1:]
    ] == expected_sequences
    assert all(state.status != "READY" for state in store.state_history)
    expected_sequence = 2 if include_barrier else 1
    assert store.api_state.status == "OFFLINE"
    assert store.api_state.sequence == expected_sequence
    assert store.freshness is None
    assert await store.load_snapshot() is None
  finally:
    agent_api._market_connections = original_registry
