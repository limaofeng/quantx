"""Durable configuration, execution, symbol, and decision-cycle facts."""

import uuid

from sqlalchemy import (
  JSON,
  BigInteger,
  CheckConstraint,
  Column,
  DateTime,
  ForeignKey,
  Index,
  Integer,
  String,
  Text,
  UniqueConstraint,
  event,
  text,
)
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.sql import func as sql_func

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class TAssistantConfigVersionRecord(Base):
  __tablename__ = "t_assistant_config_versions"
  __table_args__ = (
    UniqueConstraint(
      "config_id",
      "version",
      name="uq_t_assistant_config_version_number",
    ),
    UniqueConstraint(
      "config_id",
      "config_snapshot_hash",
      name="uq_t_assistant_config_version_hash",
    ),
    CheckConstraint(
      "version >= 1 AND feature_schema_version >= 1",
      name="ck_t_assistant_config_version_numbers",
    ),
    CheckConstraint(
      "entry_authorization IN ('MANUAL_CONFIRM','AUTO')",
      name="ck_t_assistant_config_entry_authorization",
    ),
    CheckConstraint(
      "rollout_stage IN ('CANARY','STANDARD')",
      name="ck_t_assistant_config_rollout_stage",
    ),
    CheckConstraint(
      "scorer_mode IN ('RULE_ONLY','SHADOW','ACTIVE')",
      name="ck_t_assistant_config_scorer_mode",
    ),
    CheckConstraint(
      "length(config_snapshot_hash) = 64",
      name="ck_t_assistant_config_snapshot_hash",
    ),
    CheckConstraint(
      "(scorer_mode = 'RULE_ONLY' AND model_runtime_binding IS NULL) OR "
      "(scorer_mode IN ('SHADOW','ACTIVE') AND model_runtime_binding IS NOT NULL)",
      name="ck_t_assistant_config_model_binding",
    ),
  )

  config_version_id = Column(
    String(36),
    primary_key=True,
    default=lambda: str(uuid.uuid4()),
  )
  config_id = Column(
    String(36),
    ForeignKey("t_trade_global_configs.id", ondelete="RESTRICT"),
    nullable=False,
  )
  version = Column(Integer, nullable=False)
  config_schema_version = Column(String(64), nullable=False)
  canonical_payload = Column(JSON, nullable=False)
  config_snapshot_hash = Column(String(64), nullable=False)
  entry_authorization = Column(String(24), nullable=False)
  rollout_stage = Column(String(16), nullable=False)
  policy_version = Column(String(64), nullable=False)
  feature_schema_version = Column(Integer, nullable=False)
  scorer_mode = Column(String(16), nullable=False)
  model_runtime_binding = Column(JSON(none_as_null=True), nullable=True)
  created_at = Column(DateTime(timezone=True), nullable=False, default=sql_func.now())


