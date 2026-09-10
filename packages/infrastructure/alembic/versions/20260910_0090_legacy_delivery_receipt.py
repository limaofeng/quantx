"""Retain original delivery evidence during the explicit v1 receipt upgrade."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260910_0090"
down_revision = "20260910_0089"
branch_labels = None
depends_on = None


def upgrade():
  op.add_column("development_data_export", sa.Column("legacy_manifest", JSONB()))


def downgrade():
  if op.get_bind().scalar(
    sa.text(
      "SELECT EXISTS(SELECT 1 FROM development_data_export WHERE legacy_manifest IS NOT NULL)"
    )
  ):
    raise RuntimeError("cannot remove retained legacy delivery evidence")
  op.drop_column("development_data_export", "legacy_manifest")
