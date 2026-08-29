"""Add query indexes for QMT command and market-data dispatch."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260829_0039"
down_revision = "20260829_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_index(
    "ix_trade_command_device_status_expiry_created",
    "trade_command_outbox",
    ["device_id", "delivery_status", "expires_at", "created_at"],
  )
  op.create_index(
    "ix_trade_command_device_status_delivery_expiry",
    "trade_command_outbox",
    ["device_id", "delivery_status", "delivered_at", "expires_at"],
  )
  op.create_index(
    "ix_trade_command_device_status_kind_created",
    "trade_command_outbox",
    [
      "device_id",
      "delivery_status",
      sa.text("upper(payload ->> 'command_kind')"),
      "created_at",
    ],
  )
  op.create_index(
    "ix_market_data_request_device_status_created",
    "market_data_request",
    ["device_id", "status", "created_at"],
  )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
