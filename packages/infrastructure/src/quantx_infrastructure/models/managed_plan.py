"""Stable managed trading plans and their immutable configuration revisions."""

from sqlalchemy import (
  JSON,
  Column,
  DateTime,
  Index,
  Integer,
  String,
  Text,
  UniqueConstraint,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class ManagedPlanRecord(Base, TimestampMixin):
  """Stable business identity for one entry or exit plan."""

  __tablename__ = "managed_plans"
  __table_args__ = (
    Index("ix_managed_plan_account_kind_status", "account_id", "plan_kind", "status"),
    Index("ix_managed_plan_current_run", "current_run_id"),
  )

  plan_id = Column(String(128), primary_key=True)
  plan_kind = Column(String(16), nullable=False)
  account_id = Column(String(50), nullable=False)
  instrument_code = Column(String(20), nullable=False)
  status = Column(String(32), nullable=False, default="DRAFT")
  current_config_version = Column(Integer, nullable=False, default=1)
  current_run_id = Column(String(36), nullable=True)
  last_command_id = Column(String(128), nullable=True)
  last_error = Column(Text, nullable=True)


class ManagedPlanConfigRevision(Base):
  """Append-only frozen configuration assigned to exactly one StrategyRun."""

  __tablename__ = "managed_plan_config_revisions"
  __table_args__ = (
    UniqueConstraint(
      "plan_id",
      "config_version",
      name="uq_managed_plan_config_revision",
    ),
    UniqueConstraint("run_id", name="uq_managed_plan_revision_run"),
    Index("ix_managed_plan_revision_plan_created", "plan_id", "created_at"),
  )

  revision_id = Column(String(36), primary_key=True)
  plan_id = Column(String(128), nullable=False)
  config_version = Column(Integer, nullable=False)
  config_snapshot = Column(JSON, nullable=False)
  config_fingerprint = Column(String(64), nullable=False)
  state_migration_policy = Column(String(48), nullable=False, default="RESET")
  supersedes_run_id = Column(String(36), nullable=True)
  run_id = Column(String(36), nullable=True)
  created_by_user_id = Column(String(50), nullable=True)
  created_at = Column(DateTime, nullable=False)
