"""Read-only replay of immutable uploaded bar evidence; never claim or persist work."""

from __future__ import annotations

import asyncio
import hashlib
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import text

from .market_data_ingestion_progress import evidence_hash
from .market_data_persistence_verification import (
  MarketDataPersistenceBlockedError,
  MarketDataPersistenceMismatchError,
  readback_trace_scope,
  verify_persisted_bar_summaries,
)
from .market_data_transfer_ingestion import (
  _parse_bars_request,
  _uploaded_key_batches,
  _validate_bar_manifest,
  load_uploaded_request_manifest,
)


@dataclass(frozen=True)
class RequestSnapshot:
  request_id: str
  request: dict[str, Any]
  transfers: list[dict[str, Any]]

  async def market_data_request(self, request_id):
    assert request_id == self.request_id
    return self.request

  async def market_data_transfers(self, request_id):
    assert request_id == self.request_id
    return self.transfers


async def read_request_snapshot(engine, request_id: str) -> RequestSnapshot:
  # Both rows come from one snapshot. No heartbeat, owner, attempt or request mutation.
  async with engine.connect() as connection:
    async with connection.begin():
      await connection.execute(
        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
      )
      request = (
        (
          await connection.execute(
            text("""
        SELECT request_id, request_payload, status, expected_chunks, received_chunks
        FROM market_data_request WHERE request_id=:request_id
      """),
            {"request_id": request_id},
          )
        )
        .mappings()
        .one_or_none()
      )
      if request is None:
        raise ValueError("REQUEST_NOT_FOUND")
      transfers = (
        (
          await connection.execute(
            text("""
        SELECT chunk_index, checksum_sha256, record_count, compressed,
               compressed_bytes, storage_reference
        FROM market_data_transfer WHERE request_id=:request_id ORDER BY chunk_index
      """),
            {"request_id": request_id},
          )
        )
        .mappings()
        .all()
      )
      return RequestSnapshot(
        request_id, dict(request), [dict(row) for row in transfers]
      )


class TracedReadConnection:
  """Expose only the verifier's SELECT reader API and record sanitized page evidence."""

  def __init__(self, connection, emit: Callable[[dict], None]):
    self.connection, self.emit = connection, emit
    self.scans = 0
    self.groups: dict[str, int] = {}

  @contextmanager
  def get_client(self, *, timeout=None):
    self.scans += 1
    scope = dict(readback_trace_scope.get())
    digest = scope.get("group_sha256", "")
    if digest not in self.groups:
      self.groups[digest] = len(self.groups) + 1
    scope.update(group_index=self.groups[digest], scan_index=self.scans)
    with self.connection.get_client(timeout=timeout) as client:
      yield _TracedReaderClient(client, self.emit, scope)


class _TracedReaderClient:
  def __init__(self, client, emit, scope):
    self.client, self.emit, self.scope = client, emit, scope
    self.page = 0

  def query(self, *, query, language, mode, query_parameters, timeout):
    if (
      not query.startswith("SELECT ")
      or ";" in query
      or language != "sql"
      or mode != "reader"
    ):
      raise ValueError("DIAGNOSTIC_REQUIRES_SELECT_READER")
    self.page += 1
    # Only known generated SQL timestamps are retained; never SQL, arbitrary parameters,
    # provider messages, credentials, hostnames, or source filesystem paths.
    times = re.findall(r"time\s*(>=|<|>)\s*'([0-9T: .+Z-]+)'", query)
    context = {
      **self.scope,
      "page_index": self.page,
      "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
      "parameters_sha256": evidence_hash(query_parameters),
      "time_predicates": times,
      "cursor_code_sha256": (
        hashlib.sha256(str(query_parameters["after_code"]).encode()).hexdigest()
        if "after_code" in query_parameters
        else None
      ),
    }
    self.emit({"event": "query", **context})
    try:
      reader = self.client.query(
        query=query,
        language=language,
        mode=mode,
        query_parameters=query_parameters,
        timeout=timeout,
      )
      return _TracedReader(reader, self.emit, context) if reader is not None else None
    except Exception as exc:
      self.emit(
        {"event": "query_error", **context, "exception_type": type(exc).__name__}
      )
      raise


class _TracedReader:
  def __init__(self, reader, emit, context):
    self.reader, self.emit, self.context = reader, emit, context

  @property
  def schema(self):
    return self.reader.schema

  def __iter__(self):
    rows = 0
    try:
      for batch in self.reader:
        rows += batch.num_rows
        yield batch
    except Exception as exc:
      self.emit(
        {
          "event": "reader_error",
          **self.context,
          "rows_read": rows,
          "exception_type": type(exc).__name__,
        }
      )
      raise
    else:
      self.emit({"event": "page_read", **self.context, "rows_read": rows})

  def close(self):
    close = getattr(self.reader, "close", None)
    if close is not None:
      close()


async def diagnose_readback(store, request_id, *, connection, emit):
  """Validate existing files and replay real read-back without saving results to PG."""
  phase = "LOAD_MANIFEST"
  try:
    request, payload, manifest = await load_uploaded_request_manifest(store, request_id)
    if payload.get("operation", "bars") != "bars":
      raise ValueError("BAR_REQUEST_REQUIRED")
    fingerprint = evidence_hash(
      {
        "payload": payload,
        "chunks": [
          {key: value for key, value in item.items() if key != "storage_reference"}
          for item in manifest
        ],
      }
    )
    emit(
      {
        "event": "manifest",
        "request_id": request_id,
        "manifest_sha256": fingerprint,
        "chunks": len(manifest),
        "source_status": str(request.get("status", "")),
      }
    )
    phase = "VALIDATE_MANIFEST"
    audit = await asyncio.to_thread(_validate_bar_manifest, manifest, payload)
    scope = _parse_bars_request(payload)
    emit(
      {
        "event": "validated",
        "expected_keys": sum(item["row_count"] for item in audit["code_summaries"]),
        "start_ms": scope.start_ms,
        "end_exclusive_ms": scope.end_exclusive_ms,
      }
    )
    phase = "READBACK"
    result = await verify_persisted_bar_summaries(
      code_summaries=audit["code_summaries"],
      expected_key_batches=_uploaded_key_batches(manifest),
      start_ms=scope.start_ms,
      end_exclusive_ms=scope.end_exclusive_ms,
      connection=TracedReadConnection(connection, emit),
      max_attempts=1,
      retry_delays=(),
      concurrency=1,
    )
  except Exception as exc:
    reason = (
      exc.reason_code
      if isinstance(exc, MarketDataPersistenceBlockedError)
      else "READBACK_MISMATCH"
      if isinstance(exc, MarketDataPersistenceMismatchError)
      else "DIAGNOSTIC_FAILED"
    )
    outcome = {
      "event": "result",
      "status": "failed",
      "reason_code": reason,
      "exception_type": type(exc).__name__,
      "phase": phase,
    }
  else:
    outcome = {
      "event": "result",
      "status": "verified",
      "records_verified": result["records_verified"],
      "groups_verified": result["groups_verified"],
    }
  emit(outcome)
  return outcome
