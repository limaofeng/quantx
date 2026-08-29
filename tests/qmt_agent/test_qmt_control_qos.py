from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
import quantx_qmt_agent.runtime as runtime_module
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.runtime import (
  REPORT_SEND_WINDOW,
  AgentRuntime,
  _BoundedMarketBatchBuffer,
  _PriorityControlSocketWriter,
)


class _BlockingSocket:
  def __init__(self) -> None:
    self.first_started = asyncio.Event()
    self.release_first = asyncio.Event()
    self.sent: list[str] = []
    self.active = 0
    self.max_active = 0

  async def send(self, serialized: str) -> None:
    self.active += 1
    self.max_active = max(self.max_active, self.active)
    try:
      if not self.sent:
        self.first_started.set()
        await self.release_first.wait()
      envelope = AgentEnvelope.model_validate_json(serialized)
      self.sent.append(envelope.message_type.value)
    finally:
      self.active -= 1


class _SlowMarketSocket:
  def __init__(self) -> None:
    self.text_send_started = asyncio.Event()
    self.send_attempts = 0

  async def send(self, payload) -> None:
    assert isinstance(payload, str)
    self.send_attempts += 1
    self.text_send_started.set()
    await asyncio.Event().wait()

  async def recv(self) -> str:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


class _ImmediateControlSocket:
  def __init__(self) -> None:
    self.sent: list[str] = []

  async def send(self, serialized: str) -> None:
    self.sent.append(AgentEnvelope.model_validate_json(serialized).message_type.value)


def _envelope(message_type: AgentMessageType) -> str:
  payload = (
    {"device_id": "device-1", "status": "READY"}
    if message_type is AgentMessageType.HEARTBEAT
    else {"command_message_id": "command-1", "accepted": True}
    if message_type is AgentMessageType.COMMAND_ACK
    else {"sequence": 1, "is_complete": False}
    if message_type is AgentMessageType.DELTA_REPORT
    else {"subscription_id": "market-1", "data": {}}
  )
  return AgentEnvelope(message_type=message_type, payload=payload).model_dump_json()


@pytest.mark.asyncio
async def test_control_writer_prioritizes_heartbeat_over_queued_reports() -> (
  None
):
  socket = _BlockingSocket()
  writer = _PriorityControlSocketWriter(socket)
  worker = asyncio.create_task(writer.run())
  first_report = asyncio.create_task(
    writer.send(_envelope(AgentMessageType.DELTA_REPORT), priority=1)
  )
  await asyncio.wait_for(socket.first_started.wait(), timeout=0.2)
  assert not first_report.done()
  second_report = asyncio.create_task(
    writer.send(_envelope(AgentMessageType.DELTA_REPORT), priority=1)
  )
  command_ack = asyncio.create_task(
    writer.send(_envelope(AgentMessageType.COMMAND_ACK), priority=0)
  )
  heartbeat = asyncio.create_task(
    writer.send(_envelope(AgentMessageType.HEARTBEAT), priority=0)
  )

  socket.release_first.set()
  await asyncio.wait_for(
    asyncio.gather(first_report, second_report, command_ack, heartbeat),
    timeout=1,
  )
  worker.cancel()
  await asyncio.gather(worker, return_exceptions=True)

  assert socket.sent == [
    AgentMessageType.DELTA_REPORT.value,
    AgentMessageType.COMMAND_ACK.value,
    AgentMessageType.HEARTBEAT.value,
    AgentMessageType.DELTA_REPORT.value,
  ]
  assert socket.max_active == 1


@pytest.mark.asyncio
async def test_single_quote_overflow_keeps_latest_without_failing_session() -> None:
  runtime = object.__new__(AgentRuntime)
  runtime._ensure_market_upload_state()
  runtime._session_loop = asyncio.get_running_loop()
  runtime._market_events = asyncio.Queue(maxsize=2)

  for sequence in range(4):
    runtime._enqueue_market_event({"sequence": sequence})
  await asyncio.sleep(0)

  assert runtime._market_event_drops == 2
  assert (await runtime._market_events.get())["sequence"] == 2
  runtime._market_events.task_done()
  assert (await runtime._market_events.get())["sequence"] == 3
  runtime._market_events.task_done()


