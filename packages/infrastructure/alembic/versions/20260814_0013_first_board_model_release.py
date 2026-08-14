"""Add the evidence gate for first-board model promotion."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260814_0013"
down_revision = "20260814_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
  bind = op.get_bind()
  tables = set(inspect(bind).get_table_names())
  if "first_board_model_releases" in tables:
    return

  op.create_table(
    "first_board_model_releases",
    sa.Column("model_version", sa.String(64), primary_key=True),
    sa.Column("exit_policy_version", sa.String(64), nullable=False),
    sa.Column("stage", sa.String(16), nullable=False, server_default="SHADOW"),
    sa.Column("sample_trading_days", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("main_board_eligible_samples", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("growth_board_eligible_samples", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("bootstrap_ci_lower_pct", sa.Float(), nullable=True),
    sa.Column("tail_loss_budget_passed", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("historical_rules_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("simulation_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("live_reconciliation_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("approved_by", sa.String(64), nullable=False, server_default=""),
    sa.Column("approved_at", sa.DateTime(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    comment="首板晋级模型发布证据门禁",
  )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
