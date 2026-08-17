import asyncio
from datetime import datetime, timezone

import pytest
from quantx_contracts import MarketBatchKind, MarketStreamBatch
from quantx_qmt_agent import runtime as runtime_module
from quantx_qmt_agent.runtime import AgentRuntime


class NoAckSocket:
  def __init__(self) -> None:
    self.sent = []

  async def send(self, payload):
    self.sent.append(payload)

  async def recv(self):
    await asyncio.sleep(1)


@pytest.mark.asyncio
async def test_whole_market_ack_timeout_requires_resync(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime._whole_market_events = asyncio.Queue(maxsize=8)
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
    data={"600000.SH": {"lastPrice": 10.0}},
  )

  with pytest.raises(asyncio.TimeoutError):
    await runtime._send_market_batch_and_wait_ack(socket, batch)

  assert len(socket.sent) == 1
  assert isinstance(socket.sent[0], bytes)


@pytest.mark.asyncio
async def test_whole_market_capture_overflow_is_not_silent_or_lossy() -> None:
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime._session_loop = asyncio.get_running_loop()
  runtime._whole_market_events = asyncio.Queue(maxsize=1)
  runtime._whole_market_overflow = asyncio.Event()

  first = {"600000.SH": {"lastPrice": 10.0}}
  second = {"000001.SZ": {"lastPrice": 11.0}}
  runtime._enqueue_whole_market_event(first)
  await asyncio.sleep(0)
  runtime._enqueue_whole_market_event(second)
  await asyncio.sleep(0)

  assert runtime._whole_market_overflow.is_set()
  assert runtime._whole_market_events.qsize() == 1
  _, retained = runtime._whole_market_events.get_nowait()
  assert retained is first


@pytest.mark.asyncio
async def test_whole_market_capture_enforces_64_mib_budget() -> None:
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime._session_loop = asyncio.get_running_loop()
  runtime._whole_market_events = asyncio.Queue(maxsize=8)
  runtime._whole_market_overflow = asyncio.Event()
  runtime._whole_market_estimated_batch_bytes = 40 * 1024 * 1024

  runtime._enqueue_whole_market_event({"600000.SH": {"lastPrice": 10.0}})
  await asyncio.sleep(0)
  runtime._enqueue_whole_market_event({"000001.SZ": {"lastPrice": 11.0}})
  await asyncio.sleep(0)

  assert runtime._whole_market_overflow.is_set()
  assert runtime._whole_market_events.qsize() == 1


@pytest.mark.asyncio
async def test_market_connection_registry_rejects_second_connection() -> None:
  from quantx_api.agent_api import _MarketConnectionRegistry

  registry = _MarketConnectionRegistry()
  first = await registry.register()

  assert first
  assert await registry.register() is None

  await registry.unregister(first)
  assert await registry.register()
