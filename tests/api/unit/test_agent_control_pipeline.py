import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from quantx_api import agent_api
from quantx_contracts import AgentEnvelope, AgentMessageType, ReportAckPayload
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError


class FakeWebSocket:
  def __init__(self, *, send_delay: float = 0.0) -> None:
    self.received: asyncio.Queue[str] = asyncio.Queue()
    self.sent: list[AgentEnvelope] = []
    self.send_delay = send_delay
    self.active_sends = 0
    self.max_active_sends = 0

  async def receive_text(self) -> str:
    return await self.received.get()

  async def send_text(self, raw: str) -> None:
    self.active_sends += 1
    self.max_active_sends = max(self.max_active_sends, self.active_sends)
    try:
      if self.send_delay:
        await asyncio.sleep(self.send_delay)
      self.sent.append(AgentEnvelope.model_validate_json(raw))
    finally:
      self.active_sends -= 1


def report_envelope(message_id: str) -> AgentEnvelope:
  return AgentEnvelope(
    message_id=message_id,
    message_type=AgentMessageType.DELTA_REPORT,
    payload={"sequence": 1, "is_complete": False},
  )


def control_session(device_id: str = "device-1") -> agent_api.AgentControlSession:
  return agent_api.AgentControlSession(
    device_id=device_id,
    capabilities={"market-data", "live"},
    authorized_account_ids=frozenset({"account-1"}),
    queue=asyncio.Queue(),
    api_instance_id="api-instance-1",
    agent_session_id="agent-session-1",
    server_connected_at=agent_api.utcnow(),
    remote_address_summary="10.0.0.*",
    revoked=asyncio.Event(),
  )


@pytest.mark.parametrize(
  ("host", "expected"),
  (
    ("10.20.30.40", "10.20.30.*"),
    ("::ffff:10.20.30.40", "10.20.30.*"),
    ("2001:db8:1234:5678::1", "2001:0db8:1234:*"),
    ("untrusted-hostname.example", "unknown"),
  ),
)
def test_remote_address_summary_is_masked(host: str, expected: str) -> None:
  websocket = SimpleNamespace(client=SimpleNamespace(host=host))

  assert agent_api._remote_address_summary(websocket) == expected


def test_agent_capabilities_are_normalized_and_require_one_mode() -> None:
  assert agent_api._normalized_agent_capabilities([" MARKET-DATA ", "LIVE"]) == {
    "market-data",
    "live",
  }

  with pytest.raises(agent_api.AuthError, match="唯一运行模式"):
    agent_api._normalized_agent_capabilities(["market-data"])
  with pytest.raises(agent_api.AuthError, match="唯一运行模式"):
    agent_api._normalized_agent_capabilities(["live", "paper"])


def test_source_to_receive_accepts_naive_persisted_utc(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  observed = []
  envelope = AgentEnvelope(
    message_id="00000000-0000-4000-8000-000000000000",
    message_type=AgentMessageType.DELTA_REPORT,
    payload={
      "sequence": 1,
      "is_complete": False,
      "source_event_at": "2026-08-26T01:00:00Z",
    },
  )

  def observe(**values):
    observed.append(values)

  monkeypatch.setattr(agent_api, "_observe_agent_control_stage", observe)
  agent_api._observe_source_to_receive(
    "device-1",
    envelope,
    datetime(2026, 8, 26, 1, 0, 3),
  )

  assert observed[0]["stage"] == "source_to_socket_receive"
  assert observed[0]["duration"] == 3


@pytest.mark.asyncio
async def test_database_pollers_do_not_block_report_reception(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  websocket = FakeWebSocket()
  processed = asyncio.Event()
  release_pollers = asyncio.Event()

  async def hanging_command(*_args, **_kwargs):
    await release_pollers.wait()
    return None

  async def hanging_market_request(*_args, **_kwargs):
    await release_pollers.wait()
    return None

  async def process_message(session, envelope, **_kwargs):
    assert session.device_id == "device-1"
    assert envelope.message_type is AgentMessageType.DELTA_REPORT
    processed.set()
    return AgentEnvelope(
      message_type=AgentMessageType.REPORT_ACK,
      payload=ReportAckPayload(
        report_message_id=envelope.message_id,
        accepted=True,
      ).model_dump(mode="json"),
    )

  async def wait_until_revoked(*_args, **_kwargs):
    await asyncio.sleep(60)
    return False

  async def refresh_lease(_session):
    return None

  monkeypatch.setattr(agent_api, "_next_command", hanging_command)
  monkeypatch.setattr(agent_api, "_next_market_data_request", hanging_market_request)
  monkeypatch.setattr(agent_api, "_process_message", process_message)
  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "wait_until_revoked",
    wait_until_revoked,
  )
  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "refresh_market_device",
    refresh_lease,
  )

  async def is_connected(*_args, **_kwargs):
    return True

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "is_connected",
    is_connected,
  )

  pipeline = asyncio.create_task(
    agent_api._run_agent_control_pipeline(
      websocket,
      control_session=control_session(),
      protocol_version="1.1",
      session_expires_at=agent_api.utcnow() + timedelta(minutes=5),
    )
  )
  await websocket.received.put(
    report_envelope("00000000-0000-4000-8000-000000000001").model_dump_json()
  )

  await asyncio.wait_for(processed.wait(), timeout=0.5)
  pipeline.cancel()
  with suppress(asyncio.CancelledError):
    await pipeline


