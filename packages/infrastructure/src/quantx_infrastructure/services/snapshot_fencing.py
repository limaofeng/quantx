"""Database fencing for cross-session daily snapshot writes."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Mapping

from quantx_domain.indicators import INDICATOR_VERSION
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.daily_signal_run import DailySignalRun
from quantx_infrastructure.repositories.divid_factor_repository import (
  DIVID_FACTOR_WRITE_LOCK_KEY,
)

SNAPSHOT_SESSION_LOCK_NAMESPACE = (
  int.from_bytes(
    hashlib.sha256(b"quantx:daily-indicator-snapshot-v1").digest()[:4],
    byteorder="big",
    signed=False,
  )
  & 0x7FFFFFFF
)

SNAPSHOT_FENCE_NAMESPACE = (
  int.from_bytes(
    hashlib.sha256(b"quantx:daily-indicator-snapshot-fence-v1").digest()[:4],
    byteorder="big",
    signed=False,
  )
  & 0x7FFFFFFF
)


class SnapshotFenceLost(RuntimeError):
  """A newer snapshot run superseded the caller's write generation."""


async def assert_snapshot_publish_owner(
  db: AsyncSession,
  *,
  lock_backend_pid: int,
  snapshot_dates: list[date] | tuple[date, ...],
) -> None:
  """Prove the flow's dedicated session still owns factor/date read locks."""

  dates = tuple(sorted(set(snapshot_dates)))
  if lock_backend_pid <= 0 or not dates:
    raise SnapshotFenceLost("日级快照发布缺少数据库锁所有权")
  factor_owner_held = await db.scalar(
    text(
      "SELECT EXISTS (SELECT 1 FROM pg_locks "
      "WHERE locktype = 'advisory' AND mode = 'ShareLock' AND granted "
      "AND pid = :backend_pid "
      "AND classid::bigint = "
      "((CAST(:lock_key AS bigint) >> 32) & 4294967295) "
      "AND objid::bigint = (CAST(:lock_key AS bigint) & 4294967295) "
      "AND objsubid = 1)"
    ),
    {
      "backend_pid": lock_backend_pid,
      "lock_key": DIVID_FACTOR_WRITE_LOCK_KEY,
    },
  )
  date_lock_rows = (
    (
      await db.execute(
        text(
          "SELECT objid::bigint FROM pg_locks "
          "WHERE locktype = 'advisory' AND mode = 'ExclusiveLock' AND granted "
          "AND pid = :backend_pid AND classid = CAST(:namespace AS oid) "
          "AND objsubid = 2"
        ),
        {
          "backend_pid": lock_backend_pid,
          "namespace": SNAPSHOT_SESSION_LOCK_NAMESPACE,
        },
      )
    )
    .scalars()
    .all()
  )
  if factor_owner_held is not True or set(map(int, date_lock_rows)) != {
    target.toordinal() for target in dates
  }:
    raise SnapshotFenceLost("日级快照发布数据库锁所有权已丢失")


async def acquire_snapshot_publish_guard(
  db: AsyncSession,
  *,
  lock_backend_pid: int,
  snapshot_dates: list[date] | tuple[date, ...],
) -> None:
  """Hold factor data stable in this write transaction, then verify its owner."""

  await db.execute(
    select(func.pg_advisory_xact_lock_shared(DIVID_FACTOR_WRITE_LOCK_KEY))
  )
  await assert_snapshot_publish_owner(
    db,
    lock_backend_pid=lock_backend_pid,
    snapshot_dates=snapshot_dates,
  )


async def acquire_snapshot_fences(
  db: AsyncSession,
  snapshot_dates: list[date] | tuple[date, ...],
) -> None:
  """Serialize generation creation/check/write transactions by target date."""

  for target in sorted(set(snapshot_dates)):
    await db.execute(
      select(func.pg_advisory_xact_lock(SNAPSHOT_FENCE_NAMESPACE, target.toordinal()))
    )


async def assert_snapshot_run_owner(
  db: AsyncSession,
  snapshot_run_ids: Mapping[date, int],
) -> None:
  """Lock each date and prove the caller still owns its latest run generation."""

  expected = {target: int(run_id) for target, run_id in snapshot_run_ids.items()}
  if not expected:
    raise SnapshotFenceLost("日级快照写入缺少运行代次")
  await acquire_snapshot_fences(db, tuple(expected))
  rows = (
    await db.execute(
      select(DailySignalRun.snapshot_date, func.max(DailySignalRun.id))
      .where(
        DailySignalRun.snapshot_date.in_(expected),
        DailySignalRun.signal_version == INDICATOR_VERSION,
      )
      .group_by(DailySignalRun.snapshot_date)
    )
  ).all()
  actual = {target: int(run_id) for target, run_id in rows}
  if actual != expected:
    raise SnapshotFenceLost(
      f"日级快照运行代次已被更新任务替代: expected={expected} actual={actual}"
    )
