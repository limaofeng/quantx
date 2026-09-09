import ctypes
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from quantx_trainer import contained_process, launcher
from quantx_trainer import windows_service_job as windows


@pytest.mark.parametrize("fault", [None, "birth", "membership", "query", "terminate"])
def test_job_control_requires_handle_identity_and_membership(tmp_path, fault):
  instance = "a" * 32
  (tmp_path / "status.json").write_text(
    json.dumps(
      {
        "instance_id": instance,
        "pid": 1234,
        "created_at": 100.0,
      }
    )
  )
  kernel = Mock()
  kernel.OpenJobObjectW.return_value = 11
  kernel.OpenProcess.return_value = 22

  def times(process, creation, *unused):
    assert process == 22
    ticks = 116444736000000000 + (101 if fault == "birth" else 100) * 10000000
    creation._obj.low, creation._obj.high = ticks & 0xFFFFFFFF, ticks >> 32
    return True

  def member(process, job, result):
    assert (process, job) == (22, 11)
    result._obj.value = int(fault != "membership")
    return True

  def query(job, kind, value, size, returned):
    assert job == 11 and kind == 1 and size == 48
    value._obj.ActiveProcesses = 2
    return fault != "query"

  kernel.GetProcessTimes.side_effect = times
  kernel.IsProcessInJob.side_effect = member
  kernel.QueryInformationJobObject.side_effect = query
  kernel.TerminateJobObject.return_value = fault != "terminate"
  if fault:
    with pytest.raises(RuntimeError):
      with windows.open_service_job(tmp_path, instance, kernel=kernel) as job:
        assert job.active_processes() == 2
        job.terminate()
  else:
    with windows.open_service_job(tmp_path, instance, kernel=kernel) as job:
      assert job.active_processes() == 2
      job.terminate()
    kernel.TerminateJobObject.assert_called_once_with(11, 1)
  if fault in {"birth", "membership", "query"}:
    kernel.TerminateJobObject.assert_not_called()
  assert [call.args[0] for call in kernel.CloseHandle.call_args_list] == [22, 11]
  kernel.OpenJobObjectW.assert_called_once_with(
    12, False, windows.service_job_name(instance)
  )
  assert ctypes.sizeof(windows._Accounting) == 48


def test_named_job_collision_never_modifies_existing_job():
  kernel = Mock()
  kernel.CreateJobObjectW.return_value = 123
  kernel.GetLastError.return_value = 183
  with pytest.raises(RuntimeError, match="ALREADY_EXISTS"):
    contained_process._enter_job(kernel, name=windows.service_job_name("a" * 32))
  kernel.SetInformationJobObject.assert_not_called()
  kernel.AssignProcessToJobObject.assert_not_called()
  kernel.CloseHandle.assert_called_once_with(123)


@pytest.mark.parametrize("forced", [False, True])
def test_down_waits_for_zero_members_and_marks_database_unreconciled(
  tmp_path, monkeypatch, forced
):
  job = Mock()
  job.active_processes.side_effect = [1, 0] if forced else [0]

  @contextmanager
  def opened(root, instance):
    assert root == tmp_path / "service" and instance == "a" * 32
    yield job

  (tmp_path / "service").mkdir()
  (tmp_path / "config").write_text("fixture")
  monkeypatch.setattr(windows, "open_service_job", opened)
  monkeypatch.setattr(launcher, "service_status", lambda *args: {"service": "OFFLINE"})
  monkeypatch.setattr(launcher.time, "sleep", lambda seconds: None)
  result = launcher._stop_windows_service(
    SimpleNamespace(state_root=tmp_path), tmp_path / "config", "a" * 32, 0
  )
  assert result == {
    "service": "OFFLINE",
    "execution_state": "GROUP_EXITED",
    "database_state": "NOT_RECONCILED",
  }
  assert job.terminate.call_count == int(forced)
  receipt = json.loads(
    (tmp_path / "service" / f"group-exit-{'a' * 32}.json").read_text()
  )
  assert receipt["active_processes"] == 0
  assert receipt["forced"] is forced


def test_nonzero_job_after_forced_timeout_remains_pending(tmp_path, monkeypatch):
  job = Mock(active_processes=Mock(return_value=1))

  @contextmanager
  def opened(*args):
    yield job

  (tmp_path / "service").mkdir()
  monkeypatch.setattr(windows, "open_service_job", opened)
  monkeypatch.setattr(launcher, "service_status", lambda *args: {"service": "OFFLINE"})
  ticks = iter([0, 1, 2, 10])
  monkeypatch.setattr(launcher.time, "monotonic", lambda: next(ticks))
  monkeypatch.setattr(launcher.time, "sleep", lambda seconds: None)
  assert (
    launcher._stop_windows_service(
      SimpleNamespace(state_root=tmp_path), tmp_path / "config", "a" * 32, 0
    )["service"]
    == "STOP_PENDING"
  )
  job.terminate.assert_called_once()


@pytest.mark.skipif(sys.platform != "win32", reason="Requires real Windows service Job")
def test_real_named_job_verifies_identity_and_reaps_group(tmp_path):
  config = tmp_path / "config.toml"
  config.write_text("fixture")
  script = """
import sys, subprocess, time, json
from pathlib import Path
sys.path.insert(0, sys.argv.pop())
from quantx_trainer.service_status import ServiceReporter
from quantx_trainer.contained_process import _enter_job
from quantx_trainer.windows_service_job import service_job_name
root = Path(sys.argv[1])
reporter = ServiceReporter(root, root / 'config.toml')
job = _enter_job(name=service_job_name(reporter.identity['instance_id']))
reporter.write()
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], close_fds=True)
(root / 'ready.tmp').write_text(reporter.identity['instance_id'])
(root / 'ready.tmp').replace(root / 'ready')
time.sleep(60)
"""
  process = subprocess.Popen(
    [
      sys.executable,
      "-c",
      script,
      str(tmp_path),
      str(Path(__file__).resolve().parents[2] / "apps/trainer/src"),
    ]
  )
  try:
    deadline = time.monotonic() + 10
    while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
      assert process.poll() is None
      time.sleep(0.05)
    instance = (tmp_path / "ready").read_text()
    with windows.open_service_job(tmp_path, instance) as job:
      assert job.active_processes() >= 2
      job.terminate()
      process.wait(timeout=5)
      # ActiveProcesses may retain a terminated member while handles reference it.
      process._handle.Close()
      deadline = time.monotonic() + 5
      while job.active_processes() and time.monotonic() < deadline:
        time.sleep(0.05)
      assert job.active_processes() == 0
  finally:
    if process.poll() is None:
      process.kill()
    process.wait(timeout=5)
