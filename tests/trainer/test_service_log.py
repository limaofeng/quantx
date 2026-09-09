import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from quantx_infrastructure.training_bundle_store import BundleTransferError
from quantx_trainer import main, service_log
from quantx_trainer.service_status import ServiceReporter


def test_phase_changes_log_once_and_heartbeats_do_not_duplicate(tmp_path):
  root = tmp_path / "service"
  root.mkdir()
  config = tmp_path / "config.toml"
  config.write_text("database_url = 'private-connection'")
  reporter = ServiceReporter(root, config)
  reporter.write()
  reporter.write()
  reporter.write("WORKER_LOOP")
  reporter.event("SERVICE_FAILED")
  reporter.write("EXITING")
  events = service_log.read_events(tmp_path)
  assert [event["event"] for event in events] == [
    "PREFLIGHT",
    "WORKER_LOOP",
    "SERVICE_FAILED",
    "EXITING",
  ]
  assert len({event["instance_id"] for event in events}) == 1
  assert "private-connection" not in (root / "events.jsonl").read_text()
  assert service_log.read_events(tmp_path, lines=2) == events[-2:]


def test_log_rotation_bounds_storage_and_retains_latest_events(tmp_path, monkeypatch):
  monkeypatch.setattr(service_log, "MAX_BYTES", 400)
  root = tmp_path / "service"
  for index in range(30):
    service_log.append_event(root, f"{index:032x}", "PREFLIGHT")
  paths = list(root.iterdir())
  assert len(paths) == 4
  assert all(path.stat().st_size <= 400 for path in paths)
  records = service_log.read_events(tmp_path, lines=2)
  assert [record["instance_id"] for record in records] == [
    f"{index:032x}" for index in (28, 29)
  ]


@pytest.mark.parametrize(
  "payload",
  [
    {"event": "password=private"},
    {
      "timestamp": 1,
      "instance_id": "a" * 32,
      "event": "PREFLIGHT",
      "secret": "private",
    },
    {"timestamp": float("nan"), "instance_id": "a" * 32, "event": "PREFLIGHT"},
    [],
    None,
  ],
)
def test_reader_rejects_arbitrary_or_corrupted_records(tmp_path, payload):
  root = tmp_path / "service"
  root.mkdir()
  (root / "events.jsonl").write_text(json.dumps(payload))
  with pytest.raises(ValueError, match="SERVICE_LOG_CORRUPT"):
    service_log.read_events(tmp_path)


def test_log_links_are_rejected_before_read_or_write(tmp_path):
  root = tmp_path / "service"
  root.mkdir()
  original = tmp_path / "private"
  original.write_text("private")
  (root / "events.jsonl").symlink_to(original)
  with pytest.raises(BundleTransferError):
    service_log.read_events(tmp_path)
  with pytest.raises(BundleTransferError):
    service_log.append_event(root, "a" * 32, "PREFLIGHT")
  assert original.read_text() == "private"


def test_logs_cli_is_offline_and_sanitizes_corruption(tmp_path, monkeypatch, capsys):
  from quantx_trainer import preflight

  config = SimpleNamespace(state_root=tmp_path, validate_runtime=Mock())
  monkeypatch.setattr(main.TrainerConfig, "load", lambda path: config)
  remote = Mock(side_effect=AssertionError("must stay offline"))
  monkeypatch.setattr(preflight, "preflight", remote)
  service_log.append_event(tmp_path / "service", "a" * 32, "PREFLIGHT")
  assert main.main(["logs", "--config", "config.toml", "--lines", "1"]) == 0
  assert json.loads(capsys.readouterr().out)["event"] == "PREFLIGHT"
  (tmp_path / "service/events.jsonl").write_text("private corrupt line")
  assert main.main(["logs", "--config", "config.toml"]) == 3
  output = capsys.readouterr()
  assert "private" not in output.out + output.err
  remote.assert_not_called()
