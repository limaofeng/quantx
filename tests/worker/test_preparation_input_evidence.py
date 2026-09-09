import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_worker.prefector.flows import research_preparation_flow as flow


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["claim", "input", "cancel", "compute"])
async def test_stopped_precompute_attempt_recovers_with_live_supervisor(tmp_path, monkeypatch, fault):
  job = SimpleNamespace(job_id="job", kind="CERTIFY", request={"dataset_version": "version"})
  repository = SimpleNamespace(running_jobs=AsyncMock(return_value=[]), certification_handoff_status=AsyncMock(return_value=None), requeue_worker_inputs=AsyncMock())

  async def claim(owner, *, kinds, executor, prepare_execution):
    assert executor == "WORKER"
    job.flow_run_id = owner
    prepare_execution(job.job_id, owner)
    if fault == "claim":
      raise ConnectionError("commit acknowledgement lost")
    return job

  async def perform(job, directory):
    if fault == "compute":
      _, evidence = flow.certification_execution_paths(directory, job.flow_run_id)
      evidence.write_text("unknown compute state")
    if fault == "cancel":
      raise asyncio.CancelledError
    raise ConnectionError("input preparation interrupted")

  @asynccontextmanager
  async def session():
    yield SimpleNamespace(expunge=lambda row: None)

  repository.claim = claim
  monkeypatch.setattr(flow, "root", lambda: tmp_path)
  monkeypatch.setattr(flow, "_full_live_runtime", lambda: False)
  monkeypatch.setattr(flow, "AsyncSessionLocal", session)
  monkeypatch.setattr(flow, "ResearchPreparationRepository", lambda db: repository)
  monkeypatch.setattr(flow, "perform", perform)
  monkeypatch.setattr(flow, "update_job", AsyncMock(side_effect=asyncio.CancelledError if fault == "cancel" else ConnectionError("control unavailable")))
  with pytest.raises(asyncio.CancelledError if fault == "cancel" else ConnectionError):
    await flow.research_preparation_dispatch_flow.fn()
  repository.running_jobs.return_value = [job]
  assert await flow.recover_certification_exports() == ([] if fault == "compute" else ["job"])
  assert repository.requeue_worker_inputs.await_count == int(fault != "compute")
  if fault != "compute":
    repository.requeue_worker_inputs.assert_awaited_once_with("job", expected_flow_run_id=job.flow_run_id)
