"""Adopt an authenticated producer receipt while retaining local legacy budgets."""

import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field
from quantx_application.market_data.ingestion import transition
from quantx_contracts.data_exchange import HistoryPartitionRequest
from sqlalchemy import text

from .development_bar_publication import _binding
from .development_delivery_manifest import validate_delivery_manifest
from .development_history_import import ImportedTransfer
from .immutable_bar_storage import prepare_immutable_bar_version
from .legacy_delivery_proof import validate_legacy_receipt
from .market_data_ingestion_progress import evidence_hash

REASON = "LOCAL_RECEIPT_MIGRATED_REQUIRES_RECOVERY"


class LegacyImportPlan(BaseModel):
  model_config = ConfigDict(extra="forbid")
  delivery_id: str = Field(pattern=r"^[0-9a-f]{64}$")
  snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  manifest: dict
  storage_version: str = Field(pattern=r"^[0-9a-f]{64}$")
  progress_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


async def _snapshot(db, identity):
  selected = (
    await db.execute(
      text(
        "SELECT to_jsonb(e),legacy_manifest FROM development_data_export e WHERE id=:id"
      ),
      {"id": identity},
    )
  ).one_or_none()
  if selected is None:
    raise ValueError("LEGACY_IMPORT_MISSING")
  result = {"export": selected[0]}
  for name, table in (
    ("ingestion", "development_data_ingestion"),
    ("budget", "development_data_download_budget"),
    ("version", "development_data_bar_version"),
  ):
    result[name] = await db.scalar(
      text(f"SELECT to_jsonb(t) FROM {table} t WHERE delivery_id=:id"), {"id": identity}
    )
  request = HistoryPartitionRequest.model_validate(result["export"]["request"])
  partition_owner = await db.scalar(
    text(
      "SELECT delivery_id FROM development_data_bar_version WHERE stock_code=:code AND period=:period AND trading_date=:day"
    ),
    {"code": request.instrument, "period": request.period, "day": request.trading_date},
  )
  if partition_owner is not None and partition_owner != identity:
    raise ValueError("LEGACY_IMPORT_STORAGE_BINDING_CONFLICT")
  return result


def _eligible(snapshot):
  export = snapshot["export"]
  if export["state"] not in (
    "LOCAL_VERIFIED",
    "WAITING_LOCAL_INGESTION",
    "WAITING_LOCAL_PROOF",
    "BLOCKED",
  ):
    raise ValueError("LEGACY_IMPORT_STATE_INVALID")
  budget = snapshot["budget"]
  if not isinstance(budget, dict) or any(
    type(budget.get(key)) is not int or budget[key] < 0
    for key in ("attempts", "reserved_bytes", "reserved_seconds", "proof_attempts")
  ):
    raise ValueError("LEGACY_IMPORT_BUDGET_EVIDENCE_REQUIRED")
  state = (snapshot["ingestion"] or {}).get("progress")
  if (
    not isinstance(state, dict)
    or type(state.get("version")) is not int
    or state["version"] != 1
    or state.get("phase") not in ("VALIDATE", "WRITE", "READBACK", "VERIFIED")
    or any(
      type(state.get(k)) is not int or state[k] < minimum
      for k, minimum in (("attempt", 1), ("executions", 0))
    )
    or not isinstance(state.get("checkpoints"), dict)
    or not isinstance(state.get("failures"), list)
  ):
    raise ValueError("LEGACY_IMPORT_BUDGET_EVIDENCE_REQUIRED")
  try:
    datetime.fromisoformat(state["stage_started_at"])
  except (KeyError, TypeError, ValueError):
    raise ValueError("LEGACY_IMPORT_BUDGET_EVIDENCE_REQUIRED") from None


