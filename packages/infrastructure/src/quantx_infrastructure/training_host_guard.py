"""Machine-wide admission for high-resource offline computation.

The OS lock belongs to the actual computation process, not its dispatcher. An
unclean exit leaves evidence and blocks admission until explicit reconciliation.
No trading database, runtime profile or GPU initialization is involved here.
"""

from __future__ import annotations

import _thread
import json
import math
import os
import shutil
import sys
import threading
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from datetime import time as wall_time
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import psutil

SHANGHAI = ZoneInfo("Asia/Shanghai")
MIB = 1024**2


class HostAdmissionDenied(RuntimeError):
  """Stable public reason; never include host paths or dependency exceptions."""


def host_guard_root() -> Path:
  """One machine location across checkouts, environments and user accounts."""
  if sys.platform == "win32":
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    # CSIDL_COMMON_APPDATA: use the OS identity rather than inherited env vars.
    if ctypes.windll.shell32.SHGetFolderPathW(None, 35, None, 0, buffer) != 0:
      raise HostAdmissionDenied("HOST_GUARD_LOCATION_UNAVAILABLE")
    return Path(buffer.value) / "QuantX" / "training"
  if sys.platform == "darwin":
    return Path("/Library/Application Support/QuantX/training")
  return Path("/var/lib/quantx/training")


def _reject_links(path: Path) -> None:
  for component in (path, *path.parents):
    if component.is_symlink() or (
      hasattr(component, "is_junction") and component.is_junction()
    ):
      raise HostAdmissionDenied("HOST_GUARD_LINK_FORBIDDEN")


@dataclass(frozen=True)
class HostPolicy:
  windows: tuple[tuple[tuple[int, ...], wall_time, wall_time], ...]
  disk_roots: tuple[Path, ...]
  cpu_threads: int
  max_rss_mib: int
  minimum_available_memory_mib: int
  minimum_free_disk_mib: int
  gpu_max_memory_fraction: float
  sample_seconds: int
  stop_grace_seconds: int

  @classmethod
  def load(cls, root: Path) -> HostPolicy:
    try:
      _reject_links(root / "policy.toml")
      with (root / "policy.toml").open("rb") as stream:
        data = tomllib.load(stream)
      names = set(cls.__dataclass_fields__)
      if set(data) != names:
        raise ValueError
      for name in names - {"windows", "disk_roots", "gpu_max_memory_fraction"}:
        if type(data[name]) is not int or data[name] <= 0:
          raise ValueError
      fraction = data["gpu_max_memory_fraction"]
      if type(fraction) not in (int, float) or not 0 < fraction <= 1:
        raise ValueError
      if data["sample_seconds"] > 10 or data["stop_grace_seconds"] > 60:
        raise ValueError
      if not isinstance(data["windows"], list) or not data["windows"]:
        raise ValueError
      if not isinstance(data["disk_roots"], list) or not data["disk_roots"]:
        raise ValueError
      disks = tuple(Path(value) for value in data["disk_roots"])
      if any(not disk.is_absolute() or not disk.is_dir() for disk in disks):
        raise ValueError
      for disk in disks:
        _reject_links(disk)
      windows = []
      for window in data["windows"]:
        if set(window) != {"weekdays", "start", "end"}:
          raise ValueError
        days = window["weekdays"]
        if (
          not isinstance(days, list)
          or not days
          or any(type(day) is not int or day not in range(7) for day in days)
        ):
          raise ValueError
        start, end = (
          wall_time.fromisoformat(window["start"]),
          wall_time.fromisoformat(window["end"]),
        )
        if start.tzinfo or end.tzinfo or start >= end:
          raise ValueError
        windows.append((tuple(days), start, end))
      return cls(**{**data, "windows": tuple(windows), "disk_roots": disks})
    except (OSError, ValueError, TypeError, KeyError):
      raise HostAdmissionDenied("HOST_POLICY_MISSING_OR_INVALID") from None

  def window_reason(self, now: datetime) -> str | None:
    if now.tzinfo is None:
      return "HOST_CLOCK_UNKNOWN"
    local = now.astimezone(SHANGHAI)
    current = local.time()
    if not any(
      local.weekday() in days and start <= current < end
      for days, start, end in self.windows
    ):
      return "OUTSIDE_ALLOWED_TRAINING_WINDOW"
    return None


  def capacity_reason(self, available: int, free: int) -> str | None:
    if any(type(value) is not int or value < 0 for value in (available, free)):
      return "HOST_RESOURCE_STATE_UNKNOWN"
    if available < self.minimum_available_memory_mib * MIB:
      return "HOST_MEMORY_RESERVE"
    if free < self.minimum_free_disk_mib * MIB:
      return "HOST_DISK_RESERVE"
    return None


