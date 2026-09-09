import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_existing_run_does_not_get_a_fabricated_heartbeat():
  path = Path(__file__).resolve().parents[2] / "packages/infrastructure/alembic/versions/20260909_0069_training_execution_heartbeat.py"
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
      "SELECT run_id, execution_heartbeat_at FROM stock_selection_training_runs"
    )).one() == ("old-run", None)
    migration.downgrade()
    assert connection.execute(sa.text("SELECT * FROM stock_selection_training_runs")).one() == ("old-run",)
  engine.dispose()
