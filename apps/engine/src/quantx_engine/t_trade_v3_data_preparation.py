"""QMT-backed canonical input preparation for the formal T-trade replay."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from quantx_infrastructure.runtime_store import DurableRuntimeStore
from quantx_infrastructure.services.canonical_tick_archive import (
  ArchiveCutover,
  CanonicalTickArchive,
  CanonicalTickArchiveError,
  FormalReplayScope,
  InstrumentDay,
)
from quantx_infrastructure.services.canonical_tick_preparation import (
  canonical_tick_preparation_directory,
)
from quantx_infrastructure.services.market_data_request_service import (
  request_canonical_tick_sync,
)

_MAX_QMT_TICK_REQUEST_CALENDAR_DAYS = 7


class CanonicalTickPreparationError(RuntimeError):
  """The real historical input could not satisfy the formal quality gate."""


def _request_windows(trading_dates: Sequence[date]) -> list[tuple[date, date]]:
  if not trading_dates:
    return []
  windows: list[tuple[date, date]] = []
  start = end = trading_dates[0]
  for current in trading_dates[1:]:
    if (current - start).days + 1 > _MAX_QMT_TICK_REQUEST_CALENDAR_DAYS:
      windows.append((start, end))
      start = current
    end = current
  windows.append((start, end))
  return windows


def _file_map(
  results: Iterable[Mapping[str, Any]],
  *,
  expected: set[InstrumentDay],
) -> dict[InstrumentDay, dict[str, Any]]:
  files: dict[InstrumentDay, dict[str, Any]] = {}
  for result in results:
    for raw in list(result.get("canonical_tick_files") or []):
      if not isinstance(raw, Mapping):
        raise CanonicalTickPreparationError(
          "canonical Tick ingestion returned malformed file evidence"
        )
      key = InstrumentDay.parse(raw.get("instrument_code"), raw.get("trading_date"))
      if key not in expected:
        raise CanonicalTickPreparationError(
          f"QMT returned Tick data outside formal scope: {key}"
        )
      if key in files:
        raise CanonicalTickPreparationError(
          f"canonical Tick preparation returned duplicate instrument-day: {key}"
        )
      files[key] = dict(raw)
  return files


def _write_source_manifest(
  *,
  scope: FormalReplayScope,
  first_pass: Mapping[InstrumentDay, Mapping[str, Any]],
) -> Path:
  preparation = canonical_tick_preparation_directory(scope.scope_fingerprint)
  target = preparation / "source-manifest.json"
  latest_identity_ms = max(
    int(first_pass[key]["last_source_identity"][1]) for key in scope.instrument_days
  )
  acquired = datetime.now(UTC).isoformat().replace("+00:00", "Z")
  as_of = datetime.fromtimestamp(latest_identity_ms / 1000, UTC).isoformat().replace(
    "+00:00", "Z"
  )
  identity_provenance = {
    "source_time_ms": "XTData historical row timestamp normalized to epoch milliseconds",
    "tick_ordinal": (
      "deterministic same-millisecond ordinal over the QMT historical transfer row"
    ),
  }
  payload = {
    "schema_version": 1,
    "source": {
      "provider_id": "XTData via registered QuantX QMT Agent",
      "source_kind": "QMT_AGENT_DURABLE_VERIFIED_TRANSFER_NDJSON",
      "acquired_at": acquired,
      "as_of": as_of,
      "verification_status": "VERIFIED",
      "identity_provenance": identity_provenance,
    },
    "formal_scope": scope.to_dict(),
    "instrument_days": [
      {
        **key.to_dict(),
        "raw_input_sha256": str(first_pass[key]["content_sha256"]),
        "identity_provenance": identity_provenance,
      }
      for key in scope.instrument_days
    ],
  }
  encoded = json.dumps(
    payload,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
  ).encode("utf-8") + b"\n"
  if target.is_symlink():
    raise CanonicalTickPreparationError(
      "canonical Tick source manifest path is unsafe"
    )
  if target.exists():
    existing = json.loads(target.read_text(encoding="utf-8"))
    existing_scope = dict(existing.get("formal_scope") or {})
    existing_days = list(existing.get("instrument_days") or [])
    if existing_scope != scope.to_dict() or existing_days != payload["instrument_days"]:
      raise CanonicalTickPreparationError(
        "existing canonical Tick source manifest conflicts with verified inputs"
      )
    return target
  staged = preparation / f".source-manifest.{uuid.uuid4().hex}.tmp"
  staged.write_bytes(encoded)
  try:
    os.link(staged, target)
  except FileExistsError:
    return _write_source_manifest(scope=scope, first_pass=first_pass)
  finally:
    staged.unlink(missing_ok=True)
  return target


async def _require_market_data_agent() -> str:
  store = DurableRuntimeStore()
  try:
    device_id = await store.available_market_data_device()
    blocked_ingestion = (
      await store.blocked_market_data_ingestion(str(device_id))
      if device_id
      else None
    )
  finally:
    await store.close()
  if not device_id:
    raise CanonicalTickPreparationError(
      "QMT_MARKET_DATA_AGENT_UNAVAILABLE: restore MiniQMT login before preparation"
    )
  if blocked_ingestion:
    raise CanonicalTickPreparationError(
      "QMT_MARKET_DATA_INGESTION_BLOCKED: resolve the existing durable "
      "market-data ingestion failure before formal preparation; "
      f"request_id={blocked_ingestion.get('request_id')} "
      f"status={blocked_ingestion.get('status')}"
    )
  return str(device_id)


async def prepare_canonical_tick_archive(
  *,
  snapshot_date: date,
  instrument_codes: Sequence[str],
  trading_dates: Sequence[date],
  archive_root: Path,
  timeout_seconds: float,
) -> dict[str, Any]:
  """Acquire the exact scope twice, publish it, and run the archive quality gate."""

  scope = FormalReplayScope.build(
    snapshot_date=snapshot_date,
    instrument_codes=instrument_codes,
    trading_dates=trading_dates,
  )
  device_id = await _require_market_data_agent()
  expected = set(scope.instrument_days)
  pass_results: dict[int, list[dict[str, Any]]] = {1: [], 2: []}
  request_evidence: list[dict[str, Any]] = []
  for verification_pass in (1, 2):
    for code in scope.instrument_codes:
      for window_start, window_end in _request_windows(scope.trading_dates):
        result = await request_canonical_tick_sync(
          stock_code=code,
          start_time=window_start.strftime("%Y%m%d"),
          end_time=window_end.strftime("%Y%m%d"),
          preparation_id=scope.scope_fingerprint,
          verification_pass=verification_pass,
          timeout_seconds=timeout_seconds,
        )
        status = str(result.get("status") or "").lower()
        if status != "success":
          raise CanonicalTickPreparationError(
            "QMT canonical Tick request did not complete: "
            f"pass={verification_pass} code={code} "
            f"window={window_start}..{window_end} status={status} "
            f"reason={result.get('reason')}"
          )
        pass_results[verification_pass].append(dict(result))
        request_evidence.append(
          {
            "verification_pass": verification_pass,
            "instrument_code": code,
            "start_date": window_start.isoformat(),
            "end_date": window_end.isoformat(),
            "request_id": str(result.get("request_id") or ""),
            "records_received": int(result.get("records_received") or 0),
            "records_verified": int(result.get("records_verified") or 0),
          }
        )

  first = _file_map(pass_results[1], expected=expected)
  second = _file_map(pass_results[2], expected=expected)
  if set(first) != expected or set(second) != expected:
    missing_first = sorted(expected - set(first))
    missing_second = sorted(expected - set(second))
    raise CanonicalTickPreparationError(
      "QMT canonical Tick scope is incomplete: "
      f"pass1_missing={missing_first} pass2_missing={missing_second}"
    )
  mismatches: list[dict[str, Any]] = []
  for key in scope.instrument_days:
    compared_fields = (
      "record_count",
      "content_sha256",
      "first_source_identity",
      "last_source_identity",
    )
    if any(first[key].get(field) != second[key].get(field) for field in compared_fields):
      mismatches.append(key.to_dict())
  if mismatches:
    raise CanonicalTickPreparationError(
      f"QMT repeated acquisition is not deterministic: {mismatches}"
    )

  source_manifest = _write_source_manifest(scope=scope, first_pass=first)
  records = {
    key: Path(str(first[key]["path"])) for key in scope.instrument_days
  }
  cutover: ArchiveCutover = CanonicalTickArchive(archive_root).publish(
    source_manifest=source_manifest,
    records=records,
  )
  reader = CanonicalTickArchive(archive_root, create=False).open(cutover.token)
  reader.validate_formal_scope(
    snapshot_date=snapshot_date,
    instrument_codes=instrument_codes,
    trading_dates=trading_dates,
  )
  inspections: list[dict[str, Any]] = []
  for trading_date in scope.trading_dates:
    for code in scope.instrument_codes:
      inspection = reader.inspect_tick_day(
        instrument_code=code,
        trading_date=trading_date,
      )
      inspections.append(inspection)
  failures = [item for item in inspections if not bool(item.get("complete"))]
  if failures:
    raise CanonicalTickArchiveError(
      "ARCHIVE_FORMAL_DATA_QUALITY_GATE_FAILED: "
      + json.dumps(failures, ensure_ascii=False, separators=(",", ":"))
    )
  return {
    "schema_version": 1,
    "status": "READY",
    "source": "REAL_XTDATA_QMT_AGENT",
    "synthetic": False,
    "device_id": device_id,
    "formal_scope": scope.to_dict(),
    "expected_instrument_days": len(expected),
    "verified_instrument_days": len(inspections),
    "double_acquisition_match": True,
    "request_evidence": request_evidence,
    "source_manifest": str(source_manifest),
    "archive_root": str(Path(archive_root).resolve()),
    "cutover_token": cutover.token,
    "manifest_fingerprint": cutover.manifest_fingerprint,
    "source_manifest_sha256": reader.source_manifest_sha256,
    "quality_gate": {
      "status": "PASS",
      "inspections": inspections,
    },
  }


__all__ = [
  "CanonicalTickPreparationError",
  "prepare_canonical_tick_archive",
]
