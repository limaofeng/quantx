"""Detached service launch with durable ambiguity before spawning a process."""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from quantx_infrastructure.training_bundle_store import publication_lock, reject_links
from quantx_infrastructure.training_process_evidence import (
  begin_execution,
  inspect_execution,
  local_exit_recorded,
  record_exit,
  record_spawn,
)

from quantx_trainer.service_status import service_status


def _write(path: Path, value):
  reject_links(path)
  with path.open("x", encoding="utf-8") as stream:
    json.dump(value, stream, sort_keys=True)
    stream.flush()
    os.fsync(stream.fileno())


def _previous_state(root: Path):
  current = root / "current.json"
  reject_links(current)
  if not current.exists():
    return "EXITED"
  try:
    if current.stat().st_size > 1024:
      return "UNKNOWN"
    value = json.loads(current.read_text())
    owner = value["attempt"]
    if not isinstance(owner, str) or not re.fullmatch(r"[a-f0-9]{32}", owner):
      return "UNKNOWN"
    attempt = root / owner
    identity = dict(
      run_id="trainer-service", owner=owner, request=attempt / "request.json"
    )
    evidence = attempt / "process.json"
    if local_exit_recorded(evidence, **identity):
      return "EXITED"
    return inspect_execution(evidence, **identity)
  except (OSError, ValueError, TypeError, KeyError):
    return "UNKNOWN"


def start_service(config, config_path: Path, *, startup_seconds=10.0):
  config_path = config_path.resolve(strict=True)
  root = config.state_root / "service-launches"
  reject_links(root)
  root.mkdir(parents=True, exist_ok=True)
  with publication_lock(root):
    status = service_status(config.state_root, config_path)
    if status["service"] != "OFFLINE":
      return status
    if _previous_state(root) != "EXITED":
      return {"service": "START_PENDING", "execution_state": "NOT_INSPECTED"}
    owner = uuid.uuid4().hex
    attempt = root / owner
    attempt.mkdir()
    request = attempt / "request.json"
    _write(
      request, {"config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest()}
    )
    evidence = attempt / "process.json"
    identity = dict(run_id="trainer-service", owner=owner, request=request)
    begin_execution(evidence, **identity)
    temporary = root / f".current-{owner}.partial"
    _write(temporary, {"attempt": owner})
    reject_links(root / "current.json")
    temporary.replace(root / "current.json")
    options = (
      {"start_new_session": True}
      if sys.platform != "win32"
      else {
        "creationflags": subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP,
      }
    )
    environment = config.child_environment(os.environ)
    environment.pop("DATABASE_URL", None)
    try:
      process = subprocess.Popen(
        [
          sys.executable,
          "-m",
          "quantx_trainer.main",
          "serve",
          "--config",
          str(config_path),
        ],
        cwd=config.code_root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **options,
      )
      record_spawn(evidence, process=process, **identity)
    except BaseException:
      # Even an interrupted spawn may have created a process. Keep STARTING;
      # do not treat a missing handle as permission for another launch.
      raise RuntimeError("TRAINER_SERVICE_LAUNCH_UNCONFIRMED") from None
    deadline = time.monotonic() + startup_seconds
    while True:
      code = process.poll()
      if code is not None:
        record_exit(evidence, returncode=code, **identity)
        return {
          "service": "OFFLINE",
          "reason": "SERVICE_PROCESS_EXITED",
          "execution_state": "NOT_INSPECTED",
        }
      status = service_status(config.state_root, config_path)
      if status["service"] == "ALIVE":
        return status
      if time.monotonic() >= deadline:
        return {"service": "START_PENDING", "execution_state": "NOT_INSPECTED"}
      time.sleep(0.1)
