import asyncio
import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_trainer import preparation_flow as flow


@pytest.fixture(autouse=True)
def logger(monkeypatch):
  monkeypatch.setattr(
    flow,
    "get_run_logger",
    lambda: SimpleNamespace(
      warning=lambda *args: pytest.fail("input evidence not persisted")
    ),
  )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["claim", "input", "cancel", "compute"])
async def test_gpu_input_attempt_recovers_without_restarting_supervisor(
  monkeypatch, tmp_path, fault
):
  from quantx_trainer import training_flow

  config = SimpleNamespace(state_root=tmp_path)
  job = SimpleNamespace(job_id="job", request={"dataset_version": "dataset"})
  repo = SimpleNamespace(
    running_jobs=AsyncMock(return_value=[]),
    progress=AsyncMock(side_effect=ConnectionError("control unavailable")),
    requeue_gpu_inputs=AsyncMock(),
  )

  @asynccontextmanager
  async def session(path):
    yield object()

  async def claim(owner, *, kinds, prepare_execution):
    job.flow_run_id = owner
    prepare_execution(job.job_id, owner)
    if fault == "claim":
      raise ConnectionError("commit acknowledgement lost")
    return job

  async def load(*args, **kwargs):
    if fault == "compute":
      directory = flow.attempt_directory(config, job)
      (directory / "process.json").write_text("unknown spawn")
    if fault == "cancel":
      raise asyncio.CancelledError
    raise ConnectionError("input interrupted")

  repo.claim = claim
  monkeypatch.setattr(flow, "training_session", session)
  monkeypatch.setattr(flow, "current_config", lambda: config)
  monkeypatch.setattr(training_flow, "control_root", lambda: tmp_path / "control")
  monkeypatch.setattr(flow, "_host_admission_reason", lambda: None)
  monkeypatch.setattr(flow, "ResearchPreparationRepository", lambda db: repo)
  monkeypatch.setattr(
    flow,
    "StockSelectionTrainingRepository",
    lambda db: SimpleNamespace(get_dataset=AsyncMock(return_value=object())),
  )
  monkeypatch.setattr(flow, "load_dataset", load)
  with pytest.raises(asyncio.CancelledError if fault == "cancel" else ConnectionError):
    await flow.trainer_gpu_preparation_flow.fn(config_path="test")
  repo.running_jobs.return_value = [job]
  assert await flow.recover_gpu_results(config, repo) == (
    [] if fault == "compute" else ["job"]
  )
  assert repo.requeue_gpu_inputs.await_count == int(fault != "compute")


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "outcome", ["ready", "blocked", "unconfirmed", "registration", "admission"]
)
async def test_gpu_job_is_owned_by_trainer_with_verified_inputs(
  monkeypatch, tmp_path, outcome
):
  job = SimpleNamespace(
    job_id="job",
    flow_run_id="owner",
    kind="GPU",
    request={"dataset_version": "dataset"},
  )
  repo = SimpleNamespace(
    claim=AsyncMock(return_value=job),
    requeue_gpu_admission=AsyncMock(),
    progress=AsyncMock(),
    running_jobs=AsyncMock(return_value=[]),
  )
  if outcome == "registration":

    async def progress(*args, **kwargs):
      if "status" in kwargs:
        raise ConnectionError("registration unavailable")

    repo.progress.side_effect = progress

  @asynccontextmanager
  async def session(path):
    yield object()

  async def load(config, repository, dataset, **kwargs):
    await kwargs["check"]()
    return {"directory": tmp_path / "verified"}

  async def execute(config, actual_job, files, check):
    assert actual_job is job
    assert files["directory"] == tmp_path / "verified"
    if outcome == "unconfirmed":
      raise flow.PreparationStopUnconfirmed()
    if outcome == "admission":
      raise flow.GPUAdmissionDenied()
    return {"ready": outcome in {"ready", "registration"}}

  monkeypatch.setattr(flow, "training_session", session)
  monkeypatch.setattr(
    flow, "current_config", lambda: SimpleNamespace(state_root=tmp_path)
  )
  monkeypatch.setattr(flow, "_host_admission_reason", lambda: None)
  monkeypatch.setattr(flow, "ResearchPreparationRepository", lambda db: repo)
  monkeypatch.setattr(
    flow,
    "StockSelectionTrainingRepository",
    lambda db: SimpleNamespace(get_dataset=AsyncMock(return_value=object())),
  )
  monkeypatch.setattr(flow, "load_dataset", load)
  monkeypatch.setattr(flow, "run_gpu_job", execute)
  result = await flow.trainer_gpu_preparation_flow.fn(config_path="test.toml")
  assert repo.claim.await_args.kwargs["kinds"] == ("GPU",)
  assert (
    result["status"]
    == {
      "ready": "SUCCEEDED",
      "blocked": "FAILED",
      "unconfirmed": "RUNNING",
      "admission": "QUEUED",
      "registration": "RUNNING",
    }[outcome]
  )
  for call in repo.progress.await_args_list:
    assert call.kwargs["expected_flow_run_id"] == "owner"
    if outcome == "unconfirmed":
      assert "status" not in call.kwargs
    if outcome == "registration":
      assert call.kwargs.get("status") != "FAILED"
  if outcome == "admission":
    repo.requeue_gpu_admission.assert_awaited_once_with(
      "job", expected_flow_run_id="owner"
    )


