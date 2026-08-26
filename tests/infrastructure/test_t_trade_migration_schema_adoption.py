from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "packages" / "infrastructure" / "alembic" / "versions"


class _Inspector:
  def __init__(self) -> None:
    self.tables: set[str] = set()
    self.columns: dict[str, list[dict]] = {}
    self.primary_keys: dict[str, dict] = {}
    self.unique_constraints: dict[str, list[dict]] = {}
    self.check_constraints: dict[str, list[dict]] = {}
    self.indexes: dict[str, list[dict]] = {}
    self.foreign_keys: dict[str, list[dict]] = {}

  def get_table_names(self):
    return sorted(self.tables)

  def get_columns(self, table_name):
    return self.columns[table_name]

  def get_pk_constraint(self, table_name):
    return self.primary_keys[table_name]

  def get_unique_constraints(self, table_name):
    return self.unique_constraints.get(table_name, [])

  def get_check_constraints(self, table_name):
    return self.check_constraints.get(table_name, [])

  def get_indexes(self, table_name):
    return self.indexes.get(table_name, [])

  def get_foreign_keys(self, table_name):
    return self.foreign_keys.get(table_name, [])


def _load(name: str):
  path = VERSIONS / name
  spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _seed_columns(inspector: _Inspector, revision, table_name: str, expected) -> None:
  inspector.tables.add(table_name)
  inspector.columns[table_name] = [
    {"name": name, "type": type_, "nullable": nullable}
    for name, (type_, nullable) in expected.items()
  ]


def _seed_0029_table(inspector: _Inspector, revision, table_name: str) -> None:
  _seed_columns(inspector, revision, table_name, revision._expected_columns(table_name))
  inspector.primary_keys[table_name] = {"constrained_columns": ["id"]}
  inspector.unique_constraints[table_name] = [
    {"name": name, "column_names": list(columns)}
    for name, columns in revision._EXPECTED_UNIQUE_CONSTRAINTS[table_name].items()
  ]
  inspector.check_constraints[table_name] = [
    {
      "name": name,
      "sqltext": " ".join(revision._EXPECTED_CHECK_FRAGMENTS[table_name][name]),
    }
    for name in revision._EXPECTED_CHECK_CONSTRAINTS[table_name]
  ]
  inspector.indexes[table_name] = [
    {"name": name, "column_names": list(columns), "unique": False}
    for name, columns in revision._EXPECTED_INDEXES[table_name].items()
  ]


def _seed_0030_candidate(inspector: _Inspector, revision) -> None:
  _seed_columns(
    inspector, revision, revision._CANDIDATE_TABLE, revision._candidate_columns()
  )
  inspector.primary_keys[revision._CANDIDATE_TABLE] = {"constrained_columns": ["id"]}
  inspector.unique_constraints[revision._CANDIDATE_TABLE] = [
    {
      "name": name,
      "column_names": list(columns),
    }
    for name, columns in revision._EXPECTED_UNIQUE.items()
  ]
  inspector.check_constraints[revision._CANDIDATE_TABLE] = [
    {
      "name": name,
      "sqltext": " ".join(revision._EXPECTED_CHECK_FRAGMENTS[name]),
    }
    for name in revision._EXPECTED_CHECKS
  ]
  inspector.indexes[revision._CANDIDATE_TABLE] = [
    {"name": name, "column_names": list(columns), "unique": False}
    for name, columns in revision._EXPECTED_INDEXES.items()
  ]


def _seed_runtime(inspector: _Inspector, revision, *, event_id_length: int) -> None:
  inspector.tables.update(
    {"account_trading_rollout_events", revision._RUNTIME_EVENTS_TABLE}
  )
  inspector.columns["account_trading_rollout_events"] = [
    {
      "name": "event_id",
      "type": sa.String(length=event_id_length),
      "nullable": False,
    }
  ]
  inspector.indexes[revision._RUNTIME_EVENTS_TABLE] = []


