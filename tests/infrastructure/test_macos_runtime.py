from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest


def _load_runtime_module():
  path = Path(__file__).resolve().parents[2] / "ops" / "quantx.py"
  spec = importlib.util.spec_from_file_location("quantx_macos_runtime", path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module
  spec.loader.exec_module(module)
  return module


runtime = _load_runtime_module()


def _paths(tmp_path: Path):
  root = tmp_path / "workspace"
  root.mkdir()
  return runtime.RuntimePaths.from_root(root)


def _live_environment() -> dict[str, str]:
  return {
    "PUBLIC_URL": "https://quantx-dev.internal:8080",
    "QUANTX_CADDY_TRUSTED_IPS": "192.168.50.20/32",
    "PREFECT_API_URL": "http://prefect.internal:4200/api",
  }


def test_default_web_launch_promotes_to_static_full_live(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  paths = _paths(tmp_path)
  monkeypatch.setattr(runtime, "local_ipv4_addresses", lambda: ("127.0.0.1/32",))

  configuration = runtime.resolve_runtime_configuration(
    paths=paths,
    requested_profile="web",
    requested_mode=None,
    requested_account="account-1",
    environment=_live_environment(),
  )

  assert configuration.profile == "full"
  assert configuration.agent_mode == "live"
  assert configuration.configured_live is True
  assert configuration.configured_account == "account-1"
  assert configuration.process_environment["ENABLE_REAL_TRADING"] == "true"
  assert configuration.process_environment["QMT_REAL_TRADING_ENABLED"] == "true"
  assert configuration.process_environment["QMT_AGENT_LAUNCH_STATE"] == "REMOTE"
  assert configuration.process_environment["REAL_TRADING_ACCOUNT_ALLOWLIST"] == (
    '["account-1"]'
  )
  assert "QMT_AGENT_LAUNCH_STARTED_AT" not in configuration.process_environment
  assert configuration.trusted_ips == (
    "127.0.0.1/32",
    "192.168.50.20/32",
  )


def test_runtime_bypasses_process_proxy_for_quantx_dependency_hosts(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  paths = _paths(tmp_path)
  monkeypatch.setattr(runtime, "local_ipv4_addresses", lambda: ("127.0.0.1/32",))
  environment = {
    **_live_environment(),
    "ALL_PROXY": "socks5h://127.0.0.1:6153",
    "NO_PROXY": "existing.internal,localhost",
    "DATABASE_URL": "postgresql+asyncpg://user:secret@db.internal:32432/quantx",
    "REDIS_URL": "redis://redis.internal:30179/0",
    "INFLUXDB_HOST": "http://influx.internal:30081",
  }

  configuration = runtime.resolve_runtime_configuration(
    paths=paths,
    requested_profile="web",
    requested_mode=None,
    requested_account="account-1",
    environment=environment,
  )

  bypass = configuration.process_environment["NO_PROXY"].split(",")
  assert bypass == [
    "existing.internal",
    "localhost",
    "127.0.0.1",
    "::1",
    "db.internal",
    "redis.internal",
    "influx.internal",
    "prefect.internal",
    "quantx-dev.internal",
  ]
  assert configuration.process_environment["no_proxy"] == ",".join(bypass)
  assert configuration.process_environment["ALL_PROXY"] == (
    "socks5h://127.0.0.1:6153"
  )


def test_explicit_data_only_keeps_web_profile_and_closes_every_live_gate(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  paths = _paths(tmp_path)
  monkeypatch.setattr(runtime, "local_ipv4_addresses", lambda: ("127.0.0.1/32",))

  configuration = runtime.resolve_runtime_configuration(
    paths=paths,
    requested_profile="web",
    requested_mode="data-only",
    requested_account="",
    environment={"PUBLIC_URL": "http://127.0.0.1:8080"},
  )

  assert configuration.profile == "web"
  assert configuration.agent_mode == "data-only"
  assert configuration.configured_live is False
  assert configuration.process_environment["ENABLE_REAL_TRADING"] == "false"
  assert configuration.process_environment["QMT_REAL_TRADING_ENABLED"] == "false"
  assert configuration.process_environment["T_TRADE_LIVE_ENABLED"] == "false"
  assert configuration.process_environment["REAL_TRADING_ACCOUNT_ALLOWLIST"] == "[]"
  assert configuration.process_environment["QMT_AGENT_LAUNCH_STATE"] == (
    "NOT_REQUESTED"
  )
  assert configuration.process_environment["QUANTX_CADDY_BIND"] == "127.0.0.1"


@pytest.mark.parametrize(
  ("environment", "message"),
  [
    (
      {
        "PUBLIC_URL": "http://quantx-dev.internal:8080",
        "QUANTX_CADDY_TRUSTED_IPS": "192.168.50.20/32",
      },
      "stable HTTPS PUBLIC_URL",
    ),
    (
      {"PUBLIC_URL": "https://quantx-dev.internal:8080"},
      "QUANTX_CADDY_TRUSTED_IPS",
    ),
  ],
)
def test_live_runtime_rejects_insecure_public_configuration(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  environment: dict[str, str],
  message: str,
) -> None:
  paths = _paths(tmp_path)
  monkeypatch.setattr(runtime, "local_ipv4_addresses", lambda: ("127.0.0.1/32",))

  with pytest.raises(runtime.RuntimeCommandError, match=message):
    runtime.resolve_runtime_configuration(
      paths=paths,
      requested_profile="full",
      requested_mode="live",
      requested_account="account-1",
      environment=environment,
    )


def test_runtime_root_and_state_use_the_physical_workspace(
  tmp_path: Path,
) -> None:
  physical = tmp_path / "physical"
  physical.mkdir()
  linked = tmp_path / "linked"
  linked.symlink_to(physical, target_is_directory=True)

  paths = runtime.RuntimePaths.from_root(linked)
  paths.ensure_runtime_directories()
  state = {
    "schemaVersion": runtime.STATE_SCHEMA_VERSION,
    "root": str(physical.resolve()),
    "components": [],
  }
  runtime.atomic_write_json(paths.state_file, state)

  assert paths.root == physical.resolve()
  assert runtime.read_runtime_state(paths.state_file, physical) == state
  assert oct(paths.state_file.stat().st_mode & 0o777) == "0o600"


def test_corrupt_state_is_reported_without_being_replaced(tmp_path: Path) -> None:
  paths = _paths(tmp_path)
  paths.state_file.parent.mkdir(parents=True)
  paths.state_file.write_text("{broken", encoding="utf-8")

  with pytest.raises(runtime.RuntimeCommandError) as raised:
    runtime.read_runtime_state(paths.state_file, paths.root)

  assert raised.value.exit_code == runtime.EXIT_STATE_ERROR
  assert paths.state_file.read_text(encoding="utf-8") == "{broken"


def test_process_identity_rejects_pid_reuse_metadata() -> None:
  process = psutil.Process(os.getpid())
  entry = {
    "pid": process.pid,
    "processStartedAt": process.create_time(),
    "processGroupId": os.getpgid(process.pid),
    "commandDigest": runtime.command_digest(process.cmdline()),
  }

  assert runtime.process_matches_entry(entry) is True
  entry["processStartedAt"] = float(entry["processStartedAt"]) - 10
  assert runtime.process_matches_entry(entry) is False


def test_python_resolution_preserves_virtual_environment_symlink(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  paths = _paths(tmp_path)
  interpreter = paths.root / "toolchain/python3.13"
  interpreter.parent.mkdir(parents=True)
  interpreter.touch()
  virtual_environment_python = paths.root / ".venv/bin/python"
  virtual_environment_python.parent.mkdir(parents=True)
  virtual_environment_python.symlink_to(interpreter)
  monkeypatch.setattr(runtime, "validate_python_runtime", lambda *args: None)

  resolved = runtime.resolve_python(paths, {})

  assert resolved == virtual_environment_python
  assert resolved.is_symlink()


def test_unix_wrapper_resolves_explicit_relative_python_from_repository_root() -> None:
  root = Path(__file__).resolve().parents[2]
  wrapper = (root / "ops/quantx").read_text(encoding="utf-8")

  assert '*) python_executable="$repository_root/$python_executable" ;;' in wrapper
  assert 'exec "$python_executable" "$repository_root/ops/quantx.py" "$@"' in wrapper


def test_root_environment_file_is_loaded_with_explicit_precedence(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  paths = _paths(tmp_path)
  (paths.root / ".env").write_text(
    "QUANTX_TEST_ROOT_ONLY=root\nQUANTX_TEST_PRECEDENCE=root\n",
    encoding="utf-8",
  )
  api_environment = paths.root / "apps/api/.env"
  api_environment.parent.mkdir(parents=True)
  api_environment.write_text("QUANTX_TEST_PRECEDENCE=api\n", encoding="utf-8")
  (api_environment.parent / ".env.development").write_text(
    "QUANTX_TEST_PRECEDENCE=development\n",
    encoding="utf-8",
  )
  monkeypatch.setenv("QUANTX_TEST_PRECEDENCE", "process")
  monkeypatch.delenv("QUANTX_DEV_EXTERNAL_DEPENDENCY_HOST", raising=False)

  environment = runtime.load_process_environment(paths)

  assert environment["QUANTX_TEST_ROOT_ONLY"] == "root"
  assert environment["QUANTX_TEST_PRECEDENCE"] == "process"

  monkeypatch.delenv("QUANTX_TEST_PRECEDENCE")
  environment = runtime.load_process_environment(paths)

  assert environment["QUANTX_TEST_PRECEDENCE"] == "development"


def test_external_dependency_host_override_updates_every_service_endpoint(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  paths = _paths(tmp_path)
  environment_file = paths.root / "apps/api/.env.development"
  environment_file.parent.mkdir(parents=True)
  environment_file.write_text(
    "\n".join(
      (
        "QUANTX_DEV_EXTERNAL_DEPENDENCY_HOST=192.168.5.6",
        "DATABASE_URL=postgresql+asyncpg://user:secret@old.internal:32432/quantx",
        "REDIS_URL=redis://:secret@old.internal:30179/0",
        "INFLUXDB_HOST=http://old.internal:30081",
        "PREFECT_API_URL=http://old.internal:30420/api",
      )
    ),
    encoding="utf-8",
  )
  for name in (
    "QUANTX_DEV_EXTERNAL_DEPENDENCY_HOST",
    "DATABASE_URL",
    "REDIS_URL",
    "INFLUXDB_HOST",
    "PREFECT_API_URL",
  ):
    monkeypatch.delenv(name, raising=False)

  environment = runtime.load_process_environment(paths)

  assert environment["DATABASE_URL"] == (
    "postgresql+asyncpg://user:secret@192.168.5.6:32432/quantx"
  )
  assert environment["REDIS_URL"] == "redis://:secret@192.168.5.6:30179/0"
  assert environment["INFLUXDB_HOST"] == "http://192.168.5.6:30081"
  assert environment["PREFECT_API_URL"] == "http://192.168.5.6:30420/api"
  assert environment["REDIS_HOST"] == "192.168.5.6"


def test_stop_order_never_contains_or_contacts_qmt_agent(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls: list[str] = []
  monkeypatch.setattr(
    runtime,
    "stop_component",
    lambda entry: calls.append(str(entry["name"])) or True,
  )
  entries = [
    {"name": "caddy"},
    {"name": "api"},
    {"name": "engine"},
    {"name": "worker"},
    {"name": "web"},
  ]

  assert runtime.stop_entries(entries) == []
  assert calls == ["caddy", "web", "engine", "worker", "api"]
  assert "qmt-agent" not in runtime.START_ORDER
  assert "qmt-agent" not in runtime.STOP_ORDER
  assert "monitor" not in runtime.START_ORDER
  assert "monitor" not in runtime.STOP_ORDER


def test_component_graph_has_all_non_qmt_services_and_fixed_order(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  paths = _paths(tmp_path)
  for relative in (
    "ops/supervise_process.py",
    "node_modules/vite/bin/vite.js",
    "node_modules/vitepress/bin/vitepress.js",
  ):
    target = paths.root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()
  monkeypatch.setattr(runtime, "local_ipv4_addresses", lambda: ("127.0.0.1/32",))
  configuration = runtime.resolve_runtime_configuration(
    paths=paths,
    requested_profile="web",
    requested_mode=None,
    requested_account="account-1",
    environment=_live_environment(),
  )

  graph = runtime.component_graph(
    paths,
    configuration,
    Path("/python"),
    Path("/ai-python"),
    Path("/node"),
    Path("/caddy"),
  )

  assert [spec.name for spec in graph] == [
    "sleep-guard",
    "market-gateway",
    "api",
    "engine",
    "ai-runtime",
    "worker",
    "web",
    "docs",
    "caddy",
  ]
  assert all("quantx_qmt_agent" not in " ".join(spec.command) for spec in graph)
  engine = next(spec for spec in graph if spec.name == "engine")
  assert engine.environment["QUANTX_ENGINE_INSTANCE_ID"]
  assert (
    engine.readiness.expected_instance_id
    == engine.environment["QUANTX_ENGINE_INSTANCE_ID"]
  )
  worker = next(spec for spec in graph if spec.name == "worker")
  assert worker.environment == {
    **runtime.DIRECT_NETWORK_ENVIRONMENT,
    "DATABASE_PROCESS_ROLE": "worker",
    "PREFECT_WORKER_NAME": "quantx-macos-dev",
  }


def test_engine_readiness_rejects_another_hosts_ready_heartbeat(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  paths = _paths(tmp_path)
  configuration = runtime.resolve_runtime_configuration(
    paths=paths,
    requested_profile="web",
    requested_mode=None,
    requested_account="account-1",
    environment=_live_environment(),
  )
  entry = {"name": "engine"}
  responses = iter(
    (
      (200, {"components": {"engine": {"status": "ready", "instanceId": "windows"}}}),
      (200, {"components": {"engine": {"status": "ready", "instanceId": "mac"}}}),
    )
  )
  monkeypatch.setattr(runtime, "process_matches_entry", lambda _: True)
  monkeypatch.setattr(runtime, "managed_component_state", lambda _: "RUNNING")
  monkeypatch.setattr(runtime, "http_json", lambda *args, **kwargs: next(responses))

  def assert_instance_transition(
    _description: str,
    predicate,
    _timeout_seconds: float,
    _interval_seconds: float = 0.4,
  ) -> None:
    assert predicate() is False
    assert predicate() is True

  monkeypatch.setattr(runtime, "wait_until", assert_instance_transition)

  runtime.wait_for_component(
    paths,
    configuration,
    entry,
    runtime.ReadinessProbe(
      "api-component",
      "engine",
      expected_instance_id="mac",
    ),
  )


def test_macos_caddy_lock_matches_published_artifact() -> None:
  paths = runtime.RuntimePaths.from_root(Path(__file__).resolve().parents[2])
  tool = runtime.read_tool_lock(paths, "caddy-macos-arm64")

  assert tool == {
    "version": "2.11.4",
    "url": "https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_mac_arm64.tar.gz",
    "sha256": "9efb0af2d6cf09cfb5053c0e51721b9b3d4956d346234f39368d943d25a3c9a7",
    "installedSha256": "e9ebf99dfd4b72259debe1830c83e86c63fb89a88e28b4e7c5e78a35fa76c92d",
    "archive": True,
    "executable": "caddy",
  }


@pytest.mark.parametrize(
  ("postgres_overrides", "expected_error"),
  (
    (
      {
        "windowsAbsolutePaths": {"strategy_backtests.result_path": 1},
        "windowsAbsolutePathCount": 1,
      },
      "Windows absolute paths",
    ),
    (
      {
        "windowsAbsolutePaths": {},
        "windowsAbsolutePathCount": 0,
        "engineLeaseHeld": True,
      },
      "Engine singleton lease",
    ),
  ),
)
def test_external_dependency_preflight_blocks_unsafe_database_state(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  postgres_overrides: dict[str, object],
  expected_error: str,
) -> None:
  paths = _paths(tmp_path)
  configuration = runtime.RuntimeConfiguration(
    environment="dev",
    profile="full",
    agent_mode="live",
    configured_account="account-1",
    configured_live=True,
    public_url="https://quantx-dev.internal:8080",
    trusted_ips=("127.0.0.1/32",),
    process_environment={"PREFECT_API_URL": "http://prefect.internal/api"},
  )
  dependencies = {
    "PostgreSQL": {
      "status": "reachable",
      "endpoint": "postgres.internal:5432",
      "version": "17",
      **postgres_overrides,
    },
    "Redis": {
      "status": "reachable",
      "endpoint": "redis.internal:6379",
      "version": "8",
    },
    "InfluxDB": {
      "status": "reachable",
      "endpoint": "https://influx.internal",
      "version": "2",
    },
  }
  monkeypatch.setattr(
    runtime.subprocess,
    "run",
    lambda *args, **kwargs: subprocess.CompletedProcess(
      args[0],
      0,
      stdout=json.dumps(dependencies),
      stderr="",
    ),
  )
  monkeypatch.setattr(runtime, "http_status", lambda *args, **kwargs: 200)

  with pytest.raises(runtime.RuntimeCommandError, match=expected_error):
    runtime.check_external_dependencies(paths, Path("/python"), configuration)


def test_external_dependency_preflight_reports_terminal_history_without_blocking(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  paths = _paths(tmp_path)
  configuration = runtime.RuntimeConfiguration(
    environment="dev",
    profile="full",
    agent_mode="live",
    configured_account="account-1",
    configured_live=True,
    public_url="https://quantx-dev.internal:8080",
    trusted_ips=("127.0.0.1/32",),
    process_environment={"PREFECT_API_URL": "http://prefect.internal/api"},
  )
  dependencies = {
    "PostgreSQL": {
      "status": "reachable",
      "endpoint": "postgres.internal:5432",
      "version": "17",
      "windowsAbsolutePaths": {"market_data_transfer.storage_reference": 0},
      "windowsAbsolutePathCount": 0,
      "historicalWindowsAbsolutePaths": {
        "market_data_transfer.storage_reference": 6312,
      },
      "historicalWindowsAbsolutePathCount": 6312,
    },
    "Redis": {
      "status": "reachable",
      "endpoint": "redis.internal:6379",
      "version": "8",
    },
    "InfluxDB": {
      "status": "reachable",
      "endpoint": "https://influx.internal",
      "version": "3",
    },
  }
  monkeypatch.setattr(
    runtime.subprocess,
    "run",
    lambda *args, **kwargs: subprocess.CompletedProcess(
      args[0],
      0,
      stdout=json.dumps(dependencies),
      stderr="",
    ),
  )
  monkeypatch.setattr(runtime, "http_status", lambda *args, **kwargs: 200)

  runtime.check_external_dependencies(paths, Path("/python"), configuration)

  assert "Historical terminal path audit" in capsys.readouterr().out


def test_port_conflict_reports_owner_without_stopping_it(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    runtime,
    "port_owner",
    lambda port: (
      {
        "port": port,
        "pid": 123,
        "name": "unmanaged",
        "executable": "/usr/bin/unmanaged",
      }
      if port == runtime.CADDY_PORT
      else None
    ),
  )
  monkeypatch.setattr(
    runtime,
    "stop_component",
    lambda entry: pytest.fail("unmanaged port owner must not be stopped"),
  )

  with pytest.raises(runtime.RuntimeCommandError) as raised:
    runtime.assert_ports_available((runtime.CADDY_PORT, runtime.API_PORT))

  assert raised.value.exit_code == runtime.EXIT_ALREADY_RUNNING


def test_port_owner_uses_lsof_when_macos_denies_global_process_scan(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(runtime, "tcp_port_available", lambda _port: False)
  monkeypatch.setattr(
    runtime.psutil,
    "net_connections",
    lambda **_kwargs: (_ for _ in ()).throw(runtime.psutil.AccessDenied()),
  )
  monkeypatch.setattr(runtime.shutil, "which", lambda _name: "/usr/sbin/lsof")
  monkeypatch.setattr(
    runtime.subprocess,
    "run",
    lambda *args, **kwargs: subprocess.CompletedProcess(
      args[0],
      0,
      stdout="p123\ncunmanaged\n",
      stderr="",
    ),
  )
  monkeypatch.setattr(
    runtime.psutil,
    "Process",
    lambda _pid: SimpleNamespace(exe=lambda: "/usr/bin/unmanaged"),
  )

  assert runtime.port_owner(runtime.API_PORT) == {
    "port": runtime.API_PORT,
    "pid": 123,
    "name": "unmanaged",
    "executable": "/usr/bin/unmanaged",
  }


def test_partial_start_failure_cleans_only_components_started_this_round(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  paths = _paths(tmp_path)
  monkeypatch.setattr(runtime, "local_ipv4_addresses", lambda: ("127.0.0.1/32",))
  monkeypatch.setattr(runtime, "load_process_environment", lambda _: _live_environment())
  monkeypatch.setattr(runtime, "validate_macos_safety", lambda _: None)
  monkeypatch.setattr(runtime, "resolve_python", lambda *args: Path("/python"))
  monkeypatch.setattr(runtime, "resolve_ai_runtime_python", lambda *args: Path("/python"))
  monkeypatch.setattr(
    runtime,
    "resolve_node",
    lambda *args: (Path("/node"), Path("/npm")),
  )
  monkeypatch.setattr(runtime, "verify_locked_caddy", lambda _: Path("/caddy"))
  monkeypatch.setattr(runtime, "validate_caddy_configuration", lambda *args: None)
  monkeypatch.setattr(runtime, "assert_ports_available", lambda *args: None)
  monkeypatch.setattr(runtime, "check_external_dependencies", lambda *args: None)
  monkeypatch.setattr(runtime, "generate_docs_contracts", lambda *args: None)
  specs = [
    runtime.ComponentSpec(
      name,
      ("/bin/true",),
      paths.root,
      {},
      runtime.ReadinessProbe("process", timeout_seconds=1),
    )
    for name in ("api", "engine")
  ]
  monkeypatch.setattr(runtime, "component_graph", lambda *args: specs)
  started: list[str] = []
  cleaned: list[str] = []

  def start_component(*, spec, state, **kwargs):
    entry = {"name": spec.name}
    state["components"].append(entry)
    started.append(spec.name)
    return entry

  def wait_for_component(_paths, _configuration, entry, _probe):
    if entry["name"] == "engine":
      raise runtime.RuntimeCommandError("engine readiness failed")

  def stop_entries(entries):
    cleaned.extend(str(entry["name"]) for entry in entries)
    return []

  monkeypatch.setattr(runtime, "start_component", start_component)
  monkeypatch.setattr(runtime, "wait_for_component", wait_for_component)
  monkeypatch.setattr(runtime, "stop_entries", stop_entries)
  args = runtime.argparse.Namespace(
    environment="dev",
    component="",
    profile="web",
    mode=None,
    account_id="account-1",
  )

  with pytest.raises(runtime.RuntimeCommandError, match="engine readiness failed"):
    runtime.invoke_up(args, paths)

  assert started == ["api", "engine"]
  assert cleaned == ["api", "engine"]
  failed_state = runtime.read_runtime_state(paths.state_file, paths.root)
  assert failed_state is not None
  assert failed_state["status"] == "FAILED"
  assert failed_state["components"] == []
  assert failed_state["failure"]["component"] == "engine"
  assert failed_state["failure"]["reason"] == "engine readiness failed"

  status_args = runtime.argparse.Namespace(environment="dev", component="")
  assert runtime.invoke_status(status_args, paths) == 0
  output = capsys.readouterr().out
  assert "Last runtime status=FAILED component=engine" in output
  assert "Next step=inspect ./ops/quantx logs" in output


def test_status_distinguishes_process_liveness_from_service_readiness(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  paths = _paths(tmp_path)
  paths.ensure_runtime_directories()
  runtime.atomic_write_json(
    paths.state_file,
    {
      "schemaVersion": runtime.STATE_SCHEMA_VERSION,
      "root": str(paths.root),
      "profile": "web",
      "agentMode": "data-only",
      "configuredLive": False,
      "components": [
        {
          "name": "api",
          "pid": 123,
          "processGroupId": 123,
          "startedAt": "2026-08-27T00:00:00Z",
          "readiness": {
            "kind": "http",
            "target": "http://127.0.0.1:18081/health/live",
          },
        }
      ],
    },
  )
  monkeypatch.setattr(runtime, "managed_component_state", lambda _entry: "RUNNING")
  monkeypatch.setattr(runtime, "http_json", lambda *args, **kwargs: (503, {}))
  monkeypatch.setattr(runtime, "safe_runtime_health", lambda: {})
  monkeypatch.setattr(runtime, "safe_live_trading_health", lambda: {})

  args = runtime.argparse.Namespace(environment="dev", component="")
  assert runtime.invoke_status(args, paths) == 0

  output = capsys.readouterr().out
  assert "Process=RUNNING Readiness=NOT_READY" in output
  assert "System=DEGRADED" in output


def test_status_reuses_one_health_snapshot_for_engine_and_worker(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  paths = _paths(tmp_path)
  paths.ensure_runtime_directories()
  runtime.atomic_write_json(
    paths.state_file,
    {
      "schemaVersion": runtime.STATE_SCHEMA_VERSION,
      "root": str(paths.root),
      "profile": "full",
      "agentMode": "live",
      "configuredLive": True,
      "configuredAccount": "account-1",
      "components": [
        {
          "name": "engine",
          "pid": 123,
          "processGroupId": 123,
          "startedAt": "2026-08-27T00:00:00Z",
          "readiness": {
            "kind": "api-component",
            "target": "engine",
            "expected_instance_id": "mac-engine",
          },
        },
        {
          "name": "worker",
          "pid": 124,
          "processGroupId": 124,
          "startedAt": "2026-08-27T00:00:00Z",
          "readiness": {
            "kind": "api-worker",
            "target": "quantx-macos-dev",
          },
        },
      ],
    },
  )
  health_calls = 0

  def health_snapshot():
    nonlocal health_calls
    health_calls += 1
    return {
      "engine": {"status": "ready", "instanceId": "mac-engine"},
      "worker": {
        "status": "ready",
        "workers": [{"name": "quantx-macos-dev", "status": "ONLINE"}],
      },
      "qmtAgent": {"status": "offline", "connectedDevices": 0},
    }

  monkeypatch.setattr(runtime, "managed_component_state", lambda _entry: "RUNNING")
  monkeypatch.setattr(runtime, "safe_runtime_health", health_snapshot)
  monkeypatch.setattr(runtime, "safe_live_trading_health", lambda: {})
  monkeypatch.setattr(
    runtime,
    "http_json",
    lambda *args, **kwargs: pytest.fail("status must reuse its health snapshot"),
  )

  args = runtime.argparse.Namespace(environment="dev", component="")
  assert runtime.invoke_status(args, paths) == 0

  output = capsys.readouterr().out
  assert health_calls == 1
  assert "Component=engine" in output and "Readiness=READY" in output
  assert "Component=worker" in output and output.count("Readiness=READY") == 2


def test_repeated_up_is_rejected_before_preflight(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  paths = _paths(tmp_path)
  paths.ensure_runtime_directories()
  runtime.atomic_write_json(
    paths.state_file,
    {
      "schemaVersion": runtime.STATE_SCHEMA_VERSION,
      "root": str(paths.root),
      "components": [{"name": "api"}],
    },
  )
  monkeypatch.setattr(runtime, "process_matches_entry", lambda _: True)
  monkeypatch.setattr(
    runtime,
    "load_process_environment",
    lambda _: pytest.fail("preflight must not run for repeated up"),
  )
  args = runtime.argparse.Namespace(
    environment="dev",
    component="",
    profile="web",
    mode=None,
    account_id="account-1",
  )

  with pytest.raises(runtime.RuntimeCommandError) as raised:
    runtime.invoke_up(args, paths)

  assert raised.value.exit_code == runtime.EXIT_ALREADY_RUNNING


def test_repeated_down_is_idempotent_and_leaves_monitor_state_untouched(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  paths = _paths(tmp_path)
  paths.ensure_runtime_directories()
  paths.ensure_monitor_directories()
  main_state = {
    "schemaVersion": runtime.STATE_SCHEMA_VERSION,
    "root": str(paths.root),
    "components": [{"name": "api"}, {"name": "engine"}],
  }
  monitor_state = {
    "schemaVersion": runtime.STATE_SCHEMA_VERSION,
    "root": str(paths.root),
    "components": [{"name": "monitor"}],
  }
  runtime.atomic_write_json(paths.state_file, main_state)
  runtime.atomic_write_json(paths.monitor_state_file, monitor_state)
  stopped: list[str] = []

  def stop_entries(entries):
    stopped.extend(str(entry["name"]) for entry in entries)
    return []

  monkeypatch.setattr(runtime, "stop_entries", stop_entries)
  args = runtime.argparse.Namespace(environment="dev", component="")

  assert runtime.invoke_down(args, paths) == 0
  assert runtime.invoke_down(args, paths) == 0
  assert stopped == ["api", "engine"]
  assert runtime.read_runtime_state(paths.monitor_state_file, paths.root) == monitor_state


@pytest.mark.skipif(os.name == "nt", reason="Unix supervisor contract")
def test_unix_supervisor_lock_rotation_and_child_reaping(tmp_path: Path) -> None:
  root = Path(__file__).resolve().parents[2]
  state_dir = tmp_path / "state"
  log_dir = tmp_path / "logs"
  command = [
    sys.executable,
    str(root / "ops/supervise_process.py"),
    "--name",
    "contract",
    "--state-dir",
    str(state_dir),
    "--log-dir",
    str(log_dir),
    "--max-log-bytes",
    "32",
    "--log-backups",
    "2",
    "--",
    sys.executable,
    "-c",
    "import time; print('x' * 256, flush=True); time.sleep(60)",
  ]
  supervisor = subprocess.Popen(command, start_new_session=True)
  state_path = state_dir / "contract-supervisor.json"
  try:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not state_path.exists():
      time.sleep(0.05)
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    child_pid = int(state["childPid"])

    duplicate = subprocess.run(command, check=False, timeout=5)
    assert duplicate.returncode == 73

    deadline = time.monotonic() + 5
    rotated = log_dir / "contract.stdout.log.1"
    while time.monotonic() < deadline and not rotated.exists():
      time.sleep(0.05)
    assert rotated.exists()

    os.killpg(os.getpgid(supervisor.pid), signal.SIGTERM)
    assert supervisor.wait(timeout=15) == 0
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and psutil.pid_exists(child_pid):
      time.sleep(0.05)
    assert not psutil.pid_exists(child_pid)
    final_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert final_state["status"] == "STOPPED"
    assert final_state["childPid"] == 0
  finally:
    if supervisor.poll() is None:
      os.killpg(os.getpgid(supervisor.pid), signal.SIGKILL)
      supervisor.wait(timeout=5)
