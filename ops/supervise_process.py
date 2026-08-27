"""Cross-platform restart supervisor for QuantX managed children."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO

BACKOFF_SECONDS = (1, 2, 5, 10, 30)
STABLE_RESET_SECONDS = 300
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
DEFAULT_MAX_LOG_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_BACKUPS = 5


class _IoCounters(ctypes.Structure):
  _fields_ = [
    (name, ctypes.c_ulonglong)
    for name in (
      "ReadOperationCount",
      "WriteOperationCount",
      "OtherOperationCount",
      "ReadTransferCount",
      "WriteTransferCount",
      "OtherTransferCount",
    )
  ]


class _BasicLimitInformation(ctypes.Structure):
  _fields_ = [
    ("PerProcessUserTimeLimit", ctypes.c_longlong),
    ("PerJobUserTimeLimit", ctypes.c_longlong),
    ("LimitFlags", ctypes.c_uint32),
    ("MinimumWorkingSetSize", ctypes.c_size_t),
    ("MaximumWorkingSetSize", ctypes.c_size_t),
    ("ActiveProcessLimit", ctypes.c_uint32),
    ("Affinity", ctypes.c_size_t),
    ("PriorityClass", ctypes.c_uint32),
    ("SchedulingClass", ctypes.c_uint32),
  ]


class _ExtendedLimitInformation(ctypes.Structure):
  _fields_ = [
    ("BasicLimitInformation", _BasicLimitInformation),
    ("IoInfo", _IoCounters),
    ("ProcessMemoryLimit", ctypes.c_size_t),
    ("JobMemoryLimit", ctypes.c_size_t),
    ("PeakProcessMemoryUsed", ctypes.c_size_t),
    ("PeakJobMemoryUsed", ctypes.c_size_t),
  ]


def _create_kill_on_close_job() -> int | None:
  if os.name != "nt":
    return None
  kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
  kernel32.CreateJobObjectW.restype = ctypes.c_void_p
  handle = kernel32.CreateJobObjectW(None, None)
  if not handle:
    raise ctypes.WinError(ctypes.get_last_error())
  information = _ExtendedLimitInformation()
  information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
  if not kernel32.SetInformationJobObject(
    ctypes.c_void_p(handle),
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
    ctypes.byref(information),
    ctypes.sizeof(information),
  ):
    raise ctypes.WinError(ctypes.get_last_error())
  return int(handle)


def _assign_to_job(job: int | None, process: subprocess.Popen[bytes]) -> None:
  if job is None:
    return
  kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
  if not kernel32.AssignProcessToJobObject(
    ctypes.c_void_p(job),
    ctypes.c_void_p(int(process._handle)),
  ):
    raise ctypes.WinError(ctypes.get_last_error())


def _write_state(path: Path, payload: dict[str, object]) -> None:
  temporary = path.with_suffix(".tmp")
  temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
  if os.name != "nt":
    temporary.chmod(0o600)
  os.replace(temporary, path)


def _lock_supervisor(lock_handle: BinaryIO, name: str) -> bool:
  if os.name == "nt":
    import msvcrt

    lock_handle.seek(0)
    if lock_handle.read(1) == b"":
      lock_handle.write(b"0")
      lock_handle.flush()
    lock_handle.seek(0)
    try:
      msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
      return False
    return True

  import fcntl

  try:
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
  except OSError:
    return False
  lock_handle.seek(0)
  lock_handle.truncate()
  lock_handle.write(str(os.getpid()).encode("ascii"))
  lock_handle.flush()
  return True


class _RotatingBinaryLog:
  """Small binary append log with deterministic size-based rotation."""

  def __init__(self, path: Path, max_bytes: int, backups: int) -> None:
    self.path = path
    self.max_bytes = max_bytes
    self.backups = backups
    self._lock = threading.Lock()
    self._handle = path.open("ab", buffering=0)

  def _rotate(self) -> None:
    self._handle.close()
    oldest = self.path.with_name(f"{self.path.name}.{self.backups}")
    if oldest.exists():
      oldest.unlink()
    for index in range(self.backups - 1, 0, -1):
      source = self.path.with_name(f"{self.path.name}.{index}")
      if source.exists():
        os.replace(source, self.path.with_name(f"{self.path.name}.{index + 1}"))
    if self.path.exists():
      os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))
    self._handle = self.path.open("ab", buffering=0)

  def write(self, payload: bytes) -> None:
    if not payload:
      return
    with self._lock:
      if (
        self.backups > 0
        and self.max_bytes > 0
        and self._handle.tell() + len(payload) > self.max_bytes
      ):
        self._rotate()
      self._handle.write(payload)

  def close(self) -> None:
    with self._lock:
      self._handle.close()


def _copy_stream(source: BinaryIO, destination: _RotatingBinaryLog) -> None:
  try:
    read = getattr(source, "read1", source.read)
    while chunk := read(64 * 1024):
      destination.write(chunk)
  finally:
    source.close()


def _terminate_child(child: subprocess.Popen[bytes]) -> None:
  if child.poll() is not None:
    return
  child.terminate()
  try:
    child.wait(timeout=10)
  except subprocess.TimeoutExpired:
    child.kill()
    child.wait(timeout=5)


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--name", required=True)
  parser.add_argument("--state-dir", required=True)
  parser.add_argument("--log-dir")
  parser.add_argument("--max-log-bytes", type=int, default=DEFAULT_MAX_LOG_BYTES)
  parser.add_argument("--log-backups", type=int, default=DEFAULT_LOG_BACKUPS)
  parser.add_argument("command", nargs=argparse.REMAINDER)
  args = parser.parse_args()
  if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.name):
    parser.error("--name must be a portable component identifier")
  command = list(args.command)
  if command and command[0] == "--":
    command.pop(0)
  if not command:
    parser.error("a child command is required after --")
  if args.max_log_bytes < 1:
    parser.error("--max-log-bytes must be positive")
  if args.log_backups < 1:
    parser.error("--log-backups must be positive")

  state_dir = Path(args.state_dir).expanduser().resolve()
  state_dir.mkdir(parents=True, exist_ok=True)
  log_dir = (
    Path(args.log_dir).expanduser().resolve()
    if args.log_dir
    else state_dir.parent / "logs"
  )
  log_dir.mkdir(parents=True, exist_ok=True)
  state_path = state_dir / f"{args.name}-supervisor.json"
  lock_path = state_dir / f"{args.name}-supervisor.lock"
  lock_handle = lock_path.open("a+b")
  if not _lock_supervisor(lock_handle, args.name):
    print(f"{args.name} supervisor is already running", file=sys.stderr)
    lock_handle.close()
    return 73

  stopping = False

  def request_stop(*_: object) -> None:
    nonlocal stopping
    stopping = True

  signal.signal(signal.SIGINT, request_stop)
  if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, request_stop)

  job = _create_kill_on_close_job()
  backoff_index = 0
  stdout_log = _RotatingBinaryLog(
    log_dir / f"{args.name}.stdout.log",
    args.max_log_bytes,
    args.log_backups,
  )
  stderr_log = _RotatingBinaryLog(
    log_dir / f"{args.name}.stderr.log",
    args.max_log_bytes,
    args.log_backups,
  )
  command_digest = hashlib.sha256(
    json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode()
  ).hexdigest()
  child: subprocess.Popen[bytes] | None = None
  try:
    while not stopping:
      started = time.monotonic()
      creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
      child = subprocess.Popen(
        command,
        creationflags=creationflags,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
      )
      _assign_to_job(job, child)
      if child.stdout is None or child.stderr is None:
        raise RuntimeError("child output pipes were not created")
      stdout_pump = threading.Thread(
        target=_copy_stream,
        args=(child.stdout, stdout_log),
        name=f"{args.name}-stdout-pump",
        daemon=True,
      )
      stderr_pump = threading.Thread(
        target=_copy_stream,
        args=(child.stderr, stderr_log),
        name=f"{args.name}-stderr-pump",
        daemon=True,
      )
      stdout_pump.start()
      stderr_pump.start()
      _write_state(
        state_path,
        {
          "name": args.name,
          "status": "RUNNING",
          "supervisorPid": os.getpid(),
          "childPid": child.pid,
          "startedAt": time.time(),
          "processGroupId": (
            os.getpgid(os.getpid()) if os.name != "nt" else os.getpid()
          ),
          "commandDigest": command_digest,
          "restartBackoffSeconds": BACKOFF_SECONDS[backoff_index],
        },
      )
      while child.poll() is None and not stopping:
        time.sleep(0.5)
      if stopping and child.poll() is None:
        _terminate_child(child)
      stdout_pump.join(timeout=2)
      stderr_pump.join(timeout=2)
      if stopping:
        break
      exit_code = int(child.returncode or 0)
      running_seconds = time.monotonic() - started
      if running_seconds >= STABLE_RESET_SECONDS:
        backoff_index = 0
      delay = BACKOFF_SECONDS[backoff_index]
      backoff_index = min(backoff_index + 1, len(BACKOFF_SECONDS) - 1)
      _write_state(
        state_path,
        {
          "name": args.name,
          "status": "RECOVERING",
          "supervisorPid": os.getpid(),
          "childPid": 0,
          "lastExitCode": exit_code,
          "commandDigest": command_digest,
          "restartBackoffSeconds": delay,
        },
      )
      deadline = time.monotonic() + delay
      while not stopping and time.monotonic() < deadline:
        time.sleep(0.2)
    _write_state(
      state_path,
      {
        "name": args.name,
        "status": "STOPPED",
        "supervisorPid": os.getpid(),
        "childPid": 0,
      },
    )
    return 0
  finally:
    if child is not None:
      _terminate_child(child)
    if job is not None:
      ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(job))
    stdout_log.close()
    stderr_log.close()
    lock_handle.close()


if __name__ == "__main__":
  raise SystemExit(main())
