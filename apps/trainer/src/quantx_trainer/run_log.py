"""Offline, bounded and redacted access to one training execution's logs."""

import hashlib
import os
import re
import stat
from pathlib import Path

from quantx_infrastructure.training_bundle_store import reject_links

MAX_BYTES = 128 * 1024


def redact_text(value: object) -> str:
  text = str(value or "")
  text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
  text = re.sub(
    r"""(?i)(password|secret|token|credential|api[_ -]?key|authorization)\s*["']?\s*[:=]\s*(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\r\n,;]+)""",
    r"\1=[REDACTED]",
    text,
  )
  text = re.sub(r"(?i)(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\r\n]*", "[PATH]", text)
  text = re.sub(r"(?<![A-Za-z0-9])\\\\[^\r\n]*", "[PATH]", text)
  text = re.sub(r"(?<![A-Za-z0-9])/(?!/)[^\r\n]*", "[PATH]", text)
  return re.sub(r"[\r\n\t]+", " ", text).strip()


def read_run_logs(state_root: Path, run_id: str, *, lines: int = 100) -> list[dict]:
  if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
    raise ValueError("RUN_LOG_ID_INVALID")
  if not 1 <= lines <= 1000:
    raise ValueError("RUN_LOG_LINES_INVALID")
  root = state_root / "control" / run_id
  return _read_logs(root, {"run_id": run_id}, lines=lines)


def read_preparation_logs(
  state_root: Path, job_id: str, owner: str, *, lines: int = 100
) -> list[dict]:
  if not all(
    re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value)
    for value in (job_id, owner)
  ):
    raise ValueError("PREPARATION_LOG_ID_INVALID")
  if not 1 <= lines <= 1000:
    raise ValueError("RUN_LOG_LINES_INVALID")
  attempt = hashlib.sha256(owner.encode()).hexdigest()
  return _read_logs(
    state_root / "preparation" / job_id / attempt,
    {"job_id": job_id, "owner": owner},
    lines=lines,
  )


def _read_logs(root: Path, identity: dict[str, str], *, lines: int) -> list[dict]:
  reject_links(root)
  if not root.is_dir():
    raise ValueError("RUN_LOG_NOT_FOUND")
  result = []
  for name in ("stdout", "stderr"):
    path = root / f"{name}.log"
    reject_links(path)
    if not path.exists():
      continue
    before = path.stat()
    if before.st_nlink != 1 or not stat.S_ISREG(before.st_mode):
      raise ValueError("RUN_LOG_FILE_INVALID")
    with path.open("rb") as source:
      opened = os.fstat(source.fileno())
      if (before.st_dev, before.st_ino) != (
        opened.st_dev,
        opened.st_ino,
      ) or opened.st_nlink != 1:
        raise ValueError("RUN_LOG_FILE_CHANGED")
      offset = max(0, opened.st_size - MAX_BYTES)
      source.seek(offset)
      raw = source.read(MAX_BYTES)
    if offset:
      # Never expose a secret suffix whose field name was outside the read window.
      raw = raw.partition(b"\n")[2]
    messages = raw.decode("utf-8", errors="replace").splitlines()[-lines:]
    result.extend(
      {**identity, "stream": name, "message": redact_text(message)}
      for message in messages
    )
  return result
