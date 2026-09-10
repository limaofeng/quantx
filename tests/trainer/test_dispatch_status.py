import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_trainer import dispatch_status as module
from quantx_trainer import runtime


@pytest.mark.parametrize("kind", ["training", "preparation"])
def test_decision_replaces_old_queue_reason_and_expires(tmp_path, monkeypatch, kind):
  config = tmp_path / "config.toml"
  config.write_text("fixture")
  monkeypatch.setattr(
    runtime, "current_config", lambda: SimpleNamespace(state_root=tmp_path)
  )
  monkeypatch.setattr(module.time, "time", lambda: 100)
  observer = module.DispatchObservation(config, kind)
  result = {
    "status": "QUEUED",
    "reason": "HOST_MEMORY_RESERVE",
    "private": "/private/path",
  }
  assert observer.record(result) is result
  value = module.read_dispatch_status(tmp_path, config, kind, now=110)
  assert value["decision"] == {"status": "QUEUED", "reason": "HOST_MEMORY_RESERVE"}
  observer.record({"status": "RUNNING", "run_id": "run-1"})
  assert (
    module.read_dispatch_status(tmp_path, config, kind, now=110)["decision"]["reason"]
    is None
  )
  assert module.read_dispatch_status(tmp_path, config, kind, now=191) == {
    "state": "STALE",
    "observed_at": 100,
  }
  assert module.read_dispatch_status(tmp_path, config, kind, now=99) == {
    "state": "UNKNOWN"
  }
  config.write_text("new target")
  assert module.read_dispatch_status(tmp_path, config, kind, now=110) == {
    "state": "UNKNOWN"
  }


def test_observation_failure_does_not_change_execution_result_or_leak(
  tmp_path, monkeypatch, caplog
):
  config = tmp_path / "config.toml"
  config.write_text("fixture")
  monkeypatch.setattr(
    runtime, "current_config", lambda: SimpleNamespace(state_root=tmp_path)
  )
  observer = module.DispatchObservation(config, "training")
  config.write_text("changed")
  result = {"status": "RUNNING", "run_id": "run-1"}
  assert observer.record(result) is result
  assert not (tmp_path / "observations").exists()
  assert "TRAINER_DISPATCH_OBSERVATION_UNAVAILABLE" in caplog.text
  assert str(tmp_path) not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["training", "preparation"])
async def test_actual_dispatch_records_resource_rejection_before_claim(
  tmp_path, monkeypatch, kind
):
  from quantx_trainer import preparation_flow, training_flow

  config = tmp_path / "config.toml"
  config.write_text("fixture")
  settings = SimpleNamespace(state_root=tmp_path)
  monkeypatch.setattr(runtime, "current_config", lambda: settings)

  @asynccontextmanager
  async def session(path):
    yield object()

  target = training_flow if kind == "training" else preparation_flow
  monkeypatch.setattr(target, "training_session", session)
  monkeypatch.setattr(target, "_host_admission_reason", lambda: "HOST_MEMORY_RESERVE")
  if kind == "training":
    monkeypatch.setattr(target, "get_run_logger", lambda: SimpleNamespace())
    monkeypatch.setattr(
      target, "recover_lost_training_runs", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
      target,
      "StockSelectionTrainingRepository",
      lambda db: SimpleNamespace(
        get_execution_capability=AsyncMock(return_value={"cpu_available": True})
      ),
    )
    result = await target.stock_selection_training_dispatch_flow.fn(
      config_path=str(config)
    )
  else:
    monkeypatch.setattr(target, "current_config", lambda: settings)
    monkeypatch.setattr(target, "ResearchPreparationRepository", lambda db: object())
    monkeypatch.setattr(target, "StockSelectionTrainingRepository", lambda db: object())
    monkeypatch.setattr(
      target, "recover_preparation_results", AsyncMock(return_value=[])
    )
    result = await target.trainer_preparation_flow.fn(config_path=str(config))
  value = module.read_dispatch_status(tmp_path, config, kind)
  assert value["state"] == "FRESH"
  assert value["decision"] == result
  assert (
    json.loads((tmp_path / f"observations/{kind}-dispatch.json").read_text())[
      "decision"
    ]
    == result
  )


