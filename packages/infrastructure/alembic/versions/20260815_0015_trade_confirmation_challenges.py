"""Add durable, device-bound trade confirmation challenges."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260815_0015"
down_revision = "20260814_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
  if "trade_confirmation_challenges" in set(inspect(op.get_bind()).get_table_names()):
    return
  op.create_table(
    "trade_confirmation_challenges",
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("action", sa.String(length=48), nullable=False),
    sa.Column(
      "user_id",
      sa.String(length=36),
      sa.ForeignKey("auth_users.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column(
      "device_session_id",
      sa.String(length=36),
      sa.ForeignKey("auth_device_sessions.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column("account_id", sa.String(length=50), nullable=False),
    sa.Column("idempotency_key", sa.String(length=128), nullable=False),
    sa.Column("payload", sa.JSON(), nullable=False),
    sa.Column("payload_fingerprint", sa.String(length=64), nullable=False),
    sa.Column("token_digest", sa.String(length=64), nullable=False),
    sa.Column("expires_at", sa.DateTime(), nullable=False),
    sa.Column("consumed_at", sa.DateTime(), nullable=True),
    sa.Column("result_reference", sa.JSON(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint(
      "user_id",
      "account_id",
      "action",
      "idempotency_key",
      name="uq_trade_confirmation_challenge_idempotency",
    ),
    comment="敏感交易操作一次性确认挑战",
  )
  op.create_index(
    "ix_trade_confirmation_challenges_user_id",
    "trade_confirmation_challenges",
    ["user_id"],
  )
  op.create_index(
    "ix_trade_confirmation_challenges_device_session_id",
    "trade_confirmation_challenges",
    ["device_session_id"],
  )
  op.create_index(
    "ix_trade_confirmation_challenges_account_id",
    "trade_confirmation_challenges",
    ["account_id"],
  )
  op.create_index(
    "ix_trade_confirmation_challenges_expires_at",
    "trade_confirmation_challenges",
    ["expires_at"],
  )
  op.create_index(
    "ix_trade_confirmation_challenge_session_expiry",
    "trade_confirmation_challenges",
    ["device_session_id", "expires_at"],
  )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
