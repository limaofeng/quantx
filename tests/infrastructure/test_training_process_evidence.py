import json
import subprocess
import sys
from types import SimpleNamespace

import psutil
import pytest
from quantx_infrastructure import training_process_evidence as evidence


@pytest.fixture
def execution(tmp_path):
  root = tmp_path.resolve()
  request = root / "request.json"
  request.write_text('{"run_id":"run-1"}')
  path = root / "process.json"
  args = dict(run_id="run-1", owner="owner-1", request=request)
  evidence.begin_execution(path, **args)
  process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
  try:
    evidence.record_spawn(path, process=process, **args)
    yield path, args, process
  finally:
    if process.poll() is None:
      process.terminate()
    process.wait(timeout=5)


def supervisor_gone(monkeypatch, path):
  value = json.loads(path.read_text())
  original = evidence.psutil.Process

  def inspect(pid=None):
    if pid == value["supervisor"]["pid"]:
      raise psutil.NoSuchProcess(pid)
    return original(pid)

  monkeypatch.setattr(evidence.psutil, "Process", inspect)


def test_live_orphan_is_not_lost_after_supervisor_restart(execution, monkeypatch):
  path, args, process = execution
  supervisor_gone(monkeypatch, path)
  assert evidence.inspect_execution(path, **args) == "LIVE"
  process.terminate()
  process.wait(timeout=5)
  assert evidence.inspect_execution(path, **args) == "EXITED"


def test_live_supervisor_is_allowed_to_finish_upload_after_research_exit(execution):
  path, args, process = execution
  process.terminate()
  code = process.wait(timeout=5)
  evidence.record_exit(path, returncode=code, **args)
  assert evidence.inspect_execution(path, **args) == "LIVE"


@pytest.mark.parametrize("change", ["request", "owner", "host", "broken", "missing"])
def test_unverifiable_identity_never_means_exited(execution, change):
  path, args, _ = execution
  if change == "request":
    args["request"].write_text("changed request")
  elif change == "owner":
    args = {**args, "owner": "other-owner"}
  elif change == "host":
    value = json.loads(path.read_text())
    value["host"] = "other-host"
    path.write_text(json.dumps(value))
  elif change == "broken":
    path.write_text("[")
  else:
    path.unlink()
  assert evidence.inspect_execution(path, **args) == "UNKNOWN"


def test_pid_reuse_is_not_confused_with_old_computation(execution, monkeypatch):
  path, args, process = execution
  value = json.loads(path.read_text())

  def inspect(pid):
    if pid == value["supervisor"]["pid"]:
      raise psutil.NoSuchProcess(pid)
    assert pid == process.pid
    return SimpleNamespace(create_time=lambda: value["created_at"] + 1)

  monkeypatch.setattr(evidence.psutil, "Process", inspect)
  assert evidence.inspect_execution(path, **args) == "EXITED"


def test_permission_error_remains_unknown(execution, monkeypatch):
  path, args, _ = execution

  def denied(pid):
    raise psutil.AccessDenied(pid)

  monkeypatch.setattr(evidence.psutil, "Process", denied)
  assert evidence.inspect_execution(path, **args) == "UNKNOWN"


def test_existing_record_cannot_be_overwritten_by_another_launch(execution):
  path, args, _ = execution
  before = path.read_bytes()
  with pytest.raises(evidence.ProcessEvidenceError):
    evidence.begin_execution(path, **args)
  assert path.read_bytes() == before


def test_unrecorded_spawn_gap_remains_unknown_after_supervisor_exit(
  tmp_path, monkeypatch
):
  root = tmp_path.resolve()
  request = root / "request.json"
  request.write_text("{}")
  path = root / "process.json"
  args = dict(run_id="run-1", owner="owner", request=request)
  evidence.begin_execution(path, **args)
  supervisor_gone(monkeypatch, path)
  assert evidence.inspect_execution(path, **args) == "UNKNOWN"


def test_immediately_exited_child_can_be_recorded_without_a_live_pid(tmp_path):
  root = tmp_path.resolve()
  request = root / "request.json"
  request.write_text("{}")
  path = root / "process.json"
  args = dict(run_id="run-1", owner="owner", request=request)
  evidence.begin_execution(path, **args)
  process = subprocess.Popen([sys.executable, "-c", "pass"])
  process.wait(timeout=5)
  evidence.record_spawn(path, process=process, **args)
  value = json.loads(path.read_text())
  assert value["state"] == "EXITED"
  assert value["returncode"] == 0
  assert evidence.local_success_recorded(path, **args)
  assert not evidence.local_success_recorded(path, **{**args, "owner": "another-owner"})
