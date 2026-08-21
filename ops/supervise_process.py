"""Small Windows-safe restart supervisor for QuantX managed children."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BACKOFF_SECONDS = (1, 2, 5, 10, 30)
STABLE_RESET_SECONDS = 300
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class _IoCounters(ctypes.Structure):
  _fields_ = [(name, ctypes.c_ulonglong) for name in (
    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
  )]


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
  information.BasicLimitInformation.LimitFlags = (
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
  )
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
  os.replace(temporary, path)


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--name", required=True)
  parser.add_argument("--state-dir", required=True)
  parser.add_argument("command", nargs=argparse.REMAINDER)
  args = parser.parse_args()
  command = list(args.command)
  if command and command[0] == "--":
    command.pop(0)
  if not command:
    parser.error("a child command is required after --")

  state_dir = Path(args.state_dir).expanduser().resolve()
  state_dir.mkdir(parents=True, exist_ok=True)
  log_dir = state_dir.parent / "logs"
  log_dir.mkdir(parents=True, exist_ok=True)
  state_path = state_dir / f"{args.name}-supervisor.json"
  lock_path = state_dir / f"{args.name}-supervisor.lock"
  lock_handle = lock_path.open("a+b")
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
      print(f"{args.name} supervisor is already running", file=sys.stderr)
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
  stdout_log = (log_dir / f"{args.name}.stdout.log").open("ab", buffering=0)
  stderr_log = (log_dir / f"{args.name}.stderr.log").open("ab", buffering=0)
  try:
    while not stopping:
      started = time.monotonic()
      creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
      child = subprocess.Popen(
        command,
        creationflags=creationflags,
        stdout=stdout_log,
        stderr=stderr_log,
      )
      _assign_to_job(job, child)
      _write_state(state_path, {
        "name": args.name,
        "status": "RUNNING",
        "supervisorPid": os.getpid(),
        "childPid": child.pid,
        "startedAt": time.time(),
        "restartBackoffSeconds": BACKOFF_SECONDS[backoff_index],
      })
      while child.poll() is None and not stopping:
        time.sleep(0.5)
      if stopping and child.poll() is None:
        child.terminate()
        try:
          child.wait(timeout=10)
        except subprocess.TimeoutExpired:
          child.kill()
          child.wait(timeout=5)
        break
      exit_code = int(child.returncode or 0)
      running_seconds = time.monotonic() - started
      if running_seconds >= STABLE_RESET_SECONDS:
        backoff_index = 0
      delay = BACKOFF_SECONDS[backoff_index]
      backoff_index = min(backoff_index + 1, len(BACKOFF_SECONDS) - 1)
      _write_state(state_path, {
        "name": args.name,
        "status": "RECOVERING",
        "supervisorPid": os.getpid(),
        "childPid": 0,
        "lastExitCode": exit_code,
        "restartBackoffSeconds": delay,
      })
      deadline = time.monotonic() + delay
      while not stopping and time.monotonic() < deadline:
        time.sleep(0.2)
    _write_state(state_path, {
      "name": args.name,
      "status": "STOPPED",
      "supervisorPid": os.getpid(),
      "childPid": 0,
    })
    return 0
  finally:
    if job is not None:
      ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(job))
    stdout_log.close()
    stderr_log.close()
    lock_handle.close()


if __name__ == "__main__":
  raise SystemExit(main())
