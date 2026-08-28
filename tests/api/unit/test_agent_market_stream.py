import asyncio
import threading
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
from starlette.websockets import WebSocketState


def _market_lease(device_id: str = "device-1") -> agent_api.MarketSessionLease:
  return agent_api.MarketSessionLease(
    device_id=device_id,
    api_instance_id="api-instance-1",
    agent_session_id="agent-session-1",
  )


def _authenticated_session() -> SimpleNamespace:
  return SimpleNamespace(
    device=SimpleNamespace(id="device-1"),
    expires_at=agent_api.utcnow() + timedelta(minutes=5),
  )


class FakeWebSocket:
  def __init__(self, batch: MarketStreamBatch) -> None:
    self.scope = {"subprotocols": ["quantx.market.v2"]}
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
        "capabilities": ["market-data", "data-only"],
        "agent_session_id": "agent-session-1",
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

  async def write_batch(
    self,
    _batch,
    _payload,
    *,
    received_at,
    allow_uncertain_retry=False,
  ):
    del allow_uncertain_retry
    del received_at
    raise ConnectionError("redis unavailable")

  async def mark_offline(self, stream_id, *, reason):
    self.offline.append((stream_id, reason))
    return True


class HangingStore(FailingStore):
  async def write_batch(
    self,
    _batch,
    _payload,
    *,
    received_at,
    allow_uncertain_retry=False,
  ):
    del allow_uncertain_retry
    del received_at
    await asyncio.Event().wait()

  async def mark_offline(self, stream_id, *, reason):
    del stream_id, reason
    await asyncio.Event().wait()


class SuccessfulStore(FailingStore):
  async def write_batch(self, batch, _payload, *, received_at):
    del received_at
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

  async def market_lease(device_id: str):
    nonlocal calls
    assert device_id == "device-1"
    calls += 1
    return _market_lease(device_id) if calls >= 3 else None

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "market_lease",
    market_lease,
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

  async def market_lease(device_id: str):
    nonlocal calls
    assert device_id == "standby-device"
    calls += 1
    return None

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "market_lease",
    market_lease,
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
async def test_market_auth_accepts_valid_device_token_without_control_token_coupling(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  batch = MarketStreamBatch(
    stream_id="placeholder",
    sequence=1,
    kind=MarketBatchKind.SNAPSHOT,
    captured_at=datetime.now(timezone.utc),
    instrument_count=1,
    universe_codes=("600000.SH",),
    data={"600000.SH": {"lastPrice": 10.0}},
  )
  websocket = FakeWebSocket(batch)

  async def authenticate(_envelope):
    return _authenticated_session()

  async def market_lease(device_id: str):
    return _market_lease(device_id)

  device_checked = asyncio.Event()

  async def ensure_device_active(*_args, **_kwargs):
    device_checked.set()
    raise agent_api.AuthError("UNAUTHENTICATED", "controlled stop")

  monkeypatch.setattr(agent_api, "_authenticate", authenticate)
  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "market_lease",
    market_lease,
  )
  monkeypatch.setattr(
    agent_api,
    "_ensure_device_active",
    ensure_device_active,
  )

  await agent_api.agent_market_websocket(websocket)

  assert device_checked.is_set()
  result = AgentEnvelope.model_validate_json(websocket.sent_text[0])
  assert result.message_type is AgentMessageType.AUTH_RESULT
  assert result.payload["reason"] == "controlled stop"
  assert websocket.closed[-1][0] == 4401


