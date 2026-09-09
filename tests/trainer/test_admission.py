from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from quantx_infrastructure.training_bundle_store import (
  BundleTransferError,
  publication_lock,
)
from quantx_trainer import main, training_flow
from quantx_trainer.admission import (
  TrainerAdmissionClosed,
  admission_status,
  claim_admission,
  set_admission,
)


def test_drain_fences_precommit_evidence_and_resume_reopens(tmp_path, monkeypatch):
  monkeypatch.setattr(training_flow, "control_root", lambda: tmp_path)
  prepare = Mock()
  monkeypatch.setattr(training_flow, "_prepare_execution", prepare)
  finish = Mock(return_value=True)
  monkeypatch.setattr(training_flow, "finish_input_preparation", finish)
  assert admission_status(tmp_path)["admission"] == "OPEN"
  assert set_admission(tmp_path, draining=True) == {
    "admission": "DRAINING",
    "execution_state": "NOT_INSPECTED",
  }
  with training_flow._input_attempt(Mock()) as callback:
    with pytest.raises(TrainerAdmissionClosed, match="TRAINER_DRAINING"):
      callback("run", "owner")
  prepare.assert_not_called()
  finish.assert_not_called()
  set_admission(tmp_path, draining=False)
  with training_flow._input_attempt(Mock()) as callback:
    callback("run", "owner")
    # Already admitted work can finish after drain; no cancellation is implied.
    set_admission(tmp_path, draining=True)
  prepare.assert_called_once_with("run", "owner")
  finish.assert_called_once()


def test_concurrent_control_is_bounded_and_fails_closed(tmp_path):
  (tmp_path / "admission").mkdir()
  with publication_lock(tmp_path / "admission"):
    with pytest.raises(TrainerAdmissionClosed, match="UNAVAILABLE"):
      with claim_admission(tmp_path):
        pytest.fail("busy admission lock allowed a claim")
    with pytest.raises(OSError):
      set_admission(tmp_path, draining=True)
  with claim_admission(tmp_path):
    pass


def test_partial_marker_and_link_cannot_open_admission(tmp_path):
  (tmp_path / "admission").mkdir()
  marker = tmp_path / "admission/draining"
  marker.touch()
  with pytest.raises(TrainerAdmissionClosed, match="DRAINING"):
    with claim_admission(tmp_path):
      pytest.fail("partial marker allowed a claim")
  set_admission(tmp_path, draining=True)
  marker.unlink()
  marker.symlink_to(tmp_path / "absent")
  with pytest.raises(TrainerAdmissionClosed, match="DRAINING"):
    with claim_admission(tmp_path):
      pytest.fail("link allowed a claim")
  with pytest.raises(BundleTransferError):
    set_admission(tmp_path, draining=False)


@pytest.mark.parametrize("command", ["drain", "resume", "admission-status"])
def test_offline_cli_never_needs_control_plane(command, tmp_path, monkeypatch, capsys):
  from quantx_trainer import preflight

  config = SimpleNamespace(state_root=tmp_path, validate_runtime=Mock())
  monkeypatch.setattr(main.TrainerConfig, "load", lambda path: config)
  remote = Mock(side_effect=AssertionError("offline command contacted control plane"))
  monkeypatch.setattr(preflight, "preflight", remote)
  assert main.main([command, "--config", str(tmp_path / "trainer.toml")]) == 0
  assert "NOT_INSPECTED" in capsys.readouterr().out
  remote.assert_not_called()


def test_admission_observation_never_creates_state(tmp_path):
  from quantx_trainer.admission import admission_status

  missing = tmp_path / "missing" / "control"
  assert admission_status(missing)["admission"] == "OPEN"
  assert not (tmp_path / "missing").exists()


def test_status_cli_reports_drain_without_claiming_execution_exit(tmp_path, monkeypatch, capsys):
  import json
  from types import SimpleNamespace
  from unittest.mock import Mock

  from quantx_trainer import main, preflight
  from quantx_trainer.admission import set_admission

  config = SimpleNamespace(state_root=tmp_path, validate_runtime=Mock())
  monkeypatch.setattr(main.TrainerConfig, "load", lambda path: config)
  monkeypatch.setattr(preflight, "preflight", Mock(side_effect=AssertionError("offline query")))
  assert main.main(["status", "--config", "unused.toml"]) == 0
  value = json.loads(capsys.readouterr().out)
  assert value == {"service": "OFFLINE", "execution_state": "NOT_INSPECTED", "admission": "OPEN"}
  assert not (tmp_path / "control").exists()
  set_admission(tmp_path / "control", draining=True)
  assert main.main(["status", "--config", "unused.toml"]) == 0
  value = json.loads(capsys.readouterr().out)
  assert value["admission"] == "DRAINING"
  assert value["execution_state"] == "NOT_INSPECTED"
  marker = tmp_path / "control/admission/draining"
  marker.unlink()
  try:
    marker.symlink_to(tmp_path / "private-missing-file")
  except OSError:
    pytest.skip("symlink creation unavailable")
  assert main.main(["status", "--config", "unused.toml"]) == 3
  assert json.loads(capsys.readouterr().out)["admission"] == "UNKNOWN"


def test_admission_observation_rejects_non_directory_state(tmp_path):
  from quantx_trainer.admission import admission_status

  control = tmp_path / "control"
  control.write_text("not a directory")
  with pytest.raises((OSError, ValueError)):
    admission_status(control)
