"""Explicit forward migration of frozen canonical checkpoints; never writes bars."""

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from .immutable_bar_storage import prepare_native_bar_bundle
from .market_data_ingestion_progress import evidence_hash
from .market_data_readback_diagnostic import RequestSnapshot
from .market_data_transfer_ingestion import (
  MAX_TRANSFER_REQUEST_CHUNKS,
  load_uploaded_request_manifest,
)


class NativeCheckpointPlan(BaseModel):
  model_config = ConfigDict(extra="forbid")
  request_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
  source_row_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  transfer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  old_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  native_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  native_storage_version: str = Field(pattern=r"^[0-9a-f]{64}$")


def manifest_hashes(payload, manifest, version):
  original = {
    "payload": payload,
    "chunks": [
      {k: v for k, v in item.items() if k != "storage_reference"} for item in manifest
    ],
  }
  return (
    evidence_hash(original),
    evidence_hash(
      {"storage_format": "native-bars-v1", "storage_version": version, **original}
    ),
  )


async def _snapshot(db, request_id):
  row = await db.scalar(
    text("SELECT to_jsonb(r) FROM market_data_request r WHERE request_id=:id"),
    {"id": request_id},
  )
  if row is None:
    raise ValueError("NATIVE_MIGRATION_SOURCE_MISSING")
  transfers = [
    dict(r)
    for r in (
      await db.execute(
        text("""
    SELECT chunk_index,checksum_sha256,record_count,compressed,compressed_bytes,storage_reference
    FROM market_data_transfer WHERE request_id=:id ORDER BY chunk_index LIMIT :limit
  """),
        {"id": request_id, "limit": MAX_TRANSFER_REQUEST_CHUNKS + 1},
      )
    )
    .mappings()
    .all()
  ]
  if len(transfers) > MAX_TRANSFER_REQUEST_CHUNKS:
    raise ValueError("NATIVE_MIGRATION_TRANSFER_CAPACITY")
  return row, transfers


def _eligible(row, old_hash):
  if row["status"] not in ("UPLOADED", "BLOCKED", "COMPLETED") or row.get(
    "processing_claim_token"
  ):
    raise ValueError("NATIVE_MIGRATION_SOURCE_ACTIVE")
  previous = row.get("ingestion_progress")
  if (
    not isinstance(previous, dict)
    or type(previous.get("version")) is not int
    or previous["version"] != 1
  ):
    raise ValueError("NATIVE_MIGRATION_BUDGET_EVIDENCE_REQUIRED")
  if (
    previous.get("phase") not in ("VALIDATE", "WRITE", "READBACK", "VERIFIED")
    or any(
      type(previous.get(key)) is not int or previous[key] < minimum
      for key, minimum in (("attempt", 1), ("executions", 0))
    )
    or not isinstance(previous.get("checkpoints"), dict)
    or not isinstance(previous.get("failures"), list)
  ):
    raise ValueError("NATIVE_MIGRATION_BUDGET_EVIDENCE_REQUIRED")
  try:
    datetime.fromisoformat(previous["stage_started_at"])
  except (KeyError, TypeError, ValueError):
    raise ValueError("NATIVE_MIGRATION_BUDGET_EVIDENCE_REQUIRED") from None
  if previous.get("manifest_hash") != old_hash or previous.get(
    "legacy_storage_migration"
  ):
    raise ValueError("NATIVE_MIGRATION_MANIFEST_CONFLICT")


async def plan_native_migration(engine, request_id):
  async with engine.connect() as db, db.begin():
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    row, transfers = await _snapshot(db, request_id)
  snapshot = RequestSnapshot(request_id, row, transfers)
  _, payload, manifest = await load_uploaded_request_manifest(snapshot, request_id)
  version = await prepare_native_bar_bundle(payload, manifest)
  old_hash, new_hash = manifest_hashes(payload, manifest, version.storage_version)
  _eligible(row, old_hash)
  return NativeCheckpointPlan(
    request_id=request_id,
    source_row_sha256=evidence_hash(row),
    transfer_sha256=evidence_hash(transfers),
    old_manifest_sha256=old_hash,
    native_manifest_sha256=new_hash,
    native_storage_version=version.storage_version,
  )


