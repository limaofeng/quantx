"""Locate complete training artifacts independently of a compute host's paths."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0070"
down_revision = "20260909_0069"
branch_labels = None
depends_on = None


def upgrade():
  op.add_column("stock_selection_training_runs", sa.Column("artifact_bundle", sa.JSON(), nullable=True))


def downgrade():
  op.drop_column("stock_selection_training_runs", "artifact_bundle")
