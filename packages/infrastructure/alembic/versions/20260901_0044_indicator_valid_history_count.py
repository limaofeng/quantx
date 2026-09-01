"""Persist point-in-time valid history evidence for probability selection."""

import sqlalchemy as sa
from alembic import op

revision = "20260901_0044"
down_revision = "20260901_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.add_column(
    "indicator_snapshots",
    sa.Column("valid_history_count", sa.Integer(), nullable=True),
  )


def downgrade() -> None:
  raise RuntimeError("QuantX schema downgrades are intentionally disabled")
