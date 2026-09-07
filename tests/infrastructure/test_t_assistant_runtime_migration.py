import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
)
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
from sqlalchemy.sql.dml import Insert

MIGRATION = (
  Path(__file__).parents[2]
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260903_0049_t_assistant_runtime.py"
)


def test_p3_migration_is_linear_and_contains_runtime_safety_constraints():
  spec = importlib.util.spec_from_file_location("p3_runtime_migration", MIGRATION)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)

  assert module.revision == "20260903_0049"
  assert module.down_revision == "20260903_0048"
  source = MIGRATION.read_text(encoding="utf-8")
  for table in (
    "t_assistant_config_versions",
    "t_assistant_executions",
    "t_assistant_execution_events",
    "t_assistant_symbol_states",
    "t_assistant_decision_cycles",
  ):
    assert table in source
  assert "uq_t_assistant_execution_live_entry_producer" in source
  assert "uq_t_assistant_cycle_decision_attempt" in source
  assert "P3_SHADOW_CONFLICT:opportunity_owner_unproven" in source
  assert "P3_SHADOW_CONFLICT:candidate_owner_unproven" in source
  assert 'op.alter_column(table_name, "strategy_run_id", nullable=True)' in source
  assert "trg_t_assistant_config_version_append_only" in source
  assert "trg_t_assistant_execution_event_append_only" in source
  assert "trg_t_assistant_execution_binding_guard" in source
  assert "T_ASSISTANT_EXECUTION_CONFIG_BINDING_INVALID" in source
  assert "quantx_reject_identity_mutation" in source
  assert "ck_t_assistant_cycle_fence_shape" in source
  assert "ck_t_assistant_cycle_hash_shape" in source
  assert "ck_t_assistant_cycle_claim_shape" in source
  assert "ck_t_assistant_cycle_terminal_shape" in source
  assert "sa.JSON(none_as_null=True)" in source


def test_p3_migration_never_allows_a_destructive_downgrade():
  source = MIGRATION.read_text(encoding="utf-8")
  assert "schema downgrades are intentionally disabled" in source


def test_p3_legacy_authorization_and_rollout_backfill_is_fail_closed():
  spec = importlib.util.spec_from_file_location("p3_runtime_migration", MIGRATION)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)

  assert (
    module._legacy_entry_authorization({"entry_execution_mode": "LIVE_AUTO"}) == "AUTO"
  )
  assert (
    module._legacy_entry_authorization({"execution_mode": "BACKTEST_AUTO"})
    == "MANUAL_CONFIRM"
  )
  assert (
    module._legacy_entry_authorization(
      {"entry_authorization": "AUTO", "execution_mode": "paper"}
    )
    == "AUTO"
  )
  assert module._legacy_entry_authorization({}) == "MANUAL_CONFIRM"
  assert module._legacy_rollout_stage({"rollout_stage": "STANDARD"}) == ("STANDARD")
  assert module._legacy_rollout_stage({}) == "CANARY"


def test_p3_config_head_trigger_fences_version_activation_state():
  source = MIGRATION.read_text(encoding="utf-8")
  assert "NEW.state_version <> OLD.state_version + 1" in source
  assert "T_ASSISTANT_CONFIG_HEAD_STATE_VERSION_INVALID" in source
  assert "P3_SHADOW_CONFLICT:backtest_active_config" in source


def test_p3_cycle_migration_constraints_match_orm_shape():
  expected = {
    "ck_t_assistant_cycle_status",
    "ck_t_assistant_cycle_identity",
    "ck_t_assistant_cycle_counts",
    "ck_t_assistant_cycle_material_count",
    "ck_t_assistant_cycle_fence_shape",
    "ck_t_assistant_cycle_hash_shape",
    "ck_t_assistant_cycle_claim_shape",
    "ck_t_assistant_cycle_terminal_shape",
  }
  orm_names = {
    constraint.name
    for constraint in TAssistantDecisionCycleRecord.__table__.constraints
    if isinstance(constraint, CheckConstraint)
  }
  source = MIGRATION.read_text(encoding="utf-8")

  assert expected <= orm_names
  assert all(name in source for name in expected)


def test_p3_backfill_binds_json_payload_and_sql_null(monkeypatch):
  spec = importlib.util.spec_from_file_location("p3_runtime_migration", MIGRATION)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  statements = []

  def execute(statement, *_args):
    statements.append(statement)
    return SimpleNamespace(mappings=lambda: [{
      "id": "config-1", "account_id": "account-1", "mode": "paper",
      "ignored_stock_codes": [], "settings": {}, "config_version": 1,
    }])

  monkeypatch.setattr(module.op, "get_bind", lambda: SimpleNamespace(execute=execute))
  module._backfill_config_versions()
  insert = next(statement for statement in statements if isinstance(statement, Insert))
  compiled = insert.compile(dialect=PGDialect_asyncpg())
  processors = compiled._bind_processors
  encoded = processors["canonical_payload"](compiled.params["canonical_payload"])
  assert json.loads(encoded)["config_schema_version"] == "t_assistant_config_v1"
  assert processors["model_runtime_binding"](None) is None
