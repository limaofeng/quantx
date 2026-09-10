"""Durable day planning and original-demand linkage for archive recovery."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260910_0088"
down_revision = "20260910_0087"
branch_labels = None
depends_on = None


def upgrade():
  op.add_column(
    "engine_archive_scope", sa.Column("ended_at", sa.DateTime(timezone=True))
  )
  op.add_column("engine_archive_scope", sa.Column("next_day", sa.Date))
  op.execute(
    "UPDATE engine_archive_scope SET next_day=(start_minute AT TIME ZONE 'Asia/Shanghai')::date"
  )
  op.alter_column("engine_archive_scope", "next_day", nullable=False)
  op.add_column(
    "engine_archive_scope",
    sa.Column(
      "next_probe_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
  )
  op.add_column("engine_archive_scope", sa.Column("reason", sa.String(64)))
  op.create_index("ix_archive_scope_due", "engine_archive_scope", ["next_probe_at"])
  op.create_table(
    "engine_archive_recovery",
    sa.Column("generation", sa.BigInteger, primary_key=True),
    sa.Column("instrument", sa.String(20), primary_key=True),
    sa.Column("trading_date", sa.Date, primary_key=True),
    sa.Column(
      "demand_id", sa.String(64), sa.ForeignKey("market_data_demand.demand_id")
    ),
    sa.Column("state", sa.String(16), nullable=False),
    sa.Column("evidence", JSONB),
    sa.Column(
      "created_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.ForeignKeyConstraint(
      ["generation", "instrument"],
      ["engine_archive_scope.generation", "engine_archive_scope.instrument"],
    ),
    sa.CheckConstraint("state IN ('WAITING','NO_SESSION')"),
    sa.CheckConstraint(
      "(state='WAITING' AND demand_id IS NOT NULL AND evidence IS NULL) OR (state='NO_SESSION' AND demand_id IS NULL AND evidence IS NOT NULL)"
    ),
    sa.CheckConstraint("evidence IS NULL OR octet_length(evidence::text)<=131072"),
  )


def downgrade():
  if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM engine_archive_scope)")):
    raise RuntimeError("cannot remove archive recovery evidence")
  op.drop_table("engine_archive_recovery")
  op.drop_index("ix_archive_scope_due", table_name="engine_archive_scope")
  for column in ("reason", "next_probe_at", "next_day", "ended_at"):
    op.drop_column("engine_archive_scope", column)
