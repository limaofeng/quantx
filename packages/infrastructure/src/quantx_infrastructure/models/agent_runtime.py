"""Durable QMT-agent, component-heartbeat, and message-box models."""

from sqlalchemy import (
  JSON,
  BigInteger,
  Boolean,
  Column,
  DateTime,
  Float,
  ForeignKey,
  Index,
  Integer,
  String,
  Text,
  UniqueConstraint,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class AgentDevice(Base, TimestampMixin):
  __tablename__ = "agent_devices"
  __table_args__ = (
    Index("ix_agent_devices_user_revoked", "user_id", "revoked_at"),
  )

  id = Column(String(36), primary_key=True)
  user_id = Column(
    String(36),
    ForeignKey("auth_users.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )
  name = Column(String(120), nullable=False)
  secret_hash = Column(String(64), nullable=False)
  authorized_account_ids = Column(JSON, nullable=False, default=list)
  capabilities = Column(JSON, nullable=False, default=list)
  last_seen_at = Column(DateTime, nullable=True)
  revoked_at = Column(DateTime, nullable=True)


class AgentEnrollmentCode(Base):
  __tablename__ = "agent_enrollment_codes"
  __table_args__ = (
    Index("ix_agent_enrollment_expiry", "expires_at"),
  )

  code_hash = Column(String(64), primary_key=True)
  user_id = Column(
    String(36),
    ForeignKey("auth_users.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )
  name = Column(String(120), nullable=False)
  authorized_account_ids = Column(JSON, nullable=False, default=list)
  created_at = Column(DateTime, nullable=False)
  expires_at = Column(DateTime, nullable=False)
  consumed_at = Column(DateTime, nullable=True)


class RuntimeComponentHeartbeat(Base):
  __tablename__ = "runtime_component_heartbeats"

  component = Column(String(48), primary_key=True)
  instance_id = Column(String(64), nullable=False)
  status = Column(String(32), nullable=False)
  details = Column(JSON, nullable=False, default=dict)
  updated_at = Column(DateTime, nullable=False, index=True)


class EngineCommandOutbox(Base, TimestampMixin):
  """Durable control-plane command consumed exclusively by the Engine."""

  __tablename__ = "engine_command_outbox"
  __table_args__ = (
    UniqueConstraint("idempotency_key", name="uq_engine_command_idempotency"),
    Index("ix_engine_command_processing", "processing_status", "created_at"),
  )

  message_id = Column(String(36), primary_key=True)
  idempotency_key = Column(String(160), nullable=False)
  command_type = Column(String(64), nullable=False)
  aggregate_id = Column(String(128), nullable=True, index=True)
  payload = Column(JSON, nullable=False, default=dict)
  processing_status = Column(String(24), nullable=False, default="PENDING")
  processing_attempts = Column(Integer, nullable=False, default=0)
  result = Column(JSON, nullable=True)
  processing_error = Column(Text, nullable=True)
  available_at = Column(DateTime, nullable=False)
  processed_at = Column(DateTime, nullable=True)


class TradeCommandOutbox(Base, TimestampMixin):
  __tablename__ = "trade_command_outbox"
  __table_args__ = (
    UniqueConstraint("client_order_id", name="uq_trade_command_client_order"),
    UniqueConstraint("idempotency_key", name="uq_trade_command_idempotency"),
    Index("ix_trade_command_delivery", "device_id", "delivery_status", "created_at"),
  )

  message_id = Column(String(36), primary_key=True)
  client_order_id = Column(String(128), nullable=False)
  idempotency_key = Column(String(128), nullable=False)
  device_id = Column(
    String(36),
    ForeignKey("agent_devices.id", ondelete="RESTRICT"),
    nullable=False,
  )
  account_id = Column(String(50), nullable=False, index=True)
  payload = Column(JSON, nullable=False)
  delivery_status = Column(String(24), nullable=False, default="QUEUED")
  delivered_at = Column(DateTime, nullable=True)
  acknowledged_at = Column(DateTime, nullable=True)
  expires_at = Column(DateTime, nullable=False)
  attempts = Column(Integer, nullable=False, default=0)
  last_error = Column(String(256), nullable=True)


class PendingTradeOrder(Base, TimestampMixin):
  """Server-side truth for a command before a broker order id exists."""

  __tablename__ = "pending_trade_orders"
  __table_args__ = (
    Index(
      "ix_pending_trade_order_account_batch_client",
      "account_id",
      "batch_id",
      "client_order_id",
    ),
  )

  client_order_id = Column(String(128), primary_key=True)
  user_id = Column(
    String(36),
    ForeignKey("auth_users.id", ondelete="RESTRICT"),
    nullable=False,
    index=True,
  )
  account_id = Column(String(50), nullable=False, index=True)
  instrument_code = Column(String(20), nullable=False)
  side = Column(String(16), nullable=False)
  order_type = Column(String(24), nullable=False)
  limit_price = Column(String(32), nullable=False)
  volume = Column(Integer, nullable=False)
  status = Column(String(24), nullable=False, default="QUEUED")
  broker_order_id = Column(String(128), nullable=True, index=True)
  status_reason = Column(String(256), nullable=True)
  execution_mode = Column(String(16), nullable=False, default="paper")
  strategy_run_id = Column(String(36), nullable=True, index=True)
  strategy_order_id = Column(String(128), nullable=True, index=True)
  intent_id = Column(String(128), nullable=True, index=True)
  batch_id = Column(String(36), nullable=True, index=True)
  bucket = Column(String(32), nullable=False, default="manual")
  t_trade_role = Column(String(16), nullable=True)
  risk_decision_id = Column(String(128), nullable=True)
  trace_id = Column(String(128), nullable=True)
  substitution_plan = Column(JSON, nullable=True)
  request_metadata = Column(JSON, nullable=False, default=dict)
  last_source_sequence = Column(BigInteger, nullable=False, default=0)
  last_source_event_at = Column(DateTime, nullable=True)


class StrategyOrderCorrelation(Base, TimestampMixin):
  """Restart-safe mapping between a strategy order and broker reports."""

  __tablename__ = "strategy_order_correlations"
  __table_args__ = (
    UniqueConstraint("client_order_id", name="uq_strategy_order_client"),
    Index("ix_strategy_order_run_batch", "strategy_run_id", "batch_id"),
  )

  id = Column(String(36), primary_key=True)
  client_order_id = Column(
    String(128),
    ForeignKey("pending_trade_orders.client_order_id", ondelete="CASCADE"),
    nullable=False,
  )
  broker_order_id = Column(String(128), nullable=True, index=True)
  account_id = Column(String(50), nullable=False, index=True)
  strategy_run_id = Column(String(36), nullable=False, index=True)
  strategy_order_id = Column(String(128), nullable=False)
  intent_id = Column(String(128), nullable=False)
  batch_id = Column(String(36), nullable=True, index=True)
  bucket = Column(String(32), nullable=False)
  t_trade_role = Column(String(16), nullable=True)
  execution_mode = Column(String(16), nullable=False)
  risk_decision_id = Column(String(128), nullable=True)
  trace_id = Column(String(128), nullable=False)
  substitution_plan = Column(JSON, nullable=True)
  request_metadata = Column(JSON, nullable=False, default=dict)


class StrategyRuntimeEvent(Base):
  """Durable, exactly-once input waiting to be applied by the Engine."""

  __tablename__ = "strategy_runtime_events"
  __table_args__ = (
    UniqueConstraint("business_key", name="uq_strategy_runtime_event_business"),
    Index("ix_strategy_runtime_event_apply", "application_status", "created_at"),
    Index(
      "ix_strategy_runtime_event_client_created",
      "client_order_id",
      "created_at",
      "event_id",
    ),
  )

  event_id = Column(String(36), primary_key=True)
  business_key = Column(String(192), nullable=False)
  strategy_run_id = Column(String(36), nullable=False, index=True)
  client_order_id = Column(String(128), nullable=False, index=True)
  broker_order_id = Column(String(128), nullable=True, index=True)
  event_type = Column(String(24), nullable=False)
  payload = Column(JSON, nullable=False)
  application_status = Column(String(24), nullable=False, default="PENDING")
  application_attempts = Column(Integer, nullable=False, default=0)
  application_error = Column(Text, nullable=True)
  created_at = Column(DateTime, nullable=False)
  applied_at = Column(DateTime, nullable=True)


class TTradeBatch(Base, TimestampMixin):
  """Operational read model for one positive-T entry/exit lifecycle."""

  __tablename__ = "t_trade_batches"
  __table_args__ = (
    Index("ix_t_trade_batch_account_status", "account_id", "status"),
    Index(
      "ix_t_trade_batch_account_updated",
      "account_id",
      "updated_at",
      "batch_id",
    ),
  )

  batch_id = Column(String(36), primary_key=True)
  account_id = Column(String(50), nullable=False, index=True)
  instrument_code = Column(String(20), nullable=False, index=True)
  strategy_run_id = Column(String(36), nullable=False, index=True)
  status = Column(String(32), nullable=False, default="AWAITING_ENTRY_APPROVAL")
  entry_intent_id = Column(String(128), nullable=True)
  exit_intent_id = Column(String(128), nullable=True)
  entry_client_order_id = Column(String(128), nullable=True, index=True)
  exit_client_order_id = Column(String(128), nullable=True, index=True)
  entry_broker_order_id = Column(String(128), nullable=True)
  exit_broker_order_id = Column(String(128), nullable=True)
  target_volume = Column(Integer, nullable=False, default=0)
  entry_filled_volume = Column(Integer, nullable=False, default=0)
  entry_avg_price = Column(Float, nullable=False, default=0.0)
  exit_filled_volume = Column(Integer, nullable=False, default=0)
  exit_avg_price = Column(Float, nullable=False, default=0.0)
  last_price = Column(Float, nullable=False, default=0.0)
  last_net_profit_pct = Column(Float, nullable=False, default=0.0)
  peak_net_profit_pct = Column(Float, nullable=False, default=0.0)
  trailing_floor_pct = Column(Float, nullable=True)
  exit_reason = Column(String(64), nullable=True)
  exception_reason = Column(Text, nullable=True)
  policy_version = Column(Integer, nullable=False, default=0)
  version = Column(Integer, nullable=False, default=1)


class AccountTradingRollout(Base, TimestampMixin):
  """Server-side rollout and kill-switch state for one account."""

  __tablename__ = "account_trading_rollouts"

  account_id = Column(String(50), primary_key=True)
  stage = Column(String(24), nullable=False, default="SHADOW")
  enabled = Column(Boolean, nullable=False, default=False)
  kill_switch = Column(Boolean, nullable=False, default=False)
  reconcile_status = Column(String(32), nullable=False, default="UNKNOWN")
  max_active_batches = Column(Integer, nullable=False, default=1)
  max_batch_volume = Column(Integer, nullable=False, default=100)
  max_order_amount = Column(Float, nullable=False, default=20000.0)
  max_total_exposure_pct = Column(Float, nullable=False, default=0.02)
  policy_version = Column(Integer, nullable=False, default=1)
  acknowledged_policy_version = Column(Integer, nullable=False, default=0)
  activated_by_user_id = Column(String(36), nullable=True)
  activated_at = Column(DateTime, nullable=True)
  paused_reason = Column(Text, nullable=True)
  last_snapshot_id = Column(String(128), nullable=True)
  last_snapshot_hash = Column(String(64), nullable=True)
  last_snapshot_at = Column(DateTime, nullable=True)
  last_backup_at = Column(DateTime, nullable=True)
  controlled_window_active = Column(Boolean, nullable=False, default=False)
  controlled_window_snapshot_id = Column(String(128), nullable=True)
  controlled_window_snapshot_hash = Column(String(64), nullable=True)
  controlled_window_started_at = Column(DateTime, nullable=True)
  controlled_window_started_by_user_id = Column(String(36), nullable=True)
  controlled_window_external_order_ids = Column(JSON, nullable=False, default=list)
  controlled_window_external_trade_ids = Column(JSON, nullable=False, default=list)


class AccountTradingRolloutEvent(Base):
  """Append-only audit event for account rollout state changes."""

  __tablename__ = "account_trading_rollout_events"
  __table_args__ = (
    Index(
      "ix_account_trading_rollout_event_account_created",
      "account_id",
      "created_at",
    ),
  )

  event_id = Column(String(36), primary_key=True)
  account_id = Column(String(50), nullable=False, index=True)
  event_type = Column(String(64), nullable=False)
  actor_user_id = Column(String(36), nullable=True)
  previous_stage = Column(String(24), nullable=True)
  next_stage = Column(String(24), nullable=True)
  snapshot_id = Column(String(128), nullable=True)
  details = Column(JSON, nullable=False, default=dict)
  created_at = Column(DateTime, nullable=False)


class AgentReportInbox(Base):
  __tablename__ = "agent_report_inbox"
  __table_args__ = (
    Index("ix_agent_report_processing", "processing_status", "received_at"),
    UniqueConstraint(
      "business_idempotency_key",
      name="uq_agent_report_business_idempotency",
    ),
  )

  message_id = Column(String(36), primary_key=True)
  device_id = Column(
    String(36),
    ForeignKey("agent_devices.id", ondelete="RESTRICT"),
    nullable=False,
    index=True,
  )
  message_type = Column(String(32), nullable=False)
  protocol_version = Column(String(16), nullable=False, default="1.0")
  client_order_id = Column(String(128), nullable=True, index=True)
  raw_payload_hash = Column(String(64), nullable=False)
  business_idempotency_key = Column(String(128), nullable=False)
  payload = Column(JSON, nullable=False)
  received_at = Column(DateTime, nullable=False)
  processing_status = Column(String(24), nullable=False, default="PENDING")
  processing_attempts = Column(Integer, nullable=False, default=0)
  next_attempt_at = Column(DateTime, nullable=True)
  processed_at = Column(DateTime, nullable=True)
  processing_error = Column(Text, nullable=True)


class OperationalAlert(Base):
  """Persistent operational incident with an explicit ownership lifecycle."""

  __tablename__ = "operational_alerts"
  __table_args__ = (
    Index(
      "ix_operational_alert_status_severity_last_seen",
      "status",
      "severity",
      "last_seen_at",
    ),
    Index("ix_operational_alert_account_status", "account_id", "status"),
  )

  id = Column(String(36), primary_key=True)
  fingerprint = Column(String(64), nullable=False, unique=True)
  severity = Column(String(16), nullable=False)
  source = Column(String(64), nullable=False)
  code = Column(String(64), nullable=False)
  account_id = Column(String(50), nullable=True, index=True)
  business_id = Column(String(192), nullable=True, index=True)
  message = Column(Text, nullable=False)
  details = Column(JSON, nullable=False, default=dict)
  status = Column(String(24), nullable=False, default="OPEN")
  occurrences = Column(Integer, nullable=False, default=1)
  first_seen_at = Column(DateTime, nullable=False)
  last_seen_at = Column(DateTime, nullable=False)
  acknowledged_by = Column(String(36), nullable=True)
  acknowledged_at = Column(DateTime, nullable=True)
  resolved_by = Column(String(36), nullable=True)
  resolved_at = Column(DateTime, nullable=True)
  resolution = Column(Text, nullable=True)


class MarketDataRequest(Base, TimestampMixin):
  __tablename__ = "market_data_request"
  __table_args__ = (
    UniqueConstraint("idempotency_key", name="uq_market_data_request_idempotency"),
    Index("ix_market_data_request_status", "status", "created_at"),
  )

  request_id = Column(String(36), primary_key=True)
  device_id = Column(
    String(36),
    ForeignKey("agent_devices.id", ondelete="RESTRICT"),
    nullable=False,
  )
  idempotency_key = Column(String(128), nullable=False)
  request_payload = Column(JSON, nullable=False)
  status = Column(String(24), nullable=False, default="QUEUED")
  expected_chunks = Column(Integer, nullable=True)
  received_chunks = Column(Integer, nullable=False, default=0)
  completed_at = Column(DateTime, nullable=True)
  processing_error = Column(Text, nullable=True)


class MarketDataTransfer(Base):
  __tablename__ = "market_data_transfer"
  __table_args__ = (
    UniqueConstraint(
      "request_id",
      "chunk_index",
      name="uq_market_data_transfer_chunk",
    ),
  )

  transfer_id = Column(String(36), primary_key=True)
  request_id = Column(
    String(36),
    ForeignKey("market_data_request.request_id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )
  chunk_index = Column(Integer, nullable=False)
  checksum_sha256 = Column(String(64), nullable=False)
  record_count = Column(Integer, nullable=False)
  compressed = Column(Boolean, nullable=False, default=True)
  storage_reference = Column(String(512), nullable=False)
  received_at = Column(DateTime, nullable=False)
