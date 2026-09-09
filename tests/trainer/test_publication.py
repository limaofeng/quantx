import asyncio
import hashlib
import json
import subprocess
import sys
import threading
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
    heartbeat_execution=AsyncMock(return_value=row),
    get_spec=AsyncMock(return_value=SimpleNamespace(**spec)),
    record_artifact_bundle=AsyncMock(),
    complete_run=AsyncMock(),
  )
  uploads = []
  failure = []

  @contextmanager
  def store(config, **kwargs):
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

    async def commit_with_lost_ack(*args, **kwargs):
      if result.row.status == "RUNNING":
        result.row.status = "SUCCEEDED"
        raise ConnectionError("private endpoint")

    result.repository.complete_run.side_effect = commit_with_lost_ack
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
  if failure_at == "completion":
    assert result.repository.heartbeat_execution.await_count == 2
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


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["disconnect", "cancel", "owner", "task_cancel"])
async def test_local_hashing_stops_before_upload_when_control_is_lost(
  result, monkeypatch, failure
):
  from quantx_infrastructure import training_bundle_store as bundles

  monkeypatch.setattr(publication, "PUBLICATION_HEARTBEAT_SECONDS", 0.01)
  hashing = threading.Event()
  exited = threading.Event()
  original = bundles.verify_file

  def slow_hash(path, entry, *, cancel=None):
    assert cancel is not None
    hashing.set()
    try:
      assert cancel.wait(3)
      return original(path, entry, cancel=cancel)
    finally:
      exited.set()

  async def heartbeat(*args, **kwargs):
    if hashing.is_set():
      if failure == "disconnect":
        raise ConnectionError("private endpoint")
      if failure == "cancel":
        result.row.cancel_requested_at = "requested"
      if failure == "owner":
        result.row.prefect_flow_run_id = "new-owner"
    return result.row

  monkeypatch.setattr(bundles, "verify_file", slow_hash)
  result.repository.heartbeat_execution.side_effect = heartbeat
  task = asyncio.create_task(
    publication.publish_result(
      result.config, result.repository, run_id="run-1", owner="owner"
    )
  )
  async with asyncio.timeout(3):
    if failure == "task_cancel":
      assert await asyncio.to_thread(hashing.wait, 2)
      task.cancel()
      expected = asyncio.CancelledError
    else:
      expected = publication.PublicationError
    with pytest.raises(expected):
      await task
  assert exited.is_set()
  assert result.uploads == []
  assert not (result.control / "publication.json").exists()
  result.repository.record_artifact_bundle.assert_not_called()
  result.repository.complete_run.assert_not_called()
  assert (result.directory / "model.txt").is_file()
  with publication.publication_lock(result.control):
    pass


def test_large_file_hash_checks_cancellation_between_chunks(tmp_path, monkeypatch):
  from quantx_contracts.training_bundle import BundleFile
  from quantx_infrastructure import training_bundle_store as bundles

  path = tmp_path / "model.bin"
  payload = b"x" * (bundles.CHUNK_BYTES * 3)
  path.write_bytes(payload)
  entry = BundleFile(
    path=path.name, size=len(payload), sha256=hashlib.sha256(payload).hexdigest()
  )
  cancel = threading.Event()
  actual = hashlib.sha256()
  updates = []

  def update(block):
    updates.append(len(block))
    actual.update(block)
    cancel.set()

  monkeypatch.setattr(
    bundles.hashlib,
    "sha256",
    lambda: SimpleNamespace(update=update, hexdigest=actual.hexdigest),
  )
  with pytest.raises(bundles.BundleTransferError, match="BUNDLE_CANCELLED"):
    bundles.verify_file(path, entry, cancel=cancel)
  assert updates == [bundles.CHUNK_BYTES]


