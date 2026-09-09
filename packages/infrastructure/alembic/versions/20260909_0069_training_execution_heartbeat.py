"""Persist training execution liveness separately from progress and user versions."""

import sqlalchemy as sa
from alembic import op

revision = "20260909_0069"
down_revision = "20260909_0068"
branch_labels = None
depends_on = None


def upgrade():
  op.add_column(
    "stock_selection_training_runs",
    sa.Column("execution_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
  )


def downgrade():
  op.drop_column("stock_selection_training_runs", "execution_heartbeat_at")
