"""Durable QMT-agent, component-heartbeat, and message-box models."""

from quantx_contracts import PROTOCOL_VERSION
from sqlalchemy import (
  JSON,
  BigInteger,
  Boolean,
  CheckConstraint,
  Column,
  DateTime,
  Float,
  ForeignKey,
  Index,
  Integer,
  String,
  Text,
  UniqueConstraint,
  func,
  literal,
  text,
)

from quantx_infrastructure.database.relational_base import Base, TimestampMixin
from quantx_infrastructure.models.execution_owner import (
  SOURCE_EXECUTION_FIELDS,
  register_identity_immutability,
)


class AgentDevice(Base, TimestampMixin):
  __tablename__ = "agent_devices"
  __table_args__ = (Index("ix_agent_devices_user_revoked", "user_id", "revoked_at"),)

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
  replaces_device_id = Column(
    String(36),
    ForeignKey("agent_devices.id", ondelete="RESTRICT"),
    nullable=True,
    index=True,
  )


class AgentEnrollmentCode(Base):
  __tablename__ = "agent_enrollment_codes"
  __table_args__ = (Index("ix_agent_enrollment_expiry", "expires_at"),)

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
  replaces_device_id = Column(
    String(36),
    ForeignKey("agent_devices.id", ondelete="RESTRICT"),
    nullable=True,
  )


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
    CheckConstraint(
      "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION','ENTRY_PLAN',"
      "'BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND')",
      name="ck_trade_command_owner_type",
    ),
    CheckConstraint(
      "length(owner_id) > 0 AND owner_id = trim(owner_id)",
      name="ck_trade_command_owner_id",
    ),
    CheckConstraint(
      "environment IN ('PAPER','LIVE','BACKTEST')",
      name="ck_trade_command_environment",
    ),
    UniqueConstraint("client_order_id", name="uq_trade_command_client_order"),
    UniqueConstraint("idempotency_key", name="uq_trade_command_idempotency"),
    Index("ix_trade_command_delivery", "device_id", "delivery_status", "created_at"),
    Index(
      "ix_trade_command_device_status_expiry_created",
      "device_id",
      "delivery_status",
      "expires_at",
      "created_at",
    ),
    Index(
      "ix_trade_command_device_status_delivery_expiry",
      "device_id",
      "delivery_status",
      "delivered_at",
      "expires_at",
    ),
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
  owner_type = Column(String(32), nullable=False)
  owner_id = Column(String(128), nullable=False)
  environment = Column(String(16), nullable=False)
  payload = Column(JSON, nullable=False)
  delivery_status = Column(String(24), nullable=False, default="QUEUED")
  delivered_at = Column(DateTime, nullable=True)
  acknowledged_at = Column(DateTime, nullable=True)
  expires_at = Column(DateTime, nullable=False)
  attempts = Column(Integer, nullable=False, default=0)
  last_error = Column(String(256), nullable=True)


Index(
  "ix_trade_command_device_status_kind_created",
  TradeCommandOutbox.device_id,
  TradeCommandOutbox.delivery_status,
  func.upper(TradeCommandOutbox.payload.op("->>")("command_kind")),
  TradeCommandOutbox.created_at,
)


