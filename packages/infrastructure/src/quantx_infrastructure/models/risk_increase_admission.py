"""Durable, account-scoped ordering for LIVE risk-increasing intents."""

from sqlalchemy import (
  CheckConstraint,
  Column,
  DateTime,
  ForeignKey,
  Index,
  Integer,
  String,
  Text,
  UniqueConstraint,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class AccountRiskIncreaseAdmissionBatch(Base, TimestampMixin):
  """One immutable input set and its recoverable processing lease."""

  __tablename__ = "account_risk_increase_admission_batches"
  __table_args__ = (
    CheckConstraint(
      "environment = 'LIVE'",
      name="ck_risk_admission_batch_live",
    ),
    CheckConstraint(
      "status IN ('PREPARED','COMMITTED','SUPERSEDED','EXPIRED','FAILED')",
      name="ck_risk_admission_batch_status",
    ),
    CheckConstraint(
      "attempt >= 1",
      name="ck_risk_admission_batch_attempt",
    ),
    UniqueConstraint(
      "account_id",
      "environment",
      "input_fingerprint",
      "attempt",
      name="uq_risk_admission_batch_input_attempt",
    ),
    UniqueConstraint(
      "account_id",
      "environment",
      "attempt",
      name="uq_risk_admission_batch_attempt",
    ),
    Index(
      "ix_risk_admission_batch_recovery",
      "account_id",
      "status",
      "processing_lease_until",
      "created_at",
    ),
  )

  admission_batch_id = Column(String(36), primary_key=True)
  account_id = Column(String(50), nullable=False)
  environment = Column(String(16), nullable=False, default="LIVE")
  attempt = Column(Integer, nullable=False)
  policy_version = Column(String(64), nullable=False)
  account_snapshot_id = Column(String(128), nullable=False)
  account_snapshot_hash = Column(String(64), nullable=False)
  obligation_watermark = Column(String(64), nullable=False)
  input_fingerprint = Column(String(64), nullable=False)
  intent_manifest_hash = Column(String(64), nullable=False)
  status = Column(String(24), nullable=False, default="PREPARED")
  processing_owner = Column(String(128), nullable=True)
  processing_fence_token = Column(String(36), nullable=True)
  processing_lease_until = Column(DateTime, nullable=True)
  expires_at = Column(DateTime, nullable=False)
  committed_at = Column(DateTime, nullable=True)
  terminal_reason = Column(Text, nullable=True)


class AccountRiskIncreaseAdmissionItem(Base):
  """Stable rank assigned to one intent within an admission batch."""

  __tablename__ = "account_risk_increase_admission_items"
  __table_args__ = (
    CheckConstraint(
      "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION','ENTRY_PLAN',"
      "'BOARD_ASSISTANT_EXECUTION','MANUAL_COMMAND')",
      name="ck_risk_admission_item_owner_type",
    ),
    CheckConstraint(
      "length(owner_id) > 0 AND owner_id = trim(owner_id)",
      name="ck_risk_admission_item_owner_id",
    ),
    CheckConstraint(
      "admission_rank >= 1",
      name="ck_risk_admission_item_rank",
    ),
    UniqueConstraint(
      "admission_batch_id",
      "intent_id",
      name="uq_risk_admission_item_intent",
    ),
    UniqueConstraint(
      "admission_batch_id",
      "admission_rank",
      name="uq_risk_admission_item_rank",
    ),
    Index("ix_risk_admission_item_intent", "intent_id"),
  )

  admission_item_id = Column(String(36), primary_key=True)
  admission_batch_id = Column(
    String(36),
    ForeignKey(
      "account_risk_increase_admission_batches.admission_batch_id",
      ondelete="CASCADE",
    ),
    nullable=False,
  )
  intent_id = Column(
    String(36),
    ForeignKey("trade_intents.id", ondelete="RESTRICT"),
    nullable=False,
  )
  admission_rank = Column(Integer, nullable=False)
  owner_type = Column(String(32), nullable=False)
  owner_id = Column(String(128), nullable=False)
  intent_created_at = Column(DateTime, nullable=False)


__all__ = [
  "AccountRiskIncreaseAdmissionBatch",
  "AccountRiskIncreaseAdmissionItem",
]
