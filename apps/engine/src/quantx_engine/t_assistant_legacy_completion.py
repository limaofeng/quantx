"""Internal atomic legacy completion after authoritative obligation convergence."""

from dataclasses import asdict
from datetime import UTC, datetime

from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_infrastructure.core.assistant_strategy_policy import (
  T_TRADE_STRATEGY_CLASS_NAME,
)
from quantx_infrastructure.models.agent_runtime import (
  AGENT_REPORT_SNAPSHOT_ID,
  AccountExecutionControl,
  AgentReportInbox,
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
  TTradeRolloutEvent,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.enums import StrategyRunMode, StrategyRunStatus
from quantx_infrastructure.models.strategy import Strategy
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.t_legacy_drain_guard import (
  LEGACY_T_DRAIN_EVENT,
  legacy_t_drain_event_id,
)
from sqlalchemy import or_, select
from sqlalchemy.orm.attributes import flag_modified

from .t_assistant_legacy_settlement import read_legacy_order_settlement

COMPLETED_EVENT = "LEGACY_T_DRAIN_COMPLETED"
_INTENT_TERMINAL = {
  "CANCELLED",
  "EXPIRED",
  "REJECTED",
  "FILLED",
  "RECONCILED_ZERO_FILL",
}


def _utc(value):
  return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def complete_legacy_t_drain(
  db,
  *,
  config_id: str,
  run_id: str,
  expected_head_version: int,
  actor_id: str,
  now: datetime,
):
  """Caller owns authorization, SERIALIZABLE commit/retry and post-commit memory cleanup.

  This is not an exposed mutation or automatic cutover. It consumes an existing
  drain fence, proves all original obligations, stops only the generic source
  record and clears its head pointer. Public ExitPlan/broker owners stay intact.
  A rejected proof or audit write rolls back the entire nested frame.
  """
  if (
    not db.in_transaction()
    or not isinstance(now, datetime)
    or now.tzinfo is None
    or now.utcoffset() is None
    or type(expected_head_version) is not int
    or expected_head_version < 1
    or any(
      not isinstance(value, str) or not value.strip()
      for value in (config_id, run_id, actor_id)
    )
  ):
    raise ValueError("LEGACY_T_COMPLETION_REQUEST_INVALID")
  connection = await db.connection()
  if (await connection.get_isolation_level()).upper() != "SERIALIZABLE":
    raise ValueError("LEGACY_T_COMPLETION_SERIALIZABLE_REQUIRED")
  now = _utc(now)
  request = dict(
    config_id=config_id, run_id=run_id, expected_head_version=expected_head_version
  )
  identity = f"legacy-t-completed:{run_id}"
  async with db.begin_nested():
    head = await db.get(
      TTradeGlobalConfig, config_id, with_for_update=True, populate_existing=True
    )
    if head is None:
      raise ValueError("LEGACY_T_COMPLETION_HEAD_REQUIRED")
    existing = await db.get(TTradeRolloutEvent, identity)
    if existing is not None:
      details = dict(existing.details or {})
      evidence = details.get("evidence")
      if (
        existing.event_type != COMPLETED_EVENT
        or existing.next_stage != "STOPPED"
        or existing.account_id != head.account_id
        or existing.actor_user_id != actor_id
        or details.get("request") != request
        or not isinstance(evidence, dict)
        or details.get("evidence_hash") != stable_manifest_hash(evidence)
      ):
        raise ValueError("LEGACY_T_COMPLETION_REPLAY_CONFLICT")
      return details
    run = await db.get(
      StrategyRun, run_id, with_for_update=True, populate_existing=True
    )
    strategy = await db.get(Strategy, run.strategy_id) if run else None
    marker = await db.get(TTradeRolloutEvent, legacy_t_drain_event_id(run_id))
    material = dict(marker.details or {}) if marker else {}
    if (
      head.strategy_run_id != run_id
      or head.mode != "live"
      or head.state_version != expected_head_version
      or run is None
      or strategy is None
      or strategy.class_name != T_TRADE_STRATEGY_CLASS_NAME
      or run.mode != StrategyRunMode.LIVE
      or run.status not in {StrategyRunStatus.RUNNING, StrategyRunStatus.PAUSED}
      or dict(run.parameters or {}).get("account_id") != head.account_id
      or marker is None
      or marker.event_type != LEGACY_T_DRAIN_EVENT
      or marker.next_stage != "DRAINING"
      or marker.account_id != head.account_id
      or marker.actor_user_id != actor_id
      or material.get("run_id") != run_id
      or not isinstance(material.get("request"), dict)
      or material["request"].get("config_id") != config_id
      or material["request"].get("run_id") != run_id
      or _utc(marker.created_at) > now
      or any(
        value is not None and _utc(value) > now
        for value in (
          head.updated_at,
          run.updated_at,
          run.start_time,
        )
      )
    ):
      raise ValueError("LEGACY_T_COMPLETION_SOURCE_CONFLICT")
    control = await db.get(
      AccountExecutionControl,
      head.account_id,
      with_for_update=True,
      populate_existing=True,
    )
    snapshot = None
    if control is not None and control.last_snapshot_id:
      snapshot = await db.scalar(
        select(AgentReportInbox)
        .where(
          AgentReportInbox.message_type == "delta_report",
          AGENT_REPORT_SNAPSHOT_ID == control.last_snapshot_id,
        )
        .order_by(AgentReportInbox.received_at.desc())
        .limit(1)
        .with_for_update()
      )
    from .report_processor import (
      _authoritative_snapshot_identity,
      _parse_authoritative_snapshot_time,
      _snapshot_fully_covers_account,
    )

    if (
      control is None
      or control.reconcile_status != "READY"
      or control.last_snapshot_at is None
      or not 0 <= (now - _utc(control.last_snapshot_at)).total_seconds() < 90
      or _utc(control.last_snapshot_at) < _utc(marker.created_at)
      or snapshot is None
      or snapshot.processing_status != "PROCESSED"
      or snapshot.processed_at is None
      or _utc(snapshot.processed_at) > now
      or _utc(snapshot.received_at) > now
      or _authoritative_snapshot_identity(snapshot)
      != (control.last_snapshot_id, control.last_snapshot_hash)
      or not _snapshot_fully_covers_account(snapshot.payload, head.account_id)
      or _utc(
        _parse_authoritative_snapshot_time(snapshot.payload.get("source_event_at"))
      )
      != _utc(control.last_snapshot_at)
    ):
      raise ValueError("LEGACY_T_COMPLETION_ACCOUNT_PROOF_REQUIRED")

    async def owned(model, source=False):
      prefix = "source_execution_" if source else ""
      records = list(
        await db.scalars(
          select(model)
          .where(
            getattr(model, f"{prefix}owner_type") == "STRATEGY_RUN",
            getattr(model, f"{prefix}owner_id") == run_id,
          )
          .order_by(*model.__table__.primary_key.columns)
          .with_for_update()
          .execution_options(populate_existing=True)
        )
      )
      for row in records:
        if (
          row.environment != "LIVE"
          or (source and row.source_execution_environment != "LIVE")
          or (hasattr(row, "account_id") and row.account_id != head.account_id)
          or any(
            value is not None and _utc(value) > now
            for value in (
              getattr(row, "created_at", None),
              getattr(row, "updated_at", None),
            )
          )
        ):
          raise ValueError("LEGACY_T_COMPLETION_FACT_SCOPE_CONFLICT")
      return records

    intents = await owned(TradeIntentRecord)
    pending = await owned(PendingTradeOrder)
    correlations = await owned(OrderCorrelation)
    commands = await owned(TradeCommandOutbox)
    events = await owned(StrategyRuntimeEvent)
    batches, plans = (
      await owned(TTradeBatch, True),
      await owned(AutoExitPlanRecord, True),
    )
    clients = {row.client_order_id for row in pending}
    if any(row.client_order_id not in clients for row in [*correlations, *events]):
      raise ValueError("LEGACY_T_COMPLETION_ORPHAN_OBLIGATION")
    devices = {snapshot.device_id, *(row.device_id for row in commands)}
    backlog = await db.scalar(
      select(AgentReportInbox.message_id)
      .where(
        or_(
          AgentReportInbox.device_id.in_(devices),
          AgentReportInbox.client_order_id.in_(clients),
        ),
        AgentReportInbox.message_type.in_(
          {"delta_report", "order_report", "execution_report", "command_ack"}
        ),
        AgentReportInbox.processing_status.not_in({"PROCESSED", "SUPERSEDED"}),
      )
      .limit(1)
      .with_for_update()
    )
    if backlog is not None:
      raise ValueError("LEGACY_T_COMPLETION_INBOX_BACKLOG")
    settlements = []
    filled_by_intent = {}
    pending_by_client = {row.client_order_id: row for row in pending}
    for order in pending:
      parent = pending_by_client.get(order.t_order_parent_client_id)
      if order.t_order_parent_client_id:
        if (
          parent is None
          or parent.client_order_id == order.client_order_id
          or parent.t_order_attempt + 1 != order.t_order_attempt
          or order.t_order_original_created_at is None
          or any(
            getattr(parent, key) != getattr(order, key)
            for key in (
              "intent_id",
              "instrument_code",
              "side",
              "batch_id",
              "bucket",
              "t_trade_role",
              "t_order_original_created_at",
            )
          )
        ):
          raise ValueError("LEGACY_T_COMPLETION_PARENT_CONFLICT")
      elif order.t_order_attempt != 0:
        raise ValueError("LEGACY_T_COMPLETION_PARENT_CONFLICT")
      result = await read_legacy_order_settlement(
        db,
        account_id=head.account_id,
        run_id=run_id,
        client_order_id=order.client_order_id,
        now=now,
      )
      if result.blocker:
        raise ValueError(result.blocker)
      settlements.append(
        {**asdict(result), "runtime_event_ids": list(result.runtime_event_ids)}
      )
      filled_by_intent[order.intent_id] = (
        filled_by_intent.get(order.intent_id, 0) + result.filled_volume
      )
    proven_broker_ids = {
      str(row.broker_order_id) for row in pending if row.broker_order_id
    }
    placed_clients = set()
    for command in commands:
      payload = dict(command.payload or {})
      if command.client_order_id in clients:
        order = pending_by_client[command.client_order_id]
        if (
          payload.get("command_kind") != "PLACE_ORDER"
          or payload.get("execution_mode") != "live"
          or payload.get("account_id") != head.account_id
          or payload.get("client_order_id") != command.client_order_id
          or payload.get("instrument_code") != order.instrument_code
          or payload.get("side") != order.side
          or type(payload.get("volume")) is not int
          or payload["volume"] != order.volume
        ):
          raise ValueError("LEGACY_T_COMPLETION_COMMAND_BINDING_REQUIRED")
        placed_clients.add(command.client_order_id)
        continue
      if (
        payload.get("command_kind") != "CANCEL_ORDER"
        or payload.get("execution_mode") != "live"
        or payload.get("account_id") != head.account_id
        or payload.get("client_order_id") != command.client_order_id
        or str(payload.get("broker_order_id") or "") not in proven_broker_ids
        or command.delivery_status
        not in {"ACKNOWLEDGED", "EXPIRED", "CANCELLED", "RECONCILE_REQUIRED"}
      ):
        raise ValueError("LEGACY_T_COMPLETION_ORPHAN_COMMAND")
    if placed_clients != clients:
      raise ValueError("LEGACY_T_COMPLETION_COMMAND_BINDING_REQUIRED")
    for intent in intents:
      if intent.status not in _INTENT_TERMINAL or (
        intent.executed_volume or 0
      ) != filled_by_intent.get(intent.id, 0):
        raise ValueError("LEGACY_T_COMPLETION_INTENT_UNSETTLED")
      if intent.id not in filled_by_intent and (
        intent.status not in {"CANCELLED", "EXPIRED", "REJECTED"}
        or intent.order_id
        or intent.executed_price
        or intent.executed_time
      ):
        raise ValueError("LEGACY_T_COMPLETION_ORPHAN_INTENT")
    if not set(filled_by_intent).issubset({row.id for row in intents}):
      raise ValueError("LEGACY_T_COMPLETION_INTENT_BINDING_REQUIRED")
    evidence = dict(
      snapshot_id=control.last_snapshot_id,
      snapshot_hash=control.last_snapshot_hash,
      snapshot_message_id=snapshot.message_id,
      settlements=settlements,
      intent_ids=[row.id for row in intents],
      command_ids=[row.message_id for row in commands],
      retained_batch_ids=[row.batch_id for row in batches],
      retained_exit_plan_ids=[row.plan_id for row in plans],
    )
    details = dict(
      request=request, evidence=evidence, evidence_hash=stable_manifest_hash(evidence)
    )
    head.strategy_run_id = None
    head.state_version += 1
    head.updated_at = now.replace(tzinfo=None)
    run.status = StrategyRunStatus.STOPPED
    run.stop_time = now.replace(tzinfo=None)
    run.updated_at = now.replace(tzinfo=None)
    flag_modified(head, "updated_at")
    flag_modified(run, "updated_at")
    db.add(
      TTradeRolloutEvent(
        event_id=identity,
        event_type=COMPLETED_EVENT,
        account_id=head.account_id,
        actor_user_id=actor_id,
        previous_stage="DRAINING",
        next_stage="STOPPED",
        details=details,
        created_at=now.replace(tzinfo=None),
      )
    )
    await db.flush()
    return details
