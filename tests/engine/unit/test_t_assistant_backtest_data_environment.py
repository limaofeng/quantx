"""The data CLI must validate development isolation before constructing clients."""

import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture
def cli(monkeypatch, tmp_path):
  ops = Path(__file__).resolve().parents[3] / "ops"
  monkeypatch.syspath_prepend(str(ops))
  spec = importlib.util.spec_from_file_location(
    "backtest_data_cli", ops / "t-assistant-backtest-data.py"
  )
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  monkeypatch.setattr(module, "ROOT", tmp_path)
  monkeypatch.setattr(module.sys, "platform", "darwin")
  monkeypatch.setattr(module.os, "environ", os.environ.copy())
  directory = tmp_path / "apps" / "api"
  directory.mkdir(parents=True)
  (directory / ".env.development").write_text(
    "DATABASE_URL=postgresql+asyncpg://localhost/quantx_dev\n"
    "REDIS_URL=redis://localhost:6379/0\n"
    "INFLUXDB_HOST=http://localhost:8181\n"
    "INFLUXDB_DATABASE=quantx_dev\n"
    "PREFECT_API_URL=http://localhost:4200/api\n"
    "ENABLE_REAL_TRADING=true\n"
    "QMT_REAL_TRADING_ENABLED=true\n"
    "T_TRADE_LIVE_ENABLED=true\n"
  )
  return module


def test_development_overrides_ambient_testing_and_closes_live_gates(cli):
  cli.os.environ["ENV"] = "testing"
  cli.os.environ["DATABASE_URL"] = "postgresql+asyncpg://remote/quantx"
  cli.configure_environment("development")
  assert cli.os.environ["ENV"] == "development"
  assert cli.os.environ["DATABASE_URL"].endswith("localhost/quantx_dev")
  for key in ("ENABLE_REAL_TRADING", "QMT_REAL_TRADING_ENABLED", "T_TRADE_LIVE_ENABLED"):
    assert cli.os.environ[key] == "false"
  assert cli.os.environ["REAL_TRADING_ACCOUNT_ALLOWLIST"] == "[]"
  assert cli.os.environ["INFLUXDB_MAX_RETRIES"] == "0"


@pytest.mark.parametrize("environment", ["testing", "production"])
def test_macos_rejects_other_environments_before_loading_configuration(cli, environment):
  with pytest.raises(ValueError, match="BACKTEST_DATA_"):
    cli.configure_environment(environment)


@pytest.mark.parametrize(
  ("original", "replacement"),
  [("localhost:8181", "remote:8181"), ("quantx_dev", "quantx")],
)
def test_invalid_data_source_does_not_mutate_process_environment(cli, original, replacement):
  config = cli.ROOT / "apps" / "api" / ".env.development"
  config.write_text(config.read_text().replace(original, replacement))
  before = dict(cli.os.environ)
  with pytest.raises(ValueError, match="Development"):
    cli.configure_environment("development")
  assert dict(cli.os.environ) == before