def test_store_passes_cancellation_into_upload_source_verification(result):
  from pathlib import PurePosixPath

  from quantx_infrastructure.training_bundle_store import (
    BundleTransferError,
    SFTPBundlePublisher,
  )
  from quantx_infrastructure.training_transfer import TrainingStore

  bundle = publication.result_bundle(
    result.directory, run_id="run-1", run_kind="DEVELOPMENT"
  )
  publisher = SFTPBundlePublisher.__new__(SFTPBundlePublisher)
  publisher.root = PurePosixPath("/artifacts")
  cancel = threading.Event()
  cancel.set()
  # No SSH client: verification must abort before any remote operation.
  store = TrainingStore(publisher, publisher, cancel=cancel)
  with pytest.raises(BundleTransferError, match="BUNDLE_CANCELLED"):
    store.publish(bundle, result.directory)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "fault", [None, "hash", "identity", "key", "corrupt", "inventory"]
)
async def test_final_evaluation_fetches_verified_parent_bundle(
  result, monkeypatch, fault
):
  import shutil

  from quantx_infrastructure.training_bundle_store import (
    DirectoryBundleReader,
    materialize_bundle,
  )
  from quantx_trainer import dataset_transfer as inputs

  bundle = publication.result_bundle(
    result.directory, run_id="run-1", run_kind="DEVELOPMENT"
  )
  parent = SimpleNamespace(
    run_id="run-1",
    run_kind="DEVELOPMENT",
    status="SUCCEEDED",
    run_key=hashlib.sha256(b"next-day-selection\0v1\0run-1").hexdigest(),
    artifact_bundle=bundle.model_dump(mode="json"),
    artifact_manifest_sha256=next(
      entry.sha256 for entry in bundle.files if entry.path == "manifest.json"
    ),
  )
  remote = result.config.state_root / "remote"
  remote.mkdir()
  shutil.copytree(result.directory, remote / bundle.bundle_id)
  shutil.rmtree(result.directory)
  if fault == "hash":
    parent.artifact_manifest_sha256 = "f" * 64
  elif fault == "identity":
    parent.run_id = "different-run"
  elif fault == "key":
    parent.run_key = "f" * 64
  elif fault == "corrupt":
    (remote / bundle.bundle_id / "lightgbm.txt").write_bytes(b"corrupted")
  elif fault == "inventory":
    # A content-addressed bundle still must match Research's embedded inventory.
    raw = parent.artifact_bundle
    raw["files"] = [
      item for item in raw["files"] if item["path"] != "development-lock.json"
    ]
    from quantx_contracts.training_bundle import TrainingBundle

    altered = TrainingBundle.model_validate(raw)
    (remote / bundle.bundle_id / "development-lock.json").unlink()
    (remote / bundle.bundle_id).rename(remote / altered.bundle_id)

  @contextmanager
  def store(config, *, cancel):
    def fetch(bundle, cache, *, minimum_free_bytes):
      return materialize_bundle(
        DirectoryBundleReader(remote),
        bundle,
        cache,
        reserve_bytes=minimum_free_bytes,
        cancel=cancel,
      )

    yield SimpleNamespace(fetch=fetch)

  monkeypatch.setattr(inputs, "open_store", store)
  monkeypatch.setattr(
    inputs.HostPolicy, "load", lambda *args: SimpleNamespace(minimum_free_disk_mib=1)
  )
  if fault:
    with pytest.raises((ValueError, RuntimeError)):
      await inputs.load_parent_result(
        result.config, result.repository, parent, run_id="child", owner="owner"
      )
  else:
    directory = await inputs.load_parent_result(
      result.config, result.repository, parent, run_id="child", owner="owner"
    )
    assert directory == result.config.state_root / "parent-cache" / bundle.bundle_id
    assert (directory / "lightgbm.txt").read_bytes() == b"{}"
    assert not result.directory.exists()
  result.repository.record_artifact_bundle.assert_not_called()
  result.repository.complete_run.assert_not_called()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
async def test_normal_publication_requires_its_own_completed_child(
  result, monkeypatch, ready
):
  monkeypatch.setattr(
    publication, "local_success_recorded", lambda *args, **kwargs: ready
  )
  if ready:
    outcome = await publication.publish_generated_result(
      result.config, result.repository, run_id="run-1", owner="owner"
    )
    assert outcome["status"] == "SUCCEEDED"
  else:
    with pytest.raises(publication.PublicationError, match="EXECUTION_NOT_STOPPED"):
      await publication.publish_generated_result(
        result.config, result.repository, run_id="run-1", owner="owner"
      )
    result.repository.complete_run.assert_not_called()


