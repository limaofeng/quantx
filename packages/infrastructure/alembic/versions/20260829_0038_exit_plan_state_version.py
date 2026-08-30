"""Add an optimistic state version to durable automatic exit plans."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260829_0038"
down_revision = "20260829_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.add_column(
    "auto_exit_plans",
    sa.Column(
      "state_version",
      sa.Integer(),
      nullable=False,
      server_default=sa.text("1"),
    ),
  )
  op.alter_column(
    "auto_exit_plans",
    "state_version",
    existing_type=sa.Integer(),
    nullable=False,
    server_default=None,
  )
  op.create_check_constraint(
    "ck_auto_exit_plan_state_version",
    "auto_exit_plans",
    "state_version >= 1",
  )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
