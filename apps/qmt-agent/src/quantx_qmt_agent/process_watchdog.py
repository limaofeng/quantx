"""Out-of-process liveness watchdog for native XTData GIL stalls.

The monitor receives only a parent PID, its operating-system creation identity,
and an empty heartbeat-file path. It is an independent Python interpreter, so a
C extension holding the Agent's GIL cannot prevent it from terminating a stale
process for the service supervisor. The identity prevents a recycled PID from
causing an unrelated process to be terminated.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

WATCHDOG_HEARTBEAT_INTERVAL_SECONDS = 5.0
WATCHDOG_STALE_TIMEOUT_SECONDS = 90.0
WATCHDOG_POLL_INTERVAL_SECONDS = 5.0
WATCHDOG_TERMINATION_EXIT_CODE = 70
WATCHDOG_CHILD_SHUTDOWN_SECONDS = 5.0


def _is_process_alive(process_id: int) -> bool:
  if process_id <= 0:
    return False
  if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
      wintypes.DWORD,
      wintypes.BOOL,
      wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
      wintypes.HANDLE,
      ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(
      process_query_limited_information | synchronize,
      False,
      process_id,
    )
    if not handle:
      return False
    try:
      exit_code = wintypes.DWORD()
      if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
        return False
      return int(exit_code.value) == still_active
    finally:
      kernel32.CloseHandle(handle)
  try:
    os.kill(process_id, 0)
  except ProcessLookupError:
    return False
  except PermissionError:
    return True
  return True


def _windows_process_identity_from_handle(kernel32: Any, handle: Any) -> str | None:
  import ctypes
  from ctypes import wintypes

  kernel32.GetProcessTimes.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
  ]
  kernel32.GetProcessTimes.restype = wintypes.BOOL
  creation = wintypes.FILETIME()
  exit_time = wintypes.FILETIME()
  kernel_time = wintypes.FILETIME()
  user_time = wintypes.FILETIME()
  if not kernel32.GetProcessTimes(
    handle,
    ctypes.byref(creation),
    ctypes.byref(exit_time),
    ctypes.byref(kernel_time),
    ctypes.byref(user_time),
  ):
    return None
  created_at = (
    int(creation.dwHighDateTime) << 32
  ) | int(creation.dwLowDateTime)
  return f"windows:{created_at}"


def _process_identity(process_id: int) -> str | None:
  """Return an identity that changes when an operating-system PID is reused."""
  if process_id <= 0:
    return None
  if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
      wintypes.DWORD,
      wintypes.BOOL,
      wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(
      process_query_limited_information,
      False,
      process_id,
    )
    if not handle:
      return None
    try:
      return _windows_process_identity_from_handle(kernel32, handle)
    finally:
      kernel32.CloseHandle(handle)
  stat_path = Path(f"/proc/{process_id}/stat")
  try:
    value = stat_path.read_text(encoding="utf-8")
    command_end = value.rfind(")")
    fields_after_command = value[command_end + 2 :].split()
    if command_end > 0 and len(fields_after_command) > 19:
      return f"proc:{fields_after_command[19]}"
  except (OSError, UnicodeError):
    pass
  try:
    process_stat = Path(f"/proc/{process_id}").stat()
  except OSError:
    return f"pid:{process_id}" if _is_process_alive(process_id) else None
  return (
    f"procfs:{process_stat.st_ino}:"
    f"{getattr(process_stat, 'st_ctime_ns', int(process_stat.st_ctime * 1e9))}"
  )


def _force_terminate_process(
  process_id: int,
  expected_identity: str,
) -> bool:
  if process_id <= 0 or process_id == os.getpid():
    raise ValueError("watchdog parent process id is invalid")
  if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    process_terminate = 0x0001
    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
      wintypes.DWORD,
      wintypes.BOOL,
      wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(
      process_terminate | process_query_limited_information,
      False,
      process_id,
    )
    if not handle:
      error = ctypes.get_last_error()
      raise OSError(error, "could not open stale Agent process")
    try:
      if (
        _windows_process_identity_from_handle(kernel32, handle)
        != expected_identity
      ):
        return False
      if not kernel32.TerminateProcess(
        handle,
        WATCHDOG_TERMINATION_EXIT_CODE,
      ):
        error = ctypes.get_last_error()
        raise OSError(error, "could not terminate stale Agent process")
    finally:
      kernel32.CloseHandle(handle)
    return True
  if _process_identity(process_id) != expected_identity:
    return False
  os.kill(process_id, signal.SIGKILL)
  return True


def monitor_parent_process(
  *,
  parent_pid: int,
  parent_identity: str,
  heartbeat_path: Path,
  stale_timeout_seconds: float,
  poll_interval_seconds: float,
  is_parent_alive: Callable[[int], bool] = _is_process_alive,
  read_parent_identity: Callable[[int], str | None] = _process_identity,
  terminate_parent: Callable[[int, str], bool] = _force_terminate_process,
  wall_clock: Callable[[], float] = time.time,
  sleeper: Callable[[float], None] = time.sleep,
) -> str:
  """Monitor until the parent exits or its heartbeat becomes stale."""
  if stale_timeout_seconds <= 0 or poll_interval_seconds <= 0:
    raise ValueError("watchdog intervals must be positive")
  missing_since: float | None = None
  while is_parent_alive(parent_pid):
    if read_parent_identity(parent_pid) != parent_identity:
      return "parent-exited"
    now = wall_clock()
    try:
      heartbeat_modified_at = heartbeat_path.stat().st_mtime
      missing_since = None
    except FileNotFoundError:
      if missing_since is None:
        missing_since = now
      heartbeat_modified_at = missing_since
    if max(0.0, now - heartbeat_modified_at) > stale_timeout_seconds:
      # Re-check immediately before termination. The termination helper also
      # validates identity on the same Windows handle it will terminate.
      if (
        not is_parent_alive(parent_pid)
        or read_parent_identity(parent_pid) != parent_identity
      ):
        return "parent-exited"
      return (
        "heartbeat-timeout"
        if terminate_parent(parent_pid, parent_identity)
        else "parent-exited"
      )
    sleeper(poll_interval_seconds)
  return "parent-exited"


def _hidden_process_options() -> dict[str, Any]:
  options: dict[str, Any] = {
    "stdin": subprocess.DEVNULL,
    "stdout": subprocess.DEVNULL,
    "stderr": subprocess.DEVNULL,
    "close_fds": True,
  }
  if os.name != "nt":
    return options
  startupinfo = subprocess.STARTUPINFO()
  startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
  startupinfo.wShowWindow = subprocess.SW_HIDE
  options["startupinfo"] = startupinfo
  options["creationflags"] = subprocess.CREATE_NO_WINDOW
  return options


class AgentProcessWatchdog:
  """Parent-side owner for one independent watchdog interpreter."""

  def __init__(
    self,
    heartbeat_path: Path,
    *,
    parent_pid: int | None = None,
    heartbeat_interval_seconds: float = WATCHDOG_HEARTBEAT_INTERVAL_SECONDS,
    stale_timeout_seconds: float = WATCHDOG_STALE_TIMEOUT_SECONDS,
    poll_interval_seconds: float = WATCHDOG_POLL_INTERVAL_SECONDS,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    process_identity_reader: Callable[[int], str | None] = _process_identity,
  ) -> None:
    if heartbeat_interval_seconds <= 0:
      raise ValueError("watchdog heartbeat interval must be positive")
    if stale_timeout_seconds <= heartbeat_interval_seconds * 2:
      raise ValueError("watchdog stale timeout has insufficient grace")
    if poll_interval_seconds <= 0:
      raise ValueError("watchdog poll interval must be positive")
    self.heartbeat_path = Path(heartbeat_path)
    self.parent_pid = int(parent_pid or os.getpid())
    self.heartbeat_interval_seconds = heartbeat_interval_seconds
    self.stale_timeout_seconds = stale_timeout_seconds
    self.poll_interval_seconds = poll_interval_seconds
    self._popen_factory = popen_factory
    self._process_identity_reader = process_identity_reader
    self._process: Any | None = None

  @classmethod
  def create(cls, base_directory: Path) -> AgentProcessWatchdog:
    path = (
      Path(base_directory)
      / "process-watchdog"
      / f"agent-{os.getpid()}-{uuid.uuid4().hex}.heartbeat"
    )
    return cls(path)

  def touch(self) -> None:
    self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    self.heartbeat_path.touch(exist_ok=True)
    try:
      self.heartbeat_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
      pass

  def start(self) -> None:
    if self._process is not None:
      raise RuntimeError("Agent process watchdog is already started")
    self.touch()
    parent_identity = self._process_identity_reader(self.parent_pid)
    if not parent_identity:
      self.heartbeat_path.unlink(missing_ok=True)
      raise RuntimeError("could not identify Agent process for watchdog")
    command = [
      sys.executable,
      "-m",
      "quantx_qmt_agent.process_watchdog",
      "--monitor",
      "--parent-pid",
      str(self.parent_pid),
      "--parent-identity",
      parent_identity,
      "--heartbeat-path",
      str(self.heartbeat_path),
      "--stale-timeout-seconds",
      str(self.stale_timeout_seconds),
      "--poll-interval-seconds",
      str(self.poll_interval_seconds),
    ]
    try:
      self._process = self._popen_factory(
        command,
        **_hidden_process_options(),
      )
    except BaseException:
      self.heartbeat_path.unlink(missing_ok=True)
      raise

  async def heartbeat_loop(self) -> None:
    if self._process is None:
      raise RuntimeError("Agent process watchdog is not started")
    while True:
      if self._process.poll() is not None:
        raise RuntimeError("Agent process watchdog exited unexpectedly")
      self.touch()
      await asyncio.sleep(self.heartbeat_interval_seconds)

  def close(self) -> None:
    process = self._process
    self._process = None
    if process is not None and process.poll() is None:
      process.terminate()
      try:
        process.wait(timeout=WATCHDOG_CHILD_SHUTDOWN_SECONDS)
      except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=WATCHDOG_CHILD_SHUTDOWN_SECONDS)
    self.heartbeat_path.unlink(missing_ok=True)


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(add_help=False)
  parser.add_argument("--monitor", action="store_true")
  parser.add_argument("--parent-pid", type=int, required=True)
  parser.add_argument("--parent-identity", required=True)
  parser.add_argument("--heartbeat-path", type=Path, required=True)
  parser.add_argument("--stale-timeout-seconds", type=float, required=True)
  parser.add_argument("--poll-interval-seconds", type=float, required=True)
  return parser.parse_args()


def main() -> None:
  args = _parse_args()
  if not args.monitor:
    raise SystemExit(2)
  try:
    monitor_parent_process(
      parent_pid=args.parent_pid,
      parent_identity=args.parent_identity,
      heartbeat_path=args.heartbeat_path,
      stale_timeout_seconds=args.stale_timeout_seconds,
      poll_interval_seconds=args.poll_interval_seconds,
    )
  finally:
    args.heartbeat_path.unlink(missing_ok=True)


if __name__ == "__main__":
  main()
