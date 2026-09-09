from pathlib import Path
from runpy import run_path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.mark.parametrize("existing", ["absent", "exact", "different"])
def test_history_settings_adopts_only_exact_schema(monkeypatch, existing):
  path = (
    Path(__file__).parents[2]
    / "packages/infrastructure/alembic/versions/20260909_0063_history_download_settings.py"
  )
  migration = run_path(str(path))
  engine = sa.create_engine("sqlite://")
  with engine.begin() as connection:
    operation = Operations(MigrationContext.configure(connection))
    globals_ = migration["upgrade"].__globals__
    monkeypatch.setitem(globals_, "op", operation)
    monkeypatch.setitem(
      globals_, "context", SimpleNamespace(is_offline_mode=lambda: False)
    )
    if existing != "absent":
      migration["upgrade"]()
      connection.execute(
        sa.text(
          "INSERT INTO history_download_settings VALUES ('global',1,'{}','user','2026-09-09','2026-09-09')"
        )
      )
    if existing == "different":
      connection.execute(
        sa.text("ALTER TABLE history_download_settings ADD COLUMN unexpected INTEGER")
      )
      with pytest.raises(RuntimeError, match="EXISTING_SCHEMA_CONFLICT"):
        migration["upgrade"]()
    else:
      migration["upgrade"]()
      assert connection.scalar(
        sa.text("SELECT count(*) FROM history_download_settings")
      ) == (1 if existing == "exact" else 0)
  engine.dispose()