class TAssistantExecutionRecord(Base, TimestampMixin):
  __tablename__ = "t_assistant_executions"
  __table_args__ = (
    CheckConstraint(
      "environment IN ('PAPER','LIVE','BACKTEST')",
      name="ck_t_assistant_execution_environment",
    ),
    CheckConstraint(
      "entry_authorization IN ('MANUAL_CONFIRM','AUTO')",
      name="ck_t_assistant_execution_entry_authorization",
    ),
    CheckConstraint(
      "rollout_stage IN ('CANARY','STANDARD')",
      name="ck_t_assistant_execution_rollout_stage",
    ),
    CheckConstraint(
      "status IN ('CREATED','WARMING','RUNNING','DRAINING','STOPPED','FAILED',"
      "'RECONCILE_REQUIRED')",
      name="ck_t_assistant_execution_status",
    ),
    CheckConstraint(
      "entry_readiness IN ('BLOCKED','WARMING','READY','DEGRADED','DRAINING',"
      "'RECONCILE_REQUIRED')",
      name="ck_t_assistant_execution_readiness",
    ),
    CheckConstraint(
      "scorer_mode IN ('RULE_ONLY','SHADOW','ACTIVE')",
      name="ck_t_assistant_execution_scorer_mode",
    ),
    CheckConstraint(
      "frozen_config_version >= 1 AND feature_schema_version >= 1 "
      "AND state_version >= 1",
      name="ck_t_assistant_execution_versions",
    ),
    CheckConstraint(
      "universe_revision >= 0 AND last_assigned_cycle_sequence >= 0 "
      "AND last_committed_cycle_sequence >= 0 AND checkpoint_revision >= 0 "
      "AND last_committed_cycle_sequence <= last_assigned_cycle_sequence",
      name="ck_t_assistant_execution_revisions",
    ),
    CheckConstraint(
      "(entry_readiness = 'READY' AND json_array_length(entry_readiness_reasons) = 0) "
      "OR entry_readiness <> 'READY'",
      name="ck_t_assistant_execution_ready_reasons",
    ),
    CheckConstraint(
      "(status IN ('STOPPED','FAILED') AND completed_at IS NOT NULL) OR "
      "status NOT IN ('STOPPED','FAILED')",
      name="ck_t_assistant_execution_terminal_shape",
    ),
    CheckConstraint(
      "(status = 'RUNNING' AND entry_readiness IN ('READY','DEGRADED')) OR "
      "(status = 'DRAINING' AND entry_readiness = 'DRAINING') OR "
      "(status = 'RECONCILE_REQUIRED' AND "
      "entry_readiness = 'RECONCILE_REQUIRED') OR "
      "(status IN ('STOPPED','FAILED') AND entry_readiness = 'BLOCKED') OR "
      "status IN ('CREATED','WARMING')",
      name="ck_t_assistant_execution_lifecycle_readiness",
    ),
    Index(
      "ix_t_assistant_execution_account_status",
      "account_id",
      "environment",
      "status",
    ),
    Index(
      "uq_t_assistant_execution_live_entry_producer",
      "account_id",
      unique=True,
      postgresql_where=text("environment = 'LIVE' AND status IN ('WARMING','RUNNING')"),
      sqlite_where=text("environment = 'LIVE' AND status IN ('WARMING','RUNNING')"),
    ),
  )

  execution_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  config_id = Column(
    String(36),
    ForeignKey("t_trade_global_configs.id", ondelete="RESTRICT"),
    nullable=False,
  )
  config_version_id = Column(
    String(36),
    ForeignKey(
      "t_assistant_config_versions.config_version_id",
      ondelete="RESTRICT",
    ),
    nullable=False,
  )
  frozen_config_version = Column(Integer, nullable=False)
  config_snapshot_hash = Column(String(64), nullable=False)
  account_id = Column(String(50), nullable=False)
  environment = Column(String(16), nullable=False)
  entry_authorization = Column(String(24), nullable=False)
  rollout_stage = Column(String(16), nullable=False)
  status = Column(String(24), nullable=False)
  entry_readiness = Column(String(24), nullable=False)
  entry_readiness_reasons = Column(JSON, nullable=False, default=list)
  entry_readiness_as_of = Column(DateTime(timezone=True), nullable=False)
  policy_version = Column(String(64), nullable=False)
  feature_schema_version = Column(Integer, nullable=False)
  scorer_mode = Column(String(16), nullable=False)
  model_runtime_binding = Column(JSON(none_as_null=True), nullable=True)
  universe_revision = Column(Integer, nullable=False, default=0)
  last_assigned_cycle_sequence = Column(BigInteger, nullable=False, default=0)
  last_committed_cycle_sequence = Column(BigInteger, nullable=False, default=0)
  checkpoint_revision = Column(BigInteger, nullable=False, default=0)
  started_at = Column(DateTime(timezone=True), nullable=True)
  drain_requested_at = Column(DateTime(timezone=True), nullable=True)
  completed_at = Column(DateTime(timezone=True), nullable=True)
  state_version = Column(Integer, nullable=False, default=1)
  created_at = Column(DateTime(timezone=True), nullable=False, default=sql_func.now())
  updated_at = Column(
    DateTime(timezone=True),
    nullable=False,
    default=sql_func.now(),
    onupdate=sql_func.now(),
  )


class TAssistantExecutionEventRecord(Base):
  __tablename__ = "t_assistant_execution_events"
  __table_args__ = (
    UniqueConstraint(
      "execution_id",
      "event_key",
      name="uq_t_assistant_execution_event_key",
    ),
    Index(
      "ix_t_assistant_execution_event_time",
      "execution_id",
      "occurred_at",
      "event_id",
    ),
  )

  event_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  execution_id = Column(
    String(36),
    ForeignKey("t_assistant_executions.execution_id", ondelete="RESTRICT"),
    nullable=False,
  )
  event_key = Column(String(160), nullable=False)
  event_type = Column(String(64), nullable=False)
  occurred_at = Column(DateTime(timezone=True), nullable=False)
  source_type = Column(String(32), nullable=True)
  source_id = Column(String(160), nullable=True)
  payload = Column(JSON, nullable=False, default=dict)
  created_at = Column(DateTime(timezone=True), nullable=False, default=sql_func.now())


