"""Persistent account-level limit-up board assistant state."""

import uuid

from sqlalchemy import (
  JSON,
  BigInteger,
  Boolean,
  Column,
  Date,
  DateTime,
  Integer,
  String,
  Text,
  UniqueConstraint,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class LimitUpBoardAssistantConfig(Base, TimestampMixin):
  __tablename__ = "limit_up_board_assistant_configs"

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  account_id = Column(String(50), nullable=False, unique=True, index=True)
  enabled = Column(Boolean, nullable=False, default=False)
  mode = Column(String(16), nullable=False, default="paper")
  auto_exit_acknowledged = Column(Boolean, nullable=False, default=False)
  settings = Column(JSON, nullable=False, default=dict)
  config_version = Column(Integer, nullable=False, default=1)
  strategy_run_id = Column(String(36), nullable=True, index=True)
  universe_revision = Column(Integer, nullable=False, default=0)
  last_reconciled_at = Column(DateTime, nullable=True)
  last_error = Column(Text, nullable=True)


class LimitUpBoardCandidateArm(Base, TimestampMixin):
  __tablename__ = "limit_up_board_candidate_arms"
  __table_args__ = (
    UniqueConstraint(
      "account_id",
      "trade_date",
      "instrument_code",
      name="uq_limit_up_board_candidate_arm",
    ),
  )

  id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  account_id = Column(String(50), nullable=False, index=True)
  trade_date = Column(Date, nullable=False, index=True)
  instrument_code = Column(String(20), nullable=False, index=True)
  armed = Column(Boolean, nullable=False, default=True)
  source = Column(String(16), nullable=False, default="MANUAL")
  actor_id = Column(String(64), nullable=False, default="")
  idempotency_key = Column(String(128), nullable=False, default="")
  arm_version = Column(Integer, nullable=False, default=1)
  disarmed_at = Column(DateTime, nullable=True)


class LimitUpBoardAssistantProjection(Base, TimestampMixin):
  __tablename__ = "limit_up_board_assistant_projections"

  account_id = Column(String(50), primary_key=True)
  version = Column(BigInteger, nullable=False, default=0)
  payload = Column(JSON, nullable=False, default=dict)
  generated_at = Column(DateTime, nullable=False)
