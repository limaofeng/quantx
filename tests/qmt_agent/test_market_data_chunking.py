import asyncio
import gzip
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest
import quantx_qmt_agent.broker as broker_module
import quantx_qmt_agent.runtime as runtime_module
from quantx_contracts import (
  HISTORICAL_BAR_NO_DATA_REASON,
  HISTORICAL_BAR_SUMMARY_RECORD_TYPE,
  HISTORICAL_TICK_ORDINAL_FIELD,
  HISTORICAL_TICK_ORDINALS_PER_MILLISECOND,
  HISTORICAL_TICK_SOURCE_TIME_FIELD,
  HISTORICAL_TICK_TRANSFER_FIELDS,
  HISTORICAL_TICK_TRANSFER_OPTIONAL_FIELDS,
  AgentEnvelope,
  AgentMessageType,
)
from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion
from quantx_qmt_agent.broker import (
  _iter_market_data_records,
  _market_data_records,
  _normalize_daily_market_timestamp,
  _normalize_market_timestamp,
  _validate_bars_request,
)
from quantx_qmt_agent.runtime import (
  AgentRuntime,
  _cleanup_legacy_market_data_spools,
  _initialize_market_data_spool_root,
  _iter_encoded_market_data_chunks,
  _managed_market_data_spool_bytes,
  _prepare_market_data_spool_sync,
)


def _records(count: int):
  for index in range(count):
    yield {
      "code": "000001.SZ",
      "period": "1d",
      "time": 1_735_776_000_000 + index,
      "close": 10.0,
    }


def _bar_rows(records):
  return [
    record
    for record in records
    if record.get("record_type") != HISTORICAL_BAR_SUMMARY_RECORD_TYPE
  ]


def _bar_summaries(records):
  return [
    record
    for record in records
    if record.get("record_type") == HISTORICAL_BAR_SUMMARY_RECORD_TYPE
  ]


def _prepare_spool(
  broker,
  directory: Path,
  *,
  compressed_limit: int = 10_000_000,
):
  directory.mkdir()
  return _prepare_market_data_spool_sync(
    broker,
    {"operation": "bars"},
    directory,
    max_total_uncompressed_bytes=10_000_000,
    max_total_compressed_bytes=compressed_limit,
  )


def _spool_bytes(prepared) -> list[bytes]:
  return [chunk.path.read_bytes() for chunk in prepared.chunks]


def test_websocket_ping_timeout_exceeds_native_preparation_watchdog() -> None:
  assert runtime_module.WEBSOCKET_PING_INTERVAL_SECONDS == 20
  assert runtime_module.WEBSOCKET_PING_TIMEOUT_SECONDS == 960
  assert (
    runtime_module.WEBSOCKET_PING_TIMEOUT_SECONDS
    > runtime_module.MARKET_DATA_PREPARATION_TIMEOUT_SECONDS
  )


async def _read_http_content(content) -> bytes:
  if isinstance(content, bytes):
    return content
  blocks = []
  async for block in content:
    blocks.append(block)
  return b"".join(blocks)


def _retryable_upload_error(kind: str) -> Exception:
  request = httpx.Request("PUT", "http://127.0.0.1/upload")
  if kind == "connect":
    return httpx.ConnectError("connection reset", request=request)
  if kind == "timeout":
    return httpx.ReadTimeout("upload timed out", request=request)
  status = int(kind)
  response = httpx.Response(status, request=request)
  try:
    response.raise_for_status()
  except httpx.HTTPStatusError as exc:
    return exc
  raise AssertionError(f"status did not fail: {status}")


def test_chunk_encoder_streams_and_respects_limits() -> None:
  records = [{"code": f"{index:06d}.SZ", "payload": "行情" * 8} for index in range(7)]
  chunks = list(
    _iter_encoded_market_data_chunks(
      iter(records),
      max_records=3,
      max_uncompressed_bytes=170,
    )
  )

  assert all(record_count <= 3 for _, record_count in chunks)
  assert all(len(raw) <= 170 for raw, _ in chunks)
  restored = [record for raw, _ in chunks for record in json.loads(raw.decode("utf-8"))]
  assert restored == records


def test_chunk_encoder_fails_at_record_and_byte_boundaries() -> None:
  produced = 0

  def many_records():
    nonlocal produced
    for index in range(100):
      produced += 1
      yield {"index": index}

  with pytest.raises(ValueError, match="record count limit"):
    list(
      _iter_encoded_market_data_chunks(
        many_records(),
        max_total_records=3,
      )
    )
  assert produced == 4

  with pytest.raises(ValueError, match="record byte limit"):
    list(
      _iter_encoded_market_data_chunks(
        [{"payload": "x" * 100}],
        max_record_uncompressed_bytes=50,
      )
    )

  records = [{"payload": "x" * 20}, {"payload": "y" * 20}]
  chunks = list(
    _iter_encoded_market_data_chunks(
      records,
      max_records=1,
      max_uncompressed_bytes=100,
      max_total_uncompressed_bytes=1000,
    )
  )
  total_bytes = sum(len(raw) for raw, _ in chunks)
  with pytest.raises(ValueError, match="uncompressed byte limit"):
    list(
      _iter_encoded_market_data_chunks(
        records,
        max_records=1,
        max_uncompressed_bytes=100,
        max_total_uncompressed_bytes=total_bytes - 1,
      )
    )


def test_chunk_encoder_emits_one_empty_json_array() -> None:
  assert [
    (bytes(raw), count) for raw, count in _iter_encoded_market_data_chunks([])
  ] == [(b"[]", 0)]


def test_spool_is_incremental_deterministic_and_bounded(tmp_path) -> None:
  first_directory = tmp_path / "first"

  class StreamingBroker:
    def __init__(self, directory: Path) -> None:
      self.directory = directory
      self.observed_first_chunk = False

    def iter_market_data(self, _payload):
      for index, record in enumerate(_records(5002)):
        if index == 5001:
          self.observed_first_chunk = (self.directory / "chunk-000000.json.gz").exists()
        yield record

  first_broker = StreamingBroker(first_directory)
  first = _prepare_spool(first_broker, first_directory)
  second_directory = tmp_path / "second"
  second = _prepare_spool(
    StreamingBroker(second_directory),
    second_directory,
  )

  assert first_broker.observed_first_chunk
  assert first.record_count == 5002
  assert len(first.chunks) == 2
  assert _spool_bytes(first) == _spool_bytes(second)
  assert all(content[4:8] == b"\x00\x00\x00\x00" for content in _spool_bytes(first))
  restored_count = sum(
    len(json.loads(gzip.decompress(content))) for content in _spool_bytes(first)
  )
  assert restored_count == 5002
  assert first.compressed_bytes == sum(chunk.compressed_bytes for chunk in first.chunks)


def test_spool_enforces_compressed_limit_and_removes_partial_files(
  tmp_path,
) -> None:
  class Broker:
    def iter_market_data(self, _payload):
      return _records(10)

  exact_directory = tmp_path / "exact"
  exact = _prepare_spool(Broker(), exact_directory)
  retry_directory = tmp_path / "retry"
  retry = _prepare_spool(
    Broker(),
    retry_directory,
    compressed_limit=exact.compressed_bytes,
  )
  assert retry.compressed_bytes == exact.compressed_bytes

  rejected_directory = tmp_path / "rejected"
  with pytest.raises(ValueError, match="compressed byte limit"):
    _prepare_spool(
      Broker(),
      rejected_directory,
      compressed_limit=exact.compressed_bytes - 1,
    )
  assert not rejected_directory.exists()


def test_managed_spool_cleans_only_owned_request_directories(
  tmp_path,
) -> None:
  root = _initialize_market_data_spool_root(tmp_path, "device-1")
  stale = root / "request-stale"
  stale.mkdir()
  (stale / "partial.gz").write_bytes(b"x" * 17)
  unrelated = root / "keep-me"
  unrelated.mkdir()
  (unrelated / "data").write_bytes(b"keep")

  assert _managed_market_data_spool_bytes(root) == 17
  restarted = _initialize_market_data_spool_root(tmp_path, "device-1")

  assert restarted == root
  assert not stale.exists()
  assert unrelated.exists()
  assert (root / runtime_module.MARKET_DATA_SPOOL_OWNER_MARKER).exists()


def test_legacy_spool_cleanup_is_limited_to_exact_temp_prefix(
  tmp_path,
) -> None:
  legacy = tmp_path / "quantx-market-data-spool-old"
  legacy.mkdir()
  (legacy / "chunk").write_bytes(b"old")
  unrelated = tmp_path / "quantx-market-data-other"
  unrelated.mkdir()

  _cleanup_legacy_market_data_spools(tmp_path)

  assert not legacy.exists()
  assert unrelated.exists()


def test_managed_spool_owner_mismatch_fails_closed(tmp_path) -> None:
  root = _initialize_market_data_spool_root(tmp_path, "device-1")
  marker = root / runtime_module.MARKET_DATA_SPOOL_OWNER_MARKER
  marker.write_text('{"owner_key":"different"}', encoding="utf-8")
  protected = root / "request-protected"
  protected.mkdir()

  with pytest.raises(RuntimeError, match="owner marker mismatch"):
    _initialize_market_data_spool_root(tmp_path, "device-1")
  assert protected.exists()


