"""Preserve local delivery write checkpoints and retry evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260910_0080"
down_revision = "20260910_0079"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "development_data_ingestion",
    sa.Column(
      "delivery_id",
      sa.String(64),
      sa.ForeignKey("development_data_export.id"),
      primary_key=True,
    ),
    sa.Column("claim_token", sa.String(36), nullable=False),
    sa.Column("progress", JSONB(), nullable=False),
    sa.Column(
      "updated_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
  )


def downgrade():
  if op.get_bind().scalar(
    sa.text("SELECT EXISTS(SELECT 1 FROM development_data_ingestion)")
  ):
    raise RuntimeError("cannot remove persisted development ingestion evidence")
  op.drop_table("development_data_ingestion")
