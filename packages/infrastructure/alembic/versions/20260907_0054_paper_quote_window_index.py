"""Index the bounded PAPER quote window used by point-in-time valuation.

Revision ID: 20260907_0054
Revises: 20260907_0053
"""

from alembic import op

revision = "20260907_0054"
down_revision = "20260907_0053"
branch_labels = None
depends_on = None


def upgrade():
  op.create_index(
    "ix_paper_event_scope_type_time",
    "paper_execution_events",
    ["execution_id", "event_type", "occurred_at"],
  )


def downgrade():
  op.drop_index("ix_paper_event_scope_type_time", table_name="paper_execution_events")
