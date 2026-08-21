"""Read-only discovery of a local XTData endpoint on Windows."""

from __future__ import annotations

import configparser
import ctypes
import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

XTDATA_PORT_ENV = "QMT_XTDATA_PORT"
XTDATA_PORT_MIN = 58600
XTDATA_PORT_MAX = 58699
_TCP_TABLE_OWNER_PID_LISTENER = 3
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x00000002
_MAX_PATH = 260
_NO_ERROR = 0


@dataclass(frozen=True, slots=True)
class XTDataEndpoint:
  host: str
  port: int
  source: str


@dataclass(frozen=True, slots=True)
class _TcpListener:
  address: str
  port: int
  pid: int


@dataclass(frozen=True, slots=True)
class _ProcessIdentity:
  name: str
  parent_pid: int


class _MibTcpRowOwnerPid(ctypes.Structure):
  _fields_ = [
    ("state", ctypes.c_ulong),
    ("local_address", ctypes.c_ulong),
    ("local_port", ctypes.c_ulong),
    ("remote_address", ctypes.c_ulong),
    ("remote_port", ctypes.c_ulong),
    ("owning_pid", ctypes.c_ulong),
  ]


class _ProcessEntry32W(ctypes.Structure):
  _fields_ = [
    ("size", ctypes.c_ulong),
    ("usage_count", ctypes.c_ulong),
    ("process_id", ctypes.c_ulong),
    ("default_heap_id", ctypes.c_size_t),
    ("module_id", ctypes.c_ulong),
    ("thread_count", ctypes.c_ulong),
    ("parent_process_id", ctypes.c_ulong),
    ("base_priority", ctypes.c_long),
    ("flags", ctypes.c_ulong),
    ("executable_name", ctypes.c_wchar * _MAX_PATH),
  ]


def _configured_endpoint(environment: Mapping[str, str]) -> XTDataEndpoint | None:
  value = environment.get(XTDATA_PORT_ENV, "").strip()
  if not value:
    return None
  try:
    port = int(value)
  except ValueError as exc:
    raise ValueError(f"{XTDATA_PORT_ENV} must be an integer") from exc
  if not 1 <= port <= 65535:
    raise ValueError(f"{XTDATA_PORT_ENV} must be between 1 and 65535")
  return XTDataEndpoint("127.0.0.1", port, XTDATA_PORT_ENV)


def _windows_tcp_listeners() -> tuple[_TcpListener, ...]:
  if os.name != "nt":
    return ()
  size = ctypes.c_ulong(0)
  get_table = ctypes.windll.iphlpapi.GetExtendedTcpTable
  get_table(None, ctypes.byref(size), True, socket.AF_INET, _TCP_TABLE_OWNER_PID_LISTENER, 0)
  if size.value <= ctypes.sizeof(ctypes.c_ulong):
    return ()
  buffer = ctypes.create_string_buffer(size.value)
  result = get_table(
    buffer,
    ctypes.byref(size),
    True,
    socket.AF_INET,
    _TCP_TABLE_OWNER_PID_LISTENER,
    0,
  )
  if result != _NO_ERROR:
    return ()
  count = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ulong)).contents.value
  row_size = ctypes.sizeof(_MibTcpRowOwnerPid)
  first_row = ctypes.addressof(buffer) + ctypes.sizeof(ctypes.c_ulong)
  listeners: list[_TcpListener] = []
  for index in range(count):
    row = _MibTcpRowOwnerPid.from_address(first_row + index * row_size)
    listeners.append(
      _TcpListener(
        address=socket.inet_ntoa(
          int(row.local_address).to_bytes(4, "little")
        ),
        port=socket.ntohs(int(row.local_port) & 0xFFFF),
        pid=int(row.owning_pid),
      )
    )
  return tuple(listeners)


