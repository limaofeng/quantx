import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from quantx_contracts import (
  AgentEnvelope,
  AgentMessageType,
  MarketBatchKind,
  MarketControlType,
  MarketStreamBatch,
  MarketStreamControl,
)
from quantx_qmt_agent import runtime as runtime_module
from quantx_qmt_agent.broker import _LocalMarketStreamer
from quantx_qmt_agent.runtime import (
  MARKET_STREAM_OUTBOUND_BATCHES,
  MARKET_STREAM_OUTBOUND_BYTES,
  MARKET_STREAM_READY_ESTIMATED_TICK_BYTES,
  MARKET_STREAM_READY_INGRESS_BYTES,
  MARKET_STREAM_READY_INGRESS_CALLBACKS,
  AgentRuntime,
  _BoundedMarketBatchBuffer,
  _EncodedMarketBatch,
  _MarketOutboundOverflow,
)
from quantx_qmt_agent.whole_market_capture import (
  MIN_CAPTURED_MARKET_EVENT_ESTIMATED_BYTES,
  WholeMarketCapture,
  WholeMarketCaptureOverflow,
)


class NoAckSocket:
  def __init__(self) -> None:
    self.sent = []

  async def send(self, payload):
    self.sent.append(payload)

  async def recv(self):
    await asyncio.sleep(1)


class ControlledAckSocket:
  def __init__(self) -> None:
    self.sent: list[bytes] = []
    self.controls: asyncio.Queue[str] = asyncio.Queue()

  async def send(self, payload: bytes) -> None:
    self.sent.append(payload)

  async def recv(self) -> str:
    return await self.controls.get()


class NeverStartSocket:
  def __init__(self) -> None:
    self.sent: list[str] = []
    self.receives = 0

  async def send(self, payload: str) -> None:
    self.sent.append(payload)

  async def recv(self) -> str:
    self.receives += 1
    if self.receives == 1:
      return AgentEnvelope(
        message_type=AgentMessageType.AUTH_RESULT,
        payload={"accepted": True},
      ).model_dump_json()
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


class DelayedReadyBarrierSocket:
  def __init__(self) -> None:
    self.handshake = [
      AgentEnvelope(
        message_type=AgentMessageType.AUTH_RESULT,
        payload={"accepted": True},
      ).model_dump_json(),
      MarketStreamControl(
        type=MarketControlType.START,
        stream_id="stream-1",
        markets=("SH", "SZ"),
      ).model_dump_json(),
    ]
    self.controls: asyncio.Queue[str] = asyncio.Queue()
    self.batches: list[MarketStreamBatch] = []
    self.barrier_sent = asyncio.Event()

  async def send(self, payload) -> None:
    if isinstance(payload, str):
      return
    batch = MarketStreamBatch.from_bytes(payload)
    self.batches.append(batch)
    if batch.sequence == 1:
      await self.acknowledge(1)
    elif batch.sequence == 2:
      self.barrier_sent.set()

  async def recv(self) -> str:
    if self.handshake:
      return self.handshake.pop(0)
    return await self.controls.get()

  async def acknowledge(self, sequence: int) -> None:
    await self.controls.put(
      MarketStreamControl(
        type=MarketControlType.ACK,
        stream_id="stream-1",
        sequence=sequence,
      ).model_dump_json()
    )

  async def close(self, **_kwargs) -> None:
    return None


class SocketContext:
  def __init__(self, socket) -> None:
    self.socket = socket

  async def __aenter__(self):
    return self.socket

  async def __aexit__(self, *_args):
    return False


class PassthroughWholeMarketBroker:
  @staticmethod
  def prepare_whole_market_data(data):
    return {code: {"time": 1_000, **tick} for code, tick in data.items()}


def _stream_runtime(*, max_ready_callbacks: int) -> AgentRuntime:
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.configuration = SimpleNamespace(
    device_id="device-1",
    api_url="http://api.test",
  )
  runtime.mode = "live"
  runtime.broker = PassthroughWholeMarketBroker()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=max_ready_callbacks,
    max_ready_estimated_bytes=64 * 1024 * 1024,
    estimated_tick_bytes=2048,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_subscription_ready = asyncio.Event()
  runtime._whole_market_subscription_ready.set()
  runtime._whole_market_native_reset = asyncio.Event()
  runtime._access_token = "token-1"
  runtime._access_token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
  runtime._access_token_ready = asyncio.Event()
  runtime._control_agent_session_id = "agent-session-1"
  runtime._control_hub_registered_once = asyncio.Event()
  runtime._control_hub_registered_once.set()
  runtime._whole_market_encode_executor = ThreadPoolExecutor(max_workers=1)
  runtime._market_stream_ready_since_monotonic = 0.0
  return runtime


