"""Track authenticated historical connections without business control heartbeats."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260910_0072"
down_revision = "20260910_0071"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "market_data_history_session",
    sa.Column("device_id", sa.String(36), primary_key=True),
    sa.Column("session_id", sa.String(36), nullable=False, unique=True),
    sa.Column("user_id", sa.String(36), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("heartbeat", postgresql.JSONB()),
    sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
    sa.Column("capabilities", postgresql.JSONB(), nullable=False),
    sa.CheckConstraint("expires_at <= token_expires_at"),
  )


def downgrade():
  op.drop_table("market_data_history_session")
