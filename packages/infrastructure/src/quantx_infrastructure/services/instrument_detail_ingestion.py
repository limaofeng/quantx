"""Lease-fenced instrument snapshots and exact source-content verification."""

import asyncio
import json

from quantx_contracts.instrument_details import (
  MAX_INSTRUMENT_DETAIL_RESULT_BYTES,
  InstrumentDetailSnapshot,
  instrument_detail_codes,
  instrument_detail_json,
)
from sqlalchemy import text

from .market_data_ingestion_progress import evidence_hash
from .market_data_transfer_ingestion import (
  MarketDataValidationError,
  _iter_transfer_chunks,
  load_uploaded_request_manifest,
)


async def ingest_instrument_details(store, request_id, *, progress):
  if progress is None:
    raise RuntimeError("instrument snapshot ingestion requires a fenced claim")
  _, payload, manifest = await load_uploaded_request_manifest(store, request_id)
  try:
    codes = instrument_detail_codes(payload)
  except ValueError as exc:
    raise MarketDataValidationError(str(exc)) from exc

  def validate_records():
    by_code = {}
    size = 0
    requested = set(codes)
    for chunk in _iter_transfer_chunks(manifest):
      for record in chunk:
        code = record.get("code")
        if not isinstance(code, str) or code not in requested or code in by_code:
          raise MarketDataValidationError("instrument_details record scope mismatch")
        try:
          encoded = instrument_detail_json(record)
        except ValueError as exc:
          raise MarketDataValidationError(str(exc)) from exc
        size += len(encoded.encode())
        if size > MAX_INSTRUMENT_DETAIL_RESULT_BYTES:
          raise MarketDataValidationError(
            "instrument_details result exceeds byte limit"
          )
        by_code[code] = encoded
    return by_code

  by_code = await asyncio.to_thread(validate_records)
  if set(by_code) != set(codes):
    raise MarketDataValidationError("instrument_details requested records unavailable")
  digest = evidence_hash(
    {
      "payload": payload,
      "chunks": [
        {key: value for key, value in item.items() if key != "storage_reference"}
        for item in manifest
      ],
    }
  )
  await progress.apply("manifest", sha256=digest)
  audit = {
    "operation": "instrument_details",
    "schema_version": 1,
    "records_received": len(codes),
    "records_saved": len(codes),
    "records_verified": len(codes),
    "manifest_sha256": digest,
    "content_sha256": evidence_hash([json.loads(by_code[code]) for code in codes]),
  }
  expected = [
    {
      "request_id": request_id,
      "code": code,
      "manifest_sha256": digest,
      "content_sha256": evidence_hash(json.loads(by_code[code])),
      "record_json": by_code[code],
      "schema_version": 1,
    }
    for code in codes
  ]

  async def verify(db):
    actual = (
      (
        await db.execute(
          text("""
      SELECT request_id,code,manifest_sha256,content_sha256,record_json,schema_version
      FROM market_data_instrument_snapshot WHERE request_id=:id ORDER BY code LIMIT :limit
    """),
          {"id": request_id, "limit": len(codes) + 1},
        )
      )
      .mappings()
      .all()
    )
    if [dict(row) for row in actual] != expected:
      raise MarketDataValidationError("instrument snapshot persistence mismatch")

  if progress.state["phase"] == "VALIDATE":
    await progress.apply("advance", phase="WRITE")
  if progress.state["phase"] == "READBACK":
    if progress.state["write_result"] != audit:
      raise MarketDataValidationError("instrument snapshot checkpoint mismatch")
    async with store.engine.connect() as connection:
      await verify(connection)
    return audit

  async def persist(db):
    await db.execute(
      text("""
      INSERT INTO market_data_instrument_snapshot
        (request_id,code,manifest_sha256,content_sha256,record_json,schema_version)
      VALUES (:request_id,:code,:manifest_sha256,:content_sha256,:record_json,:schema_version)
      ON CONFLICT (request_id,code) DO NOTHING
    """),
      expected,
    )
    await verify(db)
    return audit

  progress.state = await store.persist_market_data_reference(
    request_id,
    claim_token=progress.claim_token,
    persist=persist,
  )
  return progress.state["write_result"]


async def read_instrument_detail(engine, request_id: str, code: str):
  async with engine.connect() as connection:
    row = (
      (
        await connection.execute(
          text("""
      SELECT s.* FROM market_data_instrument_snapshot s
      JOIN market_data_request r ON r.request_id=s.request_id
      WHERE s.request_id=:id AND s.code=:code AND r.status='COMPLETED'
        AND r.ingestion_progress->>'phase'='VERIFIED'
        AND r.ingestion_result->>'manifest_sha256'=s.manifest_sha256
    """),
          {"id": request_id, "code": code},
        )
      )
      .mappings()
      .one_or_none()
    )
  if row is None:
    return None
  record = json.loads(row["record_json"])
  if (
    not isinstance(record, dict)
    or record.get("code") != code
    or instrument_detail_json(record) != row["record_json"]
    or evidence_hash(record) != row["content_sha256"]
  ):
    raise ValueError("instrument snapshot stored content mismatch")
  return InstrumentDetailSnapshot(
    request_id=request_id,
    code=code,
    schema_version=row["schema_version"],
    manifest_sha256=row["manifest_sha256"],
    content_sha256=row["content_sha256"],
    record=record,
  )
