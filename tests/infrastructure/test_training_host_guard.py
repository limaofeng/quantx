import json
import os
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest
from quantx_infrastructure import training_host_guard as module
from quantx_infrastructure.training_host_guard import (
  SHANGHAI,
  HostAdmissionDenied,
  HostPolicy,
  HostResourceGuard,
)


@pytest.fixture
def root(tmp_path):
  root = tmp_path.resolve()
  (root / "policy.toml").write_text(
    f"""cpu_threads = 64
max_rss_mib = 65536
minimum_available_memory_mib = 1
minimum_free_disk_mib = 1
sample_seconds = 1
stop_grace_seconds = 1
disk_roots = [{json.dumps(str(root))}]
[[windows]]
weekdays = [0, 1, 2, 3, 4, 5, 6]
start = "00:00"
end = "23:59:59"
"""
  )
  return root


def evening():
  return datetime(2026, 9, 9, 20, tzinfo=SHANGHAI)


@pytest.mark.parametrize(
  ("hour", "minute", "reason"),
  [
    (9, 14, None),
    (9, 15, "TRADING_OR_POST_CLOSE_CRITICAL_WINDOW"),
    (16, 29, "TRADING_OR_POST_CLOSE_CRITICAL_WINDOW"),
    (16, 30, None),
  ],
)
def test_weekday_protection_independent_of_development_profile(
  root, monkeypatch, hour, minute, reason
):
  monkeypatch.setenv("ENV", "development")
  monkeypatch.setenv("QUANTX_PROFILE", "data-only")
  policy = HostPolicy.load(root)
  assert (
    policy.window_reason(datetime(2026, 9, 9, hour, minute, tzinfo=SHANGHAI)) == reason
  )


def test_no_naive_clock_or_unconfigured_window(root):
  policy = HostPolicy.load(root)
  assert policy.window_reason(datetime(2026, 9, 9)) == "HOST_CLOCK_UNKNOWN"
  assert (
    policy.window_reason(datetime(2026, 9, 9, 23, 59, 59, tzinfo=SHANGHAI))
    == "OUTSIDE_ALLOWED_TRAINING_WINDOW"
  )


@pytest.mark.parametrize(
  "replacement",
  [
    "",
    "cpu_threads = 0",
    "cpu_threads = true",
    "sample_seconds = 11",
    "stop_grace_seconds = 61",
    "disk_roots = []",
    "weekdays = [7]",
    'start = "23:00"\nend = "01:00"',
  ],
)
def test_missing_or_invalid_policy_fails_closed(root, replacement):
  path = root / "policy.toml"
  if not replacement:
    path.unlink()
  else:
    lines = path.read_text().splitlines()
    changed = {line.split(" = ")[0]: line for line in replacement.splitlines()}
    path.write_text(
      "\n".join(changed.get(line.split(" = ")[0], line) for line in lines)
    )
  with pytest.raises(HostAdmissionDenied, match="HOST_POLICY_MISSING_OR_INVALID"):
    HostPolicy.load(root)


def test_clean_exit_restores_environment_and_releases_lock(root, monkeypatch):
  monkeypatch.setenv("OMP_NUM_THREADS", "123")
  with HostResourceGuard(root, now=evening):
    assert os.environ["OMP_NUM_THREADS"] == "64"
    assert json.loads((root / "owner.json").read_text())["status"] == "RUNNING"
  assert os.environ["OMP_NUM_THREADS"] == "123"
  assert json.loads((root / "owner.json").read_text())["status"] == "RELEASED"
  with HostResourceGuard(root, now=evening):
    pass


def test_nested_entrypoints_share_the_same_process_admission(root, monkeypatch):
  monkeypatch.setattr(module, "host_guard_root", lambda: root)
  original = module.HostResourceGuard
  monkeypatch.setattr(
    module, "HostResourceGuard", lambda path: original(path, now=evening)
  )
  with module.high_resource_guard() as outer:
    with module.high_resource_guard() as inner:
      assert inner is outer
  assert module._active.guard is None


def test_cpu_budget_uses_measured_process_consumption(root, monkeypatch):
  guard = HostResourceGuard(root, now=evening)
  guard._cpu_sample = (99, 0)
  monkeypatch.setattr(module.time, "monotonic", lambda: 100)
  monkeypatch.setattr(
    guard.process, "cpu_times", lambda: SimpleNamespace(user=1000, system=0)
  )
  assert guard.resource_reason() == "TASK_CPU_BUDGET"


def invoke_child(root, body):
  code = (
    """import os
from datetime import datetime
from pathlib import Path
from quantx_infrastructure.training_host_guard import HostResourceGuard, SHANGHAI, HostAdmissionDenied
root = Path(os.environ["TEST_HOST_GUARD_ROOT"])
now = lambda: datetime(2026, 9, 9, 20, tzinfo=SHANGHAI)
"""
    + body
  )
  return subprocess.run(
    [sys.executable, "-c", code],
    env={
      **os.environ,
      "PYTHONPATH": os.pathsep.join(sys.path),
      "TEST_HOST_GUARD_ROOT": str(root),
    },
    capture_output=True,
    text=True,
    timeout=15,
  )


