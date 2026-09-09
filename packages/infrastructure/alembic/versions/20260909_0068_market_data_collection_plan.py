"""Persist a collection plan's identity and next native unit for fair selection."""

import sqlalchemy as sa
from alembic import op

revision = "20260909_0068"
down_revision = "20260909_0067"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "market_data_collection_plan",
    sa.Column(
      "request_id",
      sa.String(36),
      sa.ForeignKey("market_data_request.request_id"),
      primary_key=True,
    ),
    sa.Column("plan_version", sa.String(32), nullable=False),
    sa.Column("payload_sha256", sa.String(64), nullable=False),
    sa.Column("unit_count", sa.Integer(), nullable=False),
    sa.Column("next_unit_index", sa.Integer(), nullable=False),
    sa.CheckConstraint(
      "unit_count BETWEEN 1 AND 2048 AND next_unit_index BETWEEN 0 AND unit_count"
    ),
  )

  op.create_index(
    "ix_market_data_collection_request_units",
    "market_data_collection_permit",
    ["request_id", "unit_index"],
  )


def downgrade():
  op.drop_index(
    "ix_market_data_collection_request_units",
    table_name="market_data_collection_permit",
  )
  op.drop_table("market_data_collection_plan")
