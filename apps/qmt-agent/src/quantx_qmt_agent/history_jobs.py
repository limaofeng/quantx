"""Durable original requests kept outside the disposable upload spool.

No removal API is provided until the runtime has a durable upload acceptance
and retention decision. Disappearing from WS delivery never deletes these files.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from quantx_contracts.collection_permit import CollectionUnit
from quantx_contracts.history_session import HistoryRequest

from .historical_worker import historical_work_units
from .native_unit_artifact import _ordinary

HISTORY_JOBS_DIRECTORY = "history-jobs"
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024


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
          "device_id": self.device_id,
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
