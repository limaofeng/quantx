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


class FakeWebSocket:
  def __init__(self, batch: MarketStreamBatch) -> None:
    self.scope = {"subprotocols": ["quantx.market.v1"]}
    self.batch = batch
    self.sent_text = []
    self.closed = []
    self.accepted = ""
    self.receive_count = 0

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
    self.receive_count += 1
    if self.receive_count > 1:
      return {"type": "websocket.disconnect", "code": 1000}
    return {"type": "websocket.receive", "bytes": self.batch.to_bytes()}

  async def close(self, *, code, reason=""):
    self.closed.append((code, reason))


class FailingStore:
  def __init__(self) -> None:
    self.stream_id = ""
    self.offline = []

  async def cleanup_legacy_whole_controls(self):
    return 0

  async def allocate_generation(self):
    return 1

  async def mark_syncing(self, stream_id, *, generation, reason):
    del generation, reason
    self.stream_id = stream_id

  async def write_batch(self, _batch, _payload):
    raise ConnectionError("redis unavailable")

  async def mark_offline(self, stream_id, *, reason):
    self.offline.append((stream_id, reason))
    return True


class HangingStore(FailingStore):
  async def write_batch(self, _batch, _payload):
    await asyncio.Event().wait()

  async def mark_offline(self, stream_id, *, reason):
    del stream_id, reason
    await asyncio.Event().wait()


class SuccessfulStore(FailingStore):
  async def write_batch(self, batch, _payload):
    return SimpleNamespace(sequence=batch.sequence)


class HangingAckWebSocket(FakeWebSocket):
  async def send_text(self, payload):
    try:
      control = MarketStreamControl.model_validate_json(payload)
    except Exception:
      control = None
    if control is not None and control.type is MarketControlType.ACK:
      await asyncio.Event().wait()
    await super().send_text(payload)


@pytest.mark.asyncio
async def test_market_auth_waits_for_control_registration(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls = 0

  async def is_market_device(device_id: str) -> bool:
    nonlocal calls
    assert device_id == "device-1"
    calls += 1
    return calls >= 3

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "is_market_device",
    is_market_device,
  )
  monkeypatch.setattr(
    agent_api,
    "MARKET_STREAM_CONTROL_REGISTRATION_WAIT_SECONDS",
    0.1,
  )
  monkeypatch.setattr(
    agent_api,
    "MARKET_STREAM_CONTROL_REGISTRATION_POLL_SECONDS",
    0.001,
  )

  await agent_api._wait_for_active_market_device("device-1")

  assert calls == 3


@pytest.mark.asyncio
async def test_market_auth_rejects_device_that_never_becomes_active(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls = 0

  async def is_market_device(device_id: str) -> bool:
    nonlocal calls
    assert device_id == "standby-device"
    calls += 1
    return False

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "is_market_device",
    is_market_device,
  )
  monkeypatch.setattr(
    agent_api,
    "MARKET_STREAM_CONTROL_REGISTRATION_WAIT_SECONDS",
    0.08,
  )
  monkeypatch.setattr(
    agent_api,
    "MARKET_STREAM_CONTROL_REGISTRATION_POLL_SECONDS",
    0.01,
  )

  with pytest.raises(agent_api.AuthError) as error:
    await agent_api._wait_for_active_market_device("standby-device")

  assert error.value.code == "FORBIDDEN"
  assert error.value.message == "当前设备不是活动行情 Agent"
  assert calls >= 2


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


@pytest.mark.asyncio
async def test_redis_black_hole_times_out_and_releases_single_connection(
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
  store = HangingStore()
  registry = agent_api._MarketConnectionRegistry()

  async def authenticate(_envelope):
    return SimpleNamespace(
      device=SimpleNamespace(id="device-1"),
      expires_at=agent_api.utcnow() + timedelta(minutes=5),
    )

  async def is_market_device(_device_id):
    return True

  async def ensure_device_active(_device_id):
    return None

  monkeypatch.setattr(agent_api, "_authenticate", authenticate)
  monkeypatch.setattr(agent_api, "_ensure_device_active", ensure_device_active)
  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "is_market_device",
    is_market_device,
  )
  monkeypatch.setattr(agent_api, "market_stream_store", store)
  monkeypatch.setattr(agent_api, "_market_connections", registry)
  monkeypatch.setattr(
    agent_api,
    "MARKET_STREAM_REDIS_COMMIT_TIMEOUT_SECONDS",
    0.01,
  )
  monkeypatch.setattr(
    agent_api,
    "MARKET_STREAM_REDIS_CLEANUP_TIMEOUT_SECONDS",
    0.01,
  )

  async def receive():
    batch.stream_id = store.stream_id
    return {"type": "websocket.receive", "bytes": batch.to_bytes()}

  websocket.receive = receive
  await asyncio.wait_for(agent_api.agent_market_websocket(websocket), timeout=1)

  # The timed-out handler cannot strand the single-active-connection lease.
  replacement = await registry.register()
  assert replacement
  await registry.unregister(replacement)
  controls = [
    MarketStreamControl.model_validate_json(payload)
    for payload in websocket.sent_text[1:]
  ]
  assert MarketControlType.ACK not in {control.type for control in controls}
  assert controls[-1].type is MarketControlType.RESYNC