async def _prepare(snapshot, remote):
  export = snapshot["export"]
  archive = export.get("legacy_manifest")
  original = (
    archive["previous_snapshot"]["export"]["manifest"]
    if archive
    else export["manifest"]
  )
  legacy = validate_legacy_receipt(original)
  request = HistoryPartitionRequest.model_validate(export["request"])
  if (
    remote.get("id") != export["id"]
    or remote.get("state") != "READY"
    or remote.get("request") != request.model_dump(mode="json")
  ):
    raise ValueError("LEGACY_IMPORT_PRODUCER_SCOPE_INVALID")
  manifest = remote.get("manifest")
  validate_delivery_manifest(manifest, request)
  if any(
    manifest[k] != legacy[k]
    for k in ("payload", "chunks", "reference", "source_request_id", "coverage", "rows")
  ):
    raise ValueError("LEGACY_IMPORT_ORIGINAL_CONTENT_CHANGED")
  version = await prepare_immutable_bar_version(
    ImportedTransfer(manifest), export["id"]
  )
  binding = _binding({"request": export["request"], "manifest": manifest}, version)
  progress = (snapshot["ingestion"] or {}).get("progress")
  if not isinstance(progress, dict):
    raise ValueError("LEGACY_IMPORT_BUDGET_EVIDENCE_REQUIRED")
  expected = evidence_hash(
    {
      "storage_version": version.storage_version,
      "manifest": json.loads(version.manifest_json),
    }
  )
  allowed = {expected}
  if not archive:
    allowed.add(
      evidence_hash({"payload": legacy["payload"], "chunks": legacy["chunks"]})
    )
  unfrozen = (
    not archive
    and progress.get("manifest_hash") is None
    and progress.get("phase") == "VALIDATE"
    and progress.get("checkpoints") == {}
    and progress.get("write_result") is None
  )
  if progress.get("manifest_hash") not in allowed and not unfrozen:
    raise ValueError("LEGACY_IMPORT_CHECKPOINT_CONFLICT")
  old_version = snapshot["version"]
  if old_version:
    expected_source = manifest["data_version"] if archive else legacy["data_version"]
    if old_version["source_version"] != expected_source or any(
      old_version[k] != (v.isoformat() if hasattr(v, "isoformat") else v)
      for k, v in binding.items()
      if k != "source_version"
    ):
      raise ValueError("LEGACY_IMPORT_STORAGE_BINDING_CONFLICT")
  return manifest, version


async def plan_import_migration(engine, identity, remote):
  async with engine.connect() as db, db.begin():
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    snapshot = await _snapshot(db, identity)
  _eligible(snapshot)
  if snapshot["export"].get("legacy_manifest") is not None:
    raise ValueError("LEGACY_IMPORT_ALREADY_MIGRATED")
  manifest, version = await _prepare(snapshot, remote)
  return LegacyImportPlan(
    delivery_id=identity,
    snapshot_sha256=evidence_hash(snapshot),
    manifest=manifest,
    storage_version=version.storage_version,
    progress_manifest_sha256=evidence_hash(
      {
        "storage_version": version.storage_version,
        "manifest": json.loads(version.manifest_json),
      }
    ),
  )


async def _lock(db, request):
  await db.execute(text("SET LOCAL lock_timeout='3s'"))
  await db.execute(text("SET LOCAL statement_timeout='10s'"))
  key = int.from_bytes(
    hashlib.sha256(request.model_dump_json().encode()).digest()[:8], "big", signed=True
  )
  if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
    raise ValueError("LEGACY_IMPORT_PARTITION_ACTIVE")
  await db.execute(text("LOCK TABLE market_data_worker_lease IN SHARE MODE"))
  if await db.scalar(
    text(
      "SELECT EXISTS(SELECT 1 FROM market_data_worker_lease WHERE expires_at>clock_timestamp())"
    )
  ):
    raise ValueError("LEGACY_IMPORT_WORKER_ACTIVE")
  await db.execute(
    text(
      "LOCK TABLE development_data_export,development_data_ingestion,development_data_download_budget,development_data_bar_version IN SHARE ROW EXCLUSIVE MODE"
    )
  )