def host_resource_status(root: Path, *, now: datetime | None = None) -> dict:
  """Read global capacity only; never claim a lock or initialize CPU/GPU compute."""
  observed = now if now is not None else datetime.now(SHANGHAI)
  result = {
    "status": "UNKNOWN",
    "reason": "HOST_RESOURCE_STATE_UNKNOWN",
    "observed_at": observed.isoformat(),
    "host_lock": "NOT_INSPECTED",
    "task_budgets": "NOT_INSPECTED",
  }
  try:
    policy = HostPolicy.load(root)
    result["limits"] = {
      "cpu_threads": policy.cpu_threads,
      "max_rss_mib": policy.max_rss_mib,
      "minimum_available_memory_mib": policy.minimum_available_memory_mib,
      "minimum_free_disk_mib": policy.minimum_free_disk_mib,
      "gpu_max_memory_fraction": policy.gpu_max_memory_fraction,
    }
    reason = policy.window_reason(observed)
    if reason is None:
      available = psutil.virtual_memory().available
      free = min(shutil.disk_usage(disk).free for disk in policy.disk_roots)
      reason = policy.capacity_reason(available, free)
      if reason != "HOST_RESOURCE_STATE_UNKNOWN":
        result["available_memory_mib"] = available // MIB
        result["minimum_disk_free_mib"] = free // MIB
    result.update(
      status="UNKNOWN" if reason in {"HOST_CLOCK_UNKNOWN", "HOST_RESOURCE_STATE_UNKNOWN"} else ("BLOCKED" if reason else "PASS"),
      reason=reason,
    )
  except HostAdmissionDenied:
    result["reason"] = "HOST_POLICY_MISSING_OR_INVALID"
  except Exception:
    pass
  return result