@pytest.mark.asyncio
async def test_market_auth_rejects_another_control_session_id(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  batch = MarketStreamBatch(
    stream_id="placeholder",
    sequence=1,
    kind=MarketBatchKind.SNAPSHOT,
    captured_at=datetime.now(timezone.utc),
    instrument_count=1,
    universe_codes=("600000.SH",),
    data={"600000.SH": {"lastPrice": 10.0}},
  )

  class MismatchedSessionWebSocket(FakeWebSocket):
    async def receive_text(self):
      envelope = AgentEnvelope.model_validate_json(await super().receive_text())
      envelope.payload["agent_session_id"] = "another-session"
      return envelope.model_dump_json()

  websocket = MismatchedSessionWebSocket(batch)

  async def authenticate(_envelope):
    return _authenticated_session()

  async def market_lease(device_id: str):
    return _market_lease(device_id)

  async def unexpected_device_check(*_args, **_kwargs):
    raise AssertionError("session mismatch must fail before device validation")

  monkeypatch.setattr(agent_api, "_authenticate", authenticate)
  monkeypatch.setattr(agent_api.agent_connection_hub, "market_lease", market_lease)
  monkeypatch.setattr(agent_api, "_ensure_device_active", unexpected_device_check)

  await agent_api.agent_market_websocket(websocket)

  result = AgentEnvelope.model_validate_json(websocket.sent_text[0])
  assert result.message_type is AgentMessageType.AUTH_RESULT
  assert result.payload["accepted"] is False
  assert websocket.closed[-1][0] == 4401


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
    universe_codes=("600000.SH",),
    data={"600000.SH": {"lastPrice": 10.0}},
  )
  websocket = FakeWebSocket(batch)
  store = FailingStore()

  async def authenticate(_envelope):
    return _authenticated_session()

  async def market_lease(device_id):
    return _market_lease(device_id)

  async def ensure_device_active(_device_id, *, lease=None):
    assert lease == _market_lease(_device_id)
    return None

  original_registry = agent_api._market_connections
  monkeypatch.setattr(agent_api, "_authenticate", authenticate)
  monkeypatch.setattr(agent_api, "_ensure_device_active", ensure_device_active)
  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "market_lease",
    market_lease,
  )
  monkeypatch.setattr(agent_api, "market_stream_store", store)
  monkeypatch.setattr(agent_api, "MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS", 0.05)
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
    universe_codes=("600000.SH",),
    data={"600000.SH": {"lastPrice": 10.0}},
  )
  websocket = FakeWebSocket(batch)
  store = HangingStore()
  registry = agent_api._MarketConnectionRegistry()

  async def authenticate(_envelope):
    return _authenticated_session()

  async def market_lease(device_id):
    return _market_lease(device_id)

  async def ensure_device_active(_device_id, *, lease=None):
    assert lease == _market_lease(_device_id)
    return None

  monkeypatch.setattr(agent_api, "_authenticate", authenticate)
  monkeypatch.setattr(agent_api, "_ensure_device_active", ensure_device_active)
  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "market_lease",
    market_lease,
  )
  monkeypatch.setattr(agent_api, "market_stream_store", store)
  monkeypatch.setattr(agent_api, "_market_connections", registry)
  monkeypatch.setattr(
    agent_api,
    "MARKET_STREAM_REDIS_COMMIT_TIMEOUT_SECONDS",
    0.01,
  )
  monkeypatch.setattr(agent_api, "MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS", 0.05)
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
    universe_codes=("600000.SH",),
    data={"600000.SH": {"lastPrice": 10.0}},
  )
  websocket = HangingAckWebSocket(batch)
  store = SuccessfulStore()
  registry = agent_api._MarketConnectionRegistry()

  async def authenticate(_envelope):
    return _authenticated_session()

  async def market_lease(device_id):
    return _market_lease(device_id)

  async def ensure_device_active(_device_id, *, lease=None):
    assert lease == _market_lease(_device_id)
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
    "market_lease",
    market_lease,
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
      kind=(MarketBatchKind.SNAPSHOT if sequence == 1 else MarketBatchKind.DELTA),
      captured_at=datetime.now(timezone.utc),
      instrument_count=1,
      universe_codes=("600000.SH",) if sequence == 1 else (),
      data={"600000.SH": {"lastPrice": 10.0}},
    ),
    payload=payload,
    received_at=datetime.now(timezone.utc),
    received_monotonic=agent_api.time.monotonic(),
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
async def test_market_commit_buffer_balances_queue_task_accounting() -> None:
  buffer = agent_api._MarketCommitBuffer(capacity=1, max_bytes=8)
  item = _commit_item(1, b"1111")
  await buffer.put(item)
  assert await buffer.get() is item
  await buffer.complete(item)

  disconnect = agent_api.WebSocketDisconnect(code=1000)
  await buffer.close(disconnect)
  closed = await buffer.get()
  assert isinstance(closed, agent_api._MarketCommitQueueClosed)
  buffer.complete_closed()

  await asyncio.wait_for(buffer.join(), timeout=1)


