"""Add the partial order lookup index used by exit-plan cost basis."""

from __future__ import annotations

from alembic import op

revision = "20260821_0027"
down_revision = "20260821_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.execute(
    """
    CREATE INDEX ix_orders_exit_plan_cost_basis
    ON orders (
      account_id,
      stock_code,
      order_type,
      order_time DESC,
      order_id DESC
    )
    WHERE traded_volume > 0 AND traded_price > 0
    """
  )


def downgrade() -> None:
  op.drop_index("ix_orders_exit_plan_cost_basis", table_name="orders")
