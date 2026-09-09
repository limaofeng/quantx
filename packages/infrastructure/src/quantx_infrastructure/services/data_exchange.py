"""Durable export catalog. Files are immutable, content-addressed market data."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from quantx_contracts.data_exchange import HistoryPartitionRequest
from sqlalchemy import text

from quantx_infrastructure.database.connection import AsyncSessionLocal


def export_root() -> Path:
  from quantx_infrastructure.config.settings import WORKSPACE_ROOT

  return Path(
    os.environ.get(
      "QUANTX_DATA_EXPORT_ROOT", str(WORKSPACE_ROOT / ".runtime/data-exports")
    )
  ).resolve()


def content_path(digest: str) -> Path:
  if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
    raise ValueError("Invalid content digest")
  path = export_root() / f"{digest}.json.gz"
  if path.is_symlink():
    raise ValueError("Symlink archive is forbidden")
  return path


class ExportQueueCapacity(ValueError):
  pass


async def submit(request: HistoryPartitionRequest) -> str:
  async with AsyncSessionLocal() as db:
    identity = await submit_in_transaction(request, db)
    await db.commit()
  return identity


async def submit_in_transaction(request: HistoryPartitionRequest, db) -> str:
  """Publish a catalog row inside the caller's fenced transaction."""
  encoded = json.dumps(request.model_dump(mode="json"), sort_keys=True)
  identity = hashlib.sha256(("daily-limits-v2:" + encoded).encode()).hexdigest()
  await db.execute(text("SELECT pg_advisory_xact_lock(817234592)"))
  existing = await db.scalar(
    text("SELECT id FROM development_data_export WHERE id=:id"), {"id": identity}
  )
  if existing is None:
    pending = await db.scalar(
      text(
        "SELECT count(*) FROM development_data_export WHERE state IN ('QUEUED','WAITING_SOURCE')"
      )
    )
    if pending >= 5000:
      raise ExportQueueCapacity("History request queue capacity reached")
  await db.execute(
    text("""
    INSERT INTO development_data_export(id,request,state,updated_at)
    VALUES (:id,CAST(:request AS JSON),'QUEUED',CURRENT_TIMESTAMP)
    ON CONFLICT(id) DO UPDATE SET state = CASE
      WHEN development_data_export.expires_at < CURRENT_TIMESTAMP
        OR development_data_export.state='EXPIRED' THEN 'QUEUED'
      ELSE development_data_export.state END
  """),
    {"id": identity, "request": encoded},
  )
  return identity


async def get_export(identity: str) -> dict | None:
  async with AsyncSessionLocal() as db:
    result = (
      (
        await db.execute(
          text("""
      SELECT id, request, state, manifest, error, expires_at
      FROM development_data_export WHERE id=:id
    """),
          {"id": identity},
        )
      )
      .mappings()
      .one_or_none()
    )
    return dict(result) if result else None


async def backtest_data_versions(instruments: list[str], start, end) -> list[dict]:
  """Capture imported partition versions at run initialization."""
  from quantx_infrastructure.config.settings import settings

  if settings.environment != "development":
    return []
  async with AsyncSessionLocal() as db:
    rows = (
      (
        await db.execute(
          text("""
      SELECT request, manifest->>'data_version' AS version
      FROM development_data_export WHERE state IN ('LOCAL_VERIFIED','REFERENCE_VERIFIED')
      AND request->>'instrument' = ANY(:codes)
      AND request->>'trading_date' BETWEEN :start AND :end
      ORDER BY id
    """),
          {
            "codes": instruments,
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
          },
        )
      )
      .mappings()
      .all()
    )
  return [{"partition": row["request"], "version": row["version"]} for row in rows]
