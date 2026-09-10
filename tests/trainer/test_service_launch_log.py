import json
import os

import pytest
from quantx_infrastructure.training_bundle_store import BundleTransferError
from quantx_trainer.run_log import read_service_launch_logs


def test_missing_launch_has_no_bootstrap_output(tmp_path):
  assert read_service_launch_logs(tmp_path) == []


@pytest.mark.parametrize(
  "pointer",
  [None, [], {"attempt": "../outside"}, {"attempt": 1}, {"attempt": "a" * 32, "extra": 1}],
)
def test_invalid_pointer_cannot_select_another_directory(tmp_path, pointer):
  root = tmp_path / "service-launches"
  root.mkdir()
  (root / "current.json").write_text(json.dumps(pointer))
  with pytest.raises(ValueError, match="SERVICE_LAUNCH_LOG_POINTER_INVALID"):
    read_service_launch_logs(tmp_path)


def test_oversized_or_hardlinked_pointer_is_rejected(tmp_path):
  root = tmp_path / "service-launches"
  root.mkdir()
  pointer = root / "current.json"
  pointer.write_text(" " * 1025)
  with pytest.raises(ValueError, match="POINTER_INVALID"):
    read_service_launch_logs(tmp_path)
  pointer.write_text(json.dumps({"attempt": "a" * 32}))
  os.link(pointer, root / "alias")
  with pytest.raises(BundleTransferError, match="BUNDLE_HARDLINK_FORBIDDEN"):
    read_service_launch_logs(tmp_path)


def test_latest_launch_tail_is_bounded_and_redacted(tmp_path):
  root = tmp_path / "service-launches"
  owner = "a" * 32
  attempt = root / owner
  attempt.mkdir(parents=True)
  (root / "current.json").write_text(json.dumps({"attempt": owner}))
  (attempt / "stderr.log").write_text("old\npassword=synthetic-secret\n")
  assert read_service_launch_logs(tmp_path, lines=1) == [
    {"launch_id": owner, "stream": "stderr", "message": "password=[REDACTED]"}
  ]


def test_service_logs_cli_includes_redacted_launch_output(tmp_path, monkeypatch, capsys):
  from types import SimpleNamespace
  from unittest.mock import Mock

  from quantx_trainer import main, preflight

  config = SimpleNamespace(state_root=tmp_path, validate_runtime=Mock())
  monkeypatch.setattr(main.TrainerConfig, "load", lambda path: config)
  remote = Mock(side_effect=AssertionError("must stay offline"))
  monkeypatch.setattr(preflight, "preflight", remote)
  root = tmp_path / "service-launches"
  owner = "b" * 32
  attempt = root / owner
  attempt.mkdir(parents=True)
  (root / "current.json").write_text(json.dumps({"attempt": owner}))
  (attempt / "stderr.log").write_text("token=synthetic-secret\n")
  assert main.main(["logs", "--config", "config.toml"]) == 0
  assert json.loads(capsys.readouterr().out)["message"] == "token=[REDACTED]"
  remote.assert_not_called()


def test_service_failure_reports_location_without_exception_contents(monkeypatch, capsys):
  from types import SimpleNamespace
  from unittest.mock import Mock

  from quantx_trainer import main, service

  config = SimpleNamespace(validate_runtime=Mock())
  monkeypatch.setattr(main.TrainerConfig, "load", lambda path: config)

  def fail(*args):
    raise RuntimeError("synthetic-private-message")

  monkeypatch.setattr(service, "serve", fail)
  assert main.main(["serve", "--config", "config.toml"]) == 3
  output = capsys.readouterr().err
  assert "synthetic-private-message" not in output
  diagnostic = json.loads(output.splitlines()[-1])
  assert diagnostic["error_type"] == "RuntimeError"
  assert diagnostic["frames"][-1]["file"] == "test_service_launch_log.py"
  assert diagnostic["frames"][-1]["function"] == "fail"
