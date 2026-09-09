import json
from pathlib import Path

import pytest
from quantx_trainer.config import TrainerConfig, TrainerConfigurationError


@pytest.fixture
def deployment(tmp_path):
  return {
    "environment": "development",
    "code_root": str(tmp_path / "training-code"),
    "production_root": str(tmp_path / "production"),
    "state_root": str(tmp_path / "training-state"),
    "database_url": "postgresql+asyncpg://trainer:private@dev-host:5432/quantx_dev",
    "prefect_api_url": "http://dev-host:4200/api",
    "prefect_pool": "quantx-train-pool",
  }


def load(tmp_path, values):
  filename = tmp_path / "trainer.toml"
  filename.write_text(
    "\n".join(f"{key} = {json.dumps(value)}" for key, value in values.items()),
    encoding="utf-8",
  )
  return TrainerConfig.load(filename)


def test_explicit_config_ignores_ambient_production(tmp_path, deployment, monkeypatch):
  monkeypatch.setenv("ENV", "production")
  monkeypatch.setenv("DATABASE_URL", "postgresql://production")
  config = load(tmp_path, deployment)
  assert config.environment == "development"
  assert "private" not in repr(config)
  assert config.database_url == deployment["database_url"]


@pytest.mark.parametrize("missing", list(TrainerConfig.__dataclass_fields__))
def test_no_missing_field_defaults(tmp_path, deployment, missing):
  del deployment[missing]
  with pytest.raises(TrainerConfigurationError):
    load(tmp_path, deployment)


@pytest.mark.parametrize(
  ("key", "value"),
  [
    ("environment", "production"),
    ("environment", "dev"),
    ("prefect_pool", "quantx-pool"),
    ("prefect_pool", "quantx-dev-pool"),
    ("database_url", "postgresql+asyncpg://u:private@host:5432/quantx"),
    ("database_url", "postgresql+asyncpg://u:private@host:5432/quantx_dev?host=prod"),
    ("database_url", "postgresql+asyncpg://u:private@host:bad/quantx_dev"),
    ("database_url", "sqlite:///quantx_dev"),
    ("prefect_api_url", "http://192.168.5.6:30420/api"),
    ("prefect_api_url", "http://u:private@host:4200/api"),
    ("prefect_api_url", "http://host:4200/api?target=production"),
    ("prefect_api_url", "http://[private:4200/api"),
    ("code_root", "relative"),
    ("state_root", ""),
    ("typo", "private"),
  ],
)
def test_invalid_config_rejected_without_secrets(tmp_path, deployment, key, value):
  deployment[key] = value
  with pytest.raises(TrainerConfigurationError) as caught:
    load(tmp_path, deployment)
  assert "private" not in str(caught.value)


@pytest.mark.parametrize("field", ["code_root", "state_root"])
@pytest.mark.parametrize("relationship", ["same", "child", "parent", "symlink"])
def test_production_directory_overlap_rejected(
  tmp_path, deployment, field, relationship
):
  production = Path(deployment["production_root"])
  production.mkdir()
  candidate = {
    "same": production,
    "child": production / "nested",
    "parent": production.parent,
    "symlink": tmp_path / "alias",
  }[relationship]
  if relationship == "symlink":
    candidate.symlink_to(production, target_is_directory=True)
  deployment[field] = str(candidate)
  with pytest.raises(TrainerConfigurationError, match="separate from production"):
    load(tmp_path, deployment)


def test_mutable_state_cannot_contain_checkout(tmp_path, deployment):
  deployment["state_root"] = deployment["code_root"]
  with pytest.raises(TrainerConfigurationError, match="state and code"):
    load(tmp_path, deployment)


def test_runtime_uses_actual_conda_prefix_and_checkout(tmp_path, deployment):
  config = load(tmp_path, deployment)
  prefix = tmp_path / "envs" / "quantx-train"
  (prefix / "conda-meta").mkdir(parents=True)
  config.validate_runtime(prefix=prefix, code_root=config.code_root)
  with pytest.raises(TrainerConfigurationError, match="running checkout"):
    config.validate_runtime(prefix=prefix, code_root=config.production_root)
  for invalid in [tmp_path / "quantx", tmp_path / "other" / "quantx-train"]:
    with pytest.raises(TrainerConfigurationError, match="Conda interpreter"):
      config.validate_runtime(prefix=invalid, code_root=config.code_root)


def test_child_does_not_inherit_broker_or_production_environment(tmp_path, deployment):
  config = load(tmp_path, deployment)
  env = config.child_environment(
    {
      "SystemRoot": "C:\\Windows",
      "ENV": "production",
      "DATABASE_URL": "production",
      "QUANTX_ENV_FILE": "production.env",
      "PYTHONPATH": "production/src",
      "QMT_ACCOUNT_ID": "private",
      "QMT_DEVICE_KEY": "private",
      "ENABLE_REAL_TRADING": "true",
      "QMT_REAL_TRADING_ENABLED": "true",
    }
  )
  assert env["SystemRoot"] == "C:\\Windows"
  assert env["ENV"] == "development"
  assert env["DATABASE_URL"] == deployment["database_url"]
  assert env["ENABLE_REAL_TRADING"] == env["QMT_REAL_TRADING_ENABLED"] == "false"
  assert (
    not {"QUANTX_ENV_FILE", "PYTHONPATH", "QMT_ACCOUNT_ID", "QMT_DEVICE_KEY"}
    & env.keys()
  )


def test_unreadable_and_malformed_config_are_redacted(tmp_path):
  filename = tmp_path / "private.toml"
  for contents in (None, 'database_url = "private'):
    if contents is not None:
      filename.write_text(contents)
    with pytest.raises(TrainerConfigurationError) as caught:
      TrainerConfig.load(filename)
    assert "private" not in str(caught.value)
