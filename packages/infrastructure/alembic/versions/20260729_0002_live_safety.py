"""Add protocol, snapshot, dead-letter, and monotonic-order safety state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260729_0002"
down_revision = "20260729_0001"
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
  _add_column(
    "pending_trade_orders",
    sa.Column(
      "last_source_sequence",
      sa.BigInteger(),
      nullable=False,
      server_default="0",
    ),
  )
  _add_column(
    "pending_trade_orders",
    sa.Column("last_source_event_at", sa.DateTime(), nullable=True),
  )
  _add_column(
    "agent_report_inbox",
    sa.Column(
      "protocol_version",
      sa.String(length=16),
      nullable=False,
      server_default="1.0",
    ),
  )
  for name, column_type in (
    ("last_snapshot_id", sa.String(length=128)),
    ("last_snapshot_hash", sa.String(length=64)),
    ("last_snapshot_at", sa.DateTime()),
    ("last_backup_at", sa.DateTime()),
  ):
    _add_column(
      "account_trading_rollouts",
      sa.Column(name, column_type, nullable=True),
    )

  inspector = inspect(op.get_bind())
  if "operational_alerts" not in set(inspector.get_table_names()):
    op.create_table(
      "operational_alerts",
      sa.Column("id", sa.String(length=36), primary_key=True),
      sa.Column("fingerprint", sa.String(length=64), nullable=False, unique=True),
      sa.Column("severity", sa.String(length=16), nullable=False),
      sa.Column("source", sa.String(length=64), nullable=False),
      sa.Column("code", sa.String(length=64), nullable=False),
      sa.Column("account_id", sa.String(length=50), nullable=True),
      sa.Column("business_id", sa.String(length=192), nullable=True),
      sa.Column("message", sa.Text(), nullable=False),
      sa.Column("details", sa.JSON(), nullable=False),
      sa.Column("status", sa.String(length=24), nullable=False),
      sa.Column("occurrences", sa.Integer(), nullable=False),
      sa.Column("first_seen_at", sa.DateTime(), nullable=False),
      sa.Column("last_seen_at", sa.DateTime(), nullable=False),
      sa.Column("acknowledged_by", sa.String(length=36), nullable=True),
      sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
      sa.Column("resolved_by", sa.String(length=36), nullable=True),
      sa.Column("resolved_at", sa.DateTime(), nullable=True),
      sa.Column("resolution", sa.Text(), nullable=True),
    )
    op.create_index(
      "ix_operational_alert_status_severity_last_seen",
      "operational_alerts",
      ["status", "severity", "last_seen_at"],
    )
    op.create_index(
      "ix_operational_alert_account_status",
      "operational_alerts",
      ["account_id", "status"],
    )
    op.create_index(
      "ix_operational_alerts_account_id",
      "operational_alerts",
      ["account_id"],
    )
    op.create_index(
      "ix_operational_alerts_business_id",
      "operational_alerts",
      ["business_id"],
    )

  if op.get_bind().dialect.name == "postgresql":
    op.execute(
      """
      CREATE OR REPLACE FUNCTION quantx_reject_terminal_order_regression()
      RETURNS trigger AS $$
      BEGIN
        IF OLD.status IN (
          'FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED', 'KILL_SWITCHED'
        ) AND NEW.status NOT IN (
          'FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED', 'KILL_SWITCHED'
        ) THEN
          RAISE EXCEPTION 'terminal order status regression: % -> %',
            OLD.status, NEW.status;
        END IF;
        RETURN NEW;
      END;
      $$ LANGUAGE plpgsql;
      """
    )
    op.execute(
      """
      DROP TRIGGER IF EXISTS trg_pending_order_terminal_monotonic
        ON pending_trade_orders
      """
    )
    op.execute(
      """
      CREATE TRIGGER trg_pending_order_terminal_monotonic
      BEFORE UPDATE OF status ON pending_trade_orders
      FOR EACH ROW EXECUTE FUNCTION quantx_reject_terminal_order_regression()
      """
    )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