@pytest.mark.asyncio
async def test_redelivery_reuses_one_spool_and_rejects_payload_mismatch() -> None:
  class Broker:
    def __init__(self) -> None:
      self.calls = 0

    def iter_market_data(self, _payload):
      self.calls += 1
      return _records(1)

  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  first = await runtime._prepared_market_data_chunks(
    "request-1",
    {"operation": "bars"},
  )
  second = await runtime._prepared_market_data_chunks(
    "request-1",
    {"operation": "bars"},
  )

  assert runtime.broker.calls == 1
  assert first == second
  content = first[0].path.read_bytes()
  assert first[0].digest == hashlib.sha256(content).hexdigest()
  with pytest.raises(RuntimeError, match="重投参数不一致"):
    await runtime._prepared_market_data_chunks(
      "request-1",
      {"operation": "bars", "start_time": "20250101"},
    )
  runtime.stop()


@pytest.mark.asyncio
async def test_cancelled_session_reuses_inflight_native_preparation(
  monkeypatch,
) -> None:
  started = threading.Event()
  release = threading.Event()

  class Broker:
    def __init__(self) -> None:
      self.calls = 0

    def iter_market_data(self, _payload):
      self.calls += 1
      started.set()
      assert release.wait(timeout=2)
      return _records(1)

  uploaded: list[bytes] = []

  class Response:
    def raise_for_status(self) -> None:
      return None

  class Client:
    def __init__(self, **_kwargs) -> None:
      pass

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args) -> None:
      return None

    async def put(self, _url, *, content, headers):
      body = await _read_http_content(content)
      assert headers["Content-Length"] == str(len(body))
      assert headers["X-Content-SHA256"] == hashlib.sha256(body).hexdigest()
      uploaded.append(body)
      return Response()

  monkeypatch.setattr(runtime_module.httpx, "AsyncClient", Client)
  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime.configuration = SimpleNamespace(
    api_url="http://127.0.0.1:8080",
    device_id="device-1",
  )
  runtime._access_token = "token"
  envelope = SimpleNamespace(
    payload={"request_id": "request-reconnect", "operation": "bars"}
  )

  disconnected = asyncio.create_task(runtime._handle_market_data_request(envelope))
  assert await asyncio.to_thread(started.wait, 1)
  disconnected.cancel()
  with pytest.raises(asyncio.CancelledError):
    await disconnected

  reconnected = asyncio.create_task(runtime._handle_market_data_request(envelope))
  await asyncio.sleep(0)
  assert runtime.broker.calls == 1
  release.set()
  await asyncio.wait_for(reconnected, timeout=2)

  assert runtime.broker.calls == 1
  assert len(uploaded) == 1
  assert runtime._market_upload_cache == {}
  assert runtime._market_upload_cache_bytes == 0
  assert "request-reconnect" in runtime._market_upload_tombstones
  await runtime._handle_market_data_request(envelope)
  assert runtime.broker.calls == 1
  assert len(uploaded) == 1


@pytest.mark.asyncio
async def test_partial_put_retains_exact_spool_for_retry(monkeypatch) -> None:
  class Broker:
    def __init__(self) -> None:
      self.calls = 0

    def iter_market_data(self, _payload):
      self.calls += 1
      return _records(5001)

  attempts: list[tuple[str, bytes, str]] = []
  fail_second_put = True

  class Response:
    def raise_for_status(self) -> None:
      return None

  class Client:
    def __init__(self, **_kwargs) -> None:
      pass

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args) -> None:
      return None

    async def put(self, url, *, content, headers):
      nonlocal fail_second_put
      body = await _read_http_content(content)
      attempts.append((url, body, headers["X-Content-SHA256"]))
      if fail_second_put and url.endswith("/chunks/1"):
        fail_second_put = False
        raise RuntimeError("connection dropped after first PUT")
      return Response()

  monkeypatch.setattr(runtime_module.httpx, "AsyncClient", Client)
  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime.configuration = SimpleNamespace(api_url="http://127.0.0.1:8080")
  runtime._access_token = "token"
  envelope = SimpleNamespace(
    payload={"request_id": "request-partial", "operation": "bars"}
  )

  with pytest.raises(RuntimeError, match="connection dropped"):
    await runtime._handle_market_data_request(envelope)
  assert runtime.broker.calls == 1
  assert runtime._market_upload_cache_bytes > 0
  first_attempt = list(attempts)

  await runtime._handle_market_data_request(envelope)
  retry_attempt = attempts[len(first_attempt) :]
  assert runtime.broker.calls == 1
  assert [(body, digest) for _, body, digest in retry_attempt] == [
    (body, digest) for _, body, digest in first_attempt
  ]
  assert runtime._market_upload_cache == {}
  assert runtime._market_upload_cache_bytes == 0


@pytest.mark.asyncio
async def test_partial_upload_survives_session_cancel_and_joins_redelivery(
  monkeypatch,
) -> None:
  original_encoder = runtime_module._iter_encoded_market_data_chunks

  def one_record_chunks(records, **kwargs):
    return original_encoder(records, max_records=1, **kwargs)

  monkeypatch.setattr(
    runtime_module,
    "_iter_encoded_market_data_chunks",
    one_record_chunks,
  )

  class Broker:
    def __init__(self) -> None:
      self.calls = 0

    def iter_market_data(self, _payload):
      self.calls += 1
      return _records(10)

  attempts: list[tuple[int, bytes, str, str]] = []
  sixth_started = asyncio.Event()
  release_sixth = asyncio.Event()

  class Response:
    def raise_for_status(self) -> None:
      return None

  class Client:
    def __init__(self, **_kwargs) -> None:
      pass

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args) -> None:
      return None

    async def put(self, url, *, content, headers):
      index = int(url.rsplit("/", 1)[1])
      body = await _read_http_content(content)
      attempts.append(
        (
          index,
          body,
          headers["X-Content-SHA256"],
          headers["Authorization"],
        )
      )
      if index == 5:
        sixth_started.set()
        await release_sixth.wait()
      return Response()

  class Socket:
    def __init__(self) -> None:
      self.closed: list[tuple[int, str]] = []
      self.sent: list[str] = []

    async def close(self, *, code: int, reason: str) -> None:
      self.closed.append((code, reason))

    async def send(self, serialized: str) -> None:
      self.sent.append(serialized)

  monkeypatch.setattr(runtime_module.httpx, "AsyncClient", Client)
  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime.configuration = SimpleNamespace(
    api_url="http://127.0.0.1:8080",
    device_id="device-1",
  )
  runtime._access_token = "session-token-1"
  runtime.mode = "data-only"
  runtime.emergency_stop = None
  runtime.journal = SimpleNamespace(
    stats=lambda: {
      "integrity": "ok",
      "size_bytes": 0,
      "pending_reports": 0,
      "processing_commands": 0,
    },
    pending_reports=lambda: [],
  )
  runtime._ensure_market_upload_state()
  envelope = SimpleNamespace(
    payload={"request_id": "request-session-partial", "operation": "bars"}
  )

  first_queue: asyncio.Queue = asyncio.Queue()
  runtime._market_requests = first_queue
  await first_queue.put(envelope)
  first_worker = asyncio.create_task(runtime._market_request_loop(Socket()))
  await asyncio.wait_for(sixth_started.wait(), timeout=2)
  shared_upload = runtime._market_upload_tasks["request-session-partial"].task

  first_worker.cancel()
  with pytest.raises(asyncio.CancelledError):
    await first_worker
  assert not shared_upload.done()
  assert runtime.broker.calls == 1

  runtime._access_token = "session-token-2"
  second_queue: asyncio.Queue = asyncio.Queue()
  runtime._market_requests = second_queue
  await second_queue.put(envelope)
  second_socket = Socket()
  second_worker = asyncio.create_task(runtime._market_request_loop(second_socket))
  await asyncio.sleep(0)
  assert runtime._market_upload_tasks["request-session-partial"].task is shared_upload

  release_sixth.set()
  await asyncio.wait_for(second_queue.join(), timeout=2)
  assert [index for index, *_ in attempts] == list(range(10))
  assert all(
    hashlib.sha256(body).hexdigest() == digest for _, body, digest, _ in attempts
  )
  assert [authorization for *_, authorization in attempts[:6]] == [
    "Bearer session-token-1"
  ] * 6
  assert [authorization for *_, authorization in attempts[6:]] == [
    "Bearer session-token-2"
  ] * 4
  assert runtime.broker.calls == 1
  assert runtime._market_upload_tasks == {}
  assert runtime._market_upload_cache == {}
  assert "request-session-partial" in runtime._market_upload_tombstones
  assert second_socket.closed == []
  sent_envelopes = [
    AgentEnvelope.model_validate_json(serialized) for serialized in second_socket.sent
  ]
  assert any(
    envelope.message_type is AgentMessageType.HEARTBEAT
    and envelope.payload["status"] == "READY"
    for envelope in sent_envelopes
  )

  attempt_count = len(attempts)
  await second_queue.put(envelope)
  await asyncio.wait_for(second_queue.join(), timeout=2)
  assert len(attempts) == attempt_count
  second_worker.cancel()
  with pytest.raises(asyncio.CancelledError):
    await second_worker
  runtime.stop()


