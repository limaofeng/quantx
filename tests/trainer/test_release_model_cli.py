import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
  "release_model_cli", Path(__file__).resolve().parents[2] / "ops/trainer/release_model.py",
)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def config(tmp_path, *, environment="development", host="127.0.0.1", database="quantx_dev", **extra):
  values = {
    "environment": environment,
    "database_url": f"postgresql+asyncpg://release_user@{host}/{database}",
    "artifact_root": str(tmp_path / "models"),
    **extra,
  }
  path = tmp_path / "release.toml"
  path.write_text("\n".join(f"{key} = {json.dumps(value)}" for key, value in values.items()))
  return path


@pytest.mark.parametrize("command,environment,database,platform", [
  ("export", "development", "quantx_dev", "darwin"),
  ("import", "testing", "quantx_test", "darwin"),
  ("import", "production", "quantx", "win32"),
])
def test_explicit_environment_config(tmp_path, command, environment, database, platform):
  path = config(tmp_path, environment=environment, database=database)
  assert cli.load_config(path, command, platform=platform)["environment"] == environment


@pytest.mark.parametrize("command,values,platform,error", [
  ("export", {"environment": "production", "database": "quantx"}, "win32", "REQUIRES_DEVELOPMENT"),
  ("import", {}, "darwin", "IMPORT_TARGET_INVALID"),
  ("export", {"database": "quantx"}, "darwin", "ENVIRONMENT_MISMATCH"),
  ("import", {"environment": "testing", "database": "quantx_dev"}, "win32", "ENVIRONMENT_MISMATCH"),
  ("import", {"environment": "production", "database": "quantx"}, "darwin", "PRODUCTION_TARGET_INVALID"),
  ("import", {"environment": "production"}, "win32", "PRODUCTION_TARGET_INVALID"),
  ("export", {"host": "192.168.5.6"}, "darwin", "REQUIRES_LOCAL_DATABASE"),
  ("export", {"artifact_root": "relative"}, "darwin", "MUST_BE_ABSOLUTE"),
  ("export", {"database_url": "postgresql+asyncpg://localhost/quantx_dev?sslmode=require"}, "darwin", "DATABASE_INVALID"),
  ("export", {"unexpected": "field"}, "darwin", "FIELDS_INVALID"),
])
def test_wrong_environment_is_rejected_before_connection(tmp_path, command, values, platform, error):
  with pytest.raises(ValueError, match=error):
    cli.load_config(config(tmp_path, **values), command, platform=platform)
