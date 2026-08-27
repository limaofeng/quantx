"""Physical-memory guard and low-overhead RSS telemetry for long research runs."""

from __future__ import annotations

import ctypes
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

_GIB = 1024**3


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
  total_physical_bytes: int
  available_physical_bytes: int
  process_rss_bytes: int


class PhysicalMemoryGuardError(RuntimeError):
  """The next bounded stage cannot preserve the configured RAM reserve."""

  def __init__(
    self,
    *,
    stage: str,
    snapshot: MemorySnapshot,
    reserve_bytes: int,
    estimated_increment_bytes: int,
  ) -> None:
    self.stage = stage
    self.snapshot = snapshot
    self.reserve_bytes = reserve_bytes
    self.estimated_increment_bytes = estimated_increment_bytes
    required = reserve_bytes + estimated_increment_bytes
    super().__init__(
      "物理内存保护触发: "
      f"stage={stage}, available={snapshot.available_physical_bytes / _GIB:.2f} GiB, "
      f"estimated_increment={estimated_increment_bytes / _GIB:.2f} GiB, "
      f"reserve={reserve_bytes / _GIB:.2f} GiB, "
      f"required={required / _GIB:.2f} GiB；不会使用 pagefile 冒险继续"
    )


class PhysicalMemoryMonitorError(RuntimeError):
  """Physical-memory counters could not be sampled reliably."""


