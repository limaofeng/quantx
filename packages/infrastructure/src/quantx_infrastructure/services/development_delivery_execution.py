"""Supervise one local partition while its dedicated PG lock session is alive."""

import asyncio
import hashlib

from sqlalchemy import text

from .market_data_transfer_ingestion import _settle_task

LOCK_PROBE_SECONDS = 1
LOCK_QUERY_SECONDS = 3


class DeliveryOwnershipLost(RuntimeError):
  def __init__(self):
    super().__init__("DELIVERY_EXECUTION_OWNERSHIP_LOST")


class DeliveryExecutionOwner:
  def __init__(self, key, pid, backend_start, worker_owner=None):
    self.key, self.pid, self.backend_start = key, pid, backend_start
    self.worker_owner = worker_owner
    self.active = True

  async def _guard_ingestion_owner(self, connection, *, lock=True):
    if not self.active:
      raise DeliveryOwnershipLost()
    try:
      held = await connection.scalar(
        text("""
        SELECT EXISTS(
          SELECT 1 FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
          WHERE l.locktype='advisory' AND l.granted AND l.pid=:pid
            AND a.backend_start=:started AND l.classid::bigint=:high
            AND l.objid::bigint=:low AND l.objsubid=1
        )
      """),
        {
          "pid": self.pid,
          "started": self.backend_start,
          "high": (self.key >> 32) & 0xFFFFFFFF,
          "low": self.key & 0xFFFFFFFF,
        },
      )
      if not held:
        self.active = False
        raise DeliveryOwnershipLost()
      if self.worker_owner is not None:
        await self.worker_owner._guard_ingestion_owner(connection, lock=lock)
    except Exception:
      self.active = False
      raise DeliveryOwnershipLost() from None


async def run_delivery_execution(
  request, session_factory, execute, *, worker_owner=None
):
  """The callback and sent writes settle before this function releases its lock."""
  digest = hashlib.sha256(request.model_dump_json().encode()).digest()
  key = int.from_bytes(digest[:8], "big", signed=True)
  async with session_factory() as db:
    acquired, owner, operation, monitor = None, None, None, None
    try:
      async with asyncio.timeout(LOCK_QUERY_SECONDS):
        acquired = await db.scalar(
          text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        )
        if not acquired:
          return {"status": "IMPORT_IN_PROGRESS"}
        row = (
          (
            await db.execute(
              text("""
          SELECT pid,backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid()
        """)
            )
          )
          .mappings()
          .one()
        )
        owner = DeliveryExecutionOwner(
          key, row["pid"], row["backend_start"], worker_owner
        )
        await owner._guard_ingestion_owner(db, lock=False)

      session_query = asyncio.Lock()

      async def probe():
        while True:
          await asyncio.sleep(LOCK_PROBE_SECONDS)
          try:
            async with session_query, asyncio.timeout(LOCK_QUERY_SECONDS):
              await owner._guard_ingestion_owner(db, lock=False)
          except Exception:
            owner.active = False
            raise DeliveryOwnershipLost() from None

      operation = asyncio.create_task(execute(owner))
      monitor = asyncio.create_task(probe())
      done, _ = await asyncio.wait(
        {operation, monitor}, return_when=asyncio.FIRST_COMPLETED
      )
      if monitor in done:
        monitor.result()
      result = operation.result()
      async with session_query, asyncio.timeout(LOCK_QUERY_SECONDS):
        await owner._guard_ingestion_owner(db, lock=False)
      return result
    finally:
      if owner is not None:
        owner.active = False
      interrupted = False
      if operation is not None:
        if not operation.done():
          operation.cancel()
        interrupted = await _settle_task(
          asyncio.gather(operation, return_exceptions=True)
        )
      # Cancelling an in-flight SQL probe can invalidate its connection. Keep
      # that session intact until every sent write in the operation has joined.
      if monitor is not None:
        if not monitor.done():
          monitor.cancel()
        interrupted = (
          await _settle_task(asyncio.gather(monitor, return_exceptions=True))
          or interrupted
        )

      async def release():
        if acquired is False:
          return
        try:
          if acquired is None:
            # Cancellation may hide a successful server-side lock acquisition.
            await db.invalidate()
          else:
            async with asyncio.timeout(LOCK_QUERY_SECONDS):
              unlocked = await db.scalar(
                text("SELECT pg_advisory_unlock(:key)"), {"key": key}
              )
              if not unlocked:
                await db.invalidate()
        except Exception:
          # Never return a possibly locked backend to the connection pool.
          await db.invalidate()

      cleanup = asyncio.create_task(release())
      interrupted = await _settle_task(cleanup) or interrupted
      cleanup.result()
      if interrupted:
        raise asyncio.CancelledError
