"""Index exact account snapshot evidence reads without scanning the report inbox."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260831_0041"
down_revision = "20260830_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_index(
    "ix_agent_report_snapshot_lookup",
    "agent_report_inbox",
    [
      "message_type",
      "protocol_version",
      sa.text("CAST(payload ->> 'snapshot_id' AS VARCHAR)"),
      sa.text("received_at DESC"),
    ],
  )


def downgrade() -> None:
  raise RuntimeError("QuantX schema downgrades are intentionally disabled")
