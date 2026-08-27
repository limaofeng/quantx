#!/usr/bin/env python3
"""Authoritative macOS development runtime orchestrator for QuantX."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
import ipaddress
import json
import os
import platform
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import psutil

STATE_SCHEMA_VERSION = 1
DEFAULT_PREFECT_API_URL = "http://192.168.5.6:30420/api"
DEFAULT_PREFECT_WORKER_POOL = "quantx-pool"
MACOS_PREFECT_WORKER_NAME = "quantx-macos-dev"
API_PORT = 18081
MARKET_GATEWAY_PORT = 18082
MONITOR_PORT = 18083
WEB_PORT = 5250
DOCS_PORT = 5251
CADDY_PORT = 8080
CADDY_ADMIN_PORT = 2019
AGENT_WEBSOCKET_PING_TIMEOUT_SECONDS = 960
PROCESS_START_TOLERANCE_SECONDS = 1.0
GRACEFUL_STOP_SECONDS = 20.0
FORCED_STOP_SECONDS = 5.0
EXIT_STATE_ERROR = 65
EXIT_UNAVAILABLE = 69
EXIT_ALREADY_RUNNING = 73
EXIT_STOP_INCOMPLETE = 74
START_ORDER = (
  "sleep-guard",
  "market-gateway",
  "api",
  "engine",
  "ai-runtime",
  "worker",
  "web",
  "docs",
  "caddy",
)
STOP_ORDER = (
  "caddy",
  "web",
  "docs",
  "engine",
  "worker",
  "ai-runtime",
  "api",
  "market-gateway",
  "sleep-guard",
)
SERVER_PYTHON_PATHS = (
  "apps/api/src",
  "apps/ai-runtime/src",
  "apps/engine/src",
  "apps/monitor/src",
  "apps/worker/src",
  "packages/contracts/src",
  "packages/domain/src",
  "packages/application/src",
  "packages/infrastructure/src",
)
SENSITIVE_ENVIRONMENT_MARKERS = (
  "PASSWORD",
  "SECRET",
  "TOKEN",
  "CREDENTIAL",
  "PRIVATE_KEY",
)


class RuntimeCommandError(RuntimeError):
  def __init__(self, message: str, exit_code: int = 1) -> None:
    super().__init__(message)
    self.exit_code = exit_code


@dataclasses.dataclass(frozen=True)
class RuntimePaths:
  root: Path
  runtime: Path
  state_dir: Path
  supervisor_state_dir: Path
  log_dir: Path
  component_dir: Path
  state_file: Path
  tools_dir: Path
  caddy_data_dir: Path
  caddy_config_dir: Path
  prefect_home: Path
  monitor_runtime: Path
  monitor_state_dir: Path
  monitor_log_dir: Path
  monitor_state_file: Path

  @classmethod
  def from_root(cls, root: Path) -> RuntimePaths:
    physical_root = root.expanduser().resolve(strict=True)
    runtime = physical_root / ".runtime"
    state_dir = runtime / "state"
    monitor_runtime = runtime / "monitor"
    return cls(
      root=physical_root,
      runtime=runtime,
      state_dir=state_dir,
      supervisor_state_dir=state_dir / "macos-supervisors",
      log_dir=runtime / "logs" / "macos",
      component_dir=runtime / "components" / "macos",
      state_file=state_dir / "macos-dev-runtime.json",
      tools_dir=runtime / "tools",
      caddy_data_dir=runtime / "caddy-data",
      caddy_config_dir=runtime / "caddy-config",
      prefect_home=runtime / "prefect",
      monitor_runtime=monitor_runtime,
      monitor_state_dir=monitor_runtime / "state",
      monitor_log_dir=monitor_runtime / "logs",
      monitor_state_file=monitor_runtime / "dev-runtime.json",
    )

  def ensure_runtime_directories(self) -> None:
    for path in (
      self.runtime,
      self.state_dir,
      self.supervisor_state_dir,
      self.log_dir,
      self.component_dir,
      self.tools_dir,
      self.caddy_data_dir,
      self.caddy_config_dir,
      self.prefect_home,
    ):
      ensure_runtime_path(path, self.runtime).mkdir(parents=True, exist_ok=True)

  def ensure_monitor_directories(self) -> None:
    for path in (
      self.monitor_runtime,
      self.monitor_state_dir,
      self.monitor_log_dir,
    ):
      ensure_runtime_path(path, self.runtime).mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass(frozen=True)
class RuntimeConfiguration:
  environment: str
  profile: str
  agent_mode: str
  configured_account: str
  configured_live: bool
  public_url: str
  trusted_ips: tuple[str, ...]
  process_environment: dict[str, str]


@dataclasses.dataclass(frozen=True)
class ReadinessProbe:
  kind: str
  target: str = ""
  timeout_seconds: float = 60.0
  expected_instance_id: str = ""


@dataclasses.dataclass(frozen=True)
class ComponentSpec:
  name: str
  command: tuple[str, ...]
  working_directory: Path
  environment: Mapping[str, str]
  readiness: ReadinessProbe


def repository_root() -> Path:
  return Path(__file__).resolve(strict=True).parent.parent.resolve(strict=True)


def utc_now_iso() -> str:
  return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_runtime_path(path: Path, runtime_root: Path) -> Path:
  resolved_root = runtime_root.expanduser().resolve(strict=False)
  resolved = path.expanduser().resolve(strict=False)
  if resolved != resolved_root and resolved_root not in resolved.parents:
    raise RuntimeCommandError(
      f"Refusing to use a path outside the runtime root: {resolved}",
      EXIT_STATE_ERROR,
    )
  return resolved


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
  try:
    temporary.write_text(
      json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
      encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)
  finally:
    with contextlib.suppress(FileNotFoundError):
      temporary.unlink()


def read_runtime_state(path: Path, expected_root: Path) -> dict[str, Any] | None:
  if not path.exists():
    return None
  try:
    payload = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    raise RuntimeCommandError(
      f"Runtime state is corrupt: {path} ({exc.__class__.__name__})",
      EXIT_STATE_ERROR,
    ) from exc
  if not isinstance(payload, dict):
    raise RuntimeCommandError(
      f"Runtime state must be a JSON object: {path}", EXIT_STATE_ERROR
    )
  if payload.get("schemaVersion") != STATE_SCHEMA_VERSION:
    raise RuntimeCommandError(
      f"Unsupported runtime state schema in {path}", EXIT_STATE_ERROR
    )
  stored_root = Path(str(payload.get("root") or "")).expanduser().resolve(strict=False)
  if stored_root != expected_root.resolve(strict=True):
    raise RuntimeCommandError(
      f"Runtime state belongs to a different physical repository: {stored_root}",
      EXIT_STATE_ERROR,
    )
  components = payload.get("components")
  if not isinstance(components, list) or not all(
    isinstance(item, dict) for item in components
  ):
    raise RuntimeCommandError(
      f"Runtime state has an invalid components collection: {path}",
      EXIT_STATE_ERROR,
    )
  return payload


def command_digest(command: Sequence[str]) -> str:
  serialized = json.dumps(
    list(command), ensure_ascii=False, separators=(",", ":")
  ).encode("utf-8")
  return hashlib.sha256(serialized).hexdigest()


def process_matches_entry(entry: Mapping[str, Any]) -> bool:
  try:
    pid = int(entry["pid"])
    expected_started_at = float(entry["processStartedAt"])
    expected_pgid = int(entry["processGroupId"])
    expected_digest = str(entry["commandDigest"])
    process = psutil.Process(pid)
    if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
      return False
    if (
      abs(process.create_time() - expected_started_at) > PROCESS_START_TOLERANCE_SECONDS
    ):
      return False
    if os.getpgid(pid) != expected_pgid:
      return False
    return command_digest(process.cmdline()) == expected_digest
  except (KeyError, TypeError, ValueError, OSError, psutil.Error):
    return False


def managed_component_state(entry: Mapping[str, Any]) -> str:
  if not process_matches_entry(entry):
    return "STALE"
  supervisor_state_raw = str(entry.get("supervisorState") or "")
  if not supervisor_state_raw:
    return "RUNNING"
  try:
    supervisor_state = json.loads(
      Path(supervisor_state_raw).read_text(encoding="utf-8")
    )
    status = str(supervisor_state.get("status") or "UNKNOWN").upper()
    child_pid = int(supervisor_state.get("childPid") or 0)
    child = psutil.Process(child_pid)
    if (
      status == "RUNNING"
      and child.is_running()
      and child.status() != psutil.STATUS_ZOMBIE
      and os.getpgid(child_pid) == int(entry["processGroupId"])
    ):
      return "RUNNING"
    return status
  except (
    OSError,
    ValueError,
    TypeError,
    KeyError,
    json.JSONDecodeError,
    psutil.Error,
  ):
    return "RECOVERING"


def process_group_exists(process_group_id: int) -> bool:
  for process in psutil.process_iter(("pid", "status")):
    try:
      if (
        process.info["status"] != psutil.STATUS_ZOMBIE
        and os.getpgid(int(process.info["pid"])) == process_group_id
      ):
        return True
    except (OSError, ProcessLookupError, psutil.Error):
      continue
  return False


def parse_list_setting(raw: str | None) -> list[str]:
  value = str(raw or "").strip()
  if not value:
    return []
  try:
    parsed = json.loads(value)
  except json.JSONDecodeError:
    parsed = None
  if isinstance(parsed, list):
    return list(
      dict.fromkeys(str(item).strip() for item in parsed if str(item).strip())
    )
  normalized = value.strip("[]")
  return list(
    dict.fromkeys(
      item.strip().strip("'\"")
      for item in normalized.replace(";", ",").split(",")
      if item.strip().strip("'\"")
    )
  )


def parse_trusted_ips(raw: str | None) -> tuple[str, ...]:
  values = str(raw or "").replace(",", " ").split()
  parsed: list[str] = []
  for value in values:
    try:
      parsed.append(str(ipaddress.ip_network(value, strict=False)))
    except ValueError as exc:
      raise RuntimeCommandError(
        f"QUANTX_CADDY_TRUSTED_IPS contains an invalid IP/CIDR: {value}"
      ) from exc
  return tuple(dict.fromkeys(parsed))


def local_ipv4_addresses() -> tuple[str, ...]:
  addresses = ["127.0.0.1/32"]
  for values in psutil.net_if_addrs().values():
    for value in values:
      if value.family != socket.AF_INET:
        continue
      try:
        address = ipaddress.ip_address(value.address)
      except ValueError:
        continue
      if address.is_link_local or address.is_unspecified:
        continue
      addresses.append(f"{address}/32")
  return tuple(dict.fromkeys(addresses))


def load_environment_file(
  path: Path, target: dict[str, str], protected: set[str]
) -> None:
  if not path.is_file():
    return
  for raw_line in path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
      continue
    name, raw_value = line.split("=", 1)
    name = name.strip()
    if not name or name in protected:
      continue
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
      value = value[1:-1]
    target[name] = value


def replace_url_host(raw_url: str, host: str) -> str:
  parsed = urllib.parse.urlsplit(raw_url)
  if not parsed.scheme or not parsed.hostname:
    raise RuntimeCommandError("External dependency URL is not absolute")
  user_info = ""
  if "@" in parsed.netloc:
    user_info = parsed.netloc.rsplit("@", 1)[0] + "@"
  normalized_host = f"[{host}]" if ":" in host else host
  port = f":{parsed.port}" if parsed.port is not None else ""
  return urllib.parse.urlunsplit(
    (
      parsed.scheme,
      f"{user_info}{normalized_host}{port}",
      parsed.path,
      parsed.query,
      parsed.fragment,
    )
  )


def load_process_environment(paths: RuntimePaths) -> dict[str, str]:
  environment = dict(os.environ)
  protected = set(environment)
  for path in (
    paths.root / ".env",
    paths.root / "apps" / "api" / ".env",
    paths.root / "apps" / "api" / ".env.development",
  ):
    load_environment_file(path, environment, protected)
  dependency_host = environment.get("QUANTX_DEV_EXTERNAL_DEPENDENCY_HOST", "").strip()
  if dependency_host:
    if dependency_host.lower() == "wsl":
      raise RuntimeCommandError(
        "Mac runtime cannot resolve QUANTX_DEV_EXTERNAL_DEPENDENCY_HOST=wsl; "
        "configure the external service host explicitly"
      )
    for name in ("DATABASE_URL", "REDIS_URL", "INFLUXDB_HOST", "PREFECT_API_URL"):
      if environment.get(name):
        environment[name] = replace_url_host(environment[name], dependency_host)
    environment["REDIS_HOST"] = dependency_host
  return environment


def normalized_prefect_api_url(raw: str | None) -> str:
  value = str(raw or DEFAULT_PREFECT_API_URL).strip().rstrip("/")
  parsed = urllib.parse.urlsplit(value)
  if parsed.scheme not in {"http", "https"} or not parsed.hostname:
    raise RuntimeCommandError("PREFECT_API_URL must be an absolute HTTP(S) URL")
  return value if value.endswith("/api") else f"{value}/api"


def resolve_runtime_configuration(
  *,
  paths: RuntimePaths,
  requested_profile: str,
  requested_mode: str | None,
  requested_account: str,
  environment: Mapping[str, str],
) -> RuntimeConfiguration:
  if requested_profile == "web" and requested_mode != "data-only":
    profile = "full"
  else:
    profile = requested_profile
  agent_mode = requested_mode or "live"
  configured_live = profile == "full" and agent_mode == "live"

  process_environment = dict(environment)
  process_environment["ENV"] = "development"
  process_environment["RUNTIME_PROFILE"] = profile
  process_environment["QMT_AGENT_MODE"] = agent_mode
  process_environment["QUANTX_ROOT"] = str(paths.root)
  process_environment["QUANTX_RUNTIME_DIR"] = str(paths.runtime)
  process_environment["PYTHONUTF8"] = "1"
  process_environment["PYTHONIOENCODING"] = "utf-8"
  process_environment["PYTHONPATH"] = os.pathsep.join(
    str(paths.root / relative) for relative in SERVER_PYTHON_PATHS
  )
  process_environment["PREFECT_API_URL"] = normalized_prefect_api_url(
    process_environment.get("PREFECT_API_URL")
  )
  worker_pool = process_environment.get(
    "PREFECT_WORKER_POOL", DEFAULT_PREFECT_WORKER_POOL
  ).strip()
  if not worker_pool:
    raise RuntimeCommandError("PREFECT_WORKER_POOL must not be empty")
  process_environment["PREFECT_WORKER_POOL"] = worker_pool
  process_environment["PREFECT_HOME"] = str(paths.prefect_home)
  process_environment["PREFECT_ENABLED"] = "true" if profile == "full" else "false"

  configured_account = ""
  if configured_live:
    candidates = set()
    for name in (
      "REAL_TRADING_ACCOUNT_ALLOWLIST",
      "AUTH_BOOTSTRAP_ACCOUNT_IDS",
      "QMT_ACCOUNT_WHITELIST",
    ):
      candidates.update(parse_list_setting(process_environment.get(name)))
    configured_account = requested_account.strip()
    if not configured_account and len(candidates) == 1:
      configured_account = next(iter(candidates))
    if not configured_account:
      message = (
        "Dev full/live requires --account-id or exactly one configured account"
        if not candidates
        else "Multiple accounts are configured; select one with --account-id"
      )
      raise RuntimeCommandError(message)
    process_environment["ENABLE_REAL_TRADING"] = "true"
    process_environment["QMT_REAL_TRADING_ENABLED"] = "true"
    process_environment["REAL_TRADING_ACCOUNT_ALLOWLIST"] = json.dumps(
      [configured_account], separators=(",", ":")
    )
    process_environment["T_TRADE_LIVE_ENABLED"] = (
      "true"
      if process_environment.get("T_TRADE_LIVE_ENABLED", "").strip().lower() == "true"
      else "false"
    )
    process_environment["QMT_AGENT_LAUNCH_STATE"] = "REMOTE"
    process_environment.pop("QMT_AGENT_LAUNCH_REASON", None)
    process_environment.pop("QMT_AGENT_LAUNCH_STARTED_AT", None)
  else:
    process_environment["ENABLE_REAL_TRADING"] = "false"
    process_environment["QMT_REAL_TRADING_ENABLED"] = "false"
    process_environment["T_TRADE_LIVE_ENABLED"] = "false"
    process_environment["REAL_TRADING_ACCOUNT_ALLOWLIST"] = "[]"
    process_environment["QMT_AGENT_LAUNCH_STATE"] = "NOT_REQUESTED"
    process_environment.pop("QMT_AGENT_LAUNCH_REASON", None)
    process_environment.pop("QMT_AGENT_LAUNCH_STARTED_AT", None)

  public_url = (
    process_environment.get("PUBLIC_URL", "http://127.0.0.1:8080").strip().rstrip("/")
  )
  parsed_public_url = urllib.parse.urlsplit(public_url)
  public_port = parsed_public_url.port or (
    443 if parsed_public_url.scheme == "https" else 80
  )
  if (
    parsed_public_url.scheme not in {"http", "https"}
    or not parsed_public_url.hostname
    or parsed_public_url.username
    or parsed_public_url.password
    or parsed_public_url.path not in {"", "/"}
    or parsed_public_url.query
    or parsed_public_url.fragment
    or public_port != CADDY_PORT
  ):
    raise RuntimeCommandError(
      "PUBLIC_URL must be an absolute credential-free URL on port 8080 with no path"
    )
  if configured_live and parsed_public_url.scheme != "https":
    raise RuntimeCommandError(
      "Dev full/live requires a stable HTTPS PUBLIC_URL; plain HTTP is allowed "
      "only for explicit data-only diagnostics"
    )

  configured_trusted_ips = parse_trusted_ips(
    process_environment.get("QUANTX_CADDY_TRUSTED_IPS")
  )
  if configured_live and not configured_trusted_ips:
    raise RuntimeCommandError(
      "Dev full/live requires QUANTX_CADDY_TRUSTED_IPS for the Windows Agent source"
    )
  trusted_ips = tuple(dict.fromkeys((*local_ipv4_addresses(), *configured_trusted_ips)))
  process_environment["PUBLIC_URL"] = public_url
  process_environment["QUANTX_CADDY_SITE_ADDRESS"] = public_url
  process_environment["QUANTX_CADDY_BIND"] = (
    "0.0.0.0" if configured_live else "127.0.0.1"
  )
  process_environment["QUANTX_CADDY_TLS_SNIPPET"] = (
    "tls_internal" if configured_live else "tls_disabled"
  )
  process_environment["QUANTX_CADDY_TRUSTED_IPS"] = " ".join(trusted_ips)
  process_environment["XDG_CONFIG_HOME"] = str(paths.caddy_config_dir)
  process_environment["XDG_DATA_HOME"] = str(paths.caddy_data_dir)
  cors_origins = parse_list_setting(process_environment.get("CORS_ORIGINS"))
  if public_url not in cors_origins:
    cors_origins.append(public_url)
  process_environment["CORS_ORIGINS"] = json.dumps(cors_origins, separators=(",", ":"))

  return RuntimeConfiguration(
    environment="dev",
    profile=profile,
    agent_mode=agent_mode,
    configured_account=configured_account,
    configured_live=configured_live,
    public_url=public_url,
    trusted_ips=trusted_ips,
    process_environment=process_environment,
  )


def executable_version(command: Sequence[str]) -> str:
  completed = subprocess.run(
    list(command),
    check=False,
    capture_output=True,
    text=True,
    timeout=15,
  )
  if completed.returncode:
    raise RuntimeCommandError(
      f"Version check failed for {Path(command[0]).name}", EXIT_UNAVAILABLE
    )
  return (completed.stdout or completed.stderr).strip().splitlines()[0]


def required_python_version(paths: RuntimePaths) -> str:
  return (paths.root / ".python-version").read_text(encoding="utf-8").strip()


def required_node_version(paths: RuntimePaths) -> str:
  return (paths.root / ".nvmrc").read_text(encoding="utf-8").strip().lstrip("v")


def validate_python_runtime(paths: RuntimePaths, executable: Path) -> None:
  completed = subprocess.run(
    [str(executable), "-c", "import platform; print(platform.python_version())"],
    check=False,
    capture_output=True,
    text=True,
    timeout=15,
  )
  actual = completed.stdout.strip()
  expected = required_python_version(paths)
  if completed.returncode or actual != expected:
    raise RuntimeCommandError(
      f"Mac runtime requires Python {expected}; found {actual or 'unavailable'}",
      EXIT_UNAVAILABLE,
    )


def executable_path_without_resolving_symlinks(root: Path, raw: str | Path) -> Path:
  candidate = Path(raw).expanduser()
  if not candidate.is_absolute():
    candidate = root / candidate
  return Path(os.path.abspath(candidate))


def resolve_python(paths: RuntimePaths, environment: Mapping[str, str]) -> Path:
  configured = environment.get("QUANTX_PYTHON_EXE", "").strip()
  candidate = executable_path_without_resolving_symlinks(
    paths.root,
    configured or ".venv/bin/python",
  )
  if not candidate.is_file():
    raise RuntimeCommandError(
      f"Python runtime is missing: {candidate}. Run uv sync first.", EXIT_UNAVAILABLE
    )
  validate_python_runtime(paths, candidate)
  return candidate


def resolve_ai_runtime_python(
  paths: RuntimePaths, environment: Mapping[str, str], fallback: Path
) -> Path:
  configured = environment.get("QUANTX_AI_RUNTIME_PYTHON_EXE", "").strip()
  if not configured:
    return fallback
  candidate = executable_path_without_resolving_symlinks(paths.root, configured)
  if not candidate.is_file():
    raise RuntimeCommandError(
      f"AI Runtime Python is missing: {candidate}", EXIT_UNAVAILABLE
    )
  validate_python_runtime(paths, candidate)
  return candidate


def resolve_node(paths: RuntimePaths) -> tuple[Path, Path]:
  node_raw = shutil.which("node")
  npm_raw = shutil.which("npm")
  if not node_raw or not npm_raw:
    raise RuntimeCommandError(
      "Node and npm must be available in PATH", EXIT_UNAVAILABLE
    )
  node = Path(node_raw).resolve(strict=True)
  npm = Path(npm_raw).resolve(strict=True)
  actual_node = executable_version([str(node), "--version"]).lstrip("v")
  expected_node = required_node_version(paths)
  if actual_node != expected_node:
    raise RuntimeCommandError(
      f"Mac runtime requires Node {expected_node}; found {actual_node}",
      EXIT_UNAVAILABLE,
    )
  actual_npm = executable_version([str(npm), "--version"])
  try:
    npm_major = int(actual_npm.split(".", 1)[0])
  except ValueError as exc:
    raise RuntimeCommandError(f"Could not parse npm version: {actual_npm}") from exc
  if npm_major < 10:
    raise RuntimeCommandError(
      f"Mac runtime requires npm >=10; found {actual_npm}", EXIT_UNAVAILABLE
    )
  return node, npm


def read_tool_lock(paths: RuntimePaths, name: str) -> dict[str, Any]:
  lock_path = paths.root / "ops" / "tools.lock.json"
  try:
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    tool = payload["tools"][name]
  except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
    raise RuntimeCommandError(f"Invalid tool lock entry for {name}") from exc
  if not isinstance(tool, dict):
    raise RuntimeCommandError(f"Invalid tool lock entry for {name}")
  return tool


def sha256_file(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as handle:
    for block in iter(lambda: handle.read(1024 * 1024), b""):
      digest.update(block)
  return digest.hexdigest()


def locked_caddy_path(paths: RuntimePaths) -> Path:
  return paths.tools_dir / "caddy" / "caddy"


def verify_locked_caddy(paths: RuntimePaths) -> Path:
  if platform.system() != "Darwin" or platform.machine() != "arm64":
    raise RuntimeCommandError(
      "This migration version supports the validated macOS arm64 target only",
      EXIT_UNAVAILABLE,
    )
  tool = read_tool_lock(paths, "caddy-macos-arm64")
  executable = locked_caddy_path(paths)
  if not executable.is_file():
    raise RuntimeCommandError(
      "Locked macOS Caddy is missing. Run ./ops/quantx bootstrap.",
      EXIT_UNAVAILABLE,
    )
  if sha256_file(executable) != str(tool.get("installedSha256") or ""):
    raise RuntimeCommandError(
      "Installed macOS Caddy checksum does not match ops/tools.lock.json",
      EXIT_UNAVAILABLE,
    )
  version = executable_version([str(executable), "version"])
  if not version.startswith(f"v{tool.get('version')} "):
    raise RuntimeCommandError(
      f"Installed Caddy version does not match {tool.get('version')}",
      EXIT_UNAVAILABLE,
    )
  return executable


def install_locked_caddy(paths: RuntimePaths) -> Path:
  if platform.system() != "Darwin" or platform.machine() != "arm64":
    raise RuntimeCommandError(
      "This migration version supports the validated macOS arm64 target only",
      EXIT_UNAVAILABLE,
    )
  paths.ensure_runtime_directories()
  tool = read_tool_lock(paths, "caddy-macos-arm64")
  target = locked_caddy_path(paths)
  target.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(prefix="quantx-caddy-") as temporary_raw:
    temporary = Path(temporary_raw)
    archive = temporary / "caddy.tar.gz"
    try:
      with urllib.request.urlopen(str(tool["url"]), timeout=60) as response:
        with archive.open("wb") as handle:
          shutil.copyfileobj(response, handle)
    except (OSError, urllib.error.URLError) as exc:
      raise RuntimeCommandError(
        f"Could not download locked Caddy ({exc.__class__.__name__})",
        EXIT_UNAVAILABLE,
      ) from exc
    if sha256_file(archive) != str(tool.get("sha256") or ""):
      raise RuntimeCommandError("Downloaded Caddy archive checksum mismatch")
    with tarfile.open(archive, "r:gz") as bundle:
      member = next(
        (
          item
          for item in bundle.getmembers()
          if Path(item.name).name == str(tool.get("executable") or "caddy")
          and item.isfile()
        ),
        None,
      )
      if member is None or member.name != Path(member.name).name:
        raise RuntimeCommandError("Locked Caddy archive has an unsafe layout")
      extracted_handle = bundle.extractfile(member)
      if extracted_handle is None:
        raise RuntimeCommandError("Locked Caddy archive has no executable payload")
      staged = temporary / "caddy"
      with extracted_handle, staged.open("wb") as handle:
        shutil.copyfileobj(extracted_handle, handle)
    if sha256_file(staged) != str(tool.get("installedSha256") or ""):
      raise RuntimeCommandError("Extracted Caddy executable checksum mismatch")
    staged.chmod(0o755)
    destination = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    shutil.copy2(staged, destination)
    destination.chmod(0o755)
    os.replace(destination, target)
  return verify_locked_caddy(paths)


def validate_caddy_configuration(
  paths: RuntimePaths,
  caddy: Path,
  environment: Mapping[str, str],
) -> None:
  completed = subprocess.run(
    [
      str(caddy),
      "validate",
      "--config",
      str(paths.root / "ops/caddy/Caddyfile.dev"),
      "--adapter",
      "caddyfile",
    ],
    cwd=paths.root,
    env=dict(environment),
    check=False,
    capture_output=True,
    text=True,
    timeout=30,
  )
  if completed.returncode:
    diagnostic = (completed.stderr or completed.stdout).strip().splitlines()
    detail = diagnostic[-1] if diagnostic else "unknown validation error"
    raise RuntimeCommandError(f"Caddy configuration is invalid: {detail}")


def validate_macos_safety(configuration: RuntimeConfiguration) -> None:
  if platform.system() != "Darwin" or platform.machine() != "arm64":
    raise RuntimeCommandError(
      "This migration version supports the validated macOS arm64 target only",
      EXIT_UNAVAILABLE,
    )
  if not Path("/usr/bin/caffeinate").is_file():
    raise RuntimeCommandError("macOS caffeinate is unavailable", EXIT_UNAVAILABLE)
  if not configuration.configured_live:
    return
  completed = subprocess.run(
    ["/bin/launchctl", "print", "system/com.apple.timed"],
    check=False,
    capture_output=True,
    text=True,
    timeout=10,
  )
  if completed.returncode or "state = running" not in completed.stdout:
    raise RuntimeCommandError(
      "macOS network time service is not running; full/live remains blocked",
      EXIT_UNAVAILABLE,
    )


def http_json(
  url: str,
  *,
  timeout: float = 3.0,
  ssl_context: ssl.SSLContext | None = None,
) -> tuple[int, Any]:
  opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=ssl_context),
  )
  request = urllib.request.Request(url, headers={"User-Agent": "quantx-runtime/1"})
  try:
    with opener.open(request, timeout=timeout) as response:
      body = response.read()
      status = int(response.status)
  except urllib.error.HTTPError as exc:
    body = exc.read()
    status = int(exc.code)
  payload = json.loads(body.decode("utf-8")) if body else {}
  return status, payload


def http_status(
  url: str,
  *,
  timeout: float = 3.0,
  ssl_context: ssl.SSLContext | None = None,
) -> int:
  opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=ssl_context),
  )
  request = urllib.request.Request(url, headers={"User-Agent": "quantx-runtime/1"})
  try:
    with opener.open(request, timeout=timeout) as response:
      return int(response.status)
  except urllib.error.HTTPError as exc:
    return int(exc.code)


def wait_until(
  description: str,
  predicate: Callable[[], bool],
  timeout_seconds: float,
  interval_seconds: float = 0.4,
) -> None:
  deadline = time.monotonic() + timeout_seconds
  while time.monotonic() < deadline:
    try:
      if predicate():
        return
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
      pass
    time.sleep(interval_seconds)
  raise RuntimeCommandError(
    f"{description} did not become ready within {timeout_seconds:.0f} seconds"
  )


def tcp_reachable(host: str, port: int, timeout: float = 0.7) -> bool:
  try:
    with socket.create_connection((host, port), timeout=timeout):
      return True
  except OSError:
    return False


def tcp_port_available(port: int) -> bool:
  probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  try:
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    probe.bind(("0.0.0.0", port))
  except OSError:
    return False
  finally:
    probe.close()
  return True


def lsof_port_owner(port: int) -> dict[str, Any] | None:
  lsof = shutil.which("lsof")
  if not lsof:
    return None
  completed = subprocess.run(
    [lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"],
    check=False,
    capture_output=True,
    text=True,
    timeout=5,
  )
  pid = 0
  name = "unknown"
  for line in completed.stdout.splitlines():
    if line.startswith("p") and line[1:].isdigit() and not pid:
      pid = int(line[1:])
    elif line.startswith("c") and len(line) > 1 and name == "unknown":
      name = line[1:]
  if not pid:
    return None
  executable = "unavailable"
  try:
    executable = psutil.Process(pid).exe()
  except (OSError, psutil.Error):
    pass
  return {"port": port, "pid": pid, "name": name, "executable": executable}


def port_owner(port: int) -> dict[str, Any] | None:
  if tcp_port_available(port):
    return None
  try:
    connections = psutil.net_connections(kind="tcp")
  except (OSError, psutil.Error):
    connections = []
  for connection in connections:
    if connection.status != psutil.CONN_LISTEN or not connection.laddr:
      continue
    if int(connection.laddr.port) != port:
      continue
    pid = int(connection.pid or 0)
    name = "unknown"
    executable = "unavailable"
    if pid:
      try:
        process = psutil.Process(pid)
        name = process.name()
        executable = process.exe()
      except (OSError, psutil.Error):
        pass
    return {"port": port, "pid": pid, "name": name, "executable": executable}
  return lsof_port_owner(port) or {
    "port": port,
    "pid": 0,
    "name": "unknown",
    "executable": "unavailable",
  }


def assert_ports_available(ports: Iterable[int]) -> None:
  conflicts = [owner for port in ports if (owner := port_owner(port)) is not None]
  if not conflicts:
    return
  for conflict in conflicts:
    print(
      "Port {port} is already listening: PID={pid} process={name} "
      "executable={executable}".format(**conflict),
      file=sys.stderr,
    )
  raise RuntimeCommandError(
    "QuantX will not stop or replace untracked port owners", EXIT_ALREADY_RUNNING
  )


def check_external_dependencies(
  paths: RuntimePaths,
  python: Path,
  configuration: RuntimeConfiguration,
) -> None:
  completed = subprocess.run(
    [
      str(python),
      "-m",
      "quantx_infrastructure.diagnostics.external_dependencies",
    ],
    cwd=paths.root,
    env=configuration.process_environment,
    check=False,
    capture_output=True,
    text=True,
    timeout=30,
  )
  try:
    dependencies = json.loads(completed.stdout)
  except json.JSONDecodeError as exc:
    raise RuntimeCommandError(
      "External dependency diagnostics did not return valid JSON",
      EXIT_UNAVAILABLE,
    ) from exc
  failures: list[str] = []
  for name in ("PostgreSQL", "Redis", "InfluxDB"):
    detail = dict(dependencies.get(name) or {})
    status = str(detail.get("status") or "unavailable")
    endpoint = str(detail.get("endpoint") or "not-configured")
    version = str(detail.get("version") or "unknown")
    print(f"{name}: {status} endpoint={endpoint} version={version}")
    if status != "reachable":
      failures.append(name)
    if name == "PostgreSQL" and status == "reachable":
      path_counts = dict(detail.get("windowsAbsolutePaths") or {})
      path_count = int(detail.get("windowsAbsolutePathCount") or 0)
      print(
        "Shared path audit: "
        f"{'clean' if path_count == 0 else 'blocked'} "
        f"windowsAbsolutePathCount={path_count} fields={path_counts}"
      )
      historical_path_counts = dict(
        detail.get("historicalWindowsAbsolutePaths") or {}
      )
      historical_path_count = int(
        detail.get("historicalWindowsAbsolutePathCount") or 0
      )
      print(
        "Historical terminal path audit: "
        f"windowsAbsolutePathCount={historical_path_count} "
        f"fields={historical_path_counts}"
      )
      engine_lease_held = bool(detail.get("engineLeaseHeld"))
      print(
        "Engine singleton lease: "
        f"{'BLOCKED / HELD_BY_ANOTHER_RUNTIME' if engine_lease_held else 'available'}"
      )
      if path_count:
        failures.append("Windows absolute paths")
      if engine_lease_held:
        failures.append("Engine singleton lease")
  prefect_url = configuration.process_environment["PREFECT_API_URL"].rstrip("/")
  try:
    status = http_status(f"{prefect_url}/health", timeout=5)
    prefect_ready = 200 <= status < 400
  except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
    prefect_ready = False
  print(
    f"Prefect: {'reachable' if prefect_ready else 'unavailable'} endpoint={prefect_url}"
  )
  if not prefect_ready:
    failures.append("Prefect")
  if completed.returncode or failures:
    raise RuntimeCommandError(
      "Required external dependencies are unavailable: " + ", ".join(failures),
      EXIT_UNAVAILABLE,
    )


def component_graph(
  paths: RuntimePaths,
  configuration: RuntimeConfiguration,
  python: Path,
  ai_python: Path,
  node: Path,
  caddy: Path,
) -> list[ComponentSpec]:
  supervisor = paths.root / "ops/supervise_process.py"
  vite = paths.root / "node_modules/vite/bin/vite.js"
  vitepress = paths.root / "node_modules/vitepress/bin/vitepress.js"
  for dependency in (supervisor, vite, vitepress):
    if not dependency.is_file():
      raise RuntimeCommandError(
        f"Runtime dependency is missing: {dependency}", EXIT_UNAVAILABLE
      )
  engine_instance_id = str(uuid.uuid4())
  specs = [
    ComponentSpec(
      "sleep-guard",
      ("/usr/bin/caffeinate", "-dimsu"),
      paths.root,
      {},
      ReadinessProbe("process", timeout_seconds=5),
    ),
    ComponentSpec(
      "market-gateway",
      (
        str(python),
        "-m",
        "uvicorn",
        "quantx_api.market_gateway:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(MARKET_GATEWAY_PORT),
        "--ws-max-size",
        "67108864",
        "--ws-ping-interval",
        "20",
        "--ws-ping-timeout",
        str(AGENT_WEBSOCKET_PING_TIMEOUT_SECONDS),
      ),
      paths.root,
      {"DATABASE_PROCESS_ROLE": "market-gateway"},
      ReadinessProbe(
        "http", f"http://127.0.0.1:{MARKET_GATEWAY_PORT}/health/ready", 60
      ),
    ),
    ComponentSpec(
      "api",
      (
        str(python),
        "-m",
        "uvicorn",
        "quantx_api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(API_PORT),
        "--ws-max-size",
        "67108864",
        "--ws-ping-interval",
        "20",
        "--ws-ping-timeout",
        str(AGENT_WEBSOCKET_PING_TIMEOUT_SECONDS),
      ),
      paths.root,
      {"DATABASE_PROCESS_ROLE": "api"},
      ReadinessProbe("http", f"http://127.0.0.1:{API_PORT}/health/live", 90),
    ),
    ComponentSpec(
      "engine",
      (str(python), "-m", "quantx_engine.main"),
      paths.root,
      {
        "DATABASE_PROCESS_ROLE": "engine",
        "QUANTX_ENGINE_INSTANCE_ID": engine_instance_id,
      },
      ReadinessProbe(
        "api-component",
        "engine",
        120,
        expected_instance_id=engine_instance_id,
      ),
    ),
  ]
  if (
    configuration.process_environment.get("AI_ASSISTANT_ENABLED", "true")
    .strip()
    .lower()
    == "true"
  ):
    specs.append(
      ComponentSpec(
        "ai-runtime",
        (str(ai_python), "-m", "quantx_ai_runtime.main"),
        paths.root,
        {"DATABASE_PROCESS_ROLE": "ai-runtime"},
        ReadinessProbe("process", timeout_seconds=10),
      )
    )
  if configuration.profile == "full":
    specs.append(
      ComponentSpec(
        "worker",
        (str(python), "-m", "quantx_worker.main"),
        paths.root,
        {
          "DATABASE_PROCESS_ROLE": "worker",
          "PREFECT_WORKER_NAME": MACOS_PREFECT_WORKER_NAME,
        },
        ReadinessProbe("api-worker", MACOS_PREFECT_WORKER_NAME, 180),
      )
    )
  specs.extend(
    [
      ComponentSpec(
        "web",
        (str(node), str(vite), "--host", "127.0.0.1", "--port", str(WEB_PORT)),
        paths.root / "apps/web",
        {},
        ReadinessProbe("tcp", str(WEB_PORT), 60),
      ),
      ComponentSpec(
        "docs",
        (
          str(node),
          str(vitepress),
          "dev",
          "--host",
          "127.0.0.1",
          "--port",
          str(DOCS_PORT),
          "--strictPort",
        ),
        paths.root / "apps/docs",
        {},
        ReadinessProbe("tcp", str(DOCS_PORT), 60),
      ),
      ComponentSpec(
        "caddy",
        (
          str(caddy),
          "run",
          "--config",
          str(paths.root / "ops/caddy/Caddyfile.dev"),
          "--adapter",
          "caddyfile",
        ),
        paths.root,
        {},
        ReadinessProbe("public", configuration.public_url, 90),
      ),
    ]
  )
  order = {name: index for index, name in enumerate(START_ORDER)}
  return sorted(specs, key=lambda item: order[item.name])


def supervisor_command(
  paths: RuntimePaths,
  python: Path,
  spec: ComponentSpec,
  *,
  state_dir: Path | None = None,
  log_dir: Path | None = None,
) -> tuple[str, ...]:
  return (
    str(python),
    str(paths.root / "ops/supervise_process.py"),
    "--name",
    spec.name,
    "--state-dir",
    str(state_dir or paths.supervisor_state_dir),
    "--log-dir",
    str(log_dir or paths.log_dir),
    "--",
    *spec.command,
  )


def new_runtime_state(
  paths: RuntimePaths,
  configuration: RuntimeConfiguration,
) -> dict[str, Any]:
  return {
    "schemaVersion": STATE_SCHEMA_VERSION,
    "kind": "macos-dev",
    "runtimeId": str(uuid.uuid4()),
    "root": str(paths.root),
    "environment": configuration.environment,
    "profile": configuration.profile,
    "agentMode": configuration.agent_mode,
    "configuredAccount": configuration.configured_account,
    "configuredLive": configuration.configured_live,
    "publicUrl": configuration.public_url,
    "createdAt": utc_now_iso(),
    "updatedAt": utc_now_iso(),
    "components": [],
  }


def write_runtime_state(path: Path, state: dict[str, Any]) -> None:
  state["updatedAt"] = utc_now_iso()
  atomic_write_json(path, state)


def terminate_unrecorded_process_group(process: subprocess.Popen[Any]) -> None:
  """Best-effort cleanup when a supervisor started before state was durable."""

  if process.poll() is not None:
    return
  try:
    process_group_id = os.getpgid(process.pid)
  except (OSError, ProcessLookupError):
    return
  with contextlib.suppress(OSError, ProcessLookupError):
    os.killpg(process_group_id, signal.SIGTERM)
  try:
    process.wait(timeout=5)
    return
  except subprocess.TimeoutExpired:
    pass
  with contextlib.suppress(OSError, ProcessLookupError):
    os.killpg(process_group_id, signal.SIGKILL)
  with contextlib.suppress(subprocess.TimeoutExpired):
    process.wait(timeout=2)


def start_component(
  *,
  paths: RuntimePaths,
  configuration: RuntimeConfiguration,
  python: Path,
  spec: ComponentSpec,
  state: dict[str, Any],
) -> dict[str, Any]:
  component_runtime = ensure_runtime_path(
    paths.component_dir / spec.name, paths.runtime
  )
  component_runtime.mkdir(parents=True, exist_ok=True)
  environment = dict(configuration.process_environment)
  environment.update(spec.environment)
  environment["QUANTX_COMPONENT_RUNTIME_DIR"] = str(component_runtime)
  command = supervisor_command(paths, python, spec)
  supervisor_stdout = (paths.log_dir / f"{spec.name}.supervisor.stdout.log").open("ab")
  supervisor_stderr = (paths.log_dir / f"{spec.name}.supervisor.stderr.log").open("ab")
  try:
    process = subprocess.Popen(
      list(command),
      cwd=spec.working_directory,
      env=environment,
      stdin=subprocess.DEVNULL,
      stdout=supervisor_stdout,
      stderr=supervisor_stderr,
      start_new_session=True,
    )
  finally:
    supervisor_stdout.close()
    supervisor_stderr.close()
  try:
    time.sleep(0.15)
    if process.poll() is not None:
      raise RuntimeCommandError(
        f"{spec.name} supervisor exited during startup with code {process.returncode}"
      )
    observed = psutil.Process(process.pid)
    entry = {
      "name": spec.name,
      "pid": process.pid,
      "processGroupId": os.getpgid(process.pid),
      "processStartedAt": observed.create_time(),
      "startedAt": utc_now_iso(),
      "commandDigest": command_digest(observed.cmdline()),
      "workingDirectory": str(spec.working_directory.resolve(strict=True)),
      "readiness": dataclasses.asdict(spec.readiness),
      "supervisorState": str(
        paths.supervisor_state_dir / f"{spec.name}-supervisor.json"
      ),
      "stdout": str(paths.log_dir / f"{spec.name}.stdout.log"),
      "stderr": str(paths.log_dir / f"{spec.name}.stderr.log"),
    }
  except BaseException:
    terminate_unrecorded_process_group(process)
    raise
  state["components"].append(entry)
  write_runtime_state(paths.state_file, state)
  print(
    f"Started {spec.name} (supervisor PID {process.pid}, PGID {entry['processGroupId']})"
  )
  return entry


def public_ssl_context(paths: RuntimePaths) -> ssl.SSLContext:
  root_certificate = paths.caddy_data_dir / "caddy/pki/authorities/local/root.crt"
  if not root_certificate.is_file():
    raise OSError("Caddy local CA root is not available yet")
  context = ssl.create_default_context()
  context.load_verify_locations(cafile=str(root_certificate))
  return context


def wait_for_component(
  paths: RuntimePaths,
  configuration: RuntimeConfiguration,
  entry: Mapping[str, Any],
  probe: ReadinessProbe,
) -> None:
  def still_owned() -> bool:
    if not process_matches_entry(entry):
      raise RuntimeCommandError(
        f"{entry.get('name')} supervisor exited before readiness"
      )
    return managed_component_state(entry) == "RUNNING"

  if probe.kind == "process":
    wait_until(
      f"{entry.get('name')} process",
      still_owned,
      probe.timeout_seconds,
      0.1,
    )
    return
  if probe.kind == "tcp":
    wait_until(
      f"{entry.get('name')} TCP port",
      lambda: still_owned() and tcp_reachable("127.0.0.1", int(probe.target)),
      probe.timeout_seconds,
    )
    return
  if probe.kind == "http":
    wait_until(
      f"{entry.get('name')} endpoint",
      lambda: still_owned() and 200 <= http_json(probe.target)[0] < 300,
      probe.timeout_seconds,
    )
    return
  if probe.kind == "api-component":

    def api_component_ready() -> bool:
      still_owned()
      status, payload = http_json(f"http://127.0.0.1:{API_PORT}/health/components")
      component = dict(dict(payload.get("components") or {}).get(probe.target) or {})
      return (
        200 <= status < 300
        and component.get("status") == "ready"
        and (
          not probe.expected_instance_id
          or component.get("instanceId") == probe.expected_instance_id
        )
      )

    wait_until(
      f"{entry.get('name')} API component status",
      api_component_ready,
      probe.timeout_seconds,
    )
    return
  if probe.kind == "api-worker":

    def api_worker_ready() -> bool:
      still_owned()
      status, payload = http_json(
        f"http://127.0.0.1:{API_PORT}/health/components"
      )
      worker = dict(dict(payload.get("components") or {}).get("worker") or {})
      online_names = {
        str(item.get("name") or "") for item in list(worker.get("workers") or [])
      }
      return (
        200 <= status < 300
        and worker.get("status") == "ready"
        and probe.target in online_names
      )

    wait_until(
      f"{entry.get('name')} named Prefect worker",
      api_worker_ready,
      probe.timeout_seconds,
    )
    return
  if probe.kind == "public":

    def public_ready() -> bool:
      still_owned()
      context = (
        public_ssl_context(paths)
        if configuration.public_url.startswith("https://")
        else None
      )
      health_status, _ = http_json(
        f"{configuration.public_url}/health/live", ssl_context=context
      )
      docs_status = http_status(
        f"{configuration.public_url}/docs/", ssl_context=context
      )
      return 200 <= health_status < 300 and 200 <= docs_status < 400

    wait_until("Caddy public endpoints", public_ready, probe.timeout_seconds)
    return
  raise RuntimeCommandError(f"Unknown readiness probe: {probe.kind}")


def stop_component(entry: Mapping[str, Any]) -> bool:
  name = str(entry.get("name") or "unknown")
  pid = int(entry.get("pid") or 0)
  pgid = int(entry.get("processGroupId") or 0)
  if not process_matches_entry(entry):
    print(f"Skipped stale {name} state (PID {pid}); ownership could not be verified")
    return True
  print(f"Stopping {name} (PID {pid}, PGID {pgid})")
  try:
    os.killpg(pgid, signal.SIGTERM)
  except ProcessLookupError:
    return True
  deadline = time.monotonic() + GRACEFUL_STOP_SECONDS
  while time.monotonic() < deadline:
    if not process_group_exists(pgid):
      return True
    time.sleep(0.2)
  print(f"{name} exceeded the graceful stop window; sending SIGKILL", file=sys.stderr)
  try:
    os.killpg(pgid, signal.SIGKILL)
  except ProcessLookupError:
    return True
  deadline = time.monotonic() + FORCED_STOP_SECONDS
  while time.monotonic() < deadline:
    if not process_group_exists(pgid):
      return True
    time.sleep(0.1)
  return not process_group_exists(pgid)


def stop_entries(entries: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
  order = {name: index for index, name in enumerate(STOP_ORDER)}
  sorted_entries = sorted(
    entries,
    key=lambda entry: order.get(str(entry.get("name")), len(order)),
  )
  remaining: list[Mapping[str, Any]] = []
  for entry in sorted_entries:
    if not stop_component(entry):
      remaining.append(entry)
  return remaining


def generate_docs_contracts(
  paths: RuntimePaths, node: Path, environment: Mapping[str, str]
) -> None:
  generator = paths.root / "apps/docs/scripts/generate-graphql-reference.mjs"
  completed = subprocess.run(
    [str(node), str(generator)],
    cwd=paths.root,
    env=dict(environment),
    check=False,
    timeout=120,
  )
  if completed.returncode:
    raise RuntimeCommandError("GraphQL documentation reference generation failed")


def invoke_up(args: argparse.Namespace, paths: RuntimePaths) -> int:
  if args.environment != "dev":
    raise RuntimeCommandError("The macOS orchestrator supports dev only")
  if args.component:
    if args.component != "monitor":
      raise RuntimeCommandError("up --component supports monitor only")
    return invoke_monitor_up(paths)

  paths.ensure_runtime_directories()
  existing = read_runtime_state(paths.state_file, paths.root)
  live_existing = [
    item
    for item in list((existing or {}).get("components") or [])
    if process_matches_entry(item)
  ]
  if live_existing:
    raise RuntimeCommandError(
      "QuantX already has managed macOS processes; run status or down",
      EXIT_ALREADY_RUNNING,
    )

  environment = load_process_environment(paths)
  configuration = resolve_runtime_configuration(
    paths=paths,
    requested_profile=args.profile,
    requested_mode=args.mode,
    requested_account=args.account_id,
    environment=environment,
  )
  validate_macos_safety(configuration)
  if args.profile == "web" and configuration.profile == "full":
    print("Promoted dev web to the authoritative full/live runtime")
  python = resolve_python(paths, configuration.process_environment)
  ai_python = resolve_ai_runtime_python(
    paths, configuration.process_environment, python
  )
  node, _ = resolve_node(paths)
  caddy = verify_locked_caddy(paths)
  validate_caddy_configuration(paths, caddy, configuration.process_environment)
  assert_ports_available(
    (CADDY_PORT, CADDY_ADMIN_PORT, API_PORT, MARKET_GATEWAY_PORT, WEB_PORT, DOCS_PORT)
  )
  check_external_dependencies(paths, python, configuration)
  generate_docs_contracts(paths, node, configuration.process_environment)
  graph = component_graph(paths, configuration, python, ai_python, node, caddy)
  if any(spec.name == "qmt-agent" for spec in graph):
    raise RuntimeCommandError("Mac component graph must never contain QMT Agent")

  state = new_runtime_state(paths, configuration)
  write_runtime_state(paths.state_file, state)
  current_component = "startup"
  try:
    for spec in graph:
      current_component = spec.name
      entry = start_component(
        paths=paths,
        configuration=configuration,
        python=python,
        spec=spec,
        state=state,
      )
      wait_for_component(paths, configuration, entry, spec.readiness)
  except BaseException as exc:
    remaining = stop_entries(list(state["components"]))
    state["components"] = list(remaining)
    state["status"] = "FAILED"
    state["failure"] = {
      "component": current_component,
      "reason": sanitized_error(str(exc) or exc.__class__.__name__),
      "at": utc_now_iso(),
    }
    write_runtime_state(paths.state_file, state)
    raise
  state["status"] = "RUNNING"
  write_runtime_state(paths.state_file, state)
  print(
    f"QuantX dev/{configuration.profile} ({configuration.agent_mode}) is available "
    f"at {configuration.public_url}"
  )
  if configuration.configured_live:
    print(
      "Remote QMT Agent is not owned by this launcher; effective live capability "
      "will open dynamically only after every server-side safety gate passes"
    )
  return 0


def monitor_configuration(paths: RuntimePaths) -> tuple[Path, dict[str, str]]:
  environment = load_process_environment(paths)
  python = resolve_python(paths, environment)
  environment["ENV"] = "development"
  environment["PYTHONUTF8"] = "1"
  environment["PYTHONIOENCODING"] = "utf-8"
  environment["PYTHONPATH"] = os.pathsep.join(
    str(paths.root / relative) for relative in SERVER_PYTHON_PATHS
  )
  environment["MONITOR_HOST"] = "127.0.0.1"
  environment["MONITOR_PORT"] = str(MONITOR_PORT)
  environment["MONITOR_DATABASE_PATH"] = str(
    paths.monitor_runtime / "quantx-monitor.sqlite3"
  )
  environment["MONITOR_PUBLIC_BASE_URL"] = environment.get(
    "PUBLIC_URL", "http://127.0.0.1:8080"
  ).rstrip("/")
  return python, environment


def invoke_monitor_up(paths: RuntimePaths) -> int:
  paths.ensure_monitor_directories()
  existing = read_runtime_state(paths.monitor_state_file, paths.root)
  if existing and any(process_matches_entry(item) for item in existing["components"]):
    raise RuntimeCommandError("QuantX Monitor is already running", EXIT_ALREADY_RUNNING)
  assert_ports_available((MONITOR_PORT,))
  python, environment = monitor_configuration(paths)
  configuration = RuntimeConfiguration(
    environment="dev",
    profile="monitor",
    agent_mode="not-requested",
    configured_account="",
    configured_live=False,
    public_url=environment["MONITOR_PUBLIC_BASE_URL"],
    trusted_ips=(),
    process_environment=environment,
  )
  state = new_runtime_state(paths, configuration)
  state["kind"] = "macos-monitor"
  spec = ComponentSpec(
    "monitor",
    (str(python), "-m", "quantx_monitor.main"),
    paths.root,
    {},
    ReadinessProbe("http", f"http://127.0.0.1:{MONITOR_PORT}/monitor/health/ready", 60),
  )
  command = supervisor_command(
    paths,
    python,
    spec,
    state_dir=paths.monitor_state_dir,
    log_dir=paths.monitor_log_dir,
  )
  stdout_path = paths.monitor_log_dir / "monitor.supervisor.stdout.log"
  stderr_path = paths.monitor_log_dir / "monitor.supervisor.stderr.log"
  with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
    process = subprocess.Popen(
      list(command),
      cwd=paths.root,
      env=environment,
      stdin=subprocess.DEVNULL,
      stdout=stdout,
      stderr=stderr,
      start_new_session=True,
    )
  try:
    time.sleep(0.15)
    if process.poll() is not None:
      raise RuntimeCommandError("Monitor supervisor exited during startup")
    observed = psutil.Process(process.pid)
    entry = {
      "name": "monitor",
      "pid": process.pid,
      "processGroupId": os.getpgid(process.pid),
      "processStartedAt": observed.create_time(),
      "startedAt": utc_now_iso(),
      "commandDigest": command_digest(observed.cmdline()),
      "workingDirectory": str(paths.root),
      "readiness": dataclasses.asdict(spec.readiness),
      "supervisorState": str(paths.monitor_state_dir / "monitor-supervisor.json"),
      "stdout": str(paths.monitor_log_dir / "monitor.stdout.log"),
      "stderr": str(paths.monitor_log_dir / "monitor.stderr.log"),
    }
  except BaseException:
    terminate_unrecorded_process_group(process)
    raise
  state["components"] = [entry]
  write_runtime_state(paths.monitor_state_file, state)
  try:
    wait_for_component(paths, configuration, entry, spec.readiness)
  except Exception:
    state["components"] = list(stop_entries([entry]))
    state["status"] = "FAILED"
    write_runtime_state(paths.monitor_state_file, state)
    raise
  state["status"] = "RUNNING"
  write_runtime_state(paths.monitor_state_file, state)
  print(f"QuantX Monitor is running independently on 127.0.0.1:{MONITOR_PORT}")
  return 0


def invoke_down(args: argparse.Namespace, paths: RuntimePaths) -> int:
  if args.environment != "dev":
    raise RuntimeCommandError("The macOS orchestrator supports dev only")
  if args.component and args.component != "monitor":
    raise RuntimeCommandError("down --component supports monitor only")
  state_path = (
    paths.monitor_state_file if args.component == "monitor" else paths.state_file
  )
  state = read_runtime_state(state_path, paths.root)
  if state is None or not state["components"]:
    label = "Monitor" if args.component == "monitor" else "development runtime"
    print(f"No managed QuantX {label} processes were recorded")
    return 0
  remaining = stop_entries(list(state["components"]))
  state["components"] = list(remaining)
  state["status"] = "STOP_FAILED" if remaining else "STOPPED"
  state["stoppedAt"] = utc_now_iso()
  write_runtime_state(state_path, state)
  if remaining:
    raise RuntimeCommandError(
      f"Could not stop {len(remaining)} verified process group(s)",
      EXIT_STOP_INCOMPLETE,
    )
  print(
    "Stopped QuantX Monitor"
    if args.component == "monitor"
    else "Stopped QuantX dev runtime"
  )
  return 0


def safe_runtime_health() -> dict[str, Any]:
  try:
    status, payload = http_json(
      f"http://127.0.0.1:{API_PORT}/health/components", timeout=5
    )
    if 200 <= status < 300:
      return dict(payload.get("components") or {})
  except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
    pass
  return {}


def safe_live_trading_health() -> dict[str, Any]:
  try:
    status, payload = http_json(
      f"http://127.0.0.1:{API_PORT}/health/runtime/live-trading", timeout=5
    )
    if 200 <= status < 300:
      return dict(payload.get("liveTrading") or {})
  except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
    pass
  return {}


def component_readiness_state(
  paths: RuntimePaths,
  state: Mapping[str, Any],
  entry: Mapping[str, Any],
  process_state: str,
) -> str:
  """Evaluate one stored readiness contract without changing runtime state."""

  if process_state != "RUNNING":
    return "NOT_READY"
  try:
    raw_probe = dict(entry.get("readiness") or {})
    probe = ReadinessProbe(
      kind=str(raw_probe["kind"]),
      target=str(raw_probe.get("target") or ""),
      timeout_seconds=float(raw_probe.get("timeout_seconds") or 60),
      expected_instance_id=str(raw_probe.get("expected_instance_id") or ""),
    )
    if probe.kind == "process":
      ready = True
    elif probe.kind == "tcp":
      ready = tcp_reachable("127.0.0.1", int(probe.target), timeout=0.8)
    elif probe.kind == "http":
      status, _ = http_json(probe.target, timeout=1.0)
      ready = 200 <= status < 300
    elif probe.kind == "api-component":
      status, payload = http_json(
        f"http://127.0.0.1:{API_PORT}/health/components",
        timeout=1.0,
      )
      component = dict(dict(payload.get("components") or {}).get(probe.target) or {})
      ready = bool(
        200 <= status < 300
        and component.get("status") == "ready"
        and (
          not probe.expected_instance_id
          or component.get("instanceId") == probe.expected_instance_id
        )
      )
    elif probe.kind == "api-worker":
      status, payload = http_json(
        f"http://127.0.0.1:{API_PORT}/health/components",
        timeout=1.0,
      )
      worker = dict(dict(payload.get("components") or {}).get("worker") or {})
      online_names = {
        str(item.get("name") or "") for item in list(worker.get("workers") or [])
      }
      ready = bool(
        200 <= status < 300
        and worker.get("status") == "ready"
        and probe.target in online_names
      )
    elif probe.kind == "public":
      public_url = str(probe.target or state.get("publicUrl") or "").rstrip("/")
      context = public_ssl_context(paths) if public_url.startswith("https://") else None
      health_status, _ = http_json(
        f"{public_url}/health/live",
        timeout=1.5,
        ssl_context=context,
      )
      docs_status = http_status(
        f"{public_url}/docs/",
        timeout=1.5,
        ssl_context=context,
      )
      ready = 200 <= health_status < 300 and 200 <= docs_status < 400
    else:
      return "UNKNOWN"
    return "READY" if ready else "NOT_READY"
  except (
    KeyError,
    OSError,
    TypeError,
    ValueError,
    urllib.error.URLError,
    json.JSONDecodeError,
  ):
    return "NOT_READY"


def qmt_status_label(
  state: Mapping[str, Any],
  qmt: Mapping[str, Any],
  live_health: Mapping[str, Any],
) -> str:
  if not bool(state.get("configuredLive")):
    return "NOT_REQUIRED"
  snapshot_age_raw = qmt.get("latestSnapshotAgeSeconds")
  snapshot_age = (
    float(snapshot_age_raw) if snapshot_age_raw is not None else float("inf")
  )
  ready = (
    str(qmt.get("status") or "").lower() == "ready"
    and int(qmt.get("readyDevices") or 0) == 1
    and "live" in list(qmt.get("modes") or [])
    and "1.1" in list(qmt.get("protocolVersions") or [])
    and state.get("configuredAccount") in list(qmt.get("accountIds") or [])
    and snapshot_age < 90
  )
  if ready and str(live_health.get("agentStatus") or "").upper() == "READY":
    return "READY"
  if int(qmt.get("connectedDevices") or 0) == 0:
    return "BLOCKED / REMOTE_AGENT_OFFLINE"
  return "BLOCKED / REMOTE_AGENT_NOT_RECONCILED"


def invoke_status(args: argparse.Namespace, paths: RuntimePaths) -> int:
  if args.environment != "dev":
    raise RuntimeCommandError("The macOS orchestrator supports dev only")
  if args.component and args.component != "monitor":
    raise RuntimeCommandError("status --component supports monitor only")
  state_path = (
    paths.monitor_state_file if args.component == "monitor" else paths.state_file
  )
  state = read_runtime_state(state_path, paths.root)
  if state is None:
    print(
      "No managed QuantX Monitor process was recorded"
      if args.component
      else "No managed development processes"
    )
    return 0
  components = list(state["components"])
  if not components:
    print(
      "No managed QuantX Monitor process was recorded"
      if args.component
      else "No managed development processes"
    )
    if str(state.get("status") or "").upper() == "FAILED":
      failure = dict(state.get("failure") or {})
      print(
        f"Last runtime status=FAILED component={failure.get('component') or '-'} "
        f"reason={failure.get('reason') or 'unknown'}"
      )
      print("Next step=inspect ./ops/quantx logs, correct the cause, then retry up")
    return 0
  readiness_states: list[str] = []
  for entry in components:
    process_state = managed_component_state(entry)
    readiness_state = component_readiness_state(
      paths,
      state,
      entry,
      process_state,
    )
    readiness_states.append(readiness_state)
    print(
      f"Component={entry.get('name')} PID={entry.get('pid')} "
      f"PGID={entry.get('processGroupId')} Process={process_state} "
      f"Readiness={readiness_state} "
      f"StartedAt={entry.get('startedAt')}"
    )
  if args.component == "monitor":
    readiness = "unavailable"
    try:
      status, payload = http_json(
        f"http://127.0.0.1:{MONITOR_PORT}/monitor/health/ready"
      )
      readiness = str(payload.get("status") or status)
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
      pass
    print(f"Monitor readiness={readiness}")
    return 0

  live_health = safe_live_trading_health()
  runtime_health = safe_runtime_health()
  qmt = dict(runtime_health.get("qmtAgent") or {})
  market_data = dict(runtime_health.get("marketData") or {})
  engine = dict(runtime_health.get("engine") or {})
  live_enabled = str(live_health.get("status") or "DISABLED").upper() == "ENABLED"
  qmt_label = qmt_status_label(state, qmt, live_health)
  local_running = all(
    managed_component_state(item) == "RUNNING" for item in components
  )
  local_ready = bool(readiness_states) and all(
    value == "READY" for value in readiness_states
  )
  if bool(state.get("configuredLive")):
    system = "READY" if local_running and local_ready and live_enabled else "DEGRADED"
  else:
    system = "READY" if local_running and local_ready else "DEGRADED"
  print(
    f"Runtime profile={state.get('profile')}, agentMode={state.get('agentMode')}, "
    f"configuredAccount={state.get('configuredAccount') or '-'}, "
    f"configuredLive={'true' if state.get('configuredLive') else 'false'}"
  )
  print(f"System={system}")
  print(f"liveTrading={'ENABLED' if live_enabled else 'DISABLED'}")
  print(f"QMT Agent={qmt_label}")
  print(
    "protocol={protocol}, account snapshot age={snapshot}, market stream={market}, "
    "reconciliation={reconciliation}, backup age={backup}, "
    "Engine singleton lease={lease}".format(
      protocol=live_health.get("protocolVersion") or "-",
      snapshot=(
        f"{float(live_health['snapshotAgeSeconds']):.1f}s"
        if live_health.get("snapshotAgeSeconds") is not None
        else "-"
      ),
      market=str(market_data.get("status") or "offline").upper(),
      reconciliation=live_health.get("reconciliationStatus") or "UNKNOWN",
      backup=(
        f"{float(live_health['backupAgeSeconds']):.1f}s"
        if live_health.get("backupAgeSeconds") is not None
        else "-"
      ),
      lease="HELD"
      if str(engine.get("status") or "").lower() == "ready"
      else "NOT_HELD",
    )
  )
  blocked_checks = list(live_health.get("blockedChecks") or [])
  if blocked_checks:
    print("Blocked checks=" + ",".join(str(value) for value in blocked_checks))
  return 0


def tail_lines(path: Path, count: int) -> list[str]:
  with path.open("r", encoding="utf-8", errors="replace") as handle:
    return list(deque(handle, maxlen=count))


def invoke_logs(args: argparse.Namespace, paths: RuntimePaths) -> int:
  if args.environment != "dev":
    raise RuntimeCommandError("The macOS orchestrator supports dev only")
  if args.component == "monitor":
    log_dir = paths.monitor_log_dir
    component_filter = "monitor"
  else:
    log_dir = paths.log_dir
    component_filter = args.component
  if not log_dir.is_dir():
    raise RuntimeCommandError("No matching managed macOS logs were found")
  candidates = sorted(log_dir.glob("*.log"))
  if component_filter:
    candidates = [
      path for path in candidates if path.name.startswith(f"{component_filter}.")
    ]
  if not candidates:
    raise RuntimeCommandError("No matching managed macOS logs were found")
  for path in candidates:
    print(f"[{path.name}] {path}")
    for line in tail_lines(path, args.tail):
      print(line, end="" if line.endswith("\n") else "\n")
  return 0


def invoke_bootstrap(paths: RuntimePaths) -> int:
  environment = load_process_environment(paths)
  python = resolve_python(paths, environment)
  caddy = install_locked_caddy(paths)
  node, _ = resolve_node(paths)
  print(f"Python={executable_version([str(python), '--version'])}")
  print(f"Node={executable_version([str(node), '--version'])}")
  print(f"Caddy={executable_version([str(caddy), 'version'])}")
  return 0


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(prog="quantx")
  subcommands = parser.add_subparsers(dest="command", required=True)

  up = subcommands.add_parser("up")
  up.add_argument("--environment", choices=("dev",), default="dev")
  up.add_argument("--profile", choices=("web", "full"), default="web")
  up.add_argument("--mode", choices=("data-only", "live"), default=None)
  up.add_argument("--account-id", default="")
  up.add_argument("--component", default="")

  for name in ("down", "status"):
    command = subcommands.add_parser(name)
    command.add_argument("--environment", choices=("dev",), default="dev")
    command.add_argument("--component", default="")

  logs = subcommands.add_parser("logs")
  logs.add_argument("--environment", choices=("dev",), default="dev")
  logs.add_argument("--component", default="")
  logs.add_argument("--tail", type=int, default=100)

  subcommands.add_parser("bootstrap")
  return parser


def sanitized_error(message: str) -> str:
  upper = message.upper()
  if any(marker in upper for marker in SENSITIVE_ENVIRONMENT_MARKERS):
    return "Runtime configuration failed; a sensitive setting was not valid"
  return message


def main(argv: Sequence[str] | None = None) -> int:
  parser = build_parser()
  args = parser.parse_args(argv)
  paths = RuntimePaths.from_root(repository_root())
  try:
    if args.command == "up":
      return invoke_up(args, paths)
    if args.command == "down":
      return invoke_down(args, paths)
    if args.command == "status":
      return invoke_status(args, paths)
    if args.command == "logs":
      if args.tail < 1 or args.tail > 5000:
        raise RuntimeCommandError("--tail must be between 1 and 5000")
      return invoke_logs(args, paths)
    if args.command == "bootstrap":
      return invoke_bootstrap(paths)
    parser.error(f"unsupported command: {args.command}")
  except RuntimeCommandError as exc:
    print(f"quantx: {sanitized_error(str(exc))}", file=sys.stderr)
    return exc.exit_code
  return 1


if __name__ == "__main__":
  raise SystemExit(main())
