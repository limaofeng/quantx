"""Fenced publication of one immutable version for a fixed development delivery.

Callers own the transaction. Binding precedes external writes; publication follows
actual version readback and commits with reference data, progress and the receipt.
No last-arrival-wins or implicit replacement of a fixed delivery is supported.
"""

import json
import re
from datetime import date

from quantx_contracts.data_exchange import HistoryPartitionRequest
from sqlalchemy import text

from .development_delivery_manifest import validate_delivery_manifest
from .immutable_bar_storage import ImmutableBarVersion
from .market_data_ingestion_progress import evidence_hash


class DeliveryVersionConflict(RuntimeError):
  pass


async def _locked_delivery(db, delivery_id, claim_token, owner):
  if owner is None:
    raise RuntimeError("storage publication requires an owner")
  await owner._guard_ingestion_owner(db, lock=False)
  row = (
    (
      await db.execute(
        text("""
    SELECT request,manifest,state FROM development_data_export WHERE id=:id FOR UPDATE
  """),
        {"id": delivery_id},
      )
    )
    .mappings()
    .one()
  )
  state = await db.scalar(
    text("""
    SELECT progress FROM development_data_ingestion
    WHERE delivery_id=:id AND claim_token=:token FOR UPDATE
  """),
    {"id": delivery_id, "token": claim_token},
  )
  if (
    not state
    or state["blocked"]
    or state["phase"] not in {"WRITE", "READBACK"}
    or row["state"] in {"BLOCKED", "EXPIRED", "INCOMPLETE", "LOCAL_VERIFIED"}
  ):
    raise RuntimeError("storage publication has no active ingestion claim")
  return row, state


def _binding(row, version):
  if not isinstance(version, ImmutableBarVersion) or not isinstance(
    row["manifest"], dict
  ):
    raise DeliveryVersionConflict("invalid prepared storage version")
  request = HistoryPartitionRequest.model_validate(row["request"])
  manifest = {
    key: value for key, value in row["manifest"].items() if key != "local_verification"
  }
  validate_delivery_manifest(manifest, request)
  prepared = json.loads(version.manifest_json)
  chunks = [
    {k: v for k, v in item.items() if k != "storage_reference"}
    for item in prepared["chunks"]
  ]
  if (
    prepared["payload"] != manifest["payload"]
    or chunks != manifest["chunks"]
    or version.code != request.instrument
    or version.period != request.period
    or version.trading_date != request.trading_date.isoformat()
    or type(version.records) is not int
    or version.records != manifest["rows"]
    or not isinstance(version.content_sha256, str)
    or re.fullmatch(r"[0-9a-f]{64}", version.content_sha256) is None
  ):
    raise DeliveryVersionConflict(
      "prepared storage version does not match fixed delivery"
    )
  expected = evidence_hash(
    {
      "storage_format": 1,
      "code": version.code,
      "period": version.period,
      "trading_date": version.trading_date,
      "content_sha256": version.content_sha256,
    }
  )
  if version.storage_version != expected:
    raise DeliveryVersionConflict("storage version digest is invalid")
  return {
    "stock_code": version.code,
    "period": version.period,
    "trading_date": date.fromisoformat(version.trading_date),
    "source_version": manifest["data_version"],
    "storage_version": version.storage_version,
    "content_sha256": version.content_sha256,
    "records": version.records,
  }


def _same_binding(row, binding):
  if any(row[key] != value for key, value in binding.items()):
    raise DeliveryVersionConflict("fixed storage version cannot be replaced")


async def bind_delivery_bar_version(db, delivery_id, version, *, claim_token, owner):
  row, state = await _locked_delivery(db, delivery_id, claim_token, owner)
  binding = _binding(row, version)
  existing = (
    (
      await db.execute(
        text(
          "SELECT * FROM development_data_bar_version WHERE delivery_id=:id FOR UPDATE"
        ),
        {"id": delivery_id},
      )
    )
    .mappings()
    .one_or_none()
  )
  if existing is None:
    if state["phase"] != "WRITE":
      raise DeliveryVersionConflict("storage version must be bound before READBACK")
    await db.execute(
      text("""
      INSERT INTO development_data_bar_version(delivery_id,stock_code,period,trading_date,
        source_version,storage_version,content_sha256,records)
      VALUES (:id,:stock_code,:period,:trading_date,:source_version,:storage_version,:content_sha256,:records)
    """),
      {"id": delivery_id, **binding},
    )
  else:
    _same_binding(existing, binding)
  await owner._guard_ingestion_owner(db)
  return binding


