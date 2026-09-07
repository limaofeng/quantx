"""PAPER execution facts; never rows in broker orders, trades or Agent outbox."""

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
)

from quantx_infrastructure.database.relational_base import Base


class PaperExecutionAccountRecord(Base):
  """One isolated scenario's materialized ledger and immutable seed evidence."""

  __tablename__ = "paper_execution_accounts"
  __table_args__ = (
    CheckConstraint("environment = 'PAPER'", name="ck_paper_account_environment"),
    CheckConstraint(
      "matching_policy_version = 'paper-strict-book-v2'",
      name="ck_paper_account_matching_policy",
    ),
    CheckConstraint("revision >= 0", name="ck_paper_account_revision"),
    CheckConstraint(
      "length(seed_snapshot_hash) = 64 AND length(snapshot_hash) = 64 AND length(initial_snapshot_hash) = 64",
      name="ck_paper_account_hashes",
    ),
    CheckConstraint("snapshot_as_of >= seed_as_of", name="ck_paper_account_causality"),
  )
  execution_id = Column(
    String(36),
    ForeignKey("t_assistant_executions.execution_id", ondelete="RESTRICT"),
    primary_key=True,
  )
  account_id = Column(String(50), nullable=False)
  environment = Column(String(16), nullable=False)
  seed_snapshot_id = Column(String(128), nullable=False)
  seed_snapshot_hash = Column(String(64), nullable=False)
  seed_as_of = Column(DateTime(timezone=True), nullable=False)
  seed_payload = Column(JSON, nullable=False)
  matching_policy_version = Column(String(64), nullable=False)
  broker_checkpoint = Column(JSON, nullable=False)
  bucket_checkpoint = Column(JSON, nullable=False)
  revision = Column(Integer, nullable=False)
  snapshot_hash = Column(String(64), nullable=False)
  initial_snapshot_hash = Column(String(64), nullable=False)
  snapshot_as_of = Column(DateTime(timezone=True), nullable=False)


class PaperExecutionEventRecord(Base):
  """Idempotent command/quote input and resulting committed ledger revision."""

  __tablename__ = "paper_execution_events"
  __table_args__ = (
    UniqueConstraint("execution_id", "revision", name="uq_paper_event_revision"),
    UniqueConstraint("execution_id", "event_key", name="uq_paper_event_key"),
    Index(
      "ix_paper_event_scope_type_time", "execution_id", "event_type", "occurred_at"
    ),
    Index(
      "ix_paper_event_scope_quote_source",
      "execution_id",
      "event_type",
      "quote_source_at",
    ),
    CheckConstraint(
      "(event_type = 'QUOTE' AND quote_source_at IS NOT NULL AND quote_source_at <= occurred_at) OR "
      "(event_type <> 'QUOTE' AND quote_source_at IS NULL)",
      name="ck_paper_event_quote_clock",
    ),
    CheckConstraint("environment = 'PAPER'", name="ck_paper_event_environment"),
    CheckConstraint("revision >= 1", name="ck_paper_event_revision"),
    CheckConstraint(
      "event_type IN ('ORDER','QUOTE','CANCEL')", name="ck_paper_event_type"
    ),
    CheckConstraint(
      "length(input_hash) = 64 AND length(resulting_snapshot_hash) = 64 AND length(previous_snapshot_hash) = 64",
      name="ck_paper_event_hashes",
    ),
  )
  event_id = Column(String(80), primary_key=True)
  execution_id = Column(
    String(36),
    ForeignKey("paper_execution_accounts.execution_id", ondelete="RESTRICT"),
    nullable=False,
  )
  environment = Column(String(16), nullable=False)
  event_key = Column(String(256), nullable=False)
  event_type = Column(String(16), nullable=False)
  revision = Column(Integer, nullable=False)
  input_hash = Column(String(64), nullable=False)
  input_payload = Column(JSON, nullable=False)
  result_payload = Column(JSON, nullable=False)
  resulting_snapshot_hash = Column(String(64), nullable=False)
  previous_snapshot_hash = Column(String(64), nullable=False)
  occurred_at = Column(DateTime(timezone=True), nullable=False)
  quote_source_at = Column(
    DateTime(timezone=True),
    nullable=True,
    comment="QUOTE 原始行情源时间；occurred_at 为本地受理时间",
  )