@pytest.mark.asyncio
async def test_transient_upload_failure_retires_only_upload_task(
  monkeypatch,
) -> None:
  original_encoder = runtime_module._iter_encoded_market_data_chunks

  def one_record_chunks(records, **kwargs):
    return original_encoder(records, max_records=1, **kwargs)

  monkeypatch.setattr(
    runtime_module,
    "_iter_encoded_market_data_chunks",
    one_record_chunks,
  )

  class Broker:
    def __init__(self) -> None:
      self.calls = 0

    def iter_market_data(self, _payload):
      self.calls += 1
      return _records(2)

  attempts: list[tuple[int, bytes, str]] = []
  fail_once = True

  class Response:
    def raise_for_status(self) -> None:
      return None

  class Client:
    def __init__(self, **_kwargs) -> None:
      pass

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args) -> None:
      return None

    async def put(self, url, *, content, headers):
      nonlocal fail_once
      index = int(url.rsplit("/", 1)[1])
      body = await _read_http_content(content)
      attempts.append((index, body, headers["X-Content-SHA256"]))
      if index == 1 and fail_once:
        fail_once = False
        raise httpx.ConnectError("connection reset")
      return Response()

  monkeypatch.setattr(runtime_module.httpx, "AsyncClient", Client)
  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime.configuration = SimpleNamespace(api_url="http://127.0.0.1:8080")
  runtime._access_token = "token"
  envelope = SimpleNamespace(
    payload={"request_id": "request-transient", "operation": "bars"}
  )

  first = runtime._market_upload_task(envelope)
  with pytest.raises(httpx.ConnectError):
    await first
  await asyncio.sleep(0)
  assert runtime._market_upload_tasks == {}
  assert runtime._market_upload_cache_bytes > 0
  cached = runtime._market_upload_cache["request-transient"]
  assert cached.task is not None
  prepared = cached.task.result()
  assert prepared.spool_directory.exists()

  second = runtime._market_upload_task(envelope)
  await second
  await asyncio.sleep(0)
  assert runtime.broker.calls == 1
  assert [index for index, *_ in attempts] == [0, 1, 0, 1]
  assert attempts[:2] == attempts[2:]
  assert runtime._market_upload_tasks == {}
  assert runtime._market_upload_cache == {}
  assert "request-transient" in runtime._market_upload_tombstones
  runtime.stop()


@pytest.mark.asyncio
async def test_upload_shutdown_awaits_task_before_spool_cleanup(
  monkeypatch,
) -> None:
  upload_started = asyncio.Event()
  hold_upload = asyncio.Event()

  class Broker:
    def iter_market_data(self, _payload):
      return _records(1)

  class Client:
    def __init__(self, **_kwargs) -> None:
      pass

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args) -> None:
      return None

    async def put(self, _url, *, content, headers):
      await _read_http_content(content)
      assert headers["Authorization"] == "Bearer token"
      upload_started.set()
      await hold_upload.wait()
      raise AssertionError("cancelled upload unexpectedly resumed")

  monkeypatch.setattr(runtime_module.httpx, "AsyncClient", Client)
  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime.configuration = SimpleNamespace(api_url="http://127.0.0.1:8080")
  runtime._access_token = "token"
  envelope = SimpleNamespace(
    payload={"request_id": "request-shutdown", "operation": "bars"}
  )

  upload = runtime._market_upload_task(envelope)
  await asyncio.wait_for(upload_started.wait(), timeout=2)
  cached = runtime._market_upload_cache["request-shutdown"]
  assert cached.task is not None
  spool_directory = cached.task.result().spool_directory
  assert spool_directory.exists()
  with pytest.raises(RuntimeError, match="must stop"):
    runtime._clear_market_upload_state()

  await runtime._cancel_market_upload_tasks()
  assert upload.cancelled()
  assert runtime._market_upload_tasks == {}
  assert spool_directory.exists()
  runtime._clear_market_upload_state()
  assert not spool_directory.exists()
  assert runtime._market_upload_cache == {}


@pytest.mark.asyncio
async def test_session_supervision_closes_on_heartbeat_failure() -> None:
  class Socket:
    def __init__(self) -> None:
      self.closed: list[tuple[int, str]] = []

    async def close(self, *, code: int, reason: str) -> None:
      self.closed.append((code, reason))

  receiver_release = asyncio.Event()

  async def receiver() -> None:
    await receiver_release.wait()

  async def failed_heartbeat() -> None:
    raise RuntimeError("heartbeat send failed")

  runtime = object.__new__(AgentRuntime)
  socket = Socket()
  receiver_task = asyncio.create_task(receiver())
  heartbeat_task = asyncio.create_task(failed_heartbeat())
  try:
    with pytest.raises(RuntimeError, match="heartbeat send failed"):
      await runtime._supervise_session_tasks(
        socket,
        {
          "receiver": receiver_task,
          "heartbeat": heartbeat_task,
        },
      )
  finally:
    receiver_task.cancel()
    await asyncio.gather(receiver_task, return_exceptions=True)

  assert socket.closed == [(1011, "session task failed: heartbeat")]


@pytest.mark.asyncio
async def test_market_worker_heartbeats_before_dequeuing_next_request(
  monkeypatch,
) -> None:
  handled: list[str] = []
  first_checkpoint_started = asyncio.Event()
  release_first_checkpoint = asyncio.Event()
  second_request_handled = asyncio.Event()

  async def handle(envelope) -> None:
    request_id = str(envelope.payload["request_id"])
    handled.append(request_id)
    if request_id == "request-2":
      second_request_handled.set()

  class Socket:
    def __init__(self) -> None:
      self.sent: list[str] = []

    async def send(self, serialized: str) -> None:
      self.sent.append(serialized)
      envelope = AgentEnvelope.model_validate_json(serialized)
      if (
        envelope.message_type is AgentMessageType.HEARTBEAT
        and not first_checkpoint_started.is_set()
      ):
        first_checkpoint_started.set()
        await release_first_checkpoint.wait()

    async def close(self, *, code: int, reason: str) -> None:
      raise AssertionError(f"unexpected close: {code} {reason}")

  runtime = object.__new__(AgentRuntime)
  runtime.configuration = SimpleNamespace(device_id="device-1")
  runtime.mode = "data-only"
  runtime.emergency_stop = None
  runtime.journal = SimpleNamespace(
    stats=lambda: {
      "integrity": "ok",
      "size_bytes": 0,
      "pending_reports": 0,
      "processing_commands": 0,
    },
    pending_reports=lambda: [],
  )
  runtime._ensure_market_upload_state()
  monkeypatch.setattr(runtime, "_handle_market_data_request", handle)
  runtime._market_requests = asyncio.Queue()
  await runtime._market_requests.put(
    SimpleNamespace(payload={"request_id": "request-1"})
  )
  await runtime._market_requests.put(
    SimpleNamespace(payload={"request_id": "request-2"})
  )

  worker = asyncio.create_task(runtime._market_request_loop(Socket()))
  await asyncio.wait_for(first_checkpoint_started.wait(), timeout=1)
  assert handled == ["request-1"]

  release_first_checkpoint.set()
  await asyncio.wait_for(second_request_handled.wait(), timeout=1)
  await asyncio.wait_for(runtime._market_requests.join(), timeout=1)
  assert handled == ["request-1", "request-2"]
  worker.cancel()
  await asyncio.gather(worker, return_exceptions=True)
  runtime.stop()


@pytest.mark.asyncio
async def test_invalid_market_request_is_failed_without_closing_session(
  monkeypatch,
) -> None:
  handled: list[str] = []
  failures: list[tuple[str, str]] = []
  second_handled = asyncio.Event()

  async def handle(envelope) -> None:
    request_id = str(envelope.payload["request_id"])
    handled.append(request_id)
    if request_id == "invalid-request":
      raise ValueError("instrument count limit")
    second_handled.set()

  async def report_failure(request_id: str, error: Exception) -> None:
    failures.append((request_id, str(error)))

  async def checkpoint(_socket, *, status: str) -> None:
    assert status == "READY"

  class Socket:
    async def close(self, *, code: int, reason: str) -> None:
      raise AssertionError(f"unexpected close: {code} {reason}")

  runtime = object.__new__(AgentRuntime)
  runtime._ensure_market_upload_state()
  monkeypatch.setattr(runtime, "_handle_market_data_request", handle)
  monkeypatch.setattr(runtime, "_report_market_data_failure", report_failure)
  monkeypatch.setattr(runtime, "_heartbeat_checkpoint", checkpoint)
  runtime._market_requests = asyncio.Queue()
  await runtime._market_requests.put(
    SimpleNamespace(payload={"request_id": "invalid-request"})
  )
  await runtime._market_requests.put(
    SimpleNamespace(payload={"request_id": "valid-request"})
  )

  worker = asyncio.create_task(runtime._market_request_loop(Socket()))
  await asyncio.wait_for(second_handled.wait(), timeout=1)
  await asyncio.wait_for(runtime._market_requests.join(), timeout=1)

  assert handled == ["invalid-request", "valid-request"]
  assert failures == [("invalid-request", "instrument count limit")]
  worker.cancel()
  await asyncio.gather(worker, return_exceptions=True)
  runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "failure_kind",
  ["connect", "timeout", "401", "403", "408", "429", "500", "503"],
)
async def test_retryable_upload_failure_reconnects_without_failing_request(
  monkeypatch,
  failure_kind: str,
) -> None:
  handled: list[str] = []
  failures: list[str] = []
  checkpoints: list[str] = []

  async def handle(envelope) -> None:
    handled.append(str(envelope.payload["request_id"]))
    raise _retryable_upload_error(failure_kind)

  async def report_failure(request_id: str, _error: Exception) -> None:
    failures.append(request_id)

  async def checkpoint(_socket, *, status: str) -> None:
    checkpoints.append(status)

  class Socket:
    def __init__(self) -> None:
      self.closed: list[tuple[int, str]] = []

    async def close(self, *, code: int, reason: str) -> None:
      self.closed.append((code, reason))

  runtime = object.__new__(AgentRuntime)
  runtime._ensure_market_upload_state()
  monkeypatch.setattr(runtime, "_handle_market_data_request", handle)
  monkeypatch.setattr(runtime, "_report_market_data_failure", report_failure)
  monkeypatch.setattr(runtime, "_heartbeat_checkpoint", checkpoint)
  runtime._market_requests = asyncio.Queue()
  await runtime._market_requests.put(
    SimpleNamespace(payload={"request_id": "request-retry"})
  )
  await runtime._market_requests.put(
    SimpleNamespace(payload={"request_id": "request-after-retry"})
  )
  socket = Socket()

  await runtime._market_request_loop(socket)
  await asyncio.sleep(0)

  assert handled == ["request-retry"]
  assert failures == []
  assert checkpoints == []
  assert socket.closed == [(1012, "market data upload retry")]
  assert runtime._market_requests.qsize() == 1
  queued = runtime._market_requests.get_nowait()
  assert queued.payload["request_id"] == "request-after-retry"
  runtime._market_requests.task_done()
  assert runtime._market_upload_tasks == {}
  runtime._clear_market_upload_state()
  runtime.stop()


