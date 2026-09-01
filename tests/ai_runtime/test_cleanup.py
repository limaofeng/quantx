from __future__ import annotations

import asyncio

import pytest
from quantx_infrastructure.async_lifecycle import finish_cleanup


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [None, ValueError, asyncio.CancelledError])
async def test_cleanup_completion_and_failures_are_not_swallowed(error):
  calls = 0

  async def cleanup():
    nonlocal calls
    calls += 1
    await asyncio.sleep(0)
    if error is not None:
      raise error("cleanup error")

  if error is None:
    await finish_cleanup(cleanup())
  else:
    with pytest.raises(error, match="cleanup error"):
      await asyncio.wait_for(finish_cleanup(cleanup()), timeout=1)
  assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_repeated_caller_cancellation_waits_and_stays_cancelled(cleanup_fails):
  started = asyncio.Event()
  allow_close = asyncio.Event()
  closed = asyncio.Event()

  async def cleanup():
    started.set()
    await allow_close.wait()
    closed.set()
    if cleanup_fails:
      raise ValueError("cleanup error")

  task = asyncio.create_task(finish_cleanup(cleanup()))
  try:
    await asyncio.wait_for(started.wait(), timeout=1)
    for _ in range(2):
      task.cancel("shutdown")
      await asyncio.sleep(0)
      assert not task.done()
      assert not closed.is_set()
    allow_close.set()
    with pytest.raises(asyncio.CancelledError, match="shutdown"):
      await asyncio.wait_for(task, timeout=1)
    assert closed.is_set()
  finally:
    allow_close.set()
    await asyncio.gather(task, return_exceptions=True)
