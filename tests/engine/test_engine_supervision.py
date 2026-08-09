import asyncio

import pytest
import quantx_engine.main as engine_main
from quantx_engine.main import (
  ENGINE_LEASE_IDLE_TIMEOUT_SECONDS,
  _acquire_engine_lease,
  _lease_watchdog,
  _wait_for_stop_or_failure,
)


@pytest.mark.asyncio
async def test_critical_engine_task_failure_is_propagated() -> None:
  stopped = asyncio.Event()

  async def fail():
    await asyncio.sleep(0)
    raise ValueError("consumer failed")

  task = asyncio.create_task(fail(), name="test-consumer")

  with pytest.raises(RuntimeError, match="test-consumer") as error:
    await _wait_for_stop_or_failure(stopped, [task])

  assert isinstance(error.value.__cause__, ValueError)


@pytest.mark.asyncio
async def test_failure_is_not_hidden_when_task_also_requests_stop() -> None:
  stopped = asyncio.Event()

  async def fail_and_stop():
    stopped.set()
    raise ConnectionError("database lease lost")

  task = asyncio.create_task(fail_and_stop(), name="lease-watchdog")

  with pytest.raises(RuntimeError, match="lease-watchdog") as error:
    await _wait_for_stop_or_failure(stopped, [task])

  assert isinstance(error.value.__cause__, ConnectionError)


@pytest.mark.asyncio
async def test_engine_supervision_returns_on_requested_stop() -> None:
  stopped = asyncio.Event()

  async def run_until_stopped():
    await stopped.wait()

  task = asyncio.create_task(run_until_stopped(), name="test-consumer")
  stopped.set()

  await _wait_for_stop_or_failure(stopped, [task])
  await task


@pytest.mark.asyncio
async def test_lost_lease_requests_shutdown(monkeypatch) -> None:
  stopped = asyncio.Event()

  class BrokenConnection:
    async def execute(self, _statement):
      raise ConnectionError("database disconnected")

    async def commit(self):
      raise AssertionError("commit should not follow a failed lease check")

  real_wait_for = asyncio.wait_for

  async def immediate_timeout(awaitable, *, timeout):
    if timeout == 5.0:
      awaitable.close()
      raise asyncio.TimeoutError
    return await real_wait_for(awaitable, timeout=timeout)

  monkeypatch.setattr(asyncio, "wait_for", immediate_timeout)

  with pytest.raises(RuntimeError, match="lease connection was lost"):
    await _lease_watchdog(stopped, BrokenConnection())

  assert stopped.is_set()


@pytest.mark.asyncio
async def test_engine_supervisor_restarts_after_critical_failure(
  monkeypatch,
) -> None:
  attempts = 0

  async def run_once():
    nonlocal attempts
    attempts += 1
    if attempts == 1:
      raise ConnectionError("database disconnected")

  async def no_wait(_seconds):
    return None

  monkeypatch.setattr(engine_main, "run_engine", run_once)
  monkeypatch.setattr(engine_main.asyncio, "sleep", no_wait)

  await engine_main.run_engine_supervised()

  assert attempts == 2


@pytest.mark.asyncio
async def test_engine_lease_waits_for_crashed_session_to_expire(
  monkeypatch,
) -> None:
  class Result:
    def __init__(self, value):
      self.value = value

    def scalar(self):
      return self.value

  class RecoveringConnection:
    def __init__(self):
      self.lock_results = iter((False, False, True))
      self.configuration = None
      self.commit_count = 0

    async def execute(self, statement, parameters=None):
      if "set_config" in str(statement):
        self.configuration = parameters
        return Result(None)
      return Result(next(self.lock_results))

    async def commit(self):
      self.commit_count += 1

  async def no_wait(_seconds):
    return None

  monkeypatch.setattr(asyncio, "sleep", no_wait)
  connection = RecoveringConnection()

  await _acquire_engine_lease(
    connection,
    timeout_seconds=1.0,
    retry_seconds=0.01,
  )

  assert connection.configuration == {
    "idle_timeout": f"{ENGINE_LEASE_IDLE_TIMEOUT_SECONDS}s",
    "keepalive_idle": "15",
    "keepalive_interval": "5",
    "keepalive_count": "3",
  }
  assert connection.commit_count == 4


@pytest.mark.asyncio
async def test_engine_lease_refuses_second_live_instance() -> None:
  class Result:
    def scalar(self):
      return False

  class BusyConnection:
    async def execute(self, _statement, _parameters=None):
      return Result()

    async def commit(self):
      return None

  with pytest.raises(RuntimeError, match="已有 QuantX Engine"):
    await _acquire_engine_lease(
      BusyConnection(),
      timeout_seconds=0.0,
    )
