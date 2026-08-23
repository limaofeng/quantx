from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeInstrumentProfile,
  TTradeOpportunityEvaluation,
)

ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260823_0029_t_trade_opportunity_intelligence.py"
)


def _load_revision():
  spec = importlib.util.spec_from_file_location(
    "quantx_test_t_trade_opportunity_intelligence_revision",
    REVISION_PATH,
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class _FreshSchemaInspector:
  def get_table_names(self):
    return []


def test_revision_atomically_adds_only_the_two_authoritative_tables(
  monkeypatch,
) -> None:
  revision = _load_revision()
  monkeypatch.setattr(revision, "_get_inspector", lambda: _FreshSchemaInspector())
  created_tables: dict[str, tuple[tuple, dict]] = {}
  created_indexes: dict[str, tuple[str, tuple[str, ...]]] = {}
  monkeypatch.setattr(
    revision.op,
    "create_table",
    lambda name, *args, **kwargs: created_tables.setdefault(
      name,
      (args, kwargs),
    ),
  )
  monkeypatch.setattr(
    revision.op,
    "create_index",
    lambda name, table_name, columns, **_kwargs: created_indexes.setdefault(
      name,
      (table_name, tuple(columns)),
    ),
  )

  revision.upgrade()

  assert revision.revision == "20260823_0029"
  assert revision.down_revision == "20260822_0028"
  assert set(created_tables) == {
    "t_trade_opportunity_evaluations",
    "t_trade_instrument_profiles",
  }
  assert all("tick" not in name for name in created_tables)
  assert all("latest" not in name for name in created_tables)

  expected_models = {
    "t_trade_opportunity_evaluations": TTradeOpportunityEvaluation,
    "t_trade_instrument_profiles": TTradeInstrumentProfile,
  }
  for table_name, model in expected_models.items():
    args, kwargs = created_tables[table_name]
    migration_columns = {
      argument.name for argument in args if isinstance(argument, sa.Column)
    }
    assert migration_columns == set(model.__table__.columns.keys())
    assert kwargs["comment"] == model.__table__.comment

  evaluation_args, _ = created_tables["t_trade_opportunity_evaluations"]
  evaluation_constraints = {
    argument.name
    for argument in evaluation_args
    if isinstance(argument, sa.Constraint) and argument.name
  }
  assert {
    "uq_t_trade_opportunity_evaluation_event_key",
    "ck_t_trade_evaluation_record_kind",
    "ck_t_trade_evaluation_coalesced_count",
    "ck_t_trade_evaluation_candidate_material",
    "ck_t_trade_evaluation_window_shape",
  } <= evaluation_constraints

  profile_args, _ = created_tables["t_trade_instrument_profiles"]
  profile_constraints = {
    argument.name
    for argument in profile_args
    if isinstance(argument, sa.Constraint) and argument.name
  }
  assert {
    "uq_t_trade_instrument_profile_fingerprint",
    "uq_t_trade_instrument_profile_coordinate",
  } <= profile_constraints

  assert created_indexes == {
    "ix_t_trade_evaluation_account_time": (
      "t_trade_opportunity_evaluations",
      ("account_id", "evaluated_at", "id"),
    ),
    "ix_t_trade_evaluation_account_instrument_time": (
      "t_trade_opportunity_evaluations",
      ("account_id", "instrument_code", "evaluated_at", "id"),
    ),
    "ix_t_trade_evaluation_run_time": (
      "t_trade_opportunity_evaluations",
      ("strategy_run_id", "evaluated_at", "id"),
    ),
    "ix_t_trade_evaluation_account_candidate_time": (
      "t_trade_opportunity_evaluations",
      ("account_id", "candidate_id", "evaluated_at", "id"),
    ),
    "ix_t_trade_instrument_profile_asof": (
      "t_trade_instrument_profiles",
      ("instrument_code", "as_of", "id"),
    ),
  }


def test_revision_downgrade_removes_indexes_before_tables(monkeypatch) -> None:
  revision = _load_revision()
  operations: list[tuple[str, str]] = []
  monkeypatch.setattr(
    revision.op,
    "drop_index",
    lambda name, **_kwargs: operations.append(("index", name)),
  )
  monkeypatch.setattr(
    revision.op,
    "drop_table",
    lambda name: operations.append(("table", name)),
  )

  revision.downgrade()

  assert operations.index(
    ("index", "ix_t_trade_instrument_profile_asof")
  ) < operations.index(("table", "t_trade_instrument_profiles"))
  assert operations.index(
    ("index", "ix_t_trade_evaluation_account_time")
  ) < operations.index(("table", "t_trade_opportunity_evaluations"))
  assert operations.index(
    ("index", "ix_t_trade_evaluation_account_candidate_time")
  ) < operations.index(("table", "t_trade_opportunity_evaluations"))
