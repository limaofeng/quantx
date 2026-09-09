import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_trainer import launcher, preflight, service
from quantx_trainer.admission import admission_status
from quantx_trainer.service_log import read_events
from quantx_trainer.service_stop import request_stop, stop_requested


def test_stop_request_only_matches_its_exact_instance(tmp_path):
  first, second = "a" * 32, "b" * 32
  request_stop(tmp_path, first)
  request_stop(tmp_path, first)
  assert stop_requested(tmp_path, first)
  assert not stop_requested(tmp_path, second)
  (tmp_path / f"stop-{first}.json").write_text(
    json.dumps({"instance_id": first, "stop": 1})
  )
  with pytest.raises(ValueError):
    stop_requested(tmp_path, first)


@pytest.mark.parametrize("state", ["ALIVE", "UNKNOWN", "STALE"])
def test_down_closes_admission_and_preserves_unconfirmed_stop(
  tmp_path, monkeypatch, state
):
  root = tmp_path / "service"
  root.mkdir()
  config_path = tmp_path / "config.toml"
  config_path.write_text("fixture")
  config = SimpleNamespace(state_root=tmp_path)
  instance = "a" * 32
  monkeypatch.setattr(
    launcher,
    "service_status",
    lambda *args: {
      "service": state,
      "instance_id": instance,
      "execution_state": "NOT_INSPECTED",
    },
  )
  result = launcher.stop_service(config, config_path, stop_seconds=0)
  assert result["service"] == "STOP_PENDING"
  assert admission_status(tmp_path / "control")["admission"] == "DRAINING"
  assert stop_requested(root, instance) == (state in {"ALIVE", "STALE"})


def test_service_joins_worker_cleanup_after_stop_request(tmp_path, monkeypatch):
  config_path = tmp_path / "trainer.toml"
  config_path.write_text("fixture")
  config = SimpleNamespace(
    code_root=tmp_path,
    state_root=tmp_path / "state",
    child_environment=lambda ambient: {"ENV": "development"},
  )
  monkeypatch.setattr(service.sys, "platform", "darwin")
  monkeypatch.setattr(preflight, "preflight", AsyncMock())
  cleaned = []

  async def run(config, path, report):
    report("WORKER_LOOP")
    reporter = report.__self__
    request_stop(config.state_root / "service", reporter.identity["instance_id"])
    try:
      await asyncio.Event().wait()
    finally:
      await asyncio.sleep(0.01)
      cleaned.append(True)

  monkeypatch.setattr(service, "_run_worker", run)
  service.serve(config, config_path)
  assert cleaned == [True]
  events = [row["event"] for row in read_events(config.state_root)]
  assert events[-2:] == ["STOPPING", "EXITING"]
  assert "SERVICE_FAILED" not in events