class PendingTradeOrder(Base, TimestampMixin):
  """Server-side truth for a command before a broker order id exists."""

  __tablename__ = "pending_trade_orders"
  __table_args__ = (
    CheckConstraint(
      "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION','ENTRY_PLAN',"
      "'BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND')",
      name="ck_pending_trade_order_owner_type",
    ),
    CheckConstraint(
      "length(owner_id) > 0 AND owner_id = trim(owner_id)",
      name="ck_pending_trade_order_owner_id",
    ),
    CheckConstraint(
      "environment IN ('PAPER','LIVE','BACKTEST')",
      name="ck_pending_trade_order_environment",
    ),
    CheckConstraint(
      "strategy_run_id IS NULL OR (owner_type = 'STRATEGY_RUN' "
      "AND owner_id = strategy_run_id)",
      name="ck_pending_trade_order_strategy_run_owner",
    ),
    CheckConstraint(
      "(owner_type = 'STRATEGY_RUN' AND intent_id IS NOT NULL "
      "AND strategy_order_id IS NOT NULL) OR "
      "(owner_type = 'T_ASSISTANT_EXECUTION' AND intent_id IS NOT NULL "
      "AND strategy_run_id IS NULL AND strategy_order_id IS NULL) OR "
      "(owner_type = 'EXIT_PLAN' AND intent_id IS NOT NULL "
      "AND strategy_order_id IS NULL) OR "
      "(owner_type = 'MANUAL_COMMAND' AND intent_id IS NULL "
      "AND strategy_order_id IS NULL)",
      name="ck_pending_trade_order_strategy_identity",
    ),
    Index(
      "ix_pending_trade_order_account_batch_client",
      "account_id",
      "batch_id",
      "client_order_id",
    ),
    UniqueConstraint("t_order_parent_client_id", name="uq_t_order_parent_attempt"),
    CheckConstraint("t_order_attempt >= 0", name="ck_t_order_attempt_nonnegative"),
    Index(
      "uq_t_order_intent_attempt", "intent_id", "t_order_attempt", unique=True,
      postgresql_where=text("t_trade_role IN ('ENTRY','EXIT')"),
      sqlite_where=text("t_trade_role IN ('ENTRY','EXIT')"),
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
  owner_type = Column(String(32), nullable=False)
  owner_id = Column(String(128), nullable=False)
  environment = Column(String(16), nullable=False)
  instrument_code = Column(String(20), nullable=False)
  side = Column(String(16), nullable=False)
  order_type = Column(String(24), nullable=False)
  limit_price = Column(String(32), nullable=False)
  volume = Column(Integer, nullable=False)
  status = Column(String(24), nullable=False, default="QUEUED")
  broker_order_id = Column(String(128), nullable=True, index=True)
  status_reason = Column(String(256), nullable=True)
  strategy_run_id = Column(String(36), nullable=True, index=True)
  strategy_order_id = Column(String(128), nullable=True, index=True)
  intent_id = Column(String(128), nullable=True, index=True)
  batch_id = Column(String(36), nullable=True, index=True)
  bucket = Column(String(32), nullable=False, default="manual")
  t_trade_role = Column(String(16), nullable=True)
  t_order_attempt = Column(Integer, nullable=False, default=0, server_default="0")
  t_order_parent_client_id = Column(
    String(128), ForeignKey("pending_trade_orders.client_order_id"), nullable=True,
  )
  t_order_original_created_at = Column(DateTime, nullable=True)
  risk_decision_id = Column(String(128), nullable=True)
  trace_id = Column(String(128), nullable=True)
  substitution_plan = Column(JSON, nullable=True)
  request_metadata = Column(JSON, nullable=False, default=dict)
  last_source_sequence = Column(BigInteger, nullable=False, default=0)
  last_source_event_at = Column(DateTime, nullable=True)


class OrderCorrelation(Base, TimestampMixin):
  """Restart-safe mapping between a strategy order and broker reports."""

  __tablename__ = "order_correlations"
  __table_args__ = (
    CheckConstraint(
      "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION','ENTRY_PLAN',"
      "'BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND')",
      name="ck_order_correlation_owner_type",
    ),
    CheckConstraint(
      "length(owner_id) > 0 AND owner_id = trim(owner_id)",
      name="ck_order_correlation_owner_id",
    ),
    CheckConstraint(
      "environment IN ('PAPER','LIVE','BACKTEST')",
      name="ck_order_correlation_environment",
    ),
    CheckConstraint(
      "strategy_run_id IS NULL OR (owner_type = 'STRATEGY_RUN' "
      "AND owner_id = strategy_run_id)",
      name="ck_order_correlation_strategy_run_owner",
    ),
    CheckConstraint(
      "(owner_type = 'STRATEGY_RUN' AND intent_id IS NOT NULL "
      "AND strategy_order_id IS NOT NULL) OR "
      "(owner_type = 'T_ASSISTANT_EXECUTION' AND intent_id IS NOT NULL "
      "AND strategy_run_id IS NULL AND strategy_order_id IS NULL) OR "
      "(owner_type = 'EXIT_PLAN' AND intent_id IS NOT NULL "
      "AND strategy_order_id IS NULL) OR "
      "(owner_type = 'MANUAL_COMMAND' AND intent_id IS NULL "
      "AND strategy_order_id IS NULL)",
      name="ck_order_correlation_strategy_identity",
    ),
    UniqueConstraint("client_order_id", name="uq_order_correlation_client"),
    Index("ix_order_correlation_owner_batch", "owner_type", "owner_id", "batch_id"),
  )

  id = Column(String(36), primary_key=True)
  client_order_id = Column(
    String(128),
    ForeignKey("pending_trade_orders.client_order_id", ondelete="CASCADE"),
    nullable=False,
  )
  broker_order_id = Column(String(128), nullable=True, index=True)
  account_id = Column(String(50), nullable=False, index=True)
  owner_type = Column(String(32), nullable=False)
  owner_id = Column(String(128), nullable=False)
  environment = Column(String(16), nullable=False)
  strategy_run_id = Column(String(36), nullable=True, index=True)
  # Manual commands are first-class correlations and do not fabricate a
  # strategy order or intent identity.
  strategy_order_id = Column(String(128), nullable=True)
  intent_id = Column(String(128), nullable=True)
  batch_id = Column(String(36), nullable=True, index=True)
  bucket = Column(String(32), nullable=False)
  t_trade_role = Column(String(16), nullable=True)
  risk_decision_id = Column(String(128), nullable=True)
  trace_id = Column(String(128), nullable=False)
  substitution_plan = Column(JSON, nullable=True)
  request_metadata = Column(JSON, nullable=False, default=dict)


class StrategyRuntimeEvent(Base):
  """Durable, exactly-once input waiting to be applied by the Engine."""

  __tablename__ = "strategy_runtime_events"
  __table_args__ = (
    CheckConstraint(
      "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION','ENTRY_PLAN',"
      "'BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND')",
      name="ck_strategy_runtime_event_owner_type",
    ),
    CheckConstraint(
      "length(owner_id) > 0 AND owner_id = trim(owner_id)",
      name="ck_strategy_runtime_event_owner_id",
    ),
    CheckConstraint(
      "environment IN ('PAPER','LIVE','BACKTEST')",
      name="ck_strategy_runtime_event_environment",
    ),
    CheckConstraint(
      "strategy_run_id IS NULL OR (owner_type = 'STRATEGY_RUN' "
      "AND owner_id = strategy_run_id)",
      name="ck_strategy_runtime_event_strategy_run_owner",
    ),
    UniqueConstraint("business_key", name="uq_strategy_runtime_event_business"),
    Index("ix_strategy_runtime_event_apply", "application_status", "created_at"),
    Index(
      "ix_strategy_runtime_event_client_created",
      "client_order_id",
      "created_at",
      "event_id",
    ),
    Index(
      "ix_strategy_runtime_event_owner_created",
      "owner_type",
      "owner_id",
      "created_at",
      "event_id",
    ),
  )

  event_id = Column(String(36), primary_key=True)
  business_key = Column(String(192), nullable=False)
  owner_type = Column(String(32), nullable=False)
  owner_id = Column(String(128), nullable=False)
  environment = Column(String(16), nullable=False)
  strategy_run_id = Column(String(36), nullable=True, index=True)
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
    CheckConstraint(
      "source_execution_owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION',"
      "'ENTRY_PLAN','BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND')",
      name="ck_t_trade_batch_source_owner_type",
    ),
    CheckConstraint(
      "length(source_execution_owner_id) > 0 AND "
      "source_execution_owner_id = trim(source_execution_owner_id)",
      name="ck_t_trade_batch_source_owner_id",
    ),
    CheckConstraint(
      "source_execution_environment IN ('PAPER','LIVE','BACKTEST')",
      name="ck_t_trade_batch_source_environment",
    ),
    CheckConstraint(
      "source_execution_environment = environment",
      name="ck_t_trade_batch_source_environment_match",
    ),
    CheckConstraint(
      "strategy_run_id IS NULL OR (source_execution_owner_type = 'STRATEGY_RUN' "
      "AND source_execution_owner_id = strategy_run_id)",
      name="ck_t_trade_batch_strategy_run_owner",
    ),
    Index("ix_t_trade_batch_account_status", "account_id", "status"),
    Index(
      "ix_t_trade_batch_account_updated",
      "account_id",
      "updated_at",
      "batch_id",
    ),
    Index(
      "ix_t_trade_batch_account_environment",
      "account_id",
      "environment",
    ),
    Index(
      "ix_t_trade_batch_account_closed",
      "account_id",
      "closed_at",
      "batch_id",
    ),
    Index(
      "ix_t_trade_batch_account_terminal",
      "account_id",
      "terminal_at",
      "batch_id",
    ),
  )

  batch_id = Column(String(36), primary_key=True)
  account_id = Column(String(50), nullable=False, index=True)
  instrument_code = Column(String(20), nullable=False, index=True)
  strategy_run_id = Column(String(36), nullable=True, index=True)
  source_execution_owner_type = Column(String(32), nullable=False)
  source_execution_owner_id = Column(String(128), nullable=False)
  source_execution_environment = Column(String(16), nullable=False)
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
  environment = Column(String(16), nullable=False)
  metrics_origin = Column(String(24), nullable=True)
  entry_filled_at = Column(DateTime, nullable=True)
  last_exit_filled_at = Column(DateTime, nullable=True)
  closed_at = Column(DateTime, nullable=True)
  terminal_at = Column(DateTime, nullable=True)
  commission_rate = Column(Float, nullable=True)
  minimum_commission = Column(Float, nullable=True)
  stamp_tax_rate = Column(Float, nullable=True)
  transfer_fee_rate = Column(Float, nullable=True)
  policy_version = Column(Integer, nullable=False, default=0)
  version = Column(Integer, nullable=False, default=1)


class AccountExecutionControl(Base, TimestampMixin):
  """Account-wide live execution authorization and observed broker facts."""

  __tablename__ = "account_execution_controls"

  account_id = Column(String(50), primary_key=True)
  authorization_state = Column(String(24), nullable=False, default="DISABLED")
  state_version = Column(Integer, nullable=False, default=1)
  reconcile_status = Column(String(32), nullable=False, default="UNKNOWN")
  authorized_by_user_id = Column(String(36), nullable=True)
  authorized_at = Column(DateTime, nullable=True)
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


class AccountExecutionControlEvent(Base):
  """Append-only audit event for account execution authorization changes."""

  __tablename__ = "account_execution_control_events"
  __table_args__ = (
    Index(
      "ix_account_execution_control_event_account_created",
      "account_id",
      "created_at",
    ),
  )

  event_id = Column(String(128), primary_key=True)
  account_id = Column(String(50), nullable=False, index=True)
  event_type = Column(String(64), nullable=False)
  actor_user_id = Column(String(36), nullable=True)
  previous_state = Column(String(24), nullable=True)
  next_state = Column(String(24), nullable=True)
  snapshot_id = Column(String(128), nullable=True)
  details = Column(JSON, nullable=False, default=dict)
  created_at = Column(DateTime, nullable=False)


class TTradeRollout(Base, TimestampMixin):
  """Feature-local rollout state for the existing-position T assistant."""

  __tablename__ = "account_trading_rollouts"

  account_id = Column(String(50), primary_key=True)
  stage = Column(String(24), nullable=False, default="SHADOW")
  enabled = Column(Boolean, nullable=False, default=False)
  max_active_batches = Column(Integer, nullable=False, default=1)
  max_batch_volume = Column(Integer, nullable=False, default=100)
  max_order_amount = Column(Float, nullable=False, default=20000.0)
  max_total_exposure_pct = Column(Float, nullable=False, default=0.02)
  policy_version = Column(Integer, nullable=False, default=1)
  acknowledged_policy_version = Column(Integer, nullable=False, default=0)
  activated_by_user_id = Column(String(36), nullable=True)
  activated_at = Column(DateTime, nullable=True)
  paused_reason = Column(Text, nullable=True)


class TTradeRolloutEvent(Base):
  """Append-only audit event for T-assistant rollout state changes."""

  __tablename__ = "account_trading_rollout_events"
  __table_args__ = (
    Index(
      "ix_account_trading_rollout_event_account_created",
      "account_id",
      "created_at",
    ),
  )

  # Operation markers are namespaced client idempotency keys (not UUIDs).
  # Keep room for the fixed ``t-trade:<operation>:<sha256>`` identity while
  # retaining compatibility with the historical UUID audit rows.
  event_id = Column(String(128), primary_key=True)
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
  protocol_version = Column(String(16), nullable=False, default=PROTOCOL_VERSION)
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


# Keep the fixed JSON path literal in prepared queries so they match the index.
# JSONStrIndexType also renders SQLite's JSON path correctly in unit tests.
AGENT_REPORT_SNAPSHOT_ID = AgentReportInbox.payload[
  literal("snapshot_id", type_=JSON.JSONStrIndexType, literal_execute=True)
].as_string()

Index(
  "ix_agent_report_snapshot_lookup",
  AgentReportInbox.message_type,
  AgentReportInbox.protocol_version,
  AGENT_REPORT_SNAPSHOT_ID,
  AgentReportInbox.received_at.desc(),
)


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
    Index(
      "ix_market_data_request_device_status_created",
      "device_id",
      "status",
      "created_at",
    ),
  )

  request_id = Column(String(36), primary_key=True)
  device_id = Column(
    String(36),
    ForeignKey("agent_devices.id", ondelete="RESTRICT"),
    nullable=False,
  )
  idempotency_key = Column(String(128), nullable=False)
  request_payload = Column(JSON, nullable=False)
  development_only = Column(Boolean, nullable=False, default=False, server_default=text("false"))
  status = Column(String(24), nullable=False, default="QUEUED")
  expected_chunks = Column(Integer, nullable=True)
  received_chunks = Column(Integer, nullable=False, default=0)
  completed_at = Column(DateTime, nullable=True)
  processing_error = Column(Text, nullable=True)
  processing_claim_token = Column(String(36), nullable=True)
  ingestion_result = Column(JSON, nullable=True)


