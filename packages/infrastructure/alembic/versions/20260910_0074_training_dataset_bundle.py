"""Bind certified training inputs to a verified remote bundle."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0074"
down_revision = "20260910_0073"
branch_labels = None
depends_on = None


def upgrade():
  op.add_column(
    "stock_selection_dataset_versions",
    sa.Column("source_bundle", sa.JSON(), nullable=True),
  )


def downgrade():
  op.drop_column("stock_selection_dataset_versions", "source_bundle")
