"""Atomic initial CANARY readiness after release, market warmup, and account checks."""

from datetime import UTC

from quantx_application.t_trade_v3.execution_use_cases import (
  TAssistantExecutionLifecycle,
)
from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_contracts import PROTOCOL_VERSION
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_infrastructure.models.agent_runtime import AccountExecutionControl
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.repositories.t_assistant_symbol_state_repository import (
  TAssistantSymbolStateRepository,
)
from quantx_infrastructure.services.live_portfolio_snapshot import (
  LivePortfolioSnapshotReader,
)
from sqlalchemy import select

from .t_assistant_live_admission import prepare_live_canary_execution


class LiveReadinessBlocked(ValueError):
  pass


def _require(condition, reason):
  if not condition:
    raise LiveReadinessBlocked(reason)


def _stored(value):
  return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def activate_live_canary_ready(
  db,
  *,
  execution_id,
  cycle_id,
  instrument_codes,
  market_capture,
  market_mark_reader,
  health,
  health_observed_at,
  now,
  validate_before_activation=None,
):
  """Caller owns the transaction. Health comes from shared TTradeOperationsService.

  The external health read is bound to the same durable account control version
  and snapshot inside this frame. Missing evidence never becomes zero exposure or
  implicit authorization; no approval or broker command is created here.
  """
  now = aware_time(now).astimezone(UTC)
  observed = aware_time(health_observed_at).astimezone(UTC)
  codes = tuple(sorted(set(instrument_codes)))
  _require(db.in_transaction() and codes, "LIVE_READY_TRANSACTION_AND_SYMBOLS_REQUIRED")
  _require(
    0 <= (now - observed).total_seconds() < 90, "LIVE_READY_HEALTH_STALE_OR_FUTURE"
  )
  async with db.begin_nested():
    probe = await db.get(TAssistantExecutionRecord, execution_id)
    _require(probe is not None, "LIVE_READY_EXECUTION_REQUIRED")
    head = await db.get(
      TTradeGlobalConfig, probe.config_id, with_for_update=True, populate_existing=True
    )
    row = await db.get(
      TAssistantExecutionRecord,
      execution_id,
      with_for_update=True,
      populate_existing=True,
    )
    _require(
      head is not None
      and head.enabled
      and not head.strategy_run_id
      and head.desired_environment == "LIVE"
      and head.account_id == row.account_id
      and head.active_config_version_id == row.config_version_id
      and head.config_version == row.frozen_config_version
      and row.environment == "LIVE"
      and row.entry_authorization == "MANUAL_CONFIRM"
      and row.rollout_stage == "CANARY"
      and row.scorer_mode == "RULE_ONLY",
      "LIVE_READY_SOURCE_CHANGED",
    )
    repository = TAssistantExecutionRepository(db)
    execution = await repository.get_domain(execution_id)
    _require(
      row.status == "WARMING" or (row.status == "RUNNING" and row.entry_readiness == "DEGRADED"),
      "LIVE_READY_WARMING_REQUIRED",
    )
    prepared = await db.scalar(
      select(TAssistantExecutionEventRecord).where(
        TAssistantExecutionEventRecord.execution_id == execution_id,
        TAssistantExecutionEventRecord.event_key
        == f"live-canary-prepared:{execution_id}",
      )
    )
    _require(
      prepared is not None
      and prepared.event_type == "LIVE_CANARY_EXECUTION_PREPARED"
      and _stored(prepared.occurred_at) <= now,
      "LIVE_READY_ADMISSION_REQUIRED",
    )
    material = prepared.payload
    _require(
      set(codes) <= set(material.get("allowed_stock_codes", [])),
      "LIVE_READY_CANARY_SCOPE_CONFLICT",
    )
    identity = await prepare_live_canary_execution(
      db,
      source_execution_id=material.get("source_execution_id"),
      config_version_id=row.config_version_id,
      approval_event_key=material.get("approval_event_key"),
      expected_head_version=head.state_version,
      now=now,
    )
    _require(identity == execution_id, "LIVE_READY_ADMISSION_IDENTITY_CONFLICT")
    approval = await db.scalar(
      select(TAssistantExecutionEventRecord).where(
        TAssistantExecutionEventRecord.execution_id == material["source_execution_id"],
        TAssistantExecutionEventRecord.event_key == material["approval_event_key"],
      )
    )
    if row.status == "WARMING":
      _require(
        aware_time(approval.payload["window_start"]) <= now < aware_time(approval.payload["window_end"]),
        "LIVE_READY_OUTSIDE_MAINTENANCE_WINDOW",
      )
    else:
      initial_ready = await db.scalar(
        select(TAssistantExecutionEventRecord).where(
          TAssistantExecutionEventRecord.execution_id == execution_id,
          TAssistantExecutionEventRecord.event_type == "EXECUTION_ENTRY_READY",
        ).order_by(TAssistantExecutionEventRecord.occurred_at).limit(1)
      )
      _require(
        initial_ready is not None
        and initial_ready.payload.get("admission_event_key") == prepared.event_key
        and aware_time(approval.payload["window_start"]) <= _stored(initial_ready.occurred_at)
        < aware_time(approval.payload["window_end"])
        and _stored(initial_ready.occurred_at) <= now,
        "LIVE_READY_INITIAL_ACTIVATION_REQUIRED",
      )
    cycle = await db.get(
      TAssistantDecisionCycleRecord,
      cycle_id,
      with_for_update=True,
      populate_existing=True,
    )
    _require(
      cycle is not None
      and cycle.execution_id == execution_id
      and cycle.status == "PROPOSALS_COMMITTED"
      and cycle.committed_at is not None
      and _stored(cycle.committed_at) <= now,
      "LIVE_READY_COMMITTED_CYCLE_REQUIRED",
    )
    _require(
      stable_manifest_hash(cycle.input_manifest) == cycle.input_manifest_hash
      and stable_manifest_hash(cycle.output_manifest) == cycle.output_manifest_hash
      and cycle.input_manifest.get("config_snapshot_hash") == row.config_snapshot_hash,
      "LIVE_READY_CYCLE_MATERIAL_CONFLICT",
    )
    _require(
      0 <= (now - _stored(cycle.committed_at)).total_seconds() < 90
      and cycle.input_manifest.get("stream_id") == market_capture.stream_id
      and cycle.input_manifest.get("continuity_generation")
      == market_capture.continuity_generation
      and type(cycle.input_manifest.get("fence_sequence")) is int
      and 0 < cycle.input_manifest["fence_sequence"] <= market_capture.fence_sequence,
      "LIVE_READY_CYCLE_MARKET_CONFLICT",
    )
    _require(
      market_capture.ready
      and 0 <= (now - aware_time(market_capture.captured_at)).total_seconds() < 90,
      "LIVE_READY_MARKET_CAPTURE_INVALID",
    )
    states = await TAssistantSymbolStateRepository(db).load_domains(execution_id)
    _require(
      all(
        code in states
        and states[code].lifecycle.value == "ACTIVE"
        and states[code].cursor is not None
        and states[code].cursor.stream_id == market_capture.stream_id
        and states[code].cursor.continuity_generation
        == market_capture.continuity_generation
        for code in codes
      ),
      "LIVE_READY_MARKET_WARMING",
    )
    for code in codes:
      manifest = cycle.output_manifest.get("symbol_state_manifest", {})
      expected_hash = manifest.get(
        code,
        cycle.input_manifest.get("symbols", {})
        .get(code, {})
        .get("state_material_manifest_hash"),
      )
      _require(
        bool(expected_hash) and expected_hash == states[code].material_manifest_hash,
        "LIVE_READY_SYMBOL_CYCLE_CONFLICT",
      )
    _require(
      health.get("account_id") == row.account_id
      and health.get("can_approve") is True
      and health.get("rollout_enabled") is True
      and health.get("stage") == "CANARY"
      and health.get("protocol_version") == PROTOCOL_VERSION
      and type(health.get("ready_live_agent_count")) is int
      and health["ready_live_agent_count"] == 1
      and health.get("agent_mode") == "live"
      and bool(health.get("agent_device_id")),
      "LIVE_READY_ACCOUNT_OR_AGENT_BLOCKED",
    )
    portfolio = await LivePortfolioSnapshotReader(db).read(
      execution_id=execution_id,
      cycle_id=cycle_id,
      instrument_codes=codes,
      as_of=now,
      market_mark_reader=market_mark_reader,
      account_max_age_seconds=90,
    )
    _require(
      not set(portfolio.entry_blockers) - {"T_ACCOUNT_ENTRY_DISABLED"},
      "LIVE_READY_PORTFOLIO_BLOCKED",
    )
    control = await db.get(AccountExecutionControl, row.account_id)
    _require(
      control is not None
      and control.authorization_state == "ENABLED"
      and control.reconcile_status == "READY"
      and control.last_snapshot_id == portfolio.cut.account_snapshot_id
      and control.last_snapshot_hash == portfolio.cut.account_snapshot_hash
      and health.get("snapshot_id") == portfolio.cut.account_snapshot_id
      and health.get("snapshot_hash") == portfolio.cut.account_snapshot_hash
      and type(health.get("account_safety", {}).get("state_version")) is int
      and health["account_safety"]["state_version"] == control.state_version,
      "LIVE_READY_ACCOUNT_CUT_CHANGED",
    )
    if validate_before_activation is not None:
      validate_before_activation()
    return await TAssistantExecutionLifecycle(repository).activate_ready(
      execution,
      at=now,
      payload={
        "cycle_id": cycle_id,
        "admission_event_key": prepared.event_key,
        "portfolio_input_fingerprint": portfolio.portfolio_input_fingerprint,
        "account_snapshot_id": portfolio.cut.account_snapshot_id,
        "account_snapshot_hash": portfolio.cut.account_snapshot_hash,
        "account_control_version": control.state_version,
        "agent_device_id": health["agent_device_id"],
        "protocol_version": PROTOCOL_VERSION,
        "market_stream_id": market_capture.stream_id,
        "market_generation": market_capture.continuity_generation,
        "market_fence_sequence": market_capture.fence_sequence,
        "health_observed_at": observed.isoformat(),
      },
    )
