"""Best-effort display of actual dispatcher decisions, never execution authority."""

import hashlib
import json
import logging
import math
import os
import re
import stat
import time
import uuid
from pathlib import Path

from quantx_infrastructure.training_bundle_store import reject_links

KINDS = {"training", "preparation"}
STATUSES = {
  "IDLE",
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
  "OWNERSHIP_LOST",
}
REASONS = {
  "CPU_TRAINING_UNAVAILABLE",
  "HOST_POLICY_MISSING_OR_INVALID",
  "HOST_CLOCK_UNKNOWN",
  "TRADING_OR_POST_CLOSE_CRITICAL_WINDOW",
  "OUTSIDE_ALLOWED_TRAINING_WINDOW",
  "HOST_MEMORY_RESERVE",
  "HOST_DISK_RESERVE",
  "TASK_MEMORY_BUDGET",
  "TASK_CPU_BUDGET",
  "HOST_RESOURCE_STATE_UNKNOWN",
  "HOST_GPU_MEMORY_STATE_UNKNOWN",
  "HOST_GPU_MEMORY_BUDGET",
  "TRAINER_DRAINING",
  "TRAINER_ADMISSION_UNAVAILABLE",
  "NO_QUEUED_RUN_OR_RUNNING_LIMIT",
  "HOST_ADMISSION_DENIED",
  "PREPARATION_STOP_UNCONFIRMED",
  "PREPARATION_RESULT_REGISTRATION_PENDING",
  "TRAINER_PROCESS_STOP_UNCONFIRMED",
  "TRAINER_FAILURE_REGISTRATION_PENDING",
  "RESULT_INVENTORY_MISSING",
  "RESULT_INVENTORY_INVALID",
  "RESULT_REQUIRED_ARTIFACT_MISSING",
  "PUBLICATION_EVIDENCE_CONFLICT",
  "PUBLICATION_IDENTITY_INVALID",
  "PUBLICATION_OWNERSHIP_LOST",
  "PUBLICATION_EXECUTION_NOT_STOPPED",
  "PUBLICATION_EXECUTION_NOT_SUCCESSFUL",
  "RESULT_SPEC_MISMATCH",
  "PUBLICATION_REMOTE_IDENTITY_MISMATCH",
  "PUBLICATION_RETRY_REQUIRED",
  "PUBLICATION_DATABASE_UNAVAILABLE",
}


def _project(value):
  status = value.get("status")
  reason = value.get("reason")
  if status not in STATUSES or (reason is not None and reason not in REASONS):
    raise ValueError("TRAINER_DISPATCH_EVIDENCE_INVALID")
  result = {"status": status, "reason": reason}
  for key in ("run_id", "job_id"):
    identifier = value.get(key)
    if identifier is not None:
      if not isinstance(identifier, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", identifier
      ):
        raise ValueError("TRAINER_DISPATCH_EVIDENCE_INVALID")
      result[key] = identifier
  return result


class DispatchObservation:
  def __init__(self, config_path: Path, kind: str):
    if kind not in KINDS:
      raise ValueError("TRAINER_DISPATCH_KIND_INVALID")
    self.config_path, self.kind = config_path, kind
    try:
      self.digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    except OSError:
      self.digest = None

  def record(self, result):
    # Failure to write display metadata must not strand an already claimed run.
    try:
      from quantx_trainer.runtime import current_config

      decision = _project(result)
      if (
        self.digest is None
        or hashlib.sha256(self.config_path.read_bytes()).hexdigest() != self.digest
      ):
        raise ValueError("TRAINER_DISPATCH_CONFIG_CHANGED")
      root = current_config().state_root / "observations"
      reject_links(root)
      root.mkdir(parents=True, exist_ok=True)
      target = root / f"{self.kind}-dispatch.json"
      reject_links(target)
      temporary = root / f".dispatch-{uuid.uuid4().hex}.partial"
      try:
        with temporary.open("x", encoding="utf-8") as stream:
          json.dump(
            {
              "schema_version": 1,
              "kind": self.kind,
              "config_sha256": self.digest,
              "observed_at": time.time(),
              "decision": decision,
            },
            stream,
            allow_nan=False,
          )
          stream.flush()
          os.fsync(stream.fileno())
        temporary.replace(target)
      finally:
        temporary.unlink(missing_ok=True)
    except Exception:
      logging.getLogger(__name__).warning("TRAINER_DISPATCH_OBSERVATION_UNAVAILABLE")
    return result


def read_dispatch_status(root: Path, config: Path, kind: str, *, now=None):
  unknown = {"state": "UNKNOWN"}
  if kind not in KINDS:
    return unknown
  path = root / "observations" / f"{kind}-dispatch.json"
  try:
    reject_links(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 8192 or info.st_nlink != 1:
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
      value.get("kind") != kind
      or type(value["schema_version"]) is not int
      or value["schema_version"] != 1
      or type(observed) not in (int, float)
      or not math.isfinite(observed)
      or value["config_sha256"] != hashlib.sha256(config.read_bytes()).hexdigest()
    ):
      return unknown
    decision = _project(value["decision"])
    age = (time.time() if now is None else now) - observed
    if not math.isfinite(age) or age < 0:
      return unknown
    if age > 90:
      return {"state": "STALE", "observed_at": observed}
    return {"state": "FRESH", "observed_at": observed, "decision": decision}
  except Exception:
    return unknown
