"""Add durable lifecycle projections for exit-plan historical replays."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260825_0032"
down_revision = "20260823_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
  bind = op.get_bind()
  if "exit_plan_replay_projections" in set(inspect(bind).get_table_names()):
    return
  op.create_table(
    "exit_plan_replay_projections",
    sa.Column(
      "run_id",
      sa.String(length=36),
      sa.ForeignKey("strategy_runs.id", ondelete="CASCADE"),
      primary_key=True,
    ),
    sa.Column("account_id", sa.String(length=50), nullable=False),
    sa.Column("plan_id", sa.String(length=128), nullable=True),
    sa.Column("instrument_code", sa.String(length=20), nullable=False),
    sa.Column("status", sa.String(length=20), nullable=False),
    sa.Column("progress_pct", sa.Float(), nullable=False, server_default="0"),
    sa.Column("processed_until", sa.DateTime(), nullable=True),
    sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    comment="卖出计划历史回放生命周期投影",
  )
  op.create_index(
    "ix_exit_plan_replay_projections_account_id",
    "exit_plan_replay_projections",
    ["account_id"],
  )
  op.create_index(
    "ix_exit_plan_replay_projections_plan_id",
    "exit_plan_replay_projections",
    ["plan_id"],
  )
  op.create_index(
    "ix_exit_plan_replay_projections_instrument_code",
    "exit_plan_replay_projections",
    ["instrument_code"],
  )
  op.create_index(
    "ix_exit_plan_replay_account_status",
    "exit_plan_replay_projections",
    ["account_id", "status"],
  )


def downgrade() -> None:
  op.drop_index(
    "ix_exit_plan_replay_account_status",
    table_name="exit_plan_replay_projections",
  )
  op.drop_index(
    "ix_exit_plan_replay_projections_instrument_code",
    table_name="exit_plan_replay_projections",
  )
  op.drop_index(
    "ix_exit_plan_replay_projections_plan_id",
    table_name="exit_plan_replay_projections",
  )
  op.drop_index(
    "ix_exit_plan_replay_projections_account_id",
    table_name="exit_plan_replay_projections",
  )
  op.drop_table("exit_plan_replay_projections")
