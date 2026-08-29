"""Long-lived, XTData-only historical workload process.

The registered QMT Agent remains the sole owner of control WebSocket,
XTTrading, reconciliation, real-time quotes, and health. This spawned child
owns one read-only XTData adapter, accepts one request at a time over IPC, and
publishes only immutable gzip spool manifests. It never loads an account and
never establishes an Agent connection.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .broker import HistoricalMarketDataFieldError, QmtDataBroker

XTDATA_HISTORICAL_WORKER_KIND = "xtdata"
HISTORICAL_WORK_UNIT_INSTRUMENTS = 20


def _create_historical_broker() -> QmtDataBroker:
  """Construct only the local XTData capability in the child process."""

  return QmtDataBroker(set(), data_only=True)


def _serialized_worker_error(error: Exception) -> dict[str, Any]:
  if isinstance(error, HistoricalMarketDataFieldError):
    return {
      "kind": "historical_field",
      "code": error.code,
      "period": error.period,
      "source_time_ms": error.source_time_ms,
      "field": error.field,
    }
  if isinstance(
    error,
    (AttributeError, ValueError, TypeError, OverflowError, UnicodeError),
  ):
    return {
      "kind": "deterministic",
      "error_type": error.__class__.__name__,
      "message": str(error)[:1024],
    }
  return {
    "kind": "worker_failure",
    "reason": (
      "XTDATA_UNAVAILABLE"
      if error.__class__.__name__ == "XTDataUnavailableError"
      else "MARKET_DATA_PREPARATION_FAILED"
    ),
  }


def _chunks(values: list[str], size: int) -> Iterator[list[str]]:
  if not values:
    return
  group_count = (len(values) + size - 1) // size
  group_size, larger_groups = divmod(len(values), group_count)
  offset = 0
  for index in range(group_count):
    width = group_size + (1 if index < larger_groups else 0)
    yield values[offset : offset + width]
    offset += width


def historical_work_units(
  payload: dict[str, Any],
  *,
  instrument_batch_size: int = HISTORICAL_WORK_UNIT_INSTRUMENTS,
) -> tuple[dict[str, Any], ...]:
  """Split a bulk request at native-call boundaries without changing its schema."""

  if instrument_batch_size < 10 or instrument_batch_size > 30:
    raise ValueError("historical work-unit size must be between 10 and 30")
  operation = str(payload.get("operation") or "bars")
  raw_codes = payload.get("stock_list")
  codes = list(raw_codes) if isinstance(raw_codes, list) else []
  if not codes:
    return (dict(payload),)

  units: list[dict[str, Any]] = []
  if operation == "bars":
    raw_periods = payload.get("periods") or ["1d"]
    periods = list(raw_periods) if isinstance(raw_periods, list) else []
    if not periods:
      return (dict(payload),)
    # Broker output is period-major and instrument-sorted. Matching that order
    # keeps the complete request manifest deterministic across retries.
    ordered_codes = sorted(codes)
    for period in periods:
      for code_batch in _chunks(ordered_codes, instrument_batch_size):
        units.append(
          {
            **payload,
            "stock_list": code_batch,
            "periods": [period],
          }
        )
    return tuple(units)

  for code_batch in _chunks(sorted(codes), instrument_batch_size):
    units.append({**payload, "stock_list": code_batch})
  return tuple(units)


def _prepared_manifest(prepared: Any) -> dict[str, Any]:
  """Return only primitive spool metadata across the process boundary."""

  return {
    "spool_directory": str(prepared.spool_directory),
    "chunks": [
      {
        "path": str(chunk.path),
        "record_count": int(chunk.record_count),
        "digest": str(chunk.digest),
        "compressed_bytes": int(chunk.compressed_bytes),
      }
      for chunk in prepared.chunks
    ],
    "compressed_bytes": int(prepared.compressed_bytes),
    "uncompressed_bytes": int(prepared.uncompressed_bytes),
    "record_count": int(prepared.record_count),
  }


def _await_continue(connection: Any, request_id: str) -> None:
  control = connection.recv()
  if not isinstance(control, dict):
    raise RuntimeError("invalid historical worker control message")
  if control.get("type") == "shutdown":
    raise RuntimeError("historical worker stopped while request was active")
  if (
    control.get("type") != "continue"
    or str(control.get("request_id") or "") != request_id
  ):
    raise RuntimeError("historical worker checkpoint acknowledgement mismatch")


def _iter_request_records(
  broker: QmtDataBroker,
  payload: dict[str, Any],
  connection: Any,
  request_id: str,
  chunk_boundary: object,
) -> Iterator[Any]:
  units = historical_work_units(payload)
  connection.send(
    {
      "type": "started",
      "request_id": request_id,
      "total_units": len(units),
    }
  )
  for index, unit in enumerate(units, start=1):
    yield from broker.iter_market_data(unit)
    if index < len(units):
      # Force one immutable spool publication at the scheduling point so the
      # parent uploader can drain completed work while the next unit runs.
      yield chunk_boundary
      connection.send(
        {
          "type": "checkpoint",
          "request_id": request_id,
          "completed_units": index,
          "total_units": len(units),
        }
      )
      _await_continue(connection, request_id)


def _prepare_request(
  connection: Any,
  broker: QmtDataBroker,
  message: dict[str, Any],
) -> None:
  request_id = str(message.get("request_id") or "")
  payload = message.get("payload")
  spool_directory = str(message.get("spool_directory") or "")
  if not request_id or not isinstance(payload, dict) or not spool_directory:
    raise ValueError("invalid historical worker prepare request")

  # Import lazily so the spawned process does not introduce an import cycle.
  from .runtime import (
    _MARKET_DATA_CHUNK_BOUNDARY,
    _prepare_market_data_records_spool_sync,
  )

  def publish_chunk(index: int, chunk: Any) -> None:
    connection.send(
      {
        "type": "chunk",
        "request_id": request_id,
        "chunk_index": index,
        "chunk": {
          "path": str(chunk.path),
          "record_count": int(chunk.record_count),
          "digest": str(chunk.digest),
          "compressed_bytes": int(chunk.compressed_bytes),
        },
      }
    )

  prepared = _prepare_market_data_records_spool_sync(
    _iter_request_records(
      broker,
      payload,
      connection,
      request_id,
      _MARKET_DATA_CHUNK_BOUNDARY,
    ),
    Path(spool_directory),
    max_total_uncompressed_bytes=int(message["max_total_uncompressed_bytes"]),
    max_total_compressed_bytes=int(message["max_total_compressed_bytes"]),
    on_chunk=publish_chunk,
  )
  connection.send(
    {
      "type": "ok",
      "request_id": request_id,
      "manifest": _prepared_manifest(prepared),
    }
  )


def run_historical_market_data_worker(
  connection: Any,
  worker_kind: str,
) -> None:
  """Serve serial historical requests until the parent explicitly shuts down."""

  broker: QmtDataBroker | None = None
  try:
    if worker_kind != XTDATA_HISTORICAL_WORKER_KIND:
      raise ValueError("unsupported historical market-data worker kind")
    broker = _create_historical_broker()
    while True:
      try:
        message = connection.recv()
      except EOFError:
        return
      if not isinstance(message, dict):
        connection.send(
          {
            "type": "error",
            "request_id": "",
            "error": _serialized_worker_error(
              ValueError("invalid historical worker message")
            ),
          }
        )
        continue
      if message.get("type") == "shutdown":
        return
      request_id = str(message.get("request_id") or "")
      try:
        if message.get("type") != "prepare":
          raise ValueError("unsupported historical worker message")
        _prepare_request(connection, broker, message)
      except Exception as exc:
        connection.send(
          {
            "type": "error",
            "request_id": request_id,
            "error": _serialized_worker_error(exc),
          }
        )
  finally:
    if broker is not None:
      close = getattr(broker.data_manager, "close_connection", None)
      if callable(close):
        try:
          close()
        except Exception:
          # Process exit releases the isolated native client. A close error
          # must not affect XTTrading or the control Agent.
          pass
    connection.close()