def _seed_watchlist(inspector: _Inspector, revision, *, legacy: bool = True) -> None:
  inspector.tables.add(revision._ITEM_TABLE)
  inspector.columns[revision._ITEM_TABLE] = [
    {"name": "id", "type": sa.String(length=32), "nullable": False}
  ]
  if legacy:
    inspector.columns[revision._ITEM_TABLE].append(
      {"name": "group_name", "type": sa.String(length=80), "nullable": True}
    )


def _seed_0031_groups(inspector: _Inspector, revision) -> None:
  _seed_columns(
    inspector,
    revision,
    revision._GROUP_TABLE,
    revision._expected_columns(revision._GROUP_TABLE),
  )
  inspector.primary_keys[revision._GROUP_TABLE] = {"constrained_columns": ["id"]}
  inspector.indexes[revision._GROUP_TABLE] = [
    {
      "name": "ix_watchlist_group_account_order",
      "column_names": ["account_id", "display_order"],
      "unique": False,
    },
    {
      "name": "uq_watchlist_group_account_name_ci",
      "column_names": ["account_id", None],
      "expressions": ["account_id", "lower(name::text)"],
      "unique": True,
    },
  ]


def _seed_0031_memberships(inspector: _Inspector, revision) -> None:
  _seed_columns(
    inspector,
    revision,
    revision._MEMBERSHIP_TABLE,
    revision._expected_columns(revision._MEMBERSHIP_TABLE),
  )
  inspector.primary_keys[revision._MEMBERSHIP_TABLE] = {
    "constrained_columns": ["group_id", "watchlist_item_id"]
  }
  inspector.foreign_keys[revision._MEMBERSHIP_TABLE] = [
    {
      "constrained_columns": ["group_id"],
      "referred_table": revision._GROUP_TABLE,
      "referred_columns": ["id"],
      "options": {"ondelete": "CASCADE"},
    },
    {
      "constrained_columns": ["watchlist_item_id"],
      "referred_table": revision._ITEM_TABLE,
      "referred_columns": ["id"],
      "options": {"ondelete": "CASCADE"},
    },
  ]
  inspector.indexes[revision._MEMBERSHIP_TABLE] = [
    {
      "name": "ix_watchlist_group_membership_group_order",
      "column_names": ["group_id", "display_order"],
      "unique": False,
    },
    {
      "name": "ix_watchlist_group_membership_item",
      "column_names": ["watchlist_item_id"],
      "unique": False,
    },
  ]


def test_0029_adopts_complete_precreated_schema_without_create_ddl(monkeypatch):
  revision = _load("20260823_0029_t_trade_opportunity_intelligence.py")
  inspector = _Inspector()
  for table_name in sorted(revision._TABLES):
    _seed_0029_table(inspector, revision, table_name)
  monkeypatch.setattr(revision, "_get_inspector", lambda: inspector)
  monkeypatch.setattr(
    revision.op,
    "create_table",
    lambda *_args, **_kwargs: pytest.fail("complete schema must be adopted"),
  )
  monkeypatch.setattr(
    revision.op,
    "create_index",
    lambda *_args, **_kwargs: pytest.fail("complete schema must be adopted"),
  )

  revision.upgrade()


@pytest.mark.parametrize(
  "mutate",
  [
    lambda inspector, revision: inspector.tables.remove("t_trade_instrument_profiles"),
    lambda inspector, revision: inspector.indexes[
      "t_trade_opportunity_evaluations"
    ].pop(),
    lambda inspector, revision: inspector.columns["t_trade_instrument_profiles"][
      0
    ].update(type=sa.String(length=35)),
    lambda inspector, revision: inspector.check_constraints[
      "t_trade_opportunity_evaluations"
    ][0].update(sqltext="1 = 1"),
  ],
)
def test_0029_partial_or_mismatched_schema_fails_closed(mutate, monkeypatch):
  revision = _load("20260823_0029_t_trade_opportunity_intelligence.py")
  inspector = _Inspector()
  for table_name in sorted(revision._TABLES):
    _seed_0029_table(inspector, revision, table_name)
  mutate(inspector, revision)
  monkeypatch.setattr(revision, "_get_inspector", lambda: inspector)

  with pytest.raises(RuntimeError, match="schema"):
    revision.upgrade()


