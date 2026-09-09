import ctypes as c
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from quantx_trainer import preflight
from quantx_trainer import runtime_permissions as module


@pytest.mark.parametrize("grant", [2, 4, 16, 64, 256, 65536, 262144, 524288])
def test_each_partial_write_grant_rejects_nested_dependency(tmp_path, grant):
  root = tmp_path / "environment"
  root.mkdir()
  nested = root / "library.py"
  nested.write_text("pass")
  with pytest.raises(module.RuntimePermissionsError, match="RUNTIME_WRITABLE"):
    module.inspect_trees([root], lambda path: grant if path == nested else 0x120089)


@pytest.mark.parametrize("grant", [16, 64, 256, 65536, 262144, 524288])
def test_parent_replacement_and_permission_grants_reject(tmp_path, grant):
  root = tmp_path / "code"
  root.mkdir()
  with pytest.raises(module.RuntimePermissionsError, match="RUNTIME_WRITABLE"):
    module.inspect_trees([root], lambda path: grant if path == tmp_path else 0x120089)


def test_full_trees_checked_while_unrelated_sibling_creation_allowed(tmp_path):
  roots = [tmp_path / "code", tmp_path / "conda"]
  for root in roots:
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "module.py").write_text("pass")
  visited = []

  def access(path):
    visited.append(path)
    return 6 if path == tmp_path else 0x120089

  module.inspect_trees(roots, access)
  assert all(root / "nested" / "module.py" in visited for root in roots)
  assert visited.count(tmp_path) == 1


def test_link_cannot_escape_inspection(tmp_path):
  root = tmp_path / "code"
  root.mkdir()
  try:
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
  except OSError:
    pytest.skip("symlink creation unavailable")
  with pytest.raises(module.RuntimePermissionsError, match="REPARSE_POINT"):
    module.inspect_trees([root], lambda path: 0x120089)


class Function:
  def __init__(self, fn):
    self.fn = fn

  def __call__(self, *args):
    return self.fn(*args)


def put(pointer, kind, value):
  c.cast(pointer, c.POINTER(kind))[0] = value


def win32(*, grant=0x120089, dangerous=False, fail_access=False):
  closed, freed, requested = [], [], []
  kernel, security = Mock(), Mock()
  kernel.GetCurrentProcess = Function(lambda: -1)
  kernel.CloseHandle = Function(lambda handle: closed.append(handle.value) or 1)
  kernel.LocalFree = Function(lambda handle: freed.append(handle.value))
  security.OpenProcessToken = Function(
    lambda proc, rights, out: put(out, c.c_void_p, 11) or 1
  )
  security.DuplicateToken = Function(
    lambda token, level, out: put(out, c.c_void_p, 12) or 1
  )

  def privileges(token, kind, buffer, length, size):
    c.c_uint32.from_buffer(buffer).value = int(dangerous)
    if dangerous:
      value = module._Privilege.from_buffer(buffer, 4)
      value.luid.low = 7
      value.attributes = 0  # Disabled still means available to enable.
    put(size, c.c_uint32, 4 + int(dangerous) * c.sizeof(module._Privilege))
    return 1

  security.GetTokenInformation = Function(privileges)
  security.LookupPrivilegeValueW = Function(
    lambda system, name, out: put(out, module._Luid, module._Luid(7, 0)) or 1
  )
  security.GetNamedSecurityInfoW = Function(
    lambda *args: put(args[-1], c.c_void_p, 13) or 0
  )

  def access(descriptor, token, desired, mapping, privs, length, granted, allowed):
    requested.append(desired)
    put(granted, c.c_uint32, grant)
    put(allowed, c.c_int, 1)
    return not fail_access

  security.AccessCheck = Function(access)
  return kernel, security, closed, freed, requested


def test_win32_maximum_allowed_preserves_partial_grant_and_releases_handles():
  kernel, security, closed, freed, requested = win32(grant=2)
  with module.windows_access_reader(kernel=kernel, security=security) as read:
    assert read(Path("test")) == 2
  assert requested == [0x02000000]
  assert freed == [13]
  assert closed == [12, 11]


def test_win32_failure_does_not_become_readonly_and_releases_handles():
  kernel, security, closed, freed, _ = win32(fail_access=True)
  with pytest.raises(module.RuntimePermissionsError, match="UNAVAILABLE"):
    with module.windows_access_reader(kernel=kernel, security=security) as read:
      read(Path("test"))
  assert freed == [13]
  assert closed == [12, 11]


def test_disabled_privilege_rejects_before_acl_reads():
  kernel, security, closed, freed, requested = win32(dangerous=True)
  with pytest.raises(module.RuntimePermissionsError, match="IDENTITY_TOO_POWERFUL"):
    with module.windows_access_reader(kernel=kernel, security=security):
      pytest.fail("privileged token accepted")
  assert closed == [11]
  assert freed == requested == []


@pytest.mark.asyncio
async def test_permission_failure_precedes_control_plane(tmp_path, monkeypatch):
  database, prefect = AsyncMock(), AsyncMock()
  monkeypatch.setattr(preflight, "check_database", database)
  monkeypatch.setattr(preflight, "check_prefect", prefect)
  monkeypatch.setattr(
    preflight,
    "check_runtime_permissions",
    Mock(side_effect=module.RuntimePermissionsError("TRAINER_RUNTIME_WRITABLE")),
  )
  with pytest.raises(preflight.TrainerPreflightError, match="RUNTIME_WRITABLE"):
    await preflight.preflight(SimpleNamespace(code_root=tmp_path))
  database.assert_not_called()
  prefect.assert_not_called()


def test_unreadable_runtime_is_unknown(monkeypatch, tmp_path):
  @contextmanager
  def reader():
    yield lambda path: 0x120089

  monkeypatch.setattr(module.sys, "platform", "win32")
  monkeypatch.setattr(module, "windows_access_reader", reader)
  with pytest.raises(module.RuntimePermissionsError, match="UNAVAILABLE"):
    module.check_runtime_permissions(tmp_path / "missing", tmp_path / "missing2")


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows ACL API")
def test_native_current_identity_cannot_mistake_owned_temp_file_for_frozen(tmp_path):
  path = tmp_path / "writable.py"
  path.write_text("pass")
  try:
    with module.windows_access_reader() as read:
      assert read(path) & module.WRITE_ACCESS
  except module.RuntimePermissionsError as exc:
    assert str(exc) == "TRAINER_RUNTIME_IDENTITY_TOO_POWERFUL"
