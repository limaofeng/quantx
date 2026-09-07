"""Consolidated, fingerprint-locked baseline for the pre-Alembic schema.

Existing databases are schema-doctored and stamped at this revision. Empty
databases execute this release baseline before subsequent revisions. The
metadata fingerprint makes accidental baseline drift fail closed: model changes
must be represented by a new Alembic revision.
"""

import hashlib
import json

import quantx_infrastructure.models  # noqa: F401
from alembic import op
from quantx_infrastructure.database.relational_base import Base
from sqlalchemy import (
  Boolean,
  CheckConstraint,
  Column,
  DateTime,
  ForeignKeyConstraint,
  Index,
  MetaData,
  String,
  UniqueConstraint,
)
from sqlalchemy.dialects import postgresql

revision = "20260729_0001"
down_revision = None
branch_labels = None
depends_on = None

EXPECTED_METADATA_SHA256 = (
  "1fd3acd15bc9592ba3f972786261c3dc3a49da1b0b04dad65b63fbc49ce56cd5"
)

# Models added by revisions after this immutable baseline must not affect its
# fingerprint or be created early when bootstrapping an empty database.
POST_BASELINE_TABLES = {
  "t_allocation_batches",
  "t_allocation_decisions",
  "account_risk_increase_admission_batches",
  "account_risk_increase_admission_items",
  "account_execution_control_events",
  "account_execution_controls",
  "account_trading_rollout_events",
  "ai_assistant_deletion_audits",
  "ai_assistant_events",
  "ai_assistant_messages",
  "ai_assistant_runs",
  "ai_assistant_session_items",
  "ai_assistant_threads",
  "ai_assistant_tool_calls",
  "ai_runtime_settings",
  "ai_runtime_settings_audits",
  "auto_exit_plan_events",
  "auto_exit_plans",
  "entry_automation_gates",
  "entry_plan_authorization_consumptions",
  "entry_plan_authorization_events",
  "entry_plan_authorization_grants",
  "financial_metric_roe_qualities",
  "financial_sync_code_audits",
  "financial_sync_runs",
  "first_board_candidate_preferences",
  "first_board_model_releases",
  "first_board_promotion_assessments",
  "limit_up_chain_snapshots",
  "limit_up_lifecycle_snapshots",
  "limit_up_research_artifacts",
  "limit_up_research_jobs",
  "limit_up_radar_events",
  "stock_candidate_rule_versions",
  "stock_candidates",
  "stock_prediction_runs",
  "stock_predictions",
  "stock_selection_model_versions",
  "stock_selection_dataset_versions",
  "stock_selection_training_specs",
  "stock_selection_training_runs",
  "limit_up_board_assistant_configs",
  "limit_up_board_assistant_projections",
  "limit_up_board_candidate_arms",
  "limit_up_board_replay_jobs",
  "limit_up_board_replay_scenarios",
  "limit_up_board_universe_snapshots",
  "ios_business_notification_receipts",
  "ios_notification_events",
  "ios_notification_outbox",
  "ios_push_category_preferences",
  "ios_push_registrations",
  "trade_confirmation_challenges",
  "t_trade_instrument_profiles",
  "t_assistant_config_versions",
  "t_assistant_decision_cycles",
  "t_assistant_execution_events",
  "t_assistant_executions",
  "t_assistant_symbol_states",
  "t_trade_candidate_outcomes",
  "t_trade_opportunity_evaluations",
  "t_trade_replay_projections",
  # Added by 20260823_0031; a fresh database must not create these before
  # their owning revision runs.
  "watchlist_groups",
  "watchlist_group_memberships",
}
POST_BASELINE_COLUMNS = {
  "indicator_snapshots": {
    "boll_near_lower",
    "boll_near_upper",
    "calculation_version",
    "kdj_cross_up",
    "ma_cross_up",
    "valid_history_count",
  },
  "agent_devices": {
    "replaces_device_id",
  },
  "agent_enrollment_codes": {
    "replaces_device_id",
  },
  "market_data_request": {
    "ingestion_result",
    "processing_claim_token",
  },
  "market_data_transfer": {
    "compressed_bytes",
  },
  "auth_device_sessions": {
    "granted_permissions",
  },
  "conditional_liquidation_orders": {
    "auto_exit_authorized",
    "dynamic_policy",
    "execution_mode",
    "exit_plan_id",
    "strategy",
  },
  "strategy_trade_intents": {
    "allocation_cycle_id",
    "allocation_version",
    "allocation_decision_id",
    "allocation_next_eligible_at",
    "account_id",
    "admission_batch_id",
    "admission_input_fingerprint",
    "admission_policy_version",
    "admission_rank",
    "environment",
    "idempotency_key",
    "owner_id",
    "owner_type",
  },
  "t_trade_global_configs": {
    "active_config_version_id",
    "desired_environment",
    "state_version",
  },
  "pending_trade_orders": {
    "t_order_attempt",
    "t_order_parent_client_id",
    "t_order_original_created_at",
    "owner_id",
    "owner_type",
  },
  "strategy_order_correlations": {
    "owner_id",
    "owner_type",
  },
  "trade_command_outbox": {
    "environment",
    "owner_id",
    "owner_type",
  },
  "strategy_runtime_events": {
    "environment",
    "owner_id",
    "owner_type",
  },
  # Revisions 20260829_0036/0037 own the durable T-batch history fields.  Keep
  # them out of the immutable production baseline so an empty database applies
  # their backfill and terminal-state migrations in the intended order.
  "t_trade_batches": {
    "closed_at",
    "commission_rate",
    "environment",
    "entry_filled_at",
    "last_exit_filled_at",
    "metrics_origin",
    "minimum_commission",
    "stamp_tax_rate",
    "terminal_at",
    "transfer_fee_rate",
    "source_execution_environment",
    "source_execution_owner_id",
    "source_execution_owner_type",
  },
  "stock_announcements": {
    "content_fetched_at",
    "content_hash",
    "content_text",
    "source_authority",
  },
}
POST_BASELINE_INDEXES = {
  "agent_report_inbox": {"ix_agent_report_snapshot_lookup"},
  "market_data_request": {"ix_market_data_request_device_status_created"},
  "orders": {"ix_orders_exit_plan_cost_basis"},
  # Added by 20260823_0030 for the candidate-outcome repair cursor.  Keep it
  # out of the immutable baseline so a fresh database does not create the
  # index before the revision that owns it runs.
  "strategy_runtime_events": {"ix_strategy_runtime_event_run_created"},
  "t_trade_batches": {
    "ix_t_trade_batch_account_closed",
    "ix_t_trade_batch_account_mode",
    "ix_t_trade_batch_account_terminal",
  },
  "trade_command_outbox": {
    "ix_trade_command_device_status_delivery_expiry",
    "ix_trade_command_device_status_expiry_created",
    "ix_trade_command_device_status_kind_created",
  },
}


