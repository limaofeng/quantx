import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.training_bundle_store import publication_lock
from quantx_trainer import service_reconciliation as reconciliation
from quantx_trainer.admission import set_admission
from quantx_trainer.service_exit import record_group_exit
from quantx_trainer.service_status import ServiceReporter


def evidence(tmp_path, monkeypatch):
  path = tmp_path / "config.toml"
  path.write_text("fixture")
  config = SimpleNamespace(state_root=tmp_path)
  for directory in ("service", "service-launches"):
    (tmp_path / directory).mkdir()
  reporter = ServiceReporter(tmp_path / "service", path)
  reporter.write()
  record_group_exit(tmp_path, path, reporter.identity["instance_id"], forced=True)
  set_admission(tmp_path / "control", draining=True)
  monkeypatch.setattr(reconciliation.TrainerConfig, "load", lambda value: config)
  monkeypatch.setattr(reconciliation, "current_config", lambda: config)
  return config, path


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [False, True])
async def test_reconciliation_reports_remaining_trainer_work_and_holds_lifecycle_locks(
  tmp_path, monkeypatch, remaining
):
  config, path = evidence(tmp_path, monkeypatch)
  training = SimpleNamespace(
    list_runs=AsyncMock(
      return_value=[SimpleNamespace(run_id="unconfirmed-run")] if remaining else []
    )
  )
  worker_job = SimpleNamespace(job_id="worker-export", kind="CERTIFY", request={})
  jobs = [worker_job] + (
    [SimpleNamespace(job_id="unconfirmed-gpu", kind="GPU")] if remaining else []
  )
  preparation = SimpleNamespace(running_jobs=AsyncMock(return_value=jobs))
  monkeypatch.setattr(
    reconciliation, "StockSelectionTrainingRepository", lambda db: training
  )
  monkeypatch.setattr(
    reconciliation, "ResearchPreparationRepository", lambda db: preparation
  )
  recover_runs = AsyncMock(return_value=["recovered-run"])
  recover_jobs = AsyncMock(return_value=["recovered-job"])
  monkeypatch.setattr(reconciliation, "recover_lost_training_runs", recover_runs)
  monkeypatch.setattr(reconciliation, "recover_preparation_results", recover_jobs)

  @asynccontextmanager
  async def session(config_path):
    assert config_path == str(path)
    for folder in ("service", "service-launches", "control/admission"):
      with pytest.raises(OSError):
        with publication_lock(tmp_path / folder):
          pytest.fail("lifecycle gate was released during recovery")
    yield object()

  monkeypatch.setattr(reconciliation, "training_session", session)
  result = await reconciliation.reconcile_stopped_service(path)
  assert result["database_state"] == ("PENDING" if remaining else "RECONCILED")
  assert result["pending_run_ids"] == (["unconfirmed-run"] if remaining else [])
  assert result["pending_job_ids"] == (["unconfirmed-gpu"] if remaining else [])
  recover_runs.assert_awaited_once_with(training)
  recover_jobs.assert_awaited_once_with(config, preparation, training)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["resumed", "receipt", "busy", "control_plane"])
async def test_failed_reconciliation_gate_preserves_pending_state(
  tmp_path, monkeypatch, fault
):
  config, path = evidence(tmp_path, monkeypatch)
  if fault == "resumed":
    set_admission(tmp_path / "control", draining=False)
  elif fault == "receipt":
    path.write_text("changed config")
  recover = AsyncMock()
  monkeypatch.setattr(reconciliation, "recover_lost_training_runs", recover)

  @asynccontextmanager
  async def session(config_path):
    raise ConnectionError("private endpoint unavailable")
    yield

  monkeypatch.setattr(reconciliation, "training_session", session)
  if fault == "busy":
    with publication_lock(tmp_path / "service"):
      result = await reconciliation.reconcile_stopped_service(path)
  else:
    result = await reconciliation.reconcile_stopped_service(path)
  assert result == {
    "database_state": "PENDING",
    "reason": "TRAINER_RECONCILIATION_PENDING",
  }
  recover.assert_not_called()


@pytest.mark.parametrize("execution,database,code", [
  ("GROUP_EXITED", "RECONCILED", 0),
  ("GROUP_EXITED", "PENDING", 3),
  ("NOT_INSPECTED", "PENDING", 0),
])
def test_down_command_only_reconciles_proven_group_exit(tmp_path, monkeypatch, capsys, execution, database, code):
  from quantx_trainer import launcher, main

  config = SimpleNamespace(validate_runtime=lambda **kwargs: None)
  monkeypatch.setattr(main.TrainerConfig, "load", lambda path: config)
  monkeypatch.setattr(launcher, "stop_service", lambda *args: {
    "service": "OFFLINE", "execution_state": execution,
  })
  recover = AsyncMock(return_value={"database_state": database})
  monkeypatch.setattr(reconciliation, "reconcile_stopped_service", recover)
  assert main.main(["down", "--config", str(tmp_path / "config.toml")]) == code
  result = json.loads(capsys.readouterr().out)
  assert result["execution_state"] == execution
  assert recover.await_count == int(execution == "GROUP_EXITED")