@pytest.mark.asyncio
async def test_request_loop_transport_failure_preserves_immutable_spool(
  monkeypatch,
) -> None:
  class Broker:
    def __init__(self) -> None:
      self.calls = 0

    def iter_market_data(self, _payload):
      self.calls += 1
      return _records(2)

  uploaded: list[tuple[bytes, str]] = []

  class Client:
    def __init__(self, **_kwargs) -> None:
      pass

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args) -> None:
      return None

    async def put(self, url, *, content, headers):
      body = await _read_http_content(content)
      uploaded.append((body, headers["X-Content-SHA256"]))
      raise httpx.ConnectError(
        "connection reset",
        request=httpx.Request("PUT", url),
      )

  failures: list[str] = []

  async def report_failure(request_id: str, _error: Exception) -> None:
    failures.append(request_id)

  class Socket:
    def __init__(self) -> None:
      self.closed: list[tuple[int, str]] = []

    async def close(self, *, code: int, reason: str) -> None:
      self.closed.append((code, reason))

  monkeypatch.setattr(runtime_module.httpx, "AsyncClient", Client)
  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime.configuration = SimpleNamespace(api_url="http://127.0.0.1:8080")
  runtime._access_token = "token"
  runtime._ensure_market_upload_state()
  monkeypatch.setattr(runtime, "_report_market_data_failure", report_failure)
  runtime._market_requests = asyncio.Queue()
  await runtime._market_requests.put(
    SimpleNamespace(
      payload={"request_id": "request-spooled-retry", "operation": "bars"}
    )
  )
  socket = Socket()

  await runtime._market_request_loop(socket)
  await runtime._market_requests.join()
  await asyncio.sleep(0)

  assert runtime.broker.calls == 1
  assert failures == []
  assert socket.closed == [(1012, "market data upload retry")]
  assert len(uploaded) == 1
  assert hashlib.sha256(uploaded[0][0]).hexdigest() == uploaded[0][1]
  cached = runtime._market_upload_cache["request-spooled-retry"]
  assert cached.task is not None and cached.task.done()
  prepared = cached.task.result()
  assert prepared.spool_directory.exists()
  assert prepared.chunks[0].path.read_bytes() == uploaded[0][0]
  assert runtime._market_upload_cache_bytes == prepared.compressed_bytes
  assert runtime._market_upload_tasks == {}
  runtime._clear_market_upload_state()
  runtime.stop()


@pytest.mark.asyncio
async def test_nonretryable_upload_contract_response_is_failed_without_reconnect(
  monkeypatch,
) -> None:
  handled: list[str] = []
  failures: list[tuple[str, int]] = []
  second_handled = asyncio.Event()

  async def handle(envelope) -> None:
    request_id = str(envelope.payload["request_id"])
    handled.append(request_id)
    if request_id == "request-invalid-upload":
      error = _retryable_upload_error("422")
      assert isinstance(error, httpx.HTTPStatusError)
      raise error
    second_handled.set()

  async def report_failure(request_id: str, error: Exception) -> None:
    assert isinstance(error, httpx.HTTPStatusError)
    failures.append((request_id, error.response.status_code))

  async def checkpoint(_socket, *, status: str) -> None:
    assert status == "READY"

  class Socket:
    def __init__(self) -> None:
      self.closed: list[tuple[int, str]] = []

    async def close(self, *, code: int, reason: str) -> None:
      self.closed.append((code, reason))

  runtime = object.__new__(AgentRuntime)
  runtime._ensure_market_upload_state()
  monkeypatch.setattr(runtime, "_handle_market_data_request", handle)
  monkeypatch.setattr(runtime, "_report_market_data_failure", report_failure)
  monkeypatch.setattr(runtime, "_heartbeat_checkpoint", checkpoint)
  runtime._market_requests = asyncio.Queue()
  await runtime._market_requests.put(
    SimpleNamespace(payload={"request_id": "request-invalid-upload"})
  )
  await runtime._market_requests.put(
    SimpleNamespace(payload={"request_id": "request-valid-upload"})
  )
  socket = Socket()

  worker = asyncio.create_task(runtime._market_request_loop(socket))
  await asyncio.wait_for(second_handled.wait(), timeout=1)
  await asyncio.wait_for(runtime._market_requests.join(), timeout=1)

  assert handled == ["request-invalid-upload", "request-valid-upload"]
  assert failures == [("request-invalid-upload", 422)]
  assert socket.closed == []
  worker.cancel()
  await asyncio.gather(worker, return_exceptions=True)
  runtime._clear_market_upload_state()
  runtime.stop()


@pytest.mark.asyncio
async def test_post_upload_checkpoint_failure_never_fails_durable_request(
  monkeypatch,
) -> None:
  failures: list[str] = []

  async def handle(_envelope) -> None:
    return None

  async def report_failure(request_id: str, _error: Exception) -> None:
    failures.append(request_id)

  async def checkpoint(_socket, *, status: str) -> None:
    assert status == "READY"
    raise httpx.ConnectError(
      "control session disconnected",
      request=httpx.Request("POST", "http://127.0.0.1/heartbeat"),
    )

  class Socket:
    async def close(self, *, code: int, reason: str) -> None:
      raise AssertionError(f"request loop unexpectedly closed socket: {code} {reason}")

  runtime = object.__new__(AgentRuntime)
  runtime._ensure_market_upload_state()
  monkeypatch.setattr(runtime, "_handle_market_data_request", handle)
  monkeypatch.setattr(runtime, "_report_market_data_failure", report_failure)
  monkeypatch.setattr(runtime, "_heartbeat_checkpoint", checkpoint)
  runtime._market_requests = asyncio.Queue()
  await runtime._market_requests.put(
    SimpleNamespace(payload={"request_id": "request-uploaded"})
  )

  with pytest.raises(httpx.ConnectError, match="control session disconnected"):
    await runtime._market_request_loop(Socket())
  await runtime._market_requests.join()

  assert failures == []
  runtime._clear_market_upload_state()
  runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_status", ["RECONCILING", "READY"])
async def test_heartbeat_never_reports_ready_state_when_xtdata_is_unavailable(
  requested_status: str,
) -> None:
  class Socket:
    def __init__(self) -> None:
      self.sent: list[str] = []

    async def send(self, serialized: str) -> None:
      self.sent.append(serialized)

  runtime = object.__new__(AgentRuntime)
  runtime.configuration = SimpleNamespace(device_id="device-1")
  runtime.mode = "data-only"
  runtime.emergency_stop = None
  runtime.broker = SimpleNamespace(is_market_data_ready=lambda: False)
  runtime.journal = SimpleNamespace(
    stats=lambda: {
      "integrity": "ok",
      "size_bytes": 0,
      "pending_reports": 0,
      "processing_commands": 0,
    }
  )
  runtime._websocket_send_lock = asyncio.Lock()
  socket = Socket()

  await runtime._send_heartbeat(socket, status=requested_status)

  envelope = AgentEnvelope.model_validate_json(socket.sent[0])
  assert envelope.message_type is AgentMessageType.HEARTBEAT
  assert envelope.payload["status"] == "XTDATA_UNAVAILABLE"
  assert envelope.payload["xtdata_status"] == "DISCONNECTED"
  assert envelope.payload["xtdata_reason"] == "XTDATA_UNAVAILABLE"
  assert envelope.payload["xttrading_status"] == "DISABLED"


@pytest.mark.asyncio
async def test_live_heartbeat_reports_trading_unavailable() -> None:
  class Socket:
    def __init__(self) -> None:
      self.sent: list[str] = []

    async def send(self, serialized: str) -> None:
      self.sent.append(serialized)

  runtime = object.__new__(AgentRuntime)
  runtime.configuration = SimpleNamespace(device_id="device-1")
  runtime.mode = "live"
  runtime.emergency_stop = None
  runtime.broker = SimpleNamespace(
    is_market_data_ready=lambda: True,
    is_trading_ready=lambda: False,
  )
  runtime.journal = SimpleNamespace(
    stats=lambda: {
      "integrity": "ok",
      "size_bytes": 0,
      "pending_reports": 0,
      "processing_commands": 0,
    }
  )
  runtime._websocket_send_lock = asyncio.Lock()
  socket = Socket()

  await runtime._send_heartbeat(socket, status="READY")

  envelope = AgentEnvelope.model_validate_json(socket.sent[0])
  assert envelope.payload["status"] == "TRADING_UNAVAILABLE"
  assert envelope.payload["xtdata_status"] == "CONNECTED"
  assert envelope.payload["xttrading_status"] == "DISCONNECTED"
  assert envelope.payload["xttrading_reason"] == "XTTRADING_UNAVAILABLE"