def _baseline_metadata() -> MetaData:
  """Clone only the schema that belonged to the immutable baseline."""
  metadata = MetaData()
  table_names = {
    "trade_intents": "strategy_trade_intents",
    "order_correlations": "strategy_order_correlations",
  }
  for table in Base.metadata.tables.values():
    if table.key not in POST_BASELINE_TABLES:
      table.to_metadata(metadata, name=table_names.get(table.key))

  # The live model now stores account-wide facts in
  # ``account_execution_controls``. Reconstruct the columns that were part of
  # this locked baseline so the historical fingerprint and empty-database
  # migration path remain immutable.
  rollout = metadata.tables["account_trading_rollouts"]
  historical_columns = [
    Column("kill_switch", Boolean(), nullable=False),
    Column("reconcile_status", String(length=32), nullable=False),
    Column("last_snapshot_id", String(length=128), nullable=True),
    Column("last_snapshot_hash", String(length=64), nullable=True),
    Column("last_snapshot_at", DateTime(), nullable=True),
    Column("last_backup_at", DateTime(), nullable=True),
  ]
  for column in historical_columns:
    rollout.append_column(column)
  columns = rollout._columns._collection
  for name in ("kill_switch", "reconcile_status"):
    entry = next(value for value in columns if value[0] == name)
    columns.remove(entry)
    max_batches_index = next(
      index for index, value in enumerate(columns) if value[0] == "max_active_batches"
    )
    columns.insert(max_batches_index, entry)
  for name in (
    "last_snapshot_id",
    "last_snapshot_hash",
    "last_snapshot_at",
    "last_backup_at",
  ):
    entry = next(value for value in columns if value[0] == name)
    columns.remove(entry)
    created_at_index = next(
      index for index, value in enumerate(columns) if value[0] == "created_at"
    )
    columns.insert(created_at_index, entry)

  for table_key, column_names in POST_BASELINE_COLUMNS.items():
    table = metadata.tables[table_key]
    for index in list(table.indexes):
      indexed_columns = {
        str(getattr(expression, "name", "")) for expression in index.expressions
      }
      if indexed_columns & column_names:
        table.indexes.remove(index)
    for constraint in list(table.constraints):
      if {column.name for column in constraint.columns} & column_names or (
        table_key == "auth_device_sessions"
        and constraint.name == "ck_auth_device_sessions_scope_pair"
      ):
        table.constraints.remove(constraint)
    for column_name in column_names:
      # Removing a Column/ForeignKeyConstraint does not remove the separate
      # Table.foreign_keys entries used by SQLAlchemy's DDL dependency sorter.
      for foreign_key in list(table.c[column_name].foreign_keys):
        table.foreign_keys.discard(foreign_key)
      table._columns.remove(table.c[column_name])
  for table_key, index_names in POST_BASELINE_INDEXES.items():
    table = metadata.tables[table_key]
    for index in list(table.indexes):
      if index.name in index_names:
        table.indexes.remove(index)
  # The owner/environment checks belong to the adoption revision, never to
  # this historical clone.  CheckConstraint.columns is empty for textual
  # checks, so the column-based pruning above cannot remove them.
  for table_key in (
    "strategy_trade_intents",
    "pending_trade_orders",
    "strategy_order_correlations",
    "trade_command_outbox",
    "strategy_runtime_events",
    "t_trade_batches",
    "t_trade_global_configs",
  ):
    table = metadata.tables[table_key]
    for constraint in list(table.constraints):
      if isinstance(constraint, CheckConstraint):
        table.constraints.remove(constraint)
  # ``group_name`` was part of the immutable watchlist baseline.  It is
  # intentionally absent from the live model after 20260823_0031, so restore
  # this historical-only column in the clone used for the fingerprint check.
  watchlist_items = metadata.tables["watchlist_items"]
  if "group_name" not in watchlist_items.c:
    watchlist_items.append_column(
      Column("group_name", String(length=80), nullable=True, comment="分组")
    )
    columns = watchlist_items._columns._collection
    group_entry = next(entry for entry in columns if entry[0] == "group_name")
    columns.remove(group_entry)
    note_index = next(index for index, entry in enumerate(columns) if entry[0] == "note")
    columns.insert(note_index, group_entry)
  # Revision 20260813_0010 makes strategy ownership optional for plan-owned
  # intents; the immutable baseline still required a strategy run.
  # The baseline predates the canonical table names and owner/environment
  # projection.  Restore the historical column names and nullability inside
  # this clone only; the live ORM metadata remains canonical.
  def rename_column(table_key: str, old_name: str, new_name: str) -> None:
    table = metadata.tables[table_key]
    column = table.c[old_name]
    column.name = new_name
    column.key = new_name
    for index, entry in enumerate(table._columns._collection):
      if entry[1] is column:
        table._columns._collection[index] = (new_name, column, entry[2])
        break
    table._columns._index.clear()
    for index, entry in enumerate(table._columns._collection):
      table._columns._index[index] = (entry[0], entry[1])
      table._columns._index[entry[0]] = (entry[0], entry[1])

  rename_column("strategy_order_correlations", "environment", "execution_mode")
  rename_column("pending_trade_orders", "environment", "execution_mode")
  def move_column(table_key: str, column_name: str, target_index: int) -> None:
    table = metadata.tables[table_key]
    collection = table._columns._collection
    current_index = next(
      index for index, entry in enumerate(collection) if entry[0] == column_name
    )
    entry = collection.pop(current_index)
    collection.insert(target_index, entry)
    table._columns._index.clear()
    for index, item in enumerate(collection):
      table._columns._index[index] = (item[0], item[1])
      table._columns._index[item[0]] = (item[0], item[1])

  move_column("strategy_order_correlations", "execution_mode", 10)
  move_column("pending_trade_orders", "execution_mode", 11)
  metadata.tables["strategy_trade_intents"].c.strategy_run_id.nullable = False
  metadata.tables["strategy_order_correlations"].c.strategy_run_id.nullable = False
  metadata.tables["strategy_order_correlations"].c.strategy_order_id.nullable = False
  metadata.tables["strategy_order_correlations"].c.intent_id.nullable = False
  metadata.tables["strategy_runtime_events"].c.strategy_run_id.nullable = False
  metadata.tables["t_trade_batches"].c.strategy_run_id.nullable = False
  # Revision 0047 widens vendor order IDs in the live schema; keep the
  # immutable pre-0047 clone at its historical width for the locked hash.
  metadata.tables["orders"].c.order_sysid.type = String(length=10)
  metadata.tables["trades"].c.order_sysid.type = String(length=10)
  for constraint in metadata.tables["strategy_order_correlations"].constraints:
    if isinstance(constraint, UniqueConstraint) and {
      column.name for column in constraint.columns
    } == {"client_order_id"}:
      constraint.name = "uq_strategy_order_client"

  # Canonical indexes replaced these baseline indexes.  Recreate the exact
  # historical names/columns in the clone so the locked fingerprint remains
  # a check on the original schema rather than on current ORM naming.
  order_correlations = metadata.tables["strategy_order_correlations"]
  if not any(index.name == "ix_strategy_order_run_batch" for index in order_correlations.indexes):
    Index(
      "ix_strategy_order_run_batch",
      order_correlations.c.strategy_run_id,
      order_correlations.c.batch_id,
    )
  return metadata


