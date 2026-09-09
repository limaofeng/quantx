"""Local execution identity for conservative training-process recovery.

EXITED proves the recorded supervisor and main process have stopped. It never releases the
machine resource lock or proves that orphan descendants have been reconciled.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import tempfile
from pathlib import Path
from typing import Literal

import psutil

from quantx_infrastructure.training_bundle_store import reject_links

ProcessState = Literal["LIVE", "EXITED", "UNKNOWN"]


class ProcessEvidenceError(RuntimeError):
  """Redacted local process-evidence error."""


def _request_hash(path: Path) -> str:
  reject_links(path)
  with path.open("rb") as stream:
    return hashlib.file_digest(stream, "sha256").hexdigest()


def _read(path: Path, run_id: str, owner: str, request: Path) -> dict:
  reject_links(path)
  if path.stat().st_size > 16384:
    raise ProcessEvidenceError("PROCESS_EVIDENCE_INVALID")
  value = json.loads(path.read_text(encoding="utf-8"))
  if (
    not isinstance(value, dict)
    or type(value.get("version")) is not int
    or value["version"] != 1
    or value.get("run_id") != run_id
    or not owner
    or value.get("owner") != owner
    or value.get("host") != platform.node()
    or value.get("request_sha256") != _request_hash(request)
    or value.get("state") not in {"STARTING", "RUNNING", "EXITED"}
  ):
    raise ProcessEvidenceError("PROCESS_EVIDENCE_IDENTITY_MISMATCH")
  return value


def begin_execution(path: Path, *, run_id: str, owner: str, request: Path) -> None:
  """Persist uncertainty before launch; never overwrite a prior execution."""
  if not run_id or not owner:
    raise ProcessEvidenceError("PROCESS_EVIDENCE_IDENTITY_REQUIRED")
  try:
    reject_links(path)
    supervisor = psutil.Process()
    value = dict(
      version=1,
      run_id=run_id,
      owner=owner,
      host=platform.node(),
      request_sha256=_request_hash(request),
      state="STARTING",
      supervisor=dict(
        pid=supervisor.pid,
        created_at=supervisor.create_time(),
        executable=supervisor.exe(),
      ),
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
      json.dump(value, stream)
      stream.flush()
      os.fsync(stream.fileno())
  except Exception:
    raise ProcessEvidenceError("PROCESS_EVIDENCE_START_FAILED") from None


def _replace(path: Path, value: dict) -> None:
  reject_links(path)
  descriptor, temporary = tempfile.mkstemp(prefix=".process-", dir=path.parent)
  try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
      json.dump(value, stream)
      stream.flush()
      os.fsync(stream.fileno())
    os.replace(temporary, path)
  finally:
    Path(temporary).unlink(missing_ok=True)


def record_spawn(
  path: Path, *, run_id: str, owner: str, request: Path, process
) -> None:
  try:
    value = _read(path, run_id, owner, request)
    if value["state"] != "STARTING":
      raise ProcessEvidenceError("PROCESS_ALREADY_RECORDED")
    try:
      tracked = psutil.Process(process.pid)
      value.update(
        state="RUNNING",
        pid=tracked.pid,
        created_at=tracked.create_time(),
        executable=tracked.exe(),
      )
    except psutil.NoSuchProcess:
      code = process.poll()
      if code is None:
        raise
      value.update(state="EXITED", returncode=code)
    _replace(path, value)
  except Exception:
    raise ProcessEvidenceError("PROCESS_EVIDENCE_SPAWN_FAILED") from None


def record_exit(
  path: Path, *, run_id: str, owner: str, request: Path, returncode: int
) -> None:
  if type(returncode) is not int:
    raise ProcessEvidenceError("PROCESS_EXIT_NOT_CONFIRMED")
  try:
    value = _read(path, run_id, owner, request)
    value.update(state="EXITED", returncode=returncode)
    _replace(path, value)
  except Exception:
    raise ProcessEvidenceError("PROCESS_EVIDENCE_EXIT_FAILED") from None


def _identity_state(value: dict) -> ProcessState:
  pid, created = value.get("pid"), value.get("created_at")
  if (
    type(pid) is not int
    or pid <= 0
    or type(created) not in {int, float}
    or not math.isfinite(created)
    or created <= 0
  ):
    return "UNKNOWN"
  if not isinstance(value.get("executable"), str) or not value["executable"]:
    return "UNKNOWN"
  try:
    process = psutil.Process(pid)
    if process.create_time() != created:
      return "EXITED"  # A recycled PID does not identify the recorded process.
    if os.path.normcase(process.exe()) != os.path.normcase(value["executable"]):
      return "UNKNOWN"
    return "EXITED" if process.status() == psutil.STATUS_ZOMBIE else "LIVE"
  except psutil.NoSuchProcess:
    return "EXITED"
  except psutil.Error:
    return "UNKNOWN"


def inspect_execution(
  path: Path, *, run_id: str, owner: str, request: Path
) -> ProcessState:
  try:
    value = _read(path, run_id, owner, request)
    supervisor = value.get("supervisor")
    if not isinstance(supervisor, dict):
      return "UNKNOWN"
    owner_state = _identity_state(supervisor)
    if owner_state != "EXITED":
      return owner_state
    if value["state"] == "STARTING":
      return "UNKNOWN"
    if value["state"] == "EXITED":
      return "EXITED" if type(value.get("returncode")) is int else "UNKNOWN"
    return _identity_state(value)
  except Exception:
    return "UNKNOWN"


def inspect_input_preparation(
  path: Path, *, run_id: str, owner: str, request: Path,
) -> ProcessState:
  """Inspect a supervisor-only stage; caller must exclude any compute record."""
  try:
    value = _read(path, run_id, owner, request)
    if value["state"] != "STARTING" or not isinstance(value.get("supervisor"), dict):
      return "UNKNOWN"
    return _identity_state(value["supervisor"])
  except Exception:
    return "UNKNOWN"


def local_success_recorded(path: Path, *, run_id: str, owner: str, request: Path) -> bool:
  """Current supervisor may publish its recorded, successfully exited child."""
  try:
    value = _read(path, run_id, owner, request)
    supervisor = value.get("supervisor")
    return (
      isinstance(supervisor, dict) and supervisor.get("pid") == os.getpid()
      and _identity_state(supervisor) == "LIVE"
      and value["state"] == "EXITED" and type(value.get("returncode")) is int
      and value["returncode"] == 0
    )
  except Exception:
    return False
