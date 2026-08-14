"""Add durable limit-up radar lifecycle events."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260810_0004"
down_revision = "20260730_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
  if "limit_up_radar_events" in set(inspect(op.get_bind()).get_table_names()):
    return
  op.create_table(
    "limit_up_radar_events",
    sa.Column("event_id", sa.String(length=36), primary_key=True),
    sa.Column("trade_date", sa.Date(), nullable=False),
    sa.Column("instrument_code", sa.String(length=20), nullable=False),
    sa.Column("stage", sa.String(length=24), nullable=False),
    sa.Column("occurred_at", sa.DateTime(), nullable=False),
    sa.Column("score", sa.Float(), nullable=False, server_default="0"),
    sa.Column(
      "score_version",
      sa.String(length=40),
      nullable=False,
      server_default="limit-up-radar-v1",
    ),
    sa.Column("snapshot", sa.JSON(), nullable=False),
    comment="全市场打板雷达阶段事件",
  )
  op.create_index(
    "ix_limit_up_radar_events_trade_date",
    "limit_up_radar_events",
    ["trade_date"],
  )
  op.create_index(
    "ix_limit_up_radar_events_instrument_code",
    "limit_up_radar_events",
    ["instrument_code"],
  )
  op.create_index(
    "ix_limit_up_radar_event_date_code_time",
    "limit_up_radar_events",
    ["trade_date", "instrument_code", "occurred_at"],
  )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