class HostResourceGuard:
  def __init__(
    self,
    root: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(SHANGHAI),
  ):
    self.root = root
    self.now = now
    self.policy = HostPolicy.load(root)
    self.process = psutil.Process()
    self.lock = None
    self.reason: str | None = None
    self.stop = threading.Event()
    self.monitor: threading.Thread | None = None
    self._old_environment: dict[str, str | None] = {}
    self._cpu_sample: tuple[float, float] | None = None
    self._gpu_memory_reader: Callable[[], float | None] | None = None

  def monitor_gpu_memory(self, reader: Callable[[], float | None]) -> None:
    self._gpu_memory_reader = reader
    reason = self._gpu_reason()
    if reason:
      self.reason = reason
      raise HostAdmissionDenied(reason)

  def _gpu_reason(self) -> str | None:
    if self._gpu_memory_reader is None:
      return None
    try:
      fraction = self._gpu_memory_reader()
      if (
        type(fraction) not in (int, float)
        or not math.isfinite(fraction)
        or not 0 <= fraction <= 1
      ):
        return "HOST_GPU_MEMORY_STATE_UNKNOWN"
      if fraction > self.policy.gpu_max_memory_fraction:
        return "HOST_GPU_MEMORY_BUDGET"
    except Exception:
      return "HOST_GPU_MEMORY_STATE_UNKNOWN"
    return None

  def _record(self, status: str) -> None:
    target = self.root / "owner.json"
    temporary = self.root / "owner.pending"
    _reject_links(target)
    _reject_links(temporary)
    payload = {
      "version": 1,
      "status": status,
      "pid": self.process.pid,
      "created_at": self.process.create_time(),
      "updated_at": self.now().isoformat(),
      "reason": self.reason,
      "children": [],
    }
    for child in self.process.children(recursive=True):
      try:
        payload["children"].append(
          {"pid": child.pid, "created_at": child.create_time()}
        )
      except psutil.NoSuchProcess:
        continue
    with temporary.open("w", encoding="utf-8") as stream:
      json.dump(payload, stream)
      stream.flush()
      os.fsync(stream.fileno())
    os.replace(temporary, target)

  def resource_reason(self) -> str | None:
    reason = self.policy.window_reason(self.now())
    if reason:
      return reason
    try:
      available = psutil.virtual_memory().available
      free = min(shutil.disk_usage(disk).free for disk in self.policy.disk_roots)
      descendants = self.process.children(recursive=True)
      rss = 0
      cpu_seconds = 0.0
      for process in (self.process, *descendants):
        try:
          usage = process.cpu_times()
          rss += process.memory_info().rss
          cpu_seconds += usage.user + usage.system
        except psutil.NoSuchProcess:
          if process.pid == self.process.pid:
            return "HOST_RESOURCE_STATE_UNKNOWN"
          # A child that exited during the sample no longer consumes resources.
          continue
      sampled = time.monotonic()
      if self._cpu_sample is not None:
        prior_time, prior_cpu = self._cpu_sample
        if sampled - prior_time >= 0.5:
          self._cpu_sample = sampled, cpu_seconds
          if (cpu_seconds - prior_cpu) / (
            sampled - prior_time
          ) > self.policy.cpu_threads * 1.05:
            return "TASK_CPU_BUDGET"
      else:
        self._cpu_sample = sampled, cpu_seconds
      capacity_reason = self.policy.capacity_reason(available, free)
      if capacity_reason:
        return capacity_reason
      if rss > self.policy.max_rss_mib * MIB:
        return "TASK_MEMORY_BUDGET"
    except (OSError, psutil.Error):
      return "HOST_RESOURCE_STATE_UNKNOWN"
    return self._gpu_reason()

  def __enter__(self) -> HostResourceGuard:
    try:
      _reject_links(self.root / "host.lock")
      self.lock = (self.root / "host.lock").open("a+b")
      if sys.platform == "win32":
        import msvcrt

        if self.lock.seek(0, os.SEEK_END) == 0:
          self.lock.write(b"0")
          self.lock.flush()
        self.lock.seek(0)
        msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
      else:
        import fcntl

        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
      owner = self.root / "owner.json"
      _reject_links(owner)
      if owner.exists():
        previous = json.loads(owner.read_text(encoding="utf-8"))
        if previous.get("version") != 1 or previous.get("status") != "RELEASED":
          raise HostAdmissionDenied("HOST_PREVIOUS_EXECUTION_UNRECONCILED")
      self.reason = self.resource_reason()
      if self.reason:
        raise HostAdmissionDenied(self.reason)
      self._record("RUNNING")
      for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
      ):
        self._old_environment[key] = os.environ.get(key)
        os.environ[key] = str(self.policy.cpu_threads)
      self.monitor = threading.Thread(
        target=self._watch, name="training-host-guard", daemon=True
      )
      self.monitor.start()
      return self
    except BaseException as exc:
      self._restore_environment()
      if self.lock:
        self.lock.close()
        self.lock = None
      if isinstance(exc, (OSError, ValueError, TypeError, AttributeError)):
        raise HostAdmissionDenied("HOST_LOCK_OR_EVIDENCE_UNAVAILABLE") from None
      raise

  def _watch(self) -> None:
    while not self.stop.wait(self.policy.sample_seconds):
      try:
        reason = self.resource_reason()
        if reason is None:
          self._record("RUNNING")
      except Exception:
        reason = "HOST_RESOURCE_STATE_UNKNOWN"
      if reason:
        self.reason = reason
        try:
          self._record("STOP_REQUESTED")
        except Exception:
          pass  # The prior RUNNING record still blocks unsafe recovery.
        if self.stop.is_set():
          return
        _thread.interrupt_main()
        if not self.stop.wait(self.policy.stop_grace_seconds):
          # The OS lock stays held until this computation is actually stopped.
          # An uncertain descendant or forced exit retains unreconciled evidence.
          try:
            for child in self.process.children(recursive=True):
              child.kill()
          finally:
            os._exit(75)
        return

  def __exit__(self, exc_type, exc_value, traceback) -> None:
    self.stop.set()
    try:
      if self.monitor:
        try:
          self.monitor.join()
        except KeyboardInterrupt:
          if not self.reason:
            raise
          # The watchdog can request stop just as the context begins cleanup.
          self.monitor.join()
      # Recheck at the success boundary, including a window crossed between samples.
      self.reason = self.reason or self.resource_reason()
      if any(child.is_running() for child in self.process.children(recursive=True)):
        self.reason = self.reason or "HOST_DESCENDANTS_UNRECONCILED"
        self._record("UNRECONCILED")
      else:
        self._record("RELEASED")
    except (OSError, psutil.Error):
      self.reason = self.reason or "HOST_RESOURCE_STATE_UNKNOWN"
    finally:
      self._restore_environment()
      if self.lock:
        self.lock.close()
        self.lock = None
    if self.reason:
      raise HostAdmissionDenied(self.reason)

  def _restore_environment(self) -> None:
    for key, value in self._old_environment.items():
      if value is None:
        os.environ.pop(key, None)
      else:
        os.environ[key] = value


_active = threading.local()


def training_cpu_threads() -> int:
  """Use this process's admitted budget; standalone computations use one thread."""
  guard = getattr(_active, "guard", None)
  if guard is not None and guard.process.pid == os.getpid():
    return guard.policy.cpu_threads
  return 1


def monitor_training_gpu_memory(reader: Callable[[], float | None]) -> None:
  """Attach GPU-only sampling to the current admitted CLI computation."""
  guard = getattr(_active, "guard", None)
  if guard is not None and guard.process.pid == os.getpid():
    guard.monitor_gpu_memory(reader)


@contextmanager
def high_resource_guard():
  """Nested CLI entrypoints share ownership; a fork must acquire its own lock."""
  existing = getattr(_active, "guard", None)
  if existing is not None and existing.process.pid == os.getpid():
    yield existing
    return
  with HostResourceGuard(host_guard_root()) as guard:
    _active.guard = guard
    try:
      yield guard
    finally:
      _active.guard = None
