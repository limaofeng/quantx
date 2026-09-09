import asyncio
import json
import shutil
import sys
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.training_bundle_store import (
  DirectoryBundleReader,
  materialize_bundle,
)
from quantx_research import certification_inputs, preparation_job
from quantx_research.next_day_selection_config import load_next_day_selection_config
from quantx_trainer import dataset_transfer, preparation_flow

from tests.research.test_certification_inputs import exported


@pytest.mark.asyncio
async def test_trainer_fetches_bound_inputs_and_research_entry_uses_only_files(
  tmp_path, monkeypatch
):
  directory, digest, _ = await exported(tmp_path)
  reference = certification_inputs.certification_input_reference(
    directory, dataset_version="frozen-v1", manifest_sha256=digest
  )
  store_root = tmp_path / "store"
  store_root.mkdir()
  shutil.copytree(directory, store_root / reference.bundle.bundle_id)
  job = SimpleNamespace(
    job_id="job",
    flow_run_id="owner",
    request={
      "dataset_version": "frozen-v1",
      "certification_input": reference.model_dump(mode="json"),
    },
  )
  config = SimpleNamespace(
    state_root=tmp_path / "trainer", transfer_config=tmp_path / "transfer.json"
  )
  monkeypatch.setattr(
    dataset_transfer.HostPolicy,
    "load",
    lambda path: SimpleNamespace(minimum_free_disk_mib=0),
  )
  monkeypatch.setattr(dataset_transfer.TransferConfig, "load", lambda *a, **k: object())

  @contextmanager
  def store(*args, cancel):
    yield SimpleNamespace(
      fetch=lambda bundle, cache, minimum_free_bytes: materialize_bundle(
        DirectoryBundleReader(store_root),
        bundle,
        cache,
        reserve_bytes=minimum_free_bytes,
        cancel=cancel,
      )
    )

  monkeypatch.setattr(dataset_transfer, "open_store", store)
  check = AsyncMock()
  files = await dataset_transfer.load_certification_input(
    config, object(), job, check=check
  )
  assert (
    files["directory"]
    == config.state_root / "certification-cache" / reference.bundle.bundle_id
  )
  assert check.await_count >= 1

  def forbidden(*args, **kwargs):
    pytest.fail("frozen certification must not open control-plane data")

  monkeypatch.setattr(preparation_job, "coverage", forbidden)
  monkeypatch.setattr(preparation_job, "InfrastructureResearchDataSource", forbidden)
  monkeypatch.setattr(preparation_job, "TradingDateHelper", forbidden)

  async def certify(config_path, **kwargs):
    configured = load_next_day_selection_config(config_path)
    assert configured.data.historical_st_membership_path.is_file()
    assert kwargs["source"] is kwargs["calendar"]
    assert kwargs["source"].directory == files["directory"] / "source"
    return config.state_root / "datasets/frozen-v1"

  monkeypatch.setattr(
    certification_inputs, "certify_next_day_selection_dataset", certify
  )
  from quantx_research import next_day_selection_dataset

  monkeypatch.setattr(
    next_day_selection_dataset,
    "load_certified_dataset_manifest",
    lambda path: {
      "dataset_version": "frozen-v1",
      "manifest_sha256": "b" * 64,
      "quality": {"sample_count": 10},
    },
  )
  result = await preparation_job.execute(
    {
      "kind": "CERTIFY_FROZEN",
      **job.request,
      "input_directory": str(files["directory"]),
      "output_root": str(config.state_root / "datasets"),
    },
    tmp_path / "attempt",
  )
  assert result["ready"] is True
  assert result["input_manifest_sha256"] == digest


@pytest.mark.asyncio
async def test_certification_subprocess_request_and_exit_evidence(
  tmp_path, monkeypatch
):
  create = asyncio.create_subprocess_exec
  captured = {}

  async def spawn(*args, **kwargs):
    captured.update(environment=kwargs["env"], command=args)
    code = "import pathlib,sys; pathlib.Path(sys.argv[1]).with_name('result.json').write_text('{\"ready\": true}')"
    return await create(sys.executable, "-c", code, args[-1], **kwargs)

  monkeypatch.setattr(preparation_flow.asyncio, "create_subprocess_exec", spawn)
  config = SimpleNamespace(
    state_root=tmp_path, research_environment=lambda ambient: {"PYTHONUTF8": "1"}
  )
  job = SimpleNamespace(
    job_id="cert-job",
    flow_run_id="owner",
    request={
      "dataset_version": "version",
      "certification_input": {"manifest_sha256": "a" * 64},
    },
  )
  result = await preparation_flow.run_certification_job(
    config, job, {"directory": tmp_path / "cache"}, AsyncMock()
  )
  assert result == {"ready": True}
  attempt = preparation_flow.attempt_directory(config, job)
  request = json.loads((attempt / "request.json").read_text())
  assert request["kind"] == "CERTIFY_FROZEN"
  assert request["output_root"] == str(tmp_path / "datasets")
  assert request["certification_input"] == job.request["certification_input"]
  assert captured["environment"] == {"PYTHONUTF8": "1"}
  evidence = json.loads((attempt / "process.json").read_text())
  assert evidence["state"] == "EXITED" and evidence["returncode"] == 0


@pytest.mark.asyncio
async def test_large_frozen_validation_obeys_download_cancellation(tmp_path):
  directory, digest, _ = await exported(tmp_path)
  cancel = threading.Event()
  cancel.set()
  with pytest.raises(ValueError, match="cancelled"):
    certification_inputs.load_certification_inputs(
      directory, dataset_version="frozen-v1", manifest_sha256=digest, cancel=cancel
    )


def test_frozen_hash_checks_cancellation_between_chunks(tmp_path):
  from quantx_research.data.frozen_source import _digest

  path = tmp_path / "large.parquet"
  path.write_bytes(b"x" * (3 * 1024 * 1024))
  checks = []

  def cancelled():
    checks.append(True)
    return len(checks) == 2

  with pytest.raises(ValueError, match="cancelled"):
    _digest(path, cancel=SimpleNamespace(is_set=cancelled))
  assert len(checks) == 2
