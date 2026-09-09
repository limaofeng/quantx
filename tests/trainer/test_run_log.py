import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from quantx_infrastructure.training_bundle_store import BundleTransferError
from quantx_trainer import main, run_log


def test_run_logs_are_bounded_and_redacted(tmp_path):
  root = tmp_path / "control/run-1"
  root.mkdir(parents=True)
  (root / "stdout.log").write_text("old\nready\npassword=private value\n")
  (root / "stderr.log").write_text(
    'Authorization: Bearer private-token\nFile "C:\\private\\code.py", line 1\n'
  )
  rows = run_log.read_run_logs(tmp_path, "run-1", lines=2)
  assert len(rows) == 4
  assert {row["stream"] for row in rows} == {"stdout", "stderr"}
  assert all(row["run_id"] == "run-1" for row in rows)
  rendered = json.dumps(rows)
  assert "private" not in rendered
  assert "old" not in rendered
  assert "REDACTED" in rendered and "PATH" in rendered


def test_truncated_secret_line_is_never_exposed(tmp_path):
  root = tmp_path / "control/run-1"
  root.mkdir(parents=True)
  (root / "stdout.log").write_text(
    "token=" + "secret" * run_log.MAX_BYTES + "\nfinished\n"
  )
  assert run_log.read_run_logs(tmp_path, "run-1") == [
    {"run_id": "run-1", "stream": "stdout", "message": "finished"}
  ]


@pytest.mark.parametrize("run_id", ["../other", "/absolute", "..", "bad\\path"])
def test_invalid_run_path_is_rejected(tmp_path, run_id):
  with pytest.raises(ValueError, match="RUN_LOG_ID_INVALID"):
    run_log.read_run_logs(tmp_path, run_id)


@pytest.mark.parametrize("link", ["symbolic", "hard"])
def test_linked_log_is_rejected(tmp_path, link):
  root = tmp_path / "control/run-1"
  root.mkdir(parents=True)
  target = tmp_path / "private"
  target.write_text("private text")
  path = root / "stdout.log"
  if link == "symbolic":
    path.symlink_to(target)
  else:
    path.hardlink_to(target)
  with pytest.raises((ValueError, BundleTransferError)):
    run_log.read_run_logs(tmp_path, "run-1")


def test_run_logs_cli_stays_offline_and_reports_safe_error(
  tmp_path, monkeypatch, capsys
):
  config = SimpleNamespace(state_root=tmp_path, validate_runtime=Mock())
  monkeypatch.setattr(main.TrainerConfig, "load", lambda path: config)
  root = tmp_path / "control/run-1"
  root.mkdir(parents=True)
  (root / "stdout.log").write_text("done\n")
  assert main.main(["logs", "--config", "unused", "--run-id", "run-1"]) == 0
  assert json.loads(capsys.readouterr().out)["message"] == "done"
  assert main.main(["logs", "--config", "unused", "--run-id", "../private"]) == 3
  output = capsys.readouterr()
  assert output.out == "" and "private" not in output.err


def test_preparation_logs_select_exact_attempt(tmp_path, monkeypatch, capsys):
  for owner in ("first", "second"):
    root = tmp_path / "preparation" / "job" / hashlib.sha256(owner.encode()).hexdigest()
    root.mkdir(parents=True)
    (root / "stdout.log").write_text(owner + "\n")
  rows = run_log.read_preparation_logs(tmp_path, "job", "first")
  assert rows == [
    {"job_id": "job", "owner": "first", "stream": "stdout", "message": "first"}
  ]
  config = SimpleNamespace(state_root=tmp_path, validate_runtime=Mock())
  monkeypatch.setattr(main.TrainerConfig, "load", lambda path: config)
  assert (
    main.main(["logs", "--config", "unused", "--job-id", "job", "--owner", "second"])
    == 0
  )
  assert json.loads(capsys.readouterr().out)["message"] == "second"
  with pytest.raises(ValueError, match="NOT_FOUND"):
    run_log.read_preparation_logs(tmp_path, "job", "missing")


@pytest.mark.parametrize(
  "args",
  [
    ["--job-id", "job"],
    ["--owner", "owner"],
    ["--job-id", "job", "--owner", "owner", "--run-id", "run"],
  ],
)
def test_preparation_query_requires_unambiguous_identity(args):
  with pytest.raises(SystemExit) as stopped:
    main.main(["logs", "--config", "unused", *args])
  assert stopped.value.code == 2
