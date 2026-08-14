"""Versioned, non-secret configuration for the product AI Runtime."""

from sqlalchemy import JSON, Boolean, CheckConstraint, Column, DateTime, Integer, String

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class AiRuntimeSettingsRecord(Base, TimestampMixin):
  """Singleton desired configuration shared by every AI Runtime instance."""

  __tablename__ = "ai_runtime_settings"
  __table_args__ = (
    CheckConstraint(
      "max_concurrent_runs BETWEEN 1 AND 16",
      name="ck_ai_runtime_settings_concurrency",
    ),
    CheckConstraint(
      "max_turns BETWEEN 1 AND 64",
      name="ck_ai_runtime_settings_turns",
    ),
    CheckConstraint(
      "max_tool_calls BETWEEN 1 AND 64",
      name="ck_ai_runtime_settings_tool_calls",
    ),
    CheckConstraint(
      "run_timeout_seconds BETWEEN 30 AND 3600",
      name="ck_ai_runtime_settings_timeout",
    ),
  )

  id = Column(String(32), primary_key=True, default="global")
  config_version = Column(Integer, nullable=False, default=1)
  enabled = Column(Boolean, nullable=False, default=True)
  model = Column(String(120), nullable=False)
  max_concurrent_runs = Column(Integer, nullable=False)
  max_turns = Column(Integer, nullable=False)
  max_tool_calls = Column(Integer, nullable=False)
  run_timeout_seconds = Column(Integer, nullable=False)
  updated_by_user_id = Column(String(36), nullable=False)


class AiRuntimeSettingsAudit(Base):
  """Append-only audit trail containing non-secret before/after values."""

  __tablename__ = "ai_runtime_settings_audits"

  id = Column(String(36), primary_key=True)
  config_version = Column(Integer, nullable=False, index=True)
  previous_values = Column(JSON, nullable=False, default=dict)
  next_values = Column(JSON, nullable=False, default=dict)
  user_id = Column(String(36), nullable=False, index=True)
  request_id = Column(String(64), nullable=False)
  occurred_at = Column(DateTime, nullable=False, index=True)
