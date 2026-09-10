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
  from .local_history_reader import HistoryReadInvalid

  rows = (
    (
      await db.execute(
        text("""
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
    ORDER BY created_at DESC,request_id DESC LIMIT 2
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
  if not rows:
    return None
  if (
    len(json.dumps([dict(row) for row in rows], default=str).encode()) > 4 * 1024 * 1024
  ):
    raise HistoryReadInvalid("NATIVE_VERSION_DIRECTORY_CAPACITY")
  row = rows[0]
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
  if (
    len(rows) == 2
    and rows[1]["created_at"] == row["created_at"]
    and rows[1]["version"] != row["version"]
  ):
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