def _metadata_payload() -> list[dict[str, object]]:
  dialect = postgresql.dialect()
  payload: list[dict[str, object]] = []
  metadata = _baseline_metadata()
  for table in sorted(metadata.tables.values(), key=lambda value: value.key):
    constraints: list[dict[str, object]] = []
    for constraint in sorted(
      table.constraints,
      key=lambda value: (
        value.__class__.__name__,
        value.name or "",
        ",".join(column.name for column in value.columns),
      ),
    ):
      entry: dict[str, object] = {
        "kind": constraint.__class__.__name__,
        "name": constraint.name,
        "columns": [column.name for column in constraint.columns],
      }
      if isinstance(constraint, CheckConstraint):
        entry["sqltext"] = str(constraint.sqltext)
      if isinstance(constraint, ForeignKeyConstraint):
        entry["targets"] = [element.target_fullname for element in constraint.elements]
        entry["ondelete"] = constraint.ondelete
        entry["onupdate"] = constraint.onupdate
      if isinstance(constraint, UniqueConstraint):
        entry["unique"] = True
      constraints.append(entry)
    payload.append(
      {
        "key": table.key,
        "schema": table.schema,
        "columns": [
          {
            "name": column.name,
            "type": column.type.compile(dialect=dialect),
            "nullable": column.nullable,
            "primary_key": column.primary_key,
            "unique": column.unique,
            "autoincrement": column.autoincrement,
            "server_default": (
              str(column.server_default.arg) if column.server_default else None
            ),
          }
          for column in table.columns
        ],
        "constraints": constraints,
        "indexes": [
          {
            "name": index.name,
            "unique": index.unique,
            "expressions": [str(expression) for expression in index.expressions],
          }
          for index in sorted(table.indexes, key=lambda value: value.name or "")
        ],
      }
    )
  return payload


def metadata_sha256() -> str:
  encoded = json.dumps(
    _metadata_payload(),
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def upgrade() -> None:
  actual = metadata_sha256()
  if actual != EXPECTED_METADATA_SHA256:
    raise RuntimeError(
      "The immutable QuantX baseline metadata changed. "
      "Create a new Alembic revision instead of rewriting revision "
      f"{revision}; expected={EXPECTED_METADATA_SHA256} actual={actual}."
    )
  _baseline_metadata().create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