@pytest.mark.asyncio
async def test_slow_market_text_send_cannot_block_control_frames(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  runtime = object.__new__(AgentRuntime)
  runtime._ensure_whole_market_state()
  await runtime._market_events.put(
    {
      "kind": "quote",
      "stock_code": "600000.SH",
      "period": "tick",
      "data": {"lastPrice": 10.0},
    }
  )
  market_socket = _SlowMarketSocket()
  outbound = _BoundedMarketBatchBuffer(max_batches=2, max_bytes=1024)
  monkeypatch.setattr(runtime_module, "MARKET_EVENT_SEND_TIMEOUT_SECONDS", 10.0)
  market_transport = asyncio.create_task(
    runtime._transmit_market_batches(
      market_socket,
      outbound,
      stream_id="stream-1",
    )
  )
  await asyncio.wait_for(market_socket.text_send_started.wait(), timeout=0.2)

  control_socket = _ImmediateControlSocket()
  control_writer = _PriorityControlSocketWriter(control_socket)
  runtime._control_socket_writer = control_writer
  runtime._websocket_send_lock = asyncio.Lock()
  control_task = asyncio.create_task(control_writer.run())
  try:
    await asyncio.wait_for(
      asyncio.gather(
        runtime._send_socket_text(
          control_socket,
          _envelope(AgentMessageType.HEARTBEAT),
        ),
        runtime._send_socket_text(
          control_socket,
          _envelope(AgentMessageType.COMMAND_ACK),
        ),
        runtime._send_socket_text(
          control_socket,
          _envelope(AgentMessageType.DELTA_REPORT),
        ),
      ),
      timeout=0.1,
    )
    await asyncio.sleep(0)
    assert set(control_socket.sent) == {
      AgentMessageType.HEARTBEAT.value,
      AgentMessageType.COMMAND_ACK.value,
      AgentMessageType.DELTA_REPORT.value,
    }
  finally:
    market_transport.cancel()
    control_task.cancel()
    await asyncio.gather(
      market_transport,
      control_task,
      return_exceptions=True,
    )


@pytest.mark.asyncio
async def test_market_text_send_timeout_requires_fresh_market_socket(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  runtime = object.__new__(AgentRuntime)
  runtime._ensure_whole_market_state()
  await runtime._market_events.put(
    {
      "kind": "quote",
      "stock_code": "600000.SH",
      "period": "tick",
      "data": {"lastPrice": 10.0},
    }
  )
  market_socket = _SlowMarketSocket()
  outbound = _BoundedMarketBatchBuffer(max_batches=2, max_bytes=1024)
  monkeypatch.setattr(runtime_module, "MARKET_EVENT_SEND_TIMEOUT_SECONDS", 0.01)

  with pytest.raises(
    RuntimeError,
    match="market socket send timed out; reconnect required",
  ):
    await asyncio.wait_for(
      runtime._transmit_market_batches(
        market_socket,
        outbound,
        stream_id="stream-1",
      ),
      timeout=0.2,
    )

  assert market_socket.send_attempts == 1
  assert runtime._market_event_drops == 1
  await asyncio.wait_for(runtime._market_events.join(), timeout=0.1)


@pytest.mark.asyncio
async def test_market_control_native_call_never_blocks_receiver_ack() -> None:
  runtime = object.__new__(AgentRuntime)
  runtime._ensure_market_upload_state()
  runtime._heartbeat_sent_monotonic = {"heartbeat-1": 1.0}

  await runtime._handle_message(
    None,
    AgentEnvelope(
      message_type=AgentMessageType.MARKET_SUBSCRIBE,
      payload={"kind": "quote", "subscription_id": "quote-1"},
    ).model_dump_json(),
  )
  await asyncio.wait_for(
    runtime._handle_message(
      None,
      AgentEnvelope(
        message_type=AgentMessageType.HEARTBEAT_ACK,
        payload={"heartbeat_message_id": "heartbeat-1"},
      ).model_dump_json(),
    ),
    timeout=0.2,
  )

  assert runtime._market_control_requests.qsize() == 1
  assert runtime._heartbeat_sent_monotonic == {}


@pytest.mark.asyncio
async def test_report_sender_window_is_bounded_until_ack(tmp_path) -> None:
  runtime = object.__new__(AgentRuntime)
  runtime.mode = "data-only"
  runtime.configuration = SimpleNamespace(device_id="device-1")
  runtime.journal = LocalJournal(tmp_path / "journal.sqlite3")
  runtime._ensure_market_upload_state()
  sent: list[AgentEnvelope] = []

  async def send(_socket, serialized: str) -> None:
    sent.append(AgentEnvelope.model_validate_json(serialized))

  runtime._send_socket_text = send
  for index in range(REPORT_SEND_WINDOW + 4):
    envelope = AgentEnvelope(
      message_id=f"00000000-0000-4000-8000-{index:012d}",
      message_type=AgentMessageType.DELTA_REPORT,
      payload={"sequence": index, "is_complete": False},
    )
    runtime.journal.add_report(envelope.message_id, envelope.model_dump_json())

  await runtime._flush_reports(None)
  assert len(sent) == REPORT_SEND_WINDOW

  first = sent[0]
  ack_writer = asyncio.create_task(runtime._report_ack_loop(None))
  await runtime._handle_message(
    None,
    AgentEnvelope(
      message_type=AgentMessageType.REPORT_ACK,
      payload={"report_message_id": first.message_id, "accepted": True},
    ).model_dump_json(),
  )
  await asyncio.wait_for(runtime._report_ack_requests.join(), timeout=0.5)
  await runtime._flush_reports(None)
  ack_writer.cancel()
  await asyncio.gather(ack_writer, return_exceptions=True)

  assert len(sent) == REPORT_SEND_WINDOW + 1
  assert first.message_id not in runtime._reports_inflight


@pytest.mark.asyncio
async def test_slow_report_ack_fsync_does_not_block_control_receiver(tmp_path) -> None:
  runtime = object.__new__(AgentRuntime)
  runtime.mode = "data-only"
  runtime.journal = LocalJournal(tmp_path / "slow-ack.sqlite3")
  runtime._ensure_market_upload_state()
  report_id = "00000000-0000-4000-8000-000000000099"
  report = AgentEnvelope(
    message_id=report_id,
    message_type=AgentMessageType.DELTA_REPORT,
    payload={"sequence": 1, "is_complete": False},
  )
  runtime.journal.add_report(report_id, report.model_dump_json())
  runtime._reports_inflight.add(report_id)
  persistence_started = threading.Event()
  release_persistence = threading.Event()
  acknowledge = runtime.journal.acknowledge_reports

  def slow_acknowledge(message_ids):
    persistence_started.set()
    release_persistence.wait(timeout=2)
    return acknowledge(message_ids)

  runtime.journal.acknowledge_reports = slow_acknowledge
  ack_writer = asyncio.create_task(runtime._report_ack_loop(None))
  await runtime._handle_message(
    None,
    AgentEnvelope(
      message_type=AgentMessageType.REPORT_ACK,
      payload={"report_message_id": report_id, "accepted": True},
    ).model_dump_json(),
  )
  assert await asyncio.to_thread(persistence_started.wait, 1)

  heartbeat_id = "heartbeat-while-ack-fsyncs"
  runtime._heartbeat_sent_monotonic[heartbeat_id] = 1.0
  await asyncio.wait_for(
    runtime._handle_message(
      None,
      AgentEnvelope(
        message_type=AgentMessageType.HEARTBEAT_ACK,
        payload={"heartbeat_message_id": heartbeat_id},
      ).model_dump_json(),
    ),
    timeout=0.05,
  )
  assert heartbeat_id not in runtime._heartbeat_sent_monotonic
  assert report_id in runtime._reports_inflight

  release_persistence.set()
  await asyncio.wait_for(runtime._report_ack_requests.join(), timeout=1)
  ack_writer.cancel()
  await asyncio.gather(ack_writer, return_exceptions=True)
  assert report_id not in runtime._reports_inflight


@pytest.mark.asyncio
async def test_report_backpressure_nack_uses_bounded_retry_backoff(tmp_path) -> None:
  runtime = object.__new__(AgentRuntime)
  runtime.mode = "data-only"
  runtime.journal = LocalJournal(tmp_path / "report-backoff.sqlite3")
  runtime._ensure_market_upload_state()
  report_id = "00000000-0000-4000-8000-000000000098"
  report = AgentEnvelope(
    message_id=report_id,
    message_type=AgentMessageType.DELTA_REPORT,
    payload={"sequence": 1, "is_complete": False},
  )
  runtime.journal.add_report(report_id, report.model_dump_json())
  sent: list[str] = []

  async def send(_socket, serialized: str) -> None:
    sent.append(serialized)

  runtime._send_socket_text = send
  await runtime._flush_reports(None)
  nack = AgentEnvelope(
    message_type=AgentMessageType.REPORT_ACK,
    payload={
      "report_message_id": report_id,
      "accepted": False,
      "reason": "inbound_backpressure_retry",
    },
  )
  await runtime._handle_message(None, nack.model_dump_json())
  first_retry_at = runtime._report_retry_not_before[report_id]

  await runtime._flush_reports(None)
  assert len(sent) == 1
  assert first_retry_at > runtime_module.time.monotonic()

  runtime._report_retry_not_before[report_id] = 0.0
  await runtime._flush_reports(None)
  assert len(sent) == 2
  await runtime._handle_message(None, nack.model_dump_json())
  second_delay = (
    runtime._report_retry_not_before[report_id]
    - runtime_module.time.monotonic()
  )
  assert second_delay >= runtime_module.REPORT_RETRY_BASE_SECONDS * 1.9


@pytest.mark.asyncio
async def test_history_uploads_share_one_runtime_client(monkeypatch) -> None:
  clients: list[object] = []

  class Client:
    def __init__(self, **_kwargs) -> None:
      self.closed = False
      clients.append(self)

    async def aclose(self) -> None:
      self.closed = True

  monkeypatch.setattr(runtime_module.httpx, "AsyncClient", Client)
  runtime = object.__new__(AgentRuntime)
  runtime.configuration = SimpleNamespace(api_url="http://127.0.0.1:8080")
  runtime._ensure_market_upload_state()

  first = runtime._market_data_upload_client()
  second = runtime._market_data_upload_client()

  assert first is second
  assert clients == [first]
  await runtime._close_market_data_upload_client()
  assert first.closed is True


@pytest.mark.asyncio
async def test_history_upload_slots_are_global_across_requests(tmp_path) -> None:
  two_started = asyncio.Event()
  release = asyncio.Event()

  class Response:
    @staticmethod
    def raise_for_status() -> None:
      return None

  class Client:
    def __init__(self) -> None:
      self.active = 0
      self.max_active = 0

    async def put(self, _url, *, content, headers):
      del content, headers
      self.active += 1
      self.max_active = max(self.max_active, self.active)
      if self.active == runtime_module.MAX_CONCURRENT_HISTORY_UPLOADS:
        two_started.set()
      try:
        await release.wait()
        return Response()
      finally:
        self.active -= 1

  runtime = object.__new__(AgentRuntime)
  runtime.configuration = SimpleNamespace(api_url="http://127.0.0.1:8080")
  runtime._access_token = "token"
  runtime._ensure_market_upload_state()
  client = Client()
  tasks = []
  for index in range(4):
    path = tmp_path / f"chunk-{index}.json.gz"
    path.write_bytes(b"payload")
    tasks.append(
      asyncio.create_task(
        runtime._put_market_data_chunk(
          client,
          request_id=f"request-{index}",
          chunk_index=0,
          chunk=SimpleNamespace(
            path=path,
            compressed_bytes=7,
            digest="0" * 64,
            record_count=1,
          ),
          total_chunks=1,
        )
      )
    )

  await asyncio.wait_for(two_started.wait(), timeout=0.2)
  await asyncio.sleep(0)
  assert client.max_active == runtime_module.MAX_CONCURRENT_HISTORY_UPLOADS
  assert sum(task.done() for task in tasks) == 0
  release.set()
  await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)
  assert client.max_active == runtime_module.MAX_CONCURRENT_HISTORY_UPLOADS
