"""Runtime wiring of the dedicated history lifetime without broker SDK calls."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from quantx_qmt_agent import history_session
from quantx_qmt_agent.runtime import AgentRuntime, _connect_websocket


@pytest.fixture
def runtime():
  value = AgentRuntime.__new__(AgentRuntime)
  value.configuration = SimpleNamespace(
    api_url="https://history.test", device_id=str(uuid4())
  )
  value._broker_ready = asyncio.Event()
  value._broker_ready.set()
  value._stopped = asyncio.Event()
  value._history_pipeline = SimpleNamespace(
    reset_session=AsyncMock(), handle=AsyncMock()
  )
  value._history_access_token = AsyncMock(return_value="history-only")
  value._advertised_capabilities = lambda: ["market-data"]
  value._is_market_data_ready = lambda: True
  value._history_resource_block_reason = lambda: "BROKER_REPORT_PENDING"
  value._control_session_authenticated = False
  return value


async def test_independent_wiring_and_stop_joins_session_cleanup(runtime, monkeypatch):
  entered, cleaned = asyncio.Event(), asyncio.Event()
  options = {}

  class Client:
    def __init__(self, **kwargs):
      options.update(kwargs)

    async def run(self):
      entered.set()
      try:
        await asyncio.Event().wait()
      finally:
        await options["reset"]()
        cleaned.set()

  monkeypatch.setattr(history_session, "HistorySessionClient", Client)
  task = asyncio.create_task(runtime._history_collection_supervisor())
  try:
    await asyncio.wait_for(entered.wait(), 1)
    assert options["connect"] is _connect_websocket
    assert await options["token"]() == "history-only"
    beat = options["health"]()
    assert beat.xtdata_ready and beat.qos_reason == "BROKER_REPORT_PENDING"
    runtime._history_resource_block_reason = lambda: ""
    assert options["health"]().qos_reason is None
    await options["handle"]("work")
    runtime._history_pipeline.handle.assert_awaited_once_with("work")
    runtime._stopped.set()
    await asyncio.wait_for(task, 1)
    assert cleaned.is_set() and runtime._history_collection_client is None
    runtime._history_pipeline.reset_session.assert_awaited_once()
  finally:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_cancellation_before_broker_ready_never_opens_history(
  runtime, monkeypatch
):
  runtime._broker_ready.clear()
  constructor = AsyncMock()
  monkeypatch.setattr(history_session, "HistorySessionClient", constructor)
  task = asyncio.create_task(runtime._history_collection_supervisor())
  await asyncio.sleep(0)
  task.cancel()
  with pytest.raises(asyncio.CancelledError):
    await task
  constructor.assert_not_called()


async def test_transport_retry_reuses_client_and_waits_for_prior_cleanup(
  runtime, monkeypatch
):
  options = {}
  calls, instances = [], []
  second = asyncio.Event()

  class Client:
    def __init__(self, **kwargs):
      options.update(kwargs)
      instances.append(self)

    async def run(self):
      calls.append("run")
      try:
        if len(calls) == 1:
          raise ConnectionError("private endpoint")
        second.set()
        await asyncio.Event().wait()
      finally:
        calls.append("cleaned")

  monkeypatch.setattr(history_session, "HistorySessionClient", Client)
  task = asyncio.create_task(runtime._history_collection_supervisor())
  try:
    await asyncio.wait_for(second.wait(), 2)
    assert calls == ["run", "cleaned", "run"]
    assert len(instances) == 1
    runtime._stopped.set()
    await asyncio.wait_for(task, 1)
    assert calls[-1] == "cleaned"
  finally:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
