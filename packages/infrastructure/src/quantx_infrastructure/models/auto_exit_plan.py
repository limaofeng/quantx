"""Persistent Engine-owned automatic exit plans and idempotent audit events."""

from sqlalchemy import (
  JSON,
  Boolean,
  CheckConstraint,
  Column,
  DateTime,
  Float,
  Index,
  Integer,
  String,
  Text,
  UniqueConstraint,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class AutoExitPlanRecord(Base, TimestampMixin):
  __tablename__ = "auto_exit_plans"
  __table_args__ = (
    UniqueConstraint(
      "source_type",
      "source_id",
      name="uq_auto_exit_plan_source",
    ),
    Index(
      "ix_auto_exit_plan_monitor",
      "enabled",
      "status",
      "instrument_code",
    ),
    Index("ix_auto_exit_plan_group", "group_id", "created_at"),
    Index(
      "ix_auto_exit_plan_capacity",
      "account_id",
      "instrument_code",
      "status",
    ),
    CheckConstraint(
      "(auto_exit_authorized = false AND "
      "auto_exit_authorization_fingerprint IS NULL AND "
      "auto_exit_authorization_config_version IS NULL AND "
      "auto_exit_authorized_at IS NULL AND "
      "auto_exit_authorization_expires_at IS NULL AND "
      "auto_exit_authorization_challenge_id IS NULL AND "
      "auto_exit_authorization_user_id IS NULL AND "
      "auto_exit_authorization_device_session_id IS NULL) OR "
      "(auto_exit_authorized = true AND "
      "auto_exit_authorization_fingerprint IS NOT NULL AND "
      "auto_exit_authorization_config_version IS NOT NULL AND "
      "auto_exit_authorized_at IS NOT NULL AND "
      "auto_exit_authorization_expires_at IS NOT NULL AND "
      "auto_exit_authorization_challenge_id IS NOT NULL AND "
      "auto_exit_authorization_user_id IS NOT NULL AND "
      "auto_exit_authorization_device_session_id IS NOT NULL)",
      name="ck_auto_exit_plan_exact_authorization",
    ),
  )

  plan_id = Column(String(128), primary_key=True)
  account_id = Column(String(50), nullable=False, index=True)
  instrument_code = Column(String(20), nullable=False, index=True)
  bucket = Column(String(32), nullable=False, default="manual")
  source_type = Column(String(48), nullable=False)
  source_id = Column(String(128), nullable=False)
  group_id = Column(String(36), nullable=True)
  strategy_run_id = Column(String(36), nullable=True, index=True)
  enabled = Column(Boolean, nullable=False, default=True)
  status = Column(String(32), nullable=False, default="ACTIVE")
  execution_mode = Column(String(16), nullable=False, default="paper")
  auto_exit_authorized = Column(Boolean, nullable=False, default=False)
  auto_exit_authorization_fingerprint = Column(String(64), nullable=True)
  auto_exit_authorization_config_version = Column(Integer, nullable=True)
  auto_exit_authorized_at = Column(DateTime, nullable=True)
  auto_exit_authorization_expires_at = Column(DateTime, nullable=True)
  auto_exit_authorization_challenge_id = Column(String(36), nullable=True)
  auto_exit_authorization_user_id = Column(String(36), nullable=True)
  auto_exit_authorization_device_session_id = Column(String(36), nullable=True)
  config_version = Column(Integer, nullable=False, default=1)
  completion_strategy = Column(String(32), nullable=True)

  protected_volume = Column(Integer, nullable=False)
  exited_volume = Column(Integer, nullable=False, default=0)
  remaining_volume = Column(Integer, nullable=False)
  entry_avg_price = Column(Float, nullable=False)
  plan_state = Column(JSON, nullable=False, default=dict)

  phase = Column(String(32), nullable=False, default="WAITING_ARM")
  data_quality = Column(String(32), nullable=False, default="PRICE_UNAVAILABLE")
  last_decision = Column(String(64), nullable=True)
  peak_price = Column(Float, nullable=False, default=0.0)
  peak_drawdown_pct = Column(Float, nullable=False, default=0.0)
  volume_velocity = Column(Float, nullable=True)
  weak_score = Column(Integer, nullable=False, default=0)
  trailing_floor_pct = Column(Float, nullable=True)
  pending_client_order_id = Column(String(128), nullable=True, index=True)
  last_evaluated_at = Column(DateTime, nullable=True)
  last_error = Column(Text, nullable=True)


class AutoExitPlanEvent(Base):
  __tablename__ = "auto_exit_plan_events"
  __table_args__ = (
    UniqueConstraint("business_key", name="uq_auto_exit_plan_event_business"),
    Index("ix_auto_exit_plan_event_plan_created", "plan_id", "created_at"),
  )

  event_id = Column(String(36), primary_key=True)
  business_key = Column(String(256), nullable=False)
  plan_id = Column(String(128), nullable=False, index=True)
  event_type = Column(String(48), nullable=False)
  payload = Column(JSON, nullable=False, default=dict)
  created_at = Column(DateTime, nullable=False)
