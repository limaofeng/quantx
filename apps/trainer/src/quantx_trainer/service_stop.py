"""Instance-fenced cooperative stop requests; no PID-based signalling."""

import json
import os
import re
import uuid
from pathlib import Path

from quantx_infrastructure.training_bundle_store import reject_links


def _path(root: Path, instance_id: str):
  if not re.fullmatch(r"[a-f0-9]{32}", instance_id):
    raise ValueError("SERVICE_INSTANCE_INVALID")
  path = root / f"stop-{instance_id}.json"
  reject_links(path)
  return path


def stop_requested(root: Path, instance_id: str) -> bool:
  path = _path(root, instance_id)
  if not path.exists():
    return False
  if path.stat().st_size > 1024 or path.stat().st_nlink != 1:
    raise ValueError("SERVICE_STOP_REQUEST_INVALID")
  value = json.loads(path.read_text(encoding="utf-8"))
  if value != {"instance_id": instance_id, "stop": True} or value["stop"] is not True:
    raise ValueError("SERVICE_STOP_REQUEST_INVALID")
  return True


def request_stop(root: Path, instance_id: str):
  path = _path(root, instance_id)
  if stop_requested(root, instance_id):
    return
  temporary = root / f".stop-{uuid.uuid4().hex}.partial"
  try:
    with temporary.open("x", encoding="utf-8") as stream:
      json.dump({"instance_id": instance_id, "stop": True}, stream)
      stream.flush()
      os.fsync(stream.fileno())
    temporary.replace(path)
  finally:
    temporary.unlink(missing_ok=True)
