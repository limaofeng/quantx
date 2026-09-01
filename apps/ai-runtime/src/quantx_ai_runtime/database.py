"""Bound AI database work before checkout, keeping heartbeat capacity available."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from quantx_infrastructure.async_lifecycle import finish_cleanup
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.relational_connection import (
  AsyncSessionLocal,
  database_pool_snapshot,
  pool_profile,
)
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class DatabaseCapacityTimeout(PoolTimeout):
  """The AI work queue could not enter its bounded database budget in time."""


class DatabaseAdmission:
  def __init__(self, maximum_connections: int, timeout_seconds: float) -> None:
    # Share the existing pool; do not create a second pool for housekeeping.
    # With a one-connection override all work, including heartbeats, serializes.
    self._all_slots = asyncio.BoundedSemaphore(maximum_connections)
    self._work_slots = asyncio.BoundedSemaphore(max(1, maximum_connections - 1))
    self._timeout_seconds = timeout_seconds

  @asynccontextmanager
  async def slot(self, *, heartbeat: bool = False) -> AsyncIterator[None]:
    work_acquired = False
    pool_acquired = False
    try:
      try:
        async with asyncio.timeout(self._timeout_seconds):
          if not heartbeat:
            await self._work_slots.acquire()
            work_acquired = True
          await self._all_slots.acquire()
          pool_acquired = True
      except TimeoutError:
        raise DatabaseCapacityTimeout("AI_DATABASE_CAPACITY_TIMEOUT") from None
      yield
    finally:
      if pool_acquired:
        self._all_slots.release()
      if work_acquired:
        self._work_slots.release()


database_admission = DatabaseAdmission(
  pool_profile.maximum_connections,
  settings.database_pool_timeout_seconds,
)


@asynccontextmanager
async def database_work_slot() -> AsyncIterator[None]:
  """Also budget services that open their own short-lived database session."""
  async with database_admission.slot():
    yield


@asynccontextmanager
async def database_session(*, heartbeat: bool = False) -> AsyncIterator[AsyncSession]:
  # Release admission only AFTER rollback/close has returned the connection.
  # No model request, Redis wait or retry sleep belongs inside this context.
  async with database_admission.slot(heartbeat=heartbeat):
    db = AsyncSessionLocal()
    try:
      yield db
    finally:
      # AsyncSession.__aexit__ shields close but can return before it finishes
      # if this task is cancelled. Own and join close before releasing admission.
      await finish_cleanup(db.close())


def log_database_pressure(operation: str, exc: PoolTimeout) -> None:
  snapshot = database_pool_snapshot()
  logger.warning(
    "AI database capacity timeout: operation=%s error=%s checked_out=%s maximum=%s",
    operation,
    type(exc).__name__,
    snapshot["checked_out"],
    snapshot["maximum"],
  )


async def wait_for_database_retry(stopped: asyncio.Event) -> None:
  """Bound retry frequency, remain interruptible, and hold no session or permit."""
  try:
    await asyncio.wait_for(stopped.wait(), timeout=1.0)
  except TimeoutError:
    pass
