"""Resolve a completed native content version; never infer a version from Influx."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text

from .market_data_ingestion_progress import evidence_hash


@dataclass(frozen=True)
class NativeBarPublication:
  storage_version: str
  source_request_id: str
  full_session: bool
  content_sha256: str
  records_verified: int
  fields_verified: int
  source_created_at: datetime


async def resolve_native_bar_version(db, request):
  rows = (
    (
      await db.execute(
        text("""
    WITH candidates AS (
    SELECT request_id,created_at,request_payload,
      ingestion_result->>'native_storage_version' AS version,
      ingestion_result->'content_verification' AS content,
      ingestion_result->>'records_verified' AS records,
      ingestion_result->'persistence_verification'->>'records_verified' AS persisted,
      ingestion_result->'persistence_verification'->>'status' AS status
    FROM market_data_request r WHERE status='COMPLETED'
      AND request_payload->>'operation'='bars'
      AND ingestion_result->>'native_storage_version' IS NOT NULL
      AND (request_payload->'stock_list')::jsonb @> CAST(:codes AS jsonb)
      AND (request_payload->'periods')::jsonb @> CAST(:periods AS jsonb)
      AND request_payload->>'start_time' ~ '^[0-9]{8}$'
      AND request_payload->>'end_time' ~ '^[0-9]{8}$'
      AND request_payload->>'start_time'<=:day AND request_payload->>'end_time'>=:day
      AND EXISTS(SELECT 1 FROM jsonb_array_elements(CASE
        WHEN jsonb_typeof(r.ingestion_result::jsonb->'day_coverage')='array'
        THEN r.ingestion_result::jsonb->'day_coverage' ELSE '[]'::jsonb END) AS coverage
        WHERE coverage->>'instrument_code'=:instrument AND coverage->>'period'=:period
          AND REPLACE(coverage->>'trading_date','-','')=:day
          AND coverage->>'point_count' ~ '^[1-9][0-9]*$')
    ), newest AS (
      SELECT * FROM candidates WHERE created_at=(SELECT MAX(created_at) FROM candidates)
    )
    SELECT *, (SELECT COUNT(DISTINCT version)>1 FROM newest) AS ambiguous
    FROM newest ORDER BY request_id DESC LIMIT 1
  """),
        {
          "codes": json.dumps([request.instrument]),
          "periods": json.dumps([request.period]),
          "day": request.trading_date.strftime("%Y%m%d"),
          "instrument": request.instrument,
          "period": request.period,
        },
      )
    )
    .mappings()
    .all()
  )
  return _publication_from_rows(rows, request)


def _publication_from_rows(rows, request):
  from .local_history_reader import HistoryReadInvalid

  if not rows:
    return None
  if (
    len(json.dumps([dict(row) for row in rows], default=str).encode()) > 4 * 1024 * 1024
  ):
    raise HistoryReadInvalid("NATIVE_VERSION_DIRECTORY_CAPACITY")
  row = rows[0]
  proof, count, source_hash = _validated_native_row(row)
  if row["ambiguous"]:
    raise HistoryReadInvalid("NATIVE_VERSION_ORDER_AMBIGUOUS")
  cutoff = (request.bounds()[0] + timedelta(hours=15, minutes=1)).replace(tzinfo=None)
  return NativeBarPublication(
    row["version"],
    str(row["request_id"]),
    row["created_at"] >= cutoff and row["request_payload"].get("download") is True,
    source_hash,
    count,
    proof["fields_verified"],
    row["created_at"],
  )


async def resolve_native_daily_versions(db, request):
  from zoneinfo import ZoneInfo

  from quantx_contracts.market_data_service import HistoryRead

  from .local_history_reader import HistoryReadInvalid

  start = request.start.astimezone(ZoneInfo("Asia/Shanghai")).date()
  end = request.end.astimezone(ZoneInfo("Asia/Shanghai")).date()
  limit = len(request.instruments) * 62
  result = await db.stream(
    text("""
    WITH candidates AS (
      SELECT r.request_id,r.created_at,r.request_payload,
        r.ingestion_result->>'native_storage_version' AS version,
        r.ingestion_result->'content_verification' AS content,
        r.ingestion_result->>'records_verified' AS records,
        r.ingestion_result->'persistence_verification'->>'records_verified' AS persisted,
        r.ingestion_result->'persistence_verification'->>'status' AS status,
        c->>'instrument_code' AS instrument,replace(c->>'trading_date','-','') AS day
      FROM market_data_request r CROSS JOIN LATERAL jsonb_array_elements(CASE
        WHEN jsonb_typeof(r.ingestion_result::jsonb->'day_coverage')='array'
        THEN r.ingestion_result::jsonb->'day_coverage' ELSE '[]'::jsonb END) c
      WHERE r.status='COMPLETED' AND r.request_payload->>'operation'='bars'
        AND r.ingestion_result->>'native_storage_version' IS NOT NULL
        AND c->>'instrument_code'=ANY(:codes) AND c->>'period'='1d'
        AND c->>'point_count' ~ '^[1-9][0-9]*$'
        AND replace(c->>'trading_date','-','') BETWEEN :start AND :end
        AND r.request_payload->>'start_time' ~ '^[0-9]{8}$'
        AND r.request_payload->>'end_time' ~ '^[0-9]{8}$'
        AND r.request_payload->>'start_time'<=replace(c->>'trading_date','-','')
        AND r.request_payload->>'end_time'>=replace(c->>'trading_date','-','')
        AND (r.request_payload->'periods')::jsonb @> '["1d"]'::jsonb
        AND (r.request_payload->'stock_list')::jsonb @> jsonb_build_array(c->>'instrument_code')
    ), ranked AS (
      SELECT *,row_number() OVER(PARTITION BY instrument,day ORDER BY created_at DESC,request_id DESC) AS ordinal
      FROM candidates
    ) SELECT chosen.*, EXISTS(
      SELECT 1 FROM candidates peer
      WHERE peer.instrument=chosen.instrument AND peer.day=chosen.day
        AND peer.created_at=chosen.created_at AND peer.version<>chosen.version
    ) AS ambiguous
    FROM ranked chosen WHERE ordinal=1 ORDER BY instrument,day LIMIT :limit
  """),
    {
      "codes": request.instruments,
      "start": start.strftime("%Y%m%d"),
      "end": end.strftime("%Y%m%d"),
      "limit": limit + 1,
    },
    execution_options={"yield_per": 1, "max_row_buffer": 1},
  )
  rows, bytes_seen = [], 0
  try:
    async for row in result.mappings():
      bytes_seen += len(json.dumps(dict(row), default=str).encode())
      if len(rows) >= limit or bytes_seen > 4 * 1024 * 1024:
        raise HistoryReadInvalid("NATIVE_VERSION_DIRECTORY_CAPACITY")
      rows.append(row)
  finally:
    await result.close()
  partitions = {}
  for row in rows:
    try:
      day = datetime.strptime(row["day"], "%Y%m%d").date()
    except ValueError:
      raise HistoryReadInvalid("NATIVE_VERSION_PROOF_INVALID") from None
    partitions.setdefault((row["instrument"], day), []).append(row)
  return {
    key: _publication_from_rows(
      candidates, HistoryRead(instrument=key[0], period="1d", trading_date=key[1])
    ).storage_version
    for key, candidates in partitions.items()
  }


def _validated_native_row(row):
  from .local_history_reader import HistoryReadInvalid

  proof = row["content"]
  if not isinstance(proof, dict):
    raise HistoryReadInvalid("NATIVE_VERSION_PROOF_INVALID")
  count = proof.get("records_verified")
  source_hash = proof.get("source_sha256")
  if (
    row["status"] != "verified"
    or type(proof.get("schema_version")) is not int
    or proof.get("schema_version") != 1
    or type(count) is not int
    or count <= 0
    or str(count) != row["records"]
    or str(count) != row["persisted"]
    or type(proof.get("fields_verified")) is not int
    or proof["fields_verified"] <= 0
    or not isinstance(source_hash, str)
    or re.fullmatch(r"[0-9a-f]{64}", source_hash) is None
    or source_hash != proof.get("persisted_sha256")
    or proof.get("storage_version") != row["version"]
    or row["version"]
    != evidence_hash(
      {
        "storage_format": "native-bars-v1",
        "payload": row["request_payload"],
        "content_sha256": source_hash,
      }
    )
  ):
    raise HistoryReadInvalid("NATIVE_VERSION_PROOF_INVALID")
  return proof, count, source_hash


def validate_native_bar_receipt(payload, audit):
  if not isinstance(payload, dict) or not isinstance(audit, dict):
    raise ValueError("NATIVE_VERSION_PROOF_INVALID")
  if not audit.get("native_storage_version"):
    raise ValueError("NATIVE_STORAGE_VERSION_MIGRATION_REQUIRED")
  if not isinstance(audit.get("persistence_verification"), dict):
    raise ValueError("NATIVE_VERSION_PROOF_INVALID")
  _validated_native_row(
    {
      "request_payload": payload,
      "version": audit.get("native_storage_version"),
      "content": audit.get("content_verification"),
      "records": str(audit.get("records_verified")),
      "persisted": str(
        audit.get("persistence_verification", {}).get("records_verified")
      ),
      "status": audit.get("persistence_verification", {}).get("status"),
    }
  )
  return audit["native_storage_version"]
