import asyncio
import os
import sys

import pytest
from quantx_infrastructure.async_process_stop import stop_async_process


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
