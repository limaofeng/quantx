import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from quantx_infrastructure.training_bundle_store import publication_lock
from quantx_trainer import preflight, service
from quantx_trainer.admission import admission_status, set_admission
from quantx_trainer.config import TrainerConfig


@pytest.mark.parametrize("failure", [None, "preflight", "worker"])
def test_serve_isolates_environment_holds_lease_and_preserves_drain(
  tmp_path, monkeypatch, failure
):
  config_path = tmp_path / "trainer.toml"
  config_path.write_text("fixture")
  config = SimpleNamespace(
    state_root=tmp_path / "state",
    code_root=tmp_path,
    environment="development",
    database_url="must-not-reach-worker",
    prefect_api_url="http://127.0.0.1:30421/api",
    prefect_pool="quantx-train-pool",
  )
  config.child_environment = lambda ambient: TrainerConfig.child_environment(
    config, ambient
  )
  monkeypatch.setenv("DATABASE_URL", "production-sentinel")
  monkeypatch.setenv("PYTHONPATH", "production-python-sentinel")
  monkeypatch.setenv("PREFECT_API_KEY", "unrelated-profile-sentinel")
  monkeypatch.setattr(service.sys, "platform", "darwin")
  original = dict(os.environ)
  directory = Path.cwd()
  set_admission(config.state_root / "control", draining=True)
  events = []

  async def check(config):
    events.append("preflight")
    if failure == "preflight":
      raise RuntimeError("preflight rejected")

  async def run(actual, path):
    events.append("worker")
    assert actual is config and path == config_path
    assert Path.cwd() == config.code_root
    assert os.environ["PREFECT_API_URL"] == config.prefect_api_url
    assert os.environ["QUANTX_TRAINER_CONFIG"] == str(config_path)
    assert os.environ["PREFECT_SERVER_ALLOW_EPHEMERAL_MODE"] == "false"
    assert os.environ["PREFECT_HOME"] == str(config.state_root / "prefect")
    assert not {"DATABASE_URL", "PYTHONPATH", "PREFECT_API_KEY"} & os.environ.keys()
    with pytest.raises(OSError):
      with publication_lock(config.state_root / "service"):
        pytest.fail("second service acquired the lease")
    if failure == "worker":
      raise RuntimeError("worker unavailable")

  monkeypatch.setattr(preflight, "preflight", check)
  monkeypatch.setattr(service, "_run_worker", run)
  if failure:
    with pytest.raises(RuntimeError):
      service.serve(config, config_path)
  else:
    service.serve(config, config_path)
  assert events == (
    ["preflight"] if failure == "preflight" else ["preflight", "worker"]
  )
  assert dict(os.environ) == original
  assert Path.cwd() == directory
  assert admission_status(config.state_root / "control")["admission"] == "DRAINING"
  with publication_lock(config.state_root / "service"):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_real_deployment_contracts_precede_worker_start(
  monkeypatch, tmp_path, failure
):
  from prefect.deployments.runner import RunnerDeployment
  from prefect.workers import process

  deployments, events = [], []
  root = Path(__file__).resolve().parents[2]
  config = SimpleNamespace(code_root=root, prefect_pool="quantx-train-pool")
  config_path = tmp_path / "trainer.toml"

  async def apply(deployment, **kwargs):
    deployments.append(deployment)
    if failure:
      raise ConnectionError("registration unavailable")

  class Worker:
    def __init__(self, **kwargs):
      assert len(deployments) == 3
      assert kwargs == dict(
        work_pool_name="quantx-train-pool",
        work_queues=["default"],
        name="quantx-trainer",
        create_pool_if_not_found=False,
        limit=3,
      )
      events.append("worker")

    async def start(self):
      events.append("started")

  monkeypatch.setattr(RunnerDeployment, "apply", apply)
  monkeypatch.setattr(process, "ProcessWorker", Worker)
  if failure:
    with pytest.raises(ConnectionError):
      await service._run_worker(config, config_path)
    assert events == []
    return
  await service._run_worker(config, config_path)
  assert events == ["worker", "started"]
  specification = yaml.safe_load((root / "apps/trainer/prefect.yaml").read_text())
  for actual, expected in zip(deployments, specification["deployments"], strict=True):
    assert actual.name == expected["name"]
    assert actual.entrypoint == expected["entrypoint"]
    assert actual.parameters == {"config_path": str(config_path)}
    assert actual.work_pool_name == expected["work_pool"]["name"]
    assert actual.concurrency_limit == 1
    assert actual.concurrency_options.collision_strategy == "CANCEL_NEW"
    assert actual.schedules[0].schedule.timezone == "Asia/Shanghai"
    assert actual.job_variables == {"working_dir": str(root)}