@pytest.mark.asyncio
async def test_chunk_identity_conflict_trips_fatal_stop(monkeypatch) -> None:
  class Broker:
    def iter_market_data(self, _payload):
      return _records(1)

  class Client:
    def __init__(self, **_kwargs) -> None:
      pass

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args) -> None:
      return None

    async def put(self, url, *, content, headers):
      await _read_http_content(content)
      request = httpx.Request("PUT", url, headers=headers)
      return httpx.Response(
        409,
        request=request,
        json={"detail": "重复批次内容不一致"},
      )

  monkeypatch.setattr(runtime_module.httpx, "AsyncClient", Client)
  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime.configuration = SimpleNamespace(api_url="http://127.0.0.1:8080")
  runtime._access_token = "token"
  envelope = SimpleNamespace(
    payload={"request_id": "request-conflict", "operation": "bars"}
  )

  with pytest.raises(runtime_module._FatalMarketDataUploadConflict):
    await runtime._handle_market_data_request(envelope)
  assert runtime._stopped.is_set()
  assert runtime._fatal_market_data_event.is_set()
  runtime.stop()


@pytest.mark.asyncio
async def test_preparation_runs_off_event_loop(monkeypatch) -> None:
  main_thread = threading.get_ident()
  preparation_threads: list[int] = []
  original = runtime_module._prepare_market_data_spool_sync

  def wrapped(*args, **kwargs):
    preparation_threads.append(threading.get_ident())
    return original(*args, **kwargs)

  class Broker:
    def iter_market_data(self, _payload):
      return _records(1)

  monkeypatch.setattr(
    runtime_module,
    "_prepare_market_data_spool_sync",
    wrapped,
  )
  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  await runtime._prepared_market_data_chunks(
    "request-thread",
    {"request_id": "request-thread", "operation": "bars"},
  )

  assert preparation_threads
  assert all(thread_id != main_thread for thread_id in preparation_threads)
  runtime.stop()


@pytest.mark.asyncio
async def test_hung_native_timeout_trips_fail_stop_without_reentry(
  monkeypatch,
) -> None:
  started = threading.Event()
  release = threading.Event()

  class Broker:
    def __init__(self) -> None:
      self.calls = 0

    def iter_market_data(self, _payload):
      self.calls += 1
      started.set()
      release.wait(timeout=2)
      return _records(1)

  monkeypatch.setattr(
    runtime_module,
    "MARKET_DATA_PREPARATION_TIMEOUT_SECONDS",
    0.05,
  )
  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  task = asyncio.create_task(
    runtime._prepared_market_data_chunks(
      "request-hung",
      {"request_id": "request-hung", "operation": "bars"},
    )
  )
  assert await asyncio.to_thread(started.wait, 1)
  with pytest.raises(
    runtime_module._FatalMarketDataPreparationError,
    match="Agent restart required",
  ):
    await task

  assert runtime._stopped.is_set()
  assert runtime.broker.calls == 1
  with pytest.raises(runtime_module._FatalMarketDataPreparationError):
    await runtime._prepared_market_data_chunks(
      "request-after-fatal",
      {"request_id": "request-after-fatal", "operation": "bars"},
    )
  assert runtime.broker.calls == 1
  release.set()
  await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_disconnect_reconnect_shared_request_timeout_exits_runtime(
  monkeypatch,
) -> None:
  started = threading.Event()
  release = threading.Event()

  class Broker:
    def __init__(self) -> None:
      self.calls = 0

    def iter_market_data(self, _payload):
      self.calls += 1
      started.set()
      release.wait(timeout=2)
      return _records(1)

  monkeypatch.setattr(
    runtime_module,
    "MARKET_DATA_PREPARATION_TIMEOUT_SECONDS",
    0.08,
  )
  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  session_calls = 0

  async def session():
    nonlocal session_calls
    session_calls += 1
    if session_calls == 1:
      disconnected = asyncio.create_task(
        runtime._prepared_market_data_chunks(
          "request-shared",
          {"request_id": "request-shared", "operation": "bars"},
        )
      )
      assert await asyncio.to_thread(started.wait, 1)
      disconnected.cancel()
      with pytest.raises(asyncio.CancelledError):
        await disconnected
      return
    await runtime._prepared_market_data_chunks(
      "request-shared",
      {"request_id": "request-shared", "operation": "bars"},
    )

  runtime._run_session = session
  with pytest.raises(
    runtime_module._FatalMarketDataPreparationError,
    match="Agent restart required",
  ):
    await asyncio.wait_for(runtime.run_forever(), timeout=1)

  assert session_calls == 2
  assert runtime.broker.calls == 1
  release.set()
  await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_history_and_subscription_share_one_xtdata_gate() -> None:
  history_started = threading.Event()
  history_release = threading.Event()
  subscribe_started = threading.Event()

  class Broker:
    def iter_market_data(self, _payload):
      history_started.set()
      history_release.wait(timeout=2)
      return _records(1)

    def subscribe_market(self, _payload, _callback):
      subscribe_started.set()
      return True

  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  history = asyncio.create_task(
    runtime._prepared_market_data_chunks(
      "request-history",
      {"request_id": "request-history", "operation": "bars"},
    )
  )
  assert await asyncio.to_thread(history_started.wait, 1)
  subscription = asyncio.create_task(
    runtime._run_xtdata_control(
      "subscribe-market",
      runtime.broker.subscribe_market,
      {},
      lambda _payload: None,
    )
  )
  await asyncio.sleep(0.03)
  assert not subscribe_started.is_set()

  history_release.set()
  await asyncio.wait_for(history, timeout=1)
  assert await asyncio.to_thread(subscribe_started.wait, 1)
  assert await asyncio.wait_for(subscription, timeout=1) is True
  runtime.stop()


@pytest.mark.asyncio
async def test_fatal_rejects_new_xtdata_control_entry() -> None:
  called = False
  runtime = object.__new__(AgentRuntime)
  runtime._ensure_market_upload_state()
  fatal = runtime_module._FatalMarketDataPreparationError("restart")
  runtime._trip_market_data_fatal(fatal)

  def control():
    nonlocal called
    called = True

  with pytest.raises(runtime_module._FatalMarketDataPreparationError):
    await runtime._run_xtdata_control("control", control)
  assert not called
  runtime.stop()


@pytest.mark.asyncio
async def test_full_market_request_queue_closes_without_blocking() -> None:
  runtime = object.__new__(AgentRuntime)
  runtime._ensure_market_upload_state()
  runtime._market_requests = asyncio.Queue(maxsize=1)
  runtime._market_requests.put_nowait(
    AgentEnvelope(
      message_type=AgentMessageType.MARKET_DATA_REQUEST,
      payload={"request_id": "queued"},
    )
  )

  class Socket:
    def __init__(self) -> None:
      self.closed = []

    async def close(self, **kwargs) -> None:
      self.closed.append(kwargs)

  socket = Socket()
  incoming = AgentEnvelope(
    message_type=AgentMessageType.MARKET_DATA_REQUEST,
    payload={"request_id": "overflow"},
  )
  await asyncio.wait_for(
    runtime._handle_message(socket, incoming.model_dump_json()),
    timeout=0.2,
  )

  assert runtime._market_requests.qsize() == 1
  assert socket.closed == [{"code": 1013, "reason": "market-data request queue full"}]
  runtime.stop()


@pytest.mark.asyncio
async def test_run_forever_propagates_fatal_for_supervisor_restart() -> None:
  runtime = object.__new__(AgentRuntime)
  runtime._stopped = asyncio.Event()
  runtime._stopped.set()
  runtime._fatal_market_data_error = runtime_module._FatalMarketDataPreparationError(
    "restart required"
  )

  with pytest.raises(
    runtime_module._FatalMarketDataPreparationError,
    match="restart required",
  ):
    await runtime.run_forever()


@pytest.mark.asyncio
async def test_cache_budget_ttl_and_terminal_cleanup(monkeypatch) -> None:
  class Broker:
    def iter_market_data(self, _payload):
      return _records(1)

  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  clock = [100.0]
  runtime._market_upload_clock = lambda: clock[0]
  chunks = await runtime._prepared_market_data_chunks(
    "request-ttl",
    {"request_id": "request-ttl", "operation": "bars"},
  )
  cached_bytes = sum(chunk.compressed_bytes for chunk in chunks)
  paths = [chunk.path for chunk in chunks]
  assert runtime._market_upload_cache_bytes == cached_bytes

  clock[0] += runtime_module.MARKET_DATA_UPLOAD_CACHE_TTL_SECONDS + 1
  runtime._cleanup_expired_market_uploads()
  assert runtime._market_upload_cache == {}
  assert runtime._market_upload_cache_bytes == 0
  assert not any(path.exists() for path in paths)

  monkeypatch.setattr(
    runtime_module,
    "MAX_MARKET_DATA_UPLOAD_CACHE_BYTES",
    cached_bytes - 1,
  )
  with pytest.raises(RuntimeError, match="cache byte limit"):
    await runtime._prepared_market_data_chunks(
      "request-too-large",
      {"request_id": "request-too-large", "operation": "bars"},
    )
  assert runtime._market_upload_cache == {}
  assert runtime._market_upload_cache_bytes == 0


