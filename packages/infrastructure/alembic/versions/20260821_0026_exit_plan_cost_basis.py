"""Freeze exit-plan cost basis and persist capacity reconciliation state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260821_0026"
down_revision = "20260821_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
  columns = {
    column["name"] for column in inspect(op.get_bind()).get_columns("auto_exit_plans")
  }
  expected = {
    "cost_basis_mode",
    "cost_basis_snapshot",
    "capacity_status",
    "capacity_error",
  }
  if columns & expected:
    raise RuntimeError(
      "Partial exit-plan cost-basis schema detected: "
      f"columns={sorted(columns & expected)}"
    )
  op.add_column(
    "auto_exit_plans",
    sa.Column(
      "cost_basis_mode",
      sa.String(length=32),
      nullable=False,
      server_default="POSITION_AVERAGE_SNAPSHOT",
    ),
  )
  op.add_column(
    "auto_exit_plans",
    sa.Column("cost_basis_snapshot", sa.JSON(), nullable=False, server_default="{}"),
  )
  op.add_column(
    "auto_exit_plans",
    sa.Column(
      "capacity_status",
      sa.String(length=32),
      nullable=False,
      server_default="READY",
    ),
  )
  op.add_column(
    "auto_exit_plans",
    sa.Column("capacity_error", sa.Text(), nullable=True),
  )
  op.alter_column("auto_exit_plans", "cost_basis_mode", server_default=None)
  op.alter_column("auto_exit_plans", "cost_basis_snapshot", server_default=None)
  op.alter_column("auto_exit_plans", "capacity_status", server_default=None)


def downgrade() -> None:
  op.drop_column("auto_exit_plans", "capacity_error")
  op.drop_column("auto_exit_plans", "capacity_status")
  op.drop_column("auto_exit_plans", "cost_basis_snapshot")
  op.drop_column("auto_exit_plans", "cost_basis_mode")
