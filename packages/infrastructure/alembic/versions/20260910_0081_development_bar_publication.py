"""Bind a fixed development partition to its immutable storage proof."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260910_0081"
down_revision = "20260910_0080"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "development_data_bar_version",
    sa.Column(
      "delivery_id",
      sa.String(64),
      sa.ForeignKey("development_data_export.id"),
      primary_key=True,
    ),
    sa.Column("stock_code", sa.String(20), nullable=False),
    sa.Column("period", sa.String(4), nullable=False),
    sa.Column("trading_date", sa.Date(), nullable=False),
    sa.Column("source_version", sa.String(64), nullable=False),
    sa.Column("storage_version", sa.String(64), nullable=False),
    sa.Column("content_sha256", sa.String(64), nullable=False),
    sa.Column("records", sa.Integer(), nullable=False),
    sa.Column("proof", JSONB()),
    sa.Column("verified_at", sa.DateTime(timezone=True)),
    sa.UniqueConstraint(
      "stock_code", "period", "trading_date", name="uq_development_bar_partition"
    ),
    sa.CheckConstraint(
      "period IN ('tick','1m','1d') AND records > 0", name="ck_development_bar_scope"
    ),
    sa.CheckConstraint(
      "source_version ~ '^[0-9a-f]{64}$' AND storage_version ~ '^[0-9a-f]{64}$' AND content_sha256 ~ '^[0-9a-f]{64}$'",
      name="ck_development_bar_hashes",
    ),
    sa.CheckConstraint(
      "(proof IS NULL) = (verified_at IS NULL)", name="ck_development_bar_proof_pair"
    ),
  )


def downgrade():
  if op.get_bind().scalar(
    sa.text("SELECT EXISTS(SELECT 1 FROM development_data_bar_version)")
  ):
    raise RuntimeError("cannot remove fixed development storage versions")
  op.drop_table("development_data_bar_version")
