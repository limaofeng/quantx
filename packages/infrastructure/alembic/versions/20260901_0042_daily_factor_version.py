"""Version daily factor snapshots without promoting old calculations."""

import sqlalchemy as sa
from alembic import op

revision = "20260901_0042"
down_revision = "20260831_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.add_column(
    "indicator_snapshots",
    sa.Column("calculation_version", sa.String(40), nullable=True),
  )
  op.add_column(
    "indicator_snapshots", sa.Column("kdj_cross_up", sa.Float(), nullable=True)
  )
  op.add_column(
    "indicator_snapshots", sa.Column("ma_cross_up", sa.Float(), nullable=True)
  )
  op.add_column(
    "indicator_snapshots", sa.Column("boll_near_lower", sa.Float(), nullable=True)
  )
  op.add_column(
    "indicator_snapshots", sa.Column("boll_near_upper", sa.Float(), nullable=True)
  )
  op.create_index(
    "ix_indicator_snapshots_version_date",
    "indicator_snapshots",
    ["calculation_version", "snapshot_date"],
  )


def downgrade() -> None:
  raise RuntimeError("QuantX schema downgrades are intentionally disabled")
