"""History work and heartbeats share transport without blocking each other."""

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_contracts.history_session import (
  HISTORY_SESSION_SUBPROTOCOL,
  HistoryAuthResult,
  HistoryHeartbeat,
  HistoryHeartbeatAck,
  HistoryRequest,
  HistoryRequestRemoved,
)
from quantx_qmt_agent.history_session import HistorySessionClient

PAYLOAD = {
  "operation": "bars",
  "stock_list": ["000001.SZ"],
  "periods": ["1d"],
  "start_time": "20250102",
  "end_time": "20250102",
}


class Socket:
  def __init__(self):
    self.session = uuid4()
    self.incoming = asyncio.Queue()
    self.incoming.put_nowait(
      HistoryAuthResult(session_id=self.session).model_dump_json()
    )
    self.sent = []
    self.beat = asyncio.Event()

  async def send(self, raw):
    self.sent.append(json.loads(raw))
    if self.sent[-1].get("type") == "HEARTBEAT":
      await self.incoming.put(
        HistoryHeartbeatAck(session_id=self.session).model_dump_json()
      )
      self.beat.set()

  async def recv(self):
    return await self.incoming.get()


@pytest.fixture
def connection():
  socket = Socket()
  options = {}

  @asynccontextmanager
  async def connect(url, **kwargs):
    options.update(url=url, **kwargs)
    yield socket

  client = HistorySessionClient(
    api_url="https://example.test",
    device_id=str(uuid4()),
    capabilities=["market-data"],
    connect=connect,
    token=AsyncMock(return_value="history-token"),
    health=lambda: HistoryHeartbeat(xtdata_ready=True),
    handle=AsyncMock(),
    reset=AsyncMock(),
  )
  return client, socket, options


async def test_auth_transport_and_heartbeat_continue_while_handler_waits(connection):
  client, socket, options = connection
  entered, release = asyncio.Event(), asyncio.Event()

  async def handle(_):
    entered.set()
    await release.wait()

  client.handle = handle
  await socket.incoming.put(
    HistoryRequest(
      request_id=uuid4(), payload=PAYLOAD, unit_count=1, completed_units=0
    ).model_dump_json()
  )
  task = asyncio.create_task(client.run())
  try:
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.wait_for(socket.beat.wait(), 1)
    # Reader can acknowledge even though the work consumer is still waiting.
    async with asyncio.timeout(1):
      while client.last_ack_monotonic is None:
        await asyncio.sleep(0)
    assert client.session_id == socket.session
    auth = AgentEnvelope.model_validate(socket.sent[0])
    assert auth.message_type == AgentMessageType.AUTH
    assert auth.payload["access_token"] == "history-token"
    assert options["url"] == "wss://example.test/ws/agent/history"
    assert options["subprotocols"] == [HISTORY_SESSION_SUBPROTOCOL]
    assert options["max_queue"] == 8
  finally:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
      await task
  assert client.session_id is None
  assert client.last_ack_monotonic is None


async def test_removed_request_allows_replacement_without_third_slot(connection):
  client, socket, _ = connection
  first, second, third = uuid4(), uuid4(), uuid4()
  received = []

  async def handle(message):
    received.append(message)
    if len(received) == 4:
      raise RuntimeError("test complete")

  client.handle = handle
  for message in [
    HistoryRequest(request_id=first, payload=PAYLOAD, unit_count=1, completed_units=0),
    HistoryRequest(request_id=second, payload=PAYLOAD, unit_count=1, completed_units=0),
    HistoryRequestRemoved(request_id=first),
    HistoryRequest(request_id=third, payload=PAYLOAD, unit_count=1, completed_units=0),
  ]:
    await socket.incoming.put(message.model_dump_json())
  with pytest.raises(RuntimeError, match="test complete"):
    await asyncio.wait_for(client.run(), 1)
  assert len(received) == 4
  assert client.session_id is None


async def test_old_connection_ack_rejected(connection):
  client, socket, _ = connection
  await socket.incoming.put(HistoryHeartbeatAck(session_id=uuid4()).model_dump_json())
  with pytest.raises(ValueError, match="unexpected history heartbeat"):
    await asyncio.wait_for(client.run(), 1)
  assert client.session_id is None


async def test_work_overload_disconnects_instead_of_blocking_reader(connection):
  client, socket, _ = connection
  for _ in range(10):
    await socket.incoming.put(
      HistoryRequest(
        request_id=uuid4(), payload=PAYLOAD, unit_count=1, completed_units=0
      ).model_dump_json()
    )
  with pytest.raises(asyncio.QueueFull):
    await asyncio.wait_for(client.run(), 1)
  assert client.session_id is None


def test_request_progress_must_fit_plan():
  with pytest.raises(ValueError, match="completion exceeds plan"):
    HistoryRequest(request_id=uuid4(), payload=PAYLOAD, unit_count=1, completed_units=2)


