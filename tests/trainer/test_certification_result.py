import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts.research_preparation import CertificationInputReference
from quantx_contracts.training_bundle import BundleFile, TrainingBundle
from quantx_infrastructure.training_process_evidence import begin_execution, record_exit
from quantx_trainer import certification_result as result_module
from quantx_trainer.preparation_flow import attempt_directory


def attempt(tmp_path, *, kind="CERTIFY_FROZEN"):
  config = SimpleNamespace(state_root=tmp_path)
  reference = CertificationInputReference(
    bundle=TrainingBundle(
      schema_version=1,
      kind="CERTIFICATION_INPUT",
      source_id="dataset",
      files=[
        BundleFile(path=name, size=1, sha256="a" * 64)
        for name in ["manifest.json", "config.json", "source/manifest.json"]
      ],
    ),
    manifest_sha256="a" * 64,
  )
  job = SimpleNamespace(
    job_id="job",
    flow_run_id="owner",
    request={
      "dataset_version": "dataset",
      "certification_input": reference.model_dump(mode="json"),
    },
  )
  directory = attempt_directory(config, job)
  directory.mkdir(parents=True)
  request = directory / "request.json"
  request.write_text(json.dumps({"kind": kind, **job.request}))
  identity = dict(run_id=job.job_id, owner=job.flow_run_id, request=request)
  begin_execution(directory / "process.json", **identity)
  record_exit(directory / "process.json", returncode=0, **identity)
  result = {
    "ready": True,
    "dataset_version": "dataset",
    "manifest_sha256": "b" * 64,
    "input_manifest_sha256": "a" * 64,
  }
  (directory / "result.json").write_text(json.dumps(result))
  return config, job, directory


def values():
  return {
    "status": "CERTIFIED",
    "dataset_version": "dataset",
    "quality_summary": {
      "coverage": {"source": {"source_provenance": {"input_manifest_sha256": "a" * 64}}}
    },
  }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["registration", "publication", "terminal"])
async def test_result_retry_finishes_publication_before_success(
  tmp_path, monkeypatch, failure
):
  config, job, directory = attempt(tmp_path)
  monkeypatch.setattr(result_module, "certification_values", lambda **kwargs: values())
  calls = []
  broken = True

  async def certify(payload):
    calls.append("registration")
    if broken and failure == "registration":
      raise ConnectionError("database unavailable")

  async def publish(config, datasets, *, dataset_version, check):
    await check()
    calls.append("publication")
    if broken and failure == "publication":
      raise ConnectionError("store unavailable")

  async def progress(job_id, **kwargs):
    assert job_id == "job" and kwargs["expected_flow_run_id"] == "owner"
    if "status" in kwargs:
      assert kwargs["status"] == "SUCCEEDED"
      assert calls[-1] == "publication"
      if broken and failure == "terminal":
        raise ConnectionError("acknowledgement lost")
      calls.append("success")

  monkeypatch.setattr(result_module, "publish_dataset", publish)
  preparation = SimpleNamespace(progress=AsyncMock(side_effect=progress))
  datasets = SimpleNamespace(certify_dataset=AsyncMock(side_effect=certify))
  before = {path.name: path.read_bytes() for path in directory.iterdir()}
  with pytest.raises(ConnectionError):
    await result_module.finalize_certification(config, preparation, datasets, job)
  assert "success" not in calls
  broken = False
  result = await result_module.finalize_certification(
    config, preparation, datasets, job
  )
  assert result["status"] == "SUCCEEDED" and calls[-3:] == [
    "registration",
    "publication",
    "success",
  ]
  assert before == {path.name: path.read_bytes() for path in directory.iterdir()}


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["request", "result", "provenance", "exit", "kind"])
async def test_unbound_or_unconfirmed_result_never_registers(
  tmp_path, monkeypatch, fault
):
  config, job, directory = attempt(tmp_path, kind="GPU" if fault == "kind" else "CERTIFY_FROZEN")
  evidence = values()
  if fault == "request":
    (directory / "request.json").write_text("changed request")
  if fault == "result":
    path = directory / "result.json"
    data = json.loads(path.read_text())
    data["input_manifest_sha256"] = "c" * 64
    path.write_text(json.dumps(data))
  if fault == "provenance":
    evidence["quality_summary"] = {}
  if fault == "exit":
    path = directory / "process.json"
    data = json.loads(path.read_text())
    data["returncode"] = 75
    path.write_text(json.dumps(data))
  monkeypatch.setattr(result_module, "certification_values", lambda **kwargs: evidence)
  publication = AsyncMock()
  monkeypatch.setattr(result_module, "publish_dataset", publication)
  preparation = SimpleNamespace(progress=AsyncMock())
  datasets = SimpleNamespace(certify_dataset=AsyncMock())
  with pytest.raises(ValueError, match="CERTIFICATION_"):
    await result_module.finalize_certification(config, preparation, datasets, job)
  datasets.certify_dataset.assert_not_awaited()
  publication.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "owner", "store"])
async def test_dataset_publication_checks_owner_before_upload_and_records_after_readback(
  tmp_path, monkeypatch, failure
):
  from contextlib import contextmanager

  from quantx_infrastructure.training_bundle_store import verify_bundle
  from quantx_infrastructure.training_dataset_store import certification_values
  from quantx_trainer import dataset_transfer

  from tests.research.test_next_day_selection_workflow import _dataset

  root = tmp_path / "datasets"
  root.mkdir()
  directory = _dataset(root)
  directory = directory.rename(root / "workflow-test")
  manifest = json.loads((directory / "manifest.json").read_text())
  certified = certification_values(
    dataset_version="workflow-test",
    manifest_sha256=manifest["manifest_sha256"],
    root=root,
  )
  config = SimpleNamespace(
    state_root=tmp_path, transfer_config=tmp_path / "transfer.json"
  )
  calls = []
  repository = SimpleNamespace(
    get_dataset=AsyncMock(return_value=certified), record_dataset_bundle=AsyncMock()
  )
  monkeypatch.setattr(dataset_transfer.TransferConfig, "load", lambda *a, **k: object())

  async def check():
    calls.append("owner")
    if failure == "owner":
      raise ValueError("owner changed")

  def publish(bundle, actual):
    assert calls[0] == "owner"
    verify_bundle(actual, bundle)
    repository.record_dataset_bundle.assert_not_awaited()
    calls.append("upload")
    if failure == "store":
      raise ConnectionError("readback failed")
    return bundle.bundle_id

  @contextmanager
  def store(*args, **kwargs):
    yield SimpleNamespace(publish=publish)

  monkeypatch.setattr(dataset_transfer, "open_store", store)
  if failure:
    with pytest.raises((ValueError, ConnectionError)):
      await dataset_transfer.publish_dataset(
        config, repository, dataset_version="workflow-test", check=check
      )
    repository.record_dataset_bundle.assert_not_awaited()
  else:
    result = await dataset_transfer.publish_dataset(
      config, repository, dataset_version="workflow-test", check=check
    )
    assert result["status"] == "PUBLISHED"
    repository.record_dataset_bundle.assert_awaited_once()
