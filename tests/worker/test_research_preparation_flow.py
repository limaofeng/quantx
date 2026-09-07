import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from quantx_worker.prefector.flows import research_preparation_flow as preparation
from quantx_worker.prefector.flows import stock_selection_training_flow as training


@pytest.mark.asyncio
async def test_capability_flow_never_claims_jobs_and_does_not_refresh_failed_probe(
  monkeypatch,
):
  written = []

  @asynccontextmanager
  async def session():
    yield object()

  class Repo:
    def __init__(self, db):
      pass

    async def upsert_capability_heartbeat(self, **kwargs):
      written.append(kwargs)

  monkeypatch.setattr(training, "AsyncSessionLocal", session)
  monkeypatch.setattr(training, "StockSelectionTrainingRepository", Repo)
  monkeypatch.setattr(
    training,
    "_probe_capability",
    lambda: {"cpu_available": True, "status": "GPU_UNAVAILABLE_BUILD"},
  )
  await training.stock_selection_training_capability_flow.fn()
  assert len(written) == 1
  assert written[0]["details"]["cpu_available"] is True
  monkeypatch.setattr(training, "_probe_capability", lambda: {"probe_failed": True})
  with pytest.raises(RuntimeError):
    await training.stock_selection_training_capability_flow.fn()
  assert len(written) == 1


@pytest.mark.asyncio
async def test_download_reuses_scope_and_rechecks_without_certification(
  monkeypatch, tmp_path
):
  calls, updates = [], []
  job = SimpleNamespace(job_id="fixed-job", kind="DOWNLOAD", request={})

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
    await preparation.keep_alive("job")
  assert len(ticks) == 10