def _encoded(sequence: int, *, code: str = "600000.SH") -> _EncodedMarketBatch:
  batch = MarketStreamBatch(
    stream_id="stream-1",
    sequence=sequence,
    kind=MarketBatchKind.DELTA,
    captured_at=datetime.now(timezone.utc),
    instrument_count=1,
    data={code: {"lastPrice": float(sequence)}},
  )
  return _EncodedMarketBatch(batch=batch, payload=batch.to_bytes())


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
  deadline = asyncio.get_running_loop().time() + timeout
  while not predicate():
    if asyncio.get_running_loop().time() >= deadline:
      raise AssertionError("condition was not reached before timeout")
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_whole_market_ack_timeout_requires_resync(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  runtime = AgentRuntime.__new__(AgentRuntime)
  socket = NoAckSocket()
  monkeypatch.setattr(
    runtime_module,
    "MARKET_STREAM_ACK_TIMEOUT_SECONDS",
    0.01,
  )
  batch = MarketStreamBatch(
    stream_id="stream-1",
    sequence=1,
    kind=MarketBatchKind.SNAPSHOT,
    captured_at=datetime.now(timezone.utc),
    instrument_count=1,
    universe_codes=("600000.SH",),
    data={"600000.SH": {"lastPrice": 10.0}},
  )

  with pytest.raises(asyncio.TimeoutError):
    await runtime._send_market_batch_and_wait_ack(socket, batch)

  assert len(socket.sent) == 1
  assert isinstance(socket.sent[0], bytes)


@pytest.mark.asyncio
async def test_market_handshake_times_out_when_start_never_arrives(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.configuration = SimpleNamespace(device_id="device-1")
  runtime.mode = "live"
  socket = NeverStartSocket()
  monkeypatch.setattr(
    runtime_module,
    "MARKET_STREAM_HANDSHAKE_TIMEOUT_SECONDS",
    0.01,
  )

  with pytest.raises(asyncio.TimeoutError):
    await runtime._perform_market_stream_handshake(
      socket,
      access_token="token-1",
    )

  assert len(socket.sent) == 1


@pytest.mark.asyncio
async def test_empty_delta_encodes_as_ready_barrier_but_snapshot_cannot_be_empty() -> (
  None
):
  class Broker:
    @staticmethod
    def prepare_whole_market_data(data):
      return data

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._whole_market_encode_executor = ThreadPoolExecutor(max_workers=1)
  try:
    barrier = await runtime._prepare_encoded_market_batch(
      stream_id="stream-1",
      sequence=2,
      kind=MarketBatchKind.DELTA,
      captured_at=datetime.now(timezone.utc),
      raw_data={},
    )

    assert barrier.batch.sequence == 2
    assert barrier.batch.kind is MarketBatchKind.DELTA
    assert barrier.batch.instrument_count == 0
    assert barrier.batch.data == {}

    with pytest.raises(RuntimeError, match="empty whole-market batch"):
      await runtime._prepare_encoded_market_batch(
        stream_id="stream-1",
        sequence=1,
        kind=MarketBatchKind.SNAPSHOT,
        captured_at=datetime.now(timezone.utc),
        raw_data={},
      )
  finally:
    runtime._whole_market_encode_executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_wire_frame_overflow_fails_closed_with_batch_context(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = PassthroughWholeMarketBroker()
  runtime._whole_market_encode_executor = ThreadPoolExecutor(max_workers=1)

  def reject_oversized_frame(_batch) -> bytes:
    raise ValueError("market stream frame exceeds 64 MiB")

  monkeypatch.setattr(MarketStreamBatch, "to_bytes", reject_oversized_frame)
  try:
    with pytest.raises(
      RuntimeError,
      match=(
        "whole-market batch encoding failed: stream_id=stream-1 "
        "sequence=3 kind=DELTA instruments=1 .*exceeds 64 MiB"
      ),
    ):
      await runtime._prepare_encoded_market_batch(
        stream_id="stream-1",
        sequence=3,
        kind=MarketBatchKind.DELTA,
        captured_at=datetime.now(timezone.utc),
        raw_data={"600000.SH": {"lastPrice": 10.0, "time": 1_000}},
      )
  finally:
    runtime._whole_market_encode_executor.shutdown(wait=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "tick",
  [
    {"lastPrice": 10.0},
    {"lastPrice": 10.0, "time": float("nan")},
    {"lastPrice": 10.0, "time": float("inf")},
    {"lastPrice": 10.0, "time": 0, "timetag": "invalid"},
  ],
)
async def test_missing_or_invalid_source_time_fails_market_stream_batch(
  tick,
) -> None:
  class Broker:
    @staticmethod
    def prepare_whole_market_data(data):
      return data

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._whole_market_encode_executor = ThreadPoolExecutor(max_workers=1)
  try:
    with pytest.raises(
      RuntimeError,
      match=(
        "whole-market batch contains tick without a valid source time: "
        "stream_id=stream-1 sequence=4 kind=DELTA invalid=1 "
        "samples=600000.SH"
      ),
    ):
      await runtime._prepare_encoded_market_batch(
        stream_id="stream-1",
        sequence=4,
        kind=MarketBatchKind.DELTA,
        captured_at=datetime.now(timezone.utc),
        raw_data={"600000.SH": tick},
      )
  finally:
    runtime._whole_market_encode_executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_ready_barrier_carries_sync_delta_before_ready_and_does_not_repeat(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class Broker:
    @staticmethod
    def prepare_whole_market_data(data):
      return {code: {"time": 1_000, **tick} for code, tick in data.items()}

  class Socket:
    def __init__(self) -> None:
      self.handshake = [
        AgentEnvelope(
          message_type=AgentMessageType.AUTH_RESULT,
          payload={"accepted": True},
        ).model_dump_json(),
        MarketStreamControl(
          type=MarketControlType.START,
          stream_id="stream-1",
          markets=("SH", "SZ"),
        ).model_dump_json(),
      ]
      self.controls: asyncio.Queue[str] = asyncio.Queue()
      self.batches: list[MarketStreamBatch] = []
      self.barrier_sent = asyncio.Event()
      self.sequence_three_sent = asyncio.Event()

    async def send(self, payload) -> None:
      if isinstance(payload, str):
        return
      batch = MarketStreamBatch.from_bytes(payload)
      self.batches.append(batch)
      if batch.sequence == 1:
        await self.controls.put(
          MarketStreamControl(
            type=MarketControlType.ACK,
            stream_id="stream-1",
            sequence=1,
          ).model_dump_json()
        )
      elif batch.sequence == 2:
        self.barrier_sent.set()
      elif batch.sequence == 3:
        self.sequence_three_sent.set()

    async def recv(self) -> str:
      if self.handshake:
        return self.handshake.pop(0)
      return await self.controls.get()

    async def close(self, **_kwargs) -> None:
      return None

  class SocketContext:
    def __init__(self, socket) -> None:
      self.socket = socket

    async def __aenter__(self):
      return self.socket

    async def __aexit__(self, *_args):
      return False

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.configuration = SimpleNamespace(
    device_id="device-1",
    api_url="http://api.test",
  )
  runtime.mode = "live"
  runtime.broker = Broker()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_subscription_ready = asyncio.Event()
  runtime._whole_market_subscription_ready.set()
  runtime._whole_market_native_reset = asyncio.Event()
  runtime._access_token = "token-1"
  runtime._access_token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
  runtime._access_token_ready = asyncio.Event()
  runtime._control_hub_registered_once = asyncio.Event()
  runtime._control_hub_registered_once.set()
  runtime._whole_market_encode_executor = ThreadPoolExecutor(max_workers=1)
  runtime._market_stream_ready_since_monotonic = 0.0
  socket = Socket()

  async def build_snapshot(_trading_date):
    watermark = runtime._whole_market_capture.capture_sequence
    runtime._whole_market_capture.capture({"600000.SH": {"lastPrice": 11.0}})
    return (
      {"600000.SH": {"lastPrice": 10.0}},
      watermark,
      ("600000.SH",),
    )

  runtime._build_whole_market_snapshot = build_snapshot
  monkeypatch.setattr(
    runtime_module.websockets,
    "connect",
    lambda *_args, **_kwargs: SocketContext(socket),
  )
  stream = asyncio.create_task(runtime._run_whole_market_stream())
  try:
    await asyncio.wait_for(socket.barrier_sent.wait(), timeout=1)
    assert runtime._market_stream_status == "SYNCING"
    assert [batch.sequence for batch in socket.batches] == [1, 2]
    assert socket.batches[0].data["600000.SH"]["lastPrice"] == 10.0
    assert socket.batches[1].data["600000.SH"]["lastPrice"] == 11.0

    # This callback is newer than the barrier cut and must be the only seq3
    # value. The seq2 convergence update must not be replayed.
    runtime._whole_market_capture.capture({"600000.SH": {"lastPrice": 12.0}})
    await socket.controls.put(
      MarketStreamControl(
        type=MarketControlType.ACK,
        stream_id="stream-1",
        sequence=2,
      ).model_dump_json()
    )
    await asyncio.wait_for(socket.sequence_three_sent.wait(), timeout=1)
    assert runtime._market_stream_status == "SYNCING"
    assert runtime._market_stream_ready_since_monotonic == 0.0
    assert [batch.sequence for batch in socket.batches] == [1, 2, 3]
    assert socket.batches[2].data == {"600000.SH": {"time": 1_000, "lastPrice": 12.0}}
    await socket.controls.put(
      MarketStreamControl(
        type=MarketControlType.ACK,
        stream_id="stream-1",
        sequence=3,
      ).model_dump_json()
    )
    await _wait_until(lambda: runtime._market_stream_status == "READY")
    assert runtime._market_stream_ready_since_monotonic > 0
  finally:
    stream.cancel()
    await asyncio.gather(stream, return_exceptions=True)
    runtime._whole_market_encode_executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_empty_sequence_three_is_mandatory_readiness_confirmation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  runtime = _stream_runtime(max_ready_callbacks=8)
  socket = DelayedReadyBarrierSocket()

  async def build_snapshot(_trading_date):
    return {"600000.SH": {"lastPrice": 10.0}}, 0, ("600000.SH",)

  runtime._build_whole_market_snapshot = build_snapshot
  monkeypatch.setattr(
    runtime_module.websockets,
    "connect",
    lambda *_args, **_kwargs: SocketContext(socket),
  )
  stream = asyncio.create_task(runtime._run_whole_market_stream())
  try:
    await asyncio.wait_for(socket.barrier_sent.wait(), timeout=1)
    assert [batch.sequence for batch in socket.batches] == [1, 2]
    assert runtime._market_stream_status == "SYNCING"

    await socket.acknowledge(2)
    await _wait_until(lambda: any(batch.sequence == 3 for batch in socket.batches))
    confirmation = next(batch for batch in socket.batches if batch.sequence == 3)
    assert confirmation.kind is MarketBatchKind.DELTA
    assert confirmation.data == {}
    assert confirmation.instrument_count == 0
    assert runtime._market_stream_status == "SYNCING"
    assert runtime._market_stream_ready_since_monotonic == 0.0
    await socket.acknowledge(3)
    await _wait_until(lambda: runtime._market_stream_status == "READY")
    assert runtime._market_stream_ready_since_monotonic > 0
  finally:
    stream.cancel()
    await asyncio.gather(stream, return_exceptions=True)
    runtime._whole_market_encode_executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_slow_ready_ack_converges_full_market_callbacks_without_resync(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  runtime = _stream_runtime(max_ready_callbacks=1)
  socket = DelayedReadyBarrierSocket()

  async def build_snapshot(_trading_date):
    return {"600000.SH": {"lastPrice": 10.0}}, 0, ("600000.SH",)

  runtime._build_whole_market_snapshot = build_snapshot
  monkeypatch.setattr(
    runtime_module.websockets,
    "connect",
    lambda *_args, **_kwargs: SocketContext(socket),
  )
  stream = asyncio.create_task(runtime._run_whole_market_stream())
  codes = tuple(f"{index:06d}.SH" for index in range(1, 5_147))

  def full_market(price: float) -> dict[str, dict[str, float]]:
    return {code: {"lastPrice": price} for code in codes}

  try:
    await asyncio.wait_for(socket.barrier_sent.wait(), timeout=1)

    # Even a long sequence-2 ACK window stays in state convergence. The tiny
    # one-callback READY cap is intentionally irrelevant here: twenty complete
    # native callbacks collapse to the latest value for each instrument.
    for offset in range(20):
      runtime._whole_market_capture.capture(full_market(10.1 + offset / 10))
    assert runtime._whole_market_capture.queue_depth == 0
    assert runtime._whole_market_capture.invalidation_reason == ""
    assert runtime._market_stream_outbound_depth == 0
    assert runtime._market_stream_status == "SYNCING"
    assert [batch.sequence for batch in socket.batches] == [1, 2]

    await socket.acknowledge(2)
    await _wait_until(
      lambda: any(batch.sequence == 3 for batch in socket.batches),
      timeout=3,
    )
    assert runtime._market_stream_status == "SYNCING"
    sequence_three = next(batch for batch in socket.batches if batch.sequence == 3)
    assert sequence_three.instrument_count == 5_146
    assert sequence_three.data[codes[0]]["lastPrice"] == pytest.approx(12.0)

    # Keep sequence 3 unacknowledged, then fill the two-batch ACK window and
    # one outbound slot. Every 5,146-instrument callback must remain one batch
    # instead of becoming 10-11 structural fragments.
    await asyncio.sleep(0.05)
    runtime._whole_market_capture.capture(full_market(12.1))
    await _wait_until(
      lambda: any(batch.sequence == 4 for batch in socket.batches),
      timeout=3,
    )
    runtime._whole_market_capture.capture(full_market(12.2))
    await _wait_until(
      lambda: runtime._market_stream_outbound_depth == 3,
      timeout=3,
    )
    assert runtime._whole_market_capture.queue_depth == 0
    assert runtime._whole_market_capture.invalidation_reason == ""

    await socket.acknowledge(3)
    await _wait_until(
      lambda: any(batch.sequence == 5 for batch in socket.batches),
      timeout=3,
    )
    await _wait_until(lambda: runtime._market_stream_status == "READY")
    await socket.acknowledge(4)
    await socket.acknowledge(5)
    await _wait_until(
      lambda: runtime._market_stream_sequence == 5,
      timeout=3,
    )

    deltas = [batch for batch in socket.batches if batch.sequence >= 3]
    assert [batch.sequence for batch in deltas] == [3, 4, 5]
    assert [batch.instrument_count for batch in deltas] == [5_146] * 3
    assert runtime._market_stream_outbound_depth == 0
    assert runtime._market_stream_status == "READY"
  finally:
    stream.cancel()
    await asyncio.gather(stream, return_exceptions=True)
    runtime._whole_market_encode_executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_ready_barrier_overflow_propagates_exact_reason_and_stays_closed(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  runtime = _stream_runtime(max_ready_callbacks=1)
  socket = DelayedReadyBarrierSocket()

  async def build_snapshot(_trading_date):
    return {"600000.SH": {"lastPrice": 10.0}}, 0, ("600000.SH",)

  runtime._build_whole_market_snapshot = build_snapshot
  monkeypatch.setattr(
    runtime_module.websockets,
    "connect",
    lambda *_args, **_kwargs: SocketContext(socket),
  )
  stream = asyncio.create_task(runtime._run_whole_market_stream())
  try:
    await asyncio.wait_for(socket.barrier_sent.wait(), timeout=1)
    await socket.acknowledge(2)
    await _wait_until(lambda: any(batch.sequence == 3 for batch in socket.batches))
    assert runtime._market_stream_status == "SYNCING"

    # Once READY, do not yield to the producer: the second callback exceeds
    # the explicit one-callback ingress cap and must invalidate rather than
    # merge or drop.
    runtime._whole_market_capture.capture({"600000.SH": {"lastPrice": 10.1}})
    runtime._whole_market_capture.capture({"600000.SH": {"lastPrice": 10.2}})

    with pytest.raises(
      WholeMarketCaptureOverflow,
      match=("whole-market READY ingress overflow: depth=1 .*max_callbacks=1"),
    ):
      await asyncio.wait_for(stream, timeout=1)

    assert runtime._market_stream_status == "SYNCING"
    assert [batch.sequence for batch in socket.batches] == [1, 2, 3]
  finally:
    if not stream.done():
      stream.cancel()
      await asyncio.gather(stream, return_exceptions=True)
    runtime._whole_market_encode_executor.shutdown(wait=True)


def test_native_reset_gate_rejects_every_snapshot_publication_stage() -> None:
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime._whole_market_native_reset = asyncio.Event()
  runtime._whole_market_subscription_ready = asyncio.Event()
  runtime._whole_market_subscription_ready.set()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )

  runtime._require_native_whole_market_sync("snapshot-build")
  runtime._whole_market_capture.force_resync("XTData source generation changed: 1->2")
  runtime._whole_market_native_reset.set()
  with pytest.raises(
    RuntimeError,
    match=("stage=snapshot-encode reason=XTData source generation changed: 1->2"),
  ):
    runtime._require_native_whole_market_sync("snapshot-encode")

  runtime._whole_market_native_reset.clear()
  runtime._whole_market_capture.begin_syncing()
  runtime._whole_market_subscription_ready.clear()
  with pytest.raises(RuntimeError, match="stage=snapshot-send"):
    runtime._require_native_whole_market_sync("snapshot-send")


@pytest.mark.asyncio
async def test_syncing_converges_latest_state_then_ready_preserves_order() -> None:
  capture = WholeMarketCapture(
    max_ready_callbacks=4,
    max_ready_estimated_bytes=1024 * 1024,
  )
  capture.bind_loop(asyncio.get_running_loop())
  capture.capture({"600000.SH": {"lastPrice": 10.0}})
  snapshot = capture.latest_snapshot(trading_date=None)

  capture.capture({"600000.SH": {"lastPrice": 10.1}})
  capture.capture({"000001.SZ": {"lastPrice": 11.0}})
  barrier = capture.converged_event(
    after_sequence=snapshot.capture_watermark,
    trading_date=None,
  )

  assert barrier.data == {
    "600000.SH": {"lastPrice": 10.1},
    "000001.SZ": {"lastPrice": 11.0},
  }
  assert capture.stats()["ready"] is False

  # Updates while the barrier ACK is pending still converge by instrument.
  capture.capture({"600000.SH": {"lastPrice": 10.2}})
  capture.capture({"000001.SZ": {"lastPrice": 11.1}})
  capture.capture({"600000.SH": {"lastPrice": 10.3}})
  first_delta = capture.activate_ready(
    after_sequence=barrier.capture_sequence,
    trading_date=None,
  )
  assert first_delta is not None
  assert first_delta.data == {
    "600000.SH": {"lastPrice": 10.3},
    "000001.SZ": {"lastPrice": 11.1},
  }

  capture.capture({"600000.SH": {"lastPrice": 10.4}})
  capture.capture({"600000.SH": {"lastPrice": 10.5}})
  first = await capture.next_ready_event()
  second = await capture.next_ready_event()

  assert first.capture_sequence < second.capture_sequence
  assert first.data["600000.SH"]["lastPrice"] == 10.4
  assert second.data["600000.SH"]["lastPrice"] == 10.5


@pytest.mark.asyncio
async def test_capture_detaches_mutable_tick_values_from_callback_input() -> None:
  capture = WholeMarketCapture(
    max_ready_callbacks=4,
    max_ready_estimated_bytes=1024 * 1024,
  )
  capture.bind_loop(asyncio.get_running_loop())
  tick = {"lastPrice": 10.0, "askPrice": [10.1, 10.2]}
  capture.capture({"600000.SH": tick})

  tick["lastPrice"] = 99.0
  tick["askPrice"][0] = 99.0
  snapshot = capture.latest_snapshot(trading_date=None)

  assert snapshot.data["600000.SH"] == {
    "lastPrice": 10.0,
    "askPrice": [10.1, 10.2],
  }
  assert snapshot.capture_sequences["600000.SH"] == 1
  assert snapshot.captured_monotonic["600000.SH"] > 0


@pytest.mark.asyncio
async def test_native_source_reset_cannot_publish_old_complete_snapshot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class Broker:
    def __init__(self) -> None:
      self.fallbacks = 0

    @staticmethod
    def whole_market_codes() -> tuple[str, ...]:
      return ("600000.SH",)

    def whole_market_snapshot(self):
      self.fallbacks += 1
      return {"600000.SH": {"lastPrice": 11.0}}

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_capture.capture({"600000.SH": {"lastPrice": 10.0}})
  runtime._whole_market_capture.reset_source("native continuity lost")
  monkeypatch.setattr(
    runtime_module,
    "MARKET_STREAM_INITIAL_PUSH_WAIT_SECONDS",
    0.0,
  )

  async def direct_control(operation, function, *args):
    del operation
    return function(*args)

  runtime._run_xtdata_control = direct_control
  with pytest.raises(RuntimeError, match="callback coverage is insufficient"):
    await runtime._build_whole_market_snapshot(datetime.now().astimezone().date())

  assert runtime.broker.fallbacks == 0


@pytest.mark.asyncio
async def test_snapshot_is_built_only_from_whole_quote_callbacks(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  codes = (
    "000001.SH",
    "000002.SZ",
    "300001.SZ",
    "399001.SZ",
    "600000.SH",
  )

  class Broker:
    def __init__(self) -> None:
      self.fragments: list[list[str]] = []

    @staticmethod
    def whole_market_codes() -> tuple[str, ...]:
      return codes

    def whole_market_snapshot_chunk(self, batch):
      self.fragments.append(list(batch))
      return {code: {"lastPrice": 1.0} for code in batch}

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_capture.capture({code: {"lastPrice": 1.0} for code in codes})
  operations: list[str] = []
  monkeypatch.setattr(
    runtime_module,
    "MARKET_STREAM_INITIAL_PUSH_WAIT_SECONDS",
    0.0,
  )

  async def direct_control(operation, function, *args):
    operations.append(operation)
    return function(*args)

  runtime._run_xtdata_control = direct_control
  snapshot, _, universe = await runtime._build_whole_market_snapshot(
    datetime.now().astimezone().date()
  )

  assert runtime.broker.fragments == []
  assert operations == ["whole-market-codes"]
  assert set(snapshot) == set(codes)
  assert universe == tuple(sorted(codes))


@pytest.mark.asyncio
async def test_snapshot_returns_callback_capture_watermark(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  codes = ("000001.SH", "000002.SZ")

  class Broker:
    @staticmethod
    def whole_market_codes() -> tuple[str, ...]:
      return codes

    @staticmethod
    def whole_market_snapshot_chunk(batch):
      prices = {"000001.SH": 10.0, "000002.SZ": 20.0}
      return {code: {"lastPrice": prices[code]} for code in batch}

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_capture.capture(
    {
      "000001.SH": {"lastPrice": 11.0},
      "000002.SZ": {"lastPrice": 21.0},
    }
  )
  monkeypatch.setattr(
    runtime_module,
    "MARKET_STREAM_INITIAL_PUSH_WAIT_SECONDS",
    0.0,
  )

  async def direct_control(operation, function, *args):
    del operation
    return function(*args)

  runtime._run_xtdata_control = direct_control
  snapshot, watermark, universe = await runtime._build_whole_market_snapshot(
    datetime.now().astimezone().date()
  )

  assert watermark == 1
  assert snapshot["000001.SH"]["lastPrice"] == 11.0
  assert snapshot["000002.SZ"]["lastPrice"] == 21.0
  assert universe == tuple(sorted(codes))


@pytest.mark.asyncio
async def test_snapshot_allows_missing_qmt_ticks_and_logs_bounded_samples(
  monkeypatch: pytest.MonkeyPatch,
  caplog: pytest.LogCaptureFixture,
) -> None:
  codes = tuple(f"{index:06d}.SH" for index in range(1, 101))

  class Broker:
    def __init__(self) -> None:
      self.calls = 0

    @staticmethod
    def whole_market_codes() -> tuple[str, ...]:
      return codes

    def whole_market_snapshot_chunk(self, batch):
      self.calls += 1
      if self.calls == 1:
        return {batch[0]: {"lastPrice": 1.0}}
      return {}

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_capture.capture(
    {code: {"lastPrice": 1.0} for code in codes[:-1]}
  )
  monkeypatch.setattr(
    runtime_module,
    "MARKET_STREAM_INITIAL_PUSH_WAIT_SECONDS",
    0.0,
  )

  async def direct_control(operation, function, *args):
    del operation
    return function(*args)

  runtime._run_xtdata_control = direct_control
  with caplog.at_level("WARNING"):
    snapshot, _, universe = await runtime._build_whole_market_snapshot(
      datetime.now().astimezone().date()
    )

  assert len(snapshot) == 99
  assert universe == codes
  missing_record = next(
    record
    for record in caplog.records
    if "omits instruments without an available tick" in record.getMessage()
  )
  assert missing_record.args[0] == 1
  assert missing_record.args[1] == 100
  assert missing_record.args[2] == ["000100.SH"]


@pytest.mark.asyncio
async def test_snapshot_never_calls_point_query_fallback(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  codes = ("000001.SH", "000002.SZ", "300001.SZ")

  class Broker:
    def __init__(self) -> None:
      self.calls = 0

    @staticmethod
    def whole_market_codes() -> tuple[str, ...]:
      return codes

    def whole_market_snapshot_chunk(self, batch):
      self.calls += 1
      if self.calls == 2:
        raise RuntimeError("second native fragment failed")
      return {code: {"lastPrice": 1.0} for code in batch}

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_capture.capture({code: {"lastPrice": 1.0} for code in codes})
  monkeypatch.setattr(
    runtime_module,
    "MARKET_STREAM_INITIAL_PUSH_WAIT_SECONDS",
    0.0,
  )

  async def direct_control(operation, function, *args):
    del operation
    return function(*args)

  runtime._run_xtdata_control = direct_control

  snapshot, _, _ = await runtime._build_whole_market_snapshot(
    datetime.now().astimezone().date()
  )

  assert set(snapshot) == set(codes)
  assert runtime.broker.calls == 0


@pytest.mark.asyncio
async def test_market_stream_waits_for_first_control_hub_registration() -> None:
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime._ensure_whole_market_state()

  waiter = asyncio.create_task(runtime._wait_for_initial_control_hub_registration())
  await asyncio.sleep(0)
  assert not waiter.done()

  runtime._control_hub_registered_once.set()
  await asyncio.wait_for(waiter, timeout=0.1)

  # The gate is intentionally sticky: later control reconnects do not cancel
  # or re-gate the independently owned market stream.
  await asyncio.wait_for(
    runtime._wait_for_initial_control_hub_registration(),
    timeout=0.1,
  )


@pytest.mark.asyncio
async def test_ready_overflow_enters_state_only_and_keeps_latest() -> None:
  capture = WholeMarketCapture(
    max_ready_callbacks=1,
    max_ready_estimated_bytes=1024 * 1024,
  )
  capture.bind_loop(asyncio.get_running_loop())
  capture.capture({"600000.SH": {"lastPrice": 10.0}})
  watermark = capture.latest_snapshot(trading_date=None).capture_watermark
  ready_confirmation = capture.activate_ready(
    after_sequence=watermark,
    trading_date=None,
  )
  assert ready_confirmation.data == {}
  assert ready_confirmation.capture_sequence == watermark

  capture.capture({"600000.SH": {"lastPrice": 10.1}})
  capture.capture({"600000.SH": {"lastPrice": 10.2}})

  with pytest.raises(
    WholeMarketCaptureOverflow,
    match=("whole-market READY ingress overflow: depth=1 .*max_callbacks=1"),
  ):
    await capture.next_ready_event()
  latest = capture.latest_snapshot(trading_date=None)
  assert latest.data["600000.SH"]["lastPrice"] == 10.2
  assert capture.queue_depth == 0
  assert capture.stats()["overflow_reason"] == capture.invalidation_reason


def test_production_ready_ingress_is_governed_by_64_mib_byte_budget() -> None:
  assert MARKET_STREAM_READY_INGRESS_CALLBACKS == (
    MARKET_STREAM_READY_INGRESS_BYTES // MIN_CAPTURED_MARKET_EVENT_ESTIMATED_BYTES
  )
  assert MARKET_STREAM_READY_INGRESS_CALLBACKS > 8
  capture = WholeMarketCapture(
    max_ready_callbacks=MARKET_STREAM_READY_INGRESS_CALLBACKS,
    max_ready_estimated_bytes=MARKET_STREAM_READY_INGRESS_BYTES,
    estimated_tick_bytes=MARKET_STREAM_READY_ESTIMATED_TICK_BYTES,
  )
  capture.activate_ready(after_sequence=0, trading_date=None)
  callback = {f"{index:06d}.SH": {"lastPrice": 10.0} for index in range(5_000)}
  estimated_callback_bytes = len(callback) * MARKET_STREAM_READY_ESTIMATED_TICK_BYTES
  retained_callbacks = MARKET_STREAM_READY_INGRESS_BYTES // estimated_callback_bytes

  for _ in range(retained_callbacks):
    capture.capture(callback)

  assert retained_callbacks > 1
  assert capture.queue_depth == retained_callbacks
  assert capture.queue_estimated_bytes == (
    retained_callbacks * estimated_callback_bytes
  )
  assert capture.invalidation_reason == ""

  capture.capture(callback)

  projected_bytes = (retained_callbacks + 1) * estimated_callback_bytes
  with pytest.raises(
    WholeMarketCaptureOverflow,
    match=(
      "whole-market READY ingress overflow: "
      f"depth={retained_callbacks} "
      f"projected_estimated_bytes={projected_bytes} "
      f"max_callbacks={MARKET_STREAM_READY_INGRESS_CALLBACKS} "
      f"max_estimated_bytes={MARKET_STREAM_READY_INGRESS_BYTES}"
    ),
  ):
    capture.raise_if_invalidated()
  assert capture.queue_depth == 0


@pytest.mark.asyncio
async def test_burst_over_eight_callbacks_drains_through_bounded_ack_pipeline() -> None:
  runtime = _stream_runtime(max_ready_callbacks=MARKET_STREAM_READY_INGRESS_CALLBACKS)
  runtime._market_stream_status = "READY"
  runtime._market_stream_sequence = 3
  runtime._market_stream_ack_latency_ms = 0.0
  runtime._market_stream_outbound_depth = 0
  runtime._market_stream_outbound_bytes = 0
  runtime._whole_market_capture.activate_ready(
    after_sequence=0,
    trading_date=None,
  )
  codes = tuple(f"{index:06d}.SH" for index in range(256))
  for offset in range(32):
    runtime._whole_market_capture.capture(
      {code: {"lastPrice": 10.0 + offset / 100} for code in codes}
    )

  assert runtime._whole_market_capture.queue_depth == 32
  assert runtime._whole_market_capture.queue_estimated_bytes < (
    MARKET_STREAM_READY_INGRESS_BYTES
  )
  assert runtime._whole_market_capture.invalidation_reason == ""

  outbound = _BoundedMarketBatchBuffer(
    max_batches=MARKET_STREAM_OUTBOUND_BATCHES,
    max_bytes=MARKET_STREAM_OUTBOUND_BYTES,
  )
  socket = ControlledAckSocket()
  producer = asyncio.create_task(
    runtime._whole_market_batch_producer(
      outbound,
      stream_id="stream-1",
      starting_sequence=3,
      trading_date=datetime.now(runtime_module.SHANGHAI_ZONE).date(),
      first_event=None,
      dedupe_fingerprints={},
    )
  )
  transport = asyncio.create_task(
    runtime._transmit_market_batches(
      socket,
      outbound,
      stream_id="stream-1",
    )
  )
  try:
    for sequence in range(4, 36):
      await _wait_until(
        lambda sequence=sequence: any(
          MarketStreamBatch.from_bytes(payload).sequence == sequence
          for payload in socket.sent
        ),
        timeout=3,
      )
      # Exercise real ACK backpressure instead of allowing an unbounded send.
      await asyncio.sleep(0.001)
      await socket.controls.put(
        MarketStreamControl(
          type=MarketControlType.ACK,
          stream_id="stream-1",
          sequence=sequence,
        ).model_dump_json()
      )
    await _wait_until(
      lambda: runtime._market_stream_sequence == 35,
      timeout=3,
    )
    assert [
      MarketStreamBatch.from_bytes(payload).sequence for payload in socket.sent
    ] == list(range(4, 36))
    assert runtime._whole_market_capture.queue_depth == 0
    assert runtime._whole_market_capture.invalidation_reason == ""
    assert outbound.depth == 0
  finally:
    producer.cancel()
    transport.cancel()
    await asyncio.gather(producer, transport, return_exceptions=True)
    runtime._whole_market_encode_executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_outbound_budget_uses_actual_encoded_bytes() -> None:
  buffer = _BoundedMarketBatchBuffer(max_batches=8, max_bytes=100)
  first = _encoded(2)
  second = _encoded(3, code="000001.SZ")
  first = _EncodedMarketBatch(batch=first.batch, payload=b"x" * 60)
  second = _EncodedMarketBatch(batch=second.batch, payload=b"y" * 101)

  await buffer.put(first)
  with pytest.raises(_MarketOutboundOverflow):
    await buffer.put(second)

  assert buffer.depth == 1
  assert buffer.bytes == 60
  assert await buffer.get() is first
  await buffer.acknowledge(first)
  await asyncio.wait_for(buffer.join(), timeout=1)
  assert buffer.depth == 0
  assert buffer.bytes == 0


@pytest.mark.asyncio
async def test_transport_allows_two_unacknowledged_batches() -> None:
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime._market_stream_sequence = 1
  runtime._market_stream_ack_latency_ms = 0.0
  runtime._market_stream_outbound_depth = 0
  runtime._market_stream_outbound_bytes = 0
  runtime._market_stream_status = "SYNCING"
  runtime._market_stream_ready_since_monotonic = 0.0
  outbound = _BoundedMarketBatchBuffer(max_batches=8, max_bytes=1024 * 1024)
  for sequence in (2, 3, 4):
    await outbound.put(_encoded(sequence))
  socket = ControlledAckSocket()
  transport = asyncio.create_task(
    runtime._transmit_market_batches(
      socket,
      outbound,
      stream_id="stream-1",
    )
  )
  try:
    await _wait_until(lambda: len(socket.sent) == 2)
    assert outbound.depth == 3

    await socket.controls.put(
      MarketStreamControl(
        type=MarketControlType.ACK,
        stream_id="stream-1",
        sequence=2,
      ).model_dump_json()
    )
    await _wait_until(lambda: len(socket.sent) == 3)
    assert runtime._market_stream_status == "SYNCING"
    await socket.controls.put(
      MarketStreamControl(
        type=MarketControlType.ACK,
        stream_id="stream-1",
        sequence=3,
      ).model_dump_json()
    )
    await _wait_until(lambda: runtime._market_stream_sequence == 3)
    assert runtime._market_stream_status == "READY"
    assert runtime._market_stream_ready_since_monotonic > 0
    await socket.controls.put(
      MarketStreamControl(
        type=MarketControlType.ACK,
        stream_id="stream-1",
        sequence=4,
      ).model_dump_json()
    )
    await _wait_until(lambda: runtime._market_stream_sequence == 4)
    assert outbound.depth == 0
  finally:
    transport.cancel()
    await asyncio.gather(transport, return_exceptions=True)


@pytest.mark.asyncio
async def test_outbound_backpressure_drains_more_than_eight_microbatches() -> None:
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime._market_stream_sequence = 1
  runtime._market_stream_ack_latency_ms = 0.0
  runtime._market_stream_outbound_depth = 0
  runtime._market_stream_outbound_bytes = 0
  outbound = _BoundedMarketBatchBuffer(max_batches=8, max_bytes=1024 * 1024)
  socket = ControlledAckSocket()

  async def produce() -> None:
    for sequence in range(2, 14):
      await outbound.put(_encoded(sequence))

  producer = asyncio.create_task(produce())
  transport = asyncio.create_task(
    runtime._transmit_market_batches(
      socket,
      outbound,
      stream_id="stream-1",
    )
  )
  try:
    for sequence in range(2, 14):
      await _wait_until(
        lambda sequence=sequence: any(
          MarketStreamBatch.from_bytes(payload).sequence == sequence
          for payload in socket.sent
        )
      )
      await socket.controls.put(
        MarketStreamControl(
          type=MarketControlType.ACK,
          stream_id="stream-1",
          sequence=sequence,
        ).model_dump_json()
      )
    await producer
    await _wait_until(lambda: runtime._market_stream_sequence == 13)
    assert len(socket.sent) == 12
    assert outbound.depth == 0
  finally:
    for task in (producer, transport):
      task.cancel()
    await asyncio.gather(producer, transport, return_exceptions=True)


@pytest.mark.asyncio
async def test_microbatch_seals_before_duplicate_instrument() -> None:
  class Broker:
    @staticmethod
    def prepare_whole_market_data(data):
      return {code: {"time": 1_000, **tick} for code, tick in data.items()}

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_encode_executor = ThreadPoolExecutor(max_workers=1)
  runtime._market_stream_outbound_depth = 0
  runtime._market_stream_outbound_bytes = 0
  watermark = runtime._whole_market_capture.latest_snapshot(
    trading_date=None
  ).capture_watermark
  runtime._whole_market_capture.activate_ready(
    after_sequence=watermark,
    trading_date=None,
  )
  runtime._whole_market_capture.capture({"600000.SH": {"lastPrice": 10.1}})
  runtime._whole_market_capture.capture({"600000.SH": {"lastPrice": 10.2}})
  outbound = _BoundedMarketBatchBuffer(max_batches=8, max_bytes=1024 * 1024)
  producer = asyncio.create_task(
    runtime._whole_market_batch_producer(
      outbound,
      stream_id="stream-1",
      starting_sequence=1,
      trading_date=datetime.now().astimezone().date(),
      first_event=None,
      dedupe_fingerprints={},
    )
  )
  try:
    await _wait_until(lambda: outbound.depth == 2)
    first = await outbound.get()
    second = await outbound.get()
    assert first.batch.sequence == 2
    assert second.batch.sequence == 3
    assert first.batch.data["600000.SH"]["lastPrice"] == 10.1
    assert second.batch.data["600000.SH"]["lastPrice"] == 10.2
  finally:
    producer.cancel()
    await asyncio.gather(producer, return_exceptions=True)
    runtime._whole_market_encode_executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_native_subscription_lives_until_process_shutdown() -> None:
  class Broker:
    def __init__(self) -> None:
      self.subscriptions = 0
      self.unsubscriptions = 0

    @staticmethod
    def ensure_market_data_ready() -> bool:
      return True

    def subscribe_whole_market(self, callback) -> bool:
      del callback
      self.subscriptions += 1
      return True

    def unsubscribe_whole_market(self) -> None:
      self.unsubscriptions += 1

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._stopped = asyncio.Event()
  runtime._fatal_market_data_error = None
  runtime._fatal_market_data_event = asyncio.Event()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )
  runtime._whole_market_subscription_ready = asyncio.Event()
  runtime._whole_market_subscription_active = False

  async def direct_control(operation, function, *args):
    del operation
    return function(*args)

  runtime._run_xtdata_control = direct_control
  supervisor = asyncio.create_task(runtime._whole_market_capture_supervisor())
  await asyncio.wait_for(
    runtime._whole_market_subscription_ready.wait(),
    timeout=1,
  )
  await asyncio.sleep(0)
  assert runtime.broker.subscriptions == 1
  assert runtime.broker.unsubscriptions == 0

  runtime._stopped.set()
  await supervisor
  await runtime._shutdown_whole_market_capture()
  assert runtime.broker.subscriptions == 1
  assert runtime.broker.unsubscriptions == 1


@pytest.mark.asyncio
async def test_rejected_native_subscription_clears_failed_source_capture() -> None:
  class Broker:
    def __init__(self) -> None:
      self.subscriptions = 0

    @staticmethod
    def ensure_market_data_ready() -> bool:
      return True

    def subscribe_whole_market(self, callback) -> bool:
      self.subscriptions += 1
      if self.subscriptions == 1:
        callback({"600000.SH": {"lastPrice": 10.0}})
        return False
      return True

    @staticmethod
    def unsubscribe_whole_market() -> None:
      return None

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._stopped = asyncio.Event()
  runtime._fatal_market_data_error = None
  runtime._fatal_market_data_event = asyncio.Event()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_subscription_ready = asyncio.Event()
  runtime._whole_market_subscription_active = False

  async def direct_control(operation, function, *args):
    del operation
    return function(*args)

  runtime._run_xtdata_control = direct_control
  supervisor = asyncio.create_task(runtime._whole_market_capture_supervisor())
  try:
    await asyncio.wait_for(
      runtime._whole_market_subscription_ready.wait(),
      timeout=2,
    )
    assert runtime.broker.subscriptions == 2
    assert runtime._whole_market_capture.latest_snapshot(trading_date=None).data == {}
  finally:
    runtime._stopped.set()
    await supervisor
    await runtime._shutdown_whole_market_capture()


@pytest.mark.asyncio
async def test_universe_generation_change_rebinds_native_subscription(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  sectors = {
    "沪深A股": ["600000.SH"],
    "沪深指数": ["000001.SH"],
  }
  operations = []
  callbacks = []
  active_native_subscriptions = 0

  class FakeDataManager:
    @staticmethod
    def get_stock_list_in_sector(sector):
      return list(sectors[sector])

    @staticmethod
    def get_instrument_detail_list(codes, iscomplete=False):
      assert iscomplete is True
      return {code: {} for code in codes}

    @staticmethod
    def subscribe_whole_quote(codes, callback):
      nonlocal active_native_subscriptions
      assert active_native_subscriptions == 0
      active_native_subscriptions += 1
      subscription_id = len(callbacks) + 1
      callbacks.append(callback)
      operations.append(("subscribe", subscription_id, list(codes)))
      return subscription_id

    @staticmethod
    def unsubscribe_quote(subscription_id):
      nonlocal active_native_subscriptions
      assert active_native_subscriptions == 1
      active_native_subscriptions -= 1
      operations.append(("unsubscribe", subscription_id))

  class Broker:
    def __init__(self) -> None:
      self.streamer = _LocalMarketStreamer(FakeDataManager())
      self.subscriptions = 0

    @staticmethod
    def ensure_market_data_ready() -> bool:
      return True

    @staticmethod
    def is_market_data_ready() -> bool:
      return True

    def subscribe_whole_market(self, callback) -> bool:
      accepted = self.streamer.subscribe_whole_market(callback)
      if accepted:
        self.subscriptions += 1
      if accepted and self.subscriptions == 1:
        sectors["沪深A股"] = ["600000.SH", "600001.SH"]
        with self.streamer._lock:
          active = self.streamer._whole_quote_active_universe
          assert active is not None
          self.streamer._whole_quote_active_universe = (
            self.streamer._build_whole_quote_universe(
              trading_date=active.trading_date - timedelta(days=1),
              codes=active.codes,
              metadata=active.metadata,
            )
          )
          self.streamer._whole_quote_metadata_last_attempt_monotonic = 0.0
      return accepted

    def unsubscribe_whole_market(self) -> None:
      self.streamer.unsubscribe_whole_market()

    def market_data_subscription_generation(self) -> int:
      return self.streamer.whole_market_bound_universe_generation()

    def market_data_connection_generation(self) -> int:
      return self.streamer.whole_market_universe_generation()

    def is_whole_market_trading_session(self) -> bool:
      self.streamer._ensure_whole_quote_metadata_current(["SH", "SZ"])
      return False

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._stopped = asyncio.Event()
  runtime._fatal_market_data_error = None
  runtime._fatal_market_data_event = asyncio.Event()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_subscription_ready = asyncio.Event()
  runtime._whole_market_subscription_active = False
  runtime._whole_market_native_reset = asyncio.Event()
  runtime._market_stream_status = "OFFLINE"
  monkeypatch.setattr(
    runtime_module,
    "MARKET_STREAM_NATIVE_HEALTH_CHECK_SECONDS",
    0.01,
  )

  async def direct_control(operation, function, *args):
    del operation
    return function(*args)

  runtime._run_xtdata_control = direct_control
  supervisor = asyncio.create_task(runtime._whole_market_capture_supervisor())
  try:
    await _wait_until(lambda: runtime.broker.subscriptions == 2)
    assert operations[:3] == [
      ("subscribe", 1, ["000001.SH", "600000.SH"]),
      ("unsubscribe", 1),
      ("subscribe", 2, ["000001.SH", "600000.SH", "600001.SH"]),
    ]
    assert runtime._whole_market_native_reset.is_set()
  finally:
    runtime._stopped.set()
    await supervisor
    await runtime._shutdown_whole_market_capture()


@pytest.mark.asyncio
async def test_native_subscription_recovers_after_connection_generation_change(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class Broker:
    def __init__(self) -> None:
      self.generation = 1
      self.subscriptions = 0
      self.unsubscriptions = 0

    @staticmethod
    def ensure_market_data_ready() -> bool:
      return True

    @staticmethod
    def is_market_data_ready() -> bool:
      return True

    @staticmethod
    def is_whole_market_trading_session() -> bool:
      return False

    def market_data_connection_generation(self) -> int:
      return self.generation

    def subscribe_whole_market(self, callback) -> bool:
      del callback
      self.subscriptions += 1
      return True

    def unsubscribe_whole_market(self) -> None:
      self.unsubscriptions += 1

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._stopped = asyncio.Event()
  runtime._fatal_market_data_error = None
  runtime._fatal_market_data_event = asyncio.Event()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_subscription_ready = asyncio.Event()
  runtime._whole_market_subscription_active = False
  runtime._whole_market_native_reset = asyncio.Event()
  runtime._market_stream_status = "OFFLINE"
  monkeypatch.setattr(
    runtime_module,
    "MARKET_STREAM_NATIVE_HEALTH_CHECK_SECONDS",
    0.01,
  )

  async def direct_control(operation, function, *args):
    del operation
    return function(*args)

  runtime._run_xtdata_control = direct_control
  supervisor = asyncio.create_task(runtime._whole_market_capture_supervisor())
  try:
    await _wait_until(lambda: runtime.broker.subscriptions == 1)
    runtime.broker.generation = 2
    await _wait_until(lambda: runtime.broker.subscriptions == 2)
    assert runtime.broker.unsubscriptions == 1
    assert runtime._whole_market_native_reset.is_set()
  finally:
    runtime._stopped.set()
    await supervisor
    await runtime._shutdown_whole_market_capture()


@pytest.mark.asyncio
async def test_native_unsubscribe_failure_is_fail_stop_not_duplicate(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class Broker:
    def __init__(self) -> None:
      self.generation = 1
      self.subscriptions = 0

    @staticmethod
    def ensure_market_data_ready() -> bool:
      return True

    @staticmethod
    def is_market_data_ready() -> bool:
      return True

    @staticmethod
    def is_whole_market_trading_session() -> bool:
      return False

    def market_data_connection_generation(self) -> int:
      return self.generation

    def subscribe_whole_market(self, callback) -> bool:
      del callback
      self.subscriptions += 1
      return True

    @staticmethod
    def unsubscribe_whole_market() -> None:
      raise RuntimeError("native cancellation failed")

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._stopped = asyncio.Event()
  runtime._fatal_market_data_error = None
  runtime._fatal_market_data_event = asyncio.Event()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_subscription_ready = asyncio.Event()
  runtime._whole_market_subscription_active = False
  runtime._whole_market_native_reset = asyncio.Event()
  runtime._market_stream_status = "OFFLINE"
  monkeypatch.setattr(
    runtime_module,
    "MARKET_STREAM_NATIVE_HEALTH_CHECK_SECONDS",
    0.01,
  )

  async def direct_control(operation, function, *args):
    del operation
    return function(*args)

  runtime._run_xtdata_control = direct_control
  supervisor = asyncio.create_task(runtime._whole_market_capture_supervisor())
  await _wait_until(lambda: runtime.broker.subscriptions == 1)
  runtime.broker.generation = 2

  with pytest.raises(runtime_module._FatalMarketDataPreparationError):
    await supervisor

  assert runtime._stopped.is_set()
  assert runtime._fatal_market_data_event.is_set()
  assert runtime.broker.subscriptions == 1


@pytest.mark.asyncio
async def test_confirmed_trading_session_silence_rebuilds_subscription(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class Broker:
    def __init__(self) -> None:
      self.subscriptions = 0
      self.unsubscriptions = 0

    @staticmethod
    def ensure_market_data_ready() -> bool:
      return True

    @staticmethod
    def is_market_data_ready() -> bool:
      return True

    @staticmethod
    def is_whole_market_trading_session() -> bool:
      return True

    @staticmethod
    def market_data_connection_generation() -> int:
      return 1

    def subscribe_whole_market(self, callback) -> bool:
      del callback
      self.subscriptions += 1
      return True

    def unsubscribe_whole_market(self) -> None:
      self.unsubscriptions += 1

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime.broker = Broker()
  runtime._stopped = asyncio.Event()
  runtime._fatal_market_data_error = None
  runtime._fatal_market_data_event = asyncio.Event()
  runtime._whole_market_capture = WholeMarketCapture(
    max_ready_callbacks=8,
    max_ready_estimated_bytes=1024 * 1024,
  )
  runtime._whole_market_capture.bind_loop(asyncio.get_running_loop())
  runtime._whole_market_subscription_ready = asyncio.Event()
  runtime._whole_market_subscription_active = False
  runtime._whole_market_native_reset = asyncio.Event()
  runtime._market_stream_status = "OFFLINE"
  monkeypatch.setattr(
    runtime_module,
    "MARKET_STREAM_NATIVE_HEALTH_CHECK_SECONDS",
    0.01,
  )
  monkeypatch.setattr(
    runtime_module,
    "MARKET_STREAM_NATIVE_SILENCE_SECONDS",
    0.0,
  )

  async def direct_control(operation, function, *args):
    del operation
    return function(*args)

  runtime._run_xtdata_control = direct_control
  supervisor = asyncio.create_task(runtime._whole_market_capture_supervisor())
  try:
    await _wait_until(lambda: runtime.broker.subscriptions >= 2)
    assert runtime.broker.unsubscriptions >= 1
  finally:
    runtime._stopped.set()
    await supervisor
    await runtime._shutdown_whole_market_capture()


def test_authenticated_control_session_resets_reconnect_backoff() -> None:
  assert AgentRuntime._control_reconnect_delay(
    16.0,
    authenticated=False,
  ) == (16.0, 32.0)
  assert AgentRuntime._control_reconnect_delay(
    16.0,
    authenticated=True,
  ) == (1.0, 2.0)


def test_websocket_close_code_prefers_received_close_frame() -> None:
  received = type("CloseFrame", (), {"code": 4401})()
  sent = type("CloseFrame", (), {"code": 1011})()
  exc = type(
    "ClosedConnection",
    (Exception,),
    {"rcvd": received, "sent": sent},
  )()

  assert runtime_module._websocket_close_code(exc) == 4401


def test_websocket_close_code_supports_legacy_exception_code() -> None:
  exc = type("ClosedConnection", (Exception,), {"code": 1006})()

  assert runtime_module._websocket_close_code(exc) == 1006
  assert runtime_module._websocket_close_code(RuntimeError()) is None


def test_ready_market_stream_resets_backoff_immediately() -> None:
  assert AgentRuntime._market_stream_retry_delay(
    16.0,
    ready_seconds=0.0,
  ) == (16.0, 30.0)
  assert AgentRuntime._market_stream_retry_delay(
    16.0,
    ready_seconds=0.001,
  ) == (1.0, 1.0)


@pytest.mark.asyncio
async def test_access_token_replacement_does_not_resync_ready_market_stream() -> None:
  class Capture:
    def begin_syncing(self) -> None:
      return None

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime._access_token = "old-token"
  runtime._access_token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
  runtime._access_token_ready = asyncio.Event()
  runtime._access_token_ready.set()
  runtime._whole_market_capture = Capture()
  runtime._market_stream_resyncs = 0
  runtime._market_stream_status = "READY"
  runtime._market_stream_ready_since_monotonic = 100.0
  stream_started = asyncio.Event()

  async def stream() -> None:
    stream_started.set()
    await asyncio.Event().wait()

  runtime._run_whole_market_stream = stream
  supervisor = asyncio.create_task(runtime._whole_market_stream_supervisor())
  try:
    await asyncio.wait_for(stream_started.wait(), timeout=1)
    runtime._access_token = "new-token"
    runtime._access_token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    runtime._access_token_ready.set()
    await asyncio.sleep(0)
    assert runtime._market_stream_resyncs == 0
    assert not supervisor.done()
  finally:
    supervisor.cancel()
    await asyncio.gather(supervisor, return_exceptions=True)

  assert runtime._market_stream_status == "OFFLINE"
  assert runtime._market_stream_ready_since_monotonic == 0.0


@pytest.mark.asyncio
async def test_control_reconnect_does_not_restart_process_market_stream() -> None:
  class Capture:
    def bind_loop(self, loop) -> None:
      del loop

    def unbind_loop(self) -> None:
      return None

  class Executor:
    def shutdown(self, **kwargs) -> None:
      del kwargs

  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime._stopped = asyncio.Event()
  runtime._broker_ready = asyncio.Event()
  runtime._broker_ready.set()
  runtime._fatal_market_data_error = None
  runtime._whole_market_capture = Capture()
  runtime._whole_market_encode_executor = Executor()
  control_sessions = 0
  market_stream_starts = 0

  runtime._ensure_market_upload_state = lambda: None
  runtime._ensure_whole_market_state = lambda: None
  runtime._clear_market_upload_state = lambda: None

  async def wait_for_stop() -> None:
    await runtime._stopped.wait()

  async def market_stream() -> None:
    nonlocal market_stream_starts
    market_stream_starts += 1
    await runtime._stopped.wait()

  async def control_session() -> bool:
    nonlocal control_sessions
    control_sessions += 1
    await asyncio.sleep(0)
    if control_sessions == 1:
      return True
    runtime._stopped.set()
    return False

  async def no_op() -> None:
    return None

  runtime._market_upload_cache_sweeper = wait_for_stop
  runtime._whole_market_capture_supervisor = wait_for_stop
  runtime._whole_market_stream_supervisor = market_stream
  runtime._run_session_until_fatal = control_session
  runtime._shutdown_whole_market_capture = no_op
  runtime._cancel_market_upload_tasks = no_op

  await runtime.run_forever()

  assert control_sessions == 2
  assert market_stream_starts == 1


@pytest.mark.asyncio
async def test_market_connection_registry_rejects_second_connection() -> None:
  from quantx_api.agent_api import _MarketConnectionRegistry

  registry = _MarketConnectionRegistry()
  first = await registry.register()

  assert first
  assert await registry.register() is None

  await registry.unregister(first)
  assert await registry.register()