class TAssistantSymbolStateRecord(Base, TimestampMixin):
  __tablename__ = "t_assistant_symbol_states"
  __table_args__ = (
    UniqueConstraint(
      "execution_id",
      "instrument_code",
      name="uq_t_assistant_symbol_state_execution_instrument",
    ),
    CheckConstraint(
      "lifecycle IN ('WARMING','ACTIVE','DRAINING','RETIRED')",
      name="ck_t_assistant_symbol_state_lifecycle",
    ),
    CheckConstraint(
      "revision >= 0 AND feature_schema_version >= 1",
      name="ck_t_assistant_symbol_state_versions",
    ),
    CheckConstraint(
      "last_accepted_sequence >= 0 AND last_source_time_ms >= 0 "
      "AND last_tick_ordinal >= 0 AND ring_generation >= 0",
      name="ck_t_assistant_symbol_state_cursor",
    ),
    Index(
      "ix_t_assistant_symbol_state_execution_lifecycle",
      "execution_id",
      "lifecycle",
      "instrument_code",
    ),
  )

  state_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  execution_id = Column(
    String(36),
    ForeignKey("t_assistant_executions.execution_id", ondelete="RESTRICT"),
    nullable=False,
  )
  instrument_code = Column(String(20), nullable=False)
  revision = Column(Integer, nullable=False, default=0)
  lifecycle = Column(String(16), nullable=False)
  stream_id = Column(String(128), nullable=True)
  continuity_generation = Column(String(64), nullable=True)
  ring_generation = Column(Integer, nullable=False, default=0)
  last_accepted_sequence = Column(BigInteger, nullable=False, default=0)
  last_source_time_ms = Column(BigInteger, nullable=False, default=0)
  last_tick_ordinal = Column(BigInteger, nullable=False, default=0)
  policy_version = Column(String(64), nullable=False)
  feature_schema_version = Column(Integer, nullable=False)
  state_payload = Column(JSON, nullable=False)
  material_manifest_hash = Column(String(64), nullable=False, default="")
  rewarm_reason = Column(String(64), nullable=True)
  created_at = Column(DateTime(timezone=True), nullable=False, default=sql_func.now())
  updated_at = Column(
    DateTime(timezone=True),
    nullable=False,
    default=sql_func.now(),
    onupdate=sql_func.now(),
  )


