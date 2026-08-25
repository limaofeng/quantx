from __future__ import annotations

import importlib.util
from pathlib import Path

from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AccountExecutionControlEvent,
  TTradeRollout,
  TTradeRolloutEvent,
)

ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260825_0033_account_execution_control.py"
)


def _load_revision():
  spec = importlib.util.spec_from_file_location(
    "quantx_test_account_execution_control_revision",
    REVISION_PATH,
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_account_and_t_assistant_models_have_disjoint_gate_fields() -> None:
  revision = _load_revision()
  account_columns = set(AccountExecutionControl.__table__.columns.keys())
  account_event_columns = set(AccountExecutionControlEvent.__table__.columns.keys())
  t_columns = set(TTradeRollout.__table__.columns.keys())

  assert {
    "authorization_state",
    "reconcile_status",
    "controlled_window_active",
    "last_snapshot_id",
  } <= account_columns
  assert {
    "stage",
    "enabled",
    "max_active_batches",
    "policy_version",
  } <= t_columns
  assert (
    not {
      "stage",
      "enabled",
      "max_active_batches",
      "policy_version",
    }
    & account_columns
  )
  assert (
    not {
      "authorization_state",
      "reconcile_status",
      "controlled_window_active",
      "last_snapshot_id",
    }
    & t_columns
  )
  assert AccountExecutionControlEvent.__tablename__ == (
    "account_execution_control_events"
  )
  assert set(revision._ACCOUNT_CONTROL_COLUMN_SPECS) == account_columns
  assert set(revision._ACCOUNT_EVENT_COLUMN_SPECS) == account_event_columns
  assert TTradeRolloutEvent.__tablename__ == "account_trading_rollout_events"


def test_0033_migration_resets_authorization_and_preserves_broker_facts() -> None:
  revision = _load_revision()
  source = REVISION_PATH.read_text(encoding="utf-8")

  assert revision.down_revision == "20260825_0032"
  assert "CASE WHEN kill_switch THEN 'KILLED' ELSE 'DISABLED' END" in source
  assert "last_snapshot_id" in source
  assert "last_snapshot_hash" in source
  assert "last_snapshot_at" in source
  assert "last_backup_at" in source
  assert "controlled_window_active, controlled_window_snapshot_id" in source
  assert "FALSE," in source
  assert "FROM account_trading_rollouts" in source
  assert "ON CONFLICT (account_id) DO UPDATE" in source


def test_0033_strictly_adopts_complete_create_all_tables() -> None:
  source = REVISION_PATH.read_text(encoding="utf-8")

  assert "def _adopt_precreated_tables() -> bool:" in source
  assert "account execution control schema is partially precreated" in source
  assert "has incompatible columns" in source
  assert "has incompatible primary key" in source
  assert "has incompatible indexes" in source
  assert "_normalize_precreated_table_defaults()" in source
  assert "UPDATE account_execution_controls" in source
  assert "authorized_by_user_id = NULL" in source
  assert "controlled_window_active = FALSE" in source


def test_0033_removes_only_account_gate_fields_from_t_rollout() -> None:
  revision = _load_revision()

  assert set(revision._LEGACY_GENERIC_COLUMNS) == {
    "kill_switch",
    "reconcile_status",
    "last_snapshot_id",
    "last_snapshot_hash",
    "last_snapshot_at",
    "last_backup_at",
    "controlled_window_active",
    "controlled_window_snapshot_id",
    "controlled_window_snapshot_hash",
    "controlled_window_started_at",
    "controlled_window_started_by_user_id",
    "controlled_window_external_order_ids",
    "controlled_window_external_trade_ids",
  }
  t_columns = set(TTradeRollout.__table__.columns.keys())
  assert {
    "stage",
    "enabled",
    "max_active_batches",
    "max_batch_volume",
    "max_order_amount",
    "max_total_exposure_pct",
    "policy_version",
    "acknowledged_policy_version",
  } <= t_columns


def test_0033_grants_an_independent_account_control_permission() -> None:
  source = REVISION_PATH.read_text(encoding="utf-8")

  assert source.count("account-execution:control") >= 4
  assert "permissions::jsonb ? 'trade:approve'" in source
  assert "granted_permissions::jsonb ? 'trade:approve'" in source


def test_create_all_compatibility_hook_does_not_repollute_t_rollout() -> None:
  source = (
    ROOT
    / "packages"
    / "infrastructure"
    / "src"
    / "quantx_infrastructure"
    / "database"
    / "relational.py"
  ).read_text(encoding="utf-8")
  compatibility_hook = source.split("def _ensure_compat_columns", maxsplit=1)[1]

  assert "ALTER TABLE account_trading_rollouts" not in compatibility_hook
  assert "last_snapshot_id" not in compatibility_hook
