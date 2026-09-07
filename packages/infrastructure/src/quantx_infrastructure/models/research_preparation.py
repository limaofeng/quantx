"""Durable single-user preparation settings and immutable job requests."""

from sqlalchemy import JSON, CheckConstraint, Column, DateTime, Integer, String

from quantx_infrastructure.database.relational_base import Base


class ResearchPreparationSettings(Base):
  __tablename__ = "research_preparation_settings"
  id = Column(Integer, primary_key=True)
  config = Column(JSON, nullable=False)
  __table_args__ = (
    CheckConstraint("id = 1", name="ck_research_preparation_singleton"),
  )


class ResearchPreparationJob(Base):
  __tablename__ = "research_preparation_jobs"
  job_id = Column(String(36), primary_key=True)
  request_hash = Column(String(64), nullable=False, unique=True)
  kind = Column(String(16), nullable=False)
  request = Column(JSON, nullable=False)
  status = Column(String(16), nullable=False)
  phase = Column(String(64), nullable=False)
  flow_run_id = Column(String(64), nullable=True)
  result = Column(JSON, nullable=False, default=dict)
  error = Column(String(512), nullable=True)
  created_at = Column(DateTime, nullable=False)
  updated_at = Column(DateTime, nullable=False)
  __table_args__ = (
    CheckConstraint(
      "kind IN ('COVERAGE','DOWNLOAD','CERTIFY','GPU')",
      name="ck_research_preparation_kind",
    ),
    CheckConstraint(
      "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')",
      name="ck_research_preparation_status",
    ),
  )