def test_os_lock_excludes_a_second_computation_process(root):
  with HostResourceGuard(root, now=evening):
    result = invoke_child(
      root,
      """try:
  with HostResourceGuard(root, now=now):
    raise RuntimeError("duplicate execution")
except HostAdmissionDenied:
  print("blocked")
""",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked"


def test_abrupt_exit_does_not_allow_stale_pid_based_reuse(root):
  result = invoke_child(
    root,
    """with HostResourceGuard(root, now=now):
  os._exit(0)
""",
  )
  assert result.returncode == 0, result.stderr
  owner = json.loads((root / "owner.json").read_text())
  assert owner["pid"] != os.getpid()
  assert owner["created_at"] > 0
  with pytest.raises(HostAdmissionDenied, match="UNRECONCILED"):
    with HostResourceGuard(root, now=evening):
      pytest.fail("must not reuse an unverified old execution")


def test_resource_and_window_rechecked_at_success_boundary(root):
  timestamp = [evening()]
  with pytest.raises(HostAdmissionDenied, match="TRADING_OR_POST_CLOSE"):
    with HostResourceGuard(root, now=lambda: timestamp[0]):
      timestamp[0] = datetime(2026, 9, 10, 9, 15, tzinfo=SHANGHAI)
  assert (
    json.loads((root / "owner.json").read_text())["reason"]
    == "TRADING_OR_POST_CLOSE_CRITICAL_WINDOW"
  )


@pytest.mark.parametrize("kind", ["memory", "disk", "rss", "unknown"])
def test_low_resources_or_unknown_state_prevent_entry(root, monkeypatch, kind):
  guard = HostResourceGuard(root, now=evening)
  if kind == "memory":
    monkeypatch.setattr(
      module.psutil, "virtual_memory", lambda: SimpleNamespace(available=0)
    )
  elif kind == "disk":
    monkeypatch.setattr(
      module.shutil, "disk_usage", lambda path: SimpleNamespace(free=0)
    )
  elif kind == "rss":
    monkeypatch.setattr(
      guard.process, "memory_info", lambda: SimpleNamespace(rss=2**50)
    )
  else:

    def missing():
      raise module.psutil.AccessDenied()

    monkeypatch.setattr(guard.process, "memory_info", missing)
  with pytest.raises(HostAdmissionDenied):
    with guard:
      pytest.fail("insufficient or unknown resources must block")
  assert not (root / "owner.json").exists()


def test_watchdog_requests_stop_then_forces_bounded_exit(root, monkeypatch):
  guard = HostResourceGuard(root, now=evening)
  monkeypatch.setattr(
    guard, "resource_reason", lambda: "OUTSIDE_ALLOWED_TRAINING_WINDOW"
  )
  monkeypatch.setattr(guard.stop, "wait", lambda timeout: False)
  calls = []
  monkeypatch.setattr(
    module._thread, "interrupt_main", lambda: calls.append("safe_stop")
  )

  class ForcedExit(BaseException):
    pass

  def force(code):
    calls.append(code)
    raise ForcedExit

  monkeypatch.setattr(module.os, "_exit", force)
  with pytest.raises(ForcedExit):
    guard._watch()
  assert calls == ["safe_stop", 75]
  assert json.loads((root / "owner.json").read_text())["status"] == "STOP_REQUESTED"


def test_corrupt_or_symlink_owner_cannot_bypass_lock(root):
  owner = root / "owner.json"
  owner.write_text("[]")
  with pytest.raises(HostAdmissionDenied):
    with HostResourceGuard(root, now=evening):
      pass
  owner.unlink()
  target = root / "other.json"
  target.write_text('{"version": 1, "status": "RELEASED"}')
  owner.symlink_to(target)
  with pytest.raises(HostAdmissionDenied, match="LINK_FORBIDDEN"):
    with HostResourceGuard(root, now=evening):
      pass


@pytest.mark.parametrize("cooperative", [True, False])
def test_running_process_stops_when_window_closes(root, cooperative):
  body = """import time
started = time.monotonic()
now = lambda: (
  datetime(2026, 9, 9, 20, tzinfo=SHANGHAI)
  if time.monotonic() - started < 0.2
  else datetime(2026, 9, 10, 9, 15, tzinfo=SHANGHAI)
)
try:
  with HostResourceGuard(root, now=now):
    while True:
      time.sleep(SLEEP_SECONDS)
except HostAdmissionDenied:
  print("stopped")
""".replace("SLEEP_SECONDS", "0.02" if cooperative else "30")
  result = invoke_child(root, body)
  assert result.returncode == (0 if cooperative else 75), result.stderr
  record = json.loads((root / "owner.json").read_text())
  assert record["status"] == ("RELEASED" if cooperative else "STOP_REQUESTED")
  assert record["reason"] == "TRADING_OR_POST_CLOSE_CRITICAL_WINDOW"
