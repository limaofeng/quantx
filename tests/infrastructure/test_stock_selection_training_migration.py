from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa


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


class _Inspector:
  def __init__(self) -> None:
    self.tables: set[str] = set()
    self.columns: dict[str, list[dict]] = {}
    self.primary_keys: dict[str, dict] = {}
    self.unique_constraints: dict[str, list[dict]] = {}
    self.foreign_keys: dict[str, list[dict]] = {}
    self.check_constraints: dict[str, list[dict]] = {}
    self.indexes: dict[str, list[dict]] = {}
    self.comments: dict[str, dict] = {}

  def get_table_names(self):
    return sorted(self.tables)

  def get_columns(self, table_name):
    return self.columns[table_name]

  def get_pk_constraint(self, table_name):
    return self.primary_keys[table_name]

  def get_unique_constraints(self, table_name):
    return self.unique_constraints.get(table_name, [])

  def get_foreign_keys(self, table_name):
    return self.foreign_keys.get(table_name, [])

  def get_check_constraints(self, table_name):
    return self.check_constraints.get(table_name, [])

  def get_indexes(self, table_name):
    return self.indexes.get(table_name, [])

  def get_table_comment(self, table_name):
    return self.comments[table_name]


def _seed_exact(inspector: _Inspector, revision, *, naive_utc: bool = False) -> None:
  for table_name in sorted(revision._TARGET_TABLES):
    inspector.tables.add(table_name)
    columns = []
    for column_name, (type_, nullable, default) in revision._COLUMN_SPECS[
      table_name
    ].items():
      if naive_utc and (table_name, column_name) in revision._NAIVE_UTC_COLUMNS:
        type_ = sa.DateTime()
      columns.append(
        {
          "name": column_name,
          "type": type_,
          "nullable": nullable,
          "default": default,
        }
      )
    inspector.columns[table_name] = columns
    inspector.primary_keys[table_name] = {
      "name": None,
      "constrained_columns": list(revision._PRIMARY_KEYS[table_name]),
    }
    inspector.unique_constraints[table_name] = [
      {"name": f"uq_{table_name}_{column[0]}", "column_names": list(column)}
      for column in sorted(revision._UNIQUE_CONSTRAINTS[table_name])
    ]
    inspector.foreign_keys[table_name] = [
      {
        "name": None,
        "constrained_columns": list(constrained),
        "referred_table": referred_table,
        "referred_columns": list(referred),
        "options": {
          "ondelete": ondelete,
          **({"onupdate": onupdate} if onupdate is not None else {}),
        },
      }
      for constrained, referred_table, referred, ondelete, onupdate in sorted(
        revision._FOREIGN_KEYS[table_name]
      )
    ]
    inspector.check_constraints[table_name] = [
      {"name": name, "sqltext": expression}
      for name, expression in revision._CHECK_CONSTRAINTS[table_name].items()
    ]
    inspector.indexes[table_name] = []
    for name, (columns, unique, predicate) in revision._INDEXES[table_name].items():
      index = {
        "name": name,
        "column_names": list(columns),
        "unique": unique,
        "dialect_options": {},
      }
      if predicate is not None:
        index["dialect_options"]["postgresql_where"] = sa.text(predicate)
      inspector.indexes[table_name].append(index)
    inspector.comments[table_name] = {"text": revision._TABLE_COMMENTS[table_name]}