@pytest.mark.asyncio
async def test_report_ack_waits_for_durable_recording(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  inbound = agent_api._AgentInboundBuffer()
  outbound = agent_api._AgentOutboundBuffer()
  websocket = FakeWebSocket()
  persist_started = asyncio.Event()
  allow_persist = asyncio.Event()
  envelope = report_envelope("00000000-0000-4000-8000-000000000002")

  async def record_report(session, value, *, received_at):
    assert session.device_id == "device-1"
    assert value is envelope
    assert received_at is not None
    persist_started.set()
    await allow_persist.wait()
    return ReportAckPayload(report_message_id=value.message_id, accepted=True)

  monkeypatch.setattr(agent_api, "_record_report", record_report)

  async def is_connected(*_args, **_kwargs):
    return True

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "is_connected",
    is_connected,
  )
  processor = asyncio.create_task(
    agent_api._process_agent_control_messages(
      control_session=control_session(),
      protocol_version="1.1",
      inbound=inbound,
      outbound=outbound,
      database_state=agent_api._AgentDatabaseState("device-1"),
    )
  )
  writer = asyncio.create_task(
    agent_api._send_agent_control_messages(
      websocket,
      control_session=control_session(),
      outbound=outbound,
    )
  )
  await inbound.put(
    agent_api._AgentInboundItem(
      envelope=envelope,
      received_at=agent_api.utcnow(),
      received_monotonic=agent_api.time.monotonic(),
      frame_bytes=len(envelope.model_dump_json().encode("utf-8")),
    )
  )

  await asyncio.wait_for(persist_started.wait(), timeout=0.5)
  await asyncio.sleep(0)
  assert websocket.sent == []

  allow_persist.set()
  await asyncio.wait_for(_wait_for_sent(websocket, 1), timeout=0.5)
  assert websocket.sent[0].message_type is AgentMessageType.REPORT_ACK

  processor.cancel()
  writer.cancel()
  await asyncio.gather(processor, writer, return_exceptions=True)


async def _wait_for_sent(websocket: FakeWebSocket, count: int) -> None:
  while len(websocket.sent) < count:
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_single_writer_prioritizes_ack_and_serializes_sends(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  outbound = agent_api._AgentOutboundBuffer()
  websocket = FakeWebSocket(send_delay=0.01)
  command = AgentEnvelope(
    message_id="00000000-0000-4000-8000-000000000003",
    message_type=AgentMessageType.COMMAND,
    payload={"command_kind": "PLACE_ORDER"},
  )
  ack = AgentEnvelope(
    message_type=AgentMessageType.REPORT_ACK,
    payload={
      "report_message_id": "00000000-0000-4000-8000-000000000004",
      "accepted": True,
    },
  )
  await agent_api._enqueue_agent_outbound("device-1", outbound, command)
  await agent_api._enqueue_agent_outbound("device-1", outbound, ack)

  async def is_connected(*_args, **_kwargs):
    return True

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "is_connected",
    is_connected,
  )

  async def assert_trade_delivery_session(*_args, **_kwargs):
    return None

  monkeypatch.setattr(
    agent_api,
    "_assert_trade_delivery_session",
    assert_trade_delivery_session,
  )

  writer = asyncio.create_task(
    agent_api._send_agent_control_messages(
      websocket,
      control_session=control_session(),
      outbound=outbound,
    )
  )
  while len(websocket.sent) < 2:
    await asyncio.sleep(0)
  writer.cancel()
  await asyncio.gather(writer, return_exceptions=True)

  assert [item.message_type for item in websocket.sent] == [
    AgentMessageType.REPORT_ACK,
    AgentMessageType.COMMAND,
  ]
  assert websocket.max_active_sends == 1


