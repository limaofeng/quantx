import inspect
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import sqlalchemy.ext.asyncio as sqlalchemy
import yaml
from quantx_trainer import runtime
from quantx_trainer.config import TrainerConfigurationError


@pytest.mark.asyncio
async def test_failed_preflight_cannot_select_a_database_or_run_body(monkeypatch):
  config = SimpleNamespace(validate_runtime=Mock())
  monkeypatch.setattr(runtime.TrainerConfig, "load", lambda path: config)
  monkeypatch.setattr(
    runtime, "preflight", AsyncMock(side_effect=RuntimeError("rejected"))
  )
  create = Mock()
  monkeypatch.setattr(sqlalchemy, "create_async_engine", create)
  with pytest.raises(RuntimeError, match="rejected"):
    async with runtime.training_session("explicit.toml"):
      pytest.fail("unvalidated runtime was entered")
  create.assert_not_called()


@pytest.mark.asyncio
async def test_runtime_uses_explicit_database_and_resets_task_context(monkeypatch):
  config = SimpleNamespace(
    validate_runtime=Mock(),
    database_url="postgresql+asyncpg://trainer@dev:5432/quantx_dev",
  )
  engine = SimpleNamespace(dispose=AsyncMock())
  create = Mock(return_value=engine)
  session = object()

  @asynccontextmanager
  async def open_session():
    yield session

  monkeypatch.setenv("DATABASE_URL", "ambient-production-target")
  monkeypatch.setattr(runtime.TrainerConfig, "load", lambda path: config)
  monkeypatch.setattr(runtime, "preflight", AsyncMock())
  monkeypatch.setattr(sqlalchemy, "create_async_engine", create)
  monkeypatch.setattr(
    sqlalchemy, "async_sessionmaker", lambda *args, **kwargs: open_session
  )
  async with runtime.training_session("explicit.toml") as actual:
    assert actual is session
    assert runtime.current_config() is config
    assert os.environ["DATABASE_URL"] == "ambient-production-target"
  assert create.call_args.args == (config.database_url,)
  engine.dispose.assert_awaited_once()
  with pytest.raises(TrainerConfigurationError):
    runtime.current_config()


def test_training_deployments_have_only_the_isolated_pool_and_required_configuration():
  from quantx_trainer import training_flow

  root = Path(__file__).resolve().parents[2]
  trainer = yaml.safe_load((root / "apps/trainer/prefect.yaml").read_text())[
    "deployments"
  ]
  worker = yaml.safe_load((root / "apps/worker/prefect.yaml").read_text())[
    "deployments"
  ]
  names = {entry["name"] for entry in trainer}
  assert len(names) == 2 and not names.intersection(entry["name"] for entry in worker)
  for entry in trainer:
    assert entry["work_pool"]["name"] == "quantx-train-pool"
    module, function = entry["entrypoint"].split(":")
    assert (root / module).is_file()
    parameter = inspect.signature(getattr(training_flow, function).fn).parameters[
      "config_path"
    ]
    assert parameter.default is inspect.Parameter.empty
