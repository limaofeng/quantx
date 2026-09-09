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
  "fault", ["host", "pid", "birth", "exe", "config", "json", "future", "stale", "link"]
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
