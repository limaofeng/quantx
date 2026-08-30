import asyncio
import threading
import time
from contextlib import suppress
from datetime import datetime
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
async def test_large_control_frame_parse_runs_off_event_loop(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  envelope = AgentEnvelope(
    message_type=AgentMessageType.DELTA_REPORT,
    payload={
      "sequence": 1,
      "is_complete": False,
      "padding": "x" * agent_api.AGENT_CONTROL_CPU_OFFLOAD_CHARS,
    },
  )
  raw = envelope.model_dump_json()
  original = agent_api._parse_agent_control_frame
  worker_threads: list[int] = []

  def slow_parse(value: str):
    worker_threads.append(threading.get_ident())
    time.sleep(0.05)
    return original(value)

  monkeypatch.setattr(agent_api, "_parse_agent_control_frame", slow_parse)
  parsing = asyncio.create_task(agent_api._parse_agent_control_frame_async(raw))

  await asyncio.sleep(0)
  assert not parsing.done()
  parsed, frame_bytes = await asyncio.wait_for(parsing, timeout=1)
  assert parsed.message_id == envelope.message_id
  assert frame_bytes == len(raw.encode("utf-8"))
  assert len(worker_threads) == 1
  assert worker_threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_large_report_validation_and_hashing_run_off_event_loop(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  envelope = AgentEnvelope(
    message_type=AgentMessageType.DELTA_REPORT,
    payload={
      "sequence": 1,
      "is_complete": False,
      "padding": "x" * agent_api.AGENT_CONTROL_CPU_OFFLOAD_BYTES,
    },
  )
  original = agent_api._prepare_report_persistence
  worker_threads: list[int] = []

  def slow_prepare(device_id: str, value: AgentEnvelope):
    worker_threads.append(threading.get_ident())
    time.sleep(0.05)
    return original(device_id, value)

  monkeypatch.setattr(agent_api, "_prepare_report_persistence", slow_prepare)
  preparing = asyncio.create_task(
    agent_api._prepare_report_persistence_async(
      "device-1",
      envelope,
      frame_bytes=agent_api.AGENT_CONTROL_CPU_OFFLOAD_BYTES,
    )
  )

  await asyncio.sleep(0)
  assert not preparing.done()
  prepared = await asyncio.wait_for(preparing, timeout=1)
  assert len(prepared.payload_hash) == 64
  assert len(prepared.business_idempotency_key) == 64
  assert len(worker_threads) == 1
  assert worker_threads[0] != threading.get_ident()


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

  async def record_report(session, value, *, received_at, frame_bytes):
    assert session.device_id == "device-1"
    assert value is envelope
    assert received_at is not None
    assert frame_bytes > 0
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
async def test_slow_trade_validation_cannot_hold_physical_ack_writer(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  outbound = agent_api._AgentOutboundBuffer()
  websocket = FakeWebSocket()
  session = control_session()
  validation_started = asyncio.Event()
  release_validation = asyncio.Event()
  command = AgentEnvelope(
    message_id="00000000-0000-4000-8000-000000000040",
    message_type=AgentMessageType.COMMAND,
    payload={"command_kind": "PLACE_ORDER"},
  )
  heartbeat_ack = AgentEnvelope(
    message_type=AgentMessageType.HEARTBEAT_ACK,
    payload={"heartbeat_message_id": "heartbeat-1"},
  )

  async def validate(*_args, **_kwargs) -> None:
    validation_started.set()
    await release_validation.wait()

  async def is_connected(*_args, **_kwargs) -> bool:
    return True

  monkeypatch.setattr(agent_api, "_assert_trade_delivery_session", validate)
  monkeypatch.setattr(agent_api.agent_connection_hub, "is_connected", is_connected)
  validator = asyncio.create_task(
    agent_api._enqueue_validated_trade_command(
      control_session=session,
      outbound=outbound,
      envelope=command,
    )
  )
  await asyncio.wait_for(validation_started.wait(), timeout=0.2)
  await agent_api._enqueue_agent_outbound("device-1", outbound, heartbeat_ack)
  writer = asyncio.create_task(
    agent_api._send_agent_control_messages(
      websocket,
      control_session=session,
      outbound=outbound,
    )
  )

  await asyncio.wait_for(_wait_for_sent(websocket, 1), timeout=0.2)
  assert websocket.sent[0].message_type is AgentMessageType.HEARTBEAT_ACK
  assert validator.done() is False

  release_validation.set()
  await validator
  await asyncio.wait_for(_wait_for_sent(websocket, 2), timeout=0.2)
  writer.cancel()
  await asyncio.gather(writer, return_exceptions=True)
  assert websocket.sent[1].message_type is AgentMessageType.COMMAND


@pytest.mark.asyncio
async def test_reconciling_defers_place_delivery_but_still_allows_cancel(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session = control_session()
  device = SimpleNamespace(revoked_at=None)
  heartbeat = SimpleNamespace(
    status="RECONCILING",
    details={
      "apiInstanceId": session.api_instance_id,
      "agentSessionId": session.agent_session_id,
      "marketStreamStatus": "STALE",
    },
  )

  class DatabaseSession:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    async def get(self, model, _identity):
      return device if model is agent_api.AgentDevice else heartbeat

  def evaluate(_heartbeat, *, acceptable_statuses, **_kwargs):
    current = str(heartbeat.status).upper() in acceptable_statuses
    return SimpleNamespace(
      current=current,
      reason_code=("" if current else agent_api.QMT_AGENT_NOT_RECONCILED),
      api_instance_id=session.api_instance_id,
      agent_session_id=session.agent_session_id,
    )

  monkeypatch.setattr(agent_api, "AsyncSessionLocal", DatabaseSession)
  monkeypatch.setattr(agent_api, "evaluate_agent_session", evaluate)
  place = AgentEnvelope(
    message_type=AgentMessageType.COMMAND,
    payload={
      "command_kind": "PLACE_ORDER",
      "account_id": "account-1",
      "execution_mode": "live",
      "side": "BUY",
    },
  )
  cancel = AgentEnvelope(
    message_type=AgentMessageType.CANCEL_COMMAND,
    payload={
      "command_kind": "CANCEL_ORDER",
      "account_id": "account-1",
      "execution_mode": "live",
    },
  )

  with pytest.raises(
    agent_api._TradeCommandDeliveryDeferred,
    match="agent_not_ready_for_command",
  ):
    await agent_api._assert_trade_delivery_session(session, place)
  await agent_api._assert_trade_delivery_session(session, cancel)


@pytest.mark.asyncio
async def test_closed_market_gate_defers_live_risk_increase(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session = control_session()
  device = SimpleNamespace(revoked_at=None)
  heartbeat = SimpleNamespace(
    status="READY",
    details={
      "apiInstanceId": session.api_instance_id,
      "agentSessionId": session.agent_session_id,
      "marketStreamStatus": "STALE",
    },
  )

  class DatabaseSession:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    async def get(self, model, _identity):
      return device if model is agent_api.AgentDevice else heartbeat

  monkeypatch.setattr(agent_api, "AsyncSessionLocal", DatabaseSession)
  monkeypatch.setattr(
    agent_api,
    "evaluate_agent_session",
    lambda *_args, **_kwargs: SimpleNamespace(
      current=True,
      reason_code="",
      api_instance_id=session.api_instance_id,
      agent_session_id=session.agent_session_id,
    ),
  )
  command = AgentEnvelope(
    message_type=AgentMessageType.COMMAND,
    payload={
      "command_kind": "PLACE_ORDER",
      "account_id": "account-1",
      "execution_mode": "live",
      "side": "BUY",
    },
  )

  with pytest.raises(
    agent_api._TradeCommandDeliveryDeferred,
    match="market_stream_not_ready",
  ):
    await agent_api._assert_trade_delivery_session(session, command)


@pytest.mark.asyncio
async def test_risk_gate_dependency_fluctuation_defers_delivery(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session = control_session()
  device = SimpleNamespace(revoked_at=None)
  heartbeat = SimpleNamespace(
    status="READY",
    details={
      "apiInstanceId": session.api_instance_id,
      "agentSessionId": session.agent_session_id,
      "marketStreamStatus": "READY",
    },
  )

  class DatabaseSession:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    async def get(self, model, _identity):
      return device if model is agent_api.AgentDevice else heartbeat

  class SafetyService:
    async def status(self, _account_id):
      raise SQLAlchemyTimeoutError("safety dependency busy")

  async def market_stream_tradable() -> bool:
    return True

  monkeypatch.setattr(agent_api, "AsyncSessionLocal", DatabaseSession)
  monkeypatch.setattr(
    agent_api,
    "evaluate_agent_session",
    lambda *_args, **_kwargs: SimpleNamespace(
      current=True,
      reason_code="",
      api_instance_id=session.api_instance_id,
      agent_session_id=session.agent_session_id,
    ),
  )
  monkeypatch.setattr(
    agent_api,
    "AccountExecutionSafetyService",
    SafetyService,
  )
  monkeypatch.setattr(
    agent_api,
    "authoritative_market_stream_tradable",
    market_stream_tradable,
  )
  command = AgentEnvelope(
    message_type=AgentMessageType.COMMAND,
    payload={
      "command_kind": "PLACE_ORDER",
      "account_id": "account-1",
      "execution_mode": "live",
      "side": "BUY",
    },
  )

  with pytest.raises(
    agent_api._TradeCommandDeliveryDeferred,
    match="risk_gate_dependency_unavailable",
  ):
    await agent_api._assert_trade_delivery_session(session, command)


@pytest.mark.asyncio
async def test_true_revocation_is_not_masked_by_reconciling_deferral(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session = control_session()
  device = SimpleNamespace(revoked_at=agent_api.utcnow())
  heartbeat = SimpleNamespace(
    status="RECONCILING",
    details={
      "apiInstanceId": session.api_instance_id,
      "agentSessionId": session.agent_session_id,
      "marketStreamStatus": "STALE",
    },
  )

  class DatabaseSession:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    async def get(self, model, _identity):
      return device if model is agent_api.AgentDevice else heartbeat

  monkeypatch.setattr(agent_api, "AsyncSessionLocal", DatabaseSession)
  monkeypatch.setattr(
    agent_api,
    "evaluate_agent_session",
    lambda *_args, **_kwargs: SimpleNamespace(
      current=False,
      reason_code=agent_api.QMT_AGENT_NOT_RECONCILED,
      api_instance_id=session.api_instance_id,
      agent_session_id=session.agent_session_id,
    ),
  )
  command = AgentEnvelope(
    message_type=AgentMessageType.COMMAND,
    payload={
      "command_kind": "PLACE_ORDER",
      "account_id": "account-1",
      "execution_mode": "live",
      "side": "BUY",
    },
  )

  with pytest.raises(agent_api.AuthError, match="交易投递会话已失效"):
    await agent_api._assert_trade_delivery_session(session, command)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "deferred_reason",
  ("agent_not_ready_for_command", "market_stream_not_ready"),
)
async def test_deferred_trade_validation_stays_alive_for_durable_redelivery(
  monkeypatch: pytest.MonkeyPatch,
  deferred_reason: str,
) -> None:
  session = control_session()
  outbound = agent_api._AgentOutboundBuffer()
  validations = agent_api._TradeCommandValidationBuffer(
    high_capacity=1,
    normal_capacity=1,
  )
  database_state = agent_api._AgentDatabaseState(session.device_id)
  first_attempt = asyncio.Event()
  command = AgentEnvelope(
    message_id="00000000-0000-4000-8000-000000000043",
    message_type=AgentMessageType.COMMAND,
    payload={"command_kind": "PLACE_ORDER", "side": "BUY"},
  )
  attempts = 0

  async def validate(*_args, **_kwargs):
    nonlocal attempts
    attempts += 1
    if attempts == 1:
      first_attempt.set()
      raise agent_api._TradeCommandDeliveryDeferred(deferred_reason)

  monkeypatch.setattr(agent_api, "_assert_trade_delivery_session", validate)
  processor = asyncio.create_task(
    agent_api._process_trade_command_validations(
      control_session=session,
      outbound=outbound,
      validations=validations,
      database_state=database_state,
      lane=agent_api._TRADE_VALIDATION_NORMAL_LANE,
    )
  )
  try:
    assert await validations.put(command)
    await asyncio.wait_for(first_attempt.wait(), timeout=0.2)
    accepted_for_redelivery = False
    for _ in range(100):
      if await validations.put(command):
        accepted_for_redelivery = True
        break
      await asyncio.sleep(0)
    assert accepted_for_redelivery
    redelivered = await asyncio.wait_for(outbound.get(), timeout=0.2)
    assert redelivered.envelope.message_id == command.message_id
    assert attempts == 2
    assert processor.done() is False
    assert database_state.ready.is_set()
  finally:
    processor.cancel()
    await asyncio.gather(processor, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("high_message_type", "high_command_kind"),
  (
    (AgentMessageType.CANCEL_COMMAND, "CANCEL_ORDER"),
    (AgentMessageType.COMMAND, "EMERGENCY_STOP"),
  ),
)
async def test_slow_place_validation_cannot_block_later_high_priority_command(
  monkeypatch: pytest.MonkeyPatch,
  high_message_type: AgentMessageType,
  high_command_kind: str,
) -> None:
  session = control_session()
  outbound = agent_api._AgentOutboundBuffer()
  validations = agent_api._TradeCommandValidationBuffer(
    high_capacity=2,
    normal_capacity=2,
  )
  database_state = agent_api._AgentDatabaseState(session.device_id)
  normal_validation_started = asyncio.Event()
  release_normal_validation = asyncio.Event()
  high_validation_completed = asyncio.Event()
  commands = [
    AgentEnvelope(
      message_id="00000000-0000-4000-8000-000000000041",
      message_type=AgentMessageType.COMMAND,
      payload={"command_kind": "PLACE_ORDER", "side": "BUY"},
    ),
    AgentEnvelope(
      message_id="00000000-0000-4000-8000-000000000042",
      message_type=high_message_type,
      payload={"command_kind": high_command_kind},
    ),
  ]

  async def next_command(*_args, **_kwargs):
    if commands:
      return commands.pop(0)
    await asyncio.sleep(60)
    return None

  async def validate(_session, envelope) -> None:
    if str(envelope.payload.get("command_kind") or "") == "PLACE_ORDER":
      normal_validation_started.set()
      await release_normal_validation.wait()
      return
    high_validation_completed.set()

  monkeypatch.setattr(agent_api, "_next_command", next_command)
  monkeypatch.setattr(agent_api, "_assert_trade_delivery_session", validate)
  monkeypatch.setattr(agent_api, "AGENT_CONTROL_POLL_INTERVAL_SECONDS", 0.001)
  tasks = [
    asyncio.create_task(
      agent_api._poll_agent_trade_commands(
        control_session=session,
        protocol_version="1.1",
        validations=validations,
        database_state=database_state,
      )
    ),
    asyncio.create_task(
      agent_api._process_trade_command_validations(
        control_session=session,
        outbound=outbound,
        validations=validations,
        database_state=database_state,
        lane=agent_api._TRADE_VALIDATION_HIGH_LANE,
      )
    ),
    asyncio.create_task(
      agent_api._process_trade_command_validations(
        control_session=session,
        outbound=outbound,
        validations=validations,
        database_state=database_state,
        lane=agent_api._TRADE_VALIDATION_NORMAL_LANE,
      )
    ),
  ]
  try:
    await asyncio.wait_for(normal_validation_started.wait(), timeout=0.2)
    await asyncio.wait_for(high_validation_completed.wait(), timeout=0.2)
    high_item = await asyncio.wait_for(outbound.get(), timeout=0.2)
    assert high_item.envelope.message_type is high_message_type
    assert high_item.envelope.payload["command_kind"] == high_command_kind
    assert not release_normal_validation.is_set()
  finally:
    release_normal_validation.set()
    for task in tasks:
      task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_trade_validation_buffer_reserves_high_priority_capacity() -> None:
  validations = agent_api._TradeCommandValidationBuffer(
    high_capacity=1,
    normal_capacity=1,
  )
  normal = AgentEnvelope(
    message_type=AgentMessageType.COMMAND,
    payload={"command_kind": "PLACE_ORDER"},
  )
  extra_normal = AgentEnvelope(
    message_type=AgentMessageType.COMMAND,
    payload={"command_kind": "PLACE_ORDER"},
  )
  cancel = AgentEnvelope(
    message_type=AgentMessageType.CANCEL_COMMAND,
    payload={"command_kind": "CANCEL_ORDER"},
  )

  assert await validations.put(normal)
  assert not await validations.put(extra_normal)
  assert await validations.put(cancel)
  assert validations.qsize(agent_api._TRADE_VALIDATION_NORMAL_LANE) == 1
  assert validations.qsize(agent_api._TRADE_VALIDATION_HIGH_LANE) == 1


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
async def test_full_durable_lane_rejects_immediately_without_blocking_heartbeat(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  inbound = agent_api._AgentInboundBuffer(capacity=2, max_bytes=128)
  outbound = agent_api._AgentOutboundBuffer()
  state = agent_api._AgentDatabaseState("device-1")

  def item(envelope: AgentEnvelope) -> agent_api._AgentInboundItem:
    return agent_api._AgentInboundItem(
      envelope=envelope,
      received_at=agent_api.utcnow(),
      received_monotonic=agent_api.time.monotonic(),
      frame_bytes=1,
      dedup_key=(
        "latest-heartbeat"
        if envelope.message_type is AgentMessageType.HEARTBEAT
        else envelope.message_id
      ),
    )

  reports = [
    report_envelope(f"00000000-0000-4000-8000-{index:012d}")
    for index in range(10, 13)
  ]
  assert await inbound.put(item(reports[0]))
  assert await inbound.put(item(reports[1]))
  assert not await asyncio.wait_for(inbound.put(item(reports[2])), timeout=0.05)

  first_heartbeat = AgentEnvelope(
    message_id="00000000-0000-4000-8000-000000000020",
    message_type=AgentMessageType.HEARTBEAT,
    payload={"device_id": "device-1", "status": "READY"},
  )
  latest_heartbeat = AgentEnvelope(
    message_id="00000000-0000-4000-8000-000000000021",
    message_type=AgentMessageType.HEARTBEAT,
    payload={"device_id": "device-1", "status": "READY"},
  )
  assert await asyncio.wait_for(inbound.put(item(first_heartbeat)), timeout=0.05)
  assert await asyncio.wait_for(inbound.put(item(latest_heartbeat)), timeout=0.05)
  assert inbound.qsize(agent_api._INBOUND_DURABLE_LANE) == 2
  assert inbound.qsize(agent_api._INBOUND_HEARTBEAT_LANE) == 1
  assert inbound.qsize() == 3

  processed = asyncio.Event()
  processed_ids: list[str] = []

  async def process_message(_session, envelope, **_kwargs):
    processed_ids.append(envelope.message_id)
    processed.set()
    return AgentEnvelope(
      message_type=AgentMessageType.HEARTBEAT_ACK,
      payload={"heartbeat_message_id": envelope.message_id},
    )

  monkeypatch.setattr(agent_api, "_process_message", process_message)
  processor = asyncio.create_task(
    agent_api._process_agent_control_messages(
      control_session=control_session(),
      protocol_version="1.1",
      inbound=inbound,
      outbound=outbound,
      database_state=state,
      lane=agent_api._INBOUND_HEARTBEAT_LANE,
    )
  )
  try:
    await asyncio.wait_for(processed.wait(), timeout=0.5)
    assert processed_ids == [latest_heartbeat.message_id]
    assert outbound.qsize() == 1
    assert inbound.qsize(agent_api._INBOUND_DURABLE_LANE) == 2
  finally:
    processor.cancel()
    await asyncio.gather(processor, return_exceptions=True)


@pytest.mark.asyncio
async def test_receiver_nacks_overflowed_report_and_keeps_heartbeat_lane_live() -> (
  None
):
  websocket = FakeWebSocket()
  inbound = agent_api._AgentInboundBuffer(capacity=1, max_bytes=1024)
  outbound = agent_api._AgentOutboundBuffer()
  state = agent_api._AgentDatabaseState("device-1")
  retained = report_envelope("00000000-0000-4000-8000-000000000030")
  overflow = report_envelope("00000000-0000-4000-8000-000000000031")
  heartbeat = AgentEnvelope(
    message_id="00000000-0000-4000-8000-000000000032",
    message_type=AgentMessageType.HEARTBEAT,
    payload={"device_id": "device-1", "status": "READY"},
  )
  assert await inbound.put(
    agent_api._AgentInboundItem(
      envelope=retained,
      received_at=agent_api.utcnow(),
      received_monotonic=agent_api.time.monotonic(),
      frame_bytes=1,
      dedup_key=retained.message_id,
    )
  )
  await websocket.received.put(overflow.model_dump_json())
  await websocket.received.put(heartbeat.model_dump_json())

  receiver = asyncio.create_task(
    agent_api._receive_agent_control_messages(
      websocket,
      device_id="device-1",
      protocol_version="1.1",
      inbound=inbound,
      outbound=outbound,
      database_state=state,
    )
  )
  try:
    for _ in range(100):
      if (
        outbound.qsize() == 1
        and inbound.qsize(agent_api._INBOUND_HEARTBEAT_LANE) == 1
      ):
        break
      await asyncio.sleep(0.005)
    assert inbound.qsize(agent_api._INBOUND_DURABLE_LANE) == 1
    assert inbound.qsize(agent_api._INBOUND_HEARTBEAT_LANE) == 1
    nack = await outbound.get()
    assert nack.envelope.message_type is AgentMessageType.REPORT_ACK
    assert nack.envelope.payload == {
      "report_message_id": overflow.message_id,
      "accepted": False,
      "duplicate": False,
      "reason": "inbound_backpressure_retry",
    }
  finally:
    receiver.cancel()
    await asyncio.gather(receiver, return_exceptions=True)


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


@pytest.mark.asyncio
async def test_transient_database_timeout_retries_same_report_without_disconnect(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  inbound = agent_api._AgentInboundBuffer()
  outbound = agent_api._AgentOutboundBuffer()
  state = agent_api._AgentDatabaseState("device-1")
  envelope = report_envelope("00000000-0000-4000-8000-000000000007")
  attempts = 0

  async def process_message(*_args, **_kwargs):
    nonlocal attempts
    attempts += 1
    if attempts == 1:
      raise SQLAlchemyTimeoutError("pool exhausted")
    return AgentEnvelope(
      message_type=AgentMessageType.REPORT_ACK,
      payload=ReportAckPayload(
        report_message_id=envelope.message_id,
        accepted=True,
      ).model_dump(mode="json"),
    )

  monkeypatch.setattr(agent_api, "_process_message", process_message)
  monkeypatch.setattr(agent_api, "AGENT_CONTROL_DEPENDENCY_RETRY_SECONDS", 0.001)
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
  try:
    while outbound.qsize() == 0:
      await asyncio.sleep(0)
    assert attempts == 2
    assert state.ready.is_set()
    assert state.consecutive_failures == 0
  finally:
    processor.cancel()
    await asyncio.gather(processor, return_exceptions=True)


@pytest.mark.asyncio
async def test_control_receiver_rejects_market_event_frames() -> None:
  inbound = agent_api._AgentInboundBuffer()
  outbound = agent_api._AgentOutboundBuffer()
  state = agent_api._AgentDatabaseState("device-1")
  websocket = FakeWebSocket()
  await websocket.received.put(
    AgentEnvelope(
      message_type=AgentMessageType.MARKET_EVENT,
      payload={"kind": "quote", "stock_code": "600000.SH", "data": {}},
    ).model_dump_json()
  )

  with pytest.raises(ValueError, match="/ws/agent/market"):
    await agent_api._receive_agent_control_messages(
      websocket,
      device_id="device-1",
      protocol_version="1.1",
      inbound=inbound,
      outbound=outbound,
      database_state=state,
    )

  assert inbound.qsize() == 0
  assert outbound.qsize() == 0


@pytest.mark.asyncio
async def test_slow_full_snapshot_cannot_block_command_ack_lane(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  inbound = agent_api._AgentInboundBuffer()
  outbound = agent_api._AgentOutboundBuffer()
  state = agent_api._AgentDatabaseState("device-1")
  snapshot_started = asyncio.Event()
  release_snapshot = asyncio.Event()
  command_ack_processed = asyncio.Event()
  snapshot = report_envelope("00000000-0000-4000-8000-000000000050")
  command_ack = AgentEnvelope(
    message_type=AgentMessageType.COMMAND_ACK,
    payload={
      "command_message_id": "command-1",
      "client_order_id": "client-1",
      "accepted": True,
      "reason": "accepted",
    },
  )

  async def process_message(_session, envelope, **_kwargs):
    if envelope.message_type is AgentMessageType.DELTA_REPORT:
      snapshot_started.set()
      await release_snapshot.wait()
      return None
    command_ack_processed.set()
    return None

  monkeypatch.setattr(agent_api, "_process_message", process_message)
  now = agent_api.utcnow()
  for envelope, dedup_key in (
    (snapshot, snapshot.message_id),
    (command_ack, "command-ack:command-1"),
  ):
    assert await inbound.put(
      agent_api._AgentInboundItem(
        envelope=envelope,
        received_at=now,
        received_monotonic=agent_api.time.monotonic(),
        frame_bytes=1,
        dedup_key=dedup_key,
      )
    )
  processors = [
    asyncio.create_task(
      agent_api._process_agent_control_messages(
        control_session=control_session(),
        protocol_version="1.1",
        inbound=inbound,
        outbound=outbound,
        database_state=state,
        lane=lane,
      )
    )
    for lane in (
      agent_api._INBOUND_DURABLE_LANE,
      agent_api._INBOUND_COMMAND_ACK_LANE,
    )
  ]
  try:
    await asyncio.wait_for(snapshot_started.wait(), timeout=0.2)
    await asyncio.wait_for(command_ack_processed.wait(), timeout=0.2)
  finally:
    release_snapshot.set()
    for processor in processors:
      processor.cancel()
    await asyncio.gather(*processors, return_exceptions=True)


@pytest.mark.asyncio
async def test_market_lease_refresh_retries_without_closing_control_session(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls = 0
  recovered = asyncio.Event()

  async def refresh_market_device(_session):
    nonlocal calls
    calls += 1
    if calls == 1:
      raise agent_api.RedisError("redis reconnecting")
    recovered.set()

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "refresh_market_device",
    refresh_market_device,
  )
  monkeypatch.setattr(agent_api, "AGENT_CONTROL_DEPENDENCY_RETRY_SECONDS", 0.001)
  session = control_session()
  task = asyncio.create_task(
    agent_api._refresh_agent_market_lease(
      control_session=session,
    )
  )
  try:
    await asyncio.wait_for(recovered.wait(), timeout=1)
    assert calls == 2
    assert not task.done()
  finally:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_market_lease_refresh_is_independent_from_engine_reconciliation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session = control_session()
  refreshed: list[agent_api.AgentControlSession] = []

  async def refresh(control):
    refreshed.append(control)

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "refresh_market_device",
    refresh,
  )

  task = asyncio.create_task(
    agent_api._refresh_agent_market_lease(control_session=session)
  )
  try:
    while not refreshed:
      await asyncio.sleep(0)
  finally:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

  assert refreshed == [session]
