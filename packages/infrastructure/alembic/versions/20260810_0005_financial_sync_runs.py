"""Add durable financial synchronization run audits."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260810_0005"
down_revision = "20260810_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
  if "financial_sync_runs" in set(inspect(op.get_bind()).get_table_names()):
    return
  op.create_table(
    "financial_sync_runs",
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("status", sa.String(length=32), nullable=False),
    sa.Column("started_at", sa.DateTime(), nullable=False),
    sa.Column("completed_at", sa.DateTime(), nullable=True),
    sa.Column("window_start", sa.Date(), nullable=False),
    sa.Column("window_end", sa.Date(), nullable=False),
    sa.Column("batch_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("failed_batches", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("requested_codes", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("synced_codes", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("empty_codes", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("statement_rows", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("metric_rows", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("warnings", sa.Text(), nullable=True),
    sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    comment="上市公司财务数据同步运行记录",
  )
  op.create_index(
    "ix_financial_sync_runs_status",
    "financial_sync_runs",
    ["status"],
  )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