class PaperExecutionOrderRecord(Base):
  __tablename__ = "paper_execution_orders"
  __table_args__ = (
    UniqueConstraint(
      "execution_id", "intent_id", "order_attempt", name="uq_paper_order_intent_attempt"
    ),
    CheckConstraint("environment = 'PAPER'", name="ck_paper_order_environment"),
    CheckConstraint(
      "owner_type IN ('T_ASSISTANT_EXECUTION','EXIT_PLAN')",
      name="ck_paper_order_owner_type",
    ),
    CheckConstraint("side IN ('BUY','SELL')", name="ck_paper_order_side"),
    CheckConstraint(
      "status IN ('PENDING','SUBMITTED','PARTIAL_FILLED','FILLED','CANCELLED','REJECTED','EXPIRED')",
      name="ck_paper_order_status",
    ),
    CheckConstraint(
      "order_attempt >= 0 AND volume > 0 AND filled_volume >= 0 AND filled_volume <= volume",
      name="ck_paper_order_volume",
    ),
    CheckConstraint(
      "(status = 'FILLED' AND filled_volume = volume) OR "
      "(status = 'PARTIAL_FILLED' AND filled_volume > 0 AND filled_volume < volume) OR "
      "(status IN ('PENDING','SUBMITTED','REJECTED') AND filled_volume = 0) OR "
      "status IN ('CANCELLED','EXPIRED')",
      name="ck_paper_order_filled_status",
    ),
    CheckConstraint(
      "limit_price > 0 AND limit_price <> 'NaN'",
      name="ck_paper_order_price",
    ),
    CheckConstraint("expires_at > submitted_at", name="ck_paper_order_ttl"),
    Index("ix_paper_order_active", "execution_id", "status", "instrument_code"),
  )
  order_id = Column(String(80), primary_key=True)
  execution_id = Column(
    String(36),
    ForeignKey("paper_execution_accounts.execution_id", ondelete="RESTRICT"),
    nullable=False,
  )
  environment = Column(String(16), nullable=False)
  owner_type = Column(String(32), nullable=False)
  owner_id = Column(String(128), nullable=False)
  intent_id = Column(
    String(36), ForeignKey("trade_intents.id", ondelete="RESTRICT"), nullable=False
  )
  allocation_decision_id = Column(
    String(80),
    ForeignKey("t_allocation_decisions.decision_id", ondelete="RESTRICT"),
    nullable=True,
  )
  admission_batch_id = Column(
    String(36),
    ForeignKey(
      "account_risk_increase_admission_batches.admission_batch_id", ondelete="RESTRICT"
    ),
    nullable=True,
  )
  instrument_code = Column(String(20), nullable=False)
  order_attempt = Column(Integer, nullable=False)
  side = Column(String(4), nullable=False)
  volume = Column(Integer, nullable=False)
  limit_price = Column(Numeric(24, 8), nullable=False)
  filled_volume = Column(Integer, nullable=False)
  status = Column(String(24), nullable=False)
  request_payload = Column(JSON, nullable=False)
  response_payload = Column(JSON, nullable=False)
  sizing_evidence = Column(JSON, nullable=False)
  risk_evidence = Column(JSON, nullable=False)
  last_event_id = Column(
    String(80),
    ForeignKey("paper_execution_events.event_id", ondelete="RESTRICT"),
    nullable=False,
  )
  submitted_at = Column(DateTime(timezone=True), nullable=False)
  expires_at = Column(DateTime(timezone=True), nullable=False)


class PaperExecutionFillRecord(Base):
  __tablename__ = "paper_execution_fills"
  __table_args__ = (
    CheckConstraint("environment = 'PAPER'", name="ck_paper_fill_environment"),
    CheckConstraint("volume > 0", name="ck_paper_fill_volume"),
    CheckConstraint(
      "price > 0 AND price <> 'NaN' AND fee >= 0 AND fee <> 'NaN'",
      name="ck_paper_fill_amounts",
    ),
    Index("ix_paper_fill_order", "order_id", "occurred_at"),
  )
  fill_id = Column(String(80), primary_key=True)
  execution_id = Column(
    String(36),
    ForeignKey("paper_execution_accounts.execution_id", ondelete="RESTRICT"),
    nullable=False,
  )
  environment = Column(String(16), nullable=False)
  order_id = Column(
    String(80),
    ForeignKey("paper_execution_orders.order_id", ondelete="RESTRICT"),
    nullable=False,
  )
  event_id = Column(
    String(80),
    ForeignKey("paper_execution_events.event_id", ondelete="RESTRICT"),
    nullable=False,
  )
  volume = Column(Integer, nullable=False)
  price = Column(Numeric(24, 8), nullable=False)
  fee = Column(Numeric(24, 8), nullable=False)
  occurred_at = Column(DateTime(timezone=True), nullable=False)
  trade_payload = Column(JSON, nullable=False)