def test_0030_adopts_precreated_tables_and_existing_runtime_index(monkeypatch):
  revision = _load("20260823_0030_t_trade_candidate_outcomes.py")
  inspector = _Inspector()
  _seed_runtime(inspector, revision, event_id_length=128)
  _seed_0030_candidate(inspector, revision)
  inspector.indexes[revision._RUNTIME_EVENTS_TABLE] = [
    {
      "name": revision._RUNTIME_REPAIR_INDEX,
      "column_names": ["strategy_run_id", "created_at", "event_id"],
      "unique": False,
    }
  ]
  monkeypatch.setattr(revision, "_get_inspector", lambda: inspector)
  monkeypatch.setattr(
    revision.op,
    "alter_column",
    lambda *_args, **_kwargs: pytest.fail("already widened event_id must be adopted"),
  )
  monkeypatch.setattr(
    revision.op,
    "create_table",
    lambda *_args, **_kwargs: pytest.fail("complete candidate table must be adopted"),
  )
  monkeypatch.setattr(
    revision.op,
    "create_index",
    lambda *_args, **_kwargs: pytest.fail("complete runtime index must be adopted"),
  )

  revision.upgrade()


def test_0030_repairs_only_legacy_event_width_and_missing_runtime_index(monkeypatch):
  revision = _load("20260823_0030_t_trade_candidate_outcomes.py")
  inspector = _Inspector()
  _seed_runtime(inspector, revision, event_id_length=36)
  _seed_0030_candidate(inspector, revision)
  monkeypatch.setattr(revision, "_get_inspector", lambda: inspector)
  altered = []
  indexes = []
  monkeypatch.setattr(
    revision.op,
    "alter_column",
    lambda *args, **kwargs: altered.append((args, kwargs)),
  )
  monkeypatch.setattr(
    revision.op,
    "create_index",
    lambda *args, **kwargs: indexes.append((args, kwargs)),
  )

  revision.upgrade()

  assert len(altered) == 1
  assert altered[0][0] == ("account_trading_rollout_events", "event_id")
  assert altered[0][1]["existing_type"].length == 36
  assert altered[0][1]["type_"].length == 128
  assert altered[0][1]["existing_nullable"] is False
  assert indexes[0][0] == (
    revision._RUNTIME_REPAIR_INDEX,
    revision._RUNTIME_EVENTS_TABLE,
    ["strategy_run_id", "created_at", "event_id"],
  )


def test_0030_mismatched_precreated_candidate_or_runtime_index_fails_closed():
  revision = _load("20260823_0030_t_trade_candidate_outcomes.py")
  inspector = _Inspector()
  _seed_runtime(inspector, revision, event_id_length=128)
  _seed_0030_candidate(inspector, revision)
  inspector.indexes[revision._RUNTIME_EVENTS_TABLE] = [
    {
      "name": revision._RUNTIME_REPAIR_INDEX,
      "column_names": ["strategy_run_id", "event_id"],
      "unique": False,
    }
  ]
  # A fake inspector is sufficient because the failure must happen before any
  # Alembic operation is reached.
  revision._get_inspector = lambda: inspector

  with pytest.raises(RuntimeError, match="schema"):
    revision.upgrade()


def test_0030_mismatched_precreated_candidate_constraint_fails_closed():
  revision = _load("20260823_0030_t_trade_candidate_outcomes.py")
  inspector = _Inspector()
  _seed_runtime(inspector, revision, event_id_length=128)
  _seed_0030_candidate(inspector, revision)
  inspector.check_constraints[revision._CANDIDATE_TABLE][0]["sqltext"] = "1 = 1"
  revision._get_inspector = lambda: inspector

  with pytest.raises(RuntimeError, match="constraint"):
    revision.upgrade()


