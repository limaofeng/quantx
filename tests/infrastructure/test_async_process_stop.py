import asyncio
import os
import subprocess
import sys

import pytest
from quantx_infrastructure.async_process_stop import (
  stop_async_process,
  stop_popen_process,
)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX signal fixture")
async def test_real_child_ignoring_terminate_is_killed_and_reaped():
  process = await asyncio.create_subprocess_exec(
    sys.executable,
    "-c",
    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready',flush=True); time.sleep(30)",
    stdout=asyncio.subprocess.PIPE,
  )
  try:
    assert await asyncio.wait_for(process.stdout.readline(), 3) == b"ready\n"
    assert await stop_async_process(process, grace_seconds=0.05, kill_seconds=1)
    assert process.returncode is not None and process.returncode != 0
  finally:
    if process.returncode is None:
      process.kill()
      await process.wait()


@pytest.mark.asyncio
async def test_unobserved_exit_is_not_reported_as_stopped_despite_cancellation():
  events = []

  class Process:
    returncode = None

    def terminate(self):
      events.append("terminate")

    def kill(self):
      events.append("kill")

    async def wait(self):
      await asyncio.Event().wait()

  task = asyncio.create_task(
    stop_async_process(Process(), grace_seconds=0.02, kill_seconds=0.02)
  )
  await asyncio.sleep(0.005)
  task.cancel()
  assert await asyncio.wait_for(task, 1) is False
  assert events == ["terminate", "kill"]


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX signal fixture")
async def test_popen_stop_joins_real_child_despite_repeated_cancellation():
  process = subprocess.Popen(
    [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready',flush=True); time.sleep(30)"],
    stdout=subprocess.PIPE,
  )
  try:
    assert await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 3) == b"ready\n"
    task = asyncio.create_task(stop_popen_process(process, grace_seconds=0.1, kill_seconds=1))
    for _ in range(3):
      await asyncio.sleep(0.01)
      task.cancel()
    assert await asyncio.wait_for(task, 2) is True
    assert process.poll() is not None
  finally:
    if process.poll() is None:
      process.kill()
      process.wait(timeout=3)
    process.stdout.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["poll", "terminate", "kill", "wait"])
async def test_popen_unconfirmed_stop_never_reports_exit(failure):
  class Process:
    def poll(self):
      if failure == "poll":
        raise OSError("unavailable")
      return None

    def terminate(self):
      if failure == "terminate":
        raise OSError("denied")

    def kill(self):
      if failure == "kill":
        raise OSError("denied")

    def wait(self, timeout):
      raise subprocess.TimeoutExpired("research", timeout)

  assert await stop_popen_process(Process(), grace_seconds=0.01, kill_seconds=0.01) is False
