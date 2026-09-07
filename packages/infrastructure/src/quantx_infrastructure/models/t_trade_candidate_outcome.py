"""Restart-safe maturation records for T-trade opportunity candidates."""

import uuid

from sqlalchemy import (
  JSON,
  BigInteger,
  CheckConstraint,
  Column,
  DateTime,
  Float,
  Index,
  Integer,
  String,
  UniqueConstraint,
)
from sqlalchemy.sql import func as sql_func

from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.execution_owner import register_identity_immutability


class TTradeCandidateOutcome(Base):
  """A bounded causal aggregate, never raw future Tick history."""

  __tablename__ = "t_trade_candidate_outcomes"
  __table_args__ = (
    UniqueConstraint(
      "environment",
      "owner_type",
      "owner_id",
      "candidate_id",
      name="uq_t_trade_candidate_outcome_owner_candidate",
    ),
    Index(
      "ix_t_trade_candidate_outcome_run_status",
      "strategy_run_id",
      "status",
      "instrument_code",
    ),
    Index(
      "ix_t_trade_candidate_outcome_run_post_fill_status",
      "strategy_run_id",
      "post_fill_status",
      "instrument_code",
    ),
    Index(
      "ix_t_trade_candidate_outcome_owner_status",
      "owner_type",
      "owner_id",
      "environment",
      "status",
      "instrument_code",
    ),
    Index(
      "ix_t_trade_candidate_outcome_account_time",
      "account_id",
      "candidate_at",
      "id",
    ),
    CheckConstraint(
      "status IN ('OBSERVING', 'MATURED', 'UNAVAILABLE')",
      name="ck_t_trade_candidate_outcome_status",
    ),
    CheckConstraint(
      "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION')",
      name="ck_t_trade_candidate_outcome_owner_type",
    ),
    CheckConstraint(
      "length(owner_id) > 0 AND owner_id = trim(owner_id)",
      name="ck_t_trade_candidate_outcome_owner_id",
    ),
    CheckConstraint(
      "environment IN ('PAPER','LIVE','BACKTEST')",
      name="ck_t_trade_candidate_outcome_environment",
    ),
    CheckConstraint(
      "strategy_run_id IS NULL OR (owner_type = 'STRATEGY_RUN' "
      "AND owner_id = strategy_run_id)",
      name="ck_t_trade_candidate_outcome_strategy_run_owner",
    ),
    CheckConstraint(
      "post_fill_status IN ('WAITING_ENTRY', 'OBSERVING', 'MATURED', 'UNAVAILABLE')",
      name="ck_t_trade_candidate_outcome_post_fill_status",
    ),
    CheckConstraint(
      "source_time_ms >= 0 AND tick_ordinal >= 0",
      name="ck_t_trade_candidate_outcome_source_identity",
    ),
    CheckConstraint(
      "reference_price > 0",
      name="ck_t_trade_candidate_outcome_reference_price",
    ),
    CheckConstraint(
      "state_version >= 1",
      name="ck_t_trade_candidate_outcome_state_version",
    ),
    CheckConstraint(
      "(status = 'OBSERVING' AND finalized_at IS NULL) OR "
      "(status IN ('MATURED', 'UNAVAILABLE') AND finalized_at IS NOT NULL)",
      name="ck_t_trade_candidate_outcome_terminal_shape",
    ),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  account_id = Column(String(50), nullable=False)
  owner_type = Column(String(32), nullable=False)
  owner_id = Column(String(128), nullable=False)
  environment = Column(String(16), nullable=False)
  strategy_run_id = Column(String(36), nullable=True)
  instrument_code = Column(String(20), nullable=False)
  candidate_id = Column(String(128), nullable=False)
  candidate_fingerprint = Column(String(64), nullable=False)
  candidate_at = Column(DateTime, nullable=False)
  source_time_ms = Column(BigInteger, nullable=False)
  tick_ordinal = Column(BigInteger, nullable=False)
  continuity_generation = Column(String(64), nullable=False)
  reference_price = Column(Float, nullable=False)
  policy_version = Column(String(64), nullable=False)
  feature_schema_version = Column(String(32), nullable=False)
  profile_version = Column(String(64), nullable=True)
  profile_fingerprint = Column(String(64), nullable=True)
  outcome_schema_version = Column(String(32), nullable=False)
  status = Column(String(24), nullable=False)
  post_fill_status = Column(String(24), nullable=False)
  unavailable_reason = Column(String(64), nullable=True)
  state = Column(JSON, nullable=False)
  content_fingerprint = Column(String(64), nullable=False)
  state_version = Column(Integer, nullable=False, default=1)
  finalized_at = Column(DateTime, nullable=True)
  created_at = Column(DateTime, nullable=False, default=sql_func.now())
  updated_at = Column(
    DateTime,
    nullable=False,
    default=sql_func.now(),
    onupdate=sql_func.now(),
  )


register_identity_immutability(TTradeCandidateOutcome)