def test_0031_adopts_precreated_groups_and_backfills_legacy_column(monkeypatch):
  revision = _load("20260823_0031_watchlist_groups.py")
  inspector = _Inspector()
  _seed_watchlist(inspector, revision, legacy=True)
  _seed_0031_groups(inspector, revision)
  _seed_0031_memberships(inspector, revision)
  monkeypatch.setattr(revision, "_get_inspector", lambda: inspector)
  operations = []
  monkeypatch.setattr(
    revision.op,
    "create_table",
    lambda *_args, **_kwargs: pytest.fail("complete groups must be adopted"),
  )
  monkeypatch.setattr(
    revision.op,
    "execute",
    lambda statement: operations.append(("execute", str(statement))),
  )
  monkeypatch.setattr(
    revision.op,
    "drop_column",
    lambda *args, **kwargs: operations.append(("drop_column", args, kwargs)),
  )

  revision.upgrade()

  assert sum(operation[0] == "execute" for operation in operations) == 2
  assert operations[-1][0] == "drop_column"
  assert "ON CONFLICT" in operations[0][1]
  assert "ON CONFLICT" in operations[1][1]


def test_0031_fresh_create_backfills_and_drops_legacy_column(monkeypatch):
  revision = _load("20260823_0031_watchlist_groups.py")
  inspector = _Inspector()
  _seed_watchlist(inspector, revision, legacy=True)
  monkeypatch.setattr(revision, "_get_inspector", lambda: inspector)
  created_tables = []
  operations = []
  monkeypatch.setattr(
    revision.op,
    "create_table",
    lambda name, *_args, **_kwargs: created_tables.append(name),
  )
  monkeypatch.setattr(
    revision.op,
    "create_index",
    lambda *_args, **_kwargs: operations.append(("create_index",)),
  )
  monkeypatch.setattr(
    revision.op,
    "execute",
    lambda statement: operations.append(("execute", str(statement))),
  )
  monkeypatch.setattr(
    revision.op,
    "drop_column",
    lambda *args, **kwargs: operations.append(("drop_column", args, kwargs)),
  )

  revision.upgrade()

  assert created_tables == [revision._GROUP_TABLE, revision._MEMBERSHIP_TABLE]
  assert sum(operation[0] == "execute" for operation in operations) == 3
  assert operations[-1][0] == "drop_column"


def test_0031_missing_target_tables_and_legacy_column_fails_closed():
  revision = _load("20260823_0031_watchlist_groups.py")
  inspector = _Inspector()
  _seed_watchlist(inspector, revision, legacy=False)
  revision._get_inspector = lambda: inspector

  with pytest.raises(RuntimeError, match="legacy.*group_name"):
    revision.upgrade()


@pytest.mark.parametrize(
  "mutate",
  [
    lambda inspector, revision: inspector.tables.remove(revision._MEMBERSHIP_TABLE),
    lambda inspector, revision: inspector.indexes[revision._GROUP_TABLE][1].update(
      column_names=["account_id", "name"]
    ),
    lambda inspector, revision: inspector.indexes[revision._GROUP_TABLE][1].update(
      expressions=["account_id", "upper(name)"]
    ),
  ],
)
def test_0031_partial_or_mismatched_precreated_schema_fails_closed(
  mutate,
):
  revision = _load("20260823_0031_watchlist_groups.py")
  inspector = _Inspector()
  _seed_watchlist(inspector, revision, legacy=True)
  _seed_0031_groups(inspector, revision)
  _seed_0031_memberships(inspector, revision)
  mutate(inspector, revision)
  revision._get_inspector = lambda: inspector

  with pytest.raises(RuntimeError, match="schema"):
    revision.upgrade()


def test_0031_adopts_already_migrated_watchlist_without_replaying_backfill(
  monkeypatch,
):
  revision = _load("20260823_0031_watchlist_groups.py")
  inspector = _Inspector()
  _seed_watchlist(inspector, revision, legacy=False)
  _seed_0031_groups(inspector, revision)
  _seed_0031_memberships(inspector, revision)
  monkeypatch.setattr(revision, "_get_inspector", lambda: inspector)
  monkeypatch.setattr(
    revision.op,
    "execute",
    lambda *_args, **_kwargs: pytest.fail("legacy backfill must not repeat"),
  )
  monkeypatch.setattr(
    revision.op,
    "drop_column",
    lambda *_args, **_kwargs: pytest.fail("legacy column is already gone"),
  )

  revision.upgrade()
