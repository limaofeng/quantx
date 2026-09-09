from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_trainer import training_flow as flow


@pytest.fixture(autouse=True)
def training_configuration(monkeypatch, tmp_path):
  monkeypatch.setattr(flow, "current_config", lambda: SimpleNamespace(state_root=tmp_path))


@pytest.mark.asyncio
async def test_empty_process_dictionary_does_not_prove_running_job_was_lost(
  monkeypatch, tmp_path
):
  repository = SimpleNamespace(
    list_runs=AsyncMock(
      return_value=[SimpleNamespace(run_id="run-1", prefect_flow_run_id="owner")]
    ),
    fail_run=AsyncMock(),
  )
  monkeypatch.setattr(flow, "control_root", lambda: tmp_path.resolve())
  monkeypatch.setattr(flow, "_processes", {})
  assert await flow.recover_lost_training_runs(repository) == []
  repository.fail_run.assert_not_called()
  assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["LIVE", "UNKNOWN", "EXITED"])
async def test_only_proven_exit_is_converged_with_the_original_execution_owner(
  monkeypatch, tmp_path, state
):
  repository = SimpleNamespace(
    list_runs=AsyncMock(
      return_value=[SimpleNamespace(run_id="run-1", prefect_flow_run_id="owner")]
    ),
    fail_run=AsyncMock(),
  )
  monkeypatch.setattr(flow, "control_root", lambda: tmp_path.resolve())
  monkeypatch.setattr(flow, "_processes", {})
  monkeypatch.setattr(flow, "inspect_execution", lambda *args, **kwargs: state)
  assert await flow.recover_lost_training_runs(repository) == (
    ["run-1"] if state == "EXITED" else []
  )
  if state == "EXITED":
    assert repository.fail_run.call_args.kwargs["expected_flow_run_id"] == "owner"
  else:
    repository.fail_run.assert_not_called()