@pytest.mark.asyncio
async def test_cancellation_waits_for_network_writer_before_releasing_lock(
  result, monkeypatch
):
  started, exited = threading.Event(), threading.Event()

  @contextmanager
  def store(config, *, cancel):
    def publish(bundle, directory):
      started.set()
      assert cancel.wait(5)
      raise OSError("transfer aborted")

    try:
      yield SimpleNamespace(publish=publish)
    finally:
      exited.set()

  monkeypatch.setattr(publication, "open_store", store)
  task = asyncio.create_task(
    publication.publish_result(
      result.config, result.repository, run_id="run-1", owner="owner"
    )
  )
  async with asyncio.timeout(3):
    while not started.is_set():
      await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
      await task
  assert exited.is_set()
  result.repository.record_artifact_bundle.assert_not_called()
  with publication.publication_lock(result.control):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [False, True])
async def test_trainer_normal_flow_publishes_before_success_and_resumes_failed_transfer(
  result, monkeypatch, interrupted
):
  from quantx_trainer import training_flow as flow

  process = subprocess.Popen([sys.executable, "-c", "pass"])
  process.wait(timeout=5)
  monkeypatch.setattr(flow, "current_config", lambda: result.config)
  monkeypatch.setattr(flow, "load_dataset", AsyncMock(return_value={}))
  monkeypatch.setattr(flow, "build_training_request", lambda *args, **kwargs: {})
  monkeypatch.setattr(flow, "_spawn_process", lambda *args, **kwargs: process)
  result.repository.fail_run = AsyncMock()
  if interrupted:
    result.failure.append(ConnectionError("disconnected"))
  outcome = await flow._run_claimed_job(
    result.repository, result.row, object(), object()
  )
  assert outcome["status"] == ("RUNNING" if interrupted else "SUCCEEDED")
  assert len(result.uploads) == 1
  result.repository.fail_run.assert_not_called()
  if interrupted:
    result.repository.complete_run.assert_not_called()
    monkeypatch.setattr(flow, "inspect_execution", lambda *args, **kwargs: "EXITED")
    result.repository.list_runs = AsyncMock(return_value=[result.row])
    assert await flow.recover_lost_training_runs(result.repository) == ["run-1"]
    result.repository.fail_run.assert_not_called()
    assert len(result.uploads) == 2
  result.repository.record_artifact_bundle.assert_awaited_once()
  result.repository.complete_run.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["disconnect", "cancel", "owner"])
async def test_upload_heartbeat_stops_writer_when_control_is_lost(
  result, monkeypatch, failure
):
  monkeypatch.setattr(publication, "PUBLICATION_HEARTBEAT_SECONDS", 0.01)
  exited = threading.Event()
  calls = 0

  async def heartbeat(*args, **kwargs):
    nonlocal calls
    calls += 1
    if calls == 3:
      if failure == "disconnect":
        raise ConnectionError("private control-plane endpoint")
      if failure == "cancel":
        result.row.cancel_requested_at = "requested"
      else:
        result.row.prefect_flow_run_id = "new-owner"
    return result.row

  @contextmanager
  def store(config, *, cancel):
    def publish(bundle, directory):
      assert cancel.wait(5)
      raise OSError("closed")

    try:
      yield SimpleNamespace(publish=publish)
    finally:
      exited.set()

  result.repository.heartbeat_execution.side_effect = heartbeat
  monkeypatch.setattr(publication, "open_store", store)
  async with asyncio.timeout(3):
    with pytest.raises(publication.PublicationError) as caught:
      await publication.publish_result(
        result.config, result.repository, run_id="run-1", owner="owner"
      )
  assert "private" not in str(caught.value)
  assert calls >= 3
  assert exited.is_set()
  result.repository.record_artifact_bundle.assert_not_called()
  result.repository.complete_run.assert_not_called()
  assert (result.control / "publication.json").is_file()
  with publication.publication_lock(result.control):
    pass