@pytest.mark.asyncio
async def test_hanging_ack_send_times_out_and_releases_connection(
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
  websocket = HangingAckWebSocket(batch)
  store = SuccessfulStore()
  registry = agent_api._MarketConnectionRegistry()

  async def authenticate(_envelope):
    return SimpleNamespace(
      device=SimpleNamespace(id="device-1"),
      expires_at=agent_api.utcnow() + timedelta(minutes=5),
    )

  async def is_market_device(_device_id):
    return True

  async def ensure_device_active(_device_id):
    return None

  original_receive = websocket.receive

  async def receive():
    batch.stream_id = store.stream_id
    return await original_receive()

  websocket.receive = receive
  monkeypatch.setattr(agent_api, "_authenticate", authenticate)
  monkeypatch.setattr(agent_api, "_ensure_device_active", ensure_device_active)
  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "is_market_device",
    is_market_device,
  )
  monkeypatch.setattr(agent_api, "market_stream_store", store)
  monkeypatch.setattr(agent_api, "_market_connections", registry)
  monkeypatch.setattr(
    agent_api,
    "MARKET_STREAM_CONTROL_SEND_TIMEOUT_SECONDS",
    0.01,
  )

  await asyncio.wait_for(agent_api.agent_market_websocket(websocket), timeout=1)

  replacement = await registry.register()
  assert replacement
  await registry.unregister(replacement)
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
  assert store.offline


def _commit_item(sequence: int, payload: bytes) -> agent_api._MarketCommitItem:
  return agent_api._MarketCommitItem(
    batch=MarketStreamBatch(
      stream_id="stream-1",
      sequence=sequence,
      kind=(
        MarketBatchKind.SNAPSHOT
        if sequence == 1
        else MarketBatchKind.DELTA
      ),
      captured_at=datetime.now(timezone.utc),
      instrument_count=1,
      data={"600000.SH": {"lastPrice": 10.0}},
    ),
    payload=payload,
    received_monotonic=0.0,
  )


@pytest.mark.asyncio
async def test_market_commit_buffer_bounds_batches_and_bytes() -> None:
  buffer = agent_api._MarketCommitBuffer(capacity=2, max_bytes=8)
  first = _commit_item(1, b"1111")
  second = _commit_item(2, b"2222")
  third = _commit_item(3, b"3")
  await buffer.put(first)
  await buffer.put(second)

  blocked = asyncio.create_task(buffer.put(third))
  await asyncio.sleep(0)
  assert not blocked.done()
  assert buffer.buffered_batches == 2
  assert buffer.buffered_bytes == 8

  queued = await buffer.get()
  assert queued is first
  await buffer.complete(first)
  await asyncio.wait_for(blocked, timeout=1)
  assert buffer.buffered_batches == 2
  assert buffer.buffered_bytes == 5

  oversized = agent_api._MarketCommitBuffer(capacity=2, max_bytes=3)
  with pytest.raises(ValueError, match="byte limit"):
    await oversized.put(first)


