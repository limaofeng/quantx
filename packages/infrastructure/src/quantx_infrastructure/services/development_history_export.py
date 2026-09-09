"""Low-priority, durable development history exports using QMT transfer proofs."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from quantx_contracts import HistoricalBarSummary, historical_bar_key
from quantx_contracts.data_exchange import HistoryPartitionRequest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from quantx_infrastructure.runtime_store import MarketDataSourceUnavailable
from quantx_infrastructure.services.data_exchange import content_path, export_root
from quantx_infrastructure.services.data_exchange_reference import export_reference
from quantx_infrastructure.services.development_history_window import (
  history_window_open,
)
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  MarketDataValidationError,
  _iter_transfer_chunks,
  load_uploaded_request_manifest,
  validate_bar_records_against_request,
)

from .development_delivery_execution import run_delivery_execution
from .market_data_staging_cleanup import _joined_thread

logger = logging.getLogger(__name__)


class ExportCleanupDeferred(RuntimeError):
  """Unresolved evidence prevents retirement, without preventing publication."""


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
      temporary = target.with_name(f".{digest}.{uuid.uuid4().hex}.tmp")
      try:
        temporary.write_bytes(body)
        temporary.replace(target)
      finally:
        temporary.unlink(missing_ok=True)
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


_REUSABLE_SOURCE_REQUEST_SQL = """
  SELECT request_id FROM market_data_request
  WHERE status='COMPLETED' AND request_payload->>'operation'='bars'
    AND (request_payload->'stock_list')::jsonb @> CAST(:codes AS jsonb)
    AND (request_payload->'periods')::jsonb @> CAST(:periods AS jsonb)
    AND request_payload->>'start_time' <= :day
    AND request_payload->>'end_time' >= :day
    AND ingestion_result->'persistence_verification'->>'status'='verified'
    AND EXISTS (
      SELECT 1
      FROM json_array_elements(
        COALESCE(ingestion_result->'day_coverage', '[]'::json)
      ) AS day_coverage(value)
      WHERE day_coverage.value->>'instrument_code' = :instrument
        AND LOWER(COALESCE(day_coverage.value->>'period', '')) = :period
        AND REPLACE(day_coverage.value->>'trading_date', '-', '') = :day
        AND day_coverage.value->>'point_count' ~ '^[1-9][0-9]*$'
    )
  ORDER BY completed_at DESC LIMIT 1
