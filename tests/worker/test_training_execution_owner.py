import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_worker.prefector.flows import stock_selection_training_flow as flow


@pytest.mark.asyncio
async def test_lost_owner_stops_child_without_converging_someone_elses_run(
  monkeypatch, tmp_path
):
  calls = []

  class Process:
    alive = True

    def poll(self):
      return None if self.alive else -15

    def terminate(self):
      calls.append("terminate")
      self.alive = False

    def wait(self, timeout):
      calls.append("wait")
      return -15

  process = Process()
  repository = SimpleNamespace(
    get_run=AsyncMock(
      return_value=SimpleNamespace(status="RUNNING", prefect_flow_run_id="new-owner")
    ),
    fail_run=AsyncMock(),
    complete_run=AsyncMock(),
    mark_cancelled=AsyncMock(),
  )
  monkeypatch.setattr(flow, "_control_directory", lambda run_id: tmp_path.resolve())
  monkeypatch.setattr(flow, "resolve_dataset_directory", lambda dataset: {})
  monkeypatch.setattr(flow, "build_training_request", lambda *args, **kwargs: {})
  monkeypatch.setattr(flow, "_spawn_process", lambda *args: process)
  monkeypatch.setattr(flow, "record_spawn", lambda *args, **kwargs: None)
  result = await flow._run_claimed_job(
    repository,
    SimpleNamespace(
      run_id="run-1", run_kind="DEVELOPMENT", prefect_flow_run_id="old-owner"
    ),
    object(),
    object(),
  )
  assert result == {"run_id": "run-1", "status": "OWNERSHIP_LOST"}
  assert calls == ["terminate", "wait"]
  repository.fail_run.assert_not_called()
  repository.complete_run.assert_not_called()
  repository.mark_cancelled.assert_not_called()


@pytest.mark.asyncio
async def test_stubborn_child_is_killed_after_bounded_grace():
  calls = []

  class Process:
    def poll(self):
      return None

    def terminate(self):
      calls.append("terminate")

    def kill(self):
      calls.append("kill")

    def wait(self, timeout):
      assert timeout == 5
      calls.append("wait")
      if "kill" not in calls:
        raise subprocess.TimeoutExpired("research", timeout)
      return -9

  await flow._stop_research_process(Process())
  assert calls == ["terminate", "wait", "kill", "wait"]