class RuntimeMemoryMonitor:
  """Sample process RSS/physical RAM and enforce an absolute free-RAM reserve.

  The sampler latches the first observed reserve breach. Synchronous
  checkpoints then fail closed even if free memory recovered between samples.
  Long-running callers are expected to split work into bounded chunks and call
  :meth:`guard` before and after every chunk.
  """

  def __init__(
    self,
    *,
    reserve_gib: float,
    sample_interval_seconds: float = 0.5,
    snapshot_provider: Callable[[], MemorySnapshot] | None = None,
  ) -> None:
    self.reserve_bytes = int(float(reserve_gib) * _GIB)
    self.sample_interval_seconds = float(sample_interval_seconds)
    self._snapshot_provider = snapshot_provider or read_memory_snapshot
    self._stage = "initializing"
    self._lock = threading.Lock()
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None
    self._stage_stats: dict[str, dict[str, int]] = {}
    self._peak_rss_bytes = 0
    self._minimum_available_bytes: int | None = None
    self._total_physical_bytes = 0
    self._breach_snapshot: MemorySnapshot | None = None
    self._breach_stage: str | None = None
    self._sampling_error: str | None = None

  def __enter__(self) -> "RuntimeMemoryMonitor":
    self.checkpoint("initializing")
    self._thread = threading.Thread(
      target=self._sample_loop,
      name="quantx-research-memory-monitor",
      daemon=True,
    )
    self._thread.start()
    return self

  def __exit__(
    self,
    exception_type: type[BaseException] | None,
    _exception: BaseException | None,
    _traceback: object,
  ) -> None:
    self.close(enforce=exception_type is None)

  def close(self, *, enforce: bool = True) -> None:
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=max(1.0, self.sample_interval_seconds * 2.0))
      self._thread = None
    try:
      self._capture_sample()
    except Exception as exc:
      with self._lock:
        if self._sampling_error is None:
          self._sampling_error = f"{type(exc).__name__}: {exc}"
      if enforce:
        raise PhysicalMemoryMonitorError(
          f"物理内存监控失败，已按 fail-closed 停止研究: {type(exc).__name__}: {exc}"
        ) from exc
    if enforce:
      self.raise_if_breached("monitor_close")

  def set_stage(self, stage: str) -> MemorySnapshot:
    with self._lock:
      self._stage = str(stage)
    return self.checkpoint(stage)

  def checkpoint(self, stage: str | None = None) -> MemorySnapshot:
    """Sample now and fail if any sampler observed the physical-RAM reserve."""
    if stage is not None:
      with self._lock:
        self._stage = str(stage)
    try:
      snapshot = self._capture_sample()
    except Exception as exc:
      with self._lock:
        if self._sampling_error is None:
          self._sampling_error = f"{type(exc).__name__}: {exc}"
      raise PhysicalMemoryMonitorError(
        f"物理内存监控失败，已按 fail-closed 停止研究: {type(exc).__name__}: {exc}"
      ) from exc
    self.raise_if_breached(stage)
    return snapshot

  def raise_if_breached(self, stage: str | None = None) -> None:
    """Raise for a latched breach or a failed background memory sampler."""
    with self._lock:
      sampling_error = self._sampling_error
      breach_snapshot = self._breach_snapshot
      breach_stage = self._breach_stage
      current_stage = self._stage
    if sampling_error is not None:
      raise PhysicalMemoryMonitorError(
        f"物理内存监控失败，已按 fail-closed 停止研究: {sampling_error}"
      )
    if breach_snapshot is not None:
      raise PhysicalMemoryGuardError(
        stage=str(stage or breach_stage or current_stage),
        snapshot=breach_snapshot,
        reserve_bytes=self.reserve_bytes,
        estimated_increment_bytes=0,
      )

  def guard(
    self,
    stage: str,
    *,
    estimated_increment_bytes: int = 0,
  ) -> MemorySnapshot:
    snapshot = self.checkpoint(stage)
    required = self.reserve_bytes + max(0, int(estimated_increment_bytes))
    if snapshot.available_physical_bytes < required:
      raise PhysicalMemoryGuardError(
        stage=stage,
        snapshot=snapshot,
        reserve_bytes=self.reserve_bytes,
        estimated_increment_bytes=max(0, int(estimated_increment_bytes)),
      )
    return snapshot

  def sample(self) -> MemorySnapshot:
    """Compatibility alias for an enforced synchronous checkpoint."""
    return self.checkpoint()

  def _capture_sample(self) -> MemorySnapshot:
    snapshot = self._snapshot_provider()
    with self._lock:
      stage = self._stage
      stats = self._stage_stats.setdefault(
        stage,
        {
          "sample_count": 0,
          "peak_process_rss_bytes": 0,
          "minimum_available_physical_bytes": snapshot.available_physical_bytes,
        },
      )
      stats["sample_count"] += 1
      stats["peak_process_rss_bytes"] = max(
        stats["peak_process_rss_bytes"],
        snapshot.process_rss_bytes,
      )
      stats["minimum_available_physical_bytes"] = min(
        stats["minimum_available_physical_bytes"],
        snapshot.available_physical_bytes,
      )
      self._peak_rss_bytes = max(self._peak_rss_bytes, snapshot.process_rss_bytes)
      self._minimum_available_bytes = (
        snapshot.available_physical_bytes
        if self._minimum_available_bytes is None
        else min(self._minimum_available_bytes, snapshot.available_physical_bytes)
      )
      self._total_physical_bytes = max(
        self._total_physical_bytes,
        snapshot.total_physical_bytes,
      )
      if (
        snapshot.available_physical_bytes < self.reserve_bytes
        and self._breach_snapshot is None
      ):
        self._breach_snapshot = snapshot
        self._breach_stage = stage
    return snapshot

  def to_dict(self) -> dict[str, Any]:
    with self._lock:
      stages = {
        stage: {
          **stats,
          "peak_process_rss_gib": stats["peak_process_rss_bytes"] / _GIB,
          "minimum_available_physical_gib": (
            stats["minimum_available_physical_bytes"] / _GIB
          ),
        }
        for stage, stats in sorted(self._stage_stats.items())
      }
      return {
        "physical_only": True,
        "reserve_gib": self.reserve_bytes / _GIB,
        "total_physical_bytes": self._total_physical_bytes,
        "total_physical_gib": self._total_physical_bytes / _GIB,
        "peak_process_rss_bytes": self._peak_rss_bytes,
        "peak_process_rss_gib": self._peak_rss_bytes / _GIB,
        "minimum_available_physical_bytes": self._minimum_available_bytes,
        "minimum_available_physical_gib": (
          self._minimum_available_bytes / _GIB
          if self._minimum_available_bytes is not None
          else None
        ),
        "reserve_breached": self._breach_snapshot is not None,
        "reserve_breach_stage": self._breach_stage,
        "reserve_breach_available_physical_bytes": (
          self._breach_snapshot.available_physical_bytes
          if self._breach_snapshot is not None
          else None
        ),
        "sampling_error": self._sampling_error,
        "stages": stages,
      }

  def _sample_loop(self) -> None:
    while not self._stop.wait(self.sample_interval_seconds):
      try:
        self._capture_sample()
      except Exception as exc:  # pragma: no cover - platform failure path
        with self._lock:
          if self._sampling_error is None:
            self._sampling_error = f"{type(exc).__name__}: {exc}"
        self._stop.set()
        return


