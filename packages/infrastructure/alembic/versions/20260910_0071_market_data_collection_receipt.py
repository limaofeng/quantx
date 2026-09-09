"""Persist Agent native facts separately from Worker-owned permit transitions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260910_0071"
down_revision = "20260910_0070"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "market_data_collection_receipt",
    sa.Column(
      "permit_id",
      sa.String(36),
      sa.ForeignKey("market_data_collection_permit.permit_id"),
      primary_key=True,
    ),
    sa.Column("event", sa.String(8), primary_key=True),
    sa.Column("device_id", sa.String(36), nullable=False),
    sa.Column("payload", postgresql.JSONB(), nullable=False),
    sa.Column(
      "received_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.text("clock_timestamp()"),
    ),
    sa.Column("processed_at", sa.DateTime(timezone=True)),
    sa.Column("reason_code", sa.String(64)),
    sa.CheckConstraint("event IN ('START','FINISH')"),
    sa.CheckConstraint(
      "reason_code IS NULL OR (processed_at IS NOT NULL AND reason_code='COLLECTION_RECEIPT_REJECTED')"
    ),
  )
  op.create_index(
    "ix_market_data_collection_receipt_pending",
    "market_data_collection_receipt",
    ["received_at"],
    postgresql_where=sa.text("processed_at IS NULL"),
  )


def downgrade():
  op.drop_table("market_data_collection_receipt")