@pytest.mark.asyncio
async def test_partial_spool_files_count_against_runtime_quota(
  monkeypatch,
) -> None:
  class Broker:
    def __init__(self) -> None:
      self.calls = 0

    def iter_market_data(self, _payload):
      self.calls += 1
      return _records(1)

  runtime = object.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._ensure_market_upload_state()
  partial = runtime._market_spool_root / "request-partial-orphan"
  partial.mkdir()
  (partial / "chunk.partial").write_bytes(b"x" * 64)
  monkeypatch.setattr(
    runtime_module,
    "MAX_MARKET_DATA_UPLOAD_CACHE_BYTES",
    64,
  )

  with pytest.raises(RuntimeError, match="cache byte limit"):
    await runtime._prepared_market_data_chunks(
      "request-over-quota",
      {"request_id": "request-over-quota", "operation": "bars"},
    )
  assert runtime.broker.calls == 0
  runtime.stop()


def test_bars_request_preflight_accepts_campaign_and_rejects_oom_shapes() -> None:
  campaign_codes = [f"{index:06d}.SZ" for index in range(200)]
  validated = _validate_bars_request(
    {
      "operation": "bars",
      "stock_list": campaign_codes,
      "periods": ["1d"],
      "start_time": "20200101",
      "end_time": "20260729",
    }
  )
  assert len(validated.codes) == 200

  with pytest.raises(ValueError, match="canonical instrument codes"):
    _validate_bars_request(
      {
        "stock_list": ["000001.sz"],
        "periods": ["1d"],
        "start_time": "20250101",
        "end_time": "20250101",
      }
    )
  with pytest.raises(ValueError, match="canonical values"):
    _validate_bars_request(
      {
        "stock_list": ["000001.SZ"],
        "periods": ["TICK"],
        "start_time": "20250101",
        "end_time": "20250101",
      }
    )

  with pytest.raises(ValueError, match="instrument count limit"):
    _validate_bars_request(
      {
        "stock_list": [
          f"{index:06d}.SZ" for index in range(broker_module.MAX_MARKET_DATA_CODES + 1)
        ],
        "periods": ["1d"],
        "start_time": "20250101",
        "end_time": "20250101",
      }
    )
  with pytest.raises(ValueError, match="unsupported periods"):
    _validate_bars_request(
      {
        "stock_list": ["000001.SZ"],
        "periods": ["5m"],
        "start_time": "20250101",
        "end_time": "20250101",
      }
    )
  with pytest.raises(ValueError, match="date span"):
    _validate_bars_request(
      {
        "stock_list": ["000001.SZ"],
        "periods": ["1m"],
        "start_time": "20250101",
        "end_time": "20250301",
      }
    )
  with pytest.raises(ValueError, match="estimated record count"):
    _validate_bars_request(
      {
        "stock_list": [
          "000001.SZ",
          "000002.SZ",
          "000003.SZ",
          "000004.SZ",
        ],
        "periods": ["tick"],
        "start_time": "20250101",
        "end_time": "20250107",
      }
    )


def test_bars_response_rejects_unrequested_code_and_out_of_range_time() -> None:
  class ExtraCodeManager:
    def get_market_data(self, **_kwargs):
      return {"600000.SH": pd.DataFrame([{"time": 20250102, "close": 10.0}])}

  payload = {
    "operation": "bars",
    "stock_list": ["000001.SZ"],
    "periods": ["1d"],
    "start_time": "20250102",
    "end_time": "20250102",
  }
  with pytest.raises(ValueError, match="unrequested instrument"):
    _market_data_records(ExtraCodeManager(), payload)

  class OutOfRangeManager:
    def get_market_data(self, **_kwargs):
      return {"000001.SZ": pd.DataFrame([{"time": 20250103, "close": 10.0}])}

  with pytest.raises(ValueError, match="outside requested range"):
    _market_data_records(OutOfRangeManager(), payload)


def test_daily_keys_and_canonical_json_are_cold_restart_stable(
  tmp_path,
) -> None:
  payload = {
    "operation": "bars",
    "stock_list": ["000001.SZ"],
    "periods": ["1d"],
    "start_time": "20250102",
    "end_time": "20250102",
  }

  class FirstManager:
    def get_market_data(self, **_kwargs):
      return {
        "000001.SZ": pd.DataFrame([{"close": 10.0, "time": "2025-01-02T00:00:00Z"}])
      }

  class SecondManager:
    def get_market_data(self, **_kwargs):
      frame = pd.DataFrame([{"time": datetime(2025, 1, 2, 9, 30), "close": 10.0}])
      return {"000001.SZ": frame[["time", "close"]]}

  first_records = _market_data_records(FirstManager(), payload)
  second_records = _market_data_records(SecondManager(), payload)
  expected_key = _normalize_market_timestamp(datetime(2025, 1, 2))
  assert first_records[0]["time"] == expected_key
  assert second_records[0]["time"] == expected_key
  assert _normalize_daily_market_timestamp(20250102) == expected_key

  class RecordsBroker:
    def __init__(self, records) -> None:
      self.records = records

    def iter_market_data(self, _payload):
      return iter(self.records)

  first = _prepare_spool(
    RecordsBroker(first_records),
    tmp_path / "cold-first",
  )
  second = _prepare_spool(
    RecordsBroker(second_records),
    tmp_path / "cold-second",
  )
  assert _spool_bytes(first) == _spool_bytes(second)
  assert [chunk.digest for chunk in first.chunks] == [
    chunk.digest for chunk in second.chunks
  ]


def test_intraday_time_keeps_exact_instant() -> None:
  naive = _normalize_market_timestamp(datetime(2025, 1, 2, 9, 30))
  aware = _normalize_market_timestamp("2025-01-02T01:30:00Z")
  assert naive == aware
  assert _normalize_daily_market_timestamp(naive) != naive


def test_broker_streams_rows_without_dataframe_to_dict(monkeypatch) -> None:
  frame = pd.DataFrame(
    [
      {"time": 20250103, "close": 10.3},
      {"time": 20250101, "close": 10.1},
      {"time": 20250102, "close": 10.2},
    ]
  )

  class Manager:
    def get_market_data(self, **_kwargs):
      return {"600000.SH": frame}

  def reject_to_dict(*_args, **_kwargs):
    raise AssertionError("bars must not materialize DataFrame.to_dict")

  monkeypatch.setattr(pd.DataFrame, "to_dict", reject_to_dict)
  records = list(
    _iter_market_data_records(
      Manager(),
      {
        "operation": "bars",
        "stock_list": ["600000.SH"],
        "periods": ["1d"],
        "start_time": "20250101",
        "end_time": "20250103",
        "download": False,
      },
    )
  )
  assert [record["time"] for record in _bar_rows(records)] == [
    _normalize_market_timestamp(datetime(2025, 1, 1)),
    _normalize_market_timestamp(datetime(2025, 1, 2)),
    _normalize_market_timestamp(datetime(2025, 1, 3)),
  ]


def test_broker_record_limit_fails_during_iteration() -> None:
  class Manager:
    def get_market_data(self, **_kwargs):
      return {
        "600000.SH": pd.DataFrame([{"time": 20250101 + index} for index in range(3)])
      }

  with pytest.raises(ValueError, match="record count limit"):
    list(
      _iter_market_data_records(
        Manager(),
        {
          "operation": "bars",
          "stock_list": ["600000.SH"],
          "periods": ["1d"],
          "start_time": "20250101",
          "end_time": "20250103",
          "download": False,
        },
        max_records=2,
      )
    )


def test_broker_rejects_an_unbounded_single_frame(monkeypatch) -> None:
  base = 1_735_776_000_000

  class Manager:
    def get_market_data(self, **_kwargs):
      return {"600000.SH": pd.DataFrame([{"time": base + index} for index in range(3)])}

  monkeypatch.setattr(
    broker_module,
    "MAX_MARKET_DATA_FRAME_RECORDS",
    2,
  )
  with pytest.raises(ValueError, match="single market data frame"):
    list(
      _iter_market_data_records(
        Manager(),
        {
          "operation": "bars",
          "stock_list": ["600000.SH"],
          "periods": ["1d"],
          "start_time": "20250102",
          "end_time": "20250102",
          "download": False,
        },
      )
    )


def test_market_data_records_are_sorted_by_code_and_time() -> None:
  class Manager:
    def get_market_data(self, **_kwargs):
      return {
        "600000.SH": pd.DataFrame(
          [
            {"time": 20250103, "close": 10.3},
            {"time": 20250101, "close": 10.1},
          ]
        ),
        "000001.SZ": pd.DataFrame([{"time": 20250102, "close": 9.9}]),
      }

  records = _market_data_records(
    Manager(),
    {
      "operation": "bars",
      "stock_list": ["600000.SH", "000001.SZ"],
      "periods": ["1d"],
      "start_time": "20250101",
      "end_time": "20250103",
      "download": False,
    },
  )
  assert [(record["code"], record["time"]) for record in _bar_rows(records)] == [
    (
      "000001.SZ",
      _normalize_market_timestamp(datetime(2025, 1, 2)),
    ),
    (
      "600000.SH",
      _normalize_market_timestamp(datetime(2025, 1, 1)),
    ),
    (
      "600000.SH",
      _normalize_market_timestamp(datetime(2025, 1, 3)),
    ),
  ]