"""


async def find_reusable_source_request(
  connection, request: HistoryPartitionRequest, payload: dict
) -> str | None:
  """Find a completed source that proves positive coverage for this partition."""

  return await connection.scalar(
    text(_REUSABLE_SOURCE_REQUEST_SQL),
    {
      "codes": json.dumps([request.instrument]),
      "periods": json.dumps([request.period]),
      "day": payload["start_time"],
      "instrument": request.instrument,
      "period": request.period.lower(),
    },
  )


def _has_positive_source_coverage(
  source: dict, request: HistoryPartitionRequest
) -> bool:
  ingestion = source.get("ingestion_result")
  if isinstance(ingestion, str):
    try:
      ingestion = json.loads(ingestion)
    except (TypeError, json.JSONDecodeError):
      return False
  if not isinstance(ingestion, dict):
    return False
  target_day = request.trading_date.strftime("%Y%m%d")
  for item in ingestion.get("day_coverage") or []:
    if not isinstance(item, dict):
      continue
    if (
      item.get("instrument_code") != request.instrument
      or str(item.get("period") or "").lower() != request.period
      or str(item.get("trading_date") or "").replace("-", "") != target_day
    ):
      continue
    point_count = item.get("point_count")
    if isinstance(point_count, int) and not isinstance(point_count, bool):
      if point_count > 0:
        return True
    elif (
      isinstance(point_count, str)
      and point_count.isascii()
      and point_count.isdigit()
      and not point_count.startswith("0")
    ):
      return True
  return False


@asynccontextmanager
async def _transaction(store, owner):
  async with asyncio.timeout(5), store.engine.begin() as connection:
    await owner._guard_ingestion_owner(connection)
    yield connection
    await owner._guard_ingestion_owner(connection)


async def dispatch_once(store) -> dict:
  if os.environ.get("ENV") != "production" or store.demand_source_kind != "AGENT":
    return {"status": "disabled"}
  return await run_delivery_execution(
    None,
    async_sessionmaker(store.engine),
    lambda owner: _dispatch_owned(store, owner),
    worker_owner=store,
    lock_key=817234591,
  )


async def _dispatch_owned(store, owner) -> dict:
  if time.monotonic() >= getattr(store, "_next_export_cleanup", 0):
    async with store.engine.connect() as connection:
      try:
        await cleanup_expired(connection, owner=owner)
      except ExportCleanupDeferred as exc:
        await connection.rollback()
        logger.warning("Export cleanup deferred: %s", exc)
    store._next_export_cleanup = time.monotonic() + 300

  async with _transaction(store, owner) as connection:
    row = (
      (
        await connection.execute(
          text("""
      SELECT id,request,state,source_request_id FROM development_data_export
      WHERE state IN ('QUEUED','WAITING_SOURCE')
      ORDER BY updated_at,id LIMIT 1 FOR UPDATE SKIP LOCKED
    """)
        )
      )
      .mappings()
      .one_or_none()
    )
    if row is None:
      return {"status": "idle", "partitions": 0}
    row = dict(row)
    await connection.execute(
      text("""
      UPDATE development_data_export SET state='WAITING_SOURCE',updated_at=clock_timestamp()
      WHERE id=:id
    """),
      {"id": row["id"]},
    )
    try:
      request = HistoryPartitionRequest.model_validate(row["request"])
    except ValueError:
      await connection.execute(
        text("""
        UPDATE development_data_export SET state='INCOMPLETE',error='EXPORT_REQUEST_INVALID'
        WHERE id=:id
      """),
        {"id": row["id"]},
      )
      return {"status": "incomplete", "partitions": 1}
    payload = request.agent_payload()
    source_id = row["source_request_id"]
    if not source_id:
      source_id = await find_reusable_source_request(connection, request, payload)
    if not source_id:
      if not await history_window_open():
        return {"status": "waiting", "partitions": 1}
      try:
        source_id = await store.create_market_data_request(
          payload,
          idempotency_scope=f"development-export:{row['id']}",
          development_only=True,
          _connection=connection,
        )
      except MarketDataSourceUnavailable:
        return {"status": "waiting", "partitions": 1}
    await connection.execute(
      text("""
      UPDATE development_data_export SET source_request_id=:source WHERE id=:id
    """),
      {"id": row["id"], "source": source_id},
    )

  source = await store.market_data_request(source_id)
  if source is None:
    await set_failed(store, owner, row["id"], "SOURCE_REQUEST_MISSING")
    return {"status": "incomplete", "partitions": 1}
  if (
    source["status"] == "COMPLETED"
    and source.get("development_only") is False
    and not _has_positive_source_coverage(source, request)
  ):
    await set_failed(store, owner, row["id"], "SOURCE_COVERAGE_UNVERIFIED")
    return {"status": "incomplete", "partitions": 1}
  if source["status"] != "COMPLETED":
    if source["status"] == "FAILED":
      await set_failed(store, owner, row["id"], "SOURCE_REQUEST_FAILED")
    return {"status": "waiting", "partitions": 1}
  try:
    from .data_exchange_archive import persisted_partition

    if request.period == "1d":
      persisted_rows = await persisted_partition(
        request, source.get("ingestion_result") or {}
      )
      records = await _joined_thread(partition_records, [persisted_rows], request)
    else:
      try:
        _, _, files = await load_uploaded_request_manifest(store, source_id)
        records = await _joined_thread(
          partition_records, _iter_transfer_chunks(files), request
        )
        await _joined_thread(validate_bar_records_against_request, records, payload)
      except (FileNotFoundError, MarketDataValidationError):
        persisted_rows = await persisted_partition(
          request, source.get("ingestion_result") or {}
        )
        records = await _joined_thread(partition_records, [persisted_rows], request)
    await _joined_thread(validate_bar_records_against_request, records, payload)
    async with _transaction(store, owner):
      pass
    chunks = await _joined_thread(publish, records)
    reference = await export_reference(request.instrument, request.trading_date)
    manifest = {
      "version": 1,
      "payload": payload,
      "chunks": chunks,
      "reference": reference,
      "source_request_id": source_id,
      "data_version": hashlib.sha256(
        json.dumps({"chunks": chunks, "reference": reference}, sort_keys=True).encode()
      ).hexdigest(),
      "coverage": "SOURCE_VERIFIED",
      "rows": len(records) - 1,
    }
    async with _transaction(store, owner) as update:
      result = await update.execute(
        text("""
        UPDATE development_data_export SET state='READY',manifest=CAST(:manifest AS JSON),
          error=NULL,updated_at=clock_timestamp(),expires_at=:expires
        WHERE id=:id AND state='WAITING_SOURCE' AND source_request_id=:source
      """),
        {
          "id": row["id"],
          "source": source_id,
          "manifest": json.dumps(manifest),
          "expires": datetime.now(timezone.utc) + timedelta(days=7),
        },
      )
      if result.rowcount != 1:
        raise RuntimeError("EXPORT_PUBLICATION_CONFLICT")
  except (ValueError, OSError, MarketDataValidationError) as exc:
    await set_failed(store, owner, row["id"], safe_export_error(exc))
    return {"status": "incomplete", "partitions": 1}
  return {"status": "processed", "partitions": 1}


def safe_export_error(exc: Exception) -> str:
  known = {
    "SOURCE_COVERAGE_MISSING",
    "PERSISTED_COVERAGE_UNPROVEN",
    "PERSISTED_COVERAGE_CHANGED",
    "HISTORICAL_SOURCE_IDENTITY_MISSING",
    "EXPORT_TRANSFER_BUDGET_EXCEEDED",
    "EXPORT_DISK_BUDGET_EXCEEDED",
    "EXPORT_CHECKSUM_MISMATCH",
    "EXPORT_RECORD_BUDGET_EXCEEDED",
    "REFERENCE_DATA_MISSING",
    "REFERENCE_DATA_BUDGET_EXCEEDED",
  }
  return str(exc) if str(exc) in known else type(exc).__name__


async def set_failed(store, owner, identity: str, reason: str) -> None:
  async with _transaction(store, owner) as connection:
    await connection.execute(
      text("""
      UPDATE development_data_export SET state='INCOMPLETE',error=:reason,
      updated_at=CURRENT_TIMESTAMP WHERE id=:id AND state IN ('QUEUED','WAITING_SOURCE')
    """),
      {"id": identity, "reason": reason},
    )


MAX_CLEANUP_REFERENCES = 100_000
MAX_CLEANUP_DELETIONS = 1000


async def cleanup_expired(connection, *, owner=None) -> None:
  # The same session lock serializes export publication and file retirement.
  # In particular, publish() must not reuse an old orphan between our reference
  # snapshot and unlink. Never clean through an unrelated pooled connection.
  if owner is not None:
    await owner._guard_ingestion_owner(connection)
  else:
    held = await connection.scalar(
      text("""
      SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND granted
        AND pid=pg_backend_pid() AND classid=0 AND objid=817234591 AND objsubid=1)
    """)
    )
    if not held:
      raise RuntimeError("export cleanup requires the live publication lock")
  async with asyncio.timeout(3):
    await connection.execute(
      text("""
      UPDATE development_data_export SET state='EXPIRED'
      WHERE id IN (SELECT id FROM development_data_export
        WHERE state='READY' AND expires_at < CURRENT_TIMESTAMP
        ORDER BY expires_at,id LIMIT 1000)
    """)
    )
    # Expiry revokes downloading. It does not authorize deleting evidence still
    # referenced by the catalog (including blocked imports and published versions).
    malformed = await connection.scalar(
      text("""
      SELECT EXISTS(SELECT 1 FROM development_data_export
        WHERE manifest IS NOT NULL AND CASE
          WHEN state='REFERENCE_VERIFIED' AND request->>'operation'='reference'
            AND NOT (manifest::jsonb ? 'chunks') THEN false
          WHEN jsonb_typeof(manifest::jsonb->'chunks')='array'
            THEN jsonb_array_length(manifest::jsonb->'chunks')=0
          ELSE true END)
    """)
    )
    if malformed:
      raise ExportCleanupDeferred(
        "export cleanup cannot resolve malformed manifest references"
      )
    retained = set(
      (
        await connection.execute(
          text("""
      SELECT DISTINCT chunk->>'checksum_sha256'
      FROM development_data_export e,
        LATERAL jsonb_array_elements(e.manifest::jsonb->'chunks') AS chunk
      WHERE e.manifest IS NOT NULL LIMIT :limit
    """),
          {"limit": MAX_CLEANUP_REFERENCES + 1},
        )
      )
      .scalars()
      .all()
    )
    if len(retained) > MAX_CLEANUP_REFERENCES or any(
      not isinstance(digest, str)
      or len(digest) != 64
      or any(char not in "0123456789abcdef" for char in digest)
      for digest in retained
    ):
      raise ExportCleanupDeferred(
        "export cleanup reference budget or checksum is invalid"
      )
    if owner is not None:
      await owner._guard_ingestion_owner(connection)
    await connection.commit()
  await _joined_thread(_delete_unreferenced, retained)


def _delete_unreferenced(retained):
  cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).timestamp()
  root = export_root()
  removed = 0
  for path in root.iterdir() if root.exists() else ():
    if not path.name.endswith(".json.gz") or path.is_symlink() or not path.is_file():
      continue
    digest = path.name.removesuffix(".json.gz")
    if (
      len(digest) != 64
      or any(char not in "0123456789abcdef" for char in digest)
      or digest in retained
      or path.stat().st_mtime >= cutoff
    ):
      continue
    if path == content_path(digest) and path.resolve().parent == root:
      path.unlink()
      removed += 1
      if removed >= MAX_CLEANUP_DELETIONS:
        break