def _windows_process_path(pid: int) -> Path | None:
  if os.name != "nt":
    return None
  kernel32 = ctypes.windll.kernel32
  kernel32.OpenProcess.restype = ctypes.c_void_p
  handle = kernel32.OpenProcess(
    _PROCESS_QUERY_LIMITED_INFORMATION,
    False,
    int(pid),
  )
  if not handle:
    return None
  try:
    size = ctypes.c_ulong(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not kernel32.QueryFullProcessImageNameW(
      handle,
      0,
      buffer,
      ctypes.byref(size),
    ):
      return None
    return Path(buffer.value)
  finally:
    kernel32.CloseHandle(handle)


def _windows_process_table() -> dict[int, _ProcessIdentity]:
  if os.name != "nt":
    return {}
  kernel32 = ctypes.windll.kernel32
  kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
  snapshot = kernel32.CreateToolhelp32Snapshot(
    _TH32CS_SNAPPROCESS,
    0,
  )
  if snapshot in {None, ctypes.c_void_p(-1).value}:
    return {}
  try:
    entry = _ProcessEntry32W()
    entry.size = ctypes.sizeof(entry)
    identities: dict[int, _ProcessIdentity] = {}
    available = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
    while available:
      identities[int(entry.process_id)] = _ProcessIdentity(
        name=str(entry.executable_name),
        parent_pid=int(entry.parent_process_id),
      )
      available = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    return identities
  finally:
    kernel32.CloseHandle(snapshot)


def _formula_worker_port(executable: Path) -> int | None:
  install_root = executable.parent.parent
  config_path = install_root / "config" / "broker.ini"
  parser = configparser.ConfigParser(interpolation=None)
  try:
    with config_path.open("r", encoding="utf-8-sig") as stream:
      parser.read_file(stream)
    port = parser.getint("formulaworker", "serverport")
  except (OSError, ValueError, configparser.Error):
    return None
  return port if 1 <= port <= 65535 else None


def _miniquote_port(executable: Path) -> int | None:
  config_path = executable.parent.parent / "config" / "xtminiquote.lua"
  try:
    contents = config_path.read_bytes()
  except OSError:
    return None
  match = re.search(
    rb"""(?m)^\s*address\s*=\s*["'](?:0\.0\.0\.0|127\.0\.0\.1|localhost):(\d+)["']\s*,?\s*$""",
    contents,
  )
  if match is None:
    return None
  port = int(match.group(1).decode("ascii"))
  return port if 1 <= port <= 65535 else None


def _verified_qmt_endpoints(
  listeners: tuple[_TcpListener, ...],
) -> tuple[XTDataEndpoint, ...]:
  listeners_by_pid: dict[int, set[int]] = {}
  for listener in listeners:
    listeners_by_pid.setdefault(listener.pid, set()).add(listener.port)

  endpoints: dict[tuple[str, int], XTDataEndpoint] = {}
  process_table = _windows_process_table()
  for pid, listening_ports in listeners_by_pid.items():
    identity = process_table.get(pid)
    if identity is None:
      continue
    executable = _windows_process_path(pid)
    executable_name = identity.name.casefold()
    if executable_name == "xtitclient.exe":
      if executable is None:
        continue
      configured_port = _formula_worker_port(executable)
      verified_ports = (
        {configured_port}
        if configured_port is not None
        and configured_port in listening_ports
        and XTDATA_PORT_MIN <= configured_port <= XTDATA_PORT_MAX
        else set()
      )
      source = executable.parent.parent / "config" / "broker.ini"
    elif executable_name == "miniquote.exe":
      # MiniQMT runs its quote endpoint in this sibling process and does not
      # persist xtdata.cfg. Verify its exact parent, install root config, and
      # listener ownership before using the configured endpoint.
      parent_identity = process_table.get(identity.parent_pid)
      if (
        parent_identity is None
        or parent_identity.name.casefold() != "xtminiqmt.exe"
      ):
        continue
      verified_ports = {
        port
        for port in listening_ports
        if XTDATA_PORT_MIN <= port <= XTDATA_PORT_MAX
      }
      if len(verified_ports) != 1:
        continue
      source: str | Path = "Toolhelp32:miniquote.exe<-XtMiniQmt.exe"
      if executable is not None:
        parent_path = _windows_process_path(identity.parent_pid)
        if parent_path is not None and parent_path.parent != executable.parent:
          continue
        configured_port = _miniquote_port(executable)
        if configured_port is None or verified_ports != {configured_port}:
          continue
        source = executable.parent.parent / "config" / "xtminiquote.lua"
    else:
      continue
    for verified_port in verified_ports:
      endpoint = XTDataEndpoint(
        "127.0.0.1",
        verified_port,
        str(source),
      )
      endpoints[(endpoint.host, endpoint.port)] = endpoint
  return tuple(endpoints.values())


def discover_xtdata_endpoint(
  environment: Mapping[str, str] | None = None,
) -> XTDataEndpoint:
  """Return one explicit, locally verified endpoint or fail closed."""
  configured = _configured_endpoint(environment or os.environ)
  if configured is not None:
    return configured

  discovered = _verified_qmt_endpoints(_windows_tcp_listeners())
  if len(discovered) == 1:
    return discovered[0]
  if len(discovered) > 1:
    ports = ", ".join(str(endpoint.port) for endpoint in discovered)
    raise RuntimeError(
      "multiple verified XTData endpoints found "
      f"({ports}); set {XTDATA_PORT_ENV}"
    )
  raise RuntimeError(
    "no verified local XTData endpoint found; "
    f"start QMT or set {XTDATA_PORT_ENV}"
  )
