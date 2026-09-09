"""Bounded shutdown of an asyncio subprocess without abandoning its waiter."""

import asyncio


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