async def publish_delivery_bar_version(
  db, delivery_id, version, proof, *, claim_token, owner
):
  row, state = await _locked_delivery(db, delivery_id, claim_token, owner)
  if state["phase"] != "READBACK":
    raise RuntimeError("storage publication requires READBACK")
  binding = _binding(row, version)
  existing = (
    (
      await db.execute(
        text(
          "SELECT * FROM development_data_bar_version WHERE delivery_id=:id FOR UPDATE"
        ),
        {"id": delivery_id},
      )
    )
    .mappings()
    .one_or_none()
  )
  if existing is None:
    raise DeliveryVersionConflict("storage version was not bound before writing")
  _same_binding(existing, binding)
  expected = {
    "schema_version": 1,
    "records_verified": version.records,
    "source_sha256": version.content_sha256,
    "persisted_sha256": version.content_sha256,
    "storage_version": version.storage_version,
    "code": version.code,
    "period": version.period,
    "trading_date": version.trading_date,
  }
  if (
    not isinstance(proof, dict)
    or set(proof) != {*expected, "fields_verified"}
    or any(proof[key] != value for key, value in expected.items())
    or any(
      type(proof[key]) is not int
      for key in ("schema_version", "records_verified", "fields_verified")
    )
    or proof["fields_verified"] <= 0
  ):
    raise DeliveryVersionConflict("storage publication proof is incomplete")
  if existing["proof"] is not None and existing["proof"] != proof:
    raise DeliveryVersionConflict("storage publication proof changed")
  await db.execute(
    text("""
    UPDATE development_data_bar_version SET proof=CAST(:proof AS JSONB),
      verified_at=COALESCE(verified_at,clock_timestamp()) WHERE delivery_id=:id
  """),
    {"id": delivery_id, "proof": json.dumps(proof, allow_nan=False)},
  )
  await owner._guard_ingestion_owner(db)


async def check_delivery_bar_publication(db, delivery_id, version, proof, *, owner):
  """Check an existing receipt without publishing or changing its source intent."""
  await owner._guard_ingestion_owner(db, lock=False)
  row = (
    (
      await db.execute(
        text(
          "SELECT request,manifest,state FROM development_data_export WHERE id=:id FOR UPDATE"
        ),
        {"id": delivery_id},
      )
    )
    .mappings()
    .one_or_none()
  )
  if (
    row is None
    or not isinstance(row["manifest"], dict)
    or not isinstance(row["manifest"].get("local_verification"), dict)
  ):
    raise DeliveryVersionConflict("existing storage receipt is missing")
  phase = await db.scalar(
    text(
      "SELECT progress->>'phase' FROM development_data_ingestion WHERE delivery_id=:id"
    ),
    {"id": delivery_id},
  )
  existing = (
    (
      await db.execute(
        text(
          "SELECT * FROM development_data_bar_version WHERE delivery_id=:id FOR UPDATE"
        ),
        {"id": delivery_id},
      )
    )
    .mappings()
    .one_or_none()
  )
  if (
    row["state"] not in {"LOCAL_VERIFIED", "WAITING_LOCAL_PROOF"}
    or phase != "VERIFIED"
    or existing is None
    or not isinstance(proof, dict)
    or existing["verified_at"] is None
    or existing["proof"] != proof
    or row["manifest"].get("local_verification", {}).get("immutable_storage") != proof
  ):
    raise DeliveryVersionConflict("existing storage publication proof is invalid")
  _same_binding(existing, _binding(row, version))
  return row["manifest"]


_VISIBLE_VERSION_QUERY = """
    SELECT v.* FROM development_data_bar_version v
    JOIN development_data_export e ON e.id=v.delivery_id
    JOIN development_data_ingestion i ON i.delivery_id=v.delivery_id
    WHERE e.state='LOCAL_VERIFIED' AND i.progress->>'phase'='VERIFIED'
      AND v.proof IS NOT NULL AND v.source_version=e.manifest->>'data_version'
      AND e.request->>'instrument'=v.stock_code AND e.request->>'period'=v.period
      AND e.request->>'trading_date'=to_char(v.trading_date,'YYYY-MM-DD')
      AND CAST(e.manifest->'local_verification'->'immutable_storage' AS JSONB)=v.proof
"""


async def resolve_published_bar_version(db, request: HistoryPartitionRequest):
  """Only the exact version in a committed local receipt is eligible for reads."""
  row = (
    (
      await db.execute(
        text(
          _VISIBLE_VERSION_QUERY
          + " AND v.stock_code=:code AND v.period=:period AND v.trading_date=:day"
        ),
        {
          "code": request.instrument,
          "period": request.period,
          "day": request.trading_date,
        },
      )
    )
    .mappings()
    .one_or_none()
  )
  return dict(row) if row else None


async def resolve_published_daily_versions(db, request):
  from zoneinfo import ZoneInfo

  from quantx_contracts.daily_snapshot_read import DailySnapshotRead

  request = DailySnapshotRead.model_validate(request)
  start = request.start.astimezone(ZoneInfo("Asia/Shanghai")).date()
  end = request.end.astimezone(ZoneInfo("Asia/Shanghai")).date()
  limit = len(request.instruments) * 62
  rows = (
    (
      await db.execute(
        text(
          _VISIBLE_VERSION_QUERY
          + """
    AND v.stock_code = ANY(:codes) AND v.period='1d' AND v.trading_date BETWEEN :start AND :end
    ORDER BY v.stock_code,v.trading_date LIMIT :limit
  """
        ),
        {"codes": request.instruments, "start": start, "end": end, "limit": limit + 1},
      )
    )
    .mappings()
    .all()
  )
  if len(rows) > limit:
    raise ValueError("published daily version lookup exceeded its row budget")
  return {
    (row["stock_code"], row["trading_date"]): row["storage_version"] for row in rows
  }
