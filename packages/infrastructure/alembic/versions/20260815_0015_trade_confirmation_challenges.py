"""Add durable, device-bound trade confirmation challenges."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260815_0015"
down_revision = "20260814_0014"
branch_labels = None
depends_on = None

_TABLE_NAME = "trade_confirmation_challenges"
_REQUIRED_COLUMNS = {
  "id",
  "action",
  "user_id",
  "device_session_id",
  "account_id",
  "idempotency_key",
  "payload",
  "payload_fingerprint",
  "token_digest",
  "expires_at",
  "consumed_at",
  "result_reference",
  "created_at",
  "updated_at",
}
_REQUIRED_NOT_NULL_COLUMNS = _REQUIRED_COLUMNS - {
  "consumed_at",
  "result_reference",
}
_IDEMPOTENCY_UNIQUE_COLUMNS = {
  "user_id",
  "account_id",
  "action",
  "idempotency_key",
}


def _validate_existing_schema(inspector) -> None:
  columns = {
    str(column.get("name") or ""): column
    for column in inspector.get_columns(_TABLE_NAME)
  }
  missing_columns = sorted(_REQUIRED_COLUMNS - set(columns))
  nullable_required_columns = sorted(
    name
    for name in _REQUIRED_NOT_NULL_COLUMNS
    if name in columns and bool(columns[name].get("nullable", True))
  )
  unique_constraints = inspector.get_unique_constraints(_TABLE_NAME) or []
  has_idempotency_unique = any(
    {
      str(column_name)
      for column_name in list(constraint.get("column_names") or [])
    }
    == _IDEMPOTENCY_UNIQUE_COLUMNS
    for constraint in unique_constraints
  )
  if missing_columns or nullable_required_columns or not has_idempotency_unique:
    details = []
    if missing_columns:
      details.append(f"missing columns={','.join(missing_columns)}")
    if nullable_required_columns:
      details.append(
        "nullable required columns=" + ",".join(nullable_required_columns)
      )
    if not has_idempotency_unique:
      details.append("missing user/account/action/idempotency unique constraint")
    raise RuntimeError(
      "Partial trade_confirmation_challenges schema detected: "
      + "; ".join(details)
    )


def upgrade() -> None:
  inspector = inspect(op.get_bind())
  if _TABLE_NAME in set(inspector.get_table_names()):
    _validate_existing_schema(inspector)
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
