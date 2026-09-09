import asyncio
import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_trainer import preparation_flow as flow


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ready", "blocked", "unconfirmed"])
async def test_gpu_job_is_owned_by_trainer_with_verified_inputs(
  monkeypatch, tmp_path, outcome
):
  job = SimpleNamespace(
    job_id="job",
    flow_run_id="owner",
    kind="GPU",
    request={"dataset_version": "dataset"},
  )
  repo = SimpleNamespace(claim=AsyncMock(return_value=job), progress=AsyncMock())

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
    return {"ready": outcome == "ready"}

  monkeypatch.setattr(flow, "training_session", session)
  monkeypatch.setattr(flow, "current_config", lambda: object())
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
    == {"ready": "SUCCEEDED", "blocked": "FAILED", "unconfirmed": "RUNNING"}[outcome]
  )
  for call in repo.progress.await_args_list:
    assert call.kwargs["expected_flow_run_id"] == "owner"
    if outcome == "unconfirmed":
      assert "status" not in call.kwargs


@pytest.mark.asyncio
async def test_interrupted_spawn_keeps_unknown_execution_evidence(monkeypatch, tmp_path):
  monkeypatch.setattr(flow.asyncio, "create_subprocess_exec", AsyncMock(side_effect=asyncio.CancelledError))
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
