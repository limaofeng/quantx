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
  CheckConstraint,
  ForeignKeyConstraint,
  MetaData,
  UniqueConstraint,
)
from sqlalchemy.dialects import postgresql

revision = "20260729_0001"
down_revision = None
branch_labels = None
depends_on = None

EXPECTED_METADATA_SHA256 = (
  "ce1600fbb992cdd47fd945d7c8c2fe87a045c56c8223ede3e42c98deeaa339a5"
)

# Models added by revisions after this immutable baseline must not affect its
# fingerprint or be created early when bootstrapping an empty database.
POST_BASELINE_TABLES = {
  "account_trading_rollout_events",
  "ai_assistant_deletion_audits",
  "ai_assistant_events",
  "ai_assistant_messages",
  "ai_assistant_runs",
  "ai_assistant_session_items",
  "ai_assistant_threads",
  "ai_assistant_tool_calls",
  "auto_exit_plan_events",
  "auto_exit_plans",
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
  "limit_up_board_assistant_configs",
  "limit_up_board_assistant_projections",
  "limit_up_board_candidate_arms",
}
POST_BASELINE_COLUMNS = {
  "account_trading_rollouts": {
    "controlled_window_active",
    "controlled_window_external_order_ids",
    "controlled_window_external_trade_ids",
    "controlled_window_snapshot_hash",
    "controlled_window_snapshot_id",
    "controlled_window_started_at",
    "controlled_window_started_by_user_id",
  },
  "conditional_liquidation_orders": {
    "auto_exit_authorized",
    "dynamic_policy",
    "execution_mode",
    "exit_plan_id",
    "strategy",
  },
  "strategy_trade_intents": {
    "account_id",
    "owner_id",
    "owner_type",
  },
  "stock_announcements": {
    "content_fetched_at",
    "content_hash",
    "content_text",
    "source_authority",
  },
}


def _baseline_metadata() -> MetaData:
  """Clone only the schema that belonged to the immutable baseline."""
  metadata = MetaData()
  for table in Base.metadata.tables.values():
    if table.key not in POST_BASELINE_TABLES:
      table.to_metadata(metadata)

  for table_key, column_names in POST_BASELINE_COLUMNS.items():
    table = metadata.tables[table_key]
    for index in list(table.indexes):
      indexed_columns = {
        str(getattr(expression, "name", ""))
        for expression in index.expressions
      }
      if indexed_columns & column_names:
        table.indexes.remove(index)
    for constraint in list(table.constraints):
      if {column.name for column in constraint.columns} & column_names:
        table.constraints.remove(constraint)
    for column_name in column_names:
      table._columns.remove(table.c[column_name])
  # Revision 20260813_0010 makes strategy ownership optional for plan-owned
  # intents; the immutable baseline still required a strategy run.
  metadata.tables["strategy_trade_intents"].c.strategy_run_id.nullable = False
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
        entry["targets"] = [
          element.target_fullname for element in constraint.elements
        ]
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
