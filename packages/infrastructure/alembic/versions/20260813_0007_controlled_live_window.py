"""Add controlled live window state and rollout audit events."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260813_0007"
down_revision = "20260812_0006"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
  inspector = inspect(op.get_bind())
  if table_name not in set(inspector.get_table_names()):
    return set()
  return {str(value["name"]) for value in inspector.get_columns(table_name)}


def _add_column(table_name: str, column: sa.Column) -> None:
  if column.name not in _columns(table_name):
    op.add_column(table_name, column)


def upgrade() -> None:
  for name, column_type in (
    ("controlled_window_snapshot_id", sa.String(length=128)),
    ("controlled_window_snapshot_hash", sa.String(length=64)),
    ("controlled_window_started_at", sa.DateTime()),
    ("controlled_window_started_by_user_id", sa.String(length=36)),
  ):
    _add_column(
      "account_trading_rollouts",
      sa.Column(name, column_type, nullable=True),
    )
  _add_column(
    "account_trading_rollouts",
    sa.Column(
      "controlled_window_active",
      sa.Boolean(),
      nullable=False,
      server_default=sa.false(),
    ),
  )
  for name in (
    "controlled_window_external_order_ids",
    "controlled_window_external_trade_ids",
  ):
    _add_column(
      "account_trading_rollouts",
      sa.Column(name, sa.JSON(), nullable=False, server_default="[]"),
    )

  inspector = inspect(op.get_bind())
  if "account_trading_rollout_events" not in set(inspector.get_table_names()):
    op.create_table(
      "account_trading_rollout_events",
      sa.Column("event_id", sa.String(length=36), primary_key=True),
      sa.Column("account_id", sa.String(length=50), nullable=False),
      sa.Column("event_type", sa.String(length=64), nullable=False),
      sa.Column("actor_user_id", sa.String(length=36), nullable=True),
      sa.Column("previous_stage", sa.String(length=24), nullable=True),
      sa.Column("next_stage", sa.String(length=24), nullable=True),
      sa.Column("snapshot_id", sa.String(length=128), nullable=True),
      sa.Column("details", sa.JSON(), nullable=False),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      comment="证券账户实盘灰度操作审计事件",
    )
    op.create_index(
      "ix_account_trading_rollout_events_account_id",
      "account_trading_rollout_events",
      ["account_id"],
    )
    op.create_index(
      "ix_account_trading_rollout_event_account_created",
      "account_trading_rollout_events",
      ["account_id", "created_at"],
    )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