class TAssistantDecisionCycleRecord(Base):
  __tablename__ = "t_assistant_decision_cycles"
  __table_args__ = (
    UniqueConstraint(
      "execution_id",
      "cycle_sequence",
      name="uq_t_assistant_cycle_sequence",
    ),
    UniqueConstraint(
      "execution_id",
      "decision_key",
      "attempt",
      name="uq_t_assistant_cycle_decision_attempt",
    ),
    CheckConstraint(
      "status IN ('PREPARED','PROPOSALS_COMMITTED','ABORTED_STALE','ABORTED')",
      name="ck_t_assistant_cycle_status",
    ),
    CheckConstraint(
      "cycle_sequence >= 1 AND attempt >= 1",
      name="ck_t_assistant_cycle_identity",
    ),
    CheckConstraint(
      "evaluated_symbol_count >= 0 AND material_symbol_count >= 0 "
      "AND proposed_intent_count >= 0",
      name="ck_t_assistant_cycle_counts",
    ),
    CheckConstraint(
      "material_symbol_count <= evaluated_symbol_count",
      name="ck_t_assistant_cycle_material_count",
    ),
    CheckConstraint(
      "fence_from >= 0 AND fence_to >= fence_from",
      name="ck_t_assistant_cycle_fence_shape",
    ),
    CheckConstraint(
      "length(decision_key) = 64 AND length(snapshot_hash) = 64 "
      "AND length(market_delta_manifest_hash) = 64 "
      "AND length(reducer_cursor_manifest_hash) = 64 "
      "AND length(input_manifest_hash) = 64 "
      "AND (output_manifest_hash IS NULL OR length(output_manifest_hash) = 64)",
      name="ck_t_assistant_cycle_hash_shape",
    ),
    CheckConstraint(
      "(processing_owner IS NULL AND processing_fence_token IS NULL "
      "AND processing_lease_until IS NULL) OR "
      "(status = 'PREPARED' AND processing_owner IS NOT NULL "
      "AND length(trim(processing_owner)) > 0 "
      "AND processing_fence_token IS NOT NULL "
      "AND length(trim(processing_fence_token)) > 0 "
      "AND processing_lease_until IS NOT NULL)",
      name="ck_t_assistant_cycle_claim_shape",
    ),
    CheckConstraint(
      "(status = 'PREPARED' AND committed_at IS NULL "
      "AND output_manifest_hash IS NULL AND output_manifest IS NULL "
      "AND abort_reason IS NULL) OR "
      "(status = 'PROPOSALS_COMMITTED' AND committed_at IS NOT NULL "
      "AND output_manifest_hash IS NOT NULL AND output_manifest IS NOT NULL "
      "AND abort_reason IS NULL AND processing_owner IS NULL "
      "AND processing_fence_token IS NULL AND processing_lease_until IS NULL) OR "
      "(status IN ('ABORTED_STALE','ABORTED') AND committed_at IS NOT NULL "
      "AND output_manifest_hash IS NULL AND output_manifest IS NULL "
      "AND abort_reason IS NOT NULL AND length(trim(abort_reason)) > 0 "
      "AND processing_owner IS NULL AND processing_fence_token IS NULL "
      "AND processing_lease_until IS NULL)",
      name="ck_t_assistant_cycle_terminal_shape",
    ),
    Index(
      "ix_t_assistant_cycle_execution_status",
      "execution_id",
      "status",
      "cycle_sequence",
    ),
    Index(
      "ix_t_assistant_cycle_processing_lease",
      "status",
      "processing_lease_until",
    ),
  )

  cycle_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
  execution_id = Column(
    String(36),
    ForeignKey("t_assistant_executions.execution_id", ondelete="RESTRICT"),
    nullable=False,
  )
  cycle_sequence = Column(BigInteger, nullable=False)
  decision_key = Column(String(64), nullable=False)
  attempt = Column(Integer, nullable=False)
  snapshot_hash = Column(String(64), nullable=False)
  fence_from = Column(BigInteger, nullable=False)
  fence_to = Column(BigInteger, nullable=False)
  market_delta_manifest_hash = Column(String(64), nullable=False)
  reducer_cursor_manifest_hash = Column(String(64), nullable=False)
  evaluated_symbol_count = Column(Integer, nullable=False, default=0)
  material_symbol_count = Column(Integer, nullable=False, default=0)
  proposed_intent_count = Column(Integer, nullable=False, default=0)
  status = Column(String(24), nullable=False)
  processing_owner = Column(String(128), nullable=True)
  processing_fence_token = Column(String(64), nullable=True)
  processing_lease_until = Column(DateTime(timezone=True), nullable=True)
  input_manifest_hash = Column(String(64), nullable=False)
  input_manifest = Column(JSON, nullable=False)
  output_manifest_hash = Column(String(64), nullable=True)
  output_manifest = Column(JSON(none_as_null=True), nullable=True)
  prepared_at = Column(DateTime(timezone=True), nullable=False)
  committed_at = Column(DateTime(timezone=True), nullable=True)
  abort_reason = Column(String(64), nullable=True)
  error_detail = Column(Text, nullable=True)
  created_at = Column(DateTime(timezone=True), nullable=False, default=sql_func.now())


def _register_append_only(model: type) -> None:
  @event.listens_for(model, "before_update", propagate=False)
  def _reject_update(mapper, connection, target) -> None:
    del mapper, connection
    if sa_inspect(target).persistent:
      raise ValueError("APPEND_ONLY_FACT_IMMUTABLE")

  @event.listens_for(model, "before_delete", propagate=False)
  def _reject_delete(mapper, connection, target) -> None:
    del mapper, connection, target
    raise ValueError("APPEND_ONLY_FACT_IMMUTABLE")


_register_append_only(TAssistantConfigVersionRecord)
_register_append_only(TAssistantExecutionEventRecord)


__all__ = [
  "TAssistantConfigVersionRecord",
  "TAssistantDecisionCycleRecord",
  "TAssistantExecutionEventRecord",
  "TAssistantExecutionRecord",
  "TAssistantSymbolStateRecord",
]