def test_training_migration_clean_create_keeps_the_original_ddl(monkeypatch) -> None:
  revision = _revision()
  inspector = _Inspector()
  monkeypatch.setattr(revision, "_get_inspector", lambda: inspector)
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
  assert indexes == [
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
    column_names = {
      column.name for column in columns if isinstance(column, sa.Column)
    }
    assert column_names == set(revision._COLUMN_SPECS[name])


def test_training_migration_adopts_exact_precreated_schema_without_ddl(monkeypatch) -> None:
  revision = _revision()
  inspector = _Inspector()
  _seed_exact(inspector, revision)
  monkeypatch.setattr(revision, "_get_inspector", lambda: inspector)
  monkeypatch.setattr(
    revision.op,
    "create_table",
    lambda *_args, **_kwargs: pytest.fail("exact schema must be adopted"),
  )
  monkeypatch.setattr(
    revision.op,
    "create_index",
    lambda *_args, **_kwargs: pytest.fail("exact schema must be adopted"),
  )
  monkeypatch.setattr(
    revision.op,
    "execute",
    lambda *_args, **_kwargs: pytest.fail("exact schema needs no conversion"),
  )

  revision.upgrade()


def test_training_migration_adopts_only_the_six_known_naive_utc_columns(
  monkeypatch,
) -> None:
  revision = _revision()
  inspector = _Inspector()
  _seed_exact(inspector, revision, naive_utc=True)
  monkeypatch.setattr(revision, "_get_inspector", lambda: inspector)
  converted: list[str] = []

  def execute(statement) -> None:
    sql = str(statement)
    assert "TYPE TIMESTAMP WITH TIME ZONE" in sql
    assert "AT TIME ZONE 'UTC'" in sql
    for table_name, column_name in revision._NAIVE_UTC_COLUMNS:
      if f"ALTER TABLE {table_name} ALTER COLUMN {column_name} " in sql:
        column = next(
          column
          for column in inspector.columns[table_name]
          if column["name"] == column_name
        )
        column["type"] = sa.DateTime(timezone=True)
        converted.append(f"{table_name}.{column_name}")
        return
    pytest.fail(f"unexpected conversion statement: {sql}")

  monkeypatch.setattr(revision.op, "execute", execute)
  monkeypatch.setattr(
    revision.op,
    "create_table",
    lambda *_args, **_kwargs: pytest.fail("precreated tables must be adopted"),
  )
  monkeypatch.setattr(
    revision.op,
    "create_index",
    lambda *_args, **_kwargs: pytest.fail("precreated indexes must be adopted"),
  )

  revision.upgrade()

  assert set(converted) == {
    f"{table_name}.{column_name}"
    for table_name, column_name in revision._NAIVE_UTC_COLUMNS
  }
  assert len(converted) == 6
  for table_name, column_name in revision._NAIVE_UTC_COLUMNS:
    column = next(
      value
      for value in inspector.columns[table_name]
      if value["name"] == column_name
    )
    assert column["nullable"] is revision._COLUMN_SPECS[table_name][column_name][1]
    assert column["type"].timezone is True


def test_training_migration_partial_precreated_schema_fails_closed(monkeypatch) -> None:
  revision = _revision()
  inspector = _Inspector()
  _seed_exact(inspector, revision)
  inspector.tables.remove("stock_selection_training_runs")
  monkeypatch.setattr(revision, "_get_inspector", lambda: inspector)
  operations: list[str] = []
  monkeypatch.setattr(
    revision.op,
    "create_table",
    lambda *_args, **_kwargs: operations.append("create_table"),
  )

  with pytest.raises(RuntimeError, match="partially precreated"):
    revision.upgrade()

  assert operations == []


@pytest.mark.parametrize(
  "mutate, message",
  [
    (
      lambda inspector, revision: next(
        column
        for column in inspector.columns["stock_selection_training_specs"]
        if column["name"] == "note"
      ).update(type=sa.String(length=32)),
      "type mismatch",
    ),
    (
      lambda inspector, revision: next(
        column
        for column in inspector.columns["stock_selection_training_runs"]
        if column["name"] == "run_key"
      ).update(nullable=False),
      "nullable mismatch",
    ),
    (
      lambda inspector, revision: next(
        column
        for column in inspector.columns["stock_selection_training_runs"]
        if column["name"] == "requested_at"
      ).update(default="CURRENT_TIMESTAMP - interval '1 day'"),
      "server default mismatch",
    ),
    (
      lambda inspector, revision: inspector.primary_keys[
        "stock_selection_dataset_versions"
      ].update(constrained_columns=["status"]),
      "primary key mismatch",
    ),
    (
      lambda inspector, revision: inspector.foreign_keys[
        "stock_selection_training_specs"
      ][0]["options"].update(ondelete="CASCADE"),
      "foreign keys mismatch",
    ),
    (
      lambda inspector, revision: inspector.check_constraints[
        "stock_selection_dataset_versions"
      ][0].update(sqltext="1 = 1"),
      "check constraint",
    ),
    (
      lambda inspector, revision: inspector.indexes[
        "stock_selection_training_specs"
      ][0].update(column_names=["run_kind"]),
      "index .*columns",
    ),
    (
      lambda inspector, revision: inspector.indexes[
        "stock_selection_training_runs"
      ][-1]["dialect_options"].update(
        postgresql_where=sa.text("status = 'FAILED'")
      ),
      "predicate mismatch",
    ),
    (
      lambda inspector, revision: inspector.comments[
        "stock_selection_training_runs"
      ].update(text="wrong comment"),
      "table comment mismatch",
    ),
  ],
)
def test_training_migration_mismatch_fails_closed(monkeypatch, mutate, message) -> None:
  revision = _revision()
  inspector = _Inspector()
  _seed_exact(inspector, revision)
  mutate(inspector, revision)
  monkeypatch.setattr(revision, "_get_inspector", lambda: inspector)
  operations: list[str] = []
  monkeypatch.setattr(
    revision.op,
    "create_table",
    lambda *_args, **_kwargs: operations.append("create_table"),
  )
  monkeypatch.setattr(
    revision.op,
    "create_index",
    lambda *_args, **_kwargs: operations.append("create_index"),
  )
  monkeypatch.setattr(
    revision.op,
    "execute",
    lambda *_args, **_kwargs: operations.append("execute"),
  )

  with pytest.raises(RuntimeError, match=message):
    revision.upgrade()

  assert operations == []


def test_training_migration_rejects_unapproved_timestamp_drift(monkeypatch) -> None:
  revision = _revision()
  inspector = _Inspector()
  _seed_exact(inspector, revision)
  next(
    column
    for column in inspector.columns["stock_selection_dataset_versions"]
    if column["name"] == "date_start"
  )["type"] = sa.DateTime()
  monkeypatch.setattr(revision, "_get_inspector", lambda: inspector)
  monkeypatch.setattr(revision.op, "execute", lambda *_args: pytest.fail("no DDL"))

  with pytest.raises(RuntimeError, match="type mismatch"):
    revision.upgrade()


def test_training_migration_downgrade_is_disabled() -> None:
  revision = _revision()
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()
