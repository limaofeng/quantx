"""Durable market facts and research artifacts for first-board promotion V2."""

import uuid

from sqlalchemy import (
  JSON,
  BigInteger,
  Boolean,
  Column,
  Date,
  DateTime,
  Float,
  Index,
  Integer,
  String,
  Text,
  UniqueConstraint,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class LimitUpChainSnapshot(Base, TimestampMixin):
  __tablename__ = "limit_up_chain_snapshots"
  __table_args__ = (
    UniqueConstraint("trade_date", "snapshot_version", name="uq_limit_up_chain_version"),
    Index("ix_limit_up_chain_trade_asof", "trade_date", "as_of"),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  trade_date = Column(Date, nullable=False, index=True)
  as_of = Column(DateTime, nullable=False)
  snapshot_version = Column(String(64), nullable=False)
  score_version = Column(String(64), nullable=False)
  max_board_count = Column(Integer, nullable=False, default=0)
  first_board_count = Column(Integer, nullable=False, default=0)
  sealed_count = Column(Integer, nullable=False, default=0)
  broken_count = Column(Integer, nullable=False, default=0)
  break_rate = Column(Float, nullable=False, default=0.0)
  payload = Column(JSON, nullable=False, default=dict)


class LimitUpLifecycleSnapshot(Base, TimestampMixin):
  __tablename__ = "limit_up_lifecycle_snapshots"
  __table_args__ = (
    UniqueConstraint(
      "trade_date",
      "instrument_code",
      "snapshot_version",
      name="uq_limit_up_lifecycle_version",
    ),
    Index(
      "ix_limit_up_lifecycle_date_code_asof",
      "trade_date",
      "instrument_code",
      "as_of",
    ),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  trade_date = Column(Date, nullable=False, index=True)
  instrument_code = Column(String(20), nullable=False, index=True)
  stage = Column(String(32), nullable=False)
  as_of = Column(DateTime, nullable=False)
  snapshot_version = Column(String(64), nullable=False)
  feature_version = Column(String(64), nullable=False)
  ever_touched_limit = Column(Boolean, nullable=False, default=False)
  break_count = Column(Integer, nullable=False, default=0)
  payload = Column(JSON, nullable=False, default=dict)


class FirstBoardPromotionAssessmentRecord(Base, TimestampMixin):
  __tablename__ = "first_board_promotion_assessments"
  __table_args__ = (
    UniqueConstraint(
      "lifecycle_snapshot_id",
      "model_version",
      name="uq_first_board_assessment_model",
    ),
    Index(
      "ix_first_board_assessment_rank",
      "trade_date",
      "eligible",
      "rank_score",
    ),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  lifecycle_snapshot_id = Column(String(36), nullable=False, index=True)
  trade_date = Column(Date, nullable=False, index=True)
  instrument_code = Column(String(20), nullable=False, index=True)
  as_of = Column(DateTime, nullable=False)
  model_version = Column(String(64), nullable=False)
  exit_policy_version = Column(String(64), nullable=False)
  segment = Column(String(24), nullable=False)
  eligible = Column(Boolean, nullable=False, default=False)
  rank_score = Column(Float, nullable=False, default=0.0)
  first_board_close_probability = Column(Float, nullable=False, default=0.0)
  next_day_limit_touch_probability = Column(Float, nullable=False, default=0.0)
  next_day_limit_seal_probability = Column(Float, nullable=False, default=0.0)
  expected_net_return_pct = Column(Float, nullable=False, default=0.0)
  cvar95_loss_pct = Column(Float, nullable=False, default=0.0)
  high_position_type = Column(String(32), nullable=False)
  veto_reasons = Column(JSON, nullable=False, default=list)
  payload = Column(JSON, nullable=False, default=dict)


class FirstBoardCandidatePreference(Base, TimestampMixin):
  __tablename__ = "first_board_candidate_preferences"
  __table_args__ = (
    UniqueConstraint(
      "account_id",
      "trade_date",
      "instrument_code",
      name="uq_first_board_candidate_preference",
    ),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  account_id = Column(String(50), nullable=False, index=True)
  trade_date = Column(Date, nullable=False, index=True)
  instrument_code = Column(String(20), nullable=False, index=True)
  preference = Column(String(16), nullable=False, default="PREFER")
  actor_id = Column(String(64), nullable=False, default="")
  idempotency_key = Column(String(128), nullable=False, default="")
  version = Column(BigInteger, nullable=False, default=1)


class LimitUpResearchJob(Base, TimestampMixin):
  __tablename__ = "limit_up_research_jobs"
  __table_args__ = (
    UniqueConstraint("idempotency_key", name="uq_limit_up_research_job_idempotency"),
    Index("ix_limit_up_research_job_queue", "status", "priority", "created_at"),
    Index("ix_limit_up_research_job_daily_code", "trade_date", "instrument_code"),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  assessment_id = Column(String(36), nullable=False, index=True)
  trade_date = Column(Date, nullable=False, index=True)
  instrument_code = Column(String(20), nullable=False, index=True)
  input_snapshot_version = Column(String(64), nullable=False)
  agent_id = Column(String(64), nullable=False, default="limit_up_research_assistant")
  status = Column(String(24), nullable=False, default="QUEUED")
  priority = Column(Integer, nullable=False, default=0)
  idempotency_key = Column(String(160), nullable=False)
  lease_owner = Column(String(96), nullable=True)
  lease_expires_at = Column(DateTime, nullable=True)
  started_at = Column(DateTime, nullable=True)
  finished_at = Column(DateTime, nullable=True)
  input_tokens = Column(Integer, nullable=False, default=0)
  output_tokens = Column(Integer, nullable=False, default=0)
  error_code = Column(String(64), nullable=True)
  error_message = Column(String(512), nullable=True)


class LimitUpResearchArtifact(Base, TimestampMixin):
  __tablename__ = "limit_up_research_artifacts"
  __table_args__ = (
    UniqueConstraint("job_id", name="uq_limit_up_research_artifact_job"),
    Index(
      "ix_limit_up_research_artifact_date_code",
      "trade_date",
      "instrument_code",
      "generated_at",
    ),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  job_id = Column(String(36), nullable=False, index=True)
  assessment_id = Column(String(36), nullable=False, index=True)
  trade_date = Column(Date, nullable=False, index=True)
  instrument_code = Column(String(20), nullable=False, index=True)
  input_snapshot_version = Column(String(64), nullable=False)
  agent_id = Column(String(64), nullable=False)
  model = Column(String(80), nullable=False)
  prompt_version = Column(String(32), nullable=False)
  status = Column(String(24), nullable=False, default="COMPLETED")
  summary = Column(Text, nullable=False, default="")
  content = Column(JSON, nullable=False, default=dict)
  citations = Column(JSON, nullable=False, default=list)
  generated_at = Column(DateTime, nullable=False)


class FirstBoardModelRelease(Base, TimestampMixin):
  """Operator-controlled evidence gate for shadow -> paper -> live rollout."""

  __tablename__ = "first_board_model_releases"

  model_version = Column(String(64), primary_key=True)
  exit_policy_version = Column(String(64), nullable=False)
  stage = Column(String(16), nullable=False, default="SHADOW")
  sample_trading_days = Column(Integer, nullable=False, default=0)
  main_board_eligible_samples = Column(Integer, nullable=False, default=0)
  growth_board_eligible_samples = Column(Integer, nullable=False, default=0)
  bootstrap_ci_lower_pct = Column(Float, nullable=True)
  tail_loss_budget_passed = Column(Boolean, nullable=False, default=False)
  historical_rules_complete = Column(Boolean, nullable=False, default=False)
  simulation_verified = Column(Boolean, nullable=False, default=False)
  live_reconciliation_verified = Column(Boolean, nullable=False, default=False)
  evidence = Column(JSON, nullable=False, default=dict)
  approved_by = Column(String(64), nullable=False, default="")
  approved_at = Column(DateTime, nullable=True)
