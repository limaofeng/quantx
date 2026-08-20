"""Add exact automatic-entry grants, fill debits and global pause gate."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260820_0024"
down_revision = "20260820_0023"
branch_labels = None
depends_on = None

_TABLES = (
  "entry_plan_authorization_grants",
  "entry_plan_authorization_consumptions",
  "entry_plan_authorization_events",
  "entry_automation_gates",
)


def upgrade() -> None:
  existing = set(inspect(op.get_bind()).get_table_names())
  present = existing.intersection(_TABLES)
  if present:
    raise RuntimeError(
      "entry-plan authorization schema already exists without this revision: "
      + ",".join(sorted(present))
    )

  op.create_table(
    "entry_plan_authorization_grants",
    sa.Column("grant_id", sa.String(36), primary_key=True),
    sa.Column("plan_id", sa.String(36), nullable=False),
    sa.Column(
      "run_id",
      sa.String(36),
      sa.ForeignKey("strategy_runs.id", ondelete="RESTRICT"),
      nullable=False,
    ),
    sa.Column("config_version", sa.Integer(), nullable=False),
    sa.Column("plan_fingerprint", sa.String(64), nullable=False),
    sa.Column("rule_fingerprint", sa.String(64), nullable=False),
    sa.Column("authorization_fingerprint", sa.String(64), nullable=False),
    sa.Column(
      "subject_user_id",
      sa.String(36),
      sa.ForeignKey("auth_users.id", ondelete="RESTRICT"),
      nullable=False,
    ),
    sa.Column(
      "device_session_id",
      sa.String(36),
      sa.ForeignKey("auth_device_sessions.id", ondelete="RESTRICT"),
      nullable=False,
    ),
    sa.Column("account_fingerprint", sa.String(64), nullable=False),
    sa.Column("account_snapshot_version", sa.String(64), nullable=False),
    sa.Column(
      "challenge_id",
      sa.String(36),
      sa.ForeignKey("trade_confirmation_challenges.id", ondelete="RESTRICT"),
      nullable=False,
      unique=True,
    ),
    sa.Column("instrument_code", sa.String(20), nullable=False),
    sa.Column("bucket", sa.String(16), nullable=False),
    sa.Column("max_total_amount_cny", sa.Numeric(20, 4), nullable=False),
    sa.Column("max_single_amount_cny", sa.Numeric(20, 4), nullable=False),
    sa.Column("max_daily_amount_cny", sa.Numeric(20, 4), nullable=False),
    sa.Column("max_position_pct", sa.Numeric(12, 8), nullable=False),
    sa.Column("max_buy_price", sa.Numeric(20, 6), nullable=False),
    sa.Column("max_slippage_bps", sa.Integer(), nullable=False),
    sa.Column("max_price_deviation_bps", sa.Integer(), nullable=False),
    sa.Column("plan_valid_until", sa.DateTime(), nullable=False),
    sa.Column("authorized_at", sa.DateTime(), nullable=False),
    sa.Column("expires_at", sa.DateTime(), nullable=False),
    sa.Column("revoked_at", sa.DateTime(), nullable=True),
    sa.Column("revoked_reason", sa.String(64), nullable=True),
    sa.Column("invalidated_at", sa.DateTime(), nullable=True),
    sa.Column("invalidation_reason", sa.String(64), nullable=True),
    sa.Column(
      "consumed_total_amount_cny",
      sa.Numeric(20, 4),
      nullable=False,
      server_default="0",
    ),
    sa.Column(
      "consumed_total_volume", sa.Integer(), nullable=False, server_default="0"
    ),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.CheckConstraint("plan_id = run_id", name="ck_entry_plan_auth_plan_run"),
    sa.CheckConstraint(
      "max_total_amount_cny > 0 AND max_single_amount_cny > 0 "
      "AND max_daily_amount_cny > 0",
      name="ck_entry_plan_auth_positive_amount_limits",
    ),
    sa.CheckConstraint(
      "max_position_pct > 0 AND max_position_pct <= 1",
      name="ck_entry_plan_auth_position_limit",
    ),
    sa.CheckConstraint(
      "max_buy_price > 0 AND max_slippage_bps >= 0 AND max_price_deviation_bps >= 0",
      name="ck_entry_plan_auth_price_limits",
    ),
    sa.CheckConstraint(
      "consumed_total_amount_cny >= 0 AND consumed_total_volume >= 0",
      name="ck_entry_plan_auth_monotonic_counters",
    ),
    sa.CheckConstraint(
      "authorized_at < expires_at AND expires_at <= plan_valid_until",
      name="ck_entry_plan_auth_validity_window",
    ),
  )
  op.create_index(
    "ix_entry_plan_auth_grant_authorization_fingerprint",
    "entry_plan_authorization_grants",
    ["authorization_fingerprint"],
  )
  op.create_index(
    "ix_entry_plan_auth_grant_plan_id",
    "entry_plan_authorization_grants",
    ["plan_id"],
  )
  op.create_index(
    "ix_entry_plan_auth_grant_run_id",
    "entry_plan_authorization_grants",
    ["run_id"],
  )
  op.create_index(
    "ix_entry_plan_auth_grant_subject_user_id",
    "entry_plan_authorization_grants",
    ["subject_user_id"],
  )
  op.create_index(
    "ix_entry_plan_auth_grant_device_session_id",
    "entry_plan_authorization_grants",
    ["device_session_id"],
  )
  op.create_index(
    "ix_entry_plan_auth_grant_expires_at",
    "entry_plan_authorization_grants",
    ["expires_at"],
  )
  op.create_index(
    "ix_entry_plan_auth_grant_active",
    "entry_plan_authorization_grants",
    ["plan_id", "revoked_at", "invalidated_at", "expires_at"],
  )
  op.create_index(
    "uq_entry_plan_auth_one_active_plan",
    "entry_plan_authorization_grants",
    ["plan_id"],
    unique=True,
    postgresql_where=sa.text("revoked_at IS NULL AND invalidated_at IS NULL"),
    sqlite_where=sa.text("revoked_at IS NULL AND invalidated_at IS NULL"),
  )

  op.create_table(
    "entry_plan_authorization_consumptions",
    sa.Column("consumption_id", sa.String(36), primary_key=True),
    sa.Column(
      "grant_id",
      sa.String(36),
      sa.ForeignKey("entry_plan_authorization_grants.grant_id", ondelete="RESTRICT"),
      nullable=False,
    ),
    sa.Column("plan_id", sa.String(36), nullable=False),
    sa.Column("trade_business_key", sa.String(160), nullable=False),
    sa.Column("trade_date", sa.Date(), nullable=False),
    sa.Column("filled_at", sa.DateTime(), nullable=False),
    sa.Column("filled_amount_cny", sa.Numeric(20, 4), nullable=False),
    sa.Column("filled_volume", sa.Integer(), nullable=False),
    sa.Column("fill_price", sa.Numeric(20, 6), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint(
      "trade_business_key", name="uq_entry_plan_auth_consumption_trade"
    ),
    sa.CheckConstraint(
      "filled_amount_cny > 0 AND filled_volume > 0 AND fill_price > 0",
      name="ck_entry_plan_auth_consumption_positive",
    ),
  )
  op.create_index(
    "ix_entry_plan_auth_consumption_grant_id",
    "entry_plan_authorization_consumptions",
    ["grant_id"],
  )
  op.create_index(
    "ix_entry_plan_auth_consumption_plan_id",
    "entry_plan_authorization_consumptions",
    ["plan_id"],
  )
  op.create_index(
    "ix_entry_plan_auth_consumption_grant_date",
    "entry_plan_authorization_consumptions",
    ["grant_id", "trade_date"],
  )

  op.create_table(
    "entry_plan_authorization_events",
    sa.Column("event_id", sa.String(36), primary_key=True),
    sa.Column("business_key", sa.String(192), nullable=False),
    sa.Column("plan_id", sa.String(36), nullable=False),
    sa.Column(
      "grant_id",
      sa.String(36),
      sa.ForeignKey("entry_plan_authorization_grants.grant_id", ondelete="RESTRICT"),
      nullable=True,
    ),
    sa.Column("event_type", sa.String(48), nullable=False),
    sa.Column("reason_code", sa.String(64), nullable=True),
    sa.Column("subject_fingerprint", sa.String(64), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint("business_key", name="uq_entry_plan_auth_event_business"),
  )
  op.create_index(
    "ix_entry_plan_auth_event_plan_id",
    "entry_plan_authorization_events",
    ["plan_id"],
  )
  op.create_index(
    "ix_entry_plan_auth_event_grant_id",
    "entry_plan_authorization_events",
    ["grant_id"],
  )
  op.create_index(
    "ix_entry_plan_auth_event_plan_created",
    "entry_plan_authorization_events",
    ["plan_id", "created_at"],
  )

  op.create_table(
    "entry_automation_gates",
    sa.Column("account_fingerprint", sa.String(64), primary_key=True),
    sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("reason", sa.String(160), nullable=True),
    sa.Column("actor_user_id", sa.String(36), nullable=True),
    sa.Column("changed_at", sa.DateTime(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
  )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
