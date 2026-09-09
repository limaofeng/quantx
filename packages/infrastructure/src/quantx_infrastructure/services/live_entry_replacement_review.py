"""Read-only manual T replacement preflight, not a dispatch authorization.

The caller owns the transaction. Fresh portfolio/risk review and account admission
must still follow this proof; initial-entry candidate gates are not bypassed here.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from quantx_domain.trading.t_order_policy import TEntryOrderPolicy
from sqlalchemy import select

from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
  TTradeBatch,
)
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.t_allocation_serialization import allocation_time
from quantx_infrastructure.services.t_live_entry_authorization import (
  _authorize_live_entry,
)


@dataclass(frozen=True)
class LiveEntryReplacementPreflight:
  parent_client_order_id: str
  intent_id: str
  execution_id: str
  user_id: str
  filled_volume: int
  remaining_volume: int
  limit_price: Decimal
  original_created_at: datetime
  expires_at: datetime
  allocation_version: int


async def review_live_entry_replacement(
  db,
  *,
  client_order_id: str,
  now: datetime,
  reference_price: Decimal,
  price_tick: Decimal,
  limit_up: Decimal,
  limit_down: Decimal,
) -> LiveEntryReplacementPreflight:
  if not db.in_transaction():
    raise ValueError("T_ENTRY_CALLER_TRANSACTION_REQUIRED")
  if now.tzinfo is None or now.utcoffset() is None:
    raise ValueError("T_ENTRY_AWARE_TIME_REQUIRED")
  probe = await db.get(PendingTradeOrder, client_order_id)
  if probe is None or probe.owner_type != "T_ASSISTANT_EXECUTION":
    raise ValueError("T_ENTRY_REPLACEMENT_SOURCE_INVALID")
  source = await db.get(TAssistantExecutionRecord, probe.owner_id)
  if source is None:
    raise ValueError("T_ENTRY_SOURCE_NOT_READY")
  # The standalone preflight follows head -> source -> intent -> pending order.
  # Coordinators integrating it must acquire these before account/order locks.
  await db.get(
    TTradeGlobalConfig, source.config_id, with_for_update=True, populate_existing=True
  )
  await db.get(
    TAssistantExecutionRecord,
    source.execution_id,
    with_for_update=True,
    populate_existing=True,
  )
  intent = await db.get(
    TradeIntentRecord, probe.intent_id, with_for_update=True, populate_existing=True
  )
  pending = await db.get(
    PendingTradeOrder, client_order_id, with_for_update=True, populate_existing=True
  )
  if (
    intent is None
    or pending is None
    or pending.owner_id != source.execution_id
    or pending.owner_type != "T_ASSISTANT_EXECUTION"
    or pending.environment != "LIVE"
    or pending.side != "BUY"
    or pending.order_type != "FIX_PRICE"
    or pending.t_trade_role != "ENTRY"
    or pending.strategy_run_id
    or pending.intent_id != intent.id
    or any(
      getattr(pending, key) != getattr(intent, key)
      for key in (
        "account_id",
        "instrument_code",
        "owner_type",
        "owner_id",
        "environment",
        "bucket",
      )
    )
    or pending.t_order_attempt != 0
    or pending.t_order_parent_client_id
    or (pending.request_metadata or {}).get("t_order_lifecycle_finished")
    or not pending.batch_id
    or pending.batch_id != (intent.intent_metadata or {}).get("t_batch_id")
  ):
    raise ValueError("T_ENTRY_REPLACEMENT_SCOPE_INVALID")
  attempts = list(
    (
      await db.scalars(
        select(PendingTradeOrder.client_order_id).where(
          PendingTradeOrder.intent_id == intent.id,
        )
      )
    ).all()
  )
  correlations = list(
    (
      await db.scalars(
        select(OrderCorrelation.client_order_id).where(
          OrderCorrelation.intent_id == intent.id,
        )
      )
    ).all()
  )
  if attempts != [pending.client_order_id] or correlations != attempts:
    raise ValueError("T_ENTRY_REPLACEMENT_CHAIN_CONFLICT")
  original = pending.t_order_original_created_at
  if (
    original is None
    or pending.created_at is None
    or allocation_time(pending.created_at) > now
    or allocation_time(original) > now
  ):
    raise ValueError("T_ENTRY_REPLACEMENT_CLOCK_INVALID")
  # SQL created_at may be the transaction start; the typed lifecycle clock
  # is independent and must also have reached the first attempt deadline.
  if (
    now - allocation_time(original)
  ).total_seconds() < TEntryOrderPolicy().order_ttl_seconds:
    raise ValueError("T_ENTRY_ORDER_ACTIVE")
  batch = await db.get(TTradeBatch, pending.batch_id, populate_existing=True)
  if (
    batch is None
    or batch.entry_intent_id != intent.id
    or batch.strategy_run_id
    or batch.account_id != intent.account_id
    or batch.instrument_code != intent.instrument_code
    or batch.environment != "LIVE"
    or batch.source_execution_environment != "LIVE"
    or batch.source_execution_owner_type != intent.owner_type
    or batch.source_execution_owner_id != intent.owner_id
  ):
    raise ValueError("T_ENTRY_REPLACEMENT_BATCH_CONFLICT")
  # Reuse the durable broker-order, trade and APPLIED-event proof, not an ack.
  from quantx_infrastructure.services.trade_command_service import TradeCommandService

  result = await TradeCommandService(db).evaluate_t_order_replacement(
    client_order_id=client_order_id,
    now=now,
    reference_price=reference_price,
    price_tick=price_tick,
    limit_up=limit_up,
    limit_down=limit_down,
  )
  if not result.allowed:
    raise ValueError(result.reason_code)
  filled = int(pending.volume) - result.remaining_volume
  if (
    filled != int(intent.executed_volume or 0)
    or filled != int(batch.entry_filled_volume or 0)
    or filled < 0
    or result.remaining_volume <= 0
  ):
    raise ValueError("T_ENTRY_REPLACEMENT_FILL_PROJECTION_CONFLICT")
  allowed_statuses = {"EXECUTION_PENDING", "PARTIAL_FILLED"}
  if intent.status == "EXECUTION_READY":
    from quantx_infrastructure.services.live_entry_replacement_staging import (
      validate_staged_live_entry_replacement,
    )

    staged = (intent.intent_metadata or {}).get("risk_increase_order_request") or {}
    await validate_staged_live_entry_replacement(
      db,
      intent=intent,
      volume=staged.get("volume"),
      limit_price=Decimal(str(staged.get("limit_price"))),
      now=now,
      require_fresh=False,
    )
    if staged.get("t_order_parent_client_id") != client_order_id:
      raise ValueError("T_ENTRY_REPLACEMENT_SCOPE_INVALID")
    allowed_statuses.add("EXECUTION_READY")
  actor = await _authorize_live_entry(
    db,
    intent=intent,
    account_id=pending.account_id,
    instrument_code=pending.instrument_code,
    volume=result.remaining_volume,
    limit_price=result.limit_price,
    now=now,
    allowed_statuses=allowed_statuses,
  )
  if actor != pending.user_id:
    raise ValueError("T_ENTRY_REPLACEMENT_ACTOR_CONFLICT")
  original = allocation_time(original)
  return LiveEntryReplacementPreflight(
    parent_client_order_id=client_order_id,
    intent_id=intent.id,
    execution_id=intent.owner_id,
    user_id=actor,
    filled_volume=filled,
    remaining_volume=result.remaining_volume,
    limit_price=result.limit_price,
    original_created_at=original,
    expires_at=original + timedelta(seconds=TEntryOrderPolicy().total_ttl_seconds),
    allocation_version=intent.allocation_version,
  )
