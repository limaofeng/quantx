"""Durable Windows job-exit receipt; never infers database reconciliation."""

import hashlib
import json
import os
import platform
import re
import time
import uuid
from pathlib import Path

from quantx_infrastructure.training_bundle_store import (
  BundleTransferError,
  reject_links,
)


def _read(path: Path):
  reject_links(path)
  if path.stat().st_size > 8192 or path.stat().st_nlink != 1:
    raise ValueError("SERVICE_EXIT_EVIDENCE_INVALID")
  return json.loads(path.read_text(encoding="utf-8"))


def record_group_exit(
  state_root: Path, config_path: Path, instance: str, *, forced: bool
):
  """Called with the verified job handle retained, after its count reaches zero."""
  if not re.fullmatch(r"[a-f0-9]{32}", instance) or type(forced) is not bool:
    raise ValueError("SERVICE_EXIT_IDENTITY_INVALID")
  root = state_root / "service"
  target = root / f"group-exit-{instance}.json"
  reject_links(target)
  value = {
    "schema_version": 1,
    "instance_id": instance,
    "host": platform.node(),
    "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
    "active_processes": 0,
    "forced": forced,
    "observed_at": time.time(),
  }
  if target.exists():
    raise ValueError("SERVICE_EXIT_ALREADY_RECORDED")
  temporary = root / f".exit-{uuid.uuid4().hex}.partial"
  try:
    with temporary.open("x", encoding="utf-8") as stream:
      json.dump(value, stream, sort_keys=True, allow_nan=False)
      stream.flush()
      os.fsync(stream.fileno())
    temporary.replace(target)
  finally:
    temporary.unlink(missing_ok=True)


def confirmed_group_exit(state_root: Path, config_path: Path) -> bool:
  """Only the latest service instance's matching receipt is applicable."""
  try:
    root = state_root / "service"
    current = _read(root / "status.json")
    instance = current["instance_id"]
    if not isinstance(instance, str) or not re.fullmatch(r"[a-f0-9]{32}", instance):
      return False
    value = _read(root / f"group-exit-{instance}.json")
    digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    return (
      set(value)
      == {
        "schema_version",
        "instance_id",
        "host",
        "config_sha256",
        "active_processes",
        "forced",
        "observed_at",
      }
      and type(value["schema_version"]) is int
      and value["schema_version"] == 1
      and value["instance_id"] == instance
      and value["host"] == current.get("host") == platform.node()
      and value["config_sha256"] == current.get("config_sha256") == digest
      and type(value["active_processes"]) is int
      and value["active_processes"] == 0
      and type(value["forced"]) is bool
      and type(value["observed_at"]) in {int, float}
      and 0 < value["observed_at"] <= time.time()
    )
  except (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    BundleTransferError,
  ):
    return False
