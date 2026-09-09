import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_trainer import training_flow as training


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", [None, "HOST_MEMORY_RESERVE", "stale"])
async def test_dispatch_uses_certificate_without_gpu_probe(monkeypatch, blocked):
  certificate = {"cpu_available": True, "requirement_hash": "a" * 64}
  run = SimpleNamespace(run_id="run", spec_id="spec")
  spec = SimpleNamespace(dataset_version="dataset", requested_backend="CPU")
  repo = SimpleNamespace(
    get_execution_capability=AsyncMock(
      return_value={} if blocked == "stale" else certificate
    ),
    claim_next_queued=AsyncMock(return_value=run),
    get_spec=AsyncMock(return_value=spec),
    get_dataset=AsyncMock(return_value=object()),
  )

  @asynccontextmanager
  async def session(config_path):
    yield object()

  def forbidden_probe():
    pytest.fail("dispatch must not initialize GPU")

  execute = AsyncMock(return_value={"status": "SUCCEEDED"})
  monkeypatch.setattr(training, "training_session", session)
  monkeypatch.setattr(training, "StockSelectionTrainingRepository", lambda db: repo)
  monkeypatch.setattr(
    training, "get_run_logger", lambda: SimpleNamespace(info=lambda *args: None)
  )
  monkeypatch.setattr(
    training, "recover_lost_training_runs", AsyncMock(return_value=[])
  )
  monkeypatch.setattr(training, "_probe_capability", forbidden_probe)
  monkeypatch.setattr(training, "_host_admission_reason", lambda: blocked)
  monkeypatch.setattr(training, "_run_claimed_job", execute)
  result = await training.stock_selection_training_dispatch_flow.fn(
    config_path="test.toml"
  )
  if blocked:
    assert result["status"] == "QUEUED"
    assert result["reason"] == (
      "CPU_TRAINING_UNAVAILABLE" if blocked == "stale" else blocked
    )
    repo.claim_next_queued.assert_not_awaited()
    execute.assert_not_awaited()
  else:
    assert result["status"] == "SUCCEEDED"
    assert execute.await_args.kwargs["capability"] == certificate
    assert callable(repo.claim_next_queued.await_args.kwargs["prepare_execution"])


def test_host_admission_missing_policy_stays_queued(monkeypatch, tmp_path):
  monkeypatch.setattr(training, "host_guard_root", lambda: tmp_path)
  assert training._host_admission_reason() == "HOST_POLICY_MISSING_OR_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["commit", "spec", "input", "cancel"])
async def test_finished_attempt_can_recover_while_supervisor_remains_alive(
  monkeypatch, tmp_path, fault
):
  from quantx_infrastructure.training_process_evidence import inspect_input_preparation

  run = SimpleNamespace(run_id="run", spec_id="spec", prefect_flow_run_id="owner")
  failure = asyncio.CancelledError if fault == "cancel" else ConnectionError

  @asynccontextmanager
  async def session(config_path):
    yield object()

  async def claim(owner, now, *, prepare_execution):
    prepare_execution(run.run_id, owner)
    if fault == "commit":
      raise failure()
    return run

  async def execute(*args, **kwargs):
    record, request = training._input_preparation_paths(tmp_path / "run", "owner")
    assert (
      inspect_input_preparation(record, run_id="run", owner="owner", request=request)
      == "LIVE"
    )
    raise failure()

  repo = SimpleNamespace(
    get_execution_capability=AsyncMock(return_value={"cpu_available": True}),
    list_runs=AsyncMock(return_value=[]),
    claim_next_queued=claim,
    get_spec=AsyncMock(return_value=SimpleNamespace(dataset_version="dataset")),
    get_dataset=AsyncMock(return_value=object()),
    requeue_stopped_input_preparation=AsyncMock(),
  )
  if fault == "spec":
    repo.get_spec.side_effect = failure()
  monkeypatch.setattr(training, "training_session", session)
  monkeypatch.setattr(training, "StockSelectionTrainingRepository", lambda db: repo)
  monkeypatch.setattr(training, "control_root", lambda: tmp_path)
  monkeypatch.setattr(
    training,
    "get_run_logger",
    lambda: SimpleNamespace(
      info=lambda *args: None,
      warning=lambda *args: pytest.fail("evidence not persisted"),
    ),
  )
  monkeypatch.setattr(training, "_host_admission_reason", lambda: None)
  monkeypatch.setattr(training, "_run_claimed_job", execute)
  with pytest.raises(failure):
    await training.stock_selection_training_dispatch_flow.fn(
      config_path="test", prefect_flow_run_id="owner"
    )
  record, request = training._input_preparation_paths(tmp_path / "run", "owner")
  assert (
    inspect_input_preparation(record, run_id="run", owner="owner", request=request)
    == "EXITED"
  )
  repo.list_runs.return_value = [run]
  assert await training.recover_lost_training_runs(repo) == ["run"]
  repo.requeue_stopped_input_preparation.assert_awaited_once_with(
    "run", expected_flow_run_id="owner"
  )
