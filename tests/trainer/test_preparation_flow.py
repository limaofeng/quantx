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
@pytest.mark.parametrize("kind", ["GPU", "CERTIFY"])
@pytest.mark.parametrize("fault", ["claim", "input", "cancel", "compute"])
async def test_gpu_input_attempt_recovers_without_restarting_supervisor(
  monkeypatch, tmp_path, fault, kind
):
  from quantx_trainer import training_flow

  config = SimpleNamespace(state_root=tmp_path)
  job = SimpleNamespace(kind=kind, job_id="job", request={"dataset_version": "dataset", "certification_input": {"manifest_sha256": "a" * 64}})
  repo = SimpleNamespace(
    running_jobs=AsyncMock(return_value=[]),
    progress=AsyncMock(side_effect=ConnectionError("control unavailable")),
    requeue_trainer_inputs=AsyncMock(),
  )

  @asynccontextmanager
  async def session(path):
    yield object()

  async def claim(owner, *, kinds, executor, prepare_execution):
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
  monkeypatch.setattr(flow, "load_certification_input", load)
  with pytest.raises(asyncio.CancelledError if fault == "cancel" else ConnectionError):
    await flow.trainer_preparation_flow.fn(config_path="test")
  repo.running_jobs.return_value = [job]
  assert await flow.recover_preparation_results(config, repo, SimpleNamespace()) == (
    [] if fault == "compute" else ["job"]
  )
  assert repo.requeue_trainer_inputs.await_count == int(fault != "compute")


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "outcome", ["ready", "blocked", "unconfirmed", "registration", "admission"]
)
async def test_gpu_job_is_owned_by_trainer_with_verified_inputs(
  monkeypatch, tmp_path, outcome
):
  job = SimpleNamespace(kind="GPU",
    job_id="job",
    flow_run_id="owner",
    request={"dataset_version": "dataset"},
  )
  repo = SimpleNamespace(
    claim=AsyncMock(return_value=job),
    requeue_trainer_admission=AsyncMock(),
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
      raise flow.PreparationAdmissionDenied()
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
  result = await flow.trainer_preparation_flow.fn(config_path="test.toml")
  assert repo.claim.await_args.kwargs["kinds"] == ("GPU", "CERTIFY")
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
    repo.requeue_trainer_admission.assert_awaited_once_with(
      "job", expected_flow_run_id="owner"
    )


@pytest.mark.asyncio
async def test_real_host_denial_exit_is_recorded_before_requeue(monkeypatch, tmp_path):
  create = asyncio.create_subprocess_exec

  async def spawn(*args, **kwargs):
    return await create(sys.executable, "-c", "raise SystemExit(75)", **kwargs)

  monkeypatch.setattr(flow.asyncio, "create_subprocess_exec", spawn)
  config = SimpleNamespace(state_root=tmp_path, research_environment=lambda ambient: {})
  job = SimpleNamespace(kind="GPU", job_id="job", flow_run_id="owner", request={})
  with pytest.raises(flow.PreparationAdmissionDenied):
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
  job = SimpleNamespace(kind="GPU", job_id="job", flow_run_id="owner", request={})
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
  job = SimpleNamespace(kind="GPU",
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
  assert await flow.recover_preparation_results(config, repo, SimpleNamespace()) == []
  assert await flow.recover_preparation_results(config, repo, SimpleNamespace()) == ["job"]
  assert repo.progress.await_args.kwargs["status"] == "SUCCEEDED"
  assert repo.progress.await_args.kwargs["expected_flow_run_id"] == "owner"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "fault", ["locked", "request", "nonzero", "result", "admission"]
)
async def test_recovery_rejects_active_or_unverifiable_gpu_attempt(tmp_path, fault):
  from contextlib import nullcontext

  config = SimpleNamespace(state_root=tmp_path)
  job = SimpleNamespace(kind="GPU", job_id="job", flow_run_id="owner")
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
    requeue_trainer_admission=AsyncMock(),
  )
  with flow.publication_lock(directory) if fault == "locked" else nullcontext():
    assert await flow.recover_preparation_results(config, repo, SimpleNamespace()) == (
      ["job"] if fault in {"nonzero", "admission"} else []
    )
  if fault == "nonzero":
    assert repo.progress.await_args.kwargs["status"] == "FAILED"
  else:
    repo.progress.assert_not_called()
  assert repo.requeue_trainer_admission.await_count == int(fault == "admission")


