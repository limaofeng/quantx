"""Durable portfolio attempts, separate from material decision cycles."""

from sqlalchemy import (
  JSON,
  CheckConstraint,
  Column,
  DateTime,
  ForeignKey,
  Index,
  Integer,
  Numeric,
  String,
  UniqueConstraint,
  text,
)

from quantx_infrastructure.database.relational_base import Base


class TAllocationBatchRecord(Base):
  __tablename__ = "t_allocation_batches"
  __table_args__ = (
    UniqueConstraint(
      "execution_id",
      "cycle_id",
      "allocation_attempt",
      name="uq_t_allocation_cycle_attempt",
    ),
    CheckConstraint(
      "allocation_attempt >= 1 AND intent_count >= 1", name="ck_t_allocation_counts"
    ),
    CheckConstraint("environment IN ('PAPER','LIVE')", name="ck_t_allocation_environment"),
    CheckConstraint(
      "status IN ('PREPARED','COMMITTED','SUPERSEDED','EXPIRED','FAILED')",
      name="ck_t_allocation_status",
    ),
    CheckConstraint("expires_at > created_at", name="ck_t_allocation_ttl"),
    CheckConstraint(
      "length(portfolio_input_fingerprint) = 64 AND length(intent_manifest_hash) = 64",
      name="ck_t_allocation_input_hashes",
    ),
    CheckConstraint(
      "(processing_owner IS NULL AND processing_fence_token IS NULL AND processing_lease_until IS NULL) OR "
      "(status = 'PREPARED' AND processing_owner IS NOT NULL AND length(trim(processing_owner)) > 0 "
      "AND processing_fence_token IS NOT NULL AND length(trim(processing_fence_token)) > 0 "
      "AND processing_lease_until IS NOT NULL AND processing_lease_until <= expires_at)",
      name="ck_t_allocation_claim",
    ),
    CheckConstraint(
      "(status = 'PREPARED' AND committed_at IS NULL AND decision_manifest_hash IS NULL AND terminal_reason IS NULL) OR "
      "(status = 'COMMITTED' AND committed_at IS NOT NULL AND decision_manifest_hash IS NOT NULL AND length(decision_manifest_hash) = 64 "
      "AND terminal_reason IS NULL AND processing_owner IS NULL) OR "
      "(status IN ('SUPERSEDED','EXPIRED','FAILED') AND committed_at IS NOT NULL "
      "AND decision_manifest_hash IS NULL AND terminal_reason IS NOT NULL "
      "AND length(trim(terminal_reason)) > 0 AND processing_owner IS NULL)",
      name="ck_t_allocation_terminal",
    ),
    Index(
      "uq_t_allocation_prepared_cycle",
      "execution_id",
      "cycle_id",
      unique=True,
      postgresql_where=text("status = 'PREPARED'"),
      sqlite_where=text("status = 'PREPARED'"),
    ),
    Index("ix_t_allocation_recovery", "status", "processing_lease_until", "expires_at"),
  )
  allocation_batch_id = Column(String(36), primary_key=True)
  execution_id = Column(
    String(36),
    ForeignKey("t_assistant_executions.execution_id", ondelete="RESTRICT"),
    nullable=False,
  )
  cycle_id = Column(
    String(36),
    ForeignKey("t_assistant_decision_cycles.cycle_id", ondelete="RESTRICT"),
    nullable=False,
  )
  environment = Column(String(16), nullable=False)
  allocation_attempt = Column(Integer, nullable=False)
  portfolio_input_fingerprint = Column(String(64), nullable=False)
  portfolio_snapshot = Column(JSON, nullable=False)
  intent_manifest_hash = Column(String(64), nullable=False)
  intent_manifest = Column(JSON, nullable=False)
  intent_count = Column(Integer, nullable=False)
  decision_manifest_hash = Column(String(64), nullable=True)
  status = Column(String(16), nullable=False)
  processing_owner = Column(String(128), nullable=True)
  processing_fence_token = Column(String(36), nullable=True)
  processing_lease_until = Column(DateTime(timezone=True), nullable=True)
  created_at = Column(DateTime(timezone=True), nullable=False)
  expires_at = Column(DateTime(timezone=True), nullable=False)
  committed_at = Column(DateTime(timezone=True), nullable=True)
  terminal_reason = Column(String(128), nullable=True)


class TAllocationDecisionRecord(Base):
  __tablename__ = "t_allocation_decisions"
  __table_args__ = (
    UniqueConstraint(
      "allocation_batch_id", "intent_id", name="uq_t_allocation_decision_intent"
    ),
    UniqueConstraint(
      "allocation_batch_id", "rank", name="uq_t_allocation_decision_rank"
    ),
    CheckConstraint(
      "rank >= 1 AND intent_version >= 0", name="ck_t_allocation_decision_rank"
    ),
    CheckConstraint(
      "action IN ('ALLOW','CAP','DELAY','REJECT')",
      name="ck_t_allocation_decision_action",
    ),
    CheckConstraint(
      "requested_amount_ceiling <> 'NaN' AND allocated_amount_cap <> 'NaN' AND "
      "requested_amount_ceiling > 0 AND allocated_amount_cap >= 0 AND allocated_amount_cap <= requested_amount_ceiling "
      "AND ((action = 'ALLOW' AND allocated_amount_cap = requested_amount_ceiling) OR "
      "(action = 'CAP' AND allocated_amount_cap > 0 AND allocated_amount_cap < requested_amount_ceiling) OR "
      "(action IN ('DELAY','REJECT') AND allocated_amount_cap = 0))",
      name="ck_t_allocation_decision_amount",
    ),
    CheckConstraint(
      "(action = 'DELAY' AND next_eligible_at IS NOT NULL AND next_eligible_at > created_at AND next_eligible_at < expires_at) "
      "OR (action <> 'DELAY' AND next_eligible_at IS NULL)",
      name="ck_t_allocation_decision_delay",
    ),
  )
  decision_id = Column(String(80), primary_key=True)
  allocation_batch_id = Column(
    String(36),
    ForeignKey("t_allocation_batches.allocation_batch_id", ondelete="RESTRICT"),
    nullable=False,
  )
  intent_id = Column(
    String(36), ForeignKey("trade_intents.id", ondelete="RESTRICT"), nullable=False
  )
  intent_version = Column(Integer, nullable=False)
  candidate_id = Column(String(128), nullable=False)
  instrument_code = Column(String(20), nullable=False)
  rank = Column(Integer, nullable=False)
  action = Column(String(8), nullable=False)
  requested_amount_ceiling = Column(Numeric(24, 8), nullable=False)
  allocated_amount_cap = Column(Numeric(24, 8), nullable=False)
  evidence = Column(JSON, nullable=False)
  created_at = Column(DateTime(timezone=True), nullable=False)
  expires_at = Column(DateTime(timezone=True), nullable=False)
  next_eligible_at = Column(DateTime(timezone=True), nullable=True)
