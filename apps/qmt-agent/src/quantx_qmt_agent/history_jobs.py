"""Durable original requests kept outside the disposable upload spool.

No removal API is provided until the runtime has a durable upload acceptance
and retention decision. Disappearing from WS delivery never deletes these files.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from quantx_contracts.collection_permit import CollectionUnit
from quantx_contracts.history_session import HistoryRequest
from quantx_contracts.history_upload import HistoryUploadSnapshot

from .historical_worker import historical_work_units
from .native_unit_artifact import _ordinary

HISTORY_JOBS_DIRECTORY = "history-jobs"
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def _identity(request: HistoryRequest, device_id: str):
  payloads = historical_work_units(request.payload)
  if len(payloads) != request.unit_count:
    raise ValueError("history request plan count mismatch")
  units = tuple(
    CollectionUnit.from_payload(str(request.request_id), index, payload)
    for index, payload in enumerate(payloads)
  )
  encoded = (
    json.dumps(
      {
        "format": "history-job-v1",
        "device_id": device_id,
        "request_id": str(request.request_id),
        "payload": request.payload,
        "unit_count": request.unit_count,
        "units": [unit.model_dump(mode="json") for unit in units],
      },
      ensure_ascii=False,
      sort_keys=True,
      separators=(",", ":"),
      allow_nan=False,
    )
    + "\n"
  ).encode()
  # The unit identity array is bounded by the canonical 2048-unit planner;
  # keep the manifest bounded independently of transport payload limits.
  if len(encoded) > _MAX_MANIFEST_BYTES:
    raise ValueError("history job manifest exceeds limit")
  return encoded, units


def _sync_directory(path: Path) -> None:
  if os.name != "nt":
    descriptor = os.open(path, os.O_RDONLY)
    try:
      os.fsync(descriptor)
    finally:
      os.close(descriptor)


def retained_history_bytes(
  root: Path, *, max_bytes: int, max_entries: int = 10000, timeout: float = 2
) -> int:
  """Count all retained and incomplete files, failing closed on unsafe paths."""
  _ordinary(root, directory=True)
  directory = root / HISTORY_JOBS_DIRECTORY
  try:
    _ordinary(directory, directory=True)
  except FileNotFoundError:
    return 0
  pending, total, entries = [directory], 0, 0
  deadline = time.monotonic() + timeout
  while pending:
    with os.scandir(pending.pop()) as children:
      for child in children:
        entries += 1
        if entries > max_entries or time.monotonic() >= deadline:
          raise RuntimeError("history file scan budget exceeded")
        path = Path(child.path)
        # lstat-based validation rejects symlinks and Windows reparse points.
        if child.is_dir(follow_symlinks=False):
          _ordinary(path, directory=True)
          pending.append(path)
        else:
          total += _ordinary(path).st_size
          if total > max_bytes:
            raise RuntimeError("history retained byte limit exceeded")
  return total


@dataclass(frozen=True)
class HistoryJob:
  request: HistoryRequest
  directory: Path
  units: tuple[CollectionUnit, ...]

  @property
  def artifacts_directory(self) -> Path:
    return self.directory / "units"


class HistoryJobs:
  def __init__(self, root: Path, *, device_id: str):
    _ordinary(root, directory=True)
    self.root = root.resolve(strict=True)
    self.device_id = device_id

  def request_ids(self) -> tuple[UUID, ...]:
    """Bound discovery before any network operation; never follow linked entries."""
    _ordinary(self.root, directory=True)
    root = self.root / HISTORY_JOBS_DIRECTORY
    try:
      _ordinary(root, directory=True)
    except FileNotFoundError:
      return ()
    result, deadline = [], time.monotonic() + 2
    with os.scandir(root) as children:
      for child in children:
        if len(result) >= 10000 or time.monotonic() >= deadline:
          raise RuntimeError("history request discovery budget exceeded")
        _ordinary(Path(child.path), directory=True)
        request_id = UUID(child.name)
        if str(request_id) != child.name:
          raise ValueError("history request directory identity is not canonical")
        result.append(request_id)
    return tuple(sorted(result))

  def _directory(self, request_id: UUID) -> Path:
    _ordinary(self.root, directory=True)
    root = self.root / HISTORY_JOBS_DIRECTORY
    _ordinary(root, directory=True)
    directory = root / str(UUID(str(request_id)))
    _ordinary(directory, directory=True)
    return directory

  @staticmethod
  def _read(path: Path, limit: int) -> bytes:
    metadata = _ordinary(path)
    if metadata.st_size > limit:
      raise ValueError("history evidence file exceeds limit")
    with path.open("rb") as source:
      if os.fstat(source.fileno()).st_ino != metadata.st_ino:
        raise ValueError("history evidence changed while opening")
      raw = source.read(limit + 1)
    if len(raw) > limit:
      raise ValueError("history evidence file exceeds limit")
    return raw

  def load(self, request_id: UUID) -> HistoryJob:
    """Restore original identity without inventing a server completion cursor."""
    directory = self._directory(request_id)
    raw = self._read(directory / "request.json", _MAX_MANIFEST_BYTES)
    value = json.loads(raw)
    request = HistoryRequest(
      request_id=request_id,
      payload=value["payload"],
      unit_count=value["unit_count"],
      completed_units=0,
    )
    encoded, units = _identity(request, self.device_id)
    if raw != encoded:
      raise ValueError("retained history request identity mismatch")
    _ordinary(directory / "units", directory=True)
    return HistoryJob(request, directory, units)

  def upload_acceptance(self, job: HistoryJob) -> HistoryUploadSnapshot | None:
    directory = self._directory(job.request.request_id)
    try:
      raw = self._read(directory / "upload-accepted.json", 64 * 1024)
    except FileNotFoundError:
      return None
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {
      "format",
      "request_sha256",
      "snapshot",
    }:
      raise ValueError("invalid history upload acceptance")
    identity, _ = _identity(job.request, self.device_id)
    if (
      value["format"] != "history-upload-acceptance-v1"
      or value["request_sha256"] != hashlib.sha256(identity).hexdigest()
    ):
      raise ValueError("history upload acceptance identity mismatch")
    snapshot = HistoryUploadSnapshot.model_validate(value["snapshot"])
    if snapshot.request_id != job.request.request_id or not snapshot.frozen:
      raise ValueError("history upload acceptance is not a frozen original manifest")
    return snapshot

  def record_upload_acceptance(self, job, snapshot, *, reserve, release):
    if snapshot.request_id != job.request.request_id or not snapshot.frozen:
      raise ValueError("history upload acceptance requires frozen original request")
    existing = self.upload_acceptance(job)
    if existing is not None:
      if (
        existing.chunks != snapshot.chunks
        or existing.total_chunks != snapshot.total_chunks
      ):
        raise ValueError("history accepted upload manifest conflict")
      return
    identity, _ = _identity(job.request, self.device_id)
    raw = (
      json.dumps(
        {
          "format": "history-upload-acceptance-v1",
          "request_sha256": hashlib.sha256(identity).hexdigest(),
          "snapshot": snapshot.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
      )
      + "\n"
    ).encode()
    if len(raw) > 64 * 1024:
      raise ValueError("history upload acceptance exceeds limit")
    directory = self._directory(job.request.request_id)
    temporary = directory / f".accepted-{uuid4().hex}.tmp"
    published, charged = False, False
    try:
      with temporary.open("xb") as target:
        reserve(len(raw))
        charged = True
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
      os.link(temporary, directory / "upload-accepted.json")
      published = True
      _sync_directory(directory)
    finally:
      temporary.unlink(missing_ok=True)
      if charged and not published:
        release(len(raw))

  def retain(
    self,
    request: HistoryRequest,
    *,
    reserve: Callable[[int], None],
    release: Callable[[int], None],
  ) -> HistoryJob:
    """Publish immutable identity before any native unit can be authorized.

    Progress is deliberately excluded: only the server advances the cursor.
    An existing request must match exactly, including the full request budget.
    """
    encoded, units = _identity(request, self.device_id)
    _ordinary(self.root, directory=True)
    root = self.root / HISTORY_JOBS_DIRECTORY
    root.mkdir(exist_ok=True)
    _ordinary(root, directory=True)
    directory = root / str(request.request_id)
    directory.mkdir(exist_ok=True)
    _ordinary(directory, directory=True)
    destination = directory / "request.json"
    temporary = directory / f".request-{uuid4().hex}.tmp"
    charged, published = 0, False
    try:
      try:
        _ordinary(destination)
      except FileNotFoundError:
        artifacts = directory / "units"
        if artifacts.exists():
          _ordinary(artifacts, directory=True)
          with os.scandir(artifacts) as children:
            if next(children, None) is not None:
              raise ValueError("history job manifest missing with retained units")
        with temporary.open("xb") as output:
          reserve(len(encoded))
          charged = len(encoded)
          output.write(encoded)
          output.flush()
          os.fsync(output.fileno())
        try:
          os.link(temporary, destination)
          published = True
        except FileExistsError:
          pass
      metadata = _ordinary(destination)
      if metadata.st_size != len(encoded):
        raise ValueError("history job identity conflict")
      with destination.open("rb") as source:
        if os.fstat(source.fileno()).st_ino != metadata.st_ino:
          raise ValueError("history job changed while opening")
        if source.read(len(encoded) + 1) != encoded:
          raise ValueError("history job identity conflict")
      artifacts = directory / "units"
      artifacts.mkdir(exist_ok=True)
      _ordinary(artifacts, directory=True)
      _sync_directory(directory)
      _sync_directory(root)
      _sync_directory(self.root)
      return HistoryJob(request.model_copy(deep=True), directory, units)
    finally:
      temporary.unlink(missing_ok=True)
      if charged and not published:
        release(charged)
