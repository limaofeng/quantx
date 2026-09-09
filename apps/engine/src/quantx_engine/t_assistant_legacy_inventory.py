"""Freeze legacy ownership facts for cutover review; this grants no entry authority."""

from datetime import UTC, datetime

from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
  TTradeRolloutEvent,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import select


async def freeze_legacy_t_obligation_inventory(
  db,
  *,
  config_id: str,
  run_id: str,
  expected_head_version: int,
  operation_id: str,
  actor_id: str,
  now: datetime,
):
  """Caller transaction records a review inventory, without cancelling or moving work.

  All durable attempts are retained, including terminal-looking and orphan rows.
  A repeated operation must still match the current inventory. The eventual
  cutover must fence new ENTRY and revalidate this evidence before releasing the
  old head binding; this function does neither and never certifies zero debt.
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
      for value in (config_id, run_id, operation_id, actor_id)
    )
    or len(operation_id) > 128
    or len(actor_id) > 36
  ):
    raise ValueError("LEGACY_T_INVENTORY_REQUEST_INVALID")
  now = now.astimezone(UTC)
  async with db.begin_nested():
    head = await db.get(
      TTradeGlobalConfig, config_id, with_for_update=True, populate_existing=True
    )
    if (
      head is None
      or head.strategy_run_id != run_id
      or head.state_version != expected_head_version
      or head.mode != "live"
    ):
      raise ValueError("LEGACY_T_INVENTORY_HEAD_CONFLICT")

    async def owned(model, source=False):
      owner_type = model.source_execution_owner_type if source else model.owner_type
      owner_id = model.source_execution_owner_id if source else model.owner_id
      rows = list(
        await db.scalars(
          select(model)
          .where(
            owner_type == "STRATEGY_RUN",
            owner_id == run_id,
          )
          .order_by(*model.__table__.primary_key.columns)
          .with_for_update()
          .execution_options(populate_existing=True)
        )
      )
      for row in rows:
        if (
          row.environment != "LIVE"
          or (source and row.source_execution_environment != "LIVE")
          or (hasattr(row, "account_id") and row.account_id != head.account_id)
        ):
          raise ValueError("LEGACY_T_INVENTORY_OWNER_SCOPE_CONFLICT")
        for name in ("created_at", "updated_at"):
          value = getattr(row, name, None)
          if (
            value
            and (
              value.replace(tzinfo=UTC)
              if value.tzinfo is None
              else value.astimezone(UTC)
            )
            > now
          ):
            raise ValueError("LEGACY_T_INVENTORY_FUTURE_FACT")
      return rows

    intents = await owned(TradeIntentRecord)
    pending = await owned(PendingTradeOrder)
    correlations = await owned(OrderCorrelation)
    commands = await owned(TradeCommandOutbox)
    runtime_events = await owned(StrategyRuntimeEvent)
    batches = await owned(TTradeBatch, source=True)
    plans = await owned(AutoExitPlanRecord, source=True)
    linked_intents = {row.intent_id for row in [*pending, *correlations]}
    # An orphan outbox is still a broker obligation. Never infer safety from
    # absence of its PendingTradeOrder or OrderCorrelation counterpart.
    clients = sorted(
      {row.client_order_id for row in [*pending, *correlations, *commands]}
    )
    has_orphan_command = any(
      row.client_order_id
      not in {value.client_order_id for value in [*pending, *correlations]}
      for row in commands
    )
    reviewable = sorted(
      row.id
      for row in intents
      if (
        not has_orphan_command
        and row.direction == "BUY"
        and row.id not in linked_intents
        and not row.order_id
        and not row.executed_volume
        and not row.executed_price
        and row.executed_time is None
        and row.status
        in {
          "ALLOCATION_PENDING",
          "AWAITING_APPROVAL",
          "PENDING",
          "APPROVED",
          "EXECUTION_READY",
        }
      )
    )

    def project(rows, fields):
      def value(row, field):
        raw = getattr(row, field)
        if isinstance(raw, datetime):
          return (
            raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)
          ).isoformat()
        return raw

      return [{field: value(row, field) for field in fields} for row in rows]

    manifest = {
      "schema": "legacy-t-obligations.v1",
      "config_id": config_id,
      "account_id": head.account_id,
      "run_id": run_id,
      "head_version": expected_head_version,
      "intents": project(
        intents,
        (
          "id",
          "direction",
          "instrument_code",
          "status",
          "target_volume",
          "target_amount",
          "executed_volume",
          "executed_price",
          "order_id",
        ),
      ),
      "pending": project(
        pending,
        (
          "client_order_id",
          "intent_id",
          "broker_order_id",
          "instrument_code",
          "side",
          "volume",
          "limit_price",
          "status",
          "batch_id",
          "t_trade_role",
          "last_source_sequence",
          "t_order_attempt",
          "t_order_original_created_at",
        ),
      ),
      "correlations": project(
        correlations,
        (
          "id",
          "client_order_id",
          "intent_id",
          "broker_order_id",
          "batch_id",
          "t_trade_role",
        ),
      ),
      "commands": project(
        commands, ("message_id", "client_order_id", "delivery_status", "expires_at")
      ),
      "runtime_events": project(
        runtime_events,
        ("event_id", "client_order_id", "event_type", "application_status"),
      ),
      "batches": project(
        batches,
        (
          "batch_id",
          "instrument_code",
          "status",
          "entry_intent_id",
          "entry_filled_volume",
          "exit_filled_volume",
        ),
      ),
      "exit_plans": project(
        plans,
        (
          "plan_id",
          "source_type",
          "source_id",
          "instrument_code",
          "status",
          "enabled",
          "protected_volume",
          "exited_volume",
          "remaining_volume",
          "auto_exit_authorized",
          "config_version",
          "state_version",
        ),
      ),
      "retained_client_order_ids": clients,
      "unsubmitted_intent_ids_for_review": reviewable,
      "intent_metadata_hashes": {
        row.id: stable_manifest_hash(row.intent_metadata or {}) for row in intents
      },
      "command_payload_hashes": {
        row.message_id: stable_manifest_hash(row.payload) for row in commands
      },
      "runtime_payload_hashes": {
        row.event_id: stable_manifest_hash(row.payload) for row in runtime_events
      },
      "pending_metadata_hashes": {
        row.client_order_id: stable_manifest_hash(row.request_metadata or {})
        for row in pending
      },
      "exit_plan_state_hashes": {
        row.plan_id: stable_manifest_hash(row.plan_state or {}) for row in plans
      },
    }
    digest = stable_manifest_hash(manifest)
    existing = await db.get(TTradeRolloutEvent, operation_id)
    if existing is not None:
      if (
        existing.event_type != "LEGACY_T_OBLIGATION_INVENTORY_FROZEN"
        or existing.account_id != head.account_id
        or existing.actor_user_id != actor_id
        or existing.details != {"manifest": manifest, "manifest_hash": digest}
      ):
        raise ValueError("LEGACY_T_INVENTORY_CHANGED")
      return digest
    db.add(
      TTradeRolloutEvent(
        event_id=operation_id,
        account_id=head.account_id,
        event_type="LEGACY_T_OBLIGATION_INVENTORY_FROZEN",
        actor_user_id=actor_id,
        details={"manifest": manifest, "manifest_hash": digest},
        created_at=now.replace(tzinfo=None),
      )
    )
    await db.flush()
    return digest


async def dispatch_legacy_inventory(db, *, command_id, payload, now):
  """Persist the review once; retry returns that cut, never a silently newer cut."""
  from quantx_infrastructure.core.assistant_strategy_policy import (
    T_TRADE_STRATEGY_CLASS_NAME,
  )
  from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
  from quantx_infrastructure.models.enums import StrategyRunMode, StrategyRunStatus
  from quantx_infrastructure.models.strategy import Strategy
  from quantx_infrastructure.models.strategy_run import StrategyRun

  required = {"account_id", "config_id", "run_id", "expected_head_version", "actor_id"}
  if (
    not db.in_transaction()
    or not command_id
    or set(payload) != required
    or type(payload["expected_head_version"]) is not int
    or payload["expected_head_version"] < 1
    or any(
      not isinstance(payload[key], str) or not payload[key].strip()
      for key in required - {"expected_head_version"}
    )
  ):
    raise ValueError("LEGACY_T_INVENTORY_REQUEST_INVALID")
  command = await db.get(EngineCommandOutbox, command_id)
  if (
    command is None
    or command.command_type != "T_ASSISTANT_PREPARE_LEGACY_INVENTORY"
    or command.aggregate_id != payload["run_id"]
    or command.payload != payload
  ):
    raise ValueError("LEGACY_T_INVENTORY_DURABLE_COMMAND_REQUIRED")
  head = await db.get(
    TTradeGlobalConfig,
    payload["config_id"],
    with_for_update=True,
    populate_existing=True,
  )
  if head is None or head.account_id != payload["account_id"]:
    raise ValueError("LEGACY_T_INVENTORY_HEAD_CONFLICT")
  operation_id = f"legacy-inventory:{command_id}"
  existing = await db.get(TTradeRolloutEvent, operation_id)
  if existing is not None:
    manifest = dict(existing.details.get("manifest") or {})
    if (
      existing.event_type != "LEGACY_T_OBLIGATION_INVENTORY_FROZEN"
      or existing.account_id != payload["account_id"]
      or existing.actor_user_id != payload["actor_id"]
      or any(
        manifest.get(key) != payload[key]
        for key in ("account_id", "config_id", "run_id")
      )
      or manifest.get("head_version") != payload["expected_head_version"]
      or existing.details.get("manifest_hash") != stable_manifest_hash(manifest)
    ):
      raise ValueError("LEGACY_T_INVENTORY_REPLAY_CONFLICT")
  else:
    run = await db.get(
      StrategyRun, payload["run_id"], with_for_update=True, populate_existing=True
    )
    strategy = await db.get(Strategy, run.strategy_id) if run else None
    if (
      run is None
      or strategy is None
      or strategy.class_name != T_TRADE_STRATEGY_CLASS_NAME
      or run.mode != StrategyRunMode.LIVE
      or run.status not in {StrategyRunStatus.RUNNING, StrategyRunStatus.PAUSED}
      or dict(run.parameters or {}).get("account_id") != payload["account_id"]
    ):
      raise ValueError("LEGACY_T_INVENTORY_RUN_SCOPE_CONFLICT")
    await freeze_legacy_t_obligation_inventory(
      db,
      config_id=payload["config_id"],
      run_id=payload["run_id"],
      expected_head_version=payload["expected_head_version"],
      operation_id=operation_id,
      actor_id=payload["actor_id"],
      now=now,
    )
    existing = await db.get(TTradeRolloutEvent, operation_id)
  return {"success": True, "inventory_operation_id": operation_id, **existing.details}
