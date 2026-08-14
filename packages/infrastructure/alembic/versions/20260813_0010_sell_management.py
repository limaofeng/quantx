"""Unify persistent exit plans and plan-owned trade intents."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260813_0010"
down_revision = "20260813_0009"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> dict[str, dict]:
  inspector = inspect(op.get_bind())
  if table_name not in set(inspector.get_table_names()):
    return {}
  return {str(item["name"]): item for item in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
  inspector = inspect(op.get_bind())
  if table_name not in set(inspector.get_table_names()):
    return set()
  return {str(item["name"]) for item in inspector.get_indexes(table_name)}


def upgrade() -> None:
  plan_columns = _columns("auto_exit_plans")
  if plan_columns:
    additions = (
      sa.Column("group_id", sa.String(length=36), nullable=True),
      sa.Column("completion_strategy", sa.String(length=32), nullable=True),
    )
    for column in additions:
      if column.name not in plan_columns:
        op.add_column("auto_exit_plans", column)
    plan_indexes = _index_names("auto_exit_plans")
    if "ix_auto_exit_plan_group" not in plan_indexes:
      op.create_index(
        "ix_auto_exit_plan_group",
        "auto_exit_plans",
        ["group_id", "created_at"],
      )
    if "ix_auto_exit_plan_capacity" not in plan_indexes:
      op.create_index(
        "ix_auto_exit_plan_capacity",
        "auto_exit_plans",
        ["account_id", "instrument_code", "status"],
      )

  intent_columns = _columns("strategy_trade_intents")
  if intent_columns:
    for column in (
      sa.Column(
        "owner_type",
        sa.String(length=32),
        nullable=False,
        server_default="STRATEGY_RUN",
      ),
      sa.Column("owner_id", sa.String(length=128), nullable=False, server_default=""),
      sa.Column("account_id", sa.String(length=50), nullable=True),
    ):
      if column.name not in intent_columns:
        op.add_column("strategy_trade_intents", column)
    if not bool(intent_columns.get("strategy_run_id", {}).get("nullable", True)):
      with op.batch_alter_table("strategy_trade_intents") as batch:
        batch.alter_column(
          "strategy_run_id",
          existing_type=sa.String(length=36),
          nullable=True,
        )
    intent_indexes = _index_names("strategy_trade_intents")
    if "ix_trade_intent_owner_created" not in intent_indexes:
      op.create_index(
        "ix_trade_intent_owner_created",
        "strategy_trade_intents",
        ["owner_type", "owner_id", "created_at"],
      )
    if "ix_trade_intent_account_status_created" not in intent_indexes:
      op.create_index(
        "ix_trade_intent_account_status_created",
        "strategy_trade_intents",
        ["account_id", "status", "created_at"],
      )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
