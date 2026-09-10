"""Producer-side v1-to-v2 adoption under an inactive Worker and frozen catalogs."""

import asyncio
import json
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field
from quantx_contracts.data_exchange import HistoryPartitionRequest
from sqlalchemy import text

from .legacy_delivery_proof import upgrade_legacy_delivery
from .market_data_ingestion_progress import evidence_hash


class LegacyExportPlan(BaseModel):
  model_config = ConfigDict(extra="forbid")
  delivery_id: str = Field(min_length=1, max_length=64)
  export_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  manifest: dict


async def _snapshot(db, identity):
  # Referencing the new column fails before file work if the migration is missing.
  row = (
    await db.execute(
      text(
        "SELECT to_jsonb(e),legacy_manifest FROM development_data_export e WHERE id=:id"
      ),
      {"id": identity},
    )
  ).one_or_none()
  if row is None:
    raise ValueError("LEGACY_EXPORT_MISSING")
  export = row[0]
  source = await db.scalar(
    text("SELECT to_jsonb(r) FROM market_data_request r WHERE request_id=:id"),
    {"id": export.get("source_request_id")},
  )
  if source is None:
    raise ValueError("LEGACY_EXPORT_SOURCE_MISSING")
  return export, source


async def _candidate(export, source, receipt):
  if export.get("source_request_id") != receipt.get("source_request_id"):
    raise ValueError("LEGACY_EXPORT_SOURCE_CONFLICT")
  material = dict(source)
  material["created_at"] = datetime.fromisoformat(material["created_at"])
  return await upgrade_legacy_delivery(
    receipt,
    HistoryPartitionRequest.model_validate(export["request"]),
    material,
    delivery_id=export["id"],
  )


def _eligible(export):
  if not (
    export["state"] == "READY"
    or (
      export["state"] == "BLOCKED"
      and export.get("error") == "SOURCE_PROVENANCE_MIGRATION_REQUIRED"
    )
  ):
    raise ValueError("LEGACY_EXPORT_STATE_INVALID")
  if export.get("legacy_manifest") is not None:
    raise ValueError("LEGACY_EXPORT_ALREADY_MIGRATED")


async def plan_export_migration(engine, identity):
  async with engine.connect() as db, db.begin():
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    export, source = await _snapshot(db, identity)
  _eligible(export)
  candidate = await _candidate(export, source, export["manifest"])
  return LegacyExportPlan(
    delivery_id=identity,
    export_sha256=evidence_hash(export),
    source_sha256=evidence_hash(source),
    receipt_sha256=candidate["previous_receipt_sha256"],
    manifest=candidate["manifest"],
  )


async def apply_export_migration(engine, plan):
  plan = LegacyExportPlan.model_validate(plan.model_dump())
  async with engine.connect() as db, db.begin():
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    export, source = await _snapshot(db, plan.delivery_id)
  archived = export.get("legacy_manifest")
  receipt = archived["previous_export"]["manifest"] if archived else export["manifest"]
  candidate = await _candidate(export, source, receipt)
  if (
    candidate["manifest"] != plan.manifest
    or candidate["previous_receipt_sha256"] != plan.receipt_sha256
    or evidence_hash(source) != plan.source_sha256
  ):
    raise ValueError("LEGACY_EXPORT_EVIDENCE_CHANGED")
  async with asyncio.timeout(15), engine.begin() as db:
    await db.execute(text("SET LOCAL lock_timeout='3s'"))
    await db.execute(text("SET LOCAL statement_timeout='10s'"))
    await db.execute(text("LOCK TABLE market_data_worker_lease IN SHARE MODE"))
    if await db.scalar(
      text(
        "SELECT EXISTS(SELECT 1 FROM market_data_worker_lease WHERE expires_at>clock_timestamp())"
      )
    ):
      raise ValueError("LEGACY_EXPORT_WORKER_ACTIVE")
    await db.execute(text("LOCK TABLE market_data_request IN SHARE MODE"))
    await db.execute(
      text("LOCK TABLE development_data_export IN SHARE ROW EXCLUSIVE MODE")
    )
    current, current_source = await _snapshot(db, plan.delivery_id)
    if evidence_hash(current_source) != plan.source_sha256:
      raise ValueError("LEGACY_EXPORT_SOURCE_CHANGED")
    prior = current.get("legacy_manifest")
    if prior is not None:
      if (
        prior.get("export_sha256") == plan.export_sha256
        and prior.get("source_sha256") == plan.source_sha256
        and evidence_hash(prior["previous_export"]["manifest"]) == plan.receipt_sha256
        and current["manifest"] == plan.manifest
      ):
        return {"status": "already_migrated", "delivery_id": plan.delivery_id}
      raise ValueError("LEGACY_EXPORT_ARCHIVE_CONFLICT")
    _eligible(current)
    if evidence_hash(current) != plan.export_sha256:
      raise ValueError("LEGACY_EXPORT_CHANGED")
    audit = {
      "previous_export": current,
      "export_sha256": plan.export_sha256,
      "source_sha256": plan.source_sha256,
      "at": datetime.now(timezone.utc).isoformat(),
    }
    await db.execute(
      text("""
      UPDATE development_data_export SET legacy_manifest=CAST(:audit AS jsonb),
        manifest=CAST(:manifest AS json),state='READY',error=NULL,updated_at=clock_timestamp()
      WHERE id=:id
    """),
      {
        "id": plan.delivery_id,
        "audit": json.dumps(audit),
        "manifest": json.dumps(plan.manifest),
      },
    )
    return {
      "status": "migrated",
      "delivery_id": plan.delivery_id,
      "data_version": plan.manifest["data_version"],
    }
