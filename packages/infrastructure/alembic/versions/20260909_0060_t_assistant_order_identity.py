"""Permit independent T-assistant pending/correlation identities.

Revision ID: 20260909_0060
Revises: 20260908_0059

No owner rewrites or LIVE activation. Existing strategy and exit identities keep
their original shape; a T-assistant order must never fabricate strategy IDs.
"""

from alembic import op

revision = "20260909_0060"
down_revision = "20260908_0059"
branch_labels = None
depends_on = None

ORDER_IDENTITY_CHECK = (
  "(owner_type = 'STRATEGY_RUN' AND intent_id IS NOT NULL "
  "AND strategy_order_id IS NOT NULL) OR "
  "(owner_type = 'T_ASSISTANT_EXECUTION' AND intent_id IS NOT NULL "
  "AND strategy_run_id IS NULL AND strategy_order_id IS NULL) OR "
  "(owner_type = 'EXIT_PLAN' AND intent_id IS NOT NULL "
  "AND strategy_order_id IS NULL) OR "
  "(owner_type = 'MANUAL_COMMAND' AND intent_id IS NULL "
  "AND strategy_order_id IS NULL)"
)


def upgrade():
  for table, name in (
    ("pending_trade_orders", "ck_pending_trade_order_strategy_identity"),
    ("order_correlations", "ck_order_correlation_strategy_identity"),
  ):
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, ORDER_IDENTITY_CHECK)


def downgrade():
  raise RuntimeError("T-assistant durable order identities cannot be removed")
