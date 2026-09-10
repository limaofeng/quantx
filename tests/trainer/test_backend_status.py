import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_trainer.backend_status import read_backend_status, write_backend_status


def test_backend_snapshot_is_bounded_fresh_and_config_bound(tmp_path):
  config = tmp_path / "config.toml"
  config.write_text("private connection string")
  write_backend_status(
    tmp_path,
    config,
    {
      "status": "GPU_AVAILABLE",
      "cpu_available": True,
      "environment_requirement_hash": "a" * 64,
      "password": "secret",
    },
    activity={"tasks": [], "truncated": False},
    observed_at=100,
    expected_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
  )
  assert "secret" not in (tmp_path / "observations/backend.json").read_text()
  value = read_backend_status(tmp_path, config, now=190)
  assert value["state"] == "FRESH"
  assert value["capability"]["gpu_status"] == "GPU_AVAILABLE"
  assert read_backend_status(tmp_path, config, now=191) == {
    "state": "STALE",
    "observed_at": 100,
  }
  assert read_backend_status(tmp_path, config, now=99) == {"state": "UNKNOWN"}
  config.write_text("different target")
  assert read_backend_status(tmp_path, config, now=110) == {"state": "UNKNOWN"}


@pytest.mark.parametrize("fault", ["missing", "json", "oversize", "status", "link"])
def test_backend_snapshot_never_exposes_uncertain_evidence(tmp_path, fault):
  config = tmp_path / "config.toml"
  config.write_text("fixture")
  if fault == "missing":
    assert read_backend_status(tmp_path, config) == {"state": "UNKNOWN"}
    assert not (tmp_path / "observations").exists()
    return
  write_backend_status(
    tmp_path,
    config,
    {"status": "CPU_AVAILABLE", "cpu_available": True},
    activity={"tasks": [], "truncated": False},
    observed_at=100,
    expected_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
  )
  path = tmp_path / "observations/backend.json"
  if fault == "json":
    path.write_text("private invalid content")
  elif fault == "oversize":
    path.write_text("x" * 65537)
  elif fault == "status":
    value = json.loads(path.read_text())
    value["capability"]["status"] = "/private/path"
    path.write_text(json.dumps(value))
  else:
    path.unlink()
    try:
      path.symlink_to(config)
    except OSError:
      pytest.skip("symlink creation unavailable")
  assert read_backend_status(tmp_path, config, now=110) == {"state": "UNKNOWN"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_commit", [False, True])
async def test_capability_snapshot_only_follows_successful_database_write(
  tmp_path, monkeypatch, failed_commit
):
  from quantx_trainer import public_status
  monkeypatch.setattr(public_status, "publish_runtime_status", AsyncMock())
  from quantx_infrastructure import training_activity
  from quantx_trainer import training_flow as module

  monkeypatch.setattr(module, "_host_admission_reason", lambda: None)
  monkeypatch.setattr(
    training_activity,
    "read_training_activity",
    AsyncMock(return_value={"tasks": [], "truncated": False}),
  )

  config = tmp_path / "config.toml"
  config.write_text("fixture")

  @asynccontextmanager
  async def session(path):
    yield object()

  write = AsyncMock(
    side_effect=RuntimeError("database unavailable") if failed_commit else None
  )
  monkeypatch.setattr(module, "training_session", session)
  monkeypatch.setattr(
    module, "current_config", lambda: SimpleNamespace(state_root=tmp_path)
  )
  monkeypatch.setattr(
    module,
    "StockSelectionTrainingRepository",
    lambda db: SimpleNamespace(upsert_capability_heartbeat=write),
  )
  monkeypatch.setattr(
    module,
    "_probe_capability",
    lambda: {"status": "CPU_AVAILABLE", "cpu_available": True},
  )
  if failed_commit:
    with pytest.raises(RuntimeError, match="database unavailable"):
      await module.stock_selection_training_capability_flow.fn(str(config))
    assert not (tmp_path / "observations").exists()
  else:
    await module.stock_selection_training_capability_flow.fn(str(config))
    value = read_backend_status(tmp_path, config)
    assert value["state"] == "FRESH"
    assert value["capability"]["cpu_available"] is True
    assert value["capability"]["gpu_status"] is None
  write.assert_awaited_once()


def test_config_change_cannot_relabel_prior_observation(tmp_path):
  config = tmp_path / "config.toml"
  config.write_text("old target")
  digest = hashlib.sha256(config.read_bytes()).hexdigest()
  config.write_text("new target")
  with pytest.raises(ValueError, match="TRAINER_BACKEND_CONFIG_CHANGED"):
    write_backend_status(
      tmp_path,
      config,
      {"status": "CPU_AVAILABLE", "cpu_available": True},
      activity={"tasks": [], "truncated": False},
      observed_at=100,
      expected_config_sha256=digest,
    )
  assert not (tmp_path / "observations").exists()


def test_activity_free_text_is_redacted_and_progress_not_coerced(tmp_path):
  config = tmp_path / "config.toml"
  config.write_text("fixture")
  row = {
    "type": "PREPARATION",
    "id": "prepare-1",
    "kind": "GPU",
    "status": "RUNNING",
    "phase": "检查 /private/file password=secret",
    "completed_units": None,
    "total_units": None,
  }
  options = {
    "observed_at": 100,
    "expected_config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
  }
  write_backend_status(
    tmp_path,
    config,
    {"status": "CPU_AVAILABLE", "cpu_available": True},
    {"tasks": [row], "truncated": False},
    **options,
  )
  value = read_backend_status(tmp_path, config, now=110)
  assert value["state"] == "FRESH"
  assert "private" not in json.dumps(value) and "secret" not in json.dumps(value)
  row.update(type="TRAINING", kind="DEVELOPMENT", completed_units=True, total_units=10)
  with pytest.raises(ValueError, match="TRAINER_ACTIVITY_EVIDENCE_INVALID"):
    write_backend_status(
      tmp_path,
      config,
      {"status": "CPU_AVAILABLE", "cpu_available": True},
      {"tasks": [row], "truncated": False},
      **options,
    )


@pytest.mark.asyncio
async def test_protected_window_skips_probe_without_fabricating_capability(tmp_path, monkeypatch):
  from quantx_trainer import public_status
  monkeypatch.setattr(public_status, "publish_runtime_status", AsyncMock())
  from unittest.mock import Mock
  from quantx_trainer import training_flow as module

  config = tmp_path / "trainer.toml"
  config.write_text("fixture")

  @asynccontextmanager
  async def session(path):
    yield object()

  probe = Mock(side_effect=AssertionError("protected window must not probe GPU"))
  repository = Mock(side_effect=AssertionError("must not write capability"))
  monkeypatch.setattr(module, "training_session", session)
  monkeypatch.setattr(module, "_host_admission_reason", lambda: "TRADING_OR_POST_CLOSE_CRITICAL_WINDOW")
  monkeypatch.setattr(module, "_probe_capability", probe)
  monkeypatch.setattr(module, "StockSelectionTrainingRepository", repository)
  result = await module.stock_selection_training_capability_flow.fn(str(config))
  assert result == {"status": "BLOCKED", "reason": "TRADING_OR_POST_CLOSE_CRITICAL_WINDOW"}
  probe.assert_not_called()
  repository.assert_not_called()
