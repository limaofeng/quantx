"""Recognize the retired GraphQL permission revision.

Revision ``20260816_0015`` was deployed briefly before the migration was
renumbered into the current linear chain as ``20260816_0020``.  Keeping this
empty branch lets Alembic upgrade databases stamped with the retired revision
without pretending that the intervening trading and iOS migrations ran.
"""

from __future__ import annotations

revision = "20260816_0015"
down_revision = "20260814_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
  pass


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
