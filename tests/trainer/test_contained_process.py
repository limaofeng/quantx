import ctypes
import subprocess
import sys
import time
from unittest.mock import Mock

import psutil
import pytest
from quantx_trainer import contained_process as contained


@pytest.mark.parametrize("failure", [None, "create", "limits", "assign"])
def test_job_fails_closed_and_keeps_success_handle_until_exit(failure):
  kernel = Mock()
  kernel.CreateJobObjectW.return_value = 0 if failure == "create" else 123
  kernel.SetInformationJobObject.return_value = failure != "limits"
  kernel.AssignProcessToJobObject.return_value = failure != "assign"
  if failure:
    with pytest.raises(RuntimeError, match="WINDOWS_JOB_"):
      contained._enter_job(kernel)
    assert kernel.CloseHandle.call_count == (0 if failure == "create" else 1)
    if failure in {"create", "limits"}:
      kernel.AssignProcessToJobObject.assert_not_called()
  else:
    assert contained._enter_job(kernel) == 123
    kernel.CloseHandle.assert_not_called()
    kernel.CreateJobObjectW.assert_called_once_with(None, None)
    arguments = kernel.SetInformationJobObject.call_args.args
    assert arguments[1] == 9
    assert arguments[2]._obj.BasicLimitInformation.LimitFlags == 0x2000
    assert arguments[3] == ctypes.sizeof(contained._ExtendedLimits)
    assert ctypes.sizeof(contained._ExtendedLimits) == (
      144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112
    )


def test_bootstrap_assigns_before_import_and_preserves_exit(monkeypatch):
  events = []
  monkeypatch.setattr(contained, "_enter_job", lambda: events.append("job") or 123)
  monkeypatch.setattr(contained, "_JOB_HANDLE", None)
  monkeypatch.setattr(sys, "argv", ["bootstrap"])

  def run(module, **kwargs):
    assert events == ["job"]
    assert contained._JOB_HANDLE == 123
    assert sys.argv == ["quantx_research.cli", "command", "--request-file", "request"]
    assert kwargs == {"run_name": "__main__", "alter_sys": True}
    raise SystemExit(75)

  monkeypatch.setattr(contained.runpy, "run_module", run)
  with pytest.raises(SystemExit) as error:
    contained.main(["quantx_research.cli", "command", "--request-file", "request"])
  assert error.value.code == 75


def test_failed_containment_cannot_import_research(monkeypatch, capsys):
  monkeypatch.setattr(
    contained, "_enter_job", Mock(side_effect=RuntimeError("WINDOWS_JOB_ASSIGN_FAILED"))
  )
  run = Mock()
  monkeypatch.setattr(contained.runpy, "run_module", run)
  assert contained.main(["quantx_research.preparation_job", "request"]) == 2
  run.assert_not_called()
  assert capsys.readouterr().err.strip() == "WINDOWS_JOB_ASSIGN_FAILED"


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_research_commands_contain_both_windows_entrypoints(monkeypatch, platform):
  monkeypatch.setattr(sys, "platform", platform)
  for module in ["quantx_research.cli", "quantx_research.preparation_job"]:
    command = contained.research_command(module)
    assert command[:2] == [sys.executable, "-m"]
    assert command[2:] == (
      ["quantx_trainer.contained_process", module] if platform == "win32" else [module]
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Requires real Windows Job Objects")
@pytest.mark.parametrize("crash", [False, True])
def test_windows_job_terminates_descendant_on_owner_exit(tmp_path, crash):
  pidfile = tmp_path / "descendant.pid"
  code = """
import subprocess, sys, time
from pathlib import Path
from quantx_trainer.contained_process import _enter_job
job = _enter_job()
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], close_fds=True)
temporary = Path(sys.argv[1]).with_suffix('.tmp')
temporary.write_text(str(child.pid))
temporary.replace(sys.argv[1])
time.sleep(60 if sys.argv[2] == 'crash' else 1)
"""
  process = subprocess.Popen(
    [sys.executable, "-c", code, str(pidfile), "crash" if crash else "normal"]
  )
  descendant = None
  try:
    deadline = time.monotonic() + 10
    while not pidfile.exists() and time.monotonic() < deadline:
      assert process.poll() is None
      time.sleep(0.05)
    assert pidfile.exists()
    descendant = psutil.Process(int(pidfile.read_text()))
    if crash:
      process.kill()
    process.wait(timeout=10)
    descendant.wait(timeout=10)
    assert not descendant.is_running()
  finally:
    if process.poll() is None:
      process.kill()
      process.wait(timeout=10)
    if descendant is not None and descendant.is_running():
      descendant.kill()
      descendant.wait(timeout=10)