@pytest.mark.asyncio
async def test_market_pipeline_receives_two_frames_but_acks_only_after_commit(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  batches = [
    _commit_item(1, b"").batch,
    _commit_item(2, b"").batch,
  ]
  both_received = asyncio.Event()
  release_redis = asyncio.Event()

  class PipelineWebSocket:
    def __init__(self) -> None:
      self.receive_count = 0
      self.sent_text: list[str] = []

    async def receive(self):
      if self.receive_count < len(batches):
        batch = batches[self.receive_count]
        self.receive_count += 1
        if self.receive_count == len(batches):
          both_received.set()
        return {"type": "websocket.receive", "bytes": batch.to_bytes()}
      return {"type": "websocket.disconnect", "code": 1000}

    async def send_text(self, payload):
      self.sent_text.append(payload)

  class BlockingStore:
    def __init__(self) -> None:
      self.calls = 0

    async def write_batch(self, batch, _payload):
      self.calls += 1
      if self.calls == 1:
        await release_redis.wait()
      return SimpleNamespace(sequence=batch.sequence)

  async def ensure_device_active(_device_id):
    return None

  websocket = PipelineWebSocket()
  monkeypatch.setattr(agent_api, "market_stream_store", BlockingStore())
  monkeypatch.setattr(agent_api, "_ensure_device_active", ensure_device_active)
  task = asyncio.create_task(
    agent_api._run_market_commit_pipeline(
      websocket,
      stream_id="stream-1",
      device_id="device-1",
      session_expires_at=agent_api.utcnow() + timedelta(minutes=5),
      commit_state=agent_api._MarketCommitState(),
    )
  )
  await asyncio.wait_for(both_received.wait(), timeout=1)
  await asyncio.sleep(0)
  assert websocket.sent_text == []

  release_redis.set()
  with pytest.raises(agent_api.WebSocketDisconnect):
    await asyncio.wait_for(task, timeout=1)
  controls = [
    MarketStreamControl.model_validate_json(payload)
    for payload in websocket.sent_text
  ]
  assert [control.sequence for control in controls] == [1, 2]
  assert all(control.type is MarketControlType.ACK for control in controls)


@pytest.mark.asyncio
async def test_idle_frame_crossing_session_expiry_is_not_committed_or_acked(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  started_at = datetime(2026, 8, 19, tzinfo=timezone.utc)
  observed_times = iter(
    [started_at, started_at + timedelta(seconds=2)]
  )
  batch = _commit_item(1, b"").batch

  class IdleWebSocket:
    def __init__(self) -> None:
      self.sent_text = []

    async def receive(self):
      await asyncio.sleep(0)
      return {"type": "websocket.receive", "bytes": batch.to_bytes()}

    async def send_text(self, payload):
      self.sent_text.append(payload)

  class RecordingStore:
    def __init__(self) -> None:
      self.calls = 0

    async def write_batch(self, _batch, _payload):
      self.calls += 1
      return SimpleNamespace(sequence=1)

  async def ensure_device_active(_device_id):
    return None

  store = RecordingStore()
  websocket = IdleWebSocket()
  monkeypatch.setattr(agent_api, "utcnow", lambda: next(observed_times))
  monkeypatch.setattr(agent_api, "_ensure_device_active", ensure_device_active)
  monkeypatch.setattr(agent_api, "market_stream_store", store)

  with pytest.raises(agent_api.AuthError) as error:
    await agent_api._run_market_commit_pipeline(
      websocket,
      stream_id="stream-1",
      device_id="device-1",
      session_expires_at=started_at + timedelta(seconds=1),
      commit_state=agent_api._MarketCommitState(),
    )

  assert error.value.code == "UNAUTHENTICATED"
  assert store.calls == 0
  assert websocket.sent_text == []


@pytest.mark.asyncio
async def test_idle_frame_after_device_revocation_is_not_committed(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  batch = _commit_item(1, b"").batch
  revoked = False
  device_checks = 0

  class IdleWebSocket:
    def __init__(self) -> None:
      self.sent_text = []

    async def receive(self):
      nonlocal revoked
      await asyncio.sleep(0)
      revoked = True
      return {"type": "websocket.receive", "bytes": batch.to_bytes()}

    async def send_text(self, payload):
      self.sent_text.append(payload)

  class RecordingStore:
    def __init__(self) -> None:
      self.calls = 0

    async def write_batch(self, _batch, _payload):
      self.calls += 1
      return SimpleNamespace(sequence=1)

  async def ensure_device_active(_device_id):
    nonlocal device_checks
    device_checks += 1
    if revoked:
      raise agent_api.AuthError("UNAUTHENTICATED", "Agent 设备已撤销")

  store = RecordingStore()
  websocket = IdleWebSocket()
  monkeypatch.setattr(agent_api, "_ensure_device_active", ensure_device_active)
  monkeypatch.setattr(agent_api, "market_stream_store", store)
  monkeypatch.setattr(
    agent_api,
    "MARKET_STREAM_DEVICE_REVALIDATE_SECONDS",
    0.0,
  )

  with pytest.raises(agent_api.AuthError) as error:
    await agent_api._run_market_commit_pipeline(
      websocket,
      stream_id="stream-1",
      device_id="device-1",
      session_expires_at=agent_api.utcnow() + timedelta(minutes=5),
      commit_state=agent_api._MarketCommitState(),
    )

  assert error.value.code == "UNAUTHENTICATED"
  assert device_checks == 2
  assert store.calls == 0
  assert websocket.sent_text == []
