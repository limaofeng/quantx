"""Rebuild canonical request records solely from journal-bound native results."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from quantx_contracts import (
  HISTORICAL_BAR_SUMMARY_RECORD_TYPE,
  HISTORICAL_TICK_ORDINAL_FIELD,
  HistoricalBarSummary,
  historical_bar_key,
)
from quantx_contracts.collection_permit import CollectionUnit

from .historical_worker import (
  HISTORICAL_CHECKPOINT,
  _HistoricalDiskBudget,
  _iter_request_records,
)
from .history_jobs import HistoryJob
from .journal import LocalJournal
from .native_unit_artifact import NativeUnitArtifacts


def _verified_unit_records(records, payload):
  """A sealed file proves bytes, not that provider summaries match those bytes."""
  if str(payload.get("operation") or "bars") != "bars":
    yield from records
    return
  period = payload["periods"][0]
  evidence = {code: [0, None, None, hashlib.sha256()] for code in payload["stock_list"]}
  completed = set()
  for record in records:
    code = record.get("code")
    if code not in evidence or record.get("period") != period or code in completed:
      raise ValueError("history artifact record outside unfinished series")
    facts = evidence[code]
    if "record_type" in record:
      if record["record_type"] != HISTORICAL_BAR_SUMMARY_RECORD_TYPE:
        raise ValueError("history artifact has unknown summary type")
      summary = HistoricalBarSummary.model_validate(record)
      if (
        summary.row_count,
        summary.min_time,
        summary.max_time,
        summary.key_sha256,
      ) != (facts[0], facts[1], facts[2], facts[3].hexdigest()):
        raise ValueError("history artifact summary differs from records")
      completed.add(code)
    else:
      stamp = record["time"]
      key = historical_bar_key(
        code=code,
        period=period,
        time_ms=stamp,
        tick_ordinal=record[HISTORICAL_TICK_ORDINAL_FIELD]
        if period == "tick"
        else None,
      )
      if facts[0]:
        facts[3].update(b"\n")
      facts[3].update(key.encode())
      facts[0] += 1
      facts[1] = stamp if facts[1] is None else min(facts[1], stamp)
      facts[2] = stamp if facts[2] is None else max(facts[2], stamp)
    yield record
  if completed != set(evidence):
    raise ValueError("history artifact lacks required series summary")


def history_request_records(
  job: HistoryJob,
  *,
  device_id: str,
  journal: LocalJournal,
  artifacts: NativeUnitArtifacts,
  staging_directory: Path,
  chunk_boundary: object,
  max_uncompressed_bytes: int,
  max_record_bytes: int,
  disk_budget: _HistoricalDiskBudget,
) -> Iterator[Any]:
  """Validate every result first, then reuse canonical window-to-series ordering.

  No native broker exists on this path. All FINISH acknowledgements must have
  advanced the server cursor before assembly, including on process restart.
  """
  request = job.request
  if (
    request.completed_units != request.unit_count
    or len(job.units) != request.unit_count
  ):
    raise ValueError("history assembly requires server-confirmed completed units")
  if artifacts.root != job.artifacts_directory.resolve(strict=True):
    raise ValueError("history assembly artifact directory mismatch")
  results = []
  for unit in job.units:
    result = journal.load_collection_artifact(
      device_id=device_id, unit=unit, artifacts=artifacts
    )
    if result is None:
      raise ValueError("history assembly lacks journal-bound result")
    results.append(result)

  def read_unit(payload, ordinal):
    index = ordinal - 1
    expected = CollectionUnit.from_payload(str(request.request_id), index, payload)
    if index >= len(results) or results[index].unit != expected:
      raise ValueError("history assembly plan identity mismatch")
    yield from _verified_unit_records(artifacts.replay(results[index]), payload)

  records = _iter_request_records(
    None,
    request.payload,
    SimpleNamespace(send=lambda _: None),
    str(request.request_id),
    chunk_boundary,
    staging_directory,
    max_staging_uncompressed_bytes=max_uncompressed_bytes,
    max_record_uncompressed_bytes=max_record_bytes,
    disk_budget=disk_budget,
    read_unit=read_unit,
  )
  try:
    for record in records:
      # All native work is already complete. Scheduling boundaries must not
      # turn many tiny units into more than the wire's 128 upload chunks.
      if record is not chunk_boundary and record is not HISTORICAL_CHECKPOINT:
        yield record
  finally:
    records.close()