class DevelopmentDataExport(Base):
  __tablename__ = "development_data_export"
  __table_args__ = (Index("ix_development_data_export_queue", "state", "updated_at"),)
  id = Column(String(64), primary_key=True)
  request = Column(JSON, nullable=False)
  state = Column(String(32), nullable=False)
  source_request_id = Column(String(36))
  manifest = Column(JSON)
  error = Column(String(128))
  updated_at = Column(DateTime(timezone=True), nullable=False)
  expires_at = Column(DateTime(timezone=True))


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
  compressed_bytes = Column(BigInteger, nullable=False, default=0)
  compressed = Column(Boolean, nullable=False, default=True)
  storage_reference = Column(String(512), nullable=False)
  received_at = Column(DateTime, nullable=False)


for _identity_model in (
  PendingTradeOrder,
  OrderCorrelation,
  TradeCommandOutbox,
  StrategyRuntimeEvent,
):
  register_identity_immutability(_identity_model)
register_identity_immutability(TTradeBatch, fields=SOURCE_EXECUTION_FIELDS)
del _identity_model


class MarketDataSyncPartition(Base):
  __tablename__ = "market_data_sync_partition"
  run_id = Column(String(64), primary_key=True)
  batch_index = Column(Integer, primary_key=True)
  scope = Column(JSON, nullable=False)
  request_id = Column(String(36), nullable=False)
  coverage_status = Column(String(16), nullable=False)
  summary = Column(JSON, nullable=False)
  updated_at = Column(DateTime, nullable=False)
  __table_args__ = (
    CheckConstraint("coverage_status IN ('PENDING','VERIFIED','INCOMPLETE')",
                    name="ck_market_sync_coverage_status"),
  )
