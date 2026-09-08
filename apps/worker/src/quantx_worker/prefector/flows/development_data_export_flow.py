"""Low-priority, durable development history exports using QMT transfer proofs."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from prefect import flow
from quantx_contracts import HistoricalBarSummary, historical_bar_key
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_infrastructure.runtime_store import DurableRuntimeStore
from quantx_infrastructure.services.data_exchange import content_path, export_root
from quantx_infrastructure.services.data_exchange_reference import export_reference
from quantx_infrastructure.services.development_history_window import (
  history_window_open,
)
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  MarketDataValidationError,
  _iter_transfer_chunks,
  claim_ingest_and_finish_market_data_request,
  load_uploaded_request_manifest,
  validate_bar_records_against_request,
)
from sqlalchemy import text


def partition_records(chunks, request: HistoryPartitionRequest) -> list[dict]:
  start = datetime.combine(
    request.trading_date, datetime.min.time(), ZoneInfo("Asia/Shanghai")
  )
  first, last = (
    int(start.timestamp() * 1000),
    int((start + timedelta(days=1)).timestamp() * 1000),
  )
  records = [
    record
    for chunk in chunks
    for record in chunk
    if "record_type" not in record
    and record.get("code") == request.instrument
    and record.get("period") == request.period
    and first <= record["time"] < last
  ]
  if not records:
    raise ValueError("SOURCE_COVERAGE_MISSING")
  digest = hashlib.sha256()
  for index, record in enumerate(records):
    if index:
      digest.update(b"\n")
    digest.update(
      historical_bar_key(
        code=request.instrument,
        period=request.period,
        time_ms=record["time"],
        tick_ordinal=record.get("tick_ordinal"),
      ).encode()
    )
  summary = HistoricalBarSummary(
    code=request.instrument,
    period=request.period,
    row_count=len(records),
    min_time=records[0]["time"],
    max_time=records[-1]["time"],
    key_sha256=digest.hexdigest(),
    no_data_reason=None,
  )
  return [*records, summary.model_dump(mode="json")]


def publish(records: list[dict]) -> list[dict]:
  from quantx_infrastructure.services.market_data_transfer_ingestion import (
    MAX_TRANSFER_CHUNK_RECORDS,
    MAX_TRANSFER_CHUNK_UNCOMPRESSED_BYTES,
    MAX_TRANSFER_RECORD_UNCOMPRESSED_BYTES,
    MAX_TRANSFER_REQUEST_CHUNKS,
    MAX_TRANSFER_REQUEST_COMPRESSED_BYTES,
    MAX_TRANSFER_REQUEST_UNCOMPRESSED_BYTES,
  )

  root = export_root()
  root.mkdir(parents=True, exist_ok=True)
  used = sum(path.stat().st_size for path in root.glob("*.json.gz"))
  manifest, pending = [], []
  pending_bytes, raw_total, compressed_total = 2, 0, 0

  def flush():
    nonlocal used, compressed_total
    if not pending:
      return
    body = gzip.compress(b"[" + b",".join(pending) + b"]", mtime=0)
    compressed_total += len(body)
    if (
      len(manifest) >= MAX_TRANSFER_REQUEST_CHUNKS
      or compressed_total > MAX_TRANSFER_REQUEST_COMPRESSED_BYTES
    ):
      raise ValueError("EXPORT_TRANSFER_BUDGET_EXCEEDED")
    digest = hashlib.sha256(body).hexdigest()
    target = content_path(digest)
    if not target.exists():
      used += len(body)
      if used > 4 * 1024**3:
        raise ValueError("EXPORT_DISK_BUDGET_EXCEEDED")
      temporary = target.with_suffix(".tmp")
      temporary.write_bytes(body)
      temporary.replace(target)
    elif hashlib.sha256(target.read_bytes()).hexdigest() != digest:
      raise ValueError("EXPORT_CHECKSUM_MISMATCH")
    manifest.append(
      {
        "chunk_index": len(manifest),
        "checksum_sha256": digest,
        "record_count": len(pending),
        "compressed": True,
        "compressed_bytes": len(body),
      }
    )

  for record in records:
    encoded = json.dumps(record, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_TRANSFER_RECORD_UNCOMPRESSED_BYTES:
      raise ValueError("EXPORT_RECORD_BUDGET_EXCEEDED")
    if pending and (
      len(pending) >= MAX_TRANSFER_CHUNK_RECORDS
      or pending_bytes + len(encoded) + 1 > MAX_TRANSFER_CHUNK_UNCOMPRESSED_BYTES
    ):
      flush()
      pending, pending_bytes = [], 2
    pending.append(encoded)
    pending_bytes += len(encoded) + 1
    raw_total += len(encoded) + 1
    if (
      raw_total
      > MAX_TRANSFER_REQUEST_UNCOMPRESSED_BYTES - 2 * MAX_TRANSFER_REQUEST_CHUNKS
    ):
      raise ValueError("EXPORT_TRANSFER_BUDGET_EXCEEDED")
  flush()
  return manifest


async def dispatch_once() -> dict:
  if os.environ.get("ENV") != "production":
    return {"status": "disabled"}
  store = DurableRuntimeStore()
  try:
    async with store.engine.connect() as connection:
      locked = await connection.scalar(text("SELECT pg_try_advisory_lock(817234591)"))
      if not locked:
        return {"status": "busy"}
      try:
        await cleanup_expired(store)
        rows = (
          (
            await connection.execute(
              text("""
          SELECT id, request, state, source_request_id FROM development_data_export
          WHERE state IN ('QUEUED','WAITING_SOURCE')
          ORDER BY CASE WHEN state='QUEUED' THEN 0 ELSE 1 END, updated_at LIMIT 20
        """)
            )
          )
          .mappings()
          .all()
        )
        for row in rows:
          async with store.engine.begin() as update:
            await update.execute(
              text(
                "UPDATE development_data_export SET state='WAITING_SOURCE',updated_at=CURRENT_TIMESTAMP WHERE id=:id"
              ),
              {"id": row["id"]},
            )
          request = HistoryPartitionRequest.model_validate(row["request"])
          payload = request.agent_payload()
          source_id = row["source_request_id"]
          if not source_id:
            source_id = await connection.scalar(
              text("""
              SELECT request_id FROM market_data_request
              WHERE status='COMPLETED' AND request_payload->>'operation'='bars'
                AND (request_payload->'stock_list')::jsonb @> CAST(:codes AS jsonb)
                AND (request_payload->'periods')::jsonb @> CAST(:periods AS jsonb)
                AND request_payload->>'start_time' <= :day
                AND request_payload->>'end_time' >= :day
                AND ingestion_result->'persistence_verification'->>'status'='verified'
              ORDER BY completed_at DESC LIMIT 1
            """),
              {
                "codes": json.dumps([request.instrument]),
                "periods": json.dumps([request.period]),
                "day": payload["start_time"],
              },
            )
          if not source_id:
            if not await history_window_open():
              continue
            try:
              source_id = await store.create_market_data_request(
                payload,
                idempotency_scope=f"development-export:{row['id']}",
                development_only=True,
              )
            except RuntimeError:
              continue
          async with store.engine.begin() as update:
            await update.execute(
              text("""
              UPDATE development_data_export SET source_request_id=:source,
              state='WAITING_SOURCE',updated_at=CURRENT_TIMESTAMP WHERE id=:id
            """),
              {"id": row["id"], "source": source_id},
            )
          source = await store.market_data_request(source_id)
          if source["status"] == "FAILED" and await history_window_open():
            # An explicit resubmission creates one new, still low-priority attempt.
            if row.get("source_request_id") and row.get("state") == "QUEUED":
              replacement = await store.create_market_data_request(
                payload,
                idempotency_scope=f"development-retry:{source_id}",
                development_only=True,
              )
              async with store.engine.begin() as update:
                await update.execute(
                  text(
                    "UPDATE development_data_export SET source_request_id=:source WHERE id=:id"
                  ),
                  {"source": replacement, "id": row["id"]},
                )
              continue
          if source["status"] in {"UPLOADED", "PROCESSING"}:
            await claim_ingest_and_finish_market_data_request(store, source_id)
            source = await store.market_data_request(source_id)
          if source["status"] != "COMPLETED":
            if source["status"] == "FAILED":
              await set_failed(store, row["id"], "SOURCE_REQUEST_FAILED")
            continue
          try:
            try:
              _, _, files = await load_uploaded_request_manifest(store, source_id)
              records = await asyncio.to_thread(
                partition_records, _iter_transfer_chunks(files), request
              )
            except (FileNotFoundError, MarketDataValidationError):
              from quantx_infrastructure.services.data_exchange_archive import (
                persisted_partition,
              )

              rows = await persisted_partition(
                request, source.get("ingestion_result") or {}
              )
              records = partition_records([rows], request)
            validate_bar_records_against_request(records, payload)
            chunks = await asyncio.to_thread(publish, records)
            reference = await export_reference(request.instrument, request.trading_date)
            manifest = {
              "version": 1,
              "payload": payload,
              "chunks": chunks,
              "reference": reference,
              "source_request_id": source_id,
              "data_version": hashlib.sha256(
                json.dumps(
                  {"chunks": chunks, "reference": reference}, sort_keys=True
                ).encode()
              ).hexdigest(),
              "coverage": "SOURCE_VERIFIED",
              "rows": len(records) - 1,
            }
            async with store.engine.begin() as update:
              await update.execute(
                text("""
                UPDATE development_data_export SET state='READY',manifest=CAST(:manifest AS JSON),
                error=NULL,updated_at=CURRENT_TIMESTAMP,expires_at=:expires WHERE id=:id
              """),
                {
                  "id": row["id"],
                  "manifest": json.dumps(manifest),
                  "expires": datetime.now(timezone.utc) + timedelta(days=7),
                },
              )
          except (ValueError, OSError, MarketDataValidationError) as exc:
            await set_failed(store, row["id"], type(exc).__name__)
        return {"status": "processed", "partitions": len(rows)}
      finally:
        await connection.execute(text("SELECT pg_advisory_unlock(817234591)"))
  finally:
    await store.close()


async def set_failed(store, identity: str, reason: str) -> None:
  async with store.engine.begin() as connection:
    await connection.execute(
      text("""
      UPDATE development_data_export SET state='INCOMPLETE',error=:reason,
      updated_at=CURRENT_TIMESTAMP WHERE id=:id
    """),
      {"id": identity, "reason": reason},
    )


async def cleanup_expired(store) -> None:
  async with store.engine.begin() as connection:
    await connection.execute(
      text("""
      UPDATE development_data_export SET state='EXPIRED'
      WHERE state='READY' AND expires_at < CURRENT_TIMESTAMP
    """)
    )
    manifests = (
      (
        await connection.execute(
          text("""
      SELECT manifest FROM development_data_export
      WHERE expires_at >= CURRENT_TIMESTAMP AND manifest IS NOT NULL
    """)
        )
      )
      .scalars()
      .all()
    )
  retained = {
    item["checksum_sha256"]
    for manifest in manifests
    for item in manifest.get("chunks", [])
  }
  cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).timestamp()
  for path in export_root().glob("*.json.gz"):
    digest = path.name.removesuffix(".json.gz")
    if digest in retained or path.is_symlink() or path.stat().st_mtime >= cutoff:
      continue
    if path == content_path(digest) and path.resolve().parent == export_root():
      path.unlink()


@flow(name="development-data-export", log_prints=False)
async def development_data_export_flow() -> dict:
  return await dispatch_once()