def test_missing_requested_code_emits_explicit_empty_summary() -> None:
  class Manager:
    def get_market_data(self, **_kwargs):
      return {
        "600000.SH": pd.DataFrame([{"time": 20250102, "close": 10.1}]),
      }

  records = _market_data_records(
    Manager(),
    {
      "operation": "bars",
      "stock_list": ["600000.SH", "000001.SZ"],
      "periods": ["1d"],
      "start_time": "20250102",
      "end_time": "20250102",
      "download": False,
    },
  )

  assert [record["code"] for record in _bar_rows(records)] == ["600000.SH"]
  summaries = {
    (summary["code"], summary["period"]): summary
    for summary in _bar_summaries(records)
  }
  assert set(summaries) == {("000001.SZ", "1d"), ("600000.SH", "1d")}
  empty = summaries[("000001.SZ", "1d")]
  assert empty["row_count"] == 0
  assert empty["min_time"] is None
  assert empty["max_time"] is None
  assert empty["key_sha256"] == hashlib.sha256(b"").hexdigest()
  assert empty["no_data_reason"] == HISTORICAL_BAR_NO_DATA_REASON
  assert summaries[("600000.SH", "1d")]["row_count"] == 1


def test_bars_request_rejects_non_object_xtdata_result() -> None:
  class Manager:
    def get_market_data(self, **_kwargs):
      return []

  with pytest.raises(ValueError, match="non-object market-data result"):
    _market_data_records(
      Manager(),
      {
        "operation": "bars",
        "stock_list": ["600000.SH"],
        "periods": ["1d"],
        "start_time": "20250102",
        "end_time": "20250102",
        "download": False,
      },
    )


def test_bars_request_rejects_non_dataframe_instrument_result() -> None:
  class Manager:
    def get_market_data(self, **_kwargs):
      return {"600000.SH": []}

  with pytest.raises(ValueError, match="non-DataFrame result"):
    _market_data_records(
      Manager(),
      {
        "operation": "bars",
        "stock_list": ["600000.SH"],
        "periods": ["1d"],
        "start_time": "20250102",
        "end_time": "20250102",
        "download": False,
      },
    )


def test_tick_records_preserve_same_millisecond_with_stable_ordinals() -> None:
  source_time = _normalize_market_timestamp(datetime(2025, 1, 2, 9, 30))
  rows = [
    {
      "time": source_time,
      "lastPrice": 10.0,
      "amount": 1_000.0,
      "volume": 100,
      "transactionNum": 10,
      "bidVol": [90, 0, 0, 0, 0],
      "tickvol": 1,
    },
    {
      "time": source_time,
      "lastPrice": 10.0,
      "amount": 1_000.0,
      "volume": 100,
      "transactionNum": 10,
      "bidVol": [100, 0, 0, 0, 0],
      "tickvol": 2,
    },
    {
      "time": source_time + 1,
      "lastPrice": 10.01,
      "amount": 1_001.0,
      "volume": 101,
      "transactionNum": 11,
      "bidVol": [101, 0, 0, 0, 0],
      "tickvol": 1,
    },
  ]
  payload = {
    "operation": "bars",
    "stock_list": ["601318.SH"],
    "periods": ["tick"],
    "start_time": "20250102",
    "end_time": "20250102",
    "download": False,
  }

  class Manager:
    def __init__(self, values) -> None:
      self.values = values

    def get_market_data(self, **_kwargs):
      return {"601318.SH": pd.DataFrame(self.values)}

  forward_records = _market_data_records(Manager(rows), payload)
  reversed_records = _market_data_records(Manager(list(reversed(rows))), payload)
  forward = _bar_rows(forward_records)
  reversed_rows = _bar_rows(reversed_records)

  def ordinal_by_snapshot(records):
    return {
      (record["time"], tuple(record["bidVol"])): record[
        HISTORICAL_TICK_ORDINAL_FIELD
      ]
      for record in records
    }

  assert len(forward) == len(rows)
  assert [record["time"] for record in forward] == [
    source_time,
    source_time,
    source_time + 1,
  ]
  assert [record[HISTORICAL_TICK_ORDINAL_FIELD] for record in forward] == [0, 1, 0]
  assert ordinal_by_snapshot(forward) == ordinal_by_snapshot(reversed_rows)
  assert _bar_summaries(forward_records) == _bar_summaries(reversed_records)
  assert _bar_summaries(forward_records)[0]["row_count"] == len(rows)


@pytest.mark.asyncio
async def test_historical_tick_projection_drops_vendor_fields_before_durable_ingestion() -> None:
  source_time = _normalize_market_timestamp(datetime(2025, 1, 2, 9, 30))
  payload = {
    "operation": "bars",
    "stock_list": ["601318.SH"],
    "periods": ["tick"],
    "start_time": "20250102",
    "end_time": "20250102",
    "download": False,
  }

  def tick(*, transaction_num: int, pe: float, vendor_extra: str) -> dict:
    return {
      "time": source_time,
      "lastPrice": 10.0 + transaction_num / 100,
      "open": 9.9,
      "high": 10.2,
      "low": 9.8,
      "lastClose": 9.85,
      "amount": 100_000.0 + transaction_num,
      "volume": 10_000.0 + transaction_num,
      "pvolume": 9_000.0 + transaction_num,
      "tickvol": float(transaction_num),
      "stockStatus": 0,
      "openInt": 0,
      "lastSettlementPrice": 0.0,
      "settlementPrice": 0.0,
      "transactionNum": transaction_num,
      "askPrice": [10.1, 0.0, 0.0, 0.0, 0.0],
      "bidPrice": [10.0, 0.0, 0.0, 0.0, 0.0],
      "askVol": [100.0, 0.0, 0.0, 0.0, 0.0],
      "bidVol": [90.0, 0.0, 0.0, 0.0, 0.0],
      "priceTick": 0.01,
      "upperLimit": 10.84,
      "lowerLimit": 8.87,
      "pe": pe,
      "vendor_extra": vendor_extra,
    }

  class Manager:
    def get_market_data(self, **_kwargs):
      return {
        "601318.SH": pd.DataFrame(
          [
            tick(transaction_num=11, pe=18.2, vendor_extra="new-a"),
            tick(transaction_num=10, pe=18.1, vendor_extra="new-b"),
          ]
        )
      }

  records = _market_data_records(Manager(), payload)
  rows = _bar_rows(records)
  assert len(rows) == 2
  assert all(set(row) == set(HISTORICAL_TICK_TRANSFER_FIELDS) for row in rows)
  assert all("pe" not in row and "vendor_extra" not in row for row in rows)
  assert all(
    field in row
    for row in rows
    for field in HISTORICAL_TICK_TRANSFER_OPTIONAL_FIELDS
  )
  assert [row[HISTORICAL_TICK_ORDINAL_FIELD] for row in rows] == [0, 1]
  assert _bar_summaries(records)[0]["row_count"] == 2

  captured = {}

  async def save_period(*, period, market_data):
    assert period == "tick"
    normalized = ingestion.preprocess_market_data(period, market_data)
    captured["normalized"] = normalized
    return {
      "saved_count": len(normalized),
      "status": "success",
    }

  result = await ingestion.persist_bar_records(
    records,
    payload=payload,
    save_period=save_period,
  )

  normalized = captured["normalized"]
  assert result["records_saved"] == 2
  assert normalized["source_time_ms"].tolist() == [source_time, source_time]
  assert normalized[HISTORICAL_TICK_ORDINAL_FIELD].tolist() == [0, 1]
  assert normalized["time"].dt.tz_convert("UTC").astype("int64").tolist() == [
    source_time * 1_000_000,
    source_time * 1_000_000 + 1_000,
  ]


def test_tick_same_millisecond_spool_is_cold_restart_stable(tmp_path) -> None:
  source_time = _normalize_market_timestamp(datetime(2025, 1, 2, 9, 30))
  rows = [
    {
      "time": source_time,
      "lastPrice": 10.0,
      "amount": 1_000.0,
      "volume": 100,
      "transactionNum": 10,
      "bidVol": [90, 0, 0, 0, 0],
      "tickvol": 1,
    },
    {
      "time": source_time,
      "lastPrice": 10.0,
      "amount": 1_001.0,
      "volume": 101,
      "transactionNum": 11,
      "bidVol": [100, 0, 0, 0, 0],
      "tickvol": 2,
    },
  ]
  payload = {
    "operation": "bars",
    "stock_list": ["601318.SH"],
    "periods": ["tick"],
    "start_time": "20250102",
    "end_time": "20250102",
    "download": False,
  }

  class Manager:
    def __init__(self, values, columns) -> None:
      self.values = values
      self.columns = columns

    def get_market_data(self, **_kwargs):
      frame = pd.DataFrame(self.values)
      return {"601318.SH": frame[self.columns]}

  columns = list(rows[0])
  first_records = _market_data_records(Manager(rows, columns), payload)
  second_records = _market_data_records(
    Manager(list(reversed(rows)), list(reversed(columns))),
    payload,
  )

  class RecordsBroker:
    def __init__(self, records) -> None:
      self.records = records

    def iter_market_data(self, _payload):
      return iter(self.records)

  first = _prepare_spool(RecordsBroker(first_records), tmp_path / "tick-first")
  second = _prepare_spool(RecordsBroker(second_records), tmp_path / "tick-second")

  assert _spool_bytes(first) == _spool_bytes(second)
  assert [chunk.digest for chunk in first.chunks] == [
    chunk.digest for chunk in second.chunks
  ]


