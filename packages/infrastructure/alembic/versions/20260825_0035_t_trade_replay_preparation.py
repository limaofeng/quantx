"""Persist T-trade replay portfolio/data preparation phases."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260825_0035"
down_revision = "20260825_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
  bind = op.get_bind()
  inspector = inspect(bind)
  if "t_trade_replay_projections" not in set(inspector.get_table_names()):
    return
  columns = {
    str(column["name"])
    for column in inspector.get_columns("t_trade_replay_projections")
  }
  if "phase" not in columns:
    op.add_column(
      "t_trade_replay_projections",
      sa.Column(
        "phase",
        sa.String(32),
        nullable=False,
        server_default="VALIDATING_PORTFOLIO",
      ),
    )
  if "phase_progress_pct" not in columns:
    op.add_column(
      "t_trade_replay_projections",
      sa.Column(
        "phase_progress_pct",
        sa.Float(),
        nullable=False,
        server_default="0",
      ),
    )
  if "phase_message" not in columns:
    op.add_column(
      "t_trade_replay_projections",
      sa.Column(
        "phase_message",
        sa.String(500),
        nullable=False,
        server_default="",
      ),
    )
  if "data_preparation" not in columns:
    op.add_column(
      "t_trade_replay_projections",
      sa.Column(
        "data_preparation",
        sa.JSON(),
        nullable=False,
        server_default=sa.text("'{}'"),
      ),
    )


def downgrade() -> None:
  bind = op.get_bind()
  inspector = inspect(bind)
  if "t_trade_replay_projections" not in set(inspector.get_table_names()):
    return
  columns = {
    str(column["name"])
    for column in inspector.get_columns("t_trade_replay_projections")
  }
  for column in (
    "data_preparation",
    "phase_message",
    "phase_progress_pct",
    "phase",
  ):
    if column in columns:
      op.drop_column("t_trade_replay_projections", column)