@pytest.mark.asyncio
async def test_large_market_frame_decode_keeps_event_loop_schedulable(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  batch = _commit_item(1, b"").batch
  large_payload = b"x" * (agent_api.MARKET_STREAM_DECODE_OFFLOAD_BYTES + 1)
  decode_started = threading.Event()
  release_decode = threading.Event()
  decode_thread_ids: list[int] = []
  loop_progressed = asyncio.Event()

  def controlled_decode(payload: bytes) -> MarketStreamBatch:
    assert payload is large_payload
    decode_thread_ids.append(threading.get_ident())
    decode_started.set()
    assert release_decode.wait(timeout=2)
    return batch

  class LargeFrameWebSocket:
    def __init__(self) -> None:
      self.receive_count = 0

    async def receive(self):
      self.receive_count += 1
      if self.receive_count == 1:
        return {"type": "websocket.receive", "bytes": large_payload}
      return {"type": "websocket.disconnect", "code": 1000}

  async def ensure_device_active(_device_id):
    return None

  async def heartbeat() -> None:
    while not decode_started.is_set():
      await asyncio.sleep(0)
    loop_progressed.set()

  monkeypatch.setattr(
    agent_api.MarketStreamBatch,
    "from_bytes",
    staticmethod(controlled_decode),
  )
  monkeypatch.setattr(agent_api, "_ensure_device_active", ensure_device_active)
  buffer = agent_api._MarketCommitBuffer()
  heartbeat_task = asyncio.create_task(heartbeat())
  receiver = asyncio.create_task(
    agent_api._receive_market_batches(
      LargeFrameWebSocket(),
      stream_id="stream-1",
      device_id="device-1",
      buffer=buffer,
    )
  )
  try:
    await asyncio.wait_for(loop_progressed.wait(), timeout=1)
    assert len(decode_thread_ids) == 1
    assert decode_thread_ids[0] != threading.get_ident()
  finally:
    release_decode.set()
  await asyncio.wait_for(heartbeat_task, timeout=1)
  await asyncio.wait_for(receiver, timeout=1)

  queued = await buffer.get()
  assert isinstance(queued, agent_api._MarketCommitItem)
  assert queued.batch is batch
  assert queued.payload is large_payload
  assert buffer.buffered_bytes == len(large_payload)
  await buffer.complete(queued)
  closed = await buffer.get()
  assert isinstance(closed, agent_api._MarketCommitQueueClosed)
  buffer.complete_closed()
  await asyncio.wait_for(buffer.join(), timeout=1)
  assert buffer.buffered_bytes == 0


@pytest.mark.asyncio
async def test_large_market_frame_decode_failure_releases_reservations(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  payload = b"x" * (agent_api.MARKET_STREAM_DECODE_OFFLOAD_BYTES + 1)

  class InvalidLargeFrameWebSocket:
    async def receive(self):
      return {"type": "websocket.receive", "bytes": payload}

  async def ensure_device_active(_device_id):
    return None

  def fail_decode(_payload: bytes) -> MarketStreamBatch:
    raise ValueError("controlled large-frame decode failure")

  monkeypatch.setattr(
    agent_api.MarketStreamBatch,
    "from_bytes",
    staticmethod(fail_decode),
  )
  monkeypatch.setattr(agent_api, "_ensure_device_active", ensure_device_active)
  buffer = agent_api._MarketCommitBuffer()

  with pytest.raises(ValueError, match="controlled large-frame decode failure"):
    await agent_api._receive_market_batches(
      InvalidLargeFrameWebSocket(),
      stream_id="stream-1",
      device_id="device-1",
      buffer=buffer,
    )

  assert buffer.buffered_batches == 0
  assert buffer.buffered_bytes == 0


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

    async def write_batch(self, batch, _payload, *, received_at):
      assert received_at.tzinfo is not None
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
    MarketStreamControl.model_validate_json(payload) for payload in websocket.sent_text
  ]
  assert [control.sequence for control in controls] == [1, 2]
  assert all(control.type is MarketControlType.ACK for control in controls)


@pytest.mark.asyncio
async def test_market_committer_retries_transient_redis_failure_in_place(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  item = _commit_item(1, b"payload")
  buffer = agent_api._MarketCommitBuffer()
  await buffer.put(item)
  ack_sent = asyncio.Event()

  class WebSocket:
    async def send_text(self, _payload):
      ack_sent.set()

  class FlakyStore:
    def __init__(self) -> None:
      self.calls = 0

    async def write_batch(
      self,
      batch,
      _payload,
      *,
      received_at,
      allow_uncertain_retry=False,
    ):
      del allow_uncertain_retry
      del received_at
      self.calls += 1
      if self.calls == 1:
        raise ConnectionError("redis reconnecting")
      return SimpleNamespace(sequence=batch.sequence)

  store = FlakyStore()
  monkeypatch.setattr(agent_api, "MARKET_STREAM_REDIS_COMMIT_TIMEOUT_SECONDS", 0.1)
  committer = asyncio.create_task(
    agent_api._commit_market_batches(
      WebSocket(),
      stream_id="stream-1",
      buffer=buffer,
      commit_state=agent_api._MarketCommitState(),
      store=store,
    )
  )
  try:
    await asyncio.wait_for(ack_sent.wait(), timeout=1)
    assert store.calls == 2
  finally:
    committer.cancel()
    await asyncio.gather(committer, return_exceptions=True)


@pytest.mark.asyncio
async def test_market_committer_treats_ack_after_peer_close_as_disconnect(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  item = _commit_item(1, b"payload")
  buffer = agent_api._MarketCommitBuffer()
  await buffer.put(item)

  class DisconnectedWebSocket:
    client_state = WebSocketState.DISCONNECTED
    application_state = WebSocketState.CONNECTED

    async def send_text(self, _payload):
      raise AssertionError("a disconnected WebSocket must not be written")

  class Store:
    async def write_batch(self, batch, _payload, *, received_at):
      assert received_at.tzinfo is not None
      return SimpleNamespace(sequence=batch.sequence)

  monkeypatch.setattr(agent_api, "market_stream_store", Store())

  with pytest.raises(agent_api.WebSocketDisconnect):
    await agent_api._commit_market_batches(
      DisconnectedWebSocket(),
      stream_id="stream-1",
      buffer=buffer,
      commit_state=agent_api._MarketCommitState(),
    )


@pytest.mark.asyncio
async def test_market_send_classifies_disconnect_racing_with_ack_write() -> None:
  class RacingWebSocket:
    client_state = WebSocketState.CONNECTED
    application_state = WebSocketState.CONNECTED

    async def send_text(self, _payload):
      self.client_state = WebSocketState.DISCONNECTED
      raise RuntimeError(
        "Unexpected ASGI message 'websocket.send', after sending "
        "'websocket.close' or response already completed."
      )

  with pytest.raises(agent_api.WebSocketDisconnect):
    await agent_api._send_market_text(RacingWebSocket(), "ack")


@pytest.mark.asyncio
async def test_established_market_session_outlives_handshake_token(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  batch = _commit_item(1, b"").batch

  class IdleWebSocket:
    def __init__(self) -> None:
      self.sent_text = []
      self.receive_count = 0

    async def receive(self):
      await asyncio.sleep(0)
      self.receive_count += 1
      if self.receive_count > 1:
        return {"type": "websocket.disconnect", "code": 1000}
      return {"type": "websocket.receive", "bytes": batch.to_bytes()}

    async def send_text(self, payload):
      self.sent_text.append(payload)

  class RecordingStore:
    def __init__(self) -> None:
      self.calls = 0

    async def write_batch(self, _batch, _payload, *, received_at):
      del received_at
      self.calls += 1
      return SimpleNamespace(sequence=1)

  async def ensure_device_active(_device_id):
    return None

  store = RecordingStore()
  websocket = IdleWebSocket()
  monkeypatch.setattr(agent_api, "_ensure_device_active", ensure_device_active)
  monkeypatch.setattr(agent_api, "market_stream_store", store)

  with pytest.raises(agent_api.WebSocketDisconnect):
    await agent_api._run_market_commit_pipeline(
      websocket,
      stream_id="stream-1",
      device_id="device-1",
      commit_state=agent_api._MarketCommitState(),
    )

  assert store.calls == 1
  ack = MarketStreamControl.model_validate_json(websocket.sent_text[0])
  assert ack.type is MarketControlType.ACK
  assert ack.sequence == 1


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

    async def write_batch(self, _batch, _payload, *, received_at):
      del received_at
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
      commit_state=agent_api._MarketCommitState(),
    )

  assert error.value.code == "UNAUTHENTICATED"
  assert device_checks == 2
  assert store.calls == 0
  assert websocket.sent_text == []
