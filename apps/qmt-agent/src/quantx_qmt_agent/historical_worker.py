"""Long-lived, XTData-only historical workload process.

The registered QMT Agent remains the sole owner of control WebSocket,
XTTrading, reconciliation, real-time quotes, and health. This spawned child
owns one read-only XTData adapter, accepts one request at a time over IPC, and
publishes only immutable gzip spool manifests. It never loads an account and
never establishes an Agent connection.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from quantx_contracts import (
  HISTORICAL_BAR_NO_DATA_REASON,
  HISTORICAL_BAR_SUMMARY_RECORD_TYPE,
  HISTORICAL_TICK_ORDINAL_FIELD,
  HistoricalBarSummary,
  historical_bar_key,
)

from .broker import (
  HistoricalMarketDataFieldError,
  QmtDataBroker,
  validate_market_data_request,
)

XTDATA_HISTORICAL_WORKER_KIND = "xtdata"
HISTORICAL_CHECKPOINT = object()
HISTORICAL_WORK_UNIT_INSTRUMENTS = 20
HISTORICAL_TICK_WORK_UNIT_INSTRUMENTS = 10
HISTORICAL_WORK_UNIT_WINDOW_DAYS = {
  "tick": 1,
  "1m": 1,
  "1d": 31,
}


@dataclass
class _HistoricalStagingBudget:
  max_bytes: int
  bytes_written: int = 0
  retained_bytes: int = 0
  disk_budget: _HistoricalDiskBudget | None = None

  def reserve(self, size: int) -> None:
    next_size = self.bytes_written + size
    if next_size > self.max_bytes:
      raise ValueError("market data request exceeds uncompressed byte limit")
    if self.disk_budget is not None:
      self.disk_budget.reserve(size)
    self.bytes_written = next_size
    self.retained_bytes += size

  def release(self, size: int) -> None:
    released = min(max(0, int(size)), self.retained_bytes)
    self.retained_bytes -= released
    if self.disk_budget is not None:
      self.disk_budget.release(released)


@dataclass
class _HistoricalDiskBudget:
  """Track staging and published gzip bytes against one physical quota."""

  max_bytes: int
  retained_bytes: int = 0
  shared: _HistoricalDiskBudget | None = None

  def reserve(self, size: int) -> None:
    next_size = self.retained_bytes + max(0, int(size))
    if next_size > self.max_bytes:
      raise ValueError("market data request exceeds spool disk byte limit")
    if self.shared is not None:
      self.shared.reserve(size)
    self.retained_bytes = next_size

  def release(self, size: int) -> None:
    released = min(self.retained_bytes, max(0, int(size)))
    self.retained_bytes -= released
    if self.shared is not None:
      self.shared.release(released)


@dataclass
class _HistoricalSeriesSpool:
  code: str
  period: str
  path: Path
  staging_budget: _HistoricalStagingBudget
  max_record_uncompressed_bytes: int
  row_count: int = 0
  min_time: int | None = None
  max_time: int | None = None
  last_tick_ordinal: int | None = None
  key_digest: Any = field(default_factory=hashlib.sha256)
  handle: Any = None
  disk_bytes: int = 0

  def append(self, record: dict[str, Any]) -> None:
    source_time = int(record["time"])
    tick_ordinal = (
      int(record[HISTORICAL_TICK_ORDINAL_FIELD]) if self.period == "tick" else None
    )
    key = historical_bar_key(
      code=self.code,
      period=self.period,
      time_ms=source_time,
      tick_ordinal=tick_ordinal,
    )
    encoded = json.dumps(
      record,
      ensure_ascii=False,
      separators=(",", ":"),
      sort_keys=True,
      default=str,
      allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > self.max_record_uncompressed_bytes:
      raise ValueError("single market data record exceeds record byte limit")
    retained_size = len(encoded) + 1
    self.staging_budget.reserve(retained_size)
    self.disk_bytes += retained_size
    if self.row_count:
      self.key_digest.update(b"\n")
    self.key_digest.update(key.encode("utf-8"))
    if self.min_time is None:
      self.min_time = source_time
      if self.period == "tick" and tick_ordinal != 0:
        raise ValueError("historical tick ordinals are not contiguous")
    if self.max_time is not None:
      if source_time < self.max_time:
        raise ValueError(
          "historical work units returned unordered or duplicate series keys"
        )
      if source_time == self.max_time and (
        self.period != "tick" or tick_ordinal != int(self.last_tick_ordinal or 0) + 1
      ):
        raise ValueError(
          "historical work units returned unordered or duplicate series keys"
        )
      if source_time > self.max_time and self.period == "tick" and tick_ordinal != 0:
        raise ValueError("historical tick ordinals are not contiguous")
    self.max_time = source_time
    self.last_tick_ordinal = tick_ordinal
    self.row_count += 1
    if self.handle is None:
      self.handle = self.path.open("xb")
    self.handle.write(encoded)
    self.handle.write(b"\n")

  def close(self) -> None:
    if self.handle is None:
      return
    self.handle.flush()
    self.handle.close()
    self.handle = None

  def unlink(self) -> None:
    self.close()
    self.path.unlink(missing_ok=True)
    self.staging_budget.release(self.disk_bytes)
    self.disk_bytes = 0

  def summary(self) -> dict[str, Any]:
    return HistoricalBarSummary(
      code=self.code,
      period=self.period,
      row_count=self.row_count,
      min_time=self.min_time,
      max_time=self.max_time,
      key_sha256=self.key_digest.hexdigest(),
      no_data_reason=(HISTORICAL_BAR_NO_DATA_REASON if self.row_count == 0 else None),
    ).model_dump(mode="json")


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


def _date_windows(
  start_text: str,
  end_text: str,
  *,
  max_days: int,
) -> Iterator[tuple[str, str]]:
  start = datetime.strptime(start_text, "%Y%m%d").date()
  end = datetime.strptime(end_text, "%Y%m%d").date()
  cursor = start
  while cursor <= end:
    window_end = min(end, cursor + timedelta(days=max_days - 1))
    yield cursor.strftime("%Y%m%d"), window_end.strftime("%Y%m%d")
    cursor = window_end + timedelta(days=1)


def historical_work_units(
  payload: dict[str, Any],
  *,
  instrument_batch_size: int | None = None,
) -> tuple[dict[str, Any], ...]:
  """Plan bounded, preemptible native calls for one validated request."""

  if instrument_batch_size is not None and not 10 <= instrument_batch_size <= 30:
    raise ValueError("historical work-unit size must be between 10 and 30")
  operation = str(payload.get("operation") or "bars")
  validate_market_data_request(payload)
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
      batch_size = instrument_batch_size or (
        HISTORICAL_TICK_WORK_UNIT_INSTRUMENTS
        if period == "tick"
        else HISTORICAL_WORK_UNIT_INSTRUMENTS
      )
      for code_batch in _chunks(ordered_codes, batch_size):
        for start_text, end_text in _date_windows(
          str(payload["start_time"]),
          str(payload["end_time"]),
          max_days=HISTORICAL_WORK_UNIT_WINDOW_DAYS[period],
        ):
          units.append(
            {
              **payload,
              "stock_list": code_batch,
              "periods": [period],
              "start_time": start_text,
              "end_time": end_text,
            }
          )
    return tuple(units)

  batch_size = instrument_batch_size or HISTORICAL_WORK_UNIT_INSTRUMENTS
  for code_batch in _chunks(sorted(codes), batch_size):
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


def _set_low_process_priority() -> None:
  """Move the isolated native-data worker below the live Agent on Windows."""

  if os.name != "nt":
    return
  import ctypes
  from ctypes import wintypes

  process_mode_background_begin = 0x00100000
  below_normal_priority_class = 0x00004000
  kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
  kernel32.GetCurrentProcess.argtypes = []
  kernel32.GetCurrentProcess.restype = wintypes.HANDLE
  kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
  kernel32.SetPriorityClass.restype = wintypes.BOOL
  process_handle = kernel32.GetCurrentProcess()
  if kernel32.SetPriorityClass(
    process_handle,
    process_mode_background_begin,
  ):
    return
  if not kernel32.SetPriorityClass(process_handle, below_normal_priority_class):
    raise OSError(
      ctypes.get_last_error(),
      "could not lower XTData worker process priority",
    )


def _iter_request_records(
  broker: QmtDataBroker,
  payload: dict[str, Any],
  connection: Any,
  request_id: str,
  chunk_boundary: object,
  spool_directory: Path,
  *,
  max_staging_uncompressed_bytes: int,
  max_record_uncompressed_bytes: int,
  disk_budget: _HistoricalDiskBudget | None = None,
) -> Iterator[Any]:
  units = historical_work_units(payload)
  connection.send(
    {
      "type": "started",
      "request_id": request_id,
      "total_units": len(units),
    }
  )
  if str(payload.get("operation") or "bars") != "bars":
    for index, unit in enumerate(units, start=1):
      yield from broker.iter_market_data(unit)
      if index < len(units):
        yield chunk_boundary
        connection.send(
          {
            "type": "checkpoint",
            "request_id": request_id,
            "completed_units": index,
            "total_units": len(units),
          }
        )
        yield HISTORICAL_CHECKPOINT
    return

  completed_units = 0
  group_start = 0
  staging_budget = _HistoricalStagingBudget(
    max_bytes=max_staging_uncompressed_bytes,
    disk_budget=disk_budget,
  )
  while group_start < len(units):
    first = units[group_start]
    group_key = (
      str(first["periods"][0]),
      tuple(str(code) for code in first["stock_list"]),
    )
    group_end = group_start + 1
    while group_end < len(units):
      candidate = units[group_end]
      candidate_key = (
        str(candidate["periods"][0]),
        tuple(str(code) for code in candidate["stock_list"]),
      )
      if candidate_key != group_key:
        break
      group_end += 1

    period, group_codes = group_key
    series = {
      code: _HistoricalSeriesSpool(
        code=code,
        period=period,
        path=spool_directory / f".series-{group_start:06d}-{offset:03d}.jsonl",
        staging_budget=staging_budget,
        max_record_uncompressed_bytes=max_record_uncompressed_bytes,
      )
      for offset, code in enumerate(group_codes)
    }
    try:
      for unit_index in range(group_start, group_end):
        unit = units[unit_index]
        summaries: set[str] = set()
        for raw_record in broker.iter_market_data(unit):
          if not isinstance(raw_record, dict):
            raise ValueError("XTData worker returned a non-object record")
          if "record_type" in raw_record:
            if raw_record.get("record_type") != HISTORICAL_BAR_SUMMARY_RECORD_TYPE:
              raise ValueError("XTData worker returned an unknown record type")
            summary = HistoricalBarSummary.model_validate(raw_record)
            if summary.period != period or summary.code not in series:
              raise ValueError("XTData worker returned an unexpected series summary")
            if summary.code in summaries:
              raise ValueError("XTData worker returned a duplicate series summary")
            summaries.add(summary.code)
            continue
          code = str(raw_record.get("code") or "")
          if code not in series or str(raw_record.get("period") or "") != period:
            raise ValueError("XTData worker returned a record outside its work unit")
          series[code].append(raw_record)
        if summaries != set(group_codes):
          raise ValueError("XTData worker omitted a required series summary")

        completed_units += 1
        if unit_index + 1 < group_end:
          # A window may not yet have canonical output to publish, but it is
          # still a scheduling point at which the parent can pause new work.
          yield chunk_boundary
          connection.send(
            {
              "type": "checkpoint",
              "request_id": request_id,
              "completed_units": completed_units,
              "total_units": len(units),
            }
          )
          yield HISTORICAL_CHECKPOINT

      # The wire contract is period-major, code-major, then time-major. Native
      # calls are window-major for efficiency, so replay the bounded per-code
      # disk spools only after this instrument batch has collected every window.
      for code in group_codes:
        item = series[code]
        item.close()
        if item.path.exists():
          with item.path.open("rb") as source:
            for line in source:
              yield json.loads(line)
        yield item.summary()
        item.unlink()
    finally:
      for item in series.values():
        item.unlink()

    if completed_units < len(units):
      # Publish the complete canonical instrument batch before granting the
      # next native call. Upload backpressure remains bounded in the parent.
      yield chunk_boundary
      connection.send(
        {
          "type": "checkpoint",
          "request_id": request_id,
          "completed_units": completed_units,
          "total_units": len(units),
        }
      )
      yield HISTORICAL_CHECKPOINT
    group_start = group_end


def _prepare_request(
  connection: Any,
  broker: QmtDataBroker,
  message: dict[str, Any],
  shared_disk_budget: _HistoricalDiskBudget,
) -> Iterator[None]:
  request_id = str(message.get("request_id") or "")
  payload = message.get("payload")
  spool_directory = str(message.get("spool_directory") or "")
  if not request_id or not isinstance(payload, dict) or not spool_directory:
    raise ValueError("invalid historical worker prepare request")

  # Import lazily so the spawned process does not introduce an import cycle.
  from .runtime import (
    _MARKET_DATA_CHUNK_BOUNDARY,
    MAX_MARKET_DATA_RECORD_UNCOMPRESSED_BYTES,
    _prepare_market_data_records_spool_steps,
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

  disk_budget = _HistoricalDiskBudget(
    shared=shared_disk_budget,
    max_bytes=int(
      message.get("max_spool_bytes")
      or int(message["max_total_uncompressed_bytes"])
      + int(message["max_total_compressed_bytes"])
    ),
  )
  finished_spool = False
  try:
    prepared = yield from _prepare_market_data_records_spool_steps(
      _iter_request_records(
        broker,
        payload,
        connection,
        request_id,
        _MARKET_DATA_CHUNK_BOUNDARY,
        Path(spool_directory),
        max_staging_uncompressed_bytes=int(message["max_total_uncompressed_bytes"]),
        max_record_uncompressed_bytes=MAX_MARKET_DATA_RECORD_UNCOMPRESSED_BYTES,
        disk_budget=disk_budget,
      ),
      Path(spool_directory),
      max_total_uncompressed_bytes=int(message["max_total_uncompressed_bytes"]),
      max_total_compressed_bytes=int(message["max_total_compressed_bytes"]),
      on_chunk=publish_chunk,
      reserve_compressed_bytes=disk_budget.reserve,
    )
    finished_spool = True
    connection.send(
      {
        "type": "ok",
        "request_id": request_id,
        "manifest": _prepared_manifest(prepared),
      }
    )
  finally:
    if finished_spool:
      shared_disk_budget.max_bytes -= disk_budget.retained_bytes
    disk_budget.release(disk_budget.retained_bytes)


def run_historical_market_data_worker(
  connection: Any,
  worker_kind: str,
) -> None:
  """Serve serial historical requests until the parent explicitly shuts down."""

  broker: QmtDataBroker | None = None
  active = {}
  shared_disk_budget = _HistoricalDiskBudget(0)
  try:
    if worker_kind != XTDATA_HISTORICAL_WORKER_KIND:
      raise ValueError("unsupported historical market-data worker kind")
    if multiprocessing.parent_process() is not None:
      _set_low_process_priority()
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
        if message.get("type") == "prepare":
          if request_id in active or len(active) >= 4:
            raise ValueError("historical worker request capacity or identity conflict")
          allowance = int(
            message.get("max_spool_bytes")
            or int(message["max_total_uncompressed_bytes"])
            + int(message["max_total_compressed_bytes"])
          )
          shared_disk_budget.max_bytes = allowance + shared_disk_budget.retained_bytes
          active[request_id] = _prepare_request(
            connection, broker, message, shared_disk_budget
          )
        elif message.get("type") != "continue" or request_id not in active:
          raise ValueError("unsupported historical worker control message")
        else:
          allowance = int(message["max_spool_bytes"])
          if allowance < 0:
            raise ValueError("negative historical disk allowance")
          shared_disk_budget.max_bytes = allowance + shared_disk_budget.retained_bytes
        try:
          next(active[request_id])
        except StopIteration:
          active.pop(request_id)
      except Exception as exc:
        failed = active.pop(request_id, None)
        if failed is not None:
          failed.close()
        connection.send(
          {
            "type": "error",
            "request_id": request_id,
            "error": _serialized_worker_error(exc),
          }
        )
  finally:
    for steps in active.values():
      steps.close()
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
