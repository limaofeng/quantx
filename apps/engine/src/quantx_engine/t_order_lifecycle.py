"""Engine-owned expiry of public T orders, recovered from durable pending rows."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Callable
from zoneinfo import ZoneInfo

from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.clock import to_naive_utc, utcnow
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.t_order_policy import TEntryOrderPolicy, TExitOrderPolicy
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.services.intraday_volume_scanner import (
  intraday_volume_scanner,
)
from quantx_infrastructure.services.t_order_lifecycle_state import (
  t_order_lifecycle_active,
  t_order_lifecycle_pending,
)
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  TradeCommandService,
)
from sqlalchemy import or_, select

from .t_trade_coordination import t_trade_account_coordination_lock

logger = logging.getLogger(__name__)
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_WORKING = (
  "QUEUED", "PENDING", "DELIVERED", "SUBMITTED", "ACCEPTED",
  "PARTIAL_FILLED", "PARTIALLY_FILLED", "CANCEL_REQUESTED", "CANCEL_PENDING",
  "UNKNOWN", "RECONCILE_REQUIRED",
)


def entry_cutoff_reached(order, now: datetime) -> bool:
  return str(order.t_trade_role or "").upper() == "ENTRY" and (
    to_naive_utc(now).replace(tzinfo=timezone.utc).astimezone(_SHANGHAI)
    .time().replace(tzinfo=None) >= time(14, 50)
  )


def expiry_reason(order: PendingTradeOrder, now: datetime) -> str | None:
  """Wire expiry limits delivery; this deadline also cancels broker working orders."""

  role = str(order.t_trade_role or "").upper()
  if role not in {"ENTRY", "EXIT"} or str(order.status).upper() not in _WORKING:
    return None
  if order.created_at is None:
    return None
  current = to_naive_utc(now)
  created = to_naive_utc(order.created_at)
  if entry_cutoff_reached(order, now):
    return "T_ENTRY_CUTOFF_REACHED"
  policy = TEntryOrderPolicy() if role == "ENTRY" else TExitOrderPolicy()
  original = getattr(order, "t_order_original_created_at", None)
  if original is not None and (
    current - to_naive_utc(original)
  ).total_seconds() >= policy.total_ttl_seconds:
    return f"T_{role}_ORDER_EXPIRED"
  if (current - created).total_seconds() >= policy.order_ttl_seconds:
    return f"T_{role}_ORDER_EXPIRED"
  return None


async def expire_order(db, client_order_id: str, *, now: datetime) -> bool:
  """Keep the obligation until a report converges; cancel ACK is not terminal."""

  order = await db.get(PendingTradeOrder, client_order_id, with_for_update=True)
  if order is None or (reason := expiry_reason(order, now)) is None:
    return False
  broker_order_id = str(order.broker_order_id or "").strip()
  if not broker_order_id:
    # The public outbox/report consumers own local no-delivery proof. If delivery
    # might have happened, keep polling until the broker identity is recovered.
    order.status_reason = f"{reason}:ORDER_RESULT_UNKNOWN"
    return False
  await TradeCommandService(db).enqueue_cancel(
    user_id=str(order.user_id),
    account_id=str(order.account_id),
    broker_order_id=broker_order_id,
    execution_ref=ExecutionOwnerRef(order.owner_type, order.owner_id),
    environment=ExecutionEnvironment(order.environment),
    idempotency_key=f"t-order-timeout:{client_order_id}",
    commit_transaction=False,
  )
  if str(order.status).upper() not in {"UNKNOWN", "RECONCILE_REQUIRED"}:
    order.status = "CANCEL_REQUESTED"
  order.status_reason = reason
  return True


async def run_t_order_lifecycle(stopped: asyncio.Event) -> None:
  """Run under the Engine singleton lease, including after process recovery."""

  while not stopped.is_set():
    async with AsyncSessionLocal() as db:
      clients = list((await db.execute(
        select(PendingTradeOrder.client_order_id, PendingTradeOrder.account_id).where(
          PendingTradeOrder.environment == "LIVE",
          PendingTradeOrder.t_trade_role.in_(("ENTRY", "EXIT")),
          PendingTradeOrder.request_metadata["t_order_lifecycle_finished"].as_string().is_(None),
          or_(
            PendingTradeOrder.status.in_(_WORKING),
            PendingTradeOrder.t_order_original_created_at.is_not(None),
          ),
        ).order_by(PendingTradeOrder.created_at, PendingTradeOrder.client_order_id)
      )).all())
    for client, account_id in clients:
      if stopped.is_set():
        return
      try:
        async with t_trade_account_coordination_lock(str(account_id)):
          async with AsyncSessionLocal() as db:
            validate_market = await advance_order(db, str(client), now=utcnow())
            await db.flush()
            if validate_market is not None:
              validate_market()
            await db.commit()
      except Exception as exc:
        # One disconnected or quarantined owner must not starve other cancels.
        # The existing pending row remains the retry obligation.
        reason = str(exc).split(":", 1)[0][:128] if isinstance(exc, AgentUnavailableError) else type(exc).__name__
        logger.warning("T order lifecycle deferred: %s", reason)
        async with AsyncSessionLocal() as db:
          pending = await db.get(PendingTradeOrder, str(client), with_for_update=True)
          if pending is not None:
            pending.status_reason = f"T_ORDER_LIFECYCLE_DEFERRED:{reason}"
            await db.commit()
    try:
      await asyncio.wait_for(stopped.wait(), timeout=1.0)
    except asyncio.TimeoutError:
      pass


async def _stage_independent_entry_replacement(db, pending, *, now):
  from quantx_infrastructure.services.live_entry_replacement_staging import (
    stage_live_entry_replacement,
  )

  from .t_trade_runtime import t_assistant_live_supervisor

  adapter = t_assistant_live_supervisor.entry_review_adapter(db)
  try:
    inputs = await adapter.prepare_replacement(
      execution_id=pending.owner_id, intent_id=pending.intent_id,
      client_order_id=pending.client_order_id, now=to_naive_utc(now).replace(tzinfo=timezone.utc),
    )
    await stage_live_entry_replacement(
      db, client_order_id=pending.client_order_id, market_data=inputs.market_data,
      market_mark_reader=adapter.market_mark_reader, now=inputs.now,
      validate_market=inputs.validate_market,
    )
  except (TypeError, ValueError) as exc:
    raise AgentUnavailableError(str(exc)) from exc
  return inputs.validate_market


async def advance_order(db, client_order_id: str, *, now: datetime) -> Callable[[], None] | None:
  from .report_processor import finalize_t_order_lifecycle

  # Publication/source fences precede order/account locks, including cancel-only
  # work for stopped sources. Readiness is checked only when increasing risk.
  probe = await db.get(PendingTradeOrder, client_order_id)
  if probe is not None and probe.owner_type == "T_ASSISTANT_EXECUTION" and probe.t_trade_role == "ENTRY":
    from quantx_infrastructure.models.t_assistant_execution import (
      TAssistantExecutionRecord,
    )
    from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig

    source = await db.get(TAssistantExecutionRecord, probe.owner_id)
    if source is not None:
      await db.get(TTradeGlobalConfig, source.config_id, with_for_update=True, populate_existing=True)
      await db.get(TAssistantExecutionRecord, source.execution_id, with_for_update=True, populate_existing=True)
  await expire_order(db, client_order_id, now=now)
  pending = await db.get(PendingTradeOrder, client_order_id, with_for_update=True)
  if pending is None or not t_order_lifecycle_pending(pending):
    return
  successor = await db.scalar(select(PendingTradeOrder.client_order_id).where(
    PendingTradeOrder.t_order_parent_client_id == client_order_id,
  ))
  if successor:
    return
  policy = TEntryOrderPolicy() if pending.t_trade_role == "ENTRY" else TExitOrderPolicy()
  if (
    not t_order_lifecycle_active(pending, now)
    or pending.t_order_attempt >= policy.max_replace_count
    or entry_cutoff_reached(pending, now)
  ):
    await TradeCommandService(db).retire_t_order_staged_request(pending)
    await finalize_t_order_lifecycle(db, pending)
    return
  if str(pending.status).upper() in _WORKING:
    return
  if pending.owner_type == "T_ASSISTANT_EXECUTION" and pending.t_trade_role == "ENTRY":
    return await _stage_independent_entry_replacement(db, pending, now=now)
  scanner = intraday_volume_scanner
  if not scanner.hub.is_ready or not await scanner.hub.is_trading_session():
    pending.status_reason = "T_ORDER_MARKET_NOT_READY"
    return
  state = scanner.snapshot_states().get(pending.instrument_code)
  if state is None or state.updated_at is None:
    pending.status_reason = "T_ORDER_QUOTE_MISSING"
    return
  quote_at = time_utils.to_utc(state.updated_at)
  if not 0 <= (to_naive_utc(now) - to_naive_utc(quote_at)).total_seconds() <= 2:
    pending.status_reason = "T_ORDER_QUOTE_STALE"
    return
  prices = state.ask_price if pending.t_trade_role == "ENTRY" else state.bid_price
  if not prices or prices[0] <= 0 or min(state.price_tick, state.up_stop_price, state.down_stop_price) <= 0:
    pending.status_reason = "T_ORDER_MARKET_BOUNDARY_MISSING"
    return
  market = MarketDataSnapshot(
    instrument_code=pending.instrument_code, timestamp=quote_at,
    price=state.current_price, price_tick=state.price_tick,
    limit_up=state.up_stop_price, limit_down=state.down_stop_price,
    suspended=state.stock_status != 0,
    bid_price=state.bid_price, ask_price=state.ask_price,
    bid_vol=state.bid_vol, ask_vol=state.ask_vol,
    source="QMT_WHOLE_QUOTE",
  )
  await TradeCommandService(db).replace_t_order(
    client_order_id=client_order_id, quote_at=quote_at,
    reference_price=Decimal(str(prices[0])), price_tick=Decimal(str(state.price_tick)),
    limit_up=Decimal(str(state.up_stop_price)), limit_down=Decimal(str(state.down_stop_price)),
    market_data=market,
  )