def read_memory_snapshot() -> MemorySnapshot:
  """Read physical-memory and RSS counters without consulting swap/pagefile."""
  if os.name == "nt":
    return _windows_memory_snapshot()
  proc_snapshot = _proc_memory_snapshot()
  if proc_snapshot is not None:
    return proc_snapshot
  return _portable_memory_snapshot()


class _MemoryStatusEx(ctypes.Structure):
  _fields_ = [
    ("dwLength", ctypes.c_ulong),
    ("dwMemoryLoad", ctypes.c_ulong),
    ("ullTotalPhys", ctypes.c_ulonglong),
    ("ullAvailPhys", ctypes.c_ulonglong),
    ("ullTotalPageFile", ctypes.c_ulonglong),
    ("ullAvailPageFile", ctypes.c_ulonglong),
    ("ullTotalVirtual", ctypes.c_ulonglong),
    ("ullAvailVirtual", ctypes.c_ulonglong),
    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
  ]


class _ProcessMemoryCounters(ctypes.Structure):
  _fields_ = [
    ("cb", ctypes.c_ulong),
    ("PageFaultCount", ctypes.c_ulong),
    ("PeakWorkingSetSize", ctypes.c_size_t),
    ("WorkingSetSize", ctypes.c_size_t),
    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
    ("QuotaPagedPoolUsage", ctypes.c_size_t),
    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
    ("PagefileUsage", ctypes.c_size_t),
    ("PeakPagefileUsage", ctypes.c_size_t),
  ]


def _windows_memory_snapshot() -> MemorySnapshot:
  kernel32 = ctypes.windll.kernel32
  kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
  kernel32.GlobalMemoryStatusEx.restype = ctypes.c_int
  kernel32.GetCurrentProcess.argtypes = []
  kernel32.GetCurrentProcess.restype = ctypes.c_void_p
  status = _MemoryStatusEx()
  status.dwLength = ctypes.sizeof(status)
  if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
    raise OSError(ctypes.get_last_error(), "GlobalMemoryStatusEx failed")
  counters = _ProcessMemoryCounters()
  counters.cb = ctypes.sizeof(counters)
  process = kernel32.GetCurrentProcess()
  get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
  get_process_memory_info.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(_ProcessMemoryCounters),
    ctypes.c_ulong,
  ]
  get_process_memory_info.restype = ctypes.c_int
  if not get_process_memory_info(
    process,
    ctypes.byref(counters),
    counters.cb,
  ):
    raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
  return MemorySnapshot(
    total_physical_bytes=int(status.ullTotalPhys),
    available_physical_bytes=int(status.ullAvailPhys),
    process_rss_bytes=int(counters.WorkingSetSize),
  )


def _proc_memory_snapshot() -> MemorySnapshot | None:
  meminfo = Path("/proc/meminfo")
  statm = Path("/proc/self/statm")
  if not meminfo.is_file() or not statm.is_file():
    return None
  values: dict[str, int] = {}
  for line in meminfo.read_text(encoding="ascii").splitlines():
    name, _, raw = line.partition(":")
    if not raw:
      continue
    values[name] = int(raw.strip().split()[0]) * 1024
  resident_pages = int(statm.read_text(encoding="ascii").split()[1])
  return MemorySnapshot(
    total_physical_bytes=values["MemTotal"],
    available_physical_bytes=values.get("MemAvailable", values.get("MemFree", 0)),
    process_rss_bytes=resident_pages * int(os.sysconf("SC_PAGE_SIZE")),
  )


def _portable_memory_snapshot() -> MemorySnapshot:
  memory = psutil.virtual_memory()
  process = psutil.Process()
  return MemorySnapshot(
    total_physical_bytes=int(memory.total),
    available_physical_bytes=int(memory.available),
    process_rss_bytes=int(process.memory_info().rss),
  )
