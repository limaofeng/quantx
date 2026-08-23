from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from quantx_infrastructure.models.t_trade_candidate_outcome import (
  TTradeCandidateOutcome,
)

ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260823_0030_t_trade_candidate_outcomes.py"
)
BASELINE_PATH = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260729_0001_production_baseline.py"
)


def _load_revision():
  spec = importlib.util.spec_from_file_location(
    "quantx_test_t_trade_candidate_outcome_revision", REVISION_PATH
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _load_baseline():
  spec = importlib.util.spec_from_file_location(
    "quantx_test_production_baseline_revision", BASELINE_PATH
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class _FreshSchemaInspector:
  def get_table_names(self):
    return ["account_trading_rollout_events", "strategy_runtime_events"]

  def get_columns(self, table_name):
    if table_name == "account_trading_rollout_events":
      return [{"name": "event_id", "type": sa.String(length=36), "nullable": False}]
    raise AssertionError(f"unexpected columns request for {table_name}")

  def get_indexes(self, table_name):
    assert table_name == "strategy_runtime_events"
    return []


def test_baseline_excludes_the_post_baseline_runtime_event_index() -> None:
  baseline = _load_baseline()
  metadata = baseline._baseline_metadata()
  runtime_events = metadata.tables["strategy_runtime_events"]

  assert "ix_strategy_runtime_event_run_created" not in {
    index.name for index in runtime_events.indexes
  }


def test_revision_matches_model_columns_constraints_and_indexes(monkeypatch) -> None:
  revision = _load_revision()
  monkeypatch.setattr(revision, "_get_inspector", lambda: _FreshSchemaInspector())
  created_tables: dict[str, tuple[tuple, dict]] = {}
  created_indexes: dict[str, tuple[str, tuple[str, ...]]] = {}
  altered_columns: list[tuple[str, str, dict]] = []
  monkeypatch.setattr(
    revision.op,
    "create_table",
    lambda name, *args, **kwargs: created_tables.setdefault(name, (args, kwargs)),
  )
  monkeypatch.setattr(
    revision.op,
    "create_index",
    lambda name, table_name, columns, **_kwargs: created_indexes.setdefault(
      name, (table_name, tuple(columns))
    ),
  )
  monkeypatch.setattr(
    revision.op,
    "alter_column",
    lambda table_name, column_name, **kwargs: altered_columns.append(
      (table_name, column_name, kwargs)
    ),
  )

  revision.upgrade()

  assert revision.revision == "20260823_0030"
  assert revision.down_revision == "20260823_0029"
  assert len(altered_columns) == 1
  table_name, column_name, alter_kwargs = altered_columns[0]
  assert (table_name, column_name) == (
    "account_trading_rollout_events",
    "event_id",
  )
  assert alter_kwargs["existing_type"].length == 36
  assert alter_kwargs["type_"].length == 128
  assert alter_kwargs["existing_nullable"] is False
  assert set(created_tables) == {"t_trade_candidate_outcomes"}
  arguments, kwargs = created_tables["t_trade_candidate_outcomes"]
  migration_columns = {
    argument.name for argument in arguments if isinstance(argument, sa.Column)
  }
  assert migration_columns == set(TTradeCandidateOutcome.__table__.columns.keys())
  assert kwargs["comment"] == TTradeCandidateOutcome.__table__.comment
  constraints = {
    argument.name
    for argument in arguments
    if isinstance(argument, sa.Constraint) and argument.name
  }
  assert {
    "uq_t_trade_candidate_outcome_run_candidate",
    "ck_t_trade_candidate_outcome_status",
    "ck_t_trade_candidate_outcome_post_fill_status",
    "ck_t_trade_candidate_outcome_source_identity",
    "ck_t_trade_candidate_outcome_reference_price",
    "ck_t_trade_candidate_outcome_state_version",
    "ck_t_trade_candidate_outcome_terminal_shape",
  } <= constraints
  assert created_indexes == {
    "ix_t_trade_candidate_outcome_run_status": (
      "t_trade_candidate_outcomes",
      ("strategy_run_id", "status", "instrument_code"),
    ),
    "ix_t_trade_candidate_outcome_account_time": (
      "t_trade_candidate_outcomes",
      ("account_id", "candidate_at", "id"),
    ),
    "ix_t_trade_candidate_outcome_run_post_fill_status": (
      "t_trade_candidate_outcomes",
      ("strategy_run_id", "post_fill_status", "instrument_code"),
    ),
    "ix_strategy_runtime_event_run_created": (
      "strategy_runtime_events",
      ("strategy_run_id", "created_at", "event_id"),
    ),
  }


def test_revision_downgrade_is_disabled_to_protect_namespaced_operation_ids() -> None:
  revision = _load_revision()

  with pytest.raises(RuntimeError, match="production schema downgrades"):
    revision.downgrade()
