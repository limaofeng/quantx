from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest
from quantx_qmt_agent.health import AgentHealthState
from quantx_qmt_agent.health_server import (
  QmtAgentHealthApplication,
  QmtAgentHealthServer,
)
from quantx_qmt_agent.main import _run_runtime_guarded


def ready_state(mode: str = "live") -> AgentHealthState:
  state = AgentHealthState(mode)
  state.set_control_connected(True)
  state.set_xtdata_connected(True)
  state.set_xttrading_connected(True)
  state.set_reconciliation_ready(True)
  state.set_market_stream_status("READY")
  return state


async def request(state: AgentHealthState, method: str, path: str) -> httpx.Response:
  async with httpx.AsyncClient(
    transport=httpx.ASGITransport(app=QmtAgentHealthApplication(state)),
    base_url="http://qmt-agent",
  ) as client:
    return await client.request(method, path)


@pytest.mark.asyncio
async def test_health_live_only_proves_event_loop_liveness() -> None:
  response = await request(AgentHealthState("live"), "GET", "/health/live")

  assert response.status_code == 200
  assert response.json() == {
    "schema_version": 1,
    "observed_at": response.json()["observed_at"],
    "status": "alive",
    "component": "qmt-agent",
  }
  assert (
    datetime.fromisoformat(response.json()["observed_at"].replace("Z", "+00:00")).tzinfo
    is not None
  )


@pytest.mark.asyncio
async def test_health_ready_returns_sanitized_ready_snapshot() -> None:
  response = await request(ready_state(), "GET", "/health/ready")

  assert response.status_code == 200
  payload = response.json()
  assert payload["status"] == "ready"
  assert payload["reason_code"] is None
  assert payload["protocol_version"] == "1.1"
  assert payload["xttrading_status"] == "connected"
  serialized = response.text.lower()
  for forbidden in (
    "account",
    "device",
    "remote_address",
    "userdata",
    "credential",
    "exception",
    "traceback",
  ):
    assert forbidden not in serialized


@pytest.mark.asyncio
async def test_health_ready_reads_the_projection_exactly_once() -> None:
  state = ready_state()
  original_snapshot = state.snapshot
  calls = 0

  def counted_snapshot():
    nonlocal calls
    calls += 1
    return original_snapshot()

  state.snapshot = counted_snapshot

  response = await request(state, "GET", "/health/ready")

  assert response.status_code == 200
  assert calls == 1


@pytest.mark.asyncio
async def test_data_only_health_does_not_require_xttrading() -> None:
  response = await request(ready_state("data-only"), "GET", "/health/ready")

  assert response.status_code == 200
  assert response.json()["xttrading_status"] == "disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("mutate", "expected_status", "expected_reason"),
  [
    (
      lambda state: state.set_control_connected(False),
      "unavailable",
      "CONTROL_CONNECTION_OFFLINE",
    ),
    (
      lambda state: state.set_xtdata_connected(False),
      "unavailable",
      "XTDATA_UNAVAILABLE",
    ),
    (
      lambda state: state.set_xttrading_connected(False),
      "unavailable",
      "XTTRADING_UNAVAILABLE",
    ),
    (
      lambda state: state.set_reconciliation_ready(False),
      "degraded",
      "TRADING_RECONCILING",
    ),
    (
      lambda state: state.set_market_stream_status("STALE"),
      "degraded",
      "MARKET_STREAM_NOT_READY",
    ),
  ],
)
async def test_health_ready_uses_stable_local_reason_codes(
  mutate,
  expected_status: str,
  expected_reason: str,
) -> None:
  state = ready_state()
  mutate(state)

  response = await request(state, "GET", "/health/ready")

  assert response.status_code == 503
  assert response.json()["status"] == expected_status
  assert response.json()["reason_code"] == expected_reason


@pytest.mark.asyncio
async def test_health_surface_only_allows_fixed_get_routes() -> None:
  state = ready_state()

  post = await request(state, "POST", "/health/ready")
  openapi = await request(state, "GET", "/openapi.json")
  management = await request(state, "GET", "/health/ready?target=broker")

  assert post.status_code == 405
  assert post.headers["allow"] == "GET"
  assert openapi.status_code == 404
  assert management.status_code == 404


def test_health_listener_rejects_an_occupied_port() -> None:
  first_server = QmtAgentHealthServer(ready_state(), "127.0.0.1", 0)
  first_listener = first_server._bind_listener()
  occupied_port = int(first_listener.getsockname()[1])
  try:
    second_server = QmtAgentHealthServer(
      ready_state(),
      "127.0.0.1",
      occupied_port,
    )
    with pytest.raises(OSError):
      second_server._bind_listener()
  finally:
    first_listener.close()


class _WaitingRuntime:
  def __init__(self) -> None:
    self.cancelled = False

  async def run_forever(self) -> None:
    try:
      await asyncio.Event().wait()
    finally:
      self.cancelled = True


class _WaitingWatchdog:
  def __init__(self) -> None:
    self.cancelled = False

  async def heartbeat_loop(self) -> None:
    try:
      await asyncio.Event().wait()
    finally:
      self.cancelled = True


@pytest.mark.asyncio
async def test_health_server_exit_ends_the_main_runtime_boundary() -> None:
  runtime = _WaitingRuntime()
  watchdog = _WaitingWatchdog()

  class StoppedHealthServer:
    @staticmethod
    async def run() -> None:
      return None

  with pytest.raises(RuntimeError, match="health server stopped unexpectedly"):
    await _run_runtime_guarded(runtime, watchdog, StoppedHealthServer())

  assert runtime.cancelled
  assert watchdog.cancelled


@pytest.mark.asyncio
async def test_normal_runtime_shutdown_cancels_health_server_without_orphan() -> None:
  closed = asyncio.Event()

  class CompletedRuntime:
    @staticmethod
    async def run_forever() -> None:
      await asyncio.sleep(0)

  class WaitingHealthServer:
    @staticmethod
    async def run() -> None:
      try:
        await asyncio.Event().wait()
      finally:
        closed.set()

  await _run_runtime_guarded(
    CompletedRuntime(),
    _WaitingWatchdog(),
    WaitingHealthServer(),
  )

  assert closed.is_set()
