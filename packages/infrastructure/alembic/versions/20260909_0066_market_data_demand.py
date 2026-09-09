"""Accept history demand before an online collection source exists."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260909_0066"
down_revision = "20260909_0065"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "market_data_demand",
    sa.Column("demand_id", sa.String(64), primary_key=True),
    sa.Column("partition", postgresql.JSONB(), nullable=False),
    sa.Column("source_kind", sa.String(16), nullable=False),
    sa.Column(
      "source_request_id",
      sa.String(36),
      sa.ForeignKey("market_data_request.request_id"),
    ),
    sa.Column(
      "delivery_id", sa.String(64), sa.ForeignKey("development_data_export.id")
    ),
    sa.Column("reason_code", sa.String(64)),
    sa.Column(
      "next_probe_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.Column(
      "created_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.Column(
      "last_progress_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
      "(source_kind = 'AGENT' AND delivery_id IS NULL) OR "
      "(source_kind = 'REMOTE' AND source_request_id IS NULL)",
      name="ck_market_data_demand_source",
    ),
  )
  op.create_index(
    "ix_market_data_demand_pending",
    "market_data_demand",
    ["next_probe_at", "created_at"],
    postgresql_where=sa.text("source_request_id IS NULL AND delivery_id IS NULL"),
  )


def downgrade():
  op.drop_table("market_data_demand")
