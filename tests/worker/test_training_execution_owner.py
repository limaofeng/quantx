import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_trainer import training_flow as flow


@pytest.fixture(autouse=True)
def training_configuration(monkeypatch, tmp_path):
  monkeypatch.setattr(flow, "current_config", lambda: SimpleNamespace(state_root=tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,status", [
  ("PUBLICATION_CANCEL_REQUESTED", "CANCELLED"),
  ("PUBLICATION_OWNERSHIP_LOST", "OWNERSHIP_LOST"),
])
async def test_input_transfer_cancellation_never_starts_compute(monkeypatch, tmp_path, reason, status):
  repository = SimpleNamespace(mark_cancelled=AsyncMock(), fail_run=AsyncMock())
  monkeypatch.setattr(flow, "_control_directory", lambda run_id: tmp_path)
  monkeypatch.setattr(flow, "load_dataset", AsyncMock(side_effect=flow.PublicationError(reason)))
  monkeypatch.setattr(flow, "_spawn_process", lambda *args: pytest.fail("input unavailable"))
  result = await flow._run_claimed_job(repository, SimpleNamespace(run_id="run", prefect_flow_run_id="owner"), object(), object())
  assert result["status"] == status
  repository.fail_run.assert_not_called()
  assert repository.mark_cancelled.await_count == int(status == "CANCELLED")


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
  monkeypatch.setattr(flow, "load_dataset", AsyncMock(return_value={}))
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


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [False, True])
async def test_silent_research_gets_bounded_heartbeats_and_stops_on_disconnect(
  monkeypatch, tmp_path, disconnect
):
  class Process:
    alive = True

    def poll(self):
      return None if self.alive else -15

    def terminate(self):
      self.alive = False

    def wait(self, timeout):
      return -15

  process = Process()
  current = SimpleNamespace(status="RUNNING", prefect_flow_run_id="executor")
  repository = SimpleNamespace(
    get_run=AsyncMock(side_effect=[current, current, current, SimpleNamespace(status="CANCELLED")]),
    heartbeat_execution=AsyncMock(
      return_value=current, side_effect=ConnectionError("control plane unavailable") if disconnect else None
    ),
    fail_run=AsyncMock(),
    complete_run=AsyncMock(),
  )
  ticks = iter([0, 0, 9, 10, 10])
  monkeypatch.setattr(flow, "monotonic", lambda: next(ticks))
  monkeypatch.setattr(flow, "_control_directory", lambda run_id: tmp_path.resolve())
  monkeypatch.setattr(flow, "load_dataset", AsyncMock(return_value={}))
  monkeypatch.setattr(flow, "build_training_request", lambda *args, **kwargs: {})
  monkeypatch.setattr(flow, "_spawn_process", lambda *args: process)
  monkeypatch.setattr(flow, "record_spawn", lambda *args, **kwargs: None)
  result = await flow._run_claimed_job(
    repository,
    SimpleNamespace(run_id="run-1", run_kind="DEVELOPMENT", prefect_flow_run_id="executor"),
    object(), object(), poll_interval_seconds=0.01,
  )
  assert not process.alive
  assert (tmp_path / "process.json").is_file()
  assert result["status"] == ("FAILED" if disconnect else "OWNERSHIP_LOST")
  assert repository.heartbeat_execution.await_count == (1 if disconnect else 2)
  for call in repository.heartbeat_execution.call_args_list:
    assert call.kwargs == {"expected_flow_run_id": "executor"}
  repository.complete_run.assert_not_called()
