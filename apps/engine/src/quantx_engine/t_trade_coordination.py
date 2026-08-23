"""Process-local serialization for one account's T-trade control plane.

The Engine is lease-protected as a single active process.  Within that process,
configuration mutations and manual approvals must share one account lock so a
candidate is linearized either before or after a successful configuration
change, never in the gap between the database commit and runtime rewarm.
"""

from __future__ import annotations

import asyncio
import weakref
from collections import deque
from typing import Deque


class TTradeCoordinationLock:
  """Task-safe account lock with a synchronous fail-closed fast path."""

  def __init__(self) -> None:
    self._locked = False
    self._waiters: Deque[asyncio.Future[None]] = deque()

  def locked(self) -> bool:
    return self._locked

  def try_acquire(self) -> bool:
    """Acquire without yielding; market-event candidates use this method."""

    if self._locked:
      return False
    self._locked = True
    return True

  async def acquire(self) -> bool:
    if self.try_acquire():
      return True
    waiter = asyncio.get_running_loop().create_future()
    self._waiters.append(waiter)
    try:
      await waiter
    except BaseException:
      # asyncio propagates task cancellation to a pending Future.  Removing
      # that waiter must never release the current owner's lock.  A
      # non-cancelled done Future means release already handed ownership to
      # this task immediately before it was canceled, so transfer it onward.
      if waiter.done() and not waiter.cancelled():
        self.release()
      else:
        try:
          self._waiters.remove(waiter)
        except ValueError:
          pass
      raise
    return True

  def release(self) -> None:
    if not self._locked:
      raise RuntimeError("T-trade account coordination lock is not locked")
    while self._waiters:
      waiter = self._waiters.popleft()
      if waiter.done():
        continue
      # Keep the lock marked as held while ownership is handed to the waiter.
      waiter.set_result(None)
      return
    self._locked = False

  async def __aenter__(self) -> "TTradeCoordinationLock":
    await self.acquire()
    return self

  async def __aexit__(self, *_args: object) -> None:
    self.release()


_LOCKS_BY_LOOP: weakref.WeakKeyDictionary[
  asyncio.AbstractEventLoop,
  dict[str, TTradeCoordinationLock],
] = weakref.WeakKeyDictionary()


def t_trade_account_coordination_lock(account_id: str) -> TTradeCoordinationLock:
  """Return the event-loop-local lock for one normalized broker account."""

  normalized = str(account_id or "").strip()
  if not normalized:
    raise ValueError("account_id is required for T-trade coordination")
  loop = asyncio.get_running_loop()
  locks = _LOCKS_BY_LOOP.setdefault(loop, {})
  return locks.setdefault(normalized, TTradeCoordinationLock())


__all__ = ["TTradeCoordinationLock", "t_trade_account_coordination_lock"]
