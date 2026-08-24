"""Immutable externally-provenanced Tick archive for V3 formal replay only.

The archive is intentionally not an Influx replacement and is never selected by
the live/PAPER/default-backtest paths. A reader exists only after a complete
20-trading-day D-1 scope is cryptographically pinned by a cutover token.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import heapq
import json
import math
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

from quantx_contracts import HISTORICAL_TICK_ORDINALS_PER_MILLISECOND

from quantx_infrastructure.core.data.adapter import DataAdapter, DataMode
from quantx_infrastructure.core.data.historical import HistoricalDataAdapter
from quantx_infrastructure.core.data.tick_identity import tick_storage_time
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.models.tick import Tick

ARCHIVE_SCHEMA_VERSION = 1
FORMAL_TRADING_DAYS = 20
MAX_RECORDS_PER_CHUNK = 25_000
MAX_RECORDS_PER_DATASET = 2_000_000
MAX_LINE_BYTES = 1_000_000
MAX_MANIFEST_BYTES = 2_000_000
MAX_RAW_INPUT_BYTES = 2_500_000_000
MAX_PAGE_SIZE = 10_000
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TOKEN_RE = re.compile(r"^canonical-tick-v1-[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[0-9A-Z._-]{1,32}$")

REQUIRED_FIELDS = frozenset(
  {
    "stock_code",
    "period",
    "time",
    "last_price",
    "open",
    "high",
    "low",
    "last_close",
    "amount",
    "volume",
    "pvolume",
    "tickvol",
    "stock_status",
    "open_int",
    "last_settlement_price",
    "settlement_price",
    "transaction_num",
    "price_tick",
    "up_stop_price",
    "down_stop_price",
    "ask_price",
    "bid_price",
    "ask_vol",
    "bid_vol",
    "source_time_ms",
    "tick_ordinal",
    "continuity_generation",
    "market_stream_id",
    "market_stream_sequence",
    "market_stream_reset",
  }
)
_FLOAT_FIELDS = (
  "last_price",
  "open",
  "high",
  "low",
  "last_close",
  "amount",
  "volume",
  "pvolume",
  "tickvol",
  "last_settlement_price",
  "settlement_price",
  "price_tick",
  "up_stop_price",
  "down_stop_price",
)
_INT_FIELDS = (
  "stock_status",
  "open_int",
  "transaction_num",
  "source_time_ms",
  "tick_ordinal",
  "continuity_generation",
  "market_stream_sequence",
)
_BOOK_FIELDS = ("ask_price", "bid_price", "ask_vol", "bid_vol")


class CanonicalTickArchiveError(ValueError):
  """An archive cannot prove the immutable causal input that was requested."""


@dataclass(frozen=True, order=True)
class InstrumentDay:
  instrument_code: str
  trade_date: date

  @classmethod
  def parse(cls, code: Any, trade_date: Any) -> "InstrumentDay":
    normalized = str(code or "").strip().upper()
    if not _CODE_RE.fullmatch(normalized):
      raise CanonicalTickArchiveError("ARCHIVE_INSTRUMENT_CODE_INVALID")
    if isinstance(trade_date, str):
      try:
        parsed = date.fromisoformat(trade_date)
      except ValueError as exc:
        raise CanonicalTickArchiveError("ARCHIVE_TRADE_DATE_INVALID") from exc
    elif isinstance(trade_date, date) and not isinstance(trade_date, datetime):
      parsed = trade_date
    else:
      raise CanonicalTickArchiveError("ARCHIVE_TRADE_DATE_INVALID")
    return cls(normalized, parsed)

  def to_dict(self) -> dict[str, str]:
    return {"instrument_code": self.instrument_code, "trade_date": self.trade_date.isoformat()}


def _canonical_json(value: Mapping[str, Any]) -> bytes:
  return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
  return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class FormalReplayScope:
  snapshot_date: date
  instrument_codes: tuple[str, ...]
  trading_dates: tuple[date, ...]
  scope_fingerprint: str

  @classmethod
  def build(
    cls,
    *,
    snapshot_date: date,
    instrument_codes: Iterable[str],
    trading_dates: Iterable[date],
  ) -> "FormalReplayScope":
    normalized_codes = tuple(
      sorted(InstrumentDay.parse(code, snapshot_date).instrument_code for code in instrument_codes)
    )
    normalized_dates = tuple(trading_dates)
    if (
      not normalized_codes
      or len(set(normalized_codes)) != len(normalized_codes)
      or len(normalized_dates) != FORMAL_TRADING_DAYS
      or tuple(sorted(normalized_dates)) != normalized_dates
      or len(set(normalized_dates)) != len(normalized_dates)
      or any(item <= snapshot_date for item in normalized_dates)
    ):
      raise CanonicalTickArchiveError("ARCHIVE_FORMAL_SCOPE_INVALID")
    payload = {
      "snapshot_date": snapshot_date.isoformat(),
      "instrument_codes": list(normalized_codes),
      "trading_dates": [item.isoformat() for item in normalized_dates],
    }
    return cls(snapshot_date, normalized_codes, normalized_dates, _sha256_bytes(_canonical_json(payload)))

  @property
  def instrument_days(self) -> tuple[InstrumentDay, ...]:
    return tuple(
      InstrumentDay(code, day)
      for day in self.trading_dates
      for code in self.instrument_codes
    )

  def to_dict(self) -> dict[str, Any]:
    return {
      "snapshot_date": self.snapshot_date.isoformat(),
      "instrument_codes": list(self.instrument_codes),
      "trading_dates": [item.isoformat() for item in self.trading_dates],
      "scope_fingerprint": self.scope_fingerprint,
    }


@dataclass(frozen=True)
class SourceManifest:
  source_manifest_sha256: str
  source: Mapping[str, Any]
  formal_scope: FormalReplayScope
  instrument_days: Mapping[InstrumentDay, Mapping[str, Any]]


@dataclass(frozen=True)
class ArchiveCutover:
  token: str
  manifest_fingerprint: str
  formal_scope: FormalReplayScope

  def to_dict(self) -> dict[str, Any]:
    return {
      "schema_version": ARCHIVE_SCHEMA_VERSION,
      "token": self.token,
      "manifest_fingerprint": self.manifest_fingerprint,
      "formal_scope": self.formal_scope.to_dict(),
    }


def _require_safe_path(path: Path, *, allow_missing_leaf: bool = False) -> Path:
  candidate = Path(path).expanduser().absolute()
  parts = candidate.parts
  current = Path(parts[0])
  for index, part in enumerate(parts[1:], start=1):
    current = current / part
    if current.exists() or current.is_symlink():
      if current.is_symlink():
        raise CanonicalTickArchiveError("ARCHIVE_SYMLINK_PATH_REJECTED")
      if index < len(parts) - 1 and not current.is_dir():
        raise CanonicalTickArchiveError("ARCHIVE_PATH_PARENT_NOT_DIRECTORY")
    elif not allow_missing_leaf:
      raise CanonicalTickArchiveError("ARCHIVE_PATH_DOES_NOT_EXIST")
  return candidate


def _safe_child(root: Path, *parts: str) -> Path:
  if any(not part or part in {".", ".."} or "/" in part or "\\" in part for part in parts):
    raise CanonicalTickArchiveError("ARCHIVE_PATH_COMPONENT_INVALID")
  child = root.joinpath(*parts)
  try:
    child.relative_to(root)
  except ValueError as exc:
    raise CanonicalTickArchiveError("ARCHIVE_PATH_TRAVERSAL_REJECTED") from exc
  return _require_safe_path(child, allow_missing_leaf=True)


def _ensure_root(root: Path) -> Path:
  root = _require_safe_path(root, allow_missing_leaf=True)
  if root.exists() and not root.is_dir():
    raise CanonicalTickArchiveError("ARCHIVE_ROOT_NOT_DIRECTORY")
  root.mkdir(parents=True, exist_ok=True)
  for name in ("objects", "manifests", "cutovers"):
    _safe_child(root, name).mkdir(exist_ok=True)
  return root


def _open_existing_root(root: Path) -> Path:
  root = _require_safe_path(root)
  if not root.is_dir():
    raise CanonicalTickArchiveError("ARCHIVE_ROOT_NOT_DIRECTORY")
  for name in ("objects", "manifests", "cutovers"):
    child = _safe_child(root, name)
    if not child.is_dir():
      raise CanonicalTickArchiveError("ARCHIVE_LAYOUT_INCOMPLETE")
  return root


def _sha256_file(path: Path) -> str:
  digest = hashlib.sha256()
  try:
    with path.open("rb") as handle:
      for block in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(block)
  except OSError as exc:
    raise CanonicalTickArchiveError("ARCHIVE_INPUT_UNREADABLE") from exc
  return digest.hexdigest()


def _read_json_bytes(path: Path) -> tuple[Mapping[str, Any], bytes]:
  path = _require_safe_path(path)
  if not path.is_file():
    raise CanonicalTickArchiveError("ARCHIVE_INPUT_NOT_FILE")
  try:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
      raise CanonicalTickArchiveError("ARCHIVE_JSON_TOO_LARGE")
    raw = path.read_bytes()
    parsed = json.loads(raw)
  except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise CanonicalTickArchiveError("ARCHIVE_JSON_UNREADABLE") from exc
  if not isinstance(parsed, Mapping):
    raise CanonicalTickArchiveError("ARCHIVE_JSON_NOT_OBJECT")
  return parsed, raw


def _required_text(value: Any, error: str) -> str:
  if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
    raise CanonicalTickArchiveError(error)
  return value.strip()


def _parse_aware_timestamp(value: Any, *, field: str) -> str:
  if not isinstance(value, str) or not value:
    raise CanonicalTickArchiveError(f"ARCHIVE_{field.upper()}_INVALID")
  try:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError as exc:
    raise CanonicalTickArchiveError(f"ARCHIVE_{field.upper()}_INVALID") from exc
  if parsed.tzinfo is None:
    raise CanonicalTickArchiveError(f"ARCHIVE_{field.upper()}_TIMEZONE_REQUIRED")
  return time_utils.to_utc(parsed).isoformat().replace("+00:00", "Z")


def _parse_identity_provenance(value: Any) -> dict[str, str]:
  if not isinstance(value, Mapping) or set(value) != {"source_time_ms", "tick_ordinal"}:
    raise CanonicalTickArchiveError("ARCHIVE_IDENTITY_PROVENANCE_INVALID")
  return {
    field: _required_text(value[field], "ARCHIVE_IDENTITY_PROVENANCE_INVALID")
    for field in ("source_time_ms", "tick_ordinal")
  }


def _parse_source_provenance(value: Any) -> dict[str, Any]:
  source_fields = {
    "provider_id",
    "source_kind",
    "acquired_at",
    "as_of",
    "verification_status",
    "identity_provenance",
  }
  if not isinstance(value, Mapping) or set(value) != source_fields:
    raise CanonicalTickArchiveError("ARCHIVE_SOURCE_PROVENANCE_INVALID")
  source = {
    "provider_id": _required_text(value["provider_id"], "ARCHIVE_PROVIDER_ID_INVALID"),
    "source_kind": _required_text(value["source_kind"], "ARCHIVE_SOURCE_KIND_INVALID"),
    "acquired_at": _parse_aware_timestamp(value["acquired_at"], field="acquired_at"),
    "as_of": _parse_aware_timestamp(value["as_of"], field="as_of"),
    "verification_status": str(value["verification_status"] or "").upper(),
    "identity_provenance": _parse_identity_provenance(value["identity_provenance"]),
  }
  if source["verification_status"] != "VERIFIED":
    raise CanonicalTickArchiveError("ARCHIVE_SOURCE_NOT_VERIFIED")
  return source


def _parse_formal_scope(value: Any) -> FormalReplayScope:
  expected = {"snapshot_date", "instrument_codes", "trading_dates", "scope_fingerprint"}
  if not isinstance(value, Mapping) or set(value) != expected:
    raise CanonicalTickArchiveError("ARCHIVE_FORMAL_SCOPE_INVALID")
  snapshot = InstrumentDay.parse("SCOPE", value["snapshot_date"]).trade_date
  raw_codes = value["instrument_codes"]
  raw_dates = value["trading_dates"]
  if not isinstance(raw_codes, list) or not isinstance(raw_dates, list):
    raise CanonicalTickArchiveError("ARCHIVE_FORMAL_SCOPE_INVALID")
  scope = FormalReplayScope.build(
    snapshot_date=snapshot,
    instrument_codes=[str(item) for item in raw_codes],
    trading_dates=[InstrumentDay.parse("SCOPE", item).trade_date for item in raw_dates],
  )
  if value["scope_fingerprint"] != scope.scope_fingerprint:
    raise CanonicalTickArchiveError("ARCHIVE_FORMAL_SCOPE_FINGERPRINT_INVALID")
  return scope


def load_source_manifest(path: Path | str) -> SourceManifest:
  """Require and cryptographically pin one VERIFIED external-source manifest."""

  parsed, raw = _read_json_bytes(Path(path))
  if parsed.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
    raise CanonicalTickArchiveError("ARCHIVE_SOURCE_MANIFEST_SCHEMA_UNSUPPORTED")
  if set(parsed) != {"schema_version", "source", "formal_scope", "instrument_days"}:
    raise CanonicalTickArchiveError("ARCHIVE_SOURCE_MANIFEST_FIELDS_INVALID")
  source = _parse_source_provenance(parsed["source"])
  formal_scope = _parse_formal_scope(parsed["formal_scope"])
  raw_days = parsed["instrument_days"]
  if not isinstance(raw_days, list) or len(raw_days) != len(formal_scope.instrument_days):
    raise CanonicalTickArchiveError("ARCHIVE_SOURCE_SCOPE_INCOMPLETE")
  instrument_days: dict[InstrumentDay, Mapping[str, Any]] = {}
  for item in raw_days:
    expected = {"instrument_code", "trade_date", "raw_input_sha256", "identity_provenance"}
    if not isinstance(item, Mapping) or set(item) != expected:
      raise CanonicalTickArchiveError("ARCHIVE_SOURCE_DAY_PROVENANCE_INVALID")
    key = InstrumentDay.parse(item["instrument_code"], item["trade_date"])
    digest = item["raw_input_sha256"]
    if key in instrument_days or not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
      raise CanonicalTickArchiveError("ARCHIVE_SOURCE_INPUT_HASH_INVALID")
    instrument_days[key] = {
      "raw_input_sha256": digest,
      "identity_provenance": _parse_identity_provenance(item["identity_provenance"]),
    }
  if set(instrument_days) != set(formal_scope.instrument_days):
    raise CanonicalTickArchiveError("ARCHIVE_SOURCE_SCOPE_INCOMPLETE")
  return SourceManifest(_sha256_bytes(raw), source, formal_scope, instrument_days)


def _strict_int(value: Any, *, field: str, minimum: int = 0) -> int:
  if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
    raise CanonicalTickArchiveError(f"ARCHIVE_FIELD_INVALID_{field.upper()}")
  return value


def _strict_float(value: Any, *, field: str, positive: bool = False) -> float:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise CanonicalTickArchiveError(f"ARCHIVE_FIELD_INVALID_{field.upper()}")
  result = float(value)
  if not math.isfinite(result) or result < 0 or (positive and result <= 0):
    raise CanonicalTickArchiveError(f"ARCHIVE_FIELD_INVALID_{field.upper()}")
  return result


def _parse_time(value: Any) -> datetime:
  if not isinstance(value, str) or not value:
    raise CanonicalTickArchiveError("ARCHIVE_FIELD_INVALID_TIME")
  try:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError as exc:
    raise CanonicalTickArchiveError("ARCHIVE_FIELD_INVALID_TIME") from exc
  if parsed.tzinfo is None:
    raise CanonicalTickArchiveError("ARCHIVE_TIMEZONE_REQUIRED")
  return time_utils.to_utc(parsed)


def _require_aware_window(start_time: datetime, end_time: datetime) -> tuple[datetime, datetime]:
  if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
    raise CanonicalTickArchiveError("ARCHIVE_READ_TIME_INVALID")
  if start_time.tzinfo is None or end_time.tzinfo is None:
    raise CanonicalTickArchiveError("ARCHIVE_READ_TIMEZONE_REQUIRED")
  start = time_utils.to_utc(start_time)
  end = time_utils.to_utc(end_time)
  if end < start:
    raise CanonicalTickArchiveError("ARCHIVE_READ_TIME_RANGE_INVALID")
  return start, end


def _record_key(record: Mapping[str, Any]) -> tuple[int, int, int]:
  return (int(record["continuity_generation"]), int(record["source_time_ms"]), int(record["tick_ordinal"]))


def _normalize_record(raw: Any, expected: InstrumentDay) -> dict[str, Any]:
  if not isinstance(raw, Mapping):
    raise CanonicalTickArchiveError("ARCHIVE_NDJSON_RECORD_NOT_OBJECT")
  if set(raw) != REQUIRED_FIELDS:
    if REQUIRED_FIELDS.difference(raw):
      raise CanonicalTickArchiveError("ARCHIVE_NDJSON_REQUIRED_FIELD_MISSING")
    raise CanonicalTickArchiveError("ARCHIVE_NDJSON_UNKNOWN_FIELD")
  code = str(raw["stock_code"] or "").strip().upper()
  if code != expected.instrument_code or raw["period"] != "tick":
    raise CanonicalTickArchiveError("ARCHIVE_RECORD_SCOPE_MISMATCH")
  source_time_ms = _strict_int(raw["source_time_ms"], field="source_time_ms", minimum=1)
  ordinal = _strict_int(raw["tick_ordinal"], field="tick_ordinal")
  if ordinal >= HISTORICAL_TICK_ORDINALS_PER_MILLISECOND:
    raise CanonicalTickArchiveError("ARCHIVE_TICK_ORDINAL_OUT_OF_RANGE")
  actual_time = _parse_time(raw["time"])
  try:
    expected_time = time_utils.to_utc(tick_storage_time(source_time_ms, ordinal))
  except (ValueError, OverflowError, OSError) as exc:
    raise CanonicalTickArchiveError("ARCHIVE_STORAGE_TIME_INVALID") from exc
  if actual_time != expected_time:
    raise CanonicalTickArchiveError("ARCHIVE_STORAGE_TIME_IDENTITY_MISMATCH")
  source_date = datetime.fromtimestamp(source_time_ms / 1000, tz=UTC).astimezone(_SHANGHAI).date()
  if source_date != expected.trade_date:
    raise CanonicalTickArchiveError("ARCHIVE_RECORD_TRADE_DATE_OUT_OF_SCOPE")
  normalized: dict[str, Any] = {"stock_code": code, "period": "tick", "time": actual_time.isoformat().replace("+00:00", "Z")}
  for field in _FLOAT_FIELDS:
    normalized[field] = _strict_float(raw[field], field=field, positive=field == "price_tick")
  for field in _INT_FIELDS:
    normalized[field] = _strict_int(raw[field], field=field)
  for field in _BOOK_FIELDS:
    values = raw[field]
    if not isinstance(values, list) or len(values) != 5:
      raise CanonicalTickArchiveError(f"ARCHIVE_FIELD_INVALID_{field.upper()}")
    normalized[field] = [_strict_float(item, field=field) for item in values]
  normalized["market_stream_id"] = _required_text(raw["market_stream_id"], "ARCHIVE_FIELD_INVALID_MARKET_STREAM_ID")
  if not isinstance(raw["market_stream_reset"], bool):
    raise CanonicalTickArchiveError("ARCHIVE_FIELD_INVALID_MARKET_STREAM_RESET")
  normalized["market_stream_reset"] = raw["market_stream_reset"]
  return normalized


def _iter_ndjson(path: Path, expected: InstrumentDay) -> Iterator[dict[str, Any]]:
  path = _require_safe_path(path)
  if not path.is_file():
    raise CanonicalTickArchiveError("ARCHIVE_INPUT_NOT_FILE")
  try:
    with path.open("rb") as handle:
      line_number = 0
      while line := handle.readline(MAX_LINE_BYTES + 1):
        line_number += 1
        # ``for line in handle`` calls ``readline()`` without a size bound and
        # can materialise an arbitrarily large unterminated input line before
        # this check runs.  Read one byte beyond the contract maximum instead.
        if len(line) > MAX_LINE_BYTES:
          raise CanonicalTickArchiveError("ARCHIVE_NDJSON_LINE_TOO_LARGE")
        if not line.strip():
          raise CanonicalTickArchiveError("ARCHIVE_NDJSON_EMPTY_LINE")
        try:
          raw = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
          raise CanonicalTickArchiveError(f"ARCHIVE_NDJSON_INVALID_LINE_{line_number}") from exc
        yield _normalize_record(raw, expected)
  except OSError as exc:
    raise CanonicalTickArchiveError("ARCHIVE_NDJSON_UNREADABLE") from exc


def _write_chunk(directory: Path, index: int, records: list[dict[str, Any]]) -> Path:
  records.sort(key=_record_key)
  path = directory / f"chunk-{index:08d}.ndjson"
  with path.open("xb") as handle:
    for record in records:
      handle.write(_canonical_json(record) + b"\n")
    handle.flush()
    os.fsync(handle.fileno())
  return path


def _iter_chunk(path: Path) -> Iterator[dict[str, Any]]:
  with path.open("rb") as handle:
    for line in handle:
      raw = json.loads(line)
      if not isinstance(raw, Mapping):
        raise CanonicalTickArchiveError("ARCHIVE_INTERNAL_CHUNK_INVALID")
      yield dict(raw)


def _sorted_normalized_records(source: Path, expected: InstrumentDay, *, temp_parent: Path) -> Iterator[dict[str, Any]]:
  temp_root = Path(tempfile.mkdtemp(prefix="canonical-sort-", dir=temp_parent))
  try:
    chunks: list[Path] = []
    buffered: list[dict[str, Any]] = []
    total = 0
    for record in _iter_ndjson(source, expected):
      buffered.append(record)
      total += 1
      if total > MAX_RECORDS_PER_DATASET:
        raise CanonicalTickArchiveError("ARCHIVE_DATASET_RECORD_LIMIT_EXCEEDED")
      if len(buffered) >= MAX_RECORDS_PER_CHUNK:
        chunks.append(_write_chunk(temp_root, len(chunks), buffered))
        buffered = []
    if not total:
      raise CanonicalTickArchiveError("ARCHIVE_DATASET_EMPTY")
    if buffered:
      chunks.append(_write_chunk(temp_root, len(chunks), buffered))
    iterators = [_iter_chunk(path) for path in chunks]
    heap: list[tuple[tuple[int, int, int], int, dict[str, Any]]] = []
    for index, iterator in enumerate(iterators):
      try:
        record = next(iterator)
      except StopIteration:
        continue
      heapq.heappush(heap, (_record_key(record), index, record))
    previous: tuple[int, int, int] | None = None
    while heap:
      identity, index, record = heapq.heappop(heap)
      if previous is not None and identity <= previous:
        raise CanonicalTickArchiveError("ARCHIVE_DUPLICATE_OR_UNORDERED_SOURCE_IDENTITY")
      previous = identity
      yield record
      try:
        next_record = next(iterators[index])
      except StopIteration:
        continue
      heapq.heappush(heap, (_record_key(next_record), index, next_record))
  finally:
    shutil.rmtree(temp_root, ignore_errors=True)


def _publish_immutable_file(staged: Path, target: Path, *, expected_sha256: str) -> None:
  target = _require_safe_path(target, allow_missing_leaf=True)
  if target.exists():
    if target.is_symlink() or _sha256_file(target) != expected_sha256:
      raise CanonicalTickArchiveError("ARCHIVE_IMMUTABLE_TARGET_CONFLICT")
    return
  try:
    os.link(staged, target)
  except FileExistsError:
    if target.is_symlink() or _sha256_file(target) != expected_sha256:
      raise CanonicalTickArchiveError("ARCHIVE_IMMUTABLE_TARGET_CONFLICT")
  except OSError as exc:
    raise CanonicalTickArchiveError("ARCHIVE_ATOMIC_PUBLISH_FAILED") from exc


def _publish_immutable_bytes(path: Path, payload: bytes) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  staged = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
  try:
    with staged.open("xb") as handle:
      handle.write(payload)
      handle.flush()
      os.fsync(handle.fileno())
    _publish_immutable_file(staged, path, expected_sha256=_sha256_bytes(payload))
  finally:
    staged.unlink(missing_ok=True)


def _stage_dataset(source: Path, expected: InstrumentDay, *, stage_root: Path) -> tuple[Path, dict[str, Any]]:
  temporary = stage_root / f"dataset-{uuid.uuid4().hex}.ndjson"
  digest = hashlib.sha256()
  count = 0
  first: tuple[int, int, int] | None = None
  last: tuple[int, int, int] | None = None
  try:
    with temporary.open("xb") as handle:
      for record in _sorted_normalized_records(source, expected, temp_parent=stage_root):
        identity = _record_key(record)
        first = first or identity
        last = identity
        line = _canonical_json(record) + b"\n"
        handle.write(line)
        digest.update(line)
        count += 1
      handle.flush()
      os.fsync(handle.fileno())
  except Exception:
    temporary.unlink(missing_ok=True)
    raise
  if count <= 0 or first is None or last is None:
    temporary.unlink(missing_ok=True)
    raise CanonicalTickArchiveError("ARCHIVE_DATASET_EMPTY")
  return temporary, {
    **expected.to_dict(),
    "content_sha256": digest.hexdigest(),
    "record_count": count,
    "first_source_identity": list(first),
    "last_source_identity": list(last),
  }


def _spool_verified_raw_input(
  source: Path,
  *,
  expected_sha256: str,
  stage_root: Path,
) -> Path:
  """Copy exactly the hashed external bytes into private staging storage.

  Parsing a path after a separate pre-hash leaves a time-of-check/time-of-use
  gap.  The archive instead hashes while it streams the raw input to its own
  staged file, verifies that digest, then normalizes only that staged copy.
  """

  if not source.is_file():
    raise CanonicalTickArchiveError("ARCHIVE_INPUT_NOT_FILE")
  staged = stage_root / f"raw-{uuid.uuid4().hex}.ndjson"
  digest = hashlib.sha256()
  byte_count = 0
  try:
    with source.open("rb") as source_handle, staged.open("xb") as staged_handle:
      for block in iter(lambda: source_handle.read(1024 * 1024), b""):
        byte_count += len(block)
        if byte_count > MAX_RAW_INPUT_BYTES:
          raise CanonicalTickArchiveError("ARCHIVE_RAW_INPUT_SIZE_LIMIT_EXCEEDED")
        digest.update(block)
        staged_handle.write(block)
      staged_handle.flush()
      os.fsync(staged_handle.fileno())
  except OSError as exc:
    staged.unlink(missing_ok=True)
    raise CanonicalTickArchiveError("ARCHIVE_INPUT_UNREADABLE") from exc
  except Exception:
    staged.unlink(missing_ok=True)
    raise
  if digest.hexdigest() != expected_sha256:
    staged.unlink(missing_ok=True)
    raise CanonicalTickArchiveError("ARCHIVE_SOURCE_INPUT_HASH_MISMATCH")
  return staged


class CanonicalTickArchive:
  """Separate archive that can publish only a formal 20-day scope."""

  def __init__(self, root: Path | str, *, create: bool = True):
    self.root = _ensure_root(Path(root)) if create else _open_existing_root(Path(root))

  def publish(self, *, source_manifest: Path | str, records: Mapping[InstrumentDay, Path | str]) -> ArchiveCutover:
    """Publish exactly one external VERIFIED manifest's complete formal scope.

    The source manifest is deliberately accepted only as a file path.  Passing
    a hand-assembled object would make its provenance hash unverifiable and is
    therefore rejected by the public import contract.
    """

    provenance = load_source_manifest(source_manifest)
    declared = provenance.formal_scope.instrument_days
    supplied = {
      InstrumentDay.parse(key.instrument_code, key.trade_date): _require_safe_path(Path(value))
      for key, value in records.items()
    }
    if set(supplied) != set(declared):
      raise CanonicalTickArchiveError("ARCHIVE_CUTOVER_SCOPE_NOT_COMPLETE")
    stage_root = Path(tempfile.mkdtemp(prefix="canonical-publish-", dir=self.root))
    try:
      datasets: list[dict[str, Any]] = []
      # Keep at most one raw source and one normalized output staged at a
      # time.  A complete formal scope can contain many very large files;
      # collecting either list before publication would make temporary disk
      # use grow with the entire scope.  Objects are content-addressed and
      # unreachable until the final manifest/cutover publish, so an
      # interrupted import may leave only safe, unreferenced objects.
      for key in declared:
        staged_raw: Path | None = None
        staged_file: Path | None = None
        try:
          staged_raw = _spool_verified_raw_input(
            supplied[key],
            expected_sha256=str(provenance.instrument_days[key]["raw_input_sha256"]),
            stage_root=stage_root,
          )
          staged_file, dataset = _stage_dataset(staged_raw, key, stage_root=stage_root)
          digest = str(dataset["content_sha256"])
          _publish_immutable_file(
            staged_file,
            _safe_child(self.root, "objects", f"{digest}.ndjson"),
            expected_sha256=digest,
          )
          datasets.append(
            {
              **dataset,
              "raw_input_sha256": provenance.instrument_days[key]["raw_input_sha256"],
              "identity_provenance": dict(
                provenance.instrument_days[key]["identity_provenance"]
              ),
            }
          )
        finally:
          # The object is published through a hard link, so these source and
          # normalized stage files can be removed immediately whether the
          # object was new, already present, or publication failed.
          if staged_file is not None:
            staged_file.unlink(missing_ok=True)
          if staged_raw is not None:
            staged_raw.unlink(missing_ok=True)
      manifest_base = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "kind": "IMMUTABLE_CANONICAL_TICK_ARCHIVE",
        "formal_scope": provenance.formal_scope.to_dict(),
        "source_provenance": {
          "source_manifest_sha256": provenance.source_manifest_sha256,
          "source": dict(provenance.source),
        },
        "datasets": datasets,
      }
      fingerprint = _sha256_bytes(_canonical_json(manifest_base))
      manifest = {**manifest_base, "manifest_fingerprint": fingerprint}
      _publish_immutable_bytes(_safe_child(self.root, "manifests", f"{fingerprint}.json"), _canonical_json(manifest) + b"\n")
      token = f"canonical-tick-v1-{fingerprint}"
      cutover = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "kind": "CANONICAL_TICK_ARCHIVE_FORMAL_20D_CUTOVER",
        "token": token,
        "manifest_fingerprint": fingerprint,
        "formal_scope": provenance.formal_scope.to_dict(),
      }
      _publish_immutable_bytes(_safe_child(self.root, "cutovers", f"{token}.json"), _canonical_json(cutover) + b"\n")
      return ArchiveCutover(token, fingerprint, provenance.formal_scope)
    finally:
      shutil.rmtree(stage_root, ignore_errors=True)

  def open(self, token: str) -> "CanonicalTickArchiveReader":
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
      raise CanonicalTickArchiveError("ARCHIVE_CUTOVER_TOKEN_INVALID")
    cutover, _ = _read_json_bytes(_safe_child(self.root, "cutovers", f"{token}.json"))
    if cutover.get("schema_version") != ARCHIVE_SCHEMA_VERSION or cutover.get("kind") != "CANONICAL_TICK_ARCHIVE_FORMAL_20D_CUTOVER" or cutover.get("token") != token:
      raise CanonicalTickArchiveError("ARCHIVE_CUTOVER_INVALID")
    formal_scope = _parse_formal_scope(cutover.get("formal_scope"))
    fingerprint = cutover.get("manifest_fingerprint")
    if not isinstance(fingerprint, str) or not _SHA256_RE.fullmatch(fingerprint):
      raise CanonicalTickArchiveError("ARCHIVE_CUTOVER_FINGERPRINT_INVALID")
    manifest, _ = _read_json_bytes(_safe_child(self.root, "manifests", f"{fingerprint}.json"))
    base = dict(manifest)
    if base.pop("manifest_fingerprint", None) != fingerprint or _sha256_bytes(_canonical_json(base)) != fingerprint:
      raise CanonicalTickArchiveError("ARCHIVE_MANIFEST_FINGERPRINT_INVALID")
    if manifest.get("schema_version") != ARCHIVE_SCHEMA_VERSION or manifest.get("kind") != "IMMUTABLE_CANONICAL_TICK_ARCHIVE" or _parse_formal_scope(manifest.get("formal_scope")) != formal_scope:
      raise CanonicalTickArchiveError("ARCHIVE_MANIFEST_INVALID")
    source_provenance = manifest.get("source_provenance")
    if (
      not isinstance(source_provenance, Mapping)
      or set(source_provenance) != {"source_manifest_sha256", "source"}
      or not _SHA256_RE.fullmatch(str(source_provenance.get("source_manifest_sha256") or ""))
    ):
      raise CanonicalTickArchiveError("ARCHIVE_MANIFEST_PROVENANCE_INVALID")
    _parse_source_provenance(source_provenance.get("source"))
    raw_datasets = manifest.get("datasets")
    if not isinstance(raw_datasets, list) or len(raw_datasets) != len(formal_scope.instrument_days):
      raise CanonicalTickArchiveError("ARCHIVE_MANIFEST_DATASETS_INVALID")
    datasets: dict[InstrumentDay, Mapping[str, Any]] = {}
    for dataset in raw_datasets:
      expected_dataset_fields = {
        "instrument_code",
        "trade_date",
        "content_sha256",
        "record_count",
        "first_source_identity",
        "last_source_identity",
        "raw_input_sha256",
        "identity_provenance",
      }
      if not isinstance(dataset, Mapping) or set(dataset) != expected_dataset_fields:
        raise CanonicalTickArchiveError("ARCHIVE_MANIFEST_DATASET_INVALID")
      key = InstrumentDay.parse(dataset.get("instrument_code"), dataset.get("trade_date"))
      if key in datasets or not _SHA256_RE.fullmatch(str(dataset.get("content_sha256") or "")) or not _SHA256_RE.fullmatch(str(dataset.get("raw_input_sha256") or "")) or type(dataset.get("record_count")) is not int or int(dataset["record_count"]) <= 0:
        raise CanonicalTickArchiveError("ARCHIVE_MANIFEST_DATASET_INVALID")
      _parse_identity_provenance(dataset.get("identity_provenance"))
      _validate_manifest_identity(dataset.get("first_source_identity"))
      _validate_manifest_identity(dataset.get("last_source_identity"))
      datasets[key] = dataset
    if set(datasets) != set(formal_scope.instrument_days):
      raise CanonicalTickArchiveError("ARCHIVE_MANIFEST_DATASET_SCOPE_INCOMPLETE")
    return CanonicalTickArchiveReader(
      self.root,
      ArchiveCutover(token, fingerprint, formal_scope),
      datasets,
      source_manifest_sha256=str(source_provenance["source_manifest_sha256"]),
    )


def _validate_manifest_identity(value: Any) -> tuple[int, int, int]:
  if not isinstance(value, list) or len(value) != 3:
    raise CanonicalTickArchiveError("ARCHIVE_MANIFEST_SOURCE_IDENTITY_INVALID")
  return _validate_cursor(tuple(value)) or (0, 0, 0)


class CanonicalTickArchiveReader:
  """Token-selected reader with bounded strict source-identity pagination."""

  def __init__(
    self,
    root: Path,
    cutover: ArchiveCutover,
    datasets: Mapping[InstrumentDay, Mapping[str, Any]],
    *,
    source_manifest_sha256: str,
  ):
    self._root = root
    self.cutover = cutover
    self._datasets = dict(datasets)
    self.source_manifest_sha256 = source_manifest_sha256

  def validate_formal_scope(self, *, snapshot_date: date, instrument_codes: Iterable[str], trading_dates: Iterable[date]) -> None:
    expected = FormalReplayScope.build(snapshot_date=snapshot_date, instrument_codes=instrument_codes, trading_dates=trading_dates)
    if expected != self.cutover.formal_scope:
      raise CanonicalTickArchiveError("ARCHIVE_FORMAL_SCOPE_MISMATCH")

  def iter_tick_pages(self, *, instrument_code: str, start_time: datetime, end_time: datetime, limit: int = MAX_PAGE_SIZE, after: tuple[int, int, int] | None = None) -> Iterator[tuple[Tick, ...]]:
    start, end = _require_aware_window(start_time, end_time)
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
      raise CanonicalTickArchiveError("ARCHIVE_PAGE_SIZE_INVALID")
    code = InstrumentDay.parse(instrument_code, start.astimezone(_SHANGHAI).date()).instrument_code
    cursor = _validate_cursor(after)
    page: list[Tick] = []
    previous: tuple[int, int, int] | None = None
    for record in self._iter_window_records(code=code, start=start, end=end):
      identity = _record_key(record)
      if previous is not None and identity <= previous:
        raise CanonicalTickArchiveError("ARCHIVE_OBJECT_IDENTITY_INVALID")
      previous = identity
      if cursor is not None and identity <= cursor:
        continue
      page.append(_tick_from_record(record))
      if len(page) == limit:
        yield tuple(page)
        page = []
    if page:
      yield tuple(page)

  def read_ticks(self, *, instrument_code: str, start_time: datetime, end_time: datetime, limit: int) -> list[Tick]:
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
      raise CanonicalTickArchiveError("ARCHIVE_READ_LIMIT_INVALID")
    pages = self.iter_tick_pages(instrument_code=instrument_code, start_time=start_time, end_time=end_time, limit=limit)
    first = list(next(pages, ()))
    if len(first) == limit and next(pages, ()):
      raise CanonicalTickArchiveError("ARCHIVE_READ_LIMIT_EXCEEDED")
    return first

  def inspect_tick_day(self, *, instrument_code: str, trading_date: date) -> dict[str, Any]:
    """Stream the Engine-equivalent Tick completeness gate from this archive.

    This intentionally retains only session edges and the immediately previous
    timestamp, rather than materialising a complete day of ticks.
    """

    if not isinstance(trading_date, date) or isinstance(trading_date, datetime):
      raise CanonicalTickArchiveError("ARCHIVE_TRADE_DATE_INVALID")
    code = InstrumentDay.parse(instrument_code, trading_date).instrument_code
    query_start = datetime.combine(trading_date, datetime.min.time(), tzinfo=_SHANGHAI).replace(hour=9, minute=25)
    query_end = datetime.combine(trading_date, datetime.min.time(), tzinfo=_SHANGHAI).replace(hour=15, minute=5)
    sessions = (
      (datetime.combine(trading_date, datetime.min.time()).replace(hour=9, minute=30), datetime.combine(trading_date, datetime.min.time()).replace(hour=11, minute=30)),
      (datetime.combine(trading_date, datetime.min.time()).replace(hour=13), datetime.combine(trading_date, datetime.min.time()).replace(hour=15)),
    )
    session_count = [0, 0]
    session_first: list[datetime | None] = [None, None]
    session_last: list[datetime | None] = [None, None]
    previous: list[datetime | None] = [None, None]
    record_count = 0
    continuous_count = 0
    max_gap_seconds = 0.0
    max_gap_start: datetime | None = None
    max_gap_end: datetime | None = None
    for page in self.iter_tick_pages(
      instrument_code=code,
      start_time=query_start,
      end_time=query_end,
      limit=MAX_PAGE_SIZE,
    ):
      record_count += len(page)
      for tick in page:
        timestamp = time_utils.to_shanghai(tick.time)
        for index, (session_start, session_end) in enumerate(sessions):
          if not session_start <= timestamp <= session_end:
            continue
          session_count[index] += 1
          continuous_count += 1
          session_first[index] = session_first[index] or timestamp
          prior = previous[index]
          if prior is not None:
            gap_seconds = (timestamp - prior).total_seconds()
            if gap_seconds > max_gap_seconds:
              max_gap_seconds = gap_seconds
              max_gap_start = prior
              max_gap_end = timestamp
          previous[index] = timestamp
          session_last[index] = timestamp
          break
    reason_codes: list[str] = []
    if not record_count:
      reason_codes.append("NO_TICK_DATA")
    if continuous_count < 120:
      reason_codes.append("TICK_COUNT_TOO_LOW")
    tolerance = timedelta(minutes=5)
    if session_first[0] is None or session_first[0] > sessions[0][0] + tolerance:
      reason_codes.append("SESSION_OPEN_NOT_COVERED")
    if session_last[0] is None or session_last[0] < sessions[0][1] - tolerance:
      reason_codes.append("MORNING_CLOSE_NOT_COVERED")
    if session_first[1] is None or session_first[1] > sessions[1][0] + tolerance:
      reason_codes.append("AFTERNOON_OPEN_NOT_COVERED")
    if session_last[1] is None or session_last[1] < sessions[1][1] - tolerance:
      reason_codes.append("SESSION_CLOSE_NOT_COVERED")
    if max_gap_seconds > timedelta(minutes=15).total_seconds():
      reason_codes.append("CONTINUOUS_SESSION_GAP_TOO_LARGE")
    continuous_first = min((item for item in session_first if item is not None), default=None)
    continuous_last = max((item for item in session_last if item is not None), default=None)
    statistics = {
      "record_count": record_count,
      "continuous_session_record_count": continuous_count,
      "invalid_timestamp_count": 0,
      "first_continuous_time": continuous_first.isoformat() if continuous_first else None,
      "last_continuous_time": continuous_last.isoformat() if continuous_last else None,
      "morning_last_time": session_last[0].isoformat() if session_last[0] else None,
      "afternoon_first_time": session_first[1].isoformat() if session_first[1] else None,
      "max_continuous_gap_seconds": max_gap_seconds,
      "max_continuous_gap_start": max_gap_start.isoformat() if max_gap_start else None,
      "max_continuous_gap_end": max_gap_end.isoformat() if max_gap_end else None,
      "minimum_record_count": 120,
      "maximum_gap_seconds": timedelta(minutes=15).total_seconds(),
      "session_edge_tolerance_seconds": tolerance.total_seconds(),
    }
    return {
      "data_type": "tick",
      "date": trading_date.isoformat(),
      "instrument_code": code,
      "complete": not reason_codes,
      "classification": "COMPLETE" if not reason_codes else ("MISSING" if not record_count else "PARTIAL"),
      "reason_codes": reason_codes,
      "message": (
        "Canonical archive Tick 交易时段覆盖与连续性校验通过"
        if not reason_codes
        else "Canonical archive Tick 交易时段覆盖、记录数或连续性未达到回放最低完整性要求"
      ),
      "statistics": statistics,
    }

  def _iter_window_records(self, *, code: str, start: datetime, end: datetime) -> Iterator[dict[str, Any]]:
    scope = self.cutover.formal_scope
    if code not in scope.instrument_codes:
      raise CanonicalTickArchiveError("ARCHIVE_SELECTED_SCOPE_MISSING_INSTRUMENT_DAY")
    start_day = start.astimezone(_SHANGHAI).date()
    end_day = end.astimezone(_SHANGHAI).date()
    if start_day < scope.trading_dates[0] or end_day > scope.trading_dates[-1]:
      raise CanonicalTickArchiveError("ARCHIVE_READ_WINDOW_OUTSIDE_FORMAL_SCOPE")
    # Formal scope contains trading dates, not calendar dates.  A replay
    # window legitimately crosses weekends/holidays; iterate only declared
    # instrument-days so no nonexistent Saturday/holiday dataset is queried.
    for current in scope.trading_dates:
      if current < start_day or current > end_day:
        continue
      key = InstrumentDay(code, current)
      dataset = self._datasets.get(key)
      if dataset is None:
        raise CanonicalTickArchiveError("ARCHIVE_SELECTED_SCOPE_MISSING_INSTRUMENT_DAY")
      for record in self._iter_dataset_records(key, dataset):
        timestamp = _parse_time(record["time"])
        if start <= timestamp <= end:
          yield record

  def _iter_dataset_records(self, key: InstrumentDay, dataset: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    digest = str(dataset["content_sha256"])
    object_path = _safe_child(self._root, "objects", f"{digest}.ndjson")
    if _sha256_file(object_path) != digest:
      raise CanonicalTickArchiveError("ARCHIVE_OBJECT_CONTENT_HASH_INVALID")
    count = 0
    first: tuple[int, int, int] | None = None
    last: tuple[int, int, int] | None = None
    previous: tuple[int, int, int] | None = None
    try:
      with object_path.open("rb") as handle:
        while line := handle.readline(MAX_LINE_BYTES + 1):
          # See ``_iter_ndjson``: an unbounded iterator read defeats a line
          # size cap when a corrupt object has no newline.
          if len(line) > MAX_LINE_BYTES:
            raise CanonicalTickArchiveError("ARCHIVE_OBJECT_LINE_TOO_LARGE")
          raw = json.loads(line)
          record = _normalize_record(raw, key)
          identity = _record_key(record)
          if previous is not None and identity <= previous:
            raise CanonicalTickArchiveError("ARCHIVE_OBJECT_IDENTITY_INVALID")
          previous = identity
          first = first or identity
          last = identity
          count += 1
          yield record
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
      raise CanonicalTickArchiveError("ARCHIVE_OBJECT_UNREADABLE") from exc
    if count != dataset["record_count"] or list(first or ()) != list(dataset.get("first_source_identity") or ()) or list(last or ()) != list(dataset.get("last_source_identity") or ()):
      raise CanonicalTickArchiveError("ARCHIVE_OBJECT_MANIFEST_MISMATCH")


def _validate_cursor(value: tuple[int, int, int] | None) -> tuple[int, int, int] | None:
  if value is None:
    return None
  if not isinstance(value, tuple) or len(value) != 3:
    raise CanonicalTickArchiveError("ARCHIVE_CURSOR_INVALID")
  generation = _strict_int(value[0], field="continuity_generation")
  source_time = _strict_int(value[1], field="source_time_ms", minimum=1)
  ordinal = _strict_int(value[2], field="tick_ordinal")
  if ordinal >= HISTORICAL_TICK_ORDINALS_PER_MILLISECOND:
    raise CanonicalTickArchiveError("ARCHIVE_CURSOR_INVALID")
  return generation, source_time, ordinal


def _tick_from_record(record: Mapping[str, Any]) -> Tick:
  values = dict(record)
  values["time"] = _parse_time(values["time"])
  return Tick(**values)


@dataclass
class _SequentialPageCursor:
  pages: Iterator[tuple[Tick, ...]]
  expected_offset: int = 0
  exhausted: bool = False


class CanonicalTickArchiveHistoricalAdapter(HistoricalDataAdapter):
  """HistoricalAdapter bridge used only by a registered isolated BACKTEST.

  The Engine's current executor passes naive Shanghai windows. Public archive
  APIs reject naive values; this adapter converts only that known internal
  convention and never constructs a HistoricalMarketDataService or falls back.
  """

  def __init__(self, reader: CanonicalTickArchiveReader):
    DataAdapter.__init__(self, DataMode.HISTORICAL)
    self.reader = reader
    self.current_time: datetime | None = None
    self.replay_tasks: dict[str, Any] = {}
    self.market_data_service = None
    self._page_cursors: dict[tuple[str, datetime, datetime, int], _SequentialPageCursor] = {}
    self._page_lock = asyncio.Lock()

  async def connect(self) -> bool:
    self.is_connected = True
    return True

  async def disconnect(self) -> None:
    self._page_cursors.clear()
    self.is_connected = False

  def validate_runtime_scope(
    self,
    *,
    snapshot_date: date,
    instrument_codes: Iterable[str],
    trading_dates: Iterable[date],
    start_time: datetime,
    end_time: datetime,
  ) -> None:
    start = self._engine_time(start_time)
    end = self._engine_time(end_time)
    self.reader.validate_formal_scope(
      snapshot_date=snapshot_date,
      instrument_codes=instrument_codes,
      trading_dates=trading_dates,
    )
    scope = self.reader.cutover.formal_scope
    if start.astimezone(_SHANGHAI).date() != scope.trading_dates[0] or end.astimezone(_SHANGHAI).date() != scope.trading_dates[-1]:
      raise CanonicalTickArchiveError("ARCHIVE_RUNTIME_WINDOW_SCOPE_MISMATCH")

  async def get_ticks(self, instrument_code: str, start_time: datetime, end_time: datetime | None = None, dividend_type: str = "none", limit: int | None = 1000, order: str = "asc", offset: int = 0) -> list[Tick]:
    if end_time is None or order.lower() != "asc" or dividend_type not in {"none", "front"} or limit is None:
      raise CanonicalTickArchiveError("ARCHIVE_ADAPTER_TICK_QUERY_INVALID")
    if isinstance(offset, bool) or type(offset) is not int or offset < 0:
      raise CanonicalTickArchiveError("ARCHIVE_PAGE_OFFSET_INVALID")
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
      raise CanonicalTickArchiveError("ARCHIVE_PAGE_SIZE_INVALID")
    normalized_code = InstrumentDay.parse(instrument_code, self.reader.cutover.formal_scope.trading_dates[0]).instrument_code
    normalized_start = self._engine_time(start_time)
    normalized_end = self._engine_time(end_time)
    key = (normalized_code, normalized_start, normalized_end, limit)
    async with self._page_lock:
      cursor = self._page_cursors.get(key)
      if offset == 0:
        if cursor is not None and not cursor.exhausted:
          raise CanonicalTickArchiveError("ARCHIVE_ADAPTER_PAGINATION_RESET_OR_CONCURRENT")
        cursor = _SequentialPageCursor(
          pages=self.reader.iter_tick_pages(
            instrument_code=normalized_code,
            start_time=normalized_start,
            end_time=normalized_end,
            limit=limit,
          )
        )
        self._page_cursors[key] = cursor
      elif cursor is None or cursor.exhausted or offset != cursor.expected_offset:
        raise CanonicalTickArchiveError("ARCHIVE_ADAPTER_PAGINATION_OFFSET_UNEXPECTED")
      try:
        page = next(cursor.pages)
      except StopIteration:
        cursor.exhausted = True
        return []
      cursor.expected_offset += len(page)
      if len(page) < limit:
        cursor.exhausted = True
      return [self._engine_tick(tick) for tick in page]

  async def get_klines(self, instrument_code: str, period: str, start_time: datetime, end_time: datetime | None = None, limit: int | None = 1000, order: str = "asc", dividend_type: str = "none") -> list[KLine]:
    raise CanonicalTickArchiveError("ARCHIVE_ADAPTER_KLINE_FALLBACK_FORBIDDEN")

  async def get_latest_price(self, instrument_code: str) -> float | None:
    return None

  @staticmethod
  def _engine_time(value: datetime) -> datetime:
    if not isinstance(value, datetime):
      raise CanonicalTickArchiveError("ARCHIVE_ADAPTER_TIME_INVALID")
    return value.replace(tzinfo=_SHANGHAI) if value.tzinfo is None else time_utils.to_utc(value)

  @staticmethod
  def _engine_tick(tick: Tick) -> Tick:
    values = dict(vars(tick))
    values["time"] = time_utils.to_shanghai(tick.time).replace(tzinfo=None)
    return Tick(**values)


def _parse_record_binding(value: str) -> tuple[InstrumentDay, Path]:
  if "=" not in value or "@" not in value.split("=", 1)[0]:
    raise argparse.ArgumentTypeError("record binding must be CODE@YYYY-MM-DD=PATH")
  left, raw_path = value.split("=", 1)
  code, raw_day = left.split("@", 1)
  try:
    return InstrumentDay.parse(code, raw_day), Path(raw_path)
  except CanonicalTickArchiveError as exc:
    raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description=__doc__)
  subparsers = parser.add_subparsers(dest="command", required=True)
  publish = subparsers.add_parser("publish", help="publish one verified formal 20-day cutover")
  publish.add_argument("--archive-root", type=Path, required=True)
  publish.add_argument("--source-manifest", type=Path, required=True)
  publish.add_argument("--record", type=_parse_record_binding, action="append", required=True, help="CODE@YYYY-MM-DD=NDJSON_PATH; exactly once for each source-manifest pair")
  inspect = subparsers.add_parser("inspect", help="verify and print a cutover")
  inspect.add_argument("--archive-root", type=Path, required=True)
  inspect.add_argument("--cutover-token", required=True)
  return parser


def main(argv: Sequence[str] | None = None) -> int:
  args = build_parser().parse_args(argv)
  archive = CanonicalTickArchive(args.archive_root)
  if args.command == "publish":
    bindings = dict(args.record)
    if len(bindings) != len(args.record):
      raise CanonicalTickArchiveError("ARCHIVE_DUPLICATE_RECORD_BINDING")
    cutover = archive.publish(source_manifest=args.source_manifest, records=bindings)
    print(json.dumps(cutover.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0
  print(json.dumps(archive.open(args.cutover_token).cutover.to_dict(), ensure_ascii=False, sort_keys=True))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
