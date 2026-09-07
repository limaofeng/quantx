"""Create the independent T-assistant PAPER shadow runtime.

After user-approved removal of unreferenced orphan diagnostics, the migration
proves every remaining opportunity/outcome row has one valid StrategyRun owner.
It then switches those shared evidence tables to the
canonical owner identity and creates the isolated config/execution/cycle/state
facts.  It never fabricates a StrategyRun for a TAssistantExecution.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "20260903_0049"
down_revision = "20260903_0048"
branch_labels = None
depends_on = None


def _canonical_json(value: Mapping[str, Any]) -> str:
  return json.dumps(
    dict(value),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
  )


def _mapping(value: Any) -> dict[str, Any]:
  if isinstance(value, Mapping):
    return dict(value)
  if isinstance(value, str) and value.strip():
    decoded = json.loads(value)
    return dict(decoded) if isinstance(decoded, Mapping) else {}
  return {}


def _legacy_entry_authorization(settings: Mapping[str, Any]) -> str:
  explicit = str(settings.get("entry_authorization") or "").upper()
  if explicit in {"MANUAL_CONFIRM", "AUTO"}:
    return explicit
  legacy_mode = str(
    settings.get("entry_execution_mode") or settings.get("execution_mode") or ""
  ).upper()
  # BACKTEST_AUTO never becomes an active config authorization.
  return "AUTO" if legacy_mode == "LIVE_AUTO" else "MANUAL_CONFIRM"


def _legacy_rollout_stage(settings: Mapping[str, Any]) -> str:
  explicit = str(settings.get("rollout_stage") or "").upper()
  return explicit if explicit in {"CANARY", "STANDARD"} else "CANARY"


def _prune_orphaned_opportunity_diagnostics() -> int:
  """User-approved one-time cleanup; never discard candidate or trading facts.

  Deleted StrategyRuns left non-candidate diagnostic snapshots behind. They
  have no provable environment and must not become fabricated runtime owners.
  The original complete backup is the recovery source; no legacy runtime or
  archive compatibility path is introduced.
  """
  result = op.get_bind().execute(sa.text("""
    DELETE FROM t_trade_opportunity_evaluations e
    WHERE e.strategy_run_id IS NOT NULL AND btrim(e.strategy_run_id) <> ''
      AND e.candidate_id IS NULL
      AND COALESCE(e.payload::jsonb->'signal_snapshot'->>'candidate_id', '') = ''
      AND COALESCE(e.payload::jsonb->'signal_snapshot'->>'pending_entry_intent_id', '') = ''
      AND (
        (e.record_kind = 'COALESCED_DIAGNOSTIC' AND e.event_type = 'HEARTBEAT')
        OR (e.record_kind = 'MATERIAL'
            AND e.event_type IN ('STATE_INITIALIZED','DIAGNOSTIC_STATE_CHANGED'))
      )
      AND NOT EXISTS (SELECT 1 FROM strategy_runs r WHERE r.id = e.strategy_run_id)
      AND NOT EXISTS (SELECT 1 FROM strategy_backtests b
                      WHERE b.strategy_run_id = e.strategy_run_id)
      AND NOT EXISTS (SELECT 1 FROM t_trade_candidate_outcomes c
                      WHERE c.strategy_run_id = e.strategy_run_id)
      AND NOT EXISTS (SELECT 1 FROM trade_intents t
                      WHERE t.strategy_run_id = e.strategy_run_id
                         OR (t.owner_type = 'STRATEGY_RUN' AND t.owner_id = e.strategy_run_id))
      AND NOT EXISTS (SELECT 1 FROM pending_trade_orders p
                      WHERE p.strategy_run_id = e.strategy_run_id
                         OR (p.owner_type = 'STRATEGY_RUN' AND p.owner_id = e.strategy_run_id))
      AND NOT EXISTS (SELECT 1 FROM order_correlations c
                      WHERE c.strategy_run_id = e.strategy_run_id
                         OR (c.owner_type = 'STRATEGY_RUN' AND c.owner_id = e.strategy_run_id))
      AND NOT EXISTS (SELECT 1 FROM strategy_runtime_events v
                      WHERE v.strategy_run_id = e.strategy_run_id
                         OR (v.owner_type = 'STRATEGY_RUN' AND v.owner_id = e.strategy_run_id))
      AND NOT EXISTS (SELECT 1 FROM t_trade_batches b WHERE b.strategy_run_id = e.strategy_run_id)
      AND NOT EXISTS (SELECT 1 FROM t_trade_imported_entries i WHERE i.strategy_run_id = e.strategy_run_id)
      AND NOT EXISTS (SELECT 1 FROM auto_exit_plans p
                      WHERE p.strategy_run_id = e.strategy_run_id
                         OR (p.source_execution_owner_type = 'STRATEGY_RUN'
                             AND p.source_execution_owner_id = e.strategy_run_id))
  """))
  count = int(result.rowcount)
  logging.getLogger("alembic.runtime.migration").info(
    "P3 orphan opportunity diagnostics removed: %s", count,
  )
  return count


def _preflight_legacy_evidence() -> None:
  op.execute(
    sa.text(
      """
      DO $$
      BEGIN
        IF EXISTS (
          SELECT 1
          FROM t_trade_opportunity_evaluations e
          LEFT JOIN strategy_runs r ON r.id = e.strategy_run_id
          WHERE e.strategy_run_id IS NULL OR btrim(e.strategy_run_id) = ''
             OR r.id IS NULL
             OR lower(r.mode::text) NOT IN ('paper','live','backtest')
        ) THEN
          RAISE EXCEPTION 'P3_SHADOW_CONFLICT:opportunity_owner_unproven';
        END IF;
        IF EXISTS (
          SELECT 1
          FROM t_trade_candidate_outcomes o
          LEFT JOIN strategy_runs r ON r.id = o.strategy_run_id
          WHERE o.strategy_run_id IS NULL OR btrim(o.strategy_run_id) = ''
             OR r.id IS NULL
             OR lower(r.mode::text) NOT IN ('paper','live','backtest')
        ) THEN
          RAISE EXCEPTION 'P3_SHADOW_CONFLICT:candidate_owner_unproven';
        END IF;
        IF EXISTS (
          SELECT account_id
          FROM t_trade_global_configs
          GROUP BY account_id HAVING count(*) > 1
        ) THEN
          RAISE EXCEPTION 'P3_SHADOW_CONFLICT:duplicate_account_config';
        END IF;
        IF EXISTS (
          SELECT 1 FROM t_trade_global_configs
          WHERE lower(mode) NOT IN ('paper','live')
        ) THEN
          RAISE EXCEPTION 'P3_SHADOW_CONFLICT:backtest_active_config';
        END IF;
      END $$;
      """
    )
  )


def _created_at() -> sa.Column:
  return sa.Column(
    "created_at",
    sa.DateTime(timezone=True),
    nullable=False,
    server_default=sa.func.now(),
  )


def _updated_at() -> sa.Column:
  return sa.Column(
    "updated_at",
    sa.DateTime(timezone=True),
    nullable=False,
    server_default=sa.func.now(),
  )


def _create_runtime_tables() -> None:
  op.create_table(
    "t_assistant_config_versions",
    sa.Column("config_version_id", sa.String(length=36), primary_key=True),
    sa.Column(
      "config_id",
      sa.String(length=36),
      sa.ForeignKey("t_trade_global_configs.id", ondelete="RESTRICT"),
      nullable=False,
    ),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("config_schema_version", sa.String(length=64), nullable=False),
    sa.Column("canonical_payload", sa.JSON(), nullable=False),
    sa.Column("config_snapshot_hash", sa.String(length=64), nullable=False),
    sa.Column("entry_authorization", sa.String(length=24), nullable=False),
    sa.Column("rollout_stage", sa.String(length=16), nullable=False),
    sa.Column("policy_version", sa.String(length=64), nullable=False),
    sa.Column("feature_schema_version", sa.Integer(), nullable=False),
    sa.Column("scorer_mode", sa.String(length=16), nullable=False),
    sa.Column("model_runtime_binding", sa.JSON(none_as_null=True), nullable=True),
    _created_at(),
    sa.CheckConstraint(
      "version >= 1 AND feature_schema_version >= 1",
      name="ck_t_assistant_config_version_numbers",
    ),
    sa.CheckConstraint(
      "entry_authorization IN ('MANUAL_CONFIRM','AUTO')",
      name="ck_t_assistant_config_entry_authorization",
    ),
    sa.CheckConstraint(
      "rollout_stage IN ('CANARY','STANDARD')",
      name="ck_t_assistant_config_rollout_stage",
    ),
    sa.CheckConstraint(
      "scorer_mode IN ('RULE_ONLY','SHADOW','ACTIVE')",
      name="ck_t_assistant_config_scorer_mode",
    ),
    sa.CheckConstraint(
      "length(config_snapshot_hash) = 64",
      name="ck_t_assistant_config_snapshot_hash",
    ),
    sa.CheckConstraint(
      "(scorer_mode = 'RULE_ONLY' AND model_runtime_binding IS NULL) OR "
      "(scorer_mode IN ('SHADOW','ACTIVE') AND model_runtime_binding IS NOT NULL)",
      name="ck_t_assistant_config_model_binding",
    ),
    sa.UniqueConstraint(
      "config_id",
      "version",
      name="uq_t_assistant_config_version_number",
    ),
    sa.UniqueConstraint(
      "config_id",
      "config_snapshot_hash",
      name="uq_t_assistant_config_version_hash",
    ),
    comment="做 T 助手不可变配置版本",
  )

  op.create_table(
    "t_assistant_executions",
    sa.Column("execution_id", sa.String(length=36), primary_key=True),
    sa.Column(
      "config_id",
      sa.String(length=36),
      sa.ForeignKey("t_trade_global_configs.id", ondelete="RESTRICT"),
      nullable=False,
    ),
    sa.Column(
      "config_version_id",
      sa.String(length=36),
      sa.ForeignKey(
        "t_assistant_config_versions.config_version_id",
        ondelete="RESTRICT",
      ),
      nullable=False,
    ),
    sa.Column("frozen_config_version", sa.Integer(), nullable=False),
    sa.Column("config_snapshot_hash", sa.String(length=64), nullable=False),
    sa.Column("account_id", sa.String(length=50), nullable=False),
    sa.Column("environment", sa.String(length=16), nullable=False),
    sa.Column("entry_authorization", sa.String(length=24), nullable=False),
    sa.Column("rollout_stage", sa.String(length=16), nullable=False),
    sa.Column("status", sa.String(length=24), nullable=False),
    sa.Column("entry_readiness", sa.String(length=24), nullable=False),
    sa.Column("entry_readiness_reasons", sa.JSON(), nullable=False),
    sa.Column("entry_readiness_as_of", sa.DateTime(timezone=True), nullable=False),
    sa.Column("policy_version", sa.String(length=64), nullable=False),
    sa.Column("feature_schema_version", sa.Integer(), nullable=False),
    sa.Column("scorer_mode", sa.String(length=16), nullable=False),
    sa.Column("model_runtime_binding", sa.JSON(none_as_null=True), nullable=True),
    sa.Column("universe_revision", sa.Integer(), nullable=False),
    sa.Column("last_assigned_cycle_sequence", sa.BigInteger(), nullable=False),
    sa.Column("last_committed_cycle_sequence", sa.BigInteger(), nullable=False),
    sa.Column("checkpoint_revision", sa.BigInteger(), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("drain_requested_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("state_version", sa.Integer(), nullable=False),
    _created_at(),
    _updated_at(),
    sa.CheckConstraint(
      "environment IN ('PAPER','LIVE','BACKTEST')",
      name="ck_t_assistant_execution_environment",
    ),
    sa.CheckConstraint(
      "entry_authorization IN ('MANUAL_CONFIRM','AUTO')",
      name="ck_t_assistant_execution_entry_authorization",
    ),
    sa.CheckConstraint(
      "rollout_stage IN ('CANARY','STANDARD')",
      name="ck_t_assistant_execution_rollout_stage",
    ),
    sa.CheckConstraint(
      "status IN ('CREATED','WARMING','RUNNING','DRAINING','STOPPED','FAILED',"
      "'RECONCILE_REQUIRED')",
      name="ck_t_assistant_execution_status",
    ),
    sa.CheckConstraint(
      "entry_readiness IN ('BLOCKED','WARMING','READY','DEGRADED','DRAINING',"
      "'RECONCILE_REQUIRED')",
      name="ck_t_assistant_execution_readiness",
    ),
    sa.CheckConstraint(
      "scorer_mode IN ('RULE_ONLY','SHADOW','ACTIVE')",
      name="ck_t_assistant_execution_scorer_mode",
    ),
    sa.CheckConstraint(
      "frozen_config_version >= 1 AND feature_schema_version >= 1 "
      "AND state_version >= 1",
      name="ck_t_assistant_execution_versions",
    ),
    sa.CheckConstraint(
      "universe_revision >= 0 AND last_assigned_cycle_sequence >= 0 "
      "AND last_committed_cycle_sequence >= 0 AND checkpoint_revision >= 0 "
      "AND last_committed_cycle_sequence <= last_assigned_cycle_sequence",
      name="ck_t_assistant_execution_revisions",
    ),
    sa.CheckConstraint(
      "(entry_readiness = 'READY' AND json_array_length(entry_readiness_reasons) = 0) "
      "OR entry_readiness <> 'READY'",
      name="ck_t_assistant_execution_ready_reasons",
    ),
    sa.CheckConstraint(
      "(status IN ('STOPPED','FAILED') AND completed_at IS NOT NULL) OR "
      "status NOT IN ('STOPPED','FAILED')",
      name="ck_t_assistant_execution_terminal_shape",
    ),
    sa.CheckConstraint(
      "(status = 'RUNNING' AND entry_readiness = 'READY') OR "
      "(status = 'DRAINING' AND entry_readiness = 'DRAINING') OR "
      "(status = 'RECONCILE_REQUIRED' AND "
      "entry_readiness = 'RECONCILE_REQUIRED') OR "
      "(status IN ('STOPPED','FAILED') AND entry_readiness = 'BLOCKED') OR "
      "status IN ('CREATED','WARMING')",
      name="ck_t_assistant_execution_lifecycle_readiness",
    ),
    comment="做 T 助手独立执行身份与生命周期",
  )
  op.create_index(
    "ix_t_assistant_execution_account_status",
    "t_assistant_executions",
    ["account_id", "environment", "status"],
  )
  op.create_index(
    "uq_t_assistant_execution_live_entry_producer",
    "t_assistant_executions",
    ["account_id"],
    unique=True,
    postgresql_where=sa.text(
      "environment = 'LIVE' AND status IN ('WARMING','RUNNING')"
    ),
    sqlite_where=sa.text("environment = 'LIVE' AND status IN ('WARMING','RUNNING')"),
  )

  op.create_table(
    "t_assistant_execution_events",
    sa.Column("event_id", sa.String(length=36), primary_key=True),
    sa.Column(
      "execution_id",
      sa.String(length=36),
      sa.ForeignKey("t_assistant_executions.execution_id", ondelete="RESTRICT"),
      nullable=False,
    ),
    sa.Column("event_key", sa.String(length=160), nullable=False),
    sa.Column("event_type", sa.String(length=64), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("source_type", sa.String(length=32), nullable=True),
    sa.Column("source_id", sa.String(length=160), nullable=True),
    sa.Column("payload", sa.JSON(), nullable=False),
    _created_at(),
    sa.UniqueConstraint(
      "execution_id",
      "event_key",
      name="uq_t_assistant_execution_event_key",
    ),
    comment="做 T 助手执行生命周期与影子审计事件",
  )
  op.create_index(
    "ix_t_assistant_execution_event_time",
    "t_assistant_execution_events",
    ["execution_id", "occurred_at", "event_id"],
  )

  op.create_table(
    "t_assistant_symbol_states",
    sa.Column("state_id", sa.String(length=36), primary_key=True),
    sa.Column(
      "execution_id",
      sa.String(length=36),
      sa.ForeignKey("t_assistant_executions.execution_id", ondelete="RESTRICT"),
      nullable=False,
    ),
    sa.Column("instrument_code", sa.String(length=20), nullable=False),
    sa.Column("revision", sa.Integer(), nullable=False),
    sa.Column("lifecycle", sa.String(length=16), nullable=False),
    sa.Column("stream_id", sa.String(length=128), nullable=True),
    sa.Column("continuity_generation", sa.String(length=64), nullable=True),
    sa.Column("ring_generation", sa.Integer(), nullable=False),
    sa.Column("last_accepted_sequence", sa.BigInteger(), nullable=False),
    sa.Column("last_source_time_ms", sa.BigInteger(), nullable=False),
    sa.Column("last_tick_ordinal", sa.BigInteger(), nullable=False),
    sa.Column("policy_version", sa.String(length=64), nullable=False),
    sa.Column("feature_schema_version", sa.Integer(), nullable=False),
    sa.Column("state_payload", sa.JSON(), nullable=False),
    sa.Column("material_manifest_hash", sa.String(length=64), nullable=False),
    sa.Column("rewarm_reason", sa.String(length=64), nullable=True),
    _created_at(),
    _updated_at(),
    sa.CheckConstraint(
      "lifecycle IN ('WARMING','ACTIVE','DRAINING','RETIRED')",
      name="ck_t_assistant_symbol_state_lifecycle",
    ),
    sa.CheckConstraint(
      "revision >= 0 AND feature_schema_version >= 1",
      name="ck_t_assistant_symbol_state_versions",
    ),
    sa.CheckConstraint(
      "last_accepted_sequence >= 0 AND last_source_time_ms >= 0 "
      "AND last_tick_ordinal >= 0 AND ring_generation >= 0",
      name="ck_t_assistant_symbol_state_cursor",
    ),
    sa.UniqueConstraint(
      "execution_id",
      "instrument_code",
      name="uq_t_assistant_symbol_state_execution_instrument",
    ),
    comment="做 T 助手逐标的物化算法状态",
  )
  op.create_index(
    "ix_t_assistant_symbol_state_execution_lifecycle",
    "t_assistant_symbol_states",
    ["execution_id", "lifecycle", "instrument_code"],
  )

  op.create_table(
    "t_assistant_decision_cycles",
    sa.Column("cycle_id", sa.String(length=36), primary_key=True),
    sa.Column(
      "execution_id",
      sa.String(length=36),
      sa.ForeignKey("t_assistant_executions.execution_id", ondelete="RESTRICT"),
      nullable=False,
    ),
    sa.Column("cycle_sequence", sa.BigInteger(), nullable=False),
    sa.Column("decision_key", sa.String(length=64), nullable=False),
    sa.Column("attempt", sa.Integer(), nullable=False),
    sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
    sa.Column("fence_from", sa.BigInteger(), nullable=False),
    sa.Column("fence_to", sa.BigInteger(), nullable=False),
    sa.Column("market_delta_manifest_hash", sa.String(length=64), nullable=False),
    sa.Column("reducer_cursor_manifest_hash", sa.String(length=64), nullable=False),
    sa.Column("evaluated_symbol_count", sa.Integer(), nullable=False),
    sa.Column("material_symbol_count", sa.Integer(), nullable=False),
    sa.Column("proposed_intent_count", sa.Integer(), nullable=False),
    sa.Column("status", sa.String(length=24), nullable=False),
    sa.Column("processing_owner", sa.String(length=128), nullable=True),
    sa.Column("processing_fence_token", sa.String(length=64), nullable=True),
    sa.Column("processing_lease_until", sa.DateTime(timezone=True), nullable=True),
    sa.Column("input_manifest_hash", sa.String(length=64), nullable=False),
    sa.Column("input_manifest", sa.JSON(), nullable=False),
    sa.Column("output_manifest_hash", sa.String(length=64), nullable=True),
    sa.Column("output_manifest", sa.JSON(none_as_null=True), nullable=True),
    sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("abort_reason", sa.String(length=64), nullable=True),
    sa.Column("error_detail", sa.Text(), nullable=True),
    _created_at(),
    sa.CheckConstraint(
      "status IN ('PREPARED','PROPOSALS_COMMITTED','ABORTED_STALE','ABORTED')",
      name="ck_t_assistant_cycle_status",
    ),
    sa.CheckConstraint(
      "cycle_sequence >= 1 AND attempt >= 1",
      name="ck_t_assistant_cycle_identity",
    ),
    sa.CheckConstraint(
      "evaluated_symbol_count >= 0 AND material_symbol_count >= 0 "
      "AND proposed_intent_count >= 0",
      name="ck_t_assistant_cycle_counts",
    ),
    sa.CheckConstraint(
      "material_symbol_count <= evaluated_symbol_count",
      name="ck_t_assistant_cycle_material_count",
    ),
    sa.CheckConstraint(
      "fence_from >= 0 AND fence_to >= fence_from",
      name="ck_t_assistant_cycle_fence_shape",
    ),
    sa.CheckConstraint(
      "length(decision_key) = 64 AND length(snapshot_hash) = 64 "
      "AND length(market_delta_manifest_hash) = 64 "
      "AND length(reducer_cursor_manifest_hash) = 64 "
      "AND length(input_manifest_hash) = 64 "
      "AND (output_manifest_hash IS NULL OR length(output_manifest_hash) = 64)",
      name="ck_t_assistant_cycle_hash_shape",
    ),
    sa.CheckConstraint(
      "(processing_owner IS NULL AND processing_fence_token IS NULL "
      "AND processing_lease_until IS NULL) OR "
      "(status = 'PREPARED' AND processing_owner IS NOT NULL "
      "AND length(trim(processing_owner)) > 0 "
      "AND processing_fence_token IS NOT NULL "
      "AND length(trim(processing_fence_token)) > 0 "
      "AND processing_lease_until IS NOT NULL)",
      name="ck_t_assistant_cycle_claim_shape",
    ),
    sa.CheckConstraint(
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
    sa.UniqueConstraint(
      "execution_id",
      "cycle_sequence",
      name="uq_t_assistant_cycle_sequence",
    ),
    sa.UniqueConstraint(
      "execution_id",
      "decision_key",
      "attempt",
      name="uq_t_assistant_cycle_decision_attempt",
    ),
    comment="做 T 助手可恢复物化决策周期",
  )
  op.create_index(
    "ix_t_assistant_cycle_execution_status",
    "t_assistant_decision_cycles",
    ["execution_id", "status", "cycle_sequence"],
  )
  op.create_index(
    "ix_t_assistant_cycle_processing_lease",
    "t_assistant_decision_cycles",
    ["status", "processing_lease_until"],
  )


def _backfill_config_versions() -> None:
  bind = op.get_bind()
  rows = list(
    bind.execute(
      sa.text(
        "SELECT id, account_id, mode, ignored_stock_codes, settings, "
        "config_version FROM t_trade_global_configs ORDER BY id"
      )
    ).mappings()
  )
  config_version_table = sa.table(
    "t_assistant_config_versions",
    sa.column("config_version_id"),
    sa.column("config_id"),
    sa.column("version"),
    sa.column("config_schema_version"),
    sa.column("canonical_payload", sa.JSON()),
    sa.column("config_snapshot_hash"),
    sa.column("entry_authorization"),
    sa.column("rollout_stage"),
    sa.column("policy_version"),
    sa.column("feature_schema_version"),
    sa.column("scorer_mode"),
    sa.column("model_runtime_binding", sa.JSON(none_as_null=True)),
  )
  for row in rows:
    settings = _mapping(row.get("settings"))
    signal_policy = _mapping(settings.get("signal_policy"))
    config_id = str(row["id"])
    version = max(1, int(row.get("config_version") or 1))
    desired_environment = (
      "LIVE" if str(row.get("mode") or "").lower() == "live" else "PAPER"
    )
    payload = {
      "config_schema_version": "t_assistant_config_v1",
      "universe_policy": {
        "ignored_stock_codes": list(row.get("ignored_stock_codes") or []),
      },
      "symbol_rule_policy": signal_policy,
      "portfolio_policy": _mapping(settings.get("portfolio_policy")),
      "t_trading_envelope_policy": _mapping(settings.get("t_trading_envelope_policy")),
      "entry_execution_gate_policy": _mapping(
        settings.get("entry_execution_gate_policy")
      ),
      "exit_plan_template_policy": _mapping(settings.get("exit_plan_template_policy")),
      "legacy_settings_snapshot": settings,
    }
    entry_authorization = _legacy_entry_authorization(settings)
    rollout_stage = _legacy_rollout_stage(settings)
    scorer_mode = str(settings.get("scorer_mode") or "RULE_ONLY").upper()
    model_runtime_binding = _mapping(settings.get("model_runtime_binding")) or None
    if scorer_mode not in {"SHADOW", "ACTIVE"} or model_runtime_binding is None:
      scorer_mode = "RULE_ONLY"
      model_runtime_binding = None
    policy_version = str(
      signal_policy.get("policy_version") or "t_trade_opportunity_v3.0.0"
    )
    feature_schema_version = max(
      1,
      int(signal_policy.get("feature_schema_version") or 1),
    )
    canonical = _canonical_json(payload)
    snapshot_material = {
      "config_schema_version": "t_assistant_config_v1",
      "canonical_payload": json.loads(canonical),
      "entry_authorization": entry_authorization,
      "rollout_stage": rollout_stage,
      "policy_version": policy_version,
      "feature_schema_version": feature_schema_version,
      "scorer_mode": scorer_mode,
      "model_runtime_binding": model_runtime_binding,
    }
    snapshot_hash = hashlib.sha256(
      _canonical_json(snapshot_material).encode("utf-8")
    ).hexdigest()
    config_version_id = str(
      uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"quantx:t-assistant:config:{config_id}:{version}:{snapshot_hash}",
      )
    )
    bind.execute(
      sa.insert(config_version_table).values(
        config_version_id=config_version_id,
        config_id=config_id,
        version=version,
        config_schema_version="t_assistant_config_v1",
        canonical_payload=json.loads(canonical),
        config_snapshot_hash=snapshot_hash,
        entry_authorization=entry_authorization,
        rollout_stage=rollout_stage,
        policy_version=policy_version,
        feature_schema_version=feature_schema_version,
        scorer_mode=scorer_mode,
        model_runtime_binding=model_runtime_binding,
      )
    )
    bind.execute(
      sa.text(
        "UPDATE t_trade_global_configs "
        "SET desired_environment = :environment, "
        "active_config_version_id = :version_id, state_version = 1 "
        "WHERE id = :config_id"
      ),
      {
        "environment": desired_environment,
        "version_id": config_version_id,
        "config_id": config_id,
      },
    )


def _migrate_shared_evidence_owners() -> None:
  for table_name in (
    "t_trade_opportunity_evaluations",
    "t_trade_candidate_outcomes",
  ):
    op.add_column(table_name, sa.Column("owner_type", sa.String(32), nullable=True))
    op.add_column(table_name, sa.Column("owner_id", sa.String(128), nullable=True))
    op.add_column(table_name, sa.Column("environment", sa.String(16), nullable=True))
    op.execute(
      sa.text(
        f"UPDATE {table_name} AS evidence "
        "SET owner_type = 'STRATEGY_RUN', "
        "owner_id = evidence.strategy_run_id, "
        "environment = upper(runs.mode::text) "
        "FROM strategy_runs AS runs "
        "WHERE runs.id = evidence.strategy_run_id"
      )
    )
    op.alter_column(table_name, "owner_type", nullable=False)
    op.alter_column(table_name, "owner_id", nullable=False)
    op.alter_column(table_name, "environment", nullable=False)
    op.alter_column(table_name, "strategy_run_id", nullable=True)

  op.create_check_constraint(
    "ck_t_trade_evaluation_owner_type",
    "t_trade_opportunity_evaluations",
    "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION')",
  )
  op.create_check_constraint(
    "ck_t_trade_evaluation_owner_id",
    "t_trade_opportunity_evaluations",
    "length(owner_id) > 0 AND owner_id = trim(owner_id)",
  )
  op.create_check_constraint(
    "ck_t_trade_evaluation_environment",
    "t_trade_opportunity_evaluations",
    "environment IN ('PAPER','LIVE','BACKTEST')",
  )
  op.create_check_constraint(
    "ck_t_trade_evaluation_strategy_run_owner",
    "t_trade_opportunity_evaluations",
    "strategy_run_id IS NULL OR (owner_type = 'STRATEGY_RUN' "
    "AND owner_id = strategy_run_id)",
  )
  op.create_index(
    "ix_t_trade_evaluation_owner_time",
    "t_trade_opportunity_evaluations",
    ["owner_type", "owner_id", "environment", "evaluated_at", "id"],
  )

  op.drop_constraint(
    "uq_t_trade_candidate_outcome_run_candidate",
    "t_trade_candidate_outcomes",
    type_="unique",
  )
  op.create_unique_constraint(
    "uq_t_trade_candidate_outcome_owner_candidate",
    "t_trade_candidate_outcomes",
    ["environment", "owner_type", "owner_id", "candidate_id"],
  )
  op.create_check_constraint(
    "ck_t_trade_candidate_outcome_owner_type",
    "t_trade_candidate_outcomes",
    "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION')",
  )
  op.create_check_constraint(
    "ck_t_trade_candidate_outcome_owner_id",
    "t_trade_candidate_outcomes",
    "length(owner_id) > 0 AND owner_id = trim(owner_id)",
  )
  op.create_check_constraint(
    "ck_t_trade_candidate_outcome_environment",
    "t_trade_candidate_outcomes",
    "environment IN ('PAPER','LIVE','BACKTEST')",
  )
  op.create_check_constraint(
    "ck_t_trade_candidate_outcome_strategy_run_owner",
    "t_trade_candidate_outcomes",
    "strategy_run_id IS NULL OR (owner_type = 'STRATEGY_RUN' "
    "AND owner_id = strategy_run_id)",
  )
  op.create_index(
    "ix_t_trade_candidate_outcome_owner_status",
    "t_trade_candidate_outcomes",
    ["owner_type", "owner_id", "environment", "status", "instrument_code"],
  )


def _create_runtime_integrity_triggers() -> None:
  op.execute(
    sa.text(
      """
      CREATE OR REPLACE FUNCTION quantx_t_assistant_append_only()
      RETURNS trigger AS $$
      BEGIN
        RAISE EXCEPTION 'T_ASSISTANT_APPEND_ONLY_FACT_IMMUTABLE:%', TG_TABLE_NAME;
      END;
      $$ LANGUAGE plpgsql
      """
    )
  )
  for trigger_name, table_name in (
    (
      "trg_t_assistant_config_version_append_only",
      "t_assistant_config_versions",
    ),
    (
      "trg_t_assistant_execution_event_append_only",
      "t_assistant_execution_events",
    ),
  ):
    op.execute(
      sa.text(
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE UPDATE OR DELETE ON {table_name}
        FOR EACH ROW EXECUTE FUNCTION quantx_t_assistant_append_only()
        """
      )
    )
  op.execute(
    sa.text(
      """
      CREATE OR REPLACE FUNCTION quantx_t_assistant_execution_binding_guard()
      RETURNS trigger AS $$
      BEGIN
        IF NOT EXISTS (
          SELECT 1
          FROM t_assistant_config_versions v
          JOIN t_trade_global_configs config ON config.id = v.config_id
          WHERE v.config_version_id = NEW.config_version_id
            AND config.account_id = NEW.account_id
            AND v.config_id = NEW.config_id
            AND v.version = NEW.frozen_config_version
            AND v.config_snapshot_hash = NEW.config_snapshot_hash
            AND v.entry_authorization = NEW.entry_authorization
            AND v.rollout_stage = NEW.rollout_stage
            AND v.policy_version = NEW.policy_version
            AND v.feature_schema_version = NEW.feature_schema_version
            AND v.scorer_mode = NEW.scorer_mode
            AND v.model_runtime_binding::jsonb
                IS NOT DISTINCT FROM NEW.model_runtime_binding::jsonb
        ) THEN
          RAISE EXCEPTION 'T_ASSISTANT_EXECUTION_CONFIG_BINDING_INVALID';
        END IF;
        IF TG_OP = 'UPDATE' THEN
          IF NEW.state_version <> OLD.state_version + 1 THEN
            RAISE EXCEPTION 'T_ASSISTANT_EXECUTION_STATE_VERSION_INVALID';
          END IF;
          IF (NEW.config_id, NEW.config_version_id, NEW.frozen_config_version,
              NEW.config_snapshot_hash, NEW.account_id, NEW.environment,
              NEW.entry_authorization, NEW.rollout_stage, NEW.policy_version,
              NEW.feature_schema_version, NEW.scorer_mode)
             IS DISTINCT FROM
             (OLD.config_id, OLD.config_version_id, OLD.frozen_config_version,
              OLD.config_snapshot_hash, OLD.account_id, OLD.environment,
              OLD.entry_authorization, OLD.rollout_stage, OLD.policy_version,
              OLD.feature_schema_version, OLD.scorer_mode) THEN
            RAISE EXCEPTION 'T_ASSISTANT_EXECUTION_FROZEN_BINDING_IMMUTABLE';
          END IF;
          IF NEW.model_runtime_binding::jsonb
             IS DISTINCT FROM OLD.model_runtime_binding::jsonb THEN
            RAISE EXCEPTION 'T_ASSISTANT_EXECUTION_FROZEN_BINDING_IMMUTABLE';
          END IF;
          IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
            (OLD.status = 'CREATED' AND NEW.status IN (
              'WARMING','DRAINING','FAILED','RECONCILE_REQUIRED'
            )) OR
            (OLD.status = 'WARMING' AND NEW.status IN (
              'RUNNING','DRAINING','FAILED','RECONCILE_REQUIRED'
            )) OR
            (OLD.status = 'RUNNING' AND NEW.status IN (
              'DRAINING','FAILED','RECONCILE_REQUIRED'
            )) OR
            (OLD.status = 'DRAINING' AND NEW.status IN (
              'STOPPED','FAILED','RECONCILE_REQUIRED'
            )) OR
            (OLD.status = 'RECONCILE_REQUIRED' AND NEW.status IN (
              'DRAINING','FAILED'
            ))
          ) THEN
            RAISE EXCEPTION 'T_ASSISTANT_EXECUTION_TRANSITION_INVALID';
          END IF;
        END IF;
        IF NEW.status = 'RUNNING' AND NEW.entry_readiness <> 'READY' THEN
          RAISE EXCEPTION 'T_ASSISTANT_RUNNING_NOT_READY';
        ELSIF NEW.status = 'DRAINING'
          AND NEW.entry_readiness <> 'DRAINING' THEN
          RAISE EXCEPTION 'T_ASSISTANT_DRAINING_READINESS_INVALID';
        ELSIF NEW.status = 'RECONCILE_REQUIRED'
          AND NEW.entry_readiness <> 'RECONCILE_REQUIRED' THEN
          RAISE EXCEPTION 'T_ASSISTANT_RECONCILE_READINESS_INVALID';
        ELSIF NEW.status IN ('STOPPED','FAILED')
          AND NEW.entry_readiness <> 'BLOCKED' THEN
          RAISE EXCEPTION 'T_ASSISTANT_TERMINAL_READINESS_INVALID';
        END IF;
        RETURN NEW;
      END;
      $$ LANGUAGE plpgsql
      """
    )
  )
  op.execute(
    sa.text(
      """
      CREATE TRIGGER trg_t_assistant_execution_binding_guard
      BEFORE INSERT OR UPDATE ON t_assistant_executions
      FOR EACH ROW EXECUTE FUNCTION quantx_t_assistant_execution_binding_guard()
      """
    )
  )
  op.execute(
    sa.text(
      """
      CREATE OR REPLACE FUNCTION quantx_t_assistant_config_head_guard()
      RETURNS trigger AS $$
      BEGIN
        IF TG_OP = 'UPDATE' AND NEW.state_version <> OLD.state_version + 1 THEN
          RAISE EXCEPTION 'T_ASSISTANT_CONFIG_HEAD_STATE_VERSION_INVALID';
        END IF;
        IF NEW.active_config_version_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM t_assistant_config_versions v
          WHERE v.config_version_id = NEW.active_config_version_id
            AND v.config_id = NEW.id
            AND v.version = NEW.config_version
        ) THEN
          RAISE EXCEPTION 'T_ASSISTANT_CONFIG_HEAD_VERSION_INVALID';
        END IF;
        RETURN NEW;
      END;
      $$ LANGUAGE plpgsql
      """
    )
  )
  op.execute(
    sa.text(
      """
      CREATE TRIGGER trg_t_assistant_config_head_guard
      BEFORE INSERT OR UPDATE OF active_config_version_id
      ON t_trade_global_configs
      FOR EACH ROW EXECUTE FUNCTION quantx_t_assistant_config_head_guard()
      """
    )
  )
  for table_name, trigger_name in (
    (
      "t_trade_opportunity_evaluations",
      "trg_t_trade_evaluation_owner_immutable",
    ),
    (
      "t_trade_candidate_outcomes",
      "trg_t_trade_candidate_outcome_owner_immutable",
    ),
  ):
    op.execute(
      sa.text(
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE UPDATE OF owner_type, owner_id, environment, strategy_run_id
        ON {table_name}
        FOR EACH ROW EXECUTE FUNCTION quantx_reject_identity_mutation(
          'owner_type', 'owner_id', 'environment', 'strategy_run_id'
        )
        """
      )
    )


def upgrade() -> None:
  _prune_orphaned_opportunity_diagnostics()
  _preflight_legacy_evidence()
  _create_runtime_tables()
  op.add_column(
    "t_trade_global_configs",
    sa.Column(
      "desired_environment",
      sa.String(length=16),
      nullable=False,
      server_default="PAPER",
    ),
  )
  op.add_column(
    "t_trade_global_configs",
    sa.Column("active_config_version_id", sa.String(length=36), nullable=True),
  )
  op.add_column(
    "t_trade_global_configs",
    sa.Column(
      "state_version",
      sa.Integer(),
      nullable=False,
      server_default="1",
    ),
  )
  _backfill_config_versions()
  op.create_foreign_key(
    "fk_t_trade_global_config_active_version",
    "t_trade_global_configs",
    "t_assistant_config_versions",
    ["active_config_version_id"],
    ["config_version_id"],
    ondelete="RESTRICT",
  )
  op.create_check_constraint(
    "ck_t_trade_global_config_desired_environment",
    "t_trade_global_configs",
    "desired_environment IN ('PAPER','LIVE')",
  )
  op.create_check_constraint(
    "ck_t_trade_global_config_state_version",
    "t_trade_global_configs",
    "state_version >= 1",
  )
  _migrate_shared_evidence_owners()
  _create_runtime_integrity_triggers()


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
