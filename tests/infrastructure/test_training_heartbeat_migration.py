import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.mark.parametrize("filename,column", [
  ("20260909_0069_training_execution_heartbeat.py", "execution_heartbeat_at"),
  ("20260910_0070_training_artifact_bundle.py", "artifact_bundle"),
])
def test_existing_run_does_not_get_fabricated_execution_evidence(filename, column):
  path = Path(__file__).resolve().parents[2] / "packages/infrastructure/alembic/versions" / filename
  spec = importlib.util.spec_from_file_location("training_heartbeat_migration", path)
  migration = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(migration)
  engine = sa.create_engine("sqlite:///:memory:")
  with engine.begin() as connection:
    connection.execute(sa.text("CREATE TABLE stock_selection_training_runs (run_id TEXT PRIMARY KEY)"))
    connection.execute(sa.text("INSERT INTO stock_selection_training_runs VALUES ('old-run')"))
    migration.op = Operations(MigrationContext.configure(connection))
    migration.upgrade()
    assert connection.execute(sa.text(
      f"SELECT run_id, {column} FROM stock_selection_training_runs"
    )).one() == ("old-run", None)
    migration.downgrade()
    assert connection.execute(sa.text("SELECT * FROM stock_selection_training_runs")).one() == ("old-run",)
  engine.dispose()
