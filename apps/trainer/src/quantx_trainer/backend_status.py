"""Offline display of the last successfully persisted capability heartbeat."""

import hashlib
import json
import math
import os
import re
import stat
import time
import uuid
from pathlib import Path

from quantx_infrastructure.training_bundle_store import reject_links

STATUSES = {
  "CPU_AVAILABLE",
  "GPU_AVAILABLE",
  "GPU_UNAVAILABLE_BUILD",
  "GPU_UNAVAILABLE_RUNTIME",
  "GPU_INSUFFICIENT_MEMORY",
  "GPU_UNQUALIFIED",
}
MAX_AGE_SECONDS = 90


def _projection(details):
  status = details.get("status")
  gpu = details.get("gpu_status", status if status != "CPU_AVAILABLE" else None)
  if (
    status not in STATUSES
    or gpu not in (STATUSES - {"CPU_AVAILABLE"}) | {None}
    or type(details.get("cpu_available")) is not bool
  ):
    raise ValueError("TRAINER_BACKEND_EVIDENCE_INVALID")
  digest = details.get("environment_requirement_hash")
  if digest is not None and (
    not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
  ):
    raise ValueError("TRAINER_BACKEND_EVIDENCE_INVALID")
  return {
    "status": status,
    "gpu_status": gpu,
    "cpu_available": details["cpu_available"],
    "environment_requirement_hash": digest,
  }


def write_backend_status(
  state_root: Path,
  config_path: Path,
  details,
  *,
  observed_at: float,
  expected_config_sha256: str,
):
  if not math.isfinite(observed_at):
    raise ValueError("TRAINER_BACKEND_EVIDENCE_INVALID")
  current_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
  if current_digest != expected_config_sha256:
    raise ValueError("TRAINER_BACKEND_CONFIG_CHANGED")
  payload = {
    "schema_version": 1,
    "observed_at": observed_at,
    "config_sha256": expected_config_sha256,
    "capability": _projection(details),
  }
  root = state_root / "observations"
  reject_links(root)
  root.mkdir(parents=True, exist_ok=True)
  target = root / "backend.json"
  reject_links(target)
  temporary = root / f".backend-{uuid.uuid4().hex}.partial"
  try:
    with temporary.open("x", encoding="utf-8") as stream:
      json.dump(payload, stream, allow_nan=False, sort_keys=True)
      stream.flush()
      os.fsync(stream.fileno())
    temporary.replace(target)
  finally:
    temporary.unlink(missing_ok=True)


def read_backend_status(state_root: Path, config_path: Path, *, now=None):
  unknown = {"state": "UNKNOWN"}
  path = state_root / "observations/backend.json"
  try:
    reject_links(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 8192:
      return unknown
    with path.open("rb") as stream:
      opened = os.fstat(stream.fileno())
      if (opened.st_dev, opened.st_ino, opened.st_nlink) != (
        info.st_dev,
        info.st_ino,
        1,
      ):
        return unknown
      raw = stream.read(8193)
    if len(raw) > 8192:
      return unknown
    value = json.loads(raw)
    observed = value["observed_at"]
    if (
      type(value["schema_version"]) is not int
      or value["schema_version"] != 1
      or type(observed) not in (int, float)
      or not math.isfinite(observed)
      or value["config_sha256"] != hashlib.sha256(config_path.read_bytes()).hexdigest()
    ):
      return unknown
    capability = _projection(value["capability"])
    age = (time.time() if now is None else now) - observed
    if not math.isfinite(age) or age < 0:
      return unknown
    if age > MAX_AGE_SECONDS:
      return {"state": "STALE", "observed_at": observed}
    return {"state": "FRESH", "observed_at": observed, "capability": capability}
  except Exception:
    return unknown
