"""Add versioned non-secret AI Runtime configuration."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260814_0014"
down_revision = "20260814_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
  bind = op.get_bind()
  inspector = inspect(bind)
  tables = set(inspector.get_table_names())

  if "ai_runtime_settings" not in tables:
    op.create_table(
      "ai_runtime_settings",
      sa.Column("id", sa.String(32), primary_key=True),
      sa.Column("config_version", sa.Integer(), nullable=False),
      sa.Column("enabled", sa.Boolean(), nullable=False),
      sa.Column("model", sa.String(120), nullable=False),
      sa.Column("max_concurrent_runs", sa.Integer(), nullable=False),
      sa.Column("max_turns", sa.Integer(), nullable=False),
      sa.Column("max_tool_calls", sa.Integer(), nullable=False),
      sa.Column("run_timeout_seconds", sa.Integer(), nullable=False),
      sa.Column("updated_by_user_id", sa.String(36), nullable=False),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.CheckConstraint(
        "max_concurrent_runs BETWEEN 1 AND 16",
        name="ck_ai_runtime_settings_concurrency",
      ),
      sa.CheckConstraint(
        "max_turns BETWEEN 1 AND 64",
        name="ck_ai_runtime_settings_turns",
      ),
      sa.CheckConstraint(
        "max_tool_calls BETWEEN 1 AND 64",
        name="ck_ai_runtime_settings_tool_calls",
      ),
      sa.CheckConstraint(
        "run_timeout_seconds BETWEEN 30 AND 3600",
        name="ck_ai_runtime_settings_timeout",
      ),
      comment="AI Runtime 全局非敏感动态配置",
    )

  if "ai_runtime_settings_audits" not in tables:
    op.create_table(
      "ai_runtime_settings_audits",
      sa.Column("id", sa.String(36), primary_key=True),
      sa.Column("config_version", sa.Integer(), nullable=False),
      sa.Column("previous_values", sa.JSON(), nullable=False),
      sa.Column("next_values", sa.JSON(), nullable=False),
      sa.Column("user_id", sa.String(36), nullable=False),
      sa.Column("request_id", sa.String(64), nullable=False),
      sa.Column("occurred_at", sa.DateTime(), nullable=False),
      comment="AI Runtime 配置变更审计",
    )
    op.create_index(
      "ix_ai_runtime_settings_audits_config_version",
      "ai_runtime_settings_audits",
      ["config_version"],
    )
    op.create_index(
      "ix_ai_runtime_settings_audits_user_id",
      "ai_runtime_settings_audits",
      ["user_id"],
    )
    op.create_index(
      "ix_ai_runtime_settings_audits_occurred_at",
      "ai_runtime_settings_audits",
      ["occurred_at"],
    )

  run_columns = {
    column["name"] for column in inspector.get_columns("ai_assistant_runs")
  }
  if "runtime_config_version" not in run_columns:
    op.add_column(
      "ai_assistant_runs",
      sa.Column(
        "runtime_config_version",
        sa.Integer(),
        nullable=False,
        server_default="0",
      ),
    )
  if "runtime_config_snapshot" not in run_columns:
    op.add_column(
      "ai_assistant_runs",
      sa.Column(
        "runtime_config_snapshot",
        sa.JSON(),
        nullable=False,
        server_default="{}",
      ),
    )
  op.alter_column(
    "ai_assistant_runs",
    "model",
    existing_type=sa.String(80),
    type_=sa.String(120),
    existing_nullable=False,
  )


def downgrade() -> None:
  op.alter_column(
    "ai_assistant_runs",
    "model",
    existing_type=sa.String(120),
    type_=sa.String(80),
    existing_nullable=False,
  )
  op.drop_column("ai_assistant_runs", "runtime_config_snapshot")
  op.drop_column("ai_assistant_runs", "runtime_config_version")
  op.drop_table("ai_runtime_settings_audits")
  op.drop_table("ai_runtime_settings")
