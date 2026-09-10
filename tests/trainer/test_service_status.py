import asyncio
import json
import sys
from pathlib import Path

import pytest
from quantx_infrastructure.training_bundle_store import publication_lock
from quantx_trainer.service_status import ServiceReporter, service_status


@pytest.mark.asyncio
async def test_real_service_process_exit_releases_lease_without_false_liveness(tmp_path):
  config = tmp_path / "config.toml"
  config.write_text("fixture")
  root = tmp_path / "service"
  root.mkdir()
  script = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv.pop())
from quantx_infrastructure.training_bundle_store import publication_lock
from quantx_trainer.service_status import ServiceReporter
root, config = map(Path, sys.argv[1:])
with publication_lock(root):
  ServiceReporter(root, config).write('WORKER_LOOP')
  print('ready', flush=True)
  time.sleep(30)
"""
  process = await asyncio.create_subprocess_exec(
    sys.executable, "-c", script, str(root), str(config),
    str(Path(__file__).resolve().parents[2] / "apps/trainer/src"),
    stdout=asyncio.subprocess.PIPE,
  )
  try:
    assert await asyncio.wait_for(process.stdout.readline(), 5) == b"ready\n"
    assert service_status(tmp_path, config)["service"] == "ALIVE"
    process.terminate()
    await asyncio.wait_for(process.wait(), 5)
    assert service_status(tmp_path, config) == {
      "service": "OFFLINE", "execution_state": "NOT_INSPECTED",
    }
  finally:
    if process.returncode is None:
      process.kill()
      await process.wait()


def test_service_lease_and_live_identity_are_both_required(tmp_path):
  config = tmp_path / "config.toml"
  config.write_text("secret fixture")
  assert service_status(tmp_path, config)["service"] == "OFFLINE"
  root = tmp_path / "service"
  root.mkdir()
  with publication_lock(root):
    assert service_status(tmp_path, config)["service"] == "UNKNOWN"
    reporter = ServiceReporter(root, config)
    reporter.write()
    assert service_status(tmp_path, config) == {
      "service": "ALIVE",
      "phase": "PREFLIGHT",
      "code": reporter.identity["code"],
      "heartbeat_at": json.loads((root / "status.json").read_text())["updated_at"],
      "instance_id": reporter.identity["instance_id"],
      "execution_state": "NOT_INSPECTED",
    }
    reporter.write("WORKER_LOOP")
    result = service_status(tmp_path, config)
    assert result["phase"] == "WORKER_LOOP"
    assert "secret" not in json.dumps(result)
    assert "secret" not in (root / "status.json").read_text()
  # Leftover identity of a still-live process does not imply lease ownership.
  assert service_status(tmp_path, config) == {
    "service": "OFFLINE",
    "execution_state": "NOT_INSPECTED",
  }


@pytest.mark.parametrize(
  "fault", ["host", "pid", "birth", "exe", "config", "json", "future", "stale", "link", "nan"]
)
def test_uncertain_or_stale_service_evidence_is_never_alive(tmp_path, fault):
  config = tmp_path / "config.toml"
  config.write_text("fixture")
  root = tmp_path / "service"
  root.mkdir()
  with publication_lock(root):
    ServiceReporter(root, config).write()
    path = root / "status.json"
    value = json.loads(path.read_text())
    if fault == "host":
      value["host"] = "another-host"
    elif fault == "pid":
      value["pid"] = -1
    elif fault == "birth":
      value["created_at"] = 0
    elif fault == "exe":
      value["executable"] = "wrong-executable"
    elif fault == "config":
      config.write_text("changed")
    elif fault == "nan":
      value["updated_at"] = float("nan")
    elif fault == "future":
      value["updated_at"] += 60
    elif fault == "stale":
      value["updated_at"] -= 60
    path.write_text(json.dumps(value))
    if fault == "json":
      path.write_text("{")
    if fault == "link":
      path.unlink()
      path.symlink_to(config)
    assert service_status(tmp_path, config)["service"] == (
      "STALE" if fault in {"future", "stale"} else "UNKNOWN"
    )


def test_code_identity_is_captured_once_and_returned_with_heartbeat(tmp_path, monkeypatch):
  from quantx_trainer import service_status as module

  config = tmp_path / "config.toml"
  config.write_text("fixture")
  root = tmp_path / "service"
  root.mkdir()
  code = {"commit": "a" * 40, "manifest_sha256": "b" * 64, "dirty": False}
  monkeypatch.setattr(module, "_code_identity", lambda: dict(code))
  with publication_lock(root):
    reporter = ServiceReporter(root, config)
    reporter.write("WORKER_LOOP")
    first = service_status(tmp_path, config)
    code["commit"] = "c" * 40
    reporter.write()
    second = service_status(tmp_path, config)
    assert first["code"] == second["code"] == {"commit": "a" * 40, "manifest_sha256": "b" * 64, "dirty": False}
    assert second["heartbeat_at"] >= first["heartbeat_at"]


@pytest.mark.parametrize("code", [None, {}, {"commit": "/private/path", "manifest_sha256": "b" * 64, "dirty": False}, {"commit": "a" * 40, "manifest_sha256": None, "dirty": False}, {"commit": "a" * 40, "manifest_sha256": "b" * 64, "dirty": 0}])
def test_invalid_code_identity_never_escapes_status(tmp_path, code):
  config = tmp_path / "config.toml"
  config.write_text("fixture")
  root = tmp_path / "service"
  root.mkdir()
  with publication_lock(root):
    ServiceReporter(root, config).write()
    path = root / "status.json"
    payload = json.loads(path.read_text())
    payload["code"] = code
    path.write_text(json.dumps(payload))
    assert service_status(tmp_path, config) == {"service": "UNKNOWN", "execution_state": "NOT_INSPECTED"}


def test_reporter_projects_packaged_source_without_paths(monkeypatch):
  from quantx_research import packaged_source
  from quantx_trainer import service_status as module

  monkeypatch.setattr(packaged_source, "packaged_source_state", lambda root: {"commit": "a" * 40, "code_manifest_sha256": "b" * 64, "dirty": False, "unrelated": "/private/path"})
  assert module._code_identity() == {"commit": "a" * 40, "manifest_sha256": "b" * 64, "dirty": False}


@pytest.mark.parametrize("transient", [True, False])
def test_windows_status_replace_retries_are_bounded(tmp_path, monkeypatch, transient):
  from quantx_trainer import service_status as module

  target, temporary = tmp_path / "status.json", tmp_path / "status.partial"
  target.write_text("old")
  temporary.write_text("new")
  original = Path.replace
  attempts = []

  def replace(path, destination):
    attempts.append(path)
    if transient and len(attempts) > 1:
      return original(path, destination)
    error = PermissionError("sharing conflict")
    error.winerror = 32
    raise error

  clock = iter([0, 0] if transient else [0, 2])
  monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
  monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
  monkeypatch.setattr(Path, "replace", replace)
  if transient:
    module._replace_status(temporary, target)
    assert target.read_text() == "new"
    assert len(attempts) == 2
  else:
    with pytest.raises(PermissionError):
      module._replace_status(temporary, target)
    assert target.read_text() == "old"
    assert len(attempts) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing")
def test_windows_status_reader_does_not_abort_heartbeat(tmp_path):
  import threading

  config = tmp_path / "config.toml"
  config.write_text("fixture")
  root = tmp_path / "service"
  root.mkdir()
  reporter = ServiceReporter(root, config)
  reporter.write()
  with (root / "status.json").open("rb") as reader:
    release = threading.Timer(0.2, reader.close)
    release.start()
    try:
      reporter.write("REGISTERING")
    finally:
      release.join()
  assert json.loads((root / "status.json").read_text())["phase"] == "REGISTERING"
  assert not list(root.glob(".status-*.partial"))
