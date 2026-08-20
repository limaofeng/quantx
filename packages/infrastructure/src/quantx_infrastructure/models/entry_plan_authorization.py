"""Durable exact grants and monotonic fill consumption for managed entries."""

from sqlalchemy import (
  Boolean,
  CheckConstraint,
  Column,
  Date,
  DateTime,
  ForeignKey,
  Index,
  Integer,
  Numeric,
  String,
  UniqueConstraint,
  text,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class EntryPlanAuthorizationGrant(Base, TimestampMixin):
  """One revocable, device-bound authority envelope for a LIVE entry plan."""

  __tablename__ = "entry_plan_authorization_grants"
  __table_args__ = (
    Index(
      "ix_entry_plan_auth_grant_active",
      "plan_id",
      "revoked_at",
      "invalidated_at",
      "expires_at",
    ),
    Index(
      "uq_entry_plan_auth_one_active_plan",
      "plan_id",
      unique=True,
      postgresql_where=text("revoked_at IS NULL AND invalidated_at IS NULL"),
      sqlite_where=text("revoked_at IS NULL AND invalidated_at IS NULL"),
    ),
    CheckConstraint("plan_id = run_id", name="ck_entry_plan_auth_plan_run"),
    CheckConstraint(
      "max_total_amount_cny > 0 AND max_single_amount_cny > 0 "
      "AND max_daily_amount_cny > 0",
      name="ck_entry_plan_auth_positive_amount_limits",
    ),
    CheckConstraint(
      "max_position_pct > 0 AND max_position_pct <= 1",
      name="ck_entry_plan_auth_position_limit",
    ),
    CheckConstraint(
      "max_buy_price > 0 AND max_slippage_bps >= 0 AND max_price_deviation_bps >= 0",
      name="ck_entry_plan_auth_price_limits",
    ),
    CheckConstraint(
      "consumed_total_amount_cny >= 0 AND consumed_total_volume >= 0",
      name="ck_entry_plan_auth_monotonic_counters",
    ),
    CheckConstraint(
      "authorized_at < expires_at AND expires_at <= plan_valid_until",
      name="ck_entry_plan_auth_validity_window",
    ),
  )

  grant_id = Column(String(36), primary_key=True)
  plan_id = Column(String(36), nullable=False, index=True)
  run_id = Column(
    String(36),
    ForeignKey("strategy_runs.id", ondelete="RESTRICT"),
    nullable=False,
    index=True,
  )
  config_version = Column(Integer, nullable=False)
  plan_fingerprint = Column(String(64), nullable=False)
  rule_fingerprint = Column(String(64), nullable=False)
  authorization_fingerprint = Column(String(64), nullable=False, index=True)

  subject_user_id = Column(
    String(36),
    ForeignKey("auth_users.id", ondelete="RESTRICT"),
    nullable=False,
    index=True,
  )
  device_session_id = Column(
    String(36),
    ForeignKey("auth_device_sessions.id", ondelete="RESTRICT"),
    nullable=False,
    index=True,
  )
  account_fingerprint = Column(String(64), nullable=False)
  account_snapshot_version = Column(String(64), nullable=False)
  challenge_id = Column(
    String(36),
    ForeignKey("trade_confirmation_challenges.id", ondelete="RESTRICT"),
    nullable=False,
    unique=True,
  )

  instrument_code = Column(String(20), nullable=False)
  bucket = Column(String(16), nullable=False)
  max_total_amount_cny = Column(Numeric(20, 4), nullable=False)
  max_single_amount_cny = Column(Numeric(20, 4), nullable=False)
  max_daily_amount_cny = Column(Numeric(20, 4), nullable=False)
  max_position_pct = Column(Numeric(12, 8), nullable=False)
  max_buy_price = Column(Numeric(20, 6), nullable=False)
  max_slippage_bps = Column(Integer, nullable=False)
  max_price_deviation_bps = Column(Integer, nullable=False)
  plan_valid_until = Column(DateTime, nullable=False)

  authorized_at = Column(DateTime, nullable=False)
  expires_at = Column(DateTime, nullable=False, index=True)
  revoked_at = Column(DateTime, nullable=True)
  revoked_reason = Column(String(64), nullable=True)
  invalidated_at = Column(DateTime, nullable=True)
  invalidation_reason = Column(String(64), nullable=True)

  consumed_total_amount_cny = Column(Numeric(20, 4), nullable=False, default=0)
  consumed_total_volume = Column(Integer, nullable=False, default=0)


class EntryPlanAuthorizationConsumption(Base):
  """Idempotent QMT real-trade debit against an exact entry grant."""

  __tablename__ = "entry_plan_authorization_consumptions"
  __table_args__ = (
    UniqueConstraint("trade_business_key", name="uq_entry_plan_auth_consumption_trade"),
    Index(
      "ix_entry_plan_auth_consumption_grant_date",
      "grant_id",
      "trade_date",
    ),
    CheckConstraint(
      "filled_amount_cny > 0 AND filled_volume > 0 AND fill_price > 0",
      name="ck_entry_plan_auth_consumption_positive",
    ),
  )

  consumption_id = Column(String(36), primary_key=True)
  grant_id = Column(
    String(36),
    ForeignKey("entry_plan_authorization_grants.grant_id", ondelete="RESTRICT"),
    nullable=False,
    index=True,
  )
  plan_id = Column(String(36), nullable=False, index=True)
  trade_business_key = Column(String(160), nullable=False)
  trade_date = Column(Date, nullable=False)
  filled_at = Column(DateTime, nullable=False)
  filled_amount_cny = Column(Numeric(20, 4), nullable=False)
  filled_volume = Column(Integer, nullable=False)
  fill_price = Column(Numeric(20, 6), nullable=False)
  created_at = Column(DateTime, nullable=False)


class EntryPlanAuthorizationEvent(Base):
  """Idempotent security audit event without raw broker account identifiers."""

  __tablename__ = "entry_plan_authorization_events"
  __table_args__ = (
    UniqueConstraint("business_key", name="uq_entry_plan_auth_event_business"),
    Index("ix_entry_plan_auth_event_plan_created", "plan_id", "created_at"),
  )

  event_id = Column(String(36), primary_key=True)
  business_key = Column(String(192), nullable=False)
  plan_id = Column(String(36), nullable=False, index=True)
  grant_id = Column(
    String(36),
    ForeignKey("entry_plan_authorization_grants.grant_id", ondelete="RESTRICT"),
    nullable=True,
    index=True,
  )
  event_type = Column(String(48), nullable=False)
  reason_code = Column(String(64), nullable=True)
  subject_fingerprint = Column(String(64), nullable=True)
  created_at = Column(DateTime, nullable=False)


class EntryAutomationGate(Base, TimestampMixin):
  """Persistent personal-account kill switch for all automatic BUY intents."""

  __tablename__ = "entry_automation_gates"

  account_fingerprint = Column(String(64), primary_key=True)
  paused = Column(Boolean, nullable=False, default=False)
  reason = Column(String(160), nullable=True)
  actor_user_id = Column(String(36), nullable=True)
  changed_at = Column(DateTime, nullable=False)


__all__ = [
  "EntryAutomationGate",
  "EntryPlanAuthorizationConsumption",
  "EntryPlanAuthorizationEvent",
  "EntryPlanAuthorizationGrant",
]
