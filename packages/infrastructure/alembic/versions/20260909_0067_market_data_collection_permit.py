"""Persist native collection grants and completed-unit fairness accounting."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260909_0067"
down_revision = "20260909_0066"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "market_data_collection_schedule",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("production_streak", sa.Integer(), nullable=False, server_default="0"),
    sa.CheckConstraint("id = 1 AND production_streak BETWEEN 0 AND 4"),
  )
  op.create_table(
    "market_data_collection_permit",
    sa.Column("permit_id", sa.String(36), primary_key=True),
    sa.Column(
      "request_id",
      sa.String(36),
      sa.ForeignKey("market_data_request.request_id"),
      nullable=False,
    ),
    sa.Column("unit_index", sa.Integer(), nullable=False),
    sa.CheckConstraint("unit_index >= 0"),
    sa.Column("unit_id", sa.String(64), nullable=False),
    sa.Column("permit_payload", postgresql.JSONB(), nullable=False),
    sa.Column("development_only", sa.Boolean(), nullable=False),
    sa.Column("state", sa.String(16), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint("state IN ('ISSUED','STARTED','FINISHED','EXPIRED')"),
    sa.CheckConstraint(
      "(state IN ('ISSUED','EXPIRED') AND started_at IS NULL AND finished_at IS NULL) OR (state = 'STARTED' AND started_at IS NOT NULL AND finished_at IS NULL) OR (state = 'FINISHED' AND started_at IS NOT NULL AND finished_at IS NOT NULL)"
    ),
  )
  op.create_index(
    "uq_market_data_collection_active",
    "market_data_collection_permit",
    [sa.text("(1)")],
    unique=True,
    postgresql_where=sa.text("state IN ('ISSUED','STARTED')"),
  )
  op.create_index(
    "uq_market_data_collection_unit",
    "market_data_collection_permit",
    ["request_id", "unit_index"],
    unique=True,
    postgresql_where=sa.text("state <> 'EXPIRED'"),
  )


def downgrade():
  op.drop_table("market_data_collection_permit")
  op.drop_table("market_data_collection_schedule")