def migrated_progress(row, plan, now):
  _eligible(row, plan.old_manifest_sha256)
  state = deepcopy(row["ingestion_progress"])
  state["legacy_storage_migration"] = {
    "source_row_sha256": plan.source_row_sha256,
    "transfer_sha256": plan.transfer_sha256,
    "previous_progress": deepcopy(row["ingestion_progress"]),
    "previous_status": row["status"],
    "previous_result": deepcopy(row.get("ingestion_result")),
    "previous_processing_error": row.get("processing_error"),
    "native_storage_version": plan.native_storage_version,
    "at": now.isoformat(),
  }
  # Old confirmations remain in the audit, never become confirmations of the new table.
  # Counters, attempt, stage deadline and failure history stay unchanged.
  state.update(
    phase="WRITE",
    manifest_hash=plan.native_manifest_sha256,
    checkpoints={},
    write_result=None,
    blocked=True,
    reason_code="NATIVE_STORAGE_MIGRATED_REQUIRES_RECOVERY",
    next_retry_at=None,
    last_progress_at=now.isoformat(),
  )
  return state


async def apply_native_migration(engine, plan):
  plan = NativeCheckpointPlan.model_validate(plan.model_dump())
  # Re-read and verify original files; this performs no Influx write or proof publication.
  async with engine.connect() as db, db.begin():
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    row, transfers = await _snapshot(db, plan.request_id)
  snapshot = RequestSnapshot(plan.request_id, row, transfers)
  _, payload, manifest = await load_uploaded_request_manifest(snapshot, plan.request_id)
  version = await prepare_native_bar_bundle(payload, manifest)
  if (
    manifest_hashes(payload, manifest, version.storage_version)
    != (plan.old_manifest_sha256, plan.native_manifest_sha256)
    or version.storage_version != plan.native_storage_version
    or evidence_hash(transfers) != plan.transfer_sha256
  ):
    raise ValueError("NATIVE_MIGRATION_FILES_CHANGED")
  async with asyncio.timeout(15), engine.begin() as db:
    await db.execute(text("SET LOCAL lock_timeout='3s'"))
    await db.execute(text("SET LOCAL statement_timeout='10s'"))
    await db.execute(text("LOCK TABLE market_data_worker_lease IN SHARE MODE"))
    if await db.scalar(
      text(
        "SELECT EXISTS(SELECT 1 FROM market_data_worker_lease WHERE expires_at>clock_timestamp())"
      )
    ):
      raise ValueError("NATIVE_MIGRATION_WORKER_ACTIVE")
    await db.execute(
      text(
        "LOCK TABLE market_data_request, market_data_transfer IN SHARE ROW EXCLUSIVE MODE"
      )
    )
    current, latest_transfers = await _snapshot(db, plan.request_id)
    if evidence_hash(latest_transfers) != plan.transfer_sha256:
      raise ValueError("NATIVE_MIGRATION_FILES_CHANGED")
    progress = current.get("ingestion_progress") or {}
    prior = progress.get("legacy_storage_migration") or {}
    if (
      prior.get("source_row_sha256") == plan.source_row_sha256
      and prior.get("transfer_sha256") == plan.transfer_sha256
      and prior.get("native_storage_version") == plan.native_storage_version
      and progress.get("manifest_hash") == plan.native_manifest_sha256
    ):
      return {"status": "already_migrated", "request_id": plan.request_id}
    if evidence_hash(current) != plan.source_row_sha256:
      raise ValueError("NATIVE_MIGRATION_SOURCE_CHANGED")
    state = migrated_progress(current, plan, datetime.now(timezone.utc))
    await db.execute(
      text("""
      UPDATE market_data_request SET ingestion_progress=CAST(:progress AS jsonb),
        status='BLOCKED',processing_error=:reason,updated_at=clock_timestamp()
      WHERE request_id=:id
    """),
      {
        "id": plan.request_id,
        "progress": json.dumps(state),
        "reason": state["reason_code"],
      },
    )
    return {
      "status": "migrated",
      "request_id": plan.request_id,
      "reason_code": state["reason_code"],
    }
