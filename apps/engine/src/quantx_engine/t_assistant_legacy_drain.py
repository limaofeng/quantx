"""Atomic legacy cutover fence, retaining all original broker/ExitPlan owners."""

from datetime import UTC, datetime

from quantx_infrastructure.core.assistant_strategy_policy import (
  T_TRADE_STRATEGY_CLASS_NAME,
)
from quantx_infrastructure.models.agent_runtime import TTradeRolloutEvent
from quantx_infrastructure.models.enums import StrategyRunMode, StrategyRunStatus
from quantx_infrastructure.models.strategy import Strategy
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.t_legacy_drain_guard import (
  LEGACY_T_DRAIN_EVENT,
  legacy_t_drain_event_id,
)
from sqlalchemy.orm.attributes import flag_modified

from .t_assistant_legacy_inventory import freeze_legacy_t_obligation_inventory


async def begin_legacy_t_drain(
  db,
  *,
  config_id,
  run_id,
  expected_head_version,
  inventory_operation_id,
  expected_inventory_hash,
  actor_id,
  now,
):
  """Internal cutover operation; caller authorizes the maintenance operation.

  The durable fence and cancellation of never-routed intents commit together.
  Generic StrategyRun status stays live so original broker reports can converge;
  DRAINING is represented by the immutable rollout marker consumed by the runtime
  and final dispatch. Head binding and all broker/plan ownership remain intact.
  """
  if (
    not db.in_transaction()
    or not isinstance(now, datetime)
    or now.tzinfo is None
    or now.utcoffset() is None
    or type(expected_head_version) is not int
    or expected_head_version < 1
    or not isinstance(expected_inventory_hash, str)
    or len(expected_inventory_hash) != 64
    or any(
      not isinstance(value, str) or not value.strip()
      for value in (config_id, run_id, inventory_operation_id, actor_id)
    )
  ):
    raise ValueError("LEGACY_T_DRAIN_REQUEST_INVALID")
  now = now.astimezone(UTC)
  request = dict(
    config_id=config_id,
    run_id=run_id,
    expected_head_version=expected_head_version,
    inventory_operation_id=inventory_operation_id,
    inventory_hash=expected_inventory_hash,
  )
  async with db.begin_nested():
    head = await db.get(
      TTradeGlobalConfig, config_id, with_for_update=True, populate_existing=True
    )
    if head is None:
      raise ValueError("LEGACY_T_DRAIN_HEAD_REQUIRED")
    marker_id = legacy_t_drain_event_id(run_id)
    existing = await db.get(TTradeRolloutEvent, marker_id)
    if existing is not None:
      if (
        existing.event_type != LEGACY_T_DRAIN_EVENT
        or existing.account_id != head.account_id
        or existing.actor_user_id != actor_id
        or existing.next_stage != "DRAINING"
        or existing.details.get("run_id") != run_id
        or existing.details.get("request") != request
      ):
        raise ValueError("LEGACY_T_DRAIN_REPLAY_CONFLICT")
      return dict(existing.details)
    run = await db.get(
      StrategyRun, run_id, with_for_update=True, populate_existing=True
    )
    strategy = await db.get(Strategy, run.strategy_id) if run else None
    if (
      run is None
      or strategy is None
      or strategy.class_name != T_TRADE_STRATEGY_CLASS_NAME
      or run.mode != StrategyRunMode.LIVE
      or run.status not in {StrategyRunStatus.RUNNING, StrategyRunStatus.PAUSED}
      or dict(run.parameters or {}).get("account_id") != head.account_id
    ):
      raise ValueError("LEGACY_T_DRAIN_RUN_SCOPE_CONFLICT")
    inventory = await db.get(TTradeRolloutEvent, inventory_operation_id)
    if (
      inventory is None
      or inventory.details.get("manifest_hash") != expected_inventory_hash
    ):
      raise ValueError("LEGACY_T_DRAIN_REVIEWED_INVENTORY_REQUIRED")
    digest = await freeze_legacy_t_obligation_inventory(
      db,
      config_id=config_id,
      run_id=run_id,
      expected_head_version=expected_head_version,
      operation_id=inventory_operation_id,
      actor_id=actor_id,
      now=now,
    )
    if digest != expected_inventory_hash:
      raise ValueError("LEGACY_T_DRAIN_INVENTORY_CHANGED")
    manifest = inventory.details["manifest"]
    cancelled = list(manifest["unsubmitted_intent_ids_for_review"])
    for intent_id in cancelled:
      intent = await db.get(TradeIntentRecord, intent_id, with_for_update=True)
      intent.status = "CANCELLED"
      intent.notes = "LEGACY_T_ENTRY_DRAINING"
      intent.updated_at = now.replace(tzinfo=None)
      flag_modified(intent, "updated_at")
    details = {
      "run_id": run_id,
      "request": request,
      "cancelled_intent_ids": cancelled,
      "retained_client_order_ids": list(manifest["retained_client_order_ids"]),
    }
    head.state_version += 1
    head.updated_at = now.replace(tzinfo=None)
    flag_modified(head, "updated_at")
    db.add(
      TTradeRolloutEvent(
        event_id=marker_id,
        event_type=LEGACY_T_DRAIN_EVENT,
        account_id=head.account_id,
        actor_user_id=actor_id,
        previous_stage=run.status.value.upper(),
        next_stage="DRAINING",
        details=details,
        created_at=now.replace(tzinfo=None),
      )
    )
    await db.flush()
    return details
