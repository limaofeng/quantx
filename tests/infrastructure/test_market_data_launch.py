"""Exercise launcher assembly without starting services or opening sockets."""

import importlib.util
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
  "profile,token", [("full", "x" * 48), ("web", "x" * 48), ("full", "")]
)
def test_macos_launch_orders_data_services_and_authenticates_readiness(
  tmp_path, monkeypatch, profile, token
):
  root = Path(__file__).resolve().parents[2]
  monkeypatch.syspath_prepend(str(root / "ops"))
  spec = importlib.util.spec_from_file_location(
    "isolated_macos_runtime", root / "ops/macos_runtime.py"
  )
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  monkeypatch.setattr(module, "ROOT", tmp_path)
  monkeypatch.setattr(module, "RUNTIME", tmp_path / "runtime")
  monkeypatch.setattr(module, "STATE", tmp_path / "runtime/processes.json")
  monkeypatch.setattr(module, "stop", lambda *_: None)
  monkeypatch.setattr(module.sys, "platform", "darwin")
  monkeypatch.setattr(module.sys, "argv", ["launcher", "up", "--profile", profile])
  monkeypatch.setattr(module.shutil, "which", lambda name: f"/fake/{name}")
  env = {
    key: "http://127.0.0.1:1234"
    for key in ("DATABASE_URL", "REDIS_URL", "INFLUXDB_HOST", "PREFECT_API_URL")
  }
  env["QUANTX_MARKET_DATA_INTERNAL_TOKEN"] = token
  monkeypatch.setattr(module, "load_environment", lambda *_: dict(env))
  monkeypatch.setattr(
    module.socket, "create_connection", lambda *_, **__: nullcontext()
  )
  monkeypatch.setattr(
    module.socket,
    "socket",
    lambda: nullcontext(
      SimpleNamespace(setsockopt=lambda *_: None, bind=lambda *_: None)
    ),
  )
  monkeypatch.setattr(
    module.psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 1)
  )
  launched, probes = [], []

  def start(command, **kwargs):
    launched.append((command, kwargs["env"]))
    return SimpleNamespace(pid=len(launched), poll=lambda: None)

  def probe(request, **kwargs):
    probes.append(request)
    return nullcontext(SimpleNamespace(status=200))

  monkeypatch.setattr(module.subprocess, "Popen", start)
  monkeypatch.setattr(module.urllib.request, "urlopen", probe)
  if not token:
    with pytest.raises(ValueError, match="QUANTX_MARKET_DATA_INTERNAL_TOKEN"):
      module.main()
    assert launched == []
    return
  module.main()
  assert "quantx_market_data.api:app" in launched[0][0]
  assert launched[0][0][-2:] == ["--workers", "1"]
  assert launched[1][0][-1] == "quantx_market_data.worker"
  assert launched[0][1]["DATABASE_PROCESS_ROLE"] == "market-data-api"
  assert launched[1][1]["DATABASE_PROCESS_ROLE"] == "market-data-worker"
  assert [request.full_url for request in probes[:2]] == [
    "http://127.0.0.1:18085/health/ready",
    "http://127.0.0.1:18085/health/worker",
  ]
  assert all(
    request.get_header("Authorization") == "Bearer " + token for request in probes[:2]
  )
  assert all(request.get_header("Authorization") is None for request in probes[2:])
  assert not any("quantx_qmt_agent" in " ".join(command) for command, _ in launched)
