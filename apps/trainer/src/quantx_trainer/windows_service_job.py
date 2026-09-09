"""Handle-bound verification and termination of one Windows Trainer service job."""

import ctypes
import json
import re
import sys
from contextlib import contextmanager

from quantx_infrastructure.training_bundle_store import reject_links


def service_job_name(instance):
  if not isinstance(instance, str) or not re.fullmatch(r"[a-f0-9]{32}", instance):
    raise ValueError("SERVICE_INSTANCE_INVALID")
  return f"Global\\QuantXTrainer-{instance}"


class _FileTime(ctypes.Structure):
  _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _Accounting(ctypes.Structure):
  _fields_ = [
    (name, ctypes.c_int64)
    for name in (
      "TotalUserTime",
      "TotalKernelTime",
      "ThisPeriodTotalUserTime",
      "ThisPeriodTotalKernelTime",
    )
  ] + [
    (name, ctypes.c_uint32)
    for name in (
      "TotalPageFaultCount",
      "TotalProcesses",
      "ActiveProcesses",
      "TotalTerminatedProcesses",
    )
  ]


class ServiceJob:
  def __init__(self, kernel, handle):
    self.kernel, self.handle = kernel, handle

  def active_processes(self):
    value = _Accounting()
    if not self.kernel.QueryInformationJobObject(
      self.handle, 1, ctypes.byref(value), ctypes.sizeof(value), None
    ):
      raise RuntimeError("SERVICE_JOB_QUERY_FAILED")
    return int(value.ActiveProcesses)

  def terminate(self):
    if not self.kernel.TerminateJobObject(self.handle, 1):
      raise RuntimeError("SERVICE_JOB_TERMINATION_UNCONFIRMED")


@contextmanager
def open_service_job(root, instance, *, kernel=None):
  name = service_job_name(instance)
  path = root / "status.json"
  reject_links(path)
  if path.stat().st_size > 8192:
    raise ValueError("SERVICE_IDENTITY_INVALID")
  value = json.loads(path.read_text())
  if (
    value.get("instance_id") != instance
    or type(value.get("pid")) is not int
    or value["pid"] <= 0
  ):
    raise ValueError("SERVICE_IDENTITY_INVALID")
  if kernel is None:
    if sys.platform != "win32":
      raise RuntimeError("WINDOWS_SERVICE_JOB_REQUIRED")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
  signatures = {
    "OpenJobObjectW": (
      [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p],
      ctypes.c_void_p,
    ),
    "OpenProcess": ([ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32], ctypes.c_void_p),
    "GetProcessTimes": (
      [ctypes.c_void_p] + [ctypes.POINTER(_FileTime)] * 4,
      ctypes.c_int,
    ),
    "IsProcessInJob": (
      [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)],
      ctypes.c_int,
    ),
    "QueryInformationJobObject": (
      [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
      ],
      ctypes.c_int,
    ),
    "TerminateJobObject": ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
    "CloseHandle": ([ctypes.c_void_p], ctypes.c_int),
  }
  for method, (args, result) in signatures.items():
    function = getattr(kernel, method)
    function.argtypes, function.restype = args, result
  job = kernel.OpenJobObjectW(0x0004 | 0x0008, False, name)  # QUERY | TERMINATE
  if not job:
    raise RuntimeError("SERVICE_JOB_UNAVAILABLE")
  try:
    process = kernel.OpenProcess(
      0x1000, False, value["pid"]
    )  # QUERY_LIMITED_INFORMATION
    if not process:
      raise RuntimeError("SERVICE_PROCESS_UNAVAILABLE")
    try:
      creation, exit_time, kernel_time, user_time = (_FileTime() for _ in range(4))
      if not kernel.GetProcessTimes(
        process,
        *(ctypes.byref(v) for v in (creation, exit_time, kernel_time, user_time)),
      ):
        raise RuntimeError("SERVICE_PROCESS_IDENTITY_UNAVAILABLE")
      created = ((creation.high << 32 | creation.low) - 116444736000000000) / 10000000
      if created != value.get("created_at"):
        raise RuntimeError("SERVICE_PROCESS_IDENTITY_CHANGED")
      member = ctypes.c_int()
      if (
        not kernel.IsProcessInJob(process, job, ctypes.byref(member))
        or not member.value
      ):
        raise RuntimeError("SERVICE_JOB_MEMBERSHIP_UNCONFIRMED")
    finally:
      kernel.CloseHandle(process)
    yield ServiceJob(kernel, job)
  finally:
    kernel.CloseHandle(job)
