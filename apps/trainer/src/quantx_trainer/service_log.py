"""Bounded structured lifecycle log with no arbitrary text or configuration."""

import json
import os
import re
import time
from pathlib import Path

from quantx_infrastructure.training_bundle_store import reject_links

EVENTS = {
  "PREFLIGHT",
  "REGISTERING",
  "WORKER_LOOP",
  "EXITING",
  "SERVICE_FAILED",
  "SERVICE_INTERRUPTED",
}
MAX_BYTES = 1024 * 1024
BACKUPS = 3


def append_event(root: Path, instance_id: str, event: str) -> None:
  """Caller holds the service lease. Rotate a fixed number of bounded files."""
  if event not in EVENTS or not re.fullmatch(r"[a-f0-9]{32}", instance_id):
    raise ValueError("SERVICE_LOG_EVENT_INVALID")
  root.mkdir(parents=True, exist_ok=True)
  paths = [root / "events.jsonl"] + [
    root / f"events.jsonl.{i}" for i in range(1, BACKUPS + 1)
  ]
  for path in paths:
    reject_links(path)
    if path.exists() and path.stat().st_nlink != 1:
      raise ValueError("SERVICE_LOG_LINK_FORBIDDEN")
  payload = (
    json.dumps(
      {"timestamp": time.time(), "instance_id": instance_id, "event": event},
      sort_keys=True,
    )
    + "\n"
  )
  if paths[0].exists() and paths[0].stat().st_size + len(payload.encode()) > MAX_BYTES:
    paths[-1].unlink(missing_ok=True)
    for index in range(BACKUPS - 1, -1, -1):
      if paths[index].exists():
        paths[index].replace(paths[index + 1])
  with paths[0].open("a", encoding="utf-8") as stream:
    stream.write(payload)
    stream.flush()
    os.fsync(stream.fileno())


def read_events(state_root: Path, *, lines: int = 100) -> list[dict]:
  if not 1 <= lines <= 1000:
    raise ValueError("SERVICE_LOG_LINES_INVALID")
  result = []
  root = state_root / "service"
  for index in range(BACKUPS, -1, -1):
    path = root / ("events.jsonl" if index == 0 else f"events.jsonl.{index}")
    reject_links(path)
    if not path.exists():
      continue
    if path.stat().st_nlink != 1:
      raise ValueError("SERVICE_LOG_LINK_FORBIDDEN")
    with path.open("rb") as stream:
      size = stream.seek(0, os.SEEK_END)
      start = max(0, size - MAX_BYTES)
      stream.seek(start)
      if start:
        stream.readline(MAX_BYTES)
      raw = stream.read(MAX_BYTES)
    for line in raw.splitlines():
      try:
        record = json.loads(line)
        if (
          set(record) != {"timestamp", "instance_id", "event"}
          or type(record["timestamp"]) not in {int, float}
          or not 0 < record["timestamp"] < float("inf")
          or record["event"] not in EVENTS
          or not isinstance(record["instance_id"], str)
          or not re.fullmatch(r"[a-f0-9]{32}", record["instance_id"])
        ):
          raise ValueError("invalid event")
      except (ValueError, TypeError, KeyError):
        raise ValueError("SERVICE_LOG_CORRUPT") from None
      result.append(record)
    result = result[-lines:]
  return result