@pytest.mark.asyncio
async def test_outbound_buffer_reserves_ack_capacity_and_deduplicates_work() -> None:
  outbound = agent_api._AgentOutboundBuffer(capacity=4, ack_reserve=1)
  commands = [
    AgentEnvelope(
      message_id=f"00000000-0000-4000-8000-{index:012d}",
      message_type=AgentMessageType.COMMAND,
      payload={"command_kind": "PLACE_ORDER", "index": index},
    )
    for index in range(1, 4)
  ]
  for command in commands:
    assert await outbound.put(
      command,
      priority=2,
      dedup_key=command.message_id,
    )
  assert not await outbound.put(
    commands[0],
    priority=2,
    dedup_key=commands[0].message_id,
  )

  ack = AgentEnvelope(
    message_type=AgentMessageType.HEARTBEAT_ACK,
    payload={"heartbeat_message_id": "heartbeat-1"},
  )
  assert await asyncio.wait_for(
    outbound.put(ack, priority=0, protocol_reply=True),
    timeout=0.1,
  )
  assert outbound.qsize() == 4


@pytest.mark.asyncio
async def test_inbound_buffer_coalesces_report_retries_until_processing_completes() -> (
  None
):
  inbound = agent_api._AgentInboundBuffer()
  envelope = report_envelope("00000000-0000-4000-8000-000000000005")

  def item() -> agent_api._AgentInboundItem:
    return agent_api._AgentInboundItem(
      envelope=envelope,
      received_at=agent_api.utcnow(),
      received_monotonic=agent_api.time.monotonic(),
      frame_bytes=1,
      dedup_key=envelope.message_id,
    )

  assert await inbound.put(item())
  processing = await inbound.get()
  assert not await inbound.put(item())

  await inbound.complete(processing)
  assert await inbound.put(item())


@pytest.mark.asyncio
async def test_stale_inbound_message_is_processed_without_disconnect(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  inbound = agent_api._AgentInboundBuffer()
  outbound = agent_api._AgentOutboundBuffer()
  processed = asyncio.Event()
  envelope = report_envelope("00000000-0000-4000-8000-000000000005")

  async def process_message(*_args, **_kwargs):
    processed.set()
    return None

  monkeypatch.setattr(agent_api, "_process_message", process_message)
  await inbound.put(
    agent_api._AgentInboundItem(
      envelope=envelope,
      received_at=agent_api.utcnow(),
      received_monotonic=(
        agent_api.time.monotonic() - agent_api.AGENT_CONTROL_MAX_QUEUE_AGE_SECONDS - 1
      ),
      frame_bytes=1,
    )
  )

  processor = asyncio.create_task(
    agent_api._process_agent_control_messages(
      control_session=control_session(),
      protocol_version="1.1",
      inbound=inbound,
      outbound=outbound,
      database_state=agent_api._AgentDatabaseState("device-1"),
    )
  )
  await asyncio.wait_for(processed.wait(), timeout=0.5)
  processor.cancel()
  await asyncio.gather(processor, return_exceptions=True)


@pytest.mark.asyncio
async def test_transient_database_timeout_sends_no_ack_and_pauses_pollers(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  inbound = agent_api._AgentInboundBuffer()
  outbound = agent_api._AgentOutboundBuffer()
  failed = asyncio.Event()
  state = agent_api._AgentDatabaseState("device-1")
  envelope = report_envelope("00000000-0000-4000-8000-000000000006")

  async def process_message(*_args, **_kwargs):
    failed.set()
    raise SQLAlchemyTimeoutError("pool exhausted")

  monkeypatch.setattr(agent_api, "_process_message", process_message)
  await inbound.put(
    agent_api._AgentInboundItem(
      envelope=envelope,
      received_at=agent_api.utcnow(),
      received_monotonic=agent_api.time.monotonic(),
      frame_bytes=1,
      dedup_key=envelope.message_id,
    )
  )

  processor = asyncio.create_task(
    agent_api._process_agent_control_messages(
      control_session=control_session(),
      protocol_version="1.1",
      inbound=inbound,
      outbound=outbound,
      database_state=state,
    )
  )
  await asyncio.wait_for(failed.wait(), timeout=0.5)
  while state.ready.is_set():
    await asyncio.sleep(0)

  assert outbound.qsize() == 0
  assert state.consecutive_failures == 1
  assert not state.ready.is_set()
  processor.cancel()
  await asyncio.gather(processor, return_exceptions=True)