async def apply_import_migration(engine, plan, remote):
  plan = LegacyImportPlan.model_validate(plan.model_dump())
  async with engine.connect() as db, db.begin():
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    snapshot = await _snapshot(db, plan.delivery_id)
  manifest, version = await _prepare(snapshot, remote)
  if (
    manifest != plan.manifest
    or version.storage_version != plan.storage_version
    or evidence_hash(
      {
        "storage_version": version.storage_version,
        "manifest": json.loads(version.manifest_json),
      }
    )
    != plan.progress_manifest_sha256
  ):
    raise ValueError("LEGACY_IMPORT_PLAN_CHANGED")
  async with asyncio.timeout(15), engine.begin() as db:
    await _lock(
      db, HistoryPartitionRequest.model_validate(snapshot["export"]["request"])
    )
    current = await _snapshot(db, plan.delivery_id)
    archive = current["export"].get("legacy_manifest")
    if archive:
      visible = {
        k: v
        for k, v in current["export"]["manifest"].items()
        if k != "local_verification"
      }
      if (
        archive.get("snapshot_sha256") == plan.snapshot_sha256
        and visible == plan.manifest
      ):
        return {"status": "already_migrated", "delivery_id": plan.delivery_id}
      raise ValueError("LEGACY_IMPORT_ARCHIVE_CONFLICT")
    _eligible(current)
    if evidence_hash(current) != plan.snapshot_sha256:
      raise ValueError("LEGACY_IMPORT_SNAPSHOT_CHANGED")
    now = datetime.now(timezone.utc)
    audit = {
      "previous_snapshot": current,
      "snapshot_sha256": plan.snapshot_sha256,
      "at": now.isoformat(),
    }
    progress = deepcopy(current["ingestion"]["progress"])
    progress.update(
      phase="WRITE",
      manifest_hash=plan.progress_manifest_sha256,
      checkpoints={},
      write_result=None,
      blocked=True,
      reason_code=REASON,
      next_retry_at=None,
      last_progress_at=now.isoformat(),
    )
    await db.execute(
      text(
        "UPDATE development_data_ingestion SET progress=CAST(:progress AS jsonb),updated_at=clock_timestamp() WHERE delivery_id=:id"
      ),
      {"id": plan.delivery_id, "progress": json.dumps(progress)},
    )
    if current["version"]:
      await db.execute(
        text(
          "UPDATE development_data_bar_version SET source_version=:version,proof=NULL,verified_at=NULL WHERE delivery_id=:id"
        ),
        {"id": plan.delivery_id, "version": plan.manifest["data_version"]},
      )
    await db.execute(
      text(
        "UPDATE development_data_export SET manifest=CAST(:manifest AS json),legacy_manifest=CAST(:audit AS jsonb),state='BLOCKED',error=:reason,updated_at=clock_timestamp() WHERE id=:id"
      ),
      {
        "id": plan.delivery_id,
        "manifest": json.dumps(plan.manifest),
        "audit": json.dumps(audit),
        "reason": REASON,
      },
    )
    return {
      "status": "migrated",
      "delivery_id": plan.delivery_id,
      "reason_code": REASON,
    }


async def recover_import_migration(engine, identity, reason):
  if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 256:
    raise ValueError("LEGACY_IMPORT_RECOVERY_REASON_REQUIRED")
  async with asyncio.timeout(15), engine.begin() as db:
    request = await db.scalar(
      text("SELECT request FROM development_data_export WHERE id=:id"), {"id": identity}
    )
    request = HistoryPartitionRequest.model_validate(request)
    await _lock(db, request)
    snapshot = await _snapshot(db, identity)
    if HistoryPartitionRequest.model_validate(snapshot["export"]["request"]) != request:
      raise ValueError("LEGACY_IMPORT_SNAPSHOT_CHANGED")
    export = snapshot["export"]
    if (
      not export.get("legacy_manifest")
      or export["state"] != "BLOCKED"
      or export.get("error") != REASON
    ):
      raise ValueError("LEGACY_IMPORT_NOT_WAITING_RECOVERY")
    _eligible(snapshot)
    state = snapshot["ingestion"]["progress"]
    if (
      state["phase"] != "WRITE"
      or not state["blocked"]
      or state["reason_code"] != REASON
    ):
      raise ValueError("LEGACY_IMPORT_RECOVERY_STATE_INVALID")
    progress = transition(
      snapshot["ingestion"]["progress"],
      "resume",
      {"reason": reason},
      datetime.now(timezone.utc),
    )
    await db.execute(
      text(
        "UPDATE development_data_ingestion SET progress=CAST(:progress AS jsonb),updated_at=clock_timestamp() WHERE delivery_id=:id"
      ),
      {"id": identity, "progress": json.dumps(progress)},
    )
    await db.execute(
      text(
        "UPDATE development_data_export SET state='WAITING_LOCAL_INGESTION',error=NULL,updated_at=clock_timestamp() WHERE id=:id"
      ),
      {"id": identity},
    )
    return {
      "status": "resumed",
      "delivery_id": identity,
      "attempt": progress["attempt"],
    }
