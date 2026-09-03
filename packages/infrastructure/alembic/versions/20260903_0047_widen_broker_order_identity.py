"""Widen broker order identifiers for vendor-generated values."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260903_0047"
down_revision = "20260903_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
  for table_name in ("orders", "trades"):
    op.alter_column(
      table_name,
      "order_sysid",
      existing_type=sa.String(length=10),
      type_=sa.String(length=32),
      existing_nullable=False,
    )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
