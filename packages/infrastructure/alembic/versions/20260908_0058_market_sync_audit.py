"""Persist market sync partition outcomes independently of the Flow process."""

import sqlalchemy as sa
from alembic import op

revision = "20260908_0058"
down_revision = "20260907_0057"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "market_data_sync_partition",
    sa.Column("run_id", sa.String(64), primary_key=True),
    sa.Column("batch_index", sa.Integer(), primary_key=True),
    sa.Column("scope", sa.JSON(), nullable=False),
    sa.Column("request_id", sa.String(36), nullable=False),
    sa.Column("coverage_status", sa.String(16), nullable=False),
    sa.Column("summary", sa.JSON(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.CheckConstraint(
      "coverage_status IN ('PENDING','VERIFIED','INCOMPLETE')",
      name="ck_market_sync_coverage_status",
    ),
    comment="历史行情同步分区覆盖审计",
  )


def downgrade():
  op.drop_table("market_data_sync_partition")
