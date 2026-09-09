"""Immutable native-unit results, sealed before reporting collection completion.

A receipt is not data. Callers retain this artifact until the original request's
upload is durably accepted, and bind its digest to their execution journal.
This module never acquires a permit, starts XTData, or retries a native call.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quantx_contracts.collection_permit import CollectionUnit


@dataclass(frozen=True)
class NativeUnitArtifact:
  unit: CollectionUnit
  path: Path
  sha256: str
  byte_count: int
  record_count: int


def _line(value: dict[str, Any]) -> bytes:
  return (
    json.dumps(
      value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    + "\n"
  ).encode("utf-8")


def _ordinary(path: Path, *, directory: bool = False) -> os.stat_result:
  metadata = path.lstat()
  if (
    stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400
  ):
    raise ValueError("native unit artifact contains a linked path")
  if not (
    stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
  ):
    raise ValueError("native unit artifact has an unexpected file type")
  return metadata


class NativeUnitArtifacts:
  def __init__(
    self, root: Path, *, max_bytes: int, max_record_bytes: int, max_records: int
  ):
    if min(max_bytes, max_record_bytes, max_records) <= 0:
      raise ValueError("native unit artifact requires positive limits")
    _ordinary(root, directory=True)
    self.root = root.resolve(strict=True)
    self.max_bytes = max_bytes
    self.max_record_bytes = max_record_bytes
    self.max_records = max_records

  def _path(self, unit: CollectionUnit) -> Path:
    _ordinary(self.root, directory=True)
    return self.root / f"{unit.unit_id}.jsonl"

  def inspect(
    self, unit: CollectionUnit, *, expected_sha256: str | None = None
  ) -> NativeUnitArtifact:
    """Validate the full file before allowing its records to be replayed."""
    path = self._path(unit)
    metadata = _ordinary(path)
    if metadata.st_size > self.max_bytes:
      raise ValueError("native unit artifact exceeds byte limit")
    with path.open("rb") as source:
      if os.fstat(source.fileno()).st_ino != metadata.st_ino:
        raise ValueError("native unit artifact changed while opening")
      artifact = self._inspect_source(source, path, unit)
    if expected_sha256 is not None and artifact.sha256 != expected_sha256:
      raise ValueError("native unit artifact does not match journal digest")
    return artifact

  def confirm_publication(self) -> None:
    """Retry directory durability before binding a recovered publication."""
    _ordinary(self.root, directory=True)
    if os.name != "nt":
      directory = os.open(self.root, os.O_RDONLY)
      try:
        os.fsync(directory)
      finally:
        os.close(directory)

  def _inspect_source(self, source, path, unit):
    digest, records_digest = hashlib.sha256(), hashlib.sha256()
    total = count = 0
    complete = False
    for index in range(self.max_records + 2):
      raw = source.readline(max(4096, self.max_record_bytes + 1))
      if not raw or not raw.endswith(b"\n"):
        raise ValueError("native unit artifact is incomplete")
      total += len(raw)
      if total > self.max_bytes:
        raise ValueError("native unit artifact exceeds byte limit")
      digest.update(raw)
      try:
        value = json.loads(raw)
      except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("native unit artifact contains invalid JSON") from exc
      if index == 0:
        if raw != _line(
          {"format": "native-unit-v1", "unit": unit.model_dump(mode="json")}
        ):
          raise ValueError("native unit artifact identity mismatch")
      elif isinstance(value, dict) and set(value) == {"record"}:
        if not isinstance(value["record"], dict) or len(raw) > self.max_record_bytes:
          raise ValueError("native unit artifact record is invalid or oversized")
        # Reject non-canonical/NaN data even when an external file has a footer.
        if raw != _line(value):
          raise ValueError("native unit artifact record is not canonical")
        count += 1
        if count > self.max_records:
          raise ValueError("native unit artifact exceeds record limit")
        records_digest.update(raw)
      elif value == {
        "complete": {
          "record_count": count,
          "records_sha256": records_digest.hexdigest(),
        }
      }:
        if raw != _line(
          {
            "complete": {
              "record_count": count,
              "records_sha256": records_digest.hexdigest(),
            }
          }
        ):
          raise ValueError("native unit artifact completion is not canonical")
        if source.read(1):
          raise ValueError("native unit artifact has trailing data")
        complete = True
        break
      else:
        raise ValueError("native unit artifact completion proof mismatch")
    if not complete:
      raise ValueError("native unit artifact lacks a completion proof")
    return NativeUnitArtifact(unit, path, digest.hexdigest(), total, count)

  def replay(self, artifact: NativeUnitArtifact) -> Iterator[dict[str, Any]]:
    """Verify the journal-bound artifact and replay without invoking the broker."""
    path = self._path(artifact.unit)
    if path != artifact.path:
      raise ValueError("native unit artifact path mismatch")
    metadata = _ordinary(path)
    with path.open("rb") as source:
      if os.fstat(source.fileno()).st_ino != metadata.st_ino:
        raise ValueError("native unit artifact changed while opening")
      verified = self._inspect_source(source, path, artifact.unit)
      if verified != artifact:
        raise ValueError("native unit artifact changed after completion")
      source.seek(0)
      source.readline()
      for _ in range(artifact.record_count):
        yield json.loads(source.readline(self.max_record_bytes + 1))["record"]

  def seal(
    self,
    unit: CollectionUnit,
    records: Iterable[dict[str, Any]],
    *,
    reserve: Callable[[int], None],
    release: Callable[[int], None],
  ) -> NativeUnitArtifact:
    """Consume one already-authorized call and atomically publish its result.

    Quota is charged before each write, including header/footer. A failed or
    duplicate publication releases its temporary bytes; a published file keeps
    its charge. Existing artifacts are never overwritten, even by a retry.
    """
    destination = self._path(unit)
    temporary = self.root / f".{unit.unit_id}.{uuid.uuid4().hex}.tmp"
    total = count = 0
    digest, records_digest = hashlib.sha256(), hashlib.sha256()
    published = False
    try:
      with temporary.open("xb") as target:

        def write(raw):
          nonlocal total
          if total + len(raw) > self.max_bytes:
            raise ValueError("native unit artifact exceeds byte limit")
          reserve(len(raw))
          total += len(raw)
          target.write(raw)
          digest.update(raw)

        write(_line({"format": "native-unit-v1", "unit": unit.model_dump(mode="json")}))
        for record in records:
          if not isinstance(record, dict):
            raise ValueError("native unit record must be an object")
          raw = _line({"record": record})
          if len(raw) > self.max_record_bytes:
            raise ValueError("native unit artifact record is oversized")
          count += 1
          if count > self.max_records:
            raise ValueError("native unit artifact exceeds record limit")
          write(raw)
          records_digest.update(raw)
        write(
          _line(
            {
              "complete": {
                "record_count": count,
                "records_sha256": records_digest.hexdigest(),
              }
            }
          )
        )
        target.flush()
        os.fsync(target.fileno())
      try:
        # Hard-link publication is atomic and cannot overwrite a prior result.
        os.link(temporary, destination)
        published = True
      except FileExistsError:
        return self.inspect(unit, expected_sha256=digest.hexdigest())
      self.confirm_publication()
      return NativeUnitArtifact(unit, destination, digest.hexdigest(), total, count)
    finally:
      temporary.unlink(missing_ok=True)
      if not published:
        release(total)
