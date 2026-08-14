"""Add persistent adaptive automatic exit plans."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260813_0008"
down_revision = "20260813_0007"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
  inspector = inspect(op.get_bind())
  if table_name not in set(inspector.get_table_names()):
    return set()
  return {str(item["name"]) for item in inspector.get_columns(table_name)}


def upgrade() -> None:
  existing = _columns("conditional_liquidation_orders")
  for column in (
    sa.Column(
      "strategy",
      sa.String(length=48),
      nullable=False,
      server_default="IMMEDIATE",
    ),
    sa.Column("dynamic_policy", sa.JSON(), nullable=True),
    sa.Column("exit_plan_id", sa.String(length=128), nullable=True),
    sa.Column(
      "execution_mode",
      sa.String(length=16),
      nullable=False,
      server_default="paper",
    ),
    sa.Column(
      "auto_exit_authorized",
      sa.Boolean(),
      nullable=False,
      server_default=sa.false(),
    ),
  ):
    if column.name not in existing:
      op.add_column("conditional_liquidation_orders", column)
  inspector = inspect(op.get_bind())
  indexes = {
    str(item["name"])
    for item in inspector.get_indexes("conditional_liquidation_orders")
  }
  if "ix_conditional_liquidation_orders_exit_plan_id" not in indexes:
    op.create_index(
      "ix_conditional_liquidation_orders_exit_plan_id",
      "conditional_liquidation_orders",
      ["exit_plan_id"],
    )

  table_names = set(inspector.get_table_names())
  if "auto_exit_plans" not in table_names:
    op.create_table(
      "auto_exit_plans",
      sa.Column("plan_id", sa.String(length=128), primary_key=True),
      sa.Column("account_id", sa.String(length=50), nullable=False),
      sa.Column("instrument_code", sa.String(length=20), nullable=False),
      sa.Column("bucket", sa.String(length=32), nullable=False),
      sa.Column("source_type", sa.String(length=48), nullable=False),
      sa.Column("source_id", sa.String(length=128), nullable=False),
      sa.Column("strategy_run_id", sa.String(length=36), nullable=True),
      sa.Column("enabled", sa.Boolean(), nullable=False),
      sa.Column("status", sa.String(length=32), nullable=False),
      sa.Column("execution_mode", sa.String(length=16), nullable=False),
      sa.Column("auto_exit_authorized", sa.Boolean(), nullable=False),
      sa.Column("config_version", sa.Integer(), nullable=False),
      sa.Column("protected_volume", sa.Integer(), nullable=False),
      sa.Column("exited_volume", sa.Integer(), nullable=False),
      sa.Column("remaining_volume", sa.Integer(), nullable=False),
      sa.Column("entry_avg_price", sa.Float(), nullable=False),
      sa.Column("plan_state", sa.JSON(), nullable=False),
      sa.Column("phase", sa.String(length=32), nullable=False),
      sa.Column("data_quality", sa.String(length=32), nullable=False),
      sa.Column("last_decision", sa.String(length=64), nullable=True),
      sa.Column("peak_price", sa.Float(), nullable=False),
      sa.Column("peak_drawdown_pct", sa.Float(), nullable=False),
      sa.Column("volume_velocity", sa.Float(), nullable=True),
      sa.Column("weak_score", sa.Integer(), nullable=False),
      sa.Column("trailing_floor_pct", sa.Float(), nullable=True),
      sa.Column("pending_client_order_id", sa.String(length=128), nullable=True),
      sa.Column("last_evaluated_at", sa.DateTime(), nullable=True),
      sa.Column("last_error", sa.Text(), nullable=True),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint(
        "source_type", "source_id", name="uq_auto_exit_plan_source"
      ),
      comment="Engine 统一自动退出计划",
    )
    op.create_index("ix_auto_exit_plans_account_id", "auto_exit_plans", ["account_id"])
    op.create_index(
      "ix_auto_exit_plans_instrument_code",
      "auto_exit_plans",
      ["instrument_code"],
    )
    op.create_index(
      "ix_auto_exit_plans_strategy_run_id",
      "auto_exit_plans",
      ["strategy_run_id"],
    )
    op.create_index(
      "ix_auto_exit_plans_pending_client_order_id",
      "auto_exit_plans",
      ["pending_client_order_id"],
    )
    op.create_index(
      "ix_auto_exit_plan_monitor",
      "auto_exit_plans",
      ["enabled", "status", "instrument_code"],
    )

  if "auto_exit_plan_events" not in table_names:
    op.create_table(
      "auto_exit_plan_events",
      sa.Column("event_id", sa.String(length=36), primary_key=True),
      sa.Column("business_key", sa.String(length=256), nullable=False),
      sa.Column("plan_id", sa.String(length=128), nullable=False),
      sa.Column("event_type", sa.String(length=48), nullable=False),
      sa.Column("payload", sa.JSON(), nullable=False),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint(
        "business_key", name="uq_auto_exit_plan_event_business"
      ),
      comment="自动退出计划幂等事件与审计",
    )
    op.create_index(
      "ix_auto_exit_plan_events_plan_id",
      "auto_exit_plan_events",
      ["plan_id"],
    )
    op.create_index(
      "ix_auto_exit_plan_event_plan_created",
      "auto_exit_plan_events",
      ["plan_id", "created_at"],
    )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
