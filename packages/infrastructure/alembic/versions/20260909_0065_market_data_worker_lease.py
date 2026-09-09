"""Fence the independent historical worker with one database lease."""

import sqlalchemy as sa
from alembic import op

revision = "20260909_0065"
down_revision = "20260909_0064"
branch_labels = None
depends_on = None


def upgrade():
  op.add_column(
    "market_data_request",
    sa.Column("processing_worker_epoch", sa.BigInteger(), nullable=True),
  )
  op.create_table(
    "market_data_worker_lease",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("owner_id", sa.String(36), nullable=False),
    sa.Column("epoch", sa.BigInteger(), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("id = 1 AND epoch > 0", name="ck_market_data_single_worker"),
  )


def downgrade():
  op.drop_table("market_data_worker_lease")
  op.drop_column("market_data_request", "processing_worker_epoch")