@pytest.mark.asyncio
async def test_certification_dispatch_recovers_publication_without_new_compute(monkeypatch, tmp_path):
  from quantx_trainer import certification_result

  from tests.trainer.test_certification_result import attempt, values

  _, job, _ = attempt(tmp_path / "template")
  job.kind = "CERTIFY"
  config = SimpleNamespace(state_root=tmp_path / "trainer", research_environment=lambda ambient: {"PYTHONUTF8": "1"})
  preparation = SimpleNamespace(claim=AsyncMock(return_value=job), running_jobs=AsyncMock(return_value=[]), progress=AsyncMock())
  datasets = SimpleNamespace(certify_dataset=AsyncMock())

  @asynccontextmanager
  async def session(path):
    yield object()

  create = asyncio.create_subprocess_exec
  starts = []

  async def spawn(*args, **kwargs):
    starts.append(args)
    script = (
      "import json,pathlib,sys; p=pathlib.Path(sys.argv[1]); r=json.loads(p.read_text()); "
      "p.with_name('result.json').write_text(json.dumps({'ready':True,'dataset_version':r['dataset_version'],"
      "'manifest_sha256':'b'*64,'input_manifest_sha256':r['certification_input']['manifest_sha256']}))"
    )
    return await create(sys.executable, "-c", script, args[-1], **kwargs)

  monkeypatch.setattr(flow, "training_session", session)
  monkeypatch.setattr(flow, "current_config", lambda: config)
  monkeypatch.setattr(flow, "_host_admission_reason", lambda: None)
  monkeypatch.setattr(flow, "ResearchPreparationRepository", lambda db: preparation)
  monkeypatch.setattr(flow, "StockSelectionTrainingRepository", lambda db: datasets)
  monkeypatch.setattr(flow, "load_certification_input", AsyncMock(return_value={"directory": tmp_path / "inputs"}))
  monkeypatch.setattr(flow.asyncio, "create_subprocess_exec", spawn)
  monkeypatch.setattr(certification_result, "certification_values", lambda **kwargs: values())
  publication = AsyncMock(side_effect=[ConnectionError("store unavailable"), {"status": "PUBLISHED"}])
  monkeypatch.setattr(certification_result, "publish_dataset", publication)

  first = await flow.trainer_preparation_flow.fn(config_path="trainer.toml")
  assert first["status"] == "RUNNING"
  assert first["reason"] == "PREPARATION_RESULT_REGISTRATION_PENDING"
  assert len(starts) == 1
  assert not any(call.kwargs.get("status") == "SUCCEEDED" for call in preparation.progress.await_args_list)
  preparation.running_jobs.return_value = [job]
  # Recovery still executes when the host currently refuses new computation.
  monkeypatch.setattr(flow, "_host_admission_reason", lambda: "RESOURCE_BUSY")
  second = await flow.trainer_preparation_flow.fn(config_path="trainer.toml")
  assert second["status"] == "QUEUED"
  assert len(starts) == 1 and preparation.claim.await_count == 1
  assert publication.await_count == 2
  assert preparation.progress.await_args.kwargs["status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_recovery_does_not_touch_worker_certification_exports(tmp_path):
  job = SimpleNamespace(kind="CERTIFY", job_id="worker-job", flow_run_id="worker", request={})
  repository = SimpleNamespace(running_jobs=AsyncMock(return_value=[job]), progress=AsyncMock())
  assert await flow.recover_preparation_results(SimpleNamespace(state_root=tmp_path), repository, object()) == []
  repository.progress.assert_not_awaited()
  assert list(tmp_path.iterdir()) == []
