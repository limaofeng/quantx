"""Offline display of persisted capability and bounded control-plane activity."""

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

from quantx_trainer.run_log import redact_text

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


def _activity_projection(value):
  if (
    not isinstance(value, dict)
    or type(value.get("truncated")) is not bool
    or not isinstance(value.get("tasks"), list)
    or len(value["tasks"]) > 50
  ):
    raise ValueError("TRAINER_ACTIVITY_EVIDENCE_INVALID")
  tasks = []
  seen = set()
  for row in value["tasks"]:
    category, kind = row["type"], row["kind"]
    allowed = {
      "TRAINING": {"DEVELOPMENT", "FINAL_EVALUATION"},
      "PREPARATION": {"GPU", "CERTIFY"},
    }
    if (
      category not in allowed
      or kind not in allowed[category]
      or row["status"] not in {"RUNNING", "QUEUED"}
    ):
      raise ValueError("TRAINER_ACTIVITY_EVIDENCE_INVALID")
    if (
      not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", row["id"])
      or not isinstance(row["phase"], str)
      or len(row["phase"]) > 512
    ):
      raise ValueError("TRAINER_ACTIVITY_EVIDENCE_INVALID")
    key = category, row["id"]
    if key in seen:
      raise ValueError("TRAINER_ACTIVITY_EVIDENCE_INVALID")
    seen.add(key)
    for field in ("completed_units", "total_units"):
      number = row[field]
      if category == "TRAINING" and (
        type(number) is not int or not 0 <= number <= 10**9
      ):
        raise ValueError("TRAINER_ACTIVITY_EVIDENCE_INVALID")
      if category == "PREPARATION" and number is not None:
        raise ValueError("TRAINER_ACTIVITY_EVIDENCE_INVALID")
    task = {
      key: row[key]
      for key in ("type", "id", "kind", "status", "completed_units", "total_units")
    }
    task["phase"] = redact_text(row["phase"])[:64]
    tasks.append(task)
  return {"tasks": tasks, "truncated": value["truncated"]}


def write_backend_status(
  state_root: Path,
  config_path: Path,
  details,
  activity,
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
    "activity": _activity_projection(activity),
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
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
      return unknown
    with path.open("rb") as stream:
      opened = os.fstat(stream.fileno())
      if (opened.st_dev, opened.st_ino, opened.st_nlink) != (
        info.st_dev,
        info.st_ino,
        1,
      ):
        return unknown
      raw = stream.read(65537)
    if len(raw) > 65536:
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
    activity = _activity_projection(value["activity"])
    age = (time.time() if now is None else now) - observed
    if not math.isfinite(age) or age < 0:
      return unknown
    if age > MAX_AGE_SECONDS:
      return {"state": "STALE", "observed_at": observed}
    return {
      "state": "FRESH",
      "observed_at": observed,
      "capability": capability,
      "activity": activity,
    }
  except Exception:
    return unknown
