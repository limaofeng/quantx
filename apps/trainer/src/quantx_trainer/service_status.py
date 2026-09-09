"""Local service liveness evidence; never proves task or descendant termination."""

import errno
import hashlib
import json
import math
import os
import platform
import re
import time
import uuid
from pathlib import Path
from typing import Any

import psutil
from quantx_infrastructure.training_bundle_store import (
  BundleTransferError,
  publication_lock,
  reject_links,
)

PHASES = {"PREFLIGHT", "REGISTERING", "WORKER_LOOP", "STOPPING", "EXITING"}


def _code_identity() -> dict[str, Any]:
  from quantx_research.packaged_source import packaged_source_state

  state = packaged_source_state(Path(__file__).resolve().parents[4])
  if state is None:
    return {"commit": None, "manifest_sha256": None, "dirty": None}
  return {
    "commit": state["commit"],
    "manifest_sha256": state["code_manifest_sha256"],
    "dirty": state["dirty"],
  }


def _valid_code_identity(value) -> bool:
  if not isinstance(value, dict) or set(value) != {"commit", "manifest_sha256", "dirty"}:
    return False
  if all(item is None for item in value.values()):
    return True
  return (
    isinstance(value["commit"], str)
    and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value["commit"]) is not None
    and isinstance(value["manifest_sha256"], str)
    and re.fullmatch(r"[0-9a-f]{64}", value["manifest_sha256"]) is not None
    and type(value["dirty"]) is bool
  )


class ServiceReporter:
  """One instance per held service lease. All paths remain local."""

  def __init__(self, root: Path, config_path: Path):
    self.root = root
    process = psutil.Process()
    self.identity = {
      "schema_version": 1,
      "code": _code_identity(),
      "instance_id": uuid.uuid4().hex,
      "host": platform.node(),
      "pid": process.pid,
      "created_at": process.create_time(),
      "executable": process.exe(),
      "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
    }
    self.phase = "PREFLIGHT"
    self.logged_phase = None

  def write(self, phase=None):
    if phase is not None:
      if phase not in PHASES:
        raise ValueError("SERVICE_PHASE_INVALID")
      self.phase = phase
    target = self.root / "status.json"
    reject_links(target)
    temporary = self.root / f".status-{self.identity['instance_id']}.partial"
    reject_links(temporary)
    payload = {**self.identity, "phase": self.phase, "updated_at": time.time()}
    try:
      with temporary.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
      temporary.replace(target)
      if self.logged_phase != self.phase:
        self.event(self.phase)
        self.logged_phase = self.phase
    finally:
      temporary.unlink(missing_ok=True)

  def event(self, event):
    from quantx_trainer.service_log import append_event

    append_event(self.root, self.identity["instance_id"], event)


def service_status(state_root: Path, config_path: Path) -> dict[str, Any]:
  result = {"service": "UNKNOWN", "execution_state": "NOT_INSPECTED"}
  root = state_root / "service"
  try:
    reject_links(root)
    if not root.exists():
      return {**result, "service": "OFFLINE"}
    try:
      with publication_lock(root):
        from quantx_trainer.service_exit import confirmed_group_exit

        if confirmed_group_exit(state_root, config_path):
          return {"service": "OFFLINE", "execution_state": "GROUP_EXITED", "database_state": "NOT_RECONCILED"}
        return {**result, "service": "OFFLINE"}
    except OSError as exc:
      if exc.errno not in {errno.EAGAIN, errno.EACCES}:
        return result
    path = root / "status.json"
    reject_links(path)
    if path.stat().st_size > 8192:
      return result
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
      value.get("schema_version") != 1
      or not _valid_code_identity(value.get("code"))
      or value.get("host") != platform.node()
      or value.get("config_sha256")
      != hashlib.sha256(config_path.read_bytes()).hexdigest()
      or value.get("phase") not in PHASES
      or not isinstance(value.get("instance_id"), str)
      or not re.fullmatch(r"[a-f0-9]{32}", value["instance_id"])
      or type(value.get("pid")) is not int
      or type(value.get("updated_at")) not in {int, float}
      or not math.isfinite(value["updated_at"])
    ):
      return result
    process = psutil.Process(value["pid"])
    if process.create_time() != value.get("created_at") or os.path.normcase(
      process.exe()
    ) != os.path.normcase(value["executable"]):
      return result
    age = time.time() - value["updated_at"]
    return {
      **result,
      "service": "ALIVE" if 0 <= age <= 30 else "STALE",
      "phase": value["phase"],
      "code": value["code"],
      "heartbeat_at": value["updated_at"],
      "instance_id": value["instance_id"],
    }
  except (
    OSError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    psutil.Error,
    BundleTransferError,
  ):
    return result
