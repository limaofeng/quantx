"""Archive revision generations allocated only by the live Engine lock holder."""

import asyncio
from uuid import UUID

from sqlalchemy import text

ENGINE_LOCK_NAME = "quantx-engine-singleton-v1"

_LOCK_HELD = """
  SELECT 1 FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
  WHERE l.locktype='advisory' AND l.granted AND l.objsubid=1
    AND l.database=(SELECT oid FROM pg_database WHERE datname=current_database())
    AND l.classid::bigint=((hashtext(:lock_name)::bigint >> 32) & 4294967295)
    AND l.objid::bigint=(hashtext(:lock_name)::bigint & 4294967295)
"""


async def register_engine_archive_generation(connection, registration_id: str) -> int:
  """Call on the dedicated singleton connection, once per Engine run.

  Replaying a registration after a lost response returns its original generation.
  A new run uses a new registration ID and a dedicated physical connection;
  Engine shutdown must close that connection rather than return it to a pool.
  This function commits the allocation; it never acquires or releases the lock.
  """
  if str(UUID(registration_id)) != registration_id:
    raise ValueError("Engine archive registration requires a canonical UUID")
  async with asyncio.timeout(3):
    held = await connection.scalar(
      text("SELECT EXISTS(" + _LOCK_HELD + " AND l.pid=pg_backend_pid())"),
      {"lock_name": ENGINE_LOCK_NAME},
    )
    if not held:
      raise RuntimeError("ENGINE_ARCHIVE_SINGLETON_REQUIRED")
    await connection.execute(
      text("""
      INSERT INTO engine_archive_generation(registration_id,backend_pid,backend_start)
      SELECT :registration_id,pid,backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid()
      ON CONFLICT (registration_id) DO NOTHING
    """),
      {"registration_id": registration_id},
    )
    generation = await connection.scalar(
      text("""
      SELECT generation FROM engine_archive_generation g JOIN pg_stat_activity a
        ON a.pid=g.backend_pid AND a.backend_start=g.backend_start
      WHERE g.registration_id=:registration_id AND a.pid=pg_backend_pid()
    """),
      {"registration_id": registration_id},
    )
    if generation is None:
      raise RuntimeError("ENGINE_ARCHIVE_REGISTRATION_CONFLICT")
    await verify_engine_archive_generation(connection, int(generation))
    await connection.commit()
    return int(generation)


async def verify_engine_archive_generation(connection, generation: int) -> None:
  """Reject stale epochs even when their old process or PostgreSQL PID survives."""
  if type(generation) is not int or generation <= 0:
    raise ValueError("Engine archive generation must be a positive integer")
  async with asyncio.timeout(3):
    active = await connection.scalar(
      text(
        """
      SELECT EXISTS(SELECT 1 FROM engine_archive_generation g
      WHERE g.generation=:generation
        AND NOT EXISTS(SELECT 1 FROM engine_archive_generation newer WHERE newer.generation>g.generation)
        AND EXISTS(
    """
        + _LOCK_HELD
        + """
          AND l.pid=g.backend_pid AND a.backend_start=g.backend_start))
    """
      ),
      {"generation": generation, "lock_name": ENGINE_LOCK_NAME},
    )
    if not active:
      raise RuntimeError("ENGINE_ARCHIVE_GENERATION_INACTIVE")
