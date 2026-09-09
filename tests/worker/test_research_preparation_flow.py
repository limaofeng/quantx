import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from quantx_trainer import training_flow as training
from quantx_worker.prefector.flows import research_preparation_flow as preparation


@pytest.mark.asyncio
async def test_capability_flow_never_claims_jobs_and_does_not_refresh_failed_probe(
  monkeypatch,
):
  written = []

  @asynccontextmanager
  async def session(config_path):
    yield object()

  class Repo:
    def __init__(self, db):
      pass

    async def upsert_capability_heartbeat(self, **kwargs):
      written.append(kwargs)

  monkeypatch.setattr(training, "training_session", session)
  monkeypatch.setattr(training, "StockSelectionTrainingRepository", Repo)
  monkeypatch.setattr(
    training,
    "_probe_capability",
    lambda: {"cpu_available": True, "status": "GPU_UNAVAILABLE_BUILD"},
  )
  await training.stock_selection_training_capability_flow.fn(config_path="test.toml")
  assert len(written) == 1
  assert written[0]["details"]["cpu_available"] is True
  monkeypatch.setattr(training, "_probe_capability", lambda: {"probe_failed": True})
  with pytest.raises(RuntimeError):
    await training.stock_selection_training_capability_flow.fn(config_path="test.toml")
  assert len(written) == 1


@pytest.mark.asyncio
async def test_download_reuses_scope_and_rechecks_without_certification(
  monkeypatch, tmp_path
):
  calls, updates = [], []
  job = SimpleNamespace(job_id="fixed-job", flow_run_id="owner", kind="DOWNLOAD", request={})

  async def research(job, directory):
    calls.append("check")
    return {
      "ready": False,
      "downloadable": True,
      "checks": [],
      "stock_codes": ["600000.SH"],
      "download": {
        "stock_list": ["600000.SH", "000300.SH"],
        "periods": ["1d"],
        "compute_daily_signals": False,
        "start_time": "20210101",
        "end_time": "20251231",
      },
    }

  async def download(**kwargs):
    assert kwargs["stock_list"] == ["600000.SH", "000300.SH"]
    assert kwargs["idempotency_scope"] == "research-preparation-fixed-job"
    assert kwargs["compute_daily_signals"] is False
    calls.append("download")
    return {"status": "success"}

  async def update(job_id, **kwargs):
    updates.append(kwargs)

  monkeypatch.setattr(preparation, "run_research", research)
  monkeypatch.setattr(preparation, "daily_market_data_sync_flow", download)

  async def factors(payload, **kwargs):
    assert payload["operation"] == "divid_factors"
    assert payload["stock_list"] == ["600000.SH"]
    assert kwargs["idempotency_scope"] == "research-preparation-fixed-job-factors-0"

  monkeypatch.setattr(preparation, "_request_and_wait", factors)
  monkeypatch.setattr(preparation, "update_job", update)
  await preparation.perform(job, tmp_path)
  assert calls == ["check", "download", "check"]
  assert updates[-1]["status"] == "SUCCEEDED"
  assert updates[-1]["result"]["ready"] is False
  assert "download" not in updates[-1]["result"]
  await preparation.perform(job, tmp_path)
  assert calls == ["check", "download", "check", "download", "check"]


@pytest.mark.asyncio
@pytest.mark.parametrize("unconfirmed", [False, True])
async def test_dispatch_waits_for_work_to_stop_before_making_retry_available(monkeypatch, tmp_path, unconfirmed):
  stopped = asyncio.Event()
  running = asyncio.Event()
  job = SimpleNamespace(job_id="job", flow_run_id="owner", kind="DOWNLOAD")

  @asynccontextmanager
  async def session():
    yield SimpleNamespace(expunge=lambda row: None)

  async def claim(owner, *, kinds, executor):
    assert "GPU" not in kinds
    return job

  async def work(*args):
    running.set()
    try:
      await asyncio.Event().wait()
    finally:
      await asyncio.sleep(0)
      stopped.set()
      if unconfirmed:
        raise preparation.PreparationProcessUnconfirmed("unknown")

  async def heartbeat(*args):
    await running.wait()
    raise ConnectionError("heartbeat lost")

  async def update(job_id, **values):
    assert not unconfirmed
    assert stopped.is_set()
    assert values["status"] == "FAILED"
    assert values["expected_flow_run_id"] == "owner"

  monkeypatch.setattr(preparation, "AsyncSessionLocal", session)
  monkeypatch.setattr(preparation, "ResearchPreparationRepository", lambda db: SimpleNamespace(claim=claim))
  monkeypatch.setattr(preparation, "_full_live_runtime", lambda: False)
  monkeypatch.setattr(preparation, "root", lambda: tmp_path)
  monkeypatch.setattr(preparation, "perform", work)
  monkeypatch.setattr(preparation, "keep_alive", heartbeat)
  monkeypatch.setattr(preparation, "update_job", update)
  result = await preparation.research_preparation_dispatch_flow.fn()
  if unconfirmed:
    assert result["status"] == "RUNNING"
    assert result["reason"] == "PREPARATION_PROCESS_STOP_UNCONFIRMED"
  assert stopped.is_set()


@pytest.mark.asyncio
async def test_research_stop_failure_is_explicit_and_not_retryable(monkeypatch, tmp_path):
  from unittest.mock import AsyncMock

  process = SimpleNamespace(returncode=None, wait=AsyncMock(side_effect=asyncio.CancelledError))
  stop = AsyncMock(return_value=False)
  monkeypatch.setattr(preparation.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
  monkeypatch.setattr(preparation, "stop_async_process", stop)
  with pytest.raises(preparation.PreparationProcessUnconfirmed):
    await preparation.run_research(SimpleNamespace(kind="COVERAGE", request={}), tmp_path)
  stop.assert_awaited_once_with(process)


@pytest.mark.asyncio
async def test_keep_alive_does_not_depend_on_training_progress(monkeypatch):
  ticks = []

  async def sleep(seconds):
    assert seconds == 20
    if len(ticks) == 10:
      raise asyncio.CancelledError

  async def update(job_id, **kwargs):
    ticks.append(job_id)

  monkeypatch.setattr(preparation.asyncio, "sleep", sleep)
  monkeypatch.setattr(preparation, "update_job", update)
  with pytest.raises(asyncio.CancelledError):
    await preparation.keep_alive("job", "owner")
  assert len(ticks) == 10


@pytest.mark.asyncio
async def test_interrupted_spawn_cannot_make_export_retryable(tmp_path, monkeypatch):
  import asyncio
  from types import SimpleNamespace
  from unittest.mock import AsyncMock

  from quantx_worker.prefector.flows import research_preparation_flow as preparation

  monkeypatch.setattr(preparation.asyncio, "create_subprocess_exec", AsyncMock(side_effect=asyncio.CancelledError))
  with pytest.raises(preparation.PreparationProcessUnconfirmed, match="SPAWN_UNCONFIRMED"):
    await preparation.run_research(SimpleNamespace(kind="CERTIFY", request={}), tmp_path)
