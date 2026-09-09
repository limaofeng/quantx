"""Worker-owned staging maintenance; unfinished immutable evidence is retained."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select

from quantx_infrastructure.auth.tokens import utcnow
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  MarketDataRequest,
  MarketDataTransfer,
)
from quantx_infrastructure.services import market_data_staging as _market_data_staging

logger = logging.getLogger(__name__)
_market_data_staging_sweep_lock = asyncio.Lock()
MARKET_DATA_ROOT = _market_data_staging.market_data_staging_root()
_is_reparse_point = _market_data_staging.is_reparse_point
_market_data_request_staging_usage_bytes = (
  _market_data_staging.market_data_request_staging_usage_bytes
)
_safe_market_data_request_directory = (
  _market_data_staging.safe_market_data_request_directory
)
_relative_market_data_storage_reference = (
  _market_data_staging.relative_market_data_storage_reference
)
MARKET_DATA_STAGING_SWEEP_SECONDS = 5 * 60
MARKET_DATA_STAGING_TEMP_GRACE_SECONDS = 60 * 60
MARKET_DATA_STAGING_ORPHAN_GRACE_SECONDS = 60 * 60
MARKET_DATA_STAGING_FAILED_RETENTION_SECONDS = 24 * 60 * 60


async def _check_owner(owner, connection=None):
  if connection is not None:
    await owner._guard_ingestion_owner(connection, lock=True)
    return
  async with owner.engine.connect() as connection:
    await owner._guard_ingestion_owner(connection, lock=False)


async def _joined_thread(function, *args):
  task = asyncio.create_task(asyncio.to_thread(function, *args))
  try:
    return await asyncio.shield(task)
  except asyncio.CancelledError:
    await asyncio.gather(task, return_exceptions=True)
    raise


def _as_aware_utc(value: datetime | None) -> datetime | None:
  if value is None:
    return None
  if value.tzinfo is None:
    return value.replace(tzinfo=timezone.utc)
  return value.astimezone(timezone.utc)


def _remove_safe_market_data_request_directory(root: Path, candidate: Path) -> None:
  try:
    resolved = _safe_market_data_request_directory(root, candidate)
  except FileNotFoundError:
    return
  for descendant in resolved.rglob("*"):
    if descendant.is_symlink() or _is_reparse_point(descendant):
      raise RuntimeError("refusing to remove linked market-data staging content")
    resolved_descendant = descendant.resolve()
    if resolved not in resolved_descendant.parents:
      raise RuntimeError("market-data staging content escaped request directory")
  shutil.rmtree(resolved)


async def sweep_market_data_staging_once(
  *,
  owner,
  now: datetime | None = None,
) -> dict[str, int]:
  """Remove stale temporary, terminal, and orphan Agent upload staging safely."""
  await _check_owner(owner)
  current = _as_aware_utc(now if now is not None else utcnow())
  if current is None:  # pragma: no cover - the expression above is never None
    raise RuntimeError("market-data staging sweep requires a clock value")
  removed_directories = 0
  removed_temporary_files = 0
  root = MARKET_DATA_ROOT
  if not root.exists():
    return {"directories": 0, "temporary_files": 0}

  # Uploads acquire a request row lock before the staging lock. The sweeper uses
  # a distinct process lock and relies on the same request row lock, avoiding a
  # staging-lock/row-lock inversion while still serializing sweep runs.
  async with _market_data_staging_sweep_lock:
    if root.is_symlink() or _is_reparse_point(root):
      raise RuntimeError("unsafe market-data staging root")
    candidates: dict[str, Path] = {}
    for child in list(root.iterdir()):
      if not child.is_dir():
        continue
      try:
        resolved = _safe_market_data_request_directory(root, child)
      except RuntimeError:
        logger.warning("Skipped unsafe market-data staging entry: %s", child)
        continue
      candidates[child.name] = resolved
      temporary_cutoff = current - timedelta(
        seconds=MARKET_DATA_STAGING_TEMP_GRACE_SECONDS
      )
      for temporary in resolved.glob("*.tmp"):
        if temporary.is_symlink() or _is_reparse_point(temporary):
          logger.warning("Skipped unsafe market-data staging temp: %s", temporary)
          continue
        modified = datetime.fromtimestamp(
          temporary.stat().st_mtime,
          tz=timezone.utc,
        )
        if modified <= temporary_cutoff:
          temporary.unlink(missing_ok=True)
          removed_temporary_files += 1

    if not candidates:
      return {
        "directories": removed_directories,
        "temporary_files": removed_temporary_files,
      }

    async with AsyncSessionLocal() as db:
      rows = (
        await db.execute(
          select(
            MarketDataRequest.request_id,
            MarketDataRequest.status,
            MarketDataRequest.completed_at,
            MarketDataRequest.updated_at,
          ).where(MarketDataRequest.request_id.in_(tuple(candidates)))
        )
      ).all()
    requests = {str(row.request_id): row for row in rows}
    orphan_cutoff = current - timedelta(
      seconds=MARKET_DATA_STAGING_ORPHAN_GRACE_SECONDS
    )
    failed_cutoff = current - timedelta(
      seconds=MARKET_DATA_STAGING_FAILED_RETENTION_SECONDS
    )
    for request_id, directory in candidates.items():
      await _check_owner(owner)
      request_row = requests.get(request_id)
      remove = False
      if request_row is None:
        modified = datetime.fromtimestamp(
          directory.stat().st_mtime,
          tz=timezone.utc,
        )
        remove = modified <= orphan_cutoff
      else:
        status = str(request_row.status or "").upper()
        if status in {"COMPLETED", "FAILED"}:
          # Recheck under the request row lock. A FAILED request may be reopened
          # for ingestion by another process between the initial scan and delete.
          async with AsyncSessionLocal() as db:
            locked = await db.scalar(
              select(MarketDataRequest)
              .where(MarketDataRequest.request_id == request_id)
              .with_for_update()
            )
            retired_failed_manifest = False
            if locked is not None:
              locked_status = str(locked.status or "").upper()
              if locked_status == "COMPLETED":
                remove = True
              elif locked_status == "FAILED" and not (
                locked.expected_chunks
                and locked.received_chunks == locked.expected_chunks
              ):
                terminal_at = _as_aware_utc(locked.completed_at or locked.updated_at)
                remove = terminal_at is not None and terminal_at <= failed_cutoff
                if remove:
                  # Retiring the durable manifest before deleting its files
                  # prevents a later FAILED recovery from reopening paths that
                  # no longer exist. It will derive a fresh request instead.
                  await _check_owner(owner, await db.connection())
                  await db.execute(
                    delete(MarketDataTransfer).where(
                      MarketDataTransfer.request_id == request_id
                    )
                  )
                  locked.expected_chunks = None
                  locked.received_chunks = 0
                  await db.commit()
                  retired_failed_manifest = True
              if remove:
                await _joined_thread(
                  _remove_safe_market_data_request_directory,
                  root,
                  directory,
                )
                removed_directories += 1
            if not retired_failed_manifest:
              await db.rollback()
          continue
      if not remove:
        continue
      await _joined_thread(
        _remove_safe_market_data_request_directory,
        root,
        directory,
      )
      removed_directories += 1

  return {
    "directories": removed_directories,
    "temporary_files": removed_temporary_files,
  }


async def run_market_data_staging_sweeper(stopped: asyncio.Event, *, owner) -> None:
  while not stopped.is_set():
    try:
      removed = await sweep_market_data_staging_once(owner=owner)
      if removed["directories"] or removed["temporary_files"]:
        logger.info("Cleaned market-data staging: %s", removed)
    except asyncio.CancelledError:
      raise
    except Exception:
      logger.exception("Could not sweep market-data staging")
    try:
      await asyncio.wait_for(
        stopped.wait(),
        timeout=MARKET_DATA_STAGING_SWEEP_SECONDS,
      )
    except asyncio.TimeoutError:
      pass
