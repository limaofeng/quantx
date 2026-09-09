from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_research.certification_inputs import certification_input_reference
from quantx_worker.prefector.flows import certification_transfer as transfer
from quantx_worker.prefector.flows import research_preparation_flow as flow

from tests.research.test_certification_inputs import exported


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_worker_publishes_verified_inputs_before_handoff(tmp_path, monkeypatch, failed):
  directory, digest, _ = await exported(tmp_path)
  attempt = tmp_path / "attempt"
  attempt.mkdir()
  directory = directory.rename(attempt / "certification-inputs")
  reference = certification_input_reference(directory, dataset_version="frozen-v1", manifest_sha256=digest)
  job = SimpleNamespace(kind="CERTIFY", job_id="job", flow_run_id="owner", request={"dataset_version": "frozen-v1"})
  result = {"ready": True, "dataset_version": "frozen-v1", "certification_input": reference.model_dump(mode="json")}
  calls = []

  def publish(bundle, root):
    assert bundle.bundle_id == reference.bundle.bundle_id
    assert root == directory
    calls.append("publish")
    if failed:
      raise ConnectionError("readback failed")
    return bundle.bundle_id

  @contextmanager
  def store(*args, **kwargs):
    yield SimpleNamespace(publish=publish)

  @asynccontextmanager
  async def session():
    yield object()

  async def handoff(job_id, *, expected_flow_run_id, reference):
    assert job_id == "job" and expected_flow_run_id == "owner"
    assert calls == ["publish"]
    calls.append("handoff")

  monkeypatch.setattr(transfer, "open_store", store)
  monkeypatch.setattr(flow, "export_transfer_config", lambda: object())
  monkeypatch.setattr(flow, "AsyncSessionLocal", session)
  monkeypatch.setattr(flow, "ResearchPreparationRepository", lambda db: SimpleNamespace(handoff_certification=handoff))
  monkeypatch.setattr(flow, "run_research", AsyncMock(return_value=result))
  update = AsyncMock()
  monkeypatch.setattr(flow, "update_job", update)
  if failed:
    with pytest.raises(ConnectionError):
      await flow.perform(job, attempt)
    assert calls == ["publish"]
  else:
    await flow.perform(job, attempt)
    assert calls == ["publish", "handoff"]
  assert not any(call.kwargs.get("status") == "SUCCEEDED" for call in update.await_args_list)


def test_export_refuses_missing_config_and_non_development(tmp_path, monkeypatch):
  from quantx_infrastructure.config.settings import settings

  monkeypatch.setattr(settings, "environment", "production")
  with pytest.raises(ValueError, match="development"):
    transfer.export_transfer_config()
  monkeypatch.setattr(settings, "environment", "development")
  monkeypatch.delenv("QUANTX_RESEARCH_TRANSFER_CONFIG", raising=False)
  with pytest.raises(ValueError, match="explicit absolute"):
    transfer.export_transfer_config()


@pytest.mark.asyncio
async def test_cancelled_publication_joins_thread_before_returning(tmp_path, monkeypatch):
  import asyncio
  import threading

  directory, digest, _ = await exported(tmp_path)
  attempt = tmp_path / "attempt"
  attempt.mkdir()
  directory.rename(attempt / "certification-inputs")
  reference = certification_input_reference(attempt / "certification-inputs", dataset_version="frozen-v1", manifest_sha256=digest)
  entered, stopped = threading.Event(), threading.Event()

  @contextmanager
  def store(*args, cancel):
    def publish(*args):
      entered.set()
      assert cancel.wait(5)
      stopped.set()
      raise ConnectionError("cancelled transfer")
    yield SimpleNamespace(publish=publish)

  monkeypatch.setattr(transfer, "open_store", store)
  job = SimpleNamespace(request={"dataset_version": "frozen-v1"})
  result = {"ready": True, "dataset_version": "frozen-v1", "certification_input": reference.model_dump(mode="json")}
  task = asyncio.create_task(transfer.publish_certification_input(job, attempt, result, object()))
  assert await asyncio.to_thread(entered.wait, 5)
  task.cancel()
  with pytest.raises(asyncio.CancelledError):
    await task
  assert stopped.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("handed_off", [None, "QUEUED"])
async def test_dispatch_acknowledgement_failure_respects_persisted_handoff(tmp_path, monkeypatch, handed_off):
  job = SimpleNamespace(kind="CERTIFY", job_id="job", flow_run_id="owner", request={})
  repository = SimpleNamespace(claim=AsyncMock(return_value=job), certification_handoff_status=AsyncMock(return_value=handed_off))

  @asynccontextmanager
  async def session():
    yield SimpleNamespace(expunge=lambda row: None)

  monkeypatch.setattr(flow, "root", lambda: tmp_path)
  monkeypatch.setattr(flow, "_full_live_runtime", lambda: False)
  monkeypatch.setattr(flow, "AsyncSessionLocal", session)
  monkeypatch.setattr(flow, "ResearchPreparationRepository", lambda db: repository)
  monkeypatch.setattr(flow, "perform", AsyncMock(side_effect=ConnectionError("commit acknowledgement lost")))
  update = AsyncMock()
  monkeypatch.setattr(flow, "update_job", update)
  await flow.research_preparation_dispatch_flow.fn()
  assert update.await_count == int(handed_off is None)
  if handed_off is None:
    assert update.await_args.kwargs["status"] == "FAILED"
