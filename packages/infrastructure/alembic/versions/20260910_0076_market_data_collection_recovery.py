"""Retain the explicit recovery reason on the original aborted permit."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0076"
down_revision = "20260910_0075"
branch_labels = None
depends_on = None


def upgrade():
  op.add_column(
    "market_data_collection_permit", sa.Column("resumed_at", sa.DateTime(timezone=True))
  )
  op.add_column("market_data_collection_permit", sa.Column("resume_reason", sa.Text()))
  op.create_check_constraint(
    "ck_collection_recovery_evidence",
    "market_data_collection_permit",
    "(resumed_at IS NULL AND resume_reason IS NULL) OR "
    "(state='ABORTED' AND resumed_at IS NOT NULL AND resume_reason IS NOT NULL "
    "AND length(btrim(resume_reason)) BETWEEN 1 AND 256)",
  )


def downgrade():
  if op.get_bind().scalar(
    sa.text(
      "SELECT EXISTS(SELECT 1 FROM market_data_collection_permit WHERE resumed_at IS NOT NULL)"
    )
  ):
    raise RuntimeError("cannot remove persisted collection recovery evidence")
  op.drop_constraint(
    "ck_collection_recovery_evidence", "market_data_collection_permit", type_="check"
  )
  op.drop_column("market_data_collection_permit", "resume_reason")
  op.drop_column("market_data_collection_permit", "resumed_at")