def test_cross_dispatch_or_unrecognized_reason_is_unknown(tmp_path, monkeypatch):
  config = tmp_path / "config.toml"
  config.write_text("fixture")
  monkeypatch.setattr(
    runtime, "current_config", lambda: SimpleNamespace(state_root=tmp_path)
  )
  monkeypatch.setattr(module.time, "time", lambda: 100)
  module.DispatchObservation(config, "training").record(
    {"status": "QUEUED", "reason": "TRAINER_DRAINING"}
  )
  original = tmp_path / "observations/training-dispatch.json"
  (tmp_path / "observations/preparation-dispatch.json").write_bytes(
    original.read_bytes()
  )
  assert module.read_dispatch_status(tmp_path, config, "preparation", now=110) == {
    "state": "UNKNOWN"
  }
  value = json.loads(original.read_bytes())
  value["decision"]["reason"] = "password=secret /private/path"
  original.write_text(json.dumps(value))
  assert module.read_dispatch_status(tmp_path, config, "training", now=110) == {
    "state": "UNKNOWN"
  }


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "reason",
  ["RUNNING_TRAINING_EXISTS", "NO_CLAIMABLE_QUEUED_RUN", "CLAIM_INTEGRITY_CONFLICT"],
)
async def test_dispatch_reports_claim_transaction_outcome_without_starting_compute(
  tmp_path, monkeypatch, reason
):
  from quantx_infrastructure.repositories.stock_selection_training_repository import (
    TrainingClaimRejection,
  )
  from quantx_trainer import training_flow as training

  config = tmp_path / "config.toml"
  config.write_text("fixture")
  settings = SimpleNamespace(state_root=tmp_path)
  monkeypatch.setattr(runtime, "current_config", lambda: settings)
  monkeypatch.setattr(training, "current_config", lambda: settings)

  @asynccontextmanager
  async def session(path):
    yield object()

  repository = SimpleNamespace(
    get_execution_capability=AsyncMock(return_value={"cpu_available": True}),
    claim_next_queued=AsyncMock(return_value=TrainingClaimRejection(reason)),
  )
  execute = AsyncMock(side_effect=AssertionError("rejected claim cannot compute"))
  monkeypatch.setattr(training, "training_session", session)
  monkeypatch.setattr(
    training, "StockSelectionTrainingRepository", lambda db: repository
  )
  monkeypatch.setattr(training, "get_run_logger", lambda: SimpleNamespace())
  monkeypatch.setattr(
    training, "recover_lost_training_runs", AsyncMock(return_value=[])
  )
  monkeypatch.setattr(training, "_host_admission_reason", lambda: None)
  monkeypatch.setattr(training, "_run_claimed_job", execute)
  result = await training.stock_selection_training_dispatch_flow.fn(
    config_path=str(config)
  )
  assert result["reason"] == reason
  assert result["status"] == (
    "IDLE" if reason == "NO_CLAIMABLE_QUEUED_RUN" else "QUEUED"
  )
  execute.assert_not_awaited()
  value = module.read_dispatch_status(tmp_path, config, "training")
  assert value["state"] == "FRESH"
  assert value["decision"]["reason"] == reason


@pytest.mark.parametrize("reason", ["HOST_GUARD_LOCATION_UNAVAILABLE", "HOST_GUARD_LINK_FORBIDDEN"])
def test_guard_location_failures_remain_visible(tmp_path, monkeypatch, reason):
  config = tmp_path / "trainer.toml"
  config.write_text("fixture")
  monkeypatch.setattr(runtime, "current_config", lambda: SimpleNamespace(state_root=tmp_path))
  module.DispatchObservation(config, "preparation").record({"status": "QUEUED", "reason": reason})
  value = module.read_dispatch_status(tmp_path, config, "preparation")
  assert value["state"] == "FRESH"
  assert value["decision"]["reason"] == reason
