"""Retain original instrument detail results independently of the security master."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0077"
down_revision = "20260910_0076"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "market_data_instrument_snapshot",
    sa.Column("request_id", sa.String(36), primary_key=True),
    sa.Column("code", sa.String(19), primary_key=True),
    sa.Column("manifest_sha256", sa.String(64), nullable=False),
    sa.Column("content_sha256", sa.String(64), nullable=False),
    sa.Column("record_json", sa.Text(), nullable=False),
    sa.Column("schema_version", sa.Integer(), nullable=False),
    sa.CheckConstraint("schema_version = 1", name="ck_instrument_snapshot_version"),
    sa.CheckConstraint(
      "octet_length(record_json) BETWEEN 1 AND 65536",
      name="ck_instrument_snapshot_bytes",
    ),
  )


def downgrade():
  if op.get_bind().scalar(
    sa.text("SELECT EXISTS(SELECT 1 FROM market_data_instrument_snapshot)")
  ):
    raise RuntimeError("cannot remove persisted instrument snapshot evidence")
  op.drop_table("market_data_instrument_snapshot")
