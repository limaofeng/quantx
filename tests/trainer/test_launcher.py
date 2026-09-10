import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from quantx_trainer import launcher


def configuration(tmp_path):
  path = tmp_path / "config.toml"
  path.write_text("private-config-content")
  config = SimpleNamespace(
    state_root=tmp_path / "state",
    code_root=tmp_path,
    child_environment=lambda ambient: {"ENV": "development", "DATABASE_URL": "private"},
  )
  return config, path


def test_real_background_service_is_not_launched_twice(tmp_path, monkeypatch):
  config, path = configuration(tmp_path)
  create = subprocess.Popen
  processes = []
  script = """
import sys,time
from pathlib import Path
sys.path.insert(0, sys.argv.pop())
from quantx_trainer.service_status import ServiceReporter
from quantx_trainer.service_stop import stop_requested
from quantx_infrastructure.training_bundle_store import publication_lock
root = Path(sys.argv[1]) / 'service'
root.mkdir(parents=True, exist_ok=True)
with publication_lock(root):
  reporter = ServiceReporter(root, Path(sys.argv[2]))
  reporter.write('PREFLIGHT')
  deadline = time.monotonic() + 30
  while time.monotonic() < deadline and not stop_requested(root, reporter.identity['instance_id']):
    time.sleep(0.05)
"""

  def spawn(command, **kwargs):
    assert command == [
      sys.executable,
      "-m",
      "quantx_trainer.main",
      "serve",
      "--config",
      str(path),
    ]
    assert "DATABASE_URL" not in kwargs["env"]
    assert kwargs["close_fds"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert Path(kwargs["stdout"].name).name == "stdout.log"
    assert Path(kwargs["stderr"].name).name == "stderr.log"
    process = create(
      [
        sys.executable,
        "-c",
        script,
        str(config.state_root),
        str(path),
        str(Path(__file__).resolve().parents[2] / "apps/trainer/src"),
      ],
      **kwargs,
    )
    processes.append(process)
    return process

  monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
  try:
    assert launcher.start_service(config, path, startup_seconds=5)["service"] == "ALIVE"
    assert launcher.start_service(config, path)["service"] == "ALIVE"
    assert len(processes) == 1
    root = config.state_root / "service-launches"
    owner = json.loads((root / "current.json").read_text())["attempt"]
    assert "private" not in (root / owner / "request.json").read_text()
    evidence = json.loads((root / owner / "process.json").read_text())
    assert evidence["pid"] == processes[0].pid
    assert launcher.stop_service(config, path, stop_seconds=5)["service"] == "OFFLINE"
    assert processes[0].wait(timeout=5) == 0
  finally:
    for process in processes:
      if process.poll() is None:
        process.terminate()
      process.wait(timeout=5)


def test_unknown_spawn_persists_ambiguity_and_refuses_retry(tmp_path, monkeypatch):
  config, path = configuration(tmp_path)
  calls = []

  def spawn(*args, **kwargs):
    calls.append(True)
    raise OSError("spawn acknowledgement unavailable")

  monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
  with pytest.raises(RuntimeError, match="LAUNCH_UNCONFIRMED"):
    launcher.start_service(config, path)
  assert launcher.start_service(config, path)["service"] == "START_PENDING"
  assert len(calls) == 1


def test_confirmed_early_exit_allows_a_new_attempt(tmp_path, monkeypatch):
  from quantx_trainer.run_log import read_service_launch_logs

  config, path = configuration(tmp_path)
  create = subprocess.Popen
  processes = []

  def spawn(*args, **kwargs):
    process = create(
      [
        sys.executable,
        "-c",
        "import sys; print('BOOTSTRAP_STARTED'); "
        "print('token=synthetic-secret', file=sys.stderr); raise SystemExit(2)",
      ],
      **kwargs,
    )
    processes.append(process)
    return process

  monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
  try:
    for _ in range(2):
      result = launcher.start_service(config, path, startup_seconds=5)
      assert result["service"] == "OFFLINE"
      assert result["reason"] == "SERVICE_PROCESS_EXITED"
      logs = read_service_launch_logs(config.state_root)
      assert [(entry["stream"], entry["message"]) for entry in logs] == [
        ("stdout", "BOOTSTRAP_STARTED"),
        ("stderr", "token=[REDACTED]"),
      ]
    assert len(processes) == 2
  finally:
    for process in processes:
      if process.poll() is None:
        process.kill()
      process.wait(timeout=5)


def test_startup_observation_timeout_never_restarts_live_attempt(tmp_path, monkeypatch):
  config, path = configuration(tmp_path)
  create = subprocess.Popen
  processes = []

  def spawn(*args, **kwargs):
    process = create([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
    processes.append(process)
    return process

  monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
  try:
    assert (
      launcher.start_service(config, path, startup_seconds=0)["service"]
      == "START_PENDING"
    )
    assert processes[0].poll() is None
    assert (
      launcher.start_service(config, path, startup_seconds=0)["service"]
      == "START_PENDING"
    )
    assert len(processes) == 1
  finally:
    for process in processes:
      if process.poll() is None:
        process.kill()
      process.wait(timeout=5)
