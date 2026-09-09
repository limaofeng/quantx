"""Read-only Windows ACL gate for the current Trainer process identity.

This is a launch-time observation, not protection against deployment administrators
changing ACLs later. No test writes, token adjustment or permission changes occur.
"""

from __future__ import annotations

import ctypes as c
import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path


class RuntimePermissionsError(RuntimeError):
  pass


# File writes, directory additions, deletion and permission/owner changes.
WRITE_ACCESS = 0x2 | 0x4 | 0x10 | 0x40 | 0x100 | 0x10000 | 0x40000 | 0x80000
# Creating unrelated siblings is allowed; replacing a protected ancestor is not.
ANCESTOR_ACCESS = WRITE_ACCESS & ~(0x2 | 0x4)
DANGEROUS_PRIVILEGES = (
  "SeRestorePrivilege",
  "SeTakeOwnershipPrivilege",
  "SeDebugPrivilege",
  "SeLoadDriverPrivilege",
  "SeManageVolumePrivilege",
  "SeTcbPrivilege",
  "SeImpersonatePrivilege",
  "SeAssignPrimaryTokenPrivilege",
  "SeCreateTokenPrivilege",
)


class _Mapping(c.Structure):
  _fields_ = [(name, c.c_uint32) for name in ("read", "write", "execute", "all")]


class _Luid(c.Structure):
  _fields_ = [("low", c.c_uint32), ("high", c.c_int32)]


class _Privilege(c.Structure):
  _fields_ = [("luid", _Luid), ("attributes", c.c_uint32)]


def _require(result):
  if not result:
    raise RuntimePermissionsError("TRAINER_RUNTIME_PERMISSIONS_UNAVAILABLE")


def _signatures(kernel, security):
  ptr, uint, boolean = c.c_void_p, c.c_uint32, c.c_int
  puint = c.POINTER(uint)
  for library, name, args, result in (
    (kernel, "GetCurrentProcess", [], ptr),
    (kernel, "CloseHandle", [ptr], boolean),
    (kernel, "LocalFree", [ptr], ptr),
    (security, "OpenProcessToken", [ptr, uint, c.POINTER(ptr)], boolean),
    (security, "DuplicateToken", [ptr, c.c_int, c.POINTER(ptr)], boolean),
    (security, "GetTokenInformation", [ptr, c.c_int, ptr, uint, puint], boolean),
    (
      security,
      "LookupPrivilegeValueW",
      [c.c_wchar_p, c.c_wchar_p, c.POINTER(_Luid)],
      boolean,
    ),
    (
      security,
      "GetNamedSecurityInfoW",
      [c.c_wchar_p, c.c_int, uint, ptr, ptr, ptr, ptr, c.POINTER(ptr)],
      uint,
    ),
    (
      security,
      "AccessCheck",
      [ptr, ptr, uint, c.POINTER(_Mapping), ptr, puint, puint, c.POINTER(boolean)],
      boolean,
    ),
  ):
    function = getattr(library, name)
    function.argtypes, function.restype = args, result


def _check_privileges(security, token):
  # Bound the token buffer; failure/overflow is unknown, never an empty privilege set.
  buffer = c.create_string_buffer(65536)
  size = c.c_uint32()
  _require(security.GetTokenInformation(token, 3, buffer, len(buffer), c.byref(size)))
  _require(4 <= size.value <= len(buffer))
  count = c.c_uint32.from_buffer(buffer).value
  _require(4 + count * c.sizeof(_Privilege) <= size.value)
  privileges = (_Privilege * count).from_buffer(buffer, 4)
  held = {(p.luid.low, p.luid.high) for p in privileges if not p.attributes & 4}
  for name in DANGEROUS_PRIVILEGES:
    luid = _Luid()
    _require(security.LookupPrivilegeValueW(None, name, c.byref(luid)))
    # Disabled privileges can be enabled by the process, so still reject them.
    if (luid.low, luid.high) in held:
      raise RuntimePermissionsError("TRAINER_RUNTIME_IDENTITY_TOO_POWERFUL")


@contextmanager
def windows_access_reader(*, kernel=None, security=None):
  if kernel is None:
    kernel = c.WinDLL("kernel32", use_last_error=True)
    security = c.WinDLL("advapi32", use_last_error=True)
  _signatures(kernel, security)
  primary, token = c.c_void_p(), c.c_void_p()
  try:
    _require(
      security.OpenProcessToken(kernel.GetCurrentProcess(), 0xA, c.byref(primary))
    )
    _check_privileges(security, primary)
    _require(security.DuplicateToken(primary, 2, c.byref(token)))

    def access(path):
      descriptor = c.c_void_p()
      try:
        result = security.GetNamedSecurityInfoW(
          str(path), 1, 7, None, None, None, None, c.byref(descriptor)
        )
        _require(result == 0 and descriptor.value)
        mapping = _Mapping(0x120089, 0x120116, 0x1200A0, 0x1F01FF)
        privileges = c.create_string_buffer(65536)
        length, granted, allowed = c.c_uint32(len(privileges)), c.c_uint32(), c.c_int()
        # MAXIMUM_ALLOWED exposes partial write grants; a combined write request
        # could be denied even when a subset of those dangerous rights is granted.
        _require(
          security.AccessCheck(
            descriptor,
            token,
            0x02000000,
            c.byref(mapping),
            privileges,
            c.byref(length),
            c.byref(granted),
            c.byref(allowed),
          )
        )
        _require(allowed.value)
        return granted.value
      finally:
        if descriptor.value:
          kernel.LocalFree(descriptor)

    yield access
  finally:
    for handle in (token, primary):
      if handle.value:
        kernel.CloseHandle(handle)


def _inspect(path, access, *, ancestor=False):
  info = path.lstat()
  if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
    raise RuntimePermissionsError("TRAINER_RUNTIME_REPARSE_POINT")
  if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
    raise RuntimePermissionsError("TRAINER_RUNTIME_FILE_TYPE_INVALID")
  if access(path) & (ANCESTOR_ACCESS if ancestor else WRITE_ACCESS):
    raise RuntimePermissionsError("TRAINER_RUNTIME_WRITABLE")
  return stat.S_ISDIR(info.st_mode)


def inspect_trees(roots, access):
  ancestors = set()
  for root in roots:
    if not root.is_absolute():
      raise RuntimePermissionsError("TRAINER_RUNTIME_PATH_INVALID")
    # Never resolve away a junction before inspecting it.
    for parent in reversed(root.parents):
      if parent not in ancestors:
        if not _inspect(parent, access, ancestor=True):
          raise RuntimePermissionsError("TRAINER_RUNTIME_PATH_INVALID")
        ancestors.add(parent)
    if not _inspect(root, access):
      raise RuntimePermissionsError("TRAINER_RUNTIME_PATH_INVALID")
    pending = [root]
    while pending:
      with os.scandir(pending.pop()) as entries:
        for entry in entries:
          path = Path(entry.path)
          if _inspect(path, access):
            pending.append(path)


def check_runtime_permissions(code_root: Path, prefix: Path) -> None:
  if sys.platform != "win32":
    return
  try:
    with windows_access_reader() as access:
      inspect_trees((code_root, prefix), access)
  except RuntimePermissionsError:
    raise
  except Exception:
    raise RuntimePermissionsError("TRAINER_RUNTIME_PERMISSIONS_UNAVAILABLE") from None
