"""Persist bounded public T order attempt identities (no new capacity ledger)."""

import sqlalchemy as sa
from alembic import op

revision = "20260906_0050"
down_revision = "20260903_0049"
branch_labels = None
depends_on = None


def upgrade():
  op.add_column("pending_trade_orders", sa.Column(
    "t_order_attempt", sa.Integer(), nullable=False, server_default="0",
  ))
  op.add_column("pending_trade_orders", sa.Column(
    "t_order_parent_client_id", sa.String(128), nullable=True,
  ))
  op.add_column("pending_trade_orders", sa.Column(
    "t_order_original_created_at", sa.DateTime(), nullable=True,
  ))
  op.create_foreign_key(
    "fk_t_order_parent", "pending_trade_orders", "pending_trade_orders",
    ["t_order_parent_client_id"], ["client_order_id"],
  )
  op.create_unique_constraint(
    "uq_t_order_parent_attempt", "pending_trade_orders", ["t_order_parent_client_id"],
  )
  op.create_check_constraint(
    "ck_t_order_attempt_nonnegative", "pending_trade_orders", "t_order_attempt >= 0",
  )
  op.create_index(
    "uq_t_order_intent_attempt", "pending_trade_orders",
    ["intent_id", "t_order_attempt"], unique=True,
    postgresql_where=sa.text("t_trade_role IN ('ENTRY','EXIT')"),
  )
  # Existing attempts were never eligible for replace; leave their original
  # lifecycle timestamp NULL rather than retroactively authorizing a retry.


def downgrade():
  raise RuntimeError("T order attempt evidence cannot be downgraded")
