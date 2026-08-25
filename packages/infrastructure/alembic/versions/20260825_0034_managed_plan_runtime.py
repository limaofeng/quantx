"""Separate stable managed plans from immutable StrategyRun revisions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260825_0034"
down_revision = "20260825_0033"
branch_labels = None
depends_on = None


_MANAGED_PLAN_COLUMN_SPECS = {
  "plan_id": (sa.String, 128, False),
  "plan_kind": (sa.String, 16, False),
  "account_id": (sa.String, 50, False),
  "instrument_code": (sa.String, 20, False),
  "status": (sa.String, 32, False),
  "current_config_version": (sa.Integer, None, False),
  "current_run_id": (sa.String, 36, True),
  "last_command_id": (sa.String, 128, True),
  "last_error": (sa.Text, None, True),
  "created_at": (sa.DateTime, None, False),
  "updated_at": (sa.DateTime, None, False),
}

_MANAGED_REVISION_COLUMN_SPECS = {
  "revision_id": (sa.String, 36, False),
  "plan_id": (sa.String, 128, False),
  "config_version": (sa.Integer, None, False),
  "config_snapshot": (sa.JSON, None, False),
  "config_fingerprint": (sa.String, 64, False),
  "state_migration_policy": (sa.String, 48, False),
  "supersedes_run_id": (sa.String, 36, True),
  "run_id": (sa.String, 36, True),
  "created_by_user_id": (sa.String, 50, True),
  "created_at": (sa.DateTime, None, False),
}


def _validate_precreated_table(
  inspector: sa.Inspector,
  table_name: str,
  specs: dict[str, tuple[type[sa.types.TypeEngine], int | None, bool]],
  *,
  primary_key: tuple[str, ...],
  indexes: dict[str, tuple[str, ...]],
  unique_constraints: dict[str, tuple[str, ...]],
) -> None:
  columns = {column["name"]: column for column in inspector.get_columns(table_name)}
  if set(columns) != set(specs):
    raise RuntimeError(
      f"precreated {table_name} has incompatible columns: "
      f"expected={sorted(specs)}, actual={sorted(columns)}"
    )
  for name, (type_class, length, nullable) in specs.items():
    column = columns[name]
    column_type = column["type"]
    if not isinstance(column_type, type_class):
      raise RuntimeError(
        f"precreated {table_name}.{name} has incompatible type {column_type!s}"
      )
    if length is not None and getattr(column_type, "length", None) != length:
      raise RuntimeError(
        f"precreated {table_name}.{name} has incompatible length"
      )
    if bool(column.get("nullable")) is not nullable:
      raise RuntimeError(
        f"precreated {table_name}.{name} has incompatible nullability"
      )
  actual_primary_key = tuple(
    inspector.get_pk_constraint(table_name).get("constrained_columns") or ()
  )
  if actual_primary_key != primary_key:
    raise RuntimeError(
      f"precreated {table_name} has incompatible primary key {actual_primary_key!r}"
    )
  actual_indexes = {
    str(index["name"]): tuple(index.get("column_names") or ())
    for index in inspector.get_indexes(table_name)
    if not index.get("duplicates_constraint")
  }
  if actual_indexes != indexes:
    raise RuntimeError(
      f"precreated {table_name} has incompatible indexes: "
      f"expected={indexes!r}, actual={actual_indexes!r}"
    )
  actual_unique_constraints = {
    str(constraint["name"]): tuple(constraint.get("column_names") or ())
    for constraint in inspector.get_unique_constraints(table_name)
  }
  if actual_unique_constraints != unique_constraints:
    raise RuntimeError(
      f"precreated {table_name} has incompatible unique constraints: "
      f"expected={unique_constraints!r}, actual={actual_unique_constraints!r}"
    )


def _adopt_precreated_managed_plan_tables() -> bool:
  inspector = inspect(op.get_bind())
  expected = {"managed_plans", "managed_plan_config_revisions"}
  existing = expected & set(inspector.get_table_names())
  if not existing:
    return False
  if existing != expected:
    raise RuntimeError(
      f"managed plan schema is partially precreated: {sorted(existing)}"
    )
  _validate_precreated_table(
    inspector,
    "managed_plans",
    _MANAGED_PLAN_COLUMN_SPECS,
    primary_key=("plan_id",),
    indexes={
      "ix_managed_plan_account_kind_status": (
        "account_id",
        "plan_kind",
        "status",
      ),
      "ix_managed_plan_current_run": ("current_run_id",),
    },
    unique_constraints={},
  )
  _validate_precreated_table(
    inspector,
    "managed_plan_config_revisions",
    _MANAGED_REVISION_COLUMN_SPECS,
    primary_key=("revision_id",),
    indexes={
      "ix_managed_plan_revision_plan_created": ("plan_id", "created_at"),
    },
    unique_constraints={
      "uq_managed_plan_config_revision": ("plan_id", "config_version"),
      "uq_managed_plan_revision_run": ("run_id",),
    },
  )
  return True


def _normalize_precreated_managed_plan_tables() -> None:
  for name in ("created_at", "updated_at"):
    op.alter_column(
      "managed_plans",
      name,
      existing_type=sa.DateTime(),
      server_default=sa.func.now(),
    )
  op.create_table_comment(
    "managed_plans",
    "买入与卖出托管计划稳定业务身份",
  )
  op.create_table_comment(
    "managed_plan_config_revisions",
    "托管计划不可变配置版本与运行绑定",
  )


def upgrade() -> None:
  if _adopt_precreated_managed_plan_tables():
    _normalize_precreated_managed_plan_tables()
  else:
    op.create_table(
      "managed_plans",
      sa.Column("plan_id", sa.String(length=128), primary_key=True),
      sa.Column("plan_kind", sa.String(length=16), nullable=False),
      sa.Column("account_id", sa.String(length=50), nullable=False),
      sa.Column("instrument_code", sa.String(length=20), nullable=False),
      sa.Column("status", sa.String(length=32), nullable=False),
      sa.Column("current_config_version", sa.Integer(), nullable=False),
      sa.Column("current_run_id", sa.String(length=36), nullable=True),
      sa.Column("last_command_id", sa.String(length=128), nullable=True),
      sa.Column("last_error", sa.Text(), nullable=True),
      sa.Column(
        "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
      ),
      sa.Column(
        "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
      ),
      comment="买入与卖出托管计划稳定业务身份",
    )
    op.create_index(
      "ix_managed_plan_account_kind_status",
      "managed_plans",
      ["account_id", "plan_kind", "status"],
    )
    op.create_index(
      "ix_managed_plan_current_run",
      "managed_plans",
      ["current_run_id"],
    )
    op.create_table(
      "managed_plan_config_revisions",
      sa.Column("revision_id", sa.String(length=36), primary_key=True),
      sa.Column("plan_id", sa.String(length=128), nullable=False),
      sa.Column("config_version", sa.Integer(), nullable=False),
      sa.Column("config_snapshot", sa.JSON(), nullable=False),
      sa.Column("config_fingerprint", sa.String(length=64), nullable=False),
      sa.Column("state_migration_policy", sa.String(length=48), nullable=False),
      sa.Column("supersedes_run_id", sa.String(length=36), nullable=True),
      sa.Column("run_id", sa.String(length=36), nullable=True),
      sa.Column("created_by_user_id", sa.String(length=50), nullable=True),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint(
        "plan_id",
        "config_version",
        name="uq_managed_plan_config_revision",
      ),
      sa.UniqueConstraint("run_id", name="uq_managed_plan_revision_run"),
      comment="托管计划不可变配置版本与运行绑定",
    )
    op.create_index(
      "ix_managed_plan_revision_plan_created",
      "managed_plan_config_revisions",
      ["plan_id", "created_at"],
    )

  strategy_run_inspector = inspect(op.get_bind())
  existing_strategy_columns = {
    column["name"]
    for column in strategy_run_inspector.get_columns("strategy_runs")
  }
  for column in (
    sa.Column("plan_id", sa.String(length=128), nullable=True),
    sa.Column("plan_kind", sa.String(length=16), nullable=True),
    sa.Column("plan_config_version", sa.Integer(), nullable=True),
    sa.Column("frozen_config_snapshot", sa.JSON(), nullable=True),
    sa.Column("frozen_config_fingerprint", sa.String(length=64), nullable=True),
    sa.Column("supersedes_run_id", sa.String(length=36), nullable=True),
    sa.Column("parent_run_id", sa.String(length=36), nullable=True),
    sa.Column("input_event_watermark", sa.String(length=128), nullable=True),
  ):
    if column.name not in existing_strategy_columns:
      op.add_column("strategy_runs", column)
  existing_strategy_indexes = {
    str(index["name"])
    for index in strategy_run_inspector.get_indexes("strategy_runs")
  }
  if "ix_strategy_runs_plan_id" not in existing_strategy_indexes:
    op.create_index("ix_strategy_runs_plan_id", "strategy_runs", ["plan_id"])

  authorization_checks = {
    str(constraint["name"])
    for constraint in inspect(op.get_bind()).get_check_constraints(
      "entry_plan_authorization_grants"
    )
  }
  if "ck_entry_plan_auth_plan_run" in authorization_checks:
    op.drop_constraint(
      "ck_entry_plan_auth_plan_run",
      "entry_plan_authorization_grants",
      type_="check",
    )

  # Existing entry plans remain usable. Their stable plan identity starts as
  # the old run id, while all subsequent config updates create a fresh run.
  op.execute(
    sa.text(
      """
      WITH entry_runs AS (
        SELECT
          sr.id,
          sr.status::text AS status,
          sr.created_at,
          CASE
            WHEN jsonb_typeof(sr.parameters::jsonb) = 'string'
              THEN (sr.parameters #>> '{}')::jsonb
            ELSE sr.parameters::jsonb
          END AS params
        FROM strategy_runs sr
        JOIN strategies s ON s.id = sr.strategy_id
        WHERE s.class_name = 'AshareManagedEntryPlanStrategy'
          AND sr.mode::text <> 'BACKTEST'
      )
      INSERT INTO managed_plans (
        plan_id, plan_kind, account_id, instrument_code, status,
        current_config_version, current_run_id, created_at, updated_at
      )
      SELECT
        id,
        'ENTRY',
        COALESCE(params->>'account_id', ''),
        COALESCE(params->'managed_entry_plan'->>'instrument_code', ''),
        status,
        COALESCE((params->'managed_entry_plan'->>'config_version')::integer, 1),
        id,
        created_at,
        CURRENT_TIMESTAMP
      FROM entry_runs
      """
    )
  )
  op.execute(
    sa.text(
      """
      WITH entry_runs AS (
        SELECT
          sr.id,
          sr.created_at,
          CASE
            WHEN jsonb_typeof(sr.parameters::jsonb) = 'string'
              THEN (sr.parameters #>> '{}')::jsonb
            ELSE sr.parameters::jsonb
          END AS params
        FROM strategy_runs sr
        JOIN strategies s ON s.id = sr.strategy_id
        WHERE s.class_name = 'AshareManagedEntryPlanStrategy'
          AND sr.mode::text <> 'BACKTEST'
      )
      INSERT INTO managed_plan_config_revisions (
        revision_id, plan_id, config_version, config_snapshot,
        config_fingerprint, state_migration_policy, run_id, created_at
      )
      SELECT
        'migr-' || substr(md5(id || ':' || created_at::text), 1, 31),
        id,
        COALESCE((params->'managed_entry_plan'->>'config_version')::integer, 1),
        params->'managed_entry_plan',
        md5((params->'managed_entry_plan')::text)
          || md5('managed-plan:' || (params->'managed_entry_plan')::text),
        'MIGRATED_IN_PLACE',
        id,
        created_at
      FROM entry_runs
      """
    )
  )
  op.execute(
    sa.text(
      """
      UPDATE strategy_runs sr
      SET plan_id = mp.plan_id,
          plan_kind = mp.plan_kind,
          plan_config_version = mp.current_config_version,
          frozen_config_snapshot = rev.config_snapshot,
          -- The legacy JSON payload did not use the canonical serializer that
          -- computes new revision fingerprints. Keep the migrated run usable
          -- for gate-only updates; its first real config edit creates a fully
          -- frozen replacement run through ManagedPlanRuntimeService.
          frozen_config_fingerprint = NULL
      FROM managed_plans mp
      JOIN managed_plan_config_revisions rev
        ON rev.plan_id = mp.plan_id
       AND rev.config_version = mp.current_config_version
      WHERE sr.id = mp.current_run_id
      """
    )
  )

  # Existing exit and conditional plans used a second monitor. They are
  # intentionally stopped and must be rebuilt as independent strategy runs.
  op.execute(
    sa.text(
      """
      UPDATE auto_exit_plans
      SET enabled = FALSE,
          status = 'CANCELLED',
          last_error = 'MIGRATION_REBUILD_REQUIRED'
      WHERE enabled = TRUE
        AND status NOT IN ('COMPLETED', 'CANCELLED')
      """
    )
  )
  op.execute(
    sa.text(
      """
      INSERT INTO auto_exit_plan_events (
        event_id, business_key, plan_id, event_type, payload, created_at
      )
      SELECT
        'migrate-' || substr(md5(plan_id), 1, 28),
        'managed-runtime-migration:' || plan_id,
        plan_id,
        'PLAN_STOPPED_FOR_MANAGED_RUNTIME_MIGRATION',
        json_build_object('rebuildRequired', TRUE),
        CURRENT_TIMESTAMP
      FROM auto_exit_plans
      WHERE last_error = 'MIGRATION_REBUILD_REQUIRED'
      ON CONFLICT (business_key) DO NOTHING
      """
    )
  )


def downgrade() -> None:
  op.create_check_constraint(
    "ck_entry_plan_auth_plan_run",
    "entry_plan_authorization_grants",
    "plan_id = run_id",
  )
  op.drop_index("ix_strategy_runs_plan_id", table_name="strategy_runs")
  for column_name in (
    "input_event_watermark",
    "parent_run_id",
    "supersedes_run_id",
    "frozen_config_fingerprint",
    "frozen_config_snapshot",
    "plan_config_version",
    "plan_kind",
    "plan_id",
  ):
    op.drop_column("strategy_runs", column_name)
  op.drop_index(
    "ix_managed_plan_revision_plan_created",
    table_name="managed_plan_config_revisions",
  )
  op.drop_table("managed_plan_config_revisions")
  op.drop_index("ix_managed_plan_current_run", table_name="managed_plans")
  op.drop_index("ix_managed_plan_account_kind_status", table_name="managed_plans")
  op.drop_table("managed_plans")
