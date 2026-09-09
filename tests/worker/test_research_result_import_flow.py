import asyncio
import hashlib
import json
import threading
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml
from quantx_contracts.training_bundle import TrainingBundle
from quantx_infrastructure.training_bundle_store import DirectoryBundleReader
from quantx_worker.prefector.flows import research_result_import_flow as module


@pytest.mark.asyncio
async def test_real_bundle_import_then_offline_retry(tmp_path, monkeypatch):
  raw = json.dumps(
    {
      "schema_version": 2,
      "study_id": "next-day-selection",
      "version": "v1",
      "run_id": "run",
      "run_kind": "DEVELOPMENT",
      "status": "SUCCEEDED",
    }
  ).encode()
  digest = hashlib.sha256(raw).hexdigest()
  bundle = TrainingBundle(
    schema_version=1,
    kind="RESULT",
    source_id="run",
    files=({"path": "manifest.json", "size": len(raw), "sha256": digest},),
  )
  source = tmp_path / "source" / bundle.bundle_id
  source.mkdir(parents=True)
  (source / "manifest.json").write_bytes(raw)
  row = SimpleNamespace(
    run_id="run",
    run_kind="DEVELOPMENT",
    status="SUCCEEDED",
    run_key=hashlib.sha256(b"next-day-selection\0v1\0run").hexdigest(),
    artifact_manifest_sha256=digest,
    artifact_bundle=bundle.model_dump(mode="json"),
  )

  @contextmanager
  def store(*args, **kwargs):
    yield SimpleNamespace(artifacts=DirectoryBundleReader(source.parent))

  monkeypatch.setattr(module, "open_store", store)
  target = await module._import_one(
    row, object(), tmp_path / "runs", tmp_path / "cache", 0
  )
  assert (target / "manifest.json").read_bytes() == raw
  monkeypatch.setattr(
    module,
    "open_store",
    Mock(side_effect=AssertionError("already imported; no SSH needed")),
  )
  assert (
    await module._import_one(row, object(), tmp_path / "runs", tmp_path / "cache", 0)
    == target
  )


@pytest.mark.asyncio
async def test_cancellation_waits_for_copy_thread(tmp_path, monkeypatch):
  started, cancel_seen, release, finished = (threading.Event() for _ in range(4))

  @contextmanager
  def store(*args, **kwargs):
    yield SimpleNamespace(artifacts=None)

  def copy(*args, cancel, **kwargs):
    started.set()
    assert cancel.wait(3)
    cancel_seen.set()
    assert release.wait(3)
    finished.set()
    raise ValueError("cancelled")

  monkeypatch.setattr(module, "open_store", store)
  monkeypatch.setattr(module, "import_training_result", copy)
  task = asyncio.create_task(
    module._import_one(
      SimpleNamespace(run_id="run"), object(), tmp_path / "runs", tmp_path / "cache", 0
    )
  )
  assert await asyncio.to_thread(started.wait, 3)
  task.cancel()
  try:
    assert await asyncio.to_thread(cancel_seen.wait, 3)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
  finally:
    release.set()
  with pytest.raises(asyncio.CancelledError):
    await task
  assert finished.is_set()


@pytest.mark.asyncio
async def test_scheduler_pages_without_open_transaction_and_retries_failures(
  tmp_path, monkeypatch
):
  monkeypatch.setattr(module, "export_transfer_config", lambda: object())
  monkeypatch.setattr(
    module.HostPolicy, "load", lambda root: SimpleNamespace(minimum_free_disk_mib=1)
  )
  monkeypatch.setattr(module, "root", lambda: tmp_path)
  monkeypatch.delenv("QUANTX_RESEARCH_RUNS_ROOT", raising=False)
  monkeypatch.setattr(module, "PAGE_SIZE", 1)
  opened = False

  @asynccontextmanager
  async def session():
    nonlocal opened
    opened = True
    try:
      yield object()
    finally:
      opened = False

  def row(name):
    return SimpleNamespace(**{field: name for field in module.FIELDS})

  repository = SimpleNamespace(
    list_runs=AsyncMock(side_effect=[[row("first")], [row("second")], []])
  )
  monkeypatch.setattr(module, "AsyncSessionLocal", session)
  monkeypatch.setattr(module, "StockSelectionTrainingRepository", lambda db: repository)

  async def copy(row, *args):
    assert not opened
    if row.run_id == "first":
      raise RuntimeError("private connection details")

  monkeypatch.setattr(module, "_import_one", copy)
  result = await module.research_result_import_flow.fn()
  assert result == {"status": "PENDING", "imported": ["second"], "pending": ["first"]}
  assert [call.kwargs["offset"] for call in repository.list_runs.call_args_list] == [
    0,
    1,
    2,
  ]


@pytest.mark.asyncio
async def test_configuration_denial_precedes_database_access(monkeypatch):
  monkeypatch.setattr(
    module,
    "export_transfer_config",
    Mock(side_effect=ValueError("development required")),
  )
  database = Mock(side_effect=AssertionError("must not connect"))
  monkeypatch.setattr(module, "AsyncSessionLocal", database)
  with pytest.raises(ValueError, match="development required"):
    await module.research_result_import_flow.fn()
  database.assert_not_called()


def test_result_import_is_only_scheduled_on_development_pool():
  root = Path(__file__).resolve().parents[2] / "apps/worker"
  development = yaml.safe_load((root / "prefect.development.yaml").read_text())[
    "deployments"
  ]
  selected = next(row for row in development if row["name"] == "research-result-import")
  assert selected["work_pool"]["name"] == "quantx-dev-pool"
  assert selected["concurrency_limit"] == {
    "limit": 1,
    "collision_strategy": "CANCEL_NEW",
  }
  assert not any(
    row["name"] == "research-result-import"
    for row in yaml.safe_load((root / "prefect.yaml").read_text())["deployments"]
  )