async def test_grant_payload_cannot_change_canonical_request(connection):
  from datetime import datetime, timedelta, timezone

  from quantx_contracts.collection_permit import CollectionPermit, CollectionUnit
  from quantx_contracts.history_session import HistoryGrant
  from quantx_qmt_agent.historical_worker import historical_work_units

  client, socket, _ = connection
  request_id = uuid4()
  unit = historical_work_units(PAYLOAD)[0]
  now = datetime.now(timezone.utc)
  permit = CollectionPermit(
    permit_id=uuid4(),
    device_id=client.device_id,
    owner_epoch=1,
    unit=CollectionUnit.from_payload(str(request_id), 0, unit),
    issued_at=now,
    expires_at=now + timedelta(seconds=15),
  )
  await socket.incoming.put(
    HistoryRequest(
      request_id=request_id, payload=PAYLOAD, unit_count=1, completed_units=0
    ).model_dump_json()
  )
  await socket.incoming.put(
    HistoryGrant(
      permit=permit, state="ISSUED", unit_payload={**unit, "stock_list": ["600000.SH"]}
    ).model_dump_json()
  )
  with pytest.raises(ValueError, match="canonical plan"):
    await asyncio.wait_for(client.run(), 1)
  assert client.handle.await_count == 1  # request only; no native grant dispatched


async def test_client_on_real_local_websocket():
  import websockets
  from quantx_qmt_agent.runtime import _connect_websocket

  device, session, request = uuid4(), uuid4(), uuid4()
  acknowledged = asyncio.Event()
  delivered = asyncio.Event()

  async def server(socket):
    auth = AgentEnvelope.model_validate_json(await socket.recv())
    assert auth.payload["device_id"] == str(device)
    assert socket.subprotocol == HISTORY_SESSION_SUBPROTOCOL
    await socket.send(HistoryAuthResult(session_id=session).model_dump_json())
    heartbeat = HistoryHeartbeat.model_validate_json(await socket.recv())
    assert heartbeat.xtdata_ready
    await socket.send(HistoryHeartbeatAck(session_id=session).model_dump_json())
    acknowledged.set()
    await socket.send(
      HistoryRequest(
        request_id=request, payload=PAYLOAD, unit_count=1, completed_units=0
      ).model_dump_json()
    )
    await socket.wait_closed()

  async def handle(message):
    assert message.request_id == request
    delivered.set()

  async with websockets.serve(
    server, "127.0.0.1", 0, subprotocols=[HISTORY_SESSION_SUBPROTOCOL]
  ) as listener:
    port = listener.sockets[0].getsockname()[1]
    client = HistorySessionClient(
      api_url=f"http://127.0.0.1:{port}",
      device_id=str(device),
      capabilities=["market-data"],
      connect=_connect_websocket,
      token=AsyncMock(return_value="local-test-token"),
      health=lambda: HistoryHeartbeat(xtdata_ready=True),
      handle=handle,
      reset=AsyncMock(),
    )
    task = asyncio.create_task(client.run())
    try:
      await asyncio.wait_for(acknowledged.wait(), 2)
      await asyncio.wait_for(delivered.wait(), 2)
      assert client.last_ack_monotonic is not None
    finally:
      task.cancel()
      with pytest.raises(asyncio.CancelledError):
        await task
    assert client.session_id is None


async def test_repeated_cancellation_joins_native_before_reset_and_reconnect(
  connection,
):
  import threading

  from quantx_qmt_agent.collection_execution import join_history_thread

  client, socket, _ = connection
  entered, release = threading.Event(), threading.Event()
  ended = False

  def native():
    nonlocal ended
    entered.set()
    if not release.wait(3):
      raise RuntimeError("test did not release native call")
    ended = True

  async def handle(_):
    await join_history_thread(native)

  async def reset():
    assert ended
    assert client._running
    assert client.session_id is None and client.last_ack_monotonic is None

  client.handle, client.reset = handle, AsyncMock(side_effect=reset)
  await socket.incoming.put(
    HistoryRequest(
      request_id=uuid4(), payload=PAYLOAD, unit_count=1, completed_units=0
    ).model_dump_json()
  )
  running = asyncio.create_task(client.run())
  try:
    assert await asyncio.to_thread(entered.wait, 1)
    for _ in range(3):
      running.cancel()
      await asyncio.sleep(0)
      assert not running.done()
      client.reset.assert_not_awaited()
      with pytest.raises(RuntimeError, match="already connected"):
        await client.run()
  finally:
    release.set()
    with pytest.raises(asyncio.CancelledError):
      await running
  client.reset.assert_awaited_once()
  assert not client._running


async def test_authentication_failure_resets_routes_once(connection):
  client, _, _ = connection
  client.token = AsyncMock(side_effect=ConnectionError("token unavailable"))
  with pytest.raises(ConnectionError):
    await client.run()
  client.reset.assert_awaited_once()
  assert not client._running and client.session_id is None
