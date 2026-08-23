"""Immutable evidence used by the stateful T-trade opportunity engine."""

import uuid

from sqlalchemy import (
  JSON,
  CheckConstraint,
  Column,
  DateTime,
  Index,
  Integer,
  String,
  UniqueConstraint,
)
from sqlalchemy.sql import func as sql_func

from quantx_infrastructure.database.relational_base import Base

T_TRADE_EVALUATION_KIND_MATERIAL = "MATERIAL"
T_TRADE_EVALUATION_KIND_DIAGNOSTIC = "COALESCED_DIAGNOSTIC"


class TTradeOpportunityEvaluation(Base):
  """Append-only material decision or coalesced diagnostic evidence."""

  __tablename__ = "t_trade_opportunity_evaluations"
  __table_args__ = (
    UniqueConstraint(
      "event_key",
      name="uq_t_trade_opportunity_evaluation_event_key",
    ),
    Index(
      "ix_t_trade_evaluation_account_time",
      "account_id",
      "evaluated_at",
      "id",
    ),
    Index(
      "ix_t_trade_evaluation_account_instrument_time",
      "account_id",
      "instrument_code",
      "evaluated_at",
      "id",
    ),
    Index(
      "ix_t_trade_evaluation_run_time",
      "strategy_run_id",
      "evaluated_at",
      "id",
    ),
    Index(
      "ix_t_trade_evaluation_account_candidate_time",
      "account_id",
      "candidate_id",
      "evaluated_at",
      "id",
    ),
    CheckConstraint(
      "record_kind IN ('MATERIAL', 'COALESCED_DIAGNOSTIC')",
      name="ck_t_trade_evaluation_record_kind",
    ),
    CheckConstraint(
      "coalesced_count >= 1",
      name="ck_t_trade_evaluation_coalesced_count",
    ),
    CheckConstraint(
      "record_kind = 'MATERIAL' OR candidate_id IS NULL",
      name="ck_t_trade_evaluation_candidate_material",
    ),
    CheckConstraint(
      "(record_kind = 'MATERIAL' "
      "AND coalesced_count = 1 "
      "AND window_started_at IS NULL "
      "AND window_ended_at IS NULL) "
      "OR (record_kind = 'COALESCED_DIAGNOSTIC' "
      "AND window_started_at IS NOT NULL "
      "AND window_ended_at IS NOT NULL "
      "AND window_started_at <= window_ended_at "
      "AND window_ended_at <= evaluated_at)",
      name="ck_t_trade_evaluation_window_shape",
    ),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  event_key = Column(String(160), nullable=False)
  account_id = Column(String(50), nullable=False)
  strategy_run_id = Column(String(36), nullable=False)
  instrument_code = Column(String(20), nullable=False)
  candidate_id = Column(String(128), nullable=True)
  evaluated_at = Column(DateTime, nullable=False)
  record_kind = Column(String(24), nullable=False)
  event_type = Column(String(64), nullable=False)
  window_started_at = Column(DateTime, nullable=True)
  window_ended_at = Column(DateTime, nullable=True)
  coalesced_count = Column(Integer, nullable=False, default=1)
  policy_version = Column(String(64), nullable=False)
  schema_version = Column(String(32), nullable=False)
  content_fingerprint = Column(String(64), nullable=False)
  payload = Column(JSON, nullable=False, default=dict)
  metrics = Column(JSON, nullable=False, default=dict)
  created_at = Column(DateTime, nullable=False, default=sql_func.now())


class TTradeInstrumentProfile(Base):
  """Immutable account-independent instrument profile available at ``as_of``."""

  __tablename__ = "t_trade_instrument_profiles"
  __table_args__ = (
    UniqueConstraint(
      "instrument_code",
      "fingerprint",
      name="uq_t_trade_instrument_profile_fingerprint",
    ),
    UniqueConstraint(
      "instrument_code",
      "as_of",
      "schema_version",
      "version",
      name="uq_t_trade_instrument_profile_coordinate",
    ),
    Index(
      "ix_t_trade_instrument_profile_asof",
      "instrument_code",
      "as_of",
      "id",
    ),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  instrument_code = Column(String(20), nullable=False)
  as_of = Column(DateTime, nullable=False)
  profile = Column(JSON, nullable=False, default=dict)
  schema_version = Column(String(32), nullable=False)
  version = Column(String(64), nullable=False)
  fingerprint = Column(String(64), nullable=False)
  metrics = Column(JSON, nullable=False, default=dict)
  data_manifest = Column(JSON, nullable=False, default=dict)
  created_at = Column(DateTime, nullable=False, default=sql_func.now())
