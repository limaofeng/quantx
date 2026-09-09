"""Local service liveness evidence; never proves task or descendant termination."""

import errno
import hashlib
import json
import os
import platform
import time
import uuid
from pathlib import Path

import psutil
from quantx_infrastructure.training_bundle_store import (
  BundleTransferError,
  publication_lock,
  reject_links,
)

PHASES = {"PREFLIGHT", "REGISTERING", "WORKER_LOOP", "EXITING"}


class ServiceReporter:
  """One instance per held service lease. All paths remain local."""

  def __init__(self, root: Path, config_path: Path):
    self.root = root
    process = psutil.Process()
    self.identity = {
      "schema_version": 1,
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


def service_status(state_root: Path, config_path: Path) -> dict[str, str]:
  result = {"service": "UNKNOWN", "execution_state": "NOT_INSPECTED"}
  root = state_root / "service"
  try:
    reject_links(root)
    if not root.exists():
      return {**result, "service": "OFFLINE"}
    try:
      with publication_lock(root):
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
      or value.get("host") != platform.node()
      or value.get("config_sha256")
      != hashlib.sha256(config_path.read_bytes()).hexdigest()
      or value.get("phase") not in PHASES
      or type(value.get("pid")) is not int
      or type(value.get("updated_at")) not in {int, float}
    ):
      return result
    process = psutil.Process(value["pid"])
    if process.create_time() != value.get("created_at") or os.path.normcase(
      process.exe()
    ) != os.path.normcase(value["executable"]):
      return result
    age = time.time() - value["updated_at"]
    if not 0 <= age <= 30:
      return {**result, "service": "STALE"}
    return {**result, "service": "ALIVE", "phase": value["phase"]}
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