@pytest.mark.parametrize(
  ("period", "source_time"),
  [
    ("1m", _normalize_market_timestamp(datetime(2025, 1, 2, 9, 30))),
    ("1d", 20250102),
  ],
)
def test_non_tick_records_still_reject_duplicate_normalized_time(
  period,
  source_time,
) -> None:
  class Manager:
    def get_market_data(self, **_kwargs):
      return {
        "601318.SH": pd.DataFrame(
          [
            {"time": source_time, "close": 10.0},
            {"time": source_time, "close": 10.1},
          ]
        )
      }

  with pytest.raises(ValueError, match="duplicate or unordered normalized bar key"):
    _market_data_records(
      Manager(),
      {
        "operation": "bars",
        "stock_list": ["601318.SH"],
        "periods": [period],
        "start_time": "20250102",
        "end_time": "20250102",
        "download": False,
      },
    )


@pytest.mark.parametrize(
  "reserved_field",
  [
    HISTORICAL_TICK_ORDINAL_FIELD,
    HISTORICAL_TICK_SOURCE_TIME_FIELD,
    broker_module._NORMALIZED_MARKET_TIME_COLUMN,
  ],
)
def test_market_data_records_reject_reserved_source_columns(reserved_field) -> None:
  source_time = _normalize_market_timestamp(datetime(2025, 1, 2, 9, 30))

  class Manager:
    def get_market_data(self, **_kwargs):
      return {
        "601318.SH": pd.DataFrame(
          [{"time": source_time, reserved_field: 0, "lastPrice": 10.0}]
        )
      }

  with pytest.raises(ValueError, match="contains reserved columns"):
    _market_data_records(
      Manager(),
      {
        "operation": "bars",
        "stock_list": ["601318.SH"],
        "periods": ["tick"],
        "start_time": "20250102",
        "end_time": "20250102",
        "download": False,
      },
    )


def test_tick_records_reject_exhausted_millisecond_ordinal_space() -> None:
  source_time = _normalize_market_timestamp(datetime(2025, 1, 2, 9, 30))

  class Manager:
    def get_market_data(self, **_kwargs):
      return {
        "601318.SH": pd.DataFrame(
          [
            {"time": source_time, "transactionNum": index}
            for index in range(HISTORICAL_TICK_ORDINALS_PER_MILLISECOND + 1)
          ]
        )
      }

  with pytest.raises(ValueError, match="too many ticks for one millisecond"):
    _market_data_records(
      Manager(),
      {
        "operation": "bars",
        "stock_list": ["601318.SH"],
        "periods": ["tick"],
        "start_time": "20250102",
        "end_time": "20250102",
        "download": False,
      },
    )


def test_financial_data_downloads_before_streaming_normalized_rows() -> None:
  calls = []

  class Manager:
    def download_financial_data_list(self, codes, **kwargs):
      calls.append(("download", codes, kwargs))

    def get_financial_data_list(self, codes, **kwargs):
      calls.append(("get", codes, kwargs))
      return {
        "688552.SH": {
          "Income": pd.DataFrame(
            [
              {
                "m_timetag": pd.Timestamp("2026-03-31"),
                "m_anntime": pd.Timestamp("2026-04-22"),
                "revenue": float("nan"),
              }
            ]
          )
        }
      }

  records = list(
    _iter_market_data_records(
      Manager(),
      {
        "operation": "financial_data",
        "record_format": "financial-row-v1",
        "download": True,
        "stock_list": ["688552.SH"],
        "table_list": ["Balance", "Income", "CashFlow", "Capital"],
        "start_time": "20230101",
        "end_time": "20260810",
      },
    )
  )

  assert [call[0] for call in calls] == ["download", "get"]
  assert calls[0][2] == {
    "table_list": ["Balance", "Income", "CashFlow", "Capital"],
    "start_time": "20230101",
    "end_time": "20260810",
  }
  assert calls[1][2]["table_list"] == [
    "Balance",
    "Income",
    "CashFlow",
    "Capital",
  ]
  assert calls[1][2]["start_time"] == "20230101"
  assert calls[1][2]["end_time"] == "20260810"
  assert calls[1][2]["report_type"] == "announce_time"
  assert records[0]["record_type"] == "financial_row"
  assert records[0]["row"]["m_timetag"] == "20260331"
  assert records[0]["row"]["m_anntime"] == "20260422"
  assert records[0]["row"]["revenue"] is None
  assert records[-1] == {
    "record_type": "financial_summary",
    "schema_version": 1,
    "code": "688552.SH",
    "table_counts": {
      "Balance": 0,
      "Income": 1,
      "CashFlow": 0,
      "Capital": 0,
    },
  }


def test_financial_data_emits_summary_for_code_with_no_rows() -> None:
  class Manager:
    def download_financial_data_list(self, _codes, **_kwargs):
      return None

    def get_financial_data_list(self, _codes, **_kwargs):
      return {"688552.SH": {}}

  records = list(
    _iter_market_data_records(
      Manager(),
      {
        "operation": "financial_data",
        "stock_list": ["688552.SH"],
        "start_time": "20230101",
        "end_time": "20260810",
      },
    )
  )

  assert records == [
    {
      "record_type": "financial_summary",
      "schema_version": 1,
      "code": "688552.SH",
      "table_counts": {
        "Balance": 0,
        "Income": 0,
        "CashFlow": 0,
        "Capital": 0,
      },
    }
  ]


def test_financial_data_download_failure_stops_before_read() -> None:
  class Manager:
    def download_financial_data_list(self, _codes, **_kwargs):
      raise RuntimeError("download failed")

    def get_financial_data_list(self, _codes, **_kwargs):
      raise AssertionError("read must not run after a download failure")

  with pytest.raises(RuntimeError, match="download failed"):
    list(
      _iter_market_data_records(
        Manager(),
        {
          "operation": "financial_data",
          "stock_list": ["688552.SH"],
          "start_time": "20230101",
          "end_time": "20260810",
        },
      )
    )


def test_financial_data_keeps_latest_announcement_for_duplicate_report() -> None:
  class Manager:
    def download_financial_data_list(self, _codes, **_kwargs):
      return None

    def get_financial_data_list(self, _codes, **_kwargs):
      return {
        "000001.SZ": {
          "Balance": pd.DataFrame(
            [
              {
                "m_timetag": "20221230",
                "m_anntime": "20230309",
                "tot_assets": 1,
              },
              {
                "m_timetag": "20221231",
                "m_anntime": "20230824",
                "tot_assets": 2,
              },
            ]
          )
        }
      }

  records = list(
    _iter_market_data_records(
      Manager(),
      {
        "operation": "financial_data",
        "stock_list": ["000001.SZ"],
        "start_time": "20220101",
        "end_time": "20260810",
      },
    )
  )

  assert len(records) == 2
  assert records[0]["row"]["m_timetag"] == "20221231"
  assert records[0]["row"]["m_anntime"] == "20230824"
  assert records[0]["row"]["tot_assets"] == 2
  assert records[1]["table_counts"]["Balance"] == 1


def test_financial_data_rejects_oversized_code_batch() -> None:
  with pytest.raises(ValueError, match="at most 100"):
    list(
      _iter_market_data_records(
        object(),
        {
          "operation": "financial_data",
          "stock_list": [f"{index:06d}.SZ" for index in range(101)],
          "start_time": "20230101",
          "end_time": "20260810",
        },
      )
    )


@pytest.mark.parametrize(
  ("value", "expected"),
  [
    (1_735_776_000_000, 1_735_776_000_000),
    (1_735_776_000, 1_735_776_000_000),
    ("1735776000000", 1_735_776_000_000),
    ("2025-01-02T00:00:00Z", 1_735_776_000_000),
    ("2025-01-02T08:00:00+08:00", 1_735_776_000_000),
    (20250102, 1_735_747_200_000),
    ("2025-01-02", 1_735_747_200_000),
    (datetime(2025, 1, 2), 1_735_747_200_000),
    (datetime(2025, 1, 2, 9, 30), 1_735_781_400_000),
    (pd.Timestamp("2025-01-02 09:30:00"), 1_735_781_400_000),
    (
      datetime(2025, 1, 2, tzinfo=timezone.utc),
      1_735_776_000_000,
    ),
    (pd.Timestamp("2025-01-02T00:00:00Z"), 1_735_776_000_000),
  ],
)
def test_market_timestamp_normalization(value, expected) -> None:
  assert _normalize_market_timestamp(value) == expected


@pytest.mark.parametrize(
  "value",
  [
    None,
    True,
    0,
    -1,
    float("nan"),
    1.5,
    "",
    "not-a-time",
    pd.NaT,
    datetime(1989, 12, 31, tzinfo=timezone.utc),
    datetime(2200, 1, 1, tzinfo=timezone.utc),
    object(),
  ],
)
def test_market_timestamp_normalization_fails_closed(value) -> None:
  with pytest.raises(ValueError, match="market data time"):
    _normalize_market_timestamp(value)


def test_market_data_records_fail_closed_on_unparseable_time() -> None:
  class Manager:
    def get_market_data(self, **_kwargs):
      return {"600000.SH": pd.DataFrame([{"time": "not-a-time", "close": 10.3}])}

  with pytest.raises(ValueError, match="market data time"):
    _market_data_records(
      Manager(),
      {
        "operation": "bars",
        "stock_list": ["600000.SH"],
        "periods": ["1d"],
        "start_time": "20250102",
        "end_time": "20250102",
        "download": False,
      },
    )
