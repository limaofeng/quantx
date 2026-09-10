"""Independent T model registry; immutable evidence and mutable authorization."""

from sqlalchemy import (
  JSON,
  CheckConstraint,
  Column,
  DateTime,
  ForeignKeyConstraint,
  Index,
  Integer,
  String,
  text,
)

from quantx_infrastructure.database.relational_base import Base


class TModelVersionRecord(Base):
  __tablename__ = "t_model_versions"
  __table_args__ = (
    CheckConstraint("registry_stage IN ('CANDIDATE','SHADOW','ACTIVE','SUSPENDED','RETIRED')", name="ck_t_model_stage"),
    CheckConstraint("gate_conclusion IN ('SHADOW_ELIGIBLE','ACTIVE_ELIGIBLE')", name="ck_t_model_gate"),
    CheckConstraint("registry_stage <> 'ACTIVE' OR gate_conclusion = 'ACTIVE_ELIGIBLE'", name="ck_t_model_active_gate"),
    CheckConstraint("authorization_revision >= 1", name="ck_t_model_revision"),
    Index("uq_t_model_one_active", "registry_stage", unique=True,
      postgresql_where=text("registry_stage = 'ACTIVE'"), sqlite_where=text("registry_stage = 'ACTIVE'")),
  )
  model_id = Column(String(80), primary_key=True)
  model_version = Column(String(80), primary_key=True)
  run_key = Column(String(160), nullable=False, unique=True)
  artifact_sha256 = Column(String(64), nullable=False)
  policy_compatibility_hash = Column(String(64), nullable=False)
  gate_conclusion = Column(String(24), nullable=False)
  evidence = Column(JSON, nullable=False)
  registration_hash = Column(String(64), nullable=False)
  registry_stage = Column(String(16), nullable=False)
  authorization_revision = Column(Integer, nullable=False)


class TModelRegistryEventRecord(Base):
  __tablename__ = "t_model_registry_events"
  __table_args__ = (
    ForeignKeyConstraint(["model_id", "model_version"], ["t_model_versions.model_id", "t_model_versions.model_version"]),
    CheckConstraint("authorization_revision >= 1", name="ck_t_model_event_revision"),
  )
  model_id = Column(String(80), primary_key=True)
  model_version = Column(String(80), primary_key=True)
  authorization_revision = Column(Integer, primary_key=True)
  previous_stage = Column(String(16), nullable=True)
  registry_stage = Column(String(16), nullable=False)
  actor_id = Column(String(64), nullable=False)
  reason = Column(String(256), nullable=False)
  occurred_at = Column(DateTime(timezone=True), nullable=False)
  registration_hash = Column(String(64), nullable=False)
