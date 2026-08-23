from __future__ import annotations

import asyncio

import pytest
from quantx_engine.t_trade_coordination import TTradeCoordinationLock


@pytest.mark.asyncio
async def test_cancelled_account_lock_waiter_never_releases_owner():
  lock = TTradeCoordinationLock()
  assert lock.try_acquire() is True

  waiter = asyncio.create_task(lock.acquire())
  await asyncio.sleep(0)
  waiter.cancel()
  with pytest.raises(asyncio.CancelledError):
    await waiter

  assert lock.locked() is True
  lock.release()
  assert lock.locked() is False
  assert lock.try_acquire() is True
  lock.release()


@pytest.mark.asyncio
async def test_account_lock_handoff_is_fifo_and_mutually_exclusive():
  lock = TTradeCoordinationLock()
  await lock.acquire()
  order: list[str] = []

  async def waiter(name: str) -> None:
    await lock.acquire()
    order.append(name)
    await asyncio.sleep(0)
    lock.release()

  first = asyncio.create_task(waiter("first"))
  second = asyncio.create_task(waiter("second"))
  await asyncio.sleep(0)
  lock.release()
  await asyncio.gather(first, second)

  assert order == ["first", "second"]
  assert lock.locked() is False
