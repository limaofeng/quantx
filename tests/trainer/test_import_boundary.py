"""Fresh-process regression: root pytest imports must not hide global engines."""

import os
import subprocess
import sys
from pathlib import Path


def test_trainer_flow_imports_do_not_initialize_service_connections(tmp_path):
  root = Path(__file__).resolve().parents[2]
  paths = [str(root / item / "src") for item in (
    "apps/trainer", "apps/research", "packages/infrastructure",
    "packages/application", "packages/domain", "packages/contracts",
  )]
  script = """
import importlib.abc, sys
sys.path[:0] = PATHS
class ForbidServiceConnections(importlib.abc.MetaPathFinder):
  def find_spec(self, fullname, *args):
    if fullname in {
      'quantx_infrastructure.database.connection',
      'quantx_infrastructure.database.relational_connection',
      'quantx_infrastructure.database.redis',
    }:
      raise AssertionError('unexpected service connection import: ' + fullname)
sys.meta_path.insert(0, ForbidServiceConnections())
import sqlalchemy.ext.asyncio

def no_engine(*args, **kwargs):
  raise AssertionError('import constructed a database engine')
sqlalchemy.ext.asyncio.create_async_engine = no_engine
import quantx_trainer.preparation_flow
import quantx_trainer.training_flow
from quantx_infrastructure import database
from quantx_infrastructure.database.relational_base import Base, BaseRepository
assert database.Base is Base
assert database.BaseRepository is BaseRepository
try:
  database.unknown_export
except AttributeError:
  pass
else:
  raise AssertionError('unknown database attribute was accepted')
print('FLOW_IMPORTS_WITHOUT_GLOBAL_CONNECTIONS')
""".replace("PATHS", repr(paths))
  env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "DATABASE", "PREFECT", "QUANTX"))}
  env.update(ENV="testing", DATABASE_URL="", ENABLE_REAL_TRADING="false",
             QMT_REAL_TRADING_ENABLED="false", PREFECT_SERVER_ALLOW_EPHEMERAL_MODE="false")
  result = subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path,
                          env=env, capture_output=True, text=True, timeout=45)
  assert result.returncode == 0, result.stderr
  assert result.stdout.strip() == "FLOW_IMPORTS_WITHOUT_GLOBAL_CONNECTIONS"
