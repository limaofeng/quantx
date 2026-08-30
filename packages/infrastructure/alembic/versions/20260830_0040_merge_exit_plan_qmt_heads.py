"""Merge the exit-plan and QMT dispatch migration branches."""

from __future__ import annotations

revision = "20260830_0040"
down_revision = ("20260829_0038", "20260829_0039")
branch_labels = None
depends_on = None


def upgrade() -> None:
  """Join both schema branches without changing data."""


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
