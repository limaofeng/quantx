"""New factor calculations never silently adopt old snapshots."""

import importlib.util
from pathlib import Path

from quantx_infrastructure.models.indicator_snapshot import IndicatorSnapshot


def test_additive_factor_version_migration_does_not_promote_legacy_rows(monkeypatch):
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260901_0042_daily_factor_version.py"
  )
  spec = importlib.util.spec_from_file_location("daily_factor_migration", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  additions = []
  indexes = []
  monkeypatch.setattr(
    module.op, "add_column", lambda table, column: additions.append((table, column))
  )
  monkeypatch.setattr(module.op, "create_index", lambda *args: indexes.append(args))
  module.upgrade()
  assert module.down_revision == "20260831_0041"
  assert all(table == "indicator_snapshots" for table, _ in additions)
  assert {column.name for _, column in additions} == {
    "calculation_version",
    "kdj_cross_up",
    "ma_cross_up",
    "boll_near_lower",
    "boll_near_upper",
  }
  assert all(
    column.nullable and column.server_default is None for _, column in additions
  )
  assert indexes == [
    (
      "ix_indicator_snapshots_version_date",
      "indicator_snapshots",
      ["calculation_version", "snapshot_date"],
    )
  ]
  assert IndicatorSnapshot.calculation_version.property.columns[0].default is None
  assert "ix_indicator_snapshots_version_date" in {
    index.name for index in IndicatorSnapshot.__table__.indexes
  }