@pytest.mark.asyncio
async def test_real_host_denial_exit_is_recorded_before_requeue(monkeypatch, tmp_path):
  create = asyncio.create_subprocess_exec

  async def spawn(*args, **kwargs):
    return await create(sys.executable, "-c", "raise SystemExit(75)", **kwargs)

  monkeypatch.setattr(flow.asyncio, "create_subprocess_exec", spawn)
  config = SimpleNamespace(state_root=tmp_path, research_environment=lambda ambient: {})
  job = SimpleNamespace(job_id="job", flow_run_id="owner", request={})
  with pytest.raises(flow.GPUAdmissionDenied):
    await flow.run_gpu_job(config, job, {"directory": tmp_path / "cache"}, AsyncMock())
  evidence = json.loads(
    (flow.attempt_directory(config, job) / "process.json").read_text()
  )
  assert evidence["state"] == "EXITED"
  assert evidence["returncode"] == 75


@pytest.mark.asyncio
async def test_interrupted_spawn_keeps_unknown_execution_evidence(
  monkeypatch, tmp_path
):
  monkeypatch.setattr(
    flow.asyncio,
    "create_subprocess_exec",
    AsyncMock(side_effect=asyncio.CancelledError),
  )
  config = SimpleNamespace(state_root=tmp_path, research_environment=lambda ambient: {})
  job = SimpleNamespace(job_id="job", flow_run_id="owner", request={})
  with pytest.raises(flow.PreparationStopUnconfirmed):
    await flow.run_gpu_job(config, job, {"directory": tmp_path / "cache"}, AsyncMock())
  evidence = next((tmp_path / "preparation").rglob("process.json"))
  assert json.loads(evidence.read_text())["state"] == "STARTING"


@pytest.mark.asyncio
async def test_gpu_supervisor_uses_file_request_and_records_real_child_exit(
  monkeypatch, tmp_path
):
  create = asyncio.create_subprocess_exec
  captured = {}

  async def spawn(*args, **kwargs):
    captured.update(command=args, environment=kwargs["env"])
    code = "import pathlib,sys; pathlib.Path(sys.argv[1]).with_name('result.json').write_text('{\"ready\": true}')"
    return await create(sys.executable, "-c", code, args[-1], **kwargs)

  monkeypatch.setattr(flow.asyncio, "create_subprocess_exec", spawn)
  config = SimpleNamespace(
    state_root=tmp_path, research_environment=lambda ambient: {"PYTHONUTF8": "1"}
  )
  job = SimpleNamespace(
    job_id="job",
    flow_run_id="owner",
    request={"dataset_version": "dataset", "config": {}},
  )
  result = await flow.run_gpu_job(
    config, job, {"directory": tmp_path / "cache"}, AsyncMock()
  )
  assert result == {"ready": True}
  assert captured["command"][0] == sys.executable
  assert captured["command"][2] == "quantx_research.preparation_job"
  from pathlib import Path

  request = Path(captured["command"][-1])
  payload = json.loads(request.read_text())
  assert payload["dataset_directory"] == str(tmp_path / "cache")
  assert payload["qualification_output"] == str(tmp_path / "gpu" / "qualification.json")
  assert "DATABASE_URL" not in captured["environment"]
  evidence = json.loads(request.with_name("process.json").read_text())
  assert evidence["state"] == "EXITED"
  assert evidence["returncode"] == 0
  repo = SimpleNamespace(
    running_jobs=AsyncMock(return_value=[job]),
    progress=AsyncMock(side_effect=[ConnectionError("lost DB"), None]),
  )
  assert await flow.recover_gpu_results(config, repo) == []
  assert await flow.recover_gpu_results(config, repo) == ["job"]
  assert repo.progress.await_args.kwargs["status"] == "SUCCEEDED"
  assert repo.progress.await_args.kwargs["expected_flow_run_id"] == "owner"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "fault", ["locked", "request", "nonzero", "result", "admission"]
)
async def test_recovery_rejects_active_or_unverifiable_gpu_attempt(tmp_path, fault):
  from contextlib import nullcontext

  config = SimpleNamespace(state_root=tmp_path)
  job = SimpleNamespace(job_id="job", flow_run_id="owner")
  directory = flow.attempt_directory(config, job)
  directory.mkdir(parents=True)
  request = directory / "request.json"
  request.write_text("{}")
  identity = dict(run_id="job", owner="owner", request=request)
  evidence = directory / "process.json"
  flow.begin_execution(evidence, **identity)
  flow.record_exit(
    evidence, returncode={"nonzero": 1, "admission": 75}.get(fault, 0), **identity
  )
  (directory / "result.json").write_text(
    "{}" if fault == "result" else '{"ready":true}'
  )
  if fault == "request":
    request.write_text("changed")
  repo = SimpleNamespace(
    running_jobs=AsyncMock(return_value=[job]),
    progress=AsyncMock(),
    requeue_gpu_admission=AsyncMock(),
  )
  with flow.publication_lock(directory) if fault == "locked" else nullcontext():
    assert await flow.recover_gpu_results(config, repo) == (
      ["job"] if fault in {"nonzero", "admission"} else []
    )
  if fault == "nonzero":
    assert repo.progress.await_args.kwargs["status"] == "FAILED"
  else:
    repo.progress.assert_not_called()
  assert repo.requeue_gpu_admission.await_count == int(fault == "admission")
