"""Bounded shutdown of an asyncio subprocess without abandoning its waiter."""

import asyncio
import subprocess


async def stop_popen_process(process, *, grace_seconds=5.0, kill_seconds=5.0) -> bool:
  """Join synchronous Popen shutdown even if the awaiting task is cancelled.

  A returned exit code from poll/wait is required; errors and exhausted waits
  never prove exit. As with stop_async_process, the caller propagates cancellation.
  """
  def stop():
    try:
      if process.poll() is not None:
        return True
      try:
        process.terminate()
      except ProcessLookupError:
        pass
      try:
        return process.wait(timeout=grace_seconds) is not None
      except subprocess.TimeoutExpired:
        pass
      try:
        process.kill()
      except ProcessLookupError:
        pass
      return process.wait(timeout=kill_seconds) is not None
    except (OSError, subprocess.TimeoutExpired):
      return False

  task = asyncio.create_task(asyncio.to_thread(stop))
  while not task.done():
    try:
      await asyncio.shield(task)
    except asyncio.CancelledError:
      continue
  return task.result()


async def stop_async_process(process, *, grace_seconds=5.0, kill_seconds=5.0) -> bool:
  """Return true only when the direct child's exit has been observed.

  Caller cancellation cannot abandon this stop attempt; the caller owns final
  cancellation propagation. Descendants require separate supervisor enforcement.
  """

  async def stop():
    if process.returncode is not None:
      return True
    try:
      process.terminate()
    except ProcessLookupError:
      pass
    except OSError:
      return False
    try:
      await asyncio.wait_for(process.wait(), timeout=grace_seconds)
      return process.returncode is not None
    except asyncio.TimeoutError:
      pass
    try:
      process.kill()
    except ProcessLookupError:
      pass
    except OSError:
      return False
    try:
      await asyncio.wait_for(process.wait(), timeout=kill_seconds)
      return process.returncode is not None
    except asyncio.TimeoutError:
      return False

  task = asyncio.create_task(stop())
  while not task.done():
    try:
      await asyncio.shield(task)
    except asyncio.CancelledError:
      continue
  return task.result()
