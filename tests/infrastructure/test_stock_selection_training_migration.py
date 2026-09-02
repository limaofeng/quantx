from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _revision():
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260902_0045_stock_selection_training.py"
  )
  spec = importlib.util.spec_from_file_location("stock_selection_training_migration", path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_training_migration_chain_tables_constraints_and_no_downgrade(monkeypatch) -> None:
  revision = _revision()
  tables: list[tuple[str, tuple, dict]] = []
  indexes: list[str] = []
  monkeypatch.setattr(
    revision.op,
    "create_table",
    lambda name, *columns, **kwargs: tables.append((name, columns, kwargs)),
  )
  monkeypatch.setattr(
    revision.op,
    "create_index",
    lambda name, *_args, **_kwargs: indexes.append(name),
  )

  revision.upgrade()

  assert revision.revision == "20260902_0045"
  assert revision.down_revision == "20260901_0044"
  assert [name for name, _columns, _kwargs in tables] == [
    "stock_selection_dataset_versions",
    "stock_selection_training_specs",
    "stock_selection_training_runs",
  ]
  assert [name for name in indexes] == [
    "ix_stock_selection_dataset_versions_status",
    "ix_stock_selection_dataset_status_created",
    "ix_stock_selection_training_specs_dataset_version",
    "ix_stock_selection_training_specs_dataset_kind",
    "ix_stock_selection_training_specs_experiment_group",
    "ix_stock_selection_training_runs_spec_id",
    "ix_stock_selection_training_runs_status",
    "ix_stock_selection_training_runs_status_requested",
    "ix_stock_selection_training_runs_parent",
    "uq_stock_selection_training_runs_one_running",
  ]
  for name, columns, kwargs in tables:
    assert kwargs["comment"]
    column_names = {column.name for column in columns if hasattr(column, "name")}
    if name == "stock_selection_dataset_versions":
      assert {"dataset_version", "manifest_sha256", "quality_summary"} <= column_names
    elif name == "stock_selection_training_specs":
      assert {
        "spec_id",
        "universe_spec",
        "coordinate_hash",
        "frozen_test_access_count",
      } <= column_names
    else:
      assert {
        "run_id",
        "status",
        "state_version",
        "idempotency_key",
        "cancel_idempotency_key",
      } <= column_names
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()
