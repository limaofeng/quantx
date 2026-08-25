import asyncio
from contextlib import suppress
from datetime import datetime, timedelta

import pytest
from quantx_api import agent_api
from quantx_contracts import AgentEnvelope, AgentMessageType, ReportAckPayload


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

  async def process_message(device_id, envelope, **_kwargs):
    assert device_id == "device-1"
    assert envelope.message_type is AgentMessageType.DELTA_REPORT
    processed.set()
    return AgentEnvelope(
      message_type=AgentMessageType.REPORT_ACK,
      payload=ReportAckPayload(
        report_message_id=envelope.message_id,
        accepted=True,
      ).model_dump(mode="json"),
    )

  async def ensure_device(_device_id):
    return None

  async def refresh_lease(_device_id, _queue):
    return None

  monkeypatch.setattr(agent_api, "_next_command", hanging_command)
  monkeypatch.setattr(agent_api, "_next_market_data_request", hanging_market_request)
  monkeypatch.setattr(agent_api, "_process_message", process_message)
  monkeypatch.setattr(agent_api, "_ensure_device_active", ensure_device)
  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "refresh_market_device",
    refresh_lease,
  )

  pipeline = asyncio.create_task(
    agent_api._run_agent_control_pipeline(
      websocket,
      device_id="device-1",
      protocol_version="1.1",
      session_expires_at=agent_api.utcnow() + timedelta(minutes=5),
      control_queue=asyncio.Queue(),
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

  async def record_report(device_id, value, *, received_at):
    assert device_id == "device-1"
    assert value is envelope
    assert received_at is not None
    persist_started.set()
    await allow_persist.wait()
    return ReportAckPayload(report_message_id=value.message_id, accepted=True)

  monkeypatch.setattr(agent_api, "_record_report", record_report)
  processor = asyncio.create_task(
    agent_api._process_agent_control_messages(
      device_id="device-1",
      protocol_version="1.1",
      inbound=inbound,
      outbound=outbound,
    )
  )
  writer = asyncio.create_task(
    agent_api._send_agent_control_messages(
      websocket,
      device_id="device-1",
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
  while not websocket.sent:
    await asyncio.sleep(0)
  assert websocket.sent[0].message_type is AgentMessageType.REPORT_ACK

  processor.cancel()
  writer.cancel()
  await asyncio.gather(processor, writer, return_exceptions=True)


@pytest.mark.asyncio
async def test_single_writer_prioritizes_ack_and_serializes_sends() -> None:
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

  writer = asyncio.create_task(
    agent_api._send_agent_control_messages(
      websocket,
      device_id="device-1",
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
async def test_stale_inbound_message_fails_closed() -> None:
  inbound = agent_api._AgentInboundBuffer()
  outbound = agent_api._AgentOutboundBuffer()
  envelope = report_envelope("00000000-0000-4000-8000-000000000005")
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

  with pytest.raises(agent_api._AgentControlPipelineError) as error:
    await agent_api._process_agent_control_messages(
      device_id="device-1",
      protocol_version="1.1",
      inbound=inbound,
      outbound=outbound,
    )
  assert error.value.reason == "inbound_queue_stale"
