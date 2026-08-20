from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260820_0023_limit_up_board_replay.py"
)


def _load_revision():
  spec = importlib.util.spec_from_file_location(
    "quantx_test_limit_up_board_replay_revision",
    REVISION_PATH,
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class FakeInspector:
  def __init__(self, revision, tables=None) -> None:
    self.revision = revision
    self.tables = set(revision._TARGET_TABLES if tables is None else tables)
    self.columns = {
      table: {
        name: {
          "name": name,
          "type": expected_type(),
          "nullable": nullable,
        }
        for name, (expected_type, nullable) in definitions.items()
      }
      for table, definitions in revision._EXPECTED_COLUMNS.items()
    }
    self.foreign_keys = {
      table: [
        {
          "constrained_columns": list(columns),
          "referred_table": referred_table,
          "referred_columns": list(referred_columns),
          "options": {"ondelete": ondelete},
        }
        for columns, referred_table, referred_columns, ondelete in definitions
      ]
      for table, definitions in revision._EXPECTED_FOREIGN_KEYS.items()
    }

  def get_table_names(self):
    return list(self.tables)

  def get_columns(self, table_name):
    return list(self.columns[table_name].values())

  def get_pk_constraint(self, _table_name):
    return {"constrained_columns": ["id"]}

  def get_indexes(self, table_name):
    return [
      {"name": name, "column_names": list(columns)}
      for name, columns in self.revision._EXPECTED_INDEXES[table_name].items()
    ]

  def get_unique_constraints(self, table_name):
    return [
      {"name": f"uq_{index}", "column_names": list(columns)}
      for index, columns in enumerate(
        self.revision._EXPECTED_UNIQUES[table_name]
      )
    ]

  def get_check_constraints(self, table_name):
    return [
      {"name": name, "sqltext": "1 = 1"}
      for name in self.revision._EXPECTED_CHECKS[table_name]
    ]

  def get_foreign_keys(self, table_name):
    return self.foreign_keys[table_name]


def test_revision_rejects_partial_or_invalid_preexisting_schema() -> None:
  revision = _load_revision()
  assert revision.down_revision == "20260818_0022"
  assert revision._validate_or_reject_existing_schema(
    FakeInspector(revision, tables=set())
  ) is False

  with pytest.raises(RuntimeError, match="partial"):
    revision._validate_or_reject_existing_schema(
      FakeInspector(
        revision,
        tables={"limit_up_board_replay_jobs"},
      )
    )

  invalid = FakeInspector(revision)
  del invalid.columns["limit_up_board_replay_jobs"]["dataset_fingerprint"]
  with pytest.raises(RuntimeError, match="columns"):
    revision._validate_or_reject_existing_schema(invalid)


def test_revision_accepts_only_the_complete_authoritative_schema() -> None:
  revision = _load_revision()
  inspector = FakeInspector(revision)

  assert revision._validate_or_reject_existing_schema(inspector) is True

  inspector.foreign_keys["limit_up_board_replay_scenarios"][0][
    "referred_table"
  ] = "wrong_jobs"
  with pytest.raises(RuntimeError, match="foreign keys"):
    revision._validate_or_reject_existing_schema(inspector)
