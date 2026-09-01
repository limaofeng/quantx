"""Point-in-time history evidence is added after the already-applied 0042."""

import importlib.util
from pathlib import Path


def test_valid_history_column_has_its_own_forward_revision(monkeypatch) -> None:
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260901_0044_indicator_valid_history_count.py"
  )
  spec = importlib.util.spec_from_file_location("indicator_history_migration", path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  additions = []
  monkeypatch.setattr(
    module.op,
    "add_column",
    lambda table, column: additions.append((table, column)),
  )

  module.upgrade()

  assert module.down_revision == "20260901_0043"
  assert len(additions) == 1
  table, column = additions[0]
  assert table == "indicator_snapshots"
  assert column.name == "valid_history_count"
  assert column.nullable and column.server_default is None
