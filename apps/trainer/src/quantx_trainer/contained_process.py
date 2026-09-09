"""Windows Research bootstrap: contain descendants before importing computation.

The sole, non-inheritable job handle deliberately lives until process exit.
Closing it explicitly would also terminate this process. No breakaway flags are
enabled. This module must never assign the long-lived calling Trainer to a job.
"""

import ctypes
import runpy
import sys

_JOB_HANDLE = None
_MODULES = {"quantx_research.cli", "quantx_research.preparation_job"}


class _BasicLimits(ctypes.Structure):
  _fields_ = [
    ("PerProcessUserTimeLimit", ctypes.c_int64),
    ("PerJobUserTimeLimit", ctypes.c_int64),
    ("LimitFlags", ctypes.c_uint32),
    ("MinimumWorkingSetSize", ctypes.c_size_t),
    ("MaximumWorkingSetSize", ctypes.c_size_t),
    ("ActiveProcessLimit", ctypes.c_uint32),
    ("Affinity", ctypes.c_size_t),
    ("PriorityClass", ctypes.c_uint32),
    ("SchedulingClass", ctypes.c_uint32),
  ]


class _IoCounters(ctypes.Structure):
  _fields_ = [
    (name, ctypes.c_uint64)
    for name in (
      "ReadOperationCount",
      "WriteOperationCount",
      "OtherOperationCount",
      "ReadTransferCount",
      "WriteTransferCount",
      "OtherTransferCount",
    )
  ]


class _ExtendedLimits(ctypes.Structure):
  _fields_ = [
    ("BasicLimitInformation", _BasicLimits),
    ("IoInfo", _IoCounters),
    ("ProcessMemoryLimit", ctypes.c_size_t),
    ("JobMemoryLimit", ctypes.c_size_t),
    ("PeakProcessMemoryUsed", ctypes.c_size_t),
    ("PeakJobMemoryUsed", ctypes.c_size_t),
  ]


def _enter_job(kernel=None, *, name=None):
  if kernel is None:
    if sys.platform != "win32":
      raise RuntimeError("WINDOWS_CONTAINMENT_REQUIRED")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
  signatures = {
    "CreateJobObjectW": ([ctypes.c_void_p, ctypes.c_wchar_p], ctypes.c_void_p),
    "SetInformationJobObject": (
      [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32],
      ctypes.c_int,
    ),
    "GetCurrentProcess": ([], ctypes.c_void_p),
    "AssignProcessToJobObject": ([ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
    "CloseHandle": ([ctypes.c_void_p], ctypes.c_int),
    "GetLastError": ([], ctypes.c_uint32),
  }
  for method, (arguments, result) in signatures.items():
    function = getattr(kernel, method)
    function.argtypes, function.restype = arguments, result
  handle = kernel.CreateJobObjectW(None, name)
  if not handle:
    raise RuntimeError("WINDOWS_JOB_CREATE_FAILED")
  if name is not None and kernel.GetLastError() == 183:
    kernel.CloseHandle(handle)
    raise RuntimeError("WINDOWS_JOB_ALREADY_EXISTS")
  limits = _ExtendedLimits()
  limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE only.
  if not kernel.SetInformationJobObject(
    handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
  ):
    kernel.CloseHandle(handle)
    raise RuntimeError("WINDOWS_JOB_LIMITS_FAILED")
  if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
    kernel.CloseHandle(handle)
    raise RuntimeError("WINDOWS_JOB_ASSIGN_FAILED")
  return handle


def research_command(module: str) -> list[str]:
  if module not in _MODULES:
    raise ValueError("RESEARCH_MODULE_UNSUPPORTED")
  if sys.platform == "win32":
    return [sys.executable, "-m", "quantx_trainer.contained_process", module]
  return [sys.executable, "-m", module]


def main(argv=None):
  global _JOB_HANDLE
  args = list(sys.argv[1:] if argv is None else argv)
  if not args or args[0] not in _MODULES:
    print("RESEARCH_MODULE_UNSUPPORTED", file=sys.stderr)
    return 2
  try:
    _JOB_HANDLE = _enter_job()
  except RuntimeError as exc:
    print(str(exc), file=sys.stderr)
    return 2
  # Keep the original parent/PID and exit code used by persistent process evidence.
  sys.argv = args
  runpy.run_module(args[0], run_name="__main__", alter_sys=True)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
