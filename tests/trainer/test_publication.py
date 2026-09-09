import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_trainer import publication


@pytest.fixture
def result(tmp_path, monkeypatch):
  state = tmp_path.resolve()
  directory = state / "runs" / "run-1"
  directory.mkdir(parents=True)
  control = state / "control" / "run-1"
  control.mkdir(parents=True)
  files = [
    "metrics.json",
    "data-quality.json",
    "model-runtime.json",
    "preprocessing.json",
    "calibrators.json",
    "factor-schema.json",
    "development-lock.json",
    "model.txt",
    "logistic.json",
    "lightgbm.txt",
  ]
  entries = []
  for name in files:
    payload = b"{}"
    (directory / name).write_bytes(payload)
    entries.append(
      dict(path=name, bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    )
  spec = dict(
    spec_hash="a" * 64, coordinate_hash="b" * 64, environment_requirement_hash="c" * 64
  )
  manifest = dict(
    schema_version=2,
    study_id="next-day-selection",
    version="v1",
    run_id="run-1",
    run_kind="DEVELOPMENT",
    status="SUCCEEDED",
    artifacts=entries,
    gates={},
    **spec,
  )
  (directory / "manifest.json").write_text(json.dumps(manifest))
  row = SimpleNamespace(
    run_id="run-1",
    spec_id="spec",
    run_kind="DEVELOPMENT",
    prefect_flow_run_id="owner",
    status="RUNNING",
    cancel_requested_at=None,
  )
  repository = SimpleNamespace(
    get_run=AsyncMock(return_value=row),
    get_spec=AsyncMock(return_value=SimpleNamespace(**spec)),
    record_artifact_bundle=AsyncMock(),
    complete_run=AsyncMock(),
  )
  uploads = []
  failure = []

  @contextmanager
  def store(config):
    def publish(bundle, source):
      uploads.append(bundle.bundle_id)
      if failure:
        raise failure.pop()
      return bundle.bundle_id

    yield SimpleNamespace(publish=publish)

  monkeypatch.setattr(publication, "open_store", store)
  monkeypatch.setattr(
    publication.TransferConfig, "load", lambda *args, **kwargs: object()
  )
  monkeypatch.setattr(
    publication, "inspect_execution", lambda *args, **kwargs: "EXITED"
  )
  config = SimpleNamespace(state_root=state, transfer_config=state / "transfer.toml")
  return SimpleNamespace(
    directory=directory,
    control=control,
    config=config,
    repository=repository,
    row=row,
    uploads=uploads,
    failure=failure,
  )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["upload", "registration", "completion"])
async def test_publication_retry_keeps_frozen_evidence_and_does_not_retrain(
  result, failure_at
):
  if failure_at == "upload":
    result.failure.append(ConnectionError("private endpoint"))
  elif failure_at == "registration":
    result.repository.record_artifact_bundle.side_effect = [
      ConnectionError("private endpoint"),
      None,
    ]
  else:
    result.repository.complete_run.side_effect = [
      ConnectionError("private endpoint"),
      None,
    ]
  with pytest.raises(
    publication.PublicationError, match="^PUBLICATION_RETRY_REQUIRED$"
  ):
    await publication.publish_result(
      result.config, result.repository, run_id="run-1", owner="owner"
    )
  frozen = (result.control / "publication.json").read_bytes()
  assert (result.directory / "model.txt").is_file()
  completed = await publication.publish_result(
    result.config, result.repository, run_id="run-1", owner="owner"
  )
  assert completed["status"] == "SUCCEEDED"
  assert (result.control / "publication.json").read_bytes() == frozen
  assert len(result.uploads) == 2 and len(set(result.uploads)) == 1
  assert (
    result.repository.complete_run.call_args.kwargs["expected_flow_run_id"] == "owner"
  )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["LIVE", "UNKNOWN"])
async def test_recovery_never_reads_or_publishes_a_live_or_unverifiable_execution(
  result, monkeypatch, state
):
  monkeypatch.setattr(publication, "inspect_execution", lambda *args, **kwargs: state)
  with pytest.raises(publication.PublicationError, match="EXECUTION_NOT_STOPPED"):
    await publication.publish_result(
      result.config, result.repository, run_id="run-1", owner="owner"
    )
  assert result.uploads == []
  result.repository.complete_run.assert_not_called()


@pytest.mark.asyncio
async def test_corrupt_local_artifact_cannot_be_registered_as_success(result):
  (result.directory / "model.txt").write_bytes(b"corrupt")
  with pytest.raises(publication.PublicationError):
    await publication.publish_result(
      result.config, result.repository, run_id="run-1", owner="owner"
    )
  assert result.uploads == []
  result.repository.record_artifact_bundle.assert_not_called()
  result.repository.complete_run.assert_not_called()


def test_concurrent_publication_is_excluded_by_os_lock(result):
  with publication.publication_lock(result.control):
    with pytest.raises(OSError):
      with publication.publication_lock(result.control):
        pytest.fail("second publisher acquired the lock")
  with publication.publication_lock(result.control):
    pass


def test_publication_command_runs_preflight_before_any_registration(
  monkeypatch, capsys
):
  from unittest.mock import Mock

  from quantx_trainer import main, preflight

  config = SimpleNamespace(validate_runtime=Mock())
  monkeypatch.setattr(main.TrainerConfig, "load", lambda path: config)
  check = AsyncMock(side_effect=preflight.TrainerPreflightError("NOT_READY"))
  publish = AsyncMock(return_value={"status": "SUCCEEDED", "run_id": "run-1"})
  monkeypatch.setattr(preflight, "preflight", check)
  monkeypatch.setattr(publication, "run_publication", publish)
  command = [
    "publish-result",
    "--config",
    "private.toml",
    "--run-id",
    "run-1",
    "--owner",
    "owner",
  ]
  assert main.main(command) == 2
  publish.assert_not_called()
  check.side_effect = None
  assert main.main(command) == 0
  publish.assert_awaited_once_with(config, run_id="run-1", owner="owner")
  assert json.loads(capsys.readouterr().out)["status"] == "SUCCEEDED"
