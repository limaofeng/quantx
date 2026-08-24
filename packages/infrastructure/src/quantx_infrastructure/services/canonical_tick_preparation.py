"""Prepare immutable canonical Tick inputs from verified QMT transfers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quantx_infrastructure.core.data.tick_identity import tick_storage_time
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  MarketDataTransferStore,
  MarketDataValidationError,
  _iter_transfer_chunks,
  _validate_bar_manifest,
  load_uploaded_request_manifest,
)

CANONICAL_TICK_PREPARATION_DESTINATION = "canonical_tick_archive"
_PREPARATION_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[0-9A-Z._-]{1,32}$")
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def canonical_tick_preparation_root() -> Path:
  configured = os.environ.get("QUANTX_CANONICAL_TICK_PREPARATION_DIR", "").strip()
  root = (
    Path(configured)
    if configured
    else Path.cwd() / ".runtime" / "canonical-tick-preparations"
  )
  root.mkdir(parents=True, exist_ok=True)
  if root.is_symlink() or not root.is_dir():
    raise MarketDataValidationError("canonical Tick preparation root is unsafe")
  return root.resolve(strict=True)


def canonical_tick_preparation_directory(preparation_id: str) -> Path:
  """Resolve one hash-addressed preparation directory without following links."""

  if not _PREPARATION_ID_RE.fullmatch(preparation_id):
    raise MarketDataValidationError("canonical Tick preparation id is invalid")
  root = canonical_tick_preparation_root()
  preparation = root / preparation_id
  preparation.mkdir(parents=False, exist_ok=True)
  if preparation.is_symlink():
    raise MarketDataValidationError("canonical Tick preparation path is unsafe")
  resolved = preparation.resolve(strict=True)
  if root not in resolved.parents or not resolved.is_dir():
    raise MarketDataValidationError("canonical Tick preparation path is unsafe")
  return resolved


def _preparation_directory(preparation_id: str, verification_pass: int) -> Path:
  if verification_pass not in {1, 2}:
    raise MarketDataValidationError("canonical Tick verification pass is invalid")
  preparation = canonical_tick_preparation_directory(preparation_id)
  destination = preparation / f"pass-{verification_pass}"
  destination.mkdir(parents=False, exist_ok=True)
  if destination.is_symlink():
    raise MarketDataValidationError("canonical Tick preparation path is unsafe")
  resolved = destination.resolve(strict=True)
  if preparation not in resolved.parents or not resolved.is_dir():
    raise MarketDataValidationError("canonical Tick preparation path is unsafe")
  return resolved


def _required_number(record: Mapping[str, Any], field: str, *, default: Any = None) -> Any:
  value = record.get(field, default)
  if value is None:
    raise MarketDataValidationError(
      f"canonical Tick transfer is missing required field: {field}"
    )
  return value


def _canonical_record(
  record: Mapping[str, Any],
  *,
  preparation_id: str,
  sequence: int,
) -> dict[str, Any]:
  source_time_ms = int(_required_number(record, "time"))
  tick_ordinal = int(_required_number(record, "tick_ordinal"))
  code = str(record.get("code") or "").strip().upper()
  source_date = (
    datetime.fromtimestamp(source_time_ms / 1000, UTC).astimezone(_SHANGHAI).date()
  )
  storage_time = tick_storage_time(source_time_ms, tick_ordinal).astimezone(UTC)
  return {
    "stock_code": code,
    "period": "tick",
    "time": storage_time.isoformat().replace("+00:00", "Z"),
    "last_price": _required_number(record, "lastPrice"),
    "open": _required_number(record, "open"),
    "high": _required_number(record, "high"),
    "low": _required_number(record, "low"),
    "last_close": _required_number(record, "lastClose"),
    "amount": _required_number(record, "amount"),
    "volume": _required_number(record, "volume"),
    "pvolume": _required_number(record, "pvolume"),
    "tickvol": _required_number(record, "tickvol"),
    "stock_status": _required_number(record, "stockStatus"),
    "open_int": _required_number(record, "openInt"),
    "last_settlement_price": _required_number(record, "lastSettlementPrice"),
    "settlement_price": _required_number(record, "settlementPrice"),
    "transaction_num": _required_number(record, "transactionNum"),
    "price_tick": _required_number(record, "priceTick", default=0.01),
    "up_stop_price": _required_number(record, "upperLimit", default=0.0),
    "down_stop_price": _required_number(record, "lowerLimit", default=0.0),
    "ask_price": _required_number(record, "askPrice"),
    "bid_price": _required_number(record, "bidPrice"),
    "ask_vol": _required_number(record, "askVol"),
    "bid_vol": _required_number(record, "bidVol"),
    "source_time_ms": source_time_ms,
    "tick_ordinal": tick_ordinal,
    "continuity_generation": 0,
    "market_stream_id": (
      f"qmt-history:{preparation_id[:16]}:{code}:{source_date.isoformat()}"
    ),
    "market_stream_sequence": sequence,
    "market_stream_reset": sequence == 1,
  }


def _sha256_file(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as source:
    for block in iter(lambda: source.read(1024 * 1024), b""):
      digest.update(block)
  return digest.hexdigest()


def _publish_idempotent(staged: Path, target: Path, *, digest: str) -> None:
  if target.is_symlink():
    staged.unlink(missing_ok=True)
    raise MarketDataValidationError("canonical Tick preparation target is unsafe")
  try:
    os.link(staged, target)
  except FileExistsError:
    if target.is_symlink():
      raise MarketDataValidationError(
        "canonical Tick preparation target is unsafe"
      )
    if _sha256_file(target) != digest:
      raise MarketDataValidationError(
        "canonical Tick verification pass produced conflicting content"
      )
  finally:
    staged.unlink(missing_ok=True)


def _write_canonical_tick_files(
  manifest: list[dict[str, Any]],
  *,
  preparation_id: str,
  verification_pass: int,
  instrument_code: str,
) -> list[dict[str, Any]]:
  """Stream one validated transfer without blocking the async claim lease."""

  output_directory = _preparation_directory(preparation_id, verification_pass)
  staged: dict[str, Path] = {}
  handles: dict[str, Any] = {}
  counts: dict[str, int] = {}
  first_identity: dict[str, list[int]] = {}
  last_identity: dict[str, list[int]] = {}
  try:
    try:
      for chunk in _iter_transfer_chunks(manifest):
        for raw in chunk:
          if "record_type" in raw:
            continue
          source_time_ms = int(raw["time"])
          trading_date = (
            datetime.fromtimestamp(source_time_ms / 1000, UTC)
            .astimezone(_SHANGHAI)
            .date()
            .isoformat()
          )
          sequence = counts.get(trading_date, 0) + 1
          canonical = _canonical_record(
            raw,
            preparation_id=preparation_id,
            sequence=sequence,
          )
          if trading_date not in handles:
            staged_path = output_directory / (
              f".{instrument_code}@{trading_date}.{uuid.uuid4().hex}.tmp"
            )
            handles[trading_date] = staged_path.open("xb")
            staged[trading_date] = staged_path
          encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
          ).encode("utf-8")
          handles[trading_date].write(encoded + b"\n")
          counts[trading_date] = sequence
          identity = [
            0,
            int(canonical["source_time_ms"]),
            int(canonical["tick_ordinal"]),
          ]
          first_identity.setdefault(trading_date, identity)
          last_identity[trading_date] = identity
    finally:
      for handle in handles.values():
        handle.close()

    files: list[dict[str, Any]] = []
    for trading_date, staged_path in sorted(staged.items()):
      digest = _sha256_file(staged_path)
      target = output_directory / f"{instrument_code}@{trading_date}.ndjson"
      _publish_idempotent(staged_path, target, digest=digest)
      files.append(
        {
          "instrument_code": instrument_code,
          "trading_date": trading_date,
          "record_count": counts[trading_date],
          "content_sha256": digest,
          "path": str(target),
          "first_source_identity": first_identity[trading_date],
          "last_source_identity": last_identity[trading_date],
        }
      )
    return files
  finally:
    for staged_path in staged.values():
      staged_path.unlink(missing_ok=True)


async def ingest_uploaded_canonical_tick_request(
  store: MarketDataTransferStore,
  request_id: str,
) -> dict[str, Any]:
  """Validate one QMT transfer and retain deterministic per-day NDJSON inputs."""

  _, payload, manifest = await load_uploaded_request_manifest(store, request_id)
  if payload.get("destination") != CANONICAL_TICK_PREPARATION_DESTINATION:
    raise MarketDataValidationError("canonical Tick transfer destination is invalid")
  preparation_id = str(payload.get("canonical_preparation_id") or "")
  verification_pass = payload.get("canonical_verification_pass")
  if isinstance(verification_pass, bool) or not isinstance(verification_pass, int):
    raise MarketDataValidationError("canonical Tick verification pass is invalid")
  raw_codes = payload.get("stock_list")
  if (
    not isinstance(raw_codes, list)
    or len(raw_codes) != 1
    or not _CODE_RE.fullmatch(str(raw_codes[0] or ""))
    or payload.get("periods") != ["tick"]
  ):
    raise MarketDataValidationError(
      "canonical Tick transfer must contain one instrument and tick only"
    )
  audit = await asyncio.to_thread(
    _validate_bar_manifest,
    manifest,
    payload,
  )
  files = await asyncio.to_thread(
    _write_canonical_tick_files,
    manifest,
    preparation_id=preparation_id,
    verification_pass=verification_pass,
    instrument_code=str(raw_codes[0]),
  )

  return {
    **audit,
    "destination": CANONICAL_TICK_PREPARATION_DESTINATION,
    "canonical_preparation_id": preparation_id,
    "canonical_verification_pass": verification_pass,
    "canonical_tick_files": files,
    "records_saved": int(audit["records_received"]),
    "records_verified": int(audit["records_received"]),
  }


__all__ = [
  "CANONICAL_TICK_PREPARATION_DESTINATION",
  "canonical_tick_preparation_directory",
  "canonical_tick_preparation_root",
  "ingest_uploaded_canonical_tick_request",
]
