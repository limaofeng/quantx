"""Idempotent convergence from the durable QMT Agent report inbox."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import md5, sha256
from typing import Any, Awaitable, Callable, Optional

from quantx_contracts import (
  TERMINAL_ORDER_STATUSES,
  can_transition_order_status,
  normalize_order_status,
)
from quantx_domain.brokers.base import (
  OrderRequest,
  OrderResponse,
  OrderStatus,
  OrderType,
  PriceType,
  TradeRecord,
)
from quantx_domain.clock import to_naive_utc, utcnow
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.redis_pubsub import (
  AGENT_REPORT_WAKE_CHANNEL,
  RedisChannelSubscription,
  redis_pubsub,
)
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.account import Account
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AccountExecutionControlEvent,
  AgentDevice,
  AgentReportInbox,
  OperationalAlert,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyOrderCorrelation,
  StrategyRuntimeEvent,
  TTradeBatch,
)
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.account_repository import AccountRepository
from quantx_infrastructure.services.agent_handover import converge_ready_agent
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.entry_plan_authorization_service import (
  EntryPlanAuthorizationService,
)
from quantx_infrastructure.services.operational_alert_service import (
  OperationalAlertService,
)
from quantx_infrastructure.services.order_service import OrderService
from quantx_infrastructure.services.position_service import PositionService
from quantx_infrastructure.services.runtime_subscription_bridge import (
  TRADING_EVENT_CHANNEL,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from quantx_infrastructure.services.trade_service import TradeService
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import aliased

from .t_trade_coordination import t_trade_account_coordination_lock

_MAX_TRADE_RUNTIME_AUTHORITY_SCAN = 4096
_MAX_SNAPSHOT_ACCOUNT_SCOPE = 4096

logger = logging.getLogger(__name__)

_DATABASE_CONTENTION_RETRY_SECONDS = 0.25
_DATABASE_CONTENTION_MAX_RETRY_SECONDS = 2.0

# Production owns a single Engine report consumer, while this lock also makes
# direct/test drain calls obey the same invariant. Recovery of PROCESSING rows
# is performed only while this lock is held, so it cannot reclaim an event that
# this process is still applying.
_runtime_event_drain_lock = asyncio.Lock()

_ORDER_STATUS_NAMES = {
  48: "PENDING",
  49: "SUBMITTED",
  50: "SUBMITTED",
  51: "SUBMITTED",
  52: "PARTIAL_FILLED",
  53: "CANCELLED",
  54: "CANCELLED",
  55: "PARTIAL_FILLED",
  56: "FILLED",
  57: "REJECTED",
  255: "PENDING",
}

_SNAPSHOT_PROMOTABLE_HEARTBEAT_STATUSES = {"RECONCILING"}
_AUTOMATIC_RECONCILIATION_KINDS = {
  "MISSING_WORKING_ORDER",
  "PROTOCOL_1_1_REQUIRED",
  "SNAPSHOT_COMPLETENESS_REQUIRED",
  "SNAPSHOT_IDENTITY_INVALID",
  "SNAPSHOT_PROTOCOL_INVALID",
  "SNAPSHOT_SECTION_INCOMPLETE",
  "UNKNOWN_BROKER_ORDER",
  "UNKNOWN_BROKER_TRADE",
}
_SPECIAL_RUNTIME_ORDER_STATUSES = {
  "RECONCILE_REQUIRED",
  "RECONCILED_ZERO_FILL",
}
_ZERO_FILL_RECONCILABLE_ORDER_STATUSES = {"CANCELLED", "EXPIRED"}


class RetryableReportError(RuntimeError):
  pass


def _snapshot_can_promote_heartbeat(status: Any) -> bool:
  """Only reconciliation snapshots may promote an Agent heartbeat.

  A delayed snapshot must never mask a newer runtime failure such as a lost
  XTTrading or XTData connection.
  """
  return str(status or "").strip().upper() in _SNAPSHOT_PROMOTABLE_HEARTBEAT_STATUSES


def _body(payload: dict[str, Any], key: str) -> dict[str, Any]:
  nested = payload.get(key)
  return dict(nested) if isinstance(nested, dict) else dict(payload)


def _report_account_ids(payload: dict[str, Any]) -> set[str]:
  """Return every funding account covered by one Agent report payload."""
  account_ids: set[str] = set()

  def add(value: Any) -> None:
    normalized = str(value or "").strip()
    if normalized:
      if (
        normalized not in account_ids
        and len(account_ids) >= _MAX_SNAPSHOT_ACCOUNT_SCOPE
      ):
        raise RetryableReportError(
          f"Agent snapshot account scope exceeds limit: {_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
        )
      account_ids.add(normalized)

  add(payload.get("account_id"))
  for nested_name in ("order", "execution"):
    nested = payload.get(nested_name)
    if isinstance(nested, dict):
      add(nested.get("account_id"))
  for collection_name in (
    "accounts",
    "orders",
    "trades",
    "order_errors",
    "cancel_errors",
    "position_deltas",
    "positions",
  ):
    values = payload.get(collection_name) or []
    try:
      collection_count = len(values)
    except TypeError as exc:
      raise RetryableReportError(
        f"Agent snapshot {collection_name} scope不可有界复制"
      ) from exc
    if collection_count > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
      raise RetryableReportError(
        f"Agent snapshot {collection_name} scope exceeds limit: "
        f"{_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
      )
    for item in values:
      if isinstance(item, dict):
        add(item.get("account_id"))
  positions_by_account = payload.get("positions_by_account")
  if isinstance(positions_by_account, dict):
    if len(positions_by_account) > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
      raise RetryableReportError(
        "Agent snapshot positions account scope exceeds limit: "
        f"{_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
      )
    for account_id in positions_by_account:
      add(account_id)
  section_completeness = payload.get("section_completeness_by_account")
  if isinstance(section_completeness, dict):
    if len(section_completeness) > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
      raise RetryableReportError(
        "Agent snapshot section account scope exceeds limit: "
        f"{_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
      )
    for account_id in section_completeness:
      add(account_id)
  unavailable_accounts = payload.get("unavailable_accounts") or []
  try:
    unavailable_count = len(unavailable_accounts)
  except TypeError as exc:
    raise RetryableReportError(
      "Agent snapshot unavailable account scope不可有界复制"
    ) from exc
  if unavailable_count > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
    raise RetryableReportError(
      "Agent snapshot unavailable account scope exceeds limit: "
      f"{_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
    )
  for account_id in unavailable_accounts:
    add(account_id)
  return account_ids


async def _invalidate_t_trade_entry_authority_for_account(
  account_id: str,
  *,
  reason: str,
) -> None:
  """Clear every in-memory V3 entry authority for one exact account.

  Report processing is an Engine concern, so it may reach the process-local
  StrategyManager.  Keep the lookup bounded to the executor's in-memory runs;
  durable state is not a substitute for the executor invalidation boundary.
  The caller owns the account coordination lock.  The executor boundary then
  acquires the per-runtime approval lock, preserving account -> approval order.
  """

  normalized_account = str(account_id or "").strip()
  if not normalized_account:
    raise ValueError("account_id is required for T-trade authority invalidation")

  # Lazy import avoids loading the Engine singleton during report module
  # initialization and keeps the authority boundary local to Engine.
  from .strategy_manager import strategy_manager

  executor = getattr(strategy_manager, "executor", None)
  runs = getattr(executor, "runs", None)
  if not hasattr(runs, "items"):
    raise RetryableReportError(
      "T-trade executor runtime registry unavailable; authority not cleared"
    )

  try:
    run_count = len(runs)
  except Exception as exc:
    raise RetryableReportError(
      "无法确定 T-trade runtime 数量，拒绝跳过账户 authority 清理"
    ) from exc
  if run_count > _MAX_TRADE_RUNTIME_AUTHORITY_SCAN:
    raise RetryableReportError(
      "T-trade runtime 数量超过 authority 清理上限: "
      f"{run_count}>{_MAX_TRADE_RUNTIME_AUTHORITY_SCAN}"
    )
  try:
    runtime_items = list(runs.items())
  except Exception as exc:
    raise RetryableReportError(
      "无法复制 T-trade runtime，拒绝跳过账户 authority 清理"
    ) from exc
  if len(runtime_items) > _MAX_TRADE_RUNTIME_AUTHORITY_SCAN:
    raise RetryableReportError(
      "T-trade runtime 复制结果超过 authority 清理上限: "
      f"{len(runtime_items)}>{_MAX_TRADE_RUNTIME_AUTHORITY_SCAN}"
    )

  uses_t_trade = getattr(executor, "_uses_t_trade_opportunity_runtime", None)
  invalidate = getattr(executor, "invalidate_t_trade_entry_authority", None)
  failures: list[str] = []
  for raw_run_id, runtime in runtime_items:
    context = getattr(runtime, "context", None)
    parameters = dict(getattr(context, "parameters", {}) or {})
    runtime_account = str(parameters.get("account_id") or "").strip()
    if runtime_account != normalized_account:
      continue

    try:
      if callable(uses_t_trade):
        is_t_trade = bool(uses_t_trade(runtime))
      else:
        strategy = getattr(runtime, "strategy", None)
        strategy_class = getattr(runtime, "strategy_class", None)
        is_t_trade = bool(
          getattr(strategy, "USES_T_TRADE_OPPORTUNITY_PROFILE", False)
          or getattr(strategy_class, "USES_T_TRADE_OPPORTUNITY_PROFILE", False)
          or parameters.get("t_trade_opportunity_v3")
        )
    except Exception as exc:
      failures.append(f"{raw_run_id}: runtime classification failed: {exc}")
      continue
    if not is_t_trade:
      continue

    run_id = str(raw_run_id or getattr(runtime, "run_id", "")).strip()
    if not run_id or not callable(invalidate):
      failures.append(f"{run_id or raw_run_id}: invalidation boundary unavailable")
      continue
    try:
      invalidated = await invalidate(
        run_id,
        account_id=normalized_account,
        reason=reason,
      )
    except Exception as exc:
      failures.append(f"{run_id}: {exc}")
      continue
    if not bool(invalidated):
      failures.append(f"{run_id}: invalidation returned false")

  if failures:
    raise RetryableReportError(
      "T-trade entry authority invalidation failed: " + "; ".join(failures[:20])
    )


async def _run_account_snapshot_mutation(
  account_id: str,
  mutation: Callable[[], Awaitable[Any]],
  *,
  reason: str,
) -> Any:
  """Linearize one durable snapshot mutation and authority invalidation."""

  normalized_account = str(account_id or "").strip()
  if not normalized_account:
    raise ValueError("account_id is required for snapshot mutation")

  async with t_trade_account_coordination_lock(normalized_account):
    mutation_error: Optional[Exception] = None
    result: Any = None
    try:
      result = await mutation()
    except Exception as exc:
      mutation_error = exc

    mutation_marker_error: Optional[Exception] = None
    if mutation_error is not None:
      try:
        marker = getattr(PositionService(), "mark_snapshot_failure", None)
        if not callable(marker):
          raise RuntimeError("PositionService.mark_snapshot_failure 不可用")
        await marker(
          normalized_account,
          f"{reason}:APPLY_FAILED",
        )
      except Exception as exc:
        mutation_marker_error = exc

    try:
      await _invalidate_t_trade_entry_authority_for_account(
        normalized_account,
        reason=reason,
      )
    except Exception as authority_error:
      detail = f"account={normalized_account}"
      if mutation_error is not None:
        detail += f", mutation={mutation_error!s}"
      raise RetryableReportError(
        "T-trade snapshot authority invalidation failed: " + detail
      ) from authority_error

    if mutation_marker_error is not None:
      raise RetryableReportError(
        "T-trade snapshot mutation failure marker failed: "
        f"account={normalized_account}, error={mutation_marker_error}"
      ) from mutation_marker_error
    if mutation_error is not None:
      raise mutation_error
    return result


_REQUIRED_SNAPSHOT_SECTIONS = ("account", "positions", "orders", "trades")


def _complete_snapshot_account_ids(
  payload: dict[str, Any],
) -> Optional[set[str]]:
  """Validate the unique protocol-1.1 full-snapshot completeness contract."""

  unavailable_accounts = payload.get("unavailable_accounts")
  section_completeness = payload.get("section_completeness_by_account")
  accounts = payload.get("accounts")
  positions_by_account = payload.get("positions_by_account")
  if (
    not isinstance(unavailable_accounts, list)
    or unavailable_accounts
    or not isinstance(section_completeness, dict)
    or not isinstance(accounts, list)
    or not isinstance(positions_by_account, dict)
  ):
    return None
  scoped_values = (
    ("accounts", accounts),
    ("positions_by_account", positions_by_account),
    ("section_completeness_by_account", section_completeness),
    ("unavailable_accounts", unavailable_accounts),
  )
  for section_name, values in scoped_values:
    if len(values) > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
      raise RetryableReportError(
        f"Agent snapshot {section_name} scope exceeds limit: "
        f"{_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
      )
  account_record_ids = {
    str(item.get("account_id") or "").strip()
    for item in accounts
    if isinstance(item, dict) and str(item.get("account_id") or "").strip()
  }
  position_account_ids = {
    str(account_id).strip()
    for account_id in positions_by_account
    if str(account_id).strip()
  }
  section_account_ids = {
    str(account_id).strip()
    for account_id in section_completeness
    if str(account_id).strip()
  }
  covered_accounts = _report_account_ids(payload)
  if (
    not covered_accounts
    or account_record_ids != covered_accounts
    or position_account_ids != covered_accounts
    or section_account_ids != covered_accounts
  ):
    return None
  for account_id in covered_accounts:
    sections = section_completeness.get(account_id)
    if not isinstance(sections, dict) or not all(
      sections.get(section) is True for section in _REQUIRED_SNAPSHOT_SECTIONS
    ):
      return None
  return covered_accounts


def _snapshot_section_is_complete(
  payload: dict[str, Any],
  account_id: str,
  section: str,
) -> bool:
  values = payload.get("section_completeness_by_account")
  if not isinstance(values, dict):
    return False
  account_values = values.get(account_id)
  return bool(isinstance(account_values, dict) and account_values.get(section) is True)


def _authoritative_full_snapshot_account_ids(
  payload: dict[str, Any],
  *,
  protocol_version: str,
) -> Optional[set[str]]:
  """Return covered accounts when a payload can promote a full snapshot."""

  if payload.get("is_complete") is not True or str(protocol_version) != "1.1":
    return None
  snapshot_id = str(payload.get("snapshot_id") or "").strip()
  snapshot_hash = str(payload.get("snapshot_hash") or "")
  if not snapshot_id or len(snapshot_hash) != 64:
    return None
  hash_input = {key: value for key, value in payload.items() if key != "snapshot_hash"}
  expected_hash = sha256(
    json.dumps(
      hash_input,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  if expected_hash != snapshot_hash:
    return None
  return _complete_snapshot_account_ids(payload)


def _was_automatic_reconciliation_pause(reason: Any) -> bool:
  """Distinguish an Engine reconciliation pause from an operator pause."""
  if not isinstance(reason, str) or not reason.strip():
    return False
  try:
    items = json.loads(reason)
  except (TypeError, ValueError):
    return False
  return bool(
    isinstance(items, list)
    and items
    and all(
      isinstance(item, dict)
      and str(item.get("kind") or "") in _AUTOMATIC_RECONCILIATION_KINDS
      for item in items
    )
  )


async def _update_pending(
  client_order_id: Optional[str],
  *,
  status: str,
  broker_order_id: Optional[str] = None,
  reason: Optional[str] = None,
  source_sequence: int = 0,
  source_event_at: Optional[datetime] = None,
) -> None:
  if not client_order_id:
    return
  async with AsyncSessionLocal() as db:
    pending = await db.get(PendingTradeOrder, client_order_id)
    if pending is None:
      return
    cancel_rejected = str(status or "").upper() == "CANCEL_REJECTED"
    cancel_requested = str(pending.status or "").upper() == "CANCEL_REQUESTED"
    proposed_status = (
      str(pending.status or "PENDING")
      if cancel_rejected
      else _normalized_order_status(status)
    )
    proposed_terminal = proposed_status in TERMINAL_ORDER_STATUSES
    stored_sequence = int(pending.last_source_sequence or 0)
    sequence = max(0, int(source_sequence or 0))
    stale_sequence = bool(sequence and stored_sequence and sequence < stored_sequence)
    transition_allowed = not stale_sequence and (
      (cancel_requested and not proposed_terminal)
      or can_transition_order_status(pending.status, proposed_status)
    )
    if transition_allowed:
      pending.status = (
        "CANCEL_REQUESTED"
        if cancel_requested and not proposed_terminal
        else proposed_status[:24]
      )
      if sequence:
        pending.last_source_sequence = sequence
      if source_event_at is not None:
        pending.last_source_event_at = to_naive_utc(source_event_at)
    pending.broker_order_id = broker_order_id or pending.broker_order_id
    if stale_sequence:
      pending.status_reason = "ignored stale broker report"
    elif cancel_rejected:
      pending.status_reason = (reason or "cancel rejected")[:256]
    elif cancel_requested and not proposed_terminal:
      pending.status_reason = str(pending.status_reason or "cancellation requested")[
        :256
      ]
    elif not transition_allowed:
      pending.status_reason = (f"ignored non-monotonic status {proposed_status}")[:256]
    else:
      pending.status_reason = (reason or "")[:256] or None
    correlation = (
      await db.execute(
        select(StrategyOrderCorrelation).where(
          StrategyOrderCorrelation.client_order_id == client_order_id
        )
      )
    ).scalar_one_or_none()
    if correlation is not None and broker_order_id:
      correlation.broker_order_id = broker_order_id
    if cancel_requested and not proposed_terminal and broker_order_id:
      await TradeCommandService(db).enqueue_cancel(
        user_id=str(pending.user_id),
        account_id=str(pending.account_id),
        broker_order_id=str(broker_order_id),
        idempotency_key=(f"entry-plan-cancel:{client_order_id}:{broker_order_id}"),
        execution_mode=str(pending.execution_mode or "paper").lower(),
        commit_transaction=False,
      )
    await db.commit()


async def _update_pending_by_broker(
  broker_order_id: Any,
  *,
  status: str,
  reason: str,
  source_sequence: int = 0,
  source_event_at: Optional[datetime] = None,
) -> None:
  if broker_order_id is None:
    return
  async with AsyncSessionLocal() as db:
    pending = (
      await db.execute(
        select(PendingTradeOrder).where(
          PendingTradeOrder.broker_order_id == str(broker_order_id)
        )
      )
    ).scalar_one_or_none()
    if pending is None:
      return
    proposed_status = _normalized_order_status(status)
    cancel_requested = str(pending.status or "").upper() == "CANCEL_REQUESTED"
    proposed_terminal = proposed_status in TERMINAL_ORDER_STATUSES
    sequence = max(0, int(source_sequence or 0))
    stored_sequence = int(pending.last_source_sequence or 0)
    if (not sequence or not stored_sequence or sequence >= stored_sequence) and (
      (cancel_requested and not proposed_terminal)
      or can_transition_order_status(pending.status, proposed_status)
    ):
      pending.status = (
        "CANCEL_REQUESTED"
        if cancel_requested and not proposed_terminal
        else proposed_status[:24]
      )
      if sequence:
        pending.last_source_sequence = sequence
      if source_event_at is not None:
        pending.last_source_event_at = to_naive_utc(source_event_at)
    pending.status_reason = (
      str(pending.status_reason or "cancellation requested")[:256]
      if cancel_requested and not proposed_terminal
      else reason[:256] or None
    )
    if cancel_requested and not proposed_terminal:
      await TradeCommandService(db).enqueue_cancel(
        user_id=str(pending.user_id),
        account_id=str(pending.account_id),
        broker_order_id=str(broker_order_id),
        idempotency_key=(
          f"entry-plan-cancel:{pending.client_order_id}:{broker_order_id}"
        ),
        execution_mode=str(pending.execution_mode or "paper").lower(),
        commit_transaction=False,
      )
    await db.commit()


async def _process_order_report(payload: dict[str, Any]) -> None:
  order = _body(payload, "order")
  broker_order_id = order.get("order_id") or order.get("broker_order_id")
  if broker_order_id is None:
    raise ValueError("order_report 缺少 broker order id")
  order["order_id"] = int(broker_order_id)
  order.setdefault("order_sysid", str(broker_order_id)[-10:])
  order.setdefault("order_time", int(time_utils.now().timestamp()))
  order.setdefault("traded_volume", 0)
  order.setdefault("traded_price", 0)
  order.setdefault("order_status", 49)
  order.setdefault("price_type", 50)
  await OrderService(str(order.get("account_id", ""))).upsert_report(order)
  status = _normalized_order_status(
    order.get("effective_order_status")
    or order.get("status")
    or order.get("order_status")
    or "SUBMITTED"
  )
  await _update_pending(
    str(payload.get("client_order_id") or "") or None,
    status=status,
    broker_order_id=str(broker_order_id),
    reason=str(order.get("effective_status_reason") or order.get("status_msg") or ""),
    source_sequence=int(payload.get("source_sequence") or 0),
    source_event_at=_parse_report_time(payload.get("source_event_at")),
  )
  await AutoExitPlanService().apply_order_event_for_report(
    client_order_id=str(payload.get("client_order_id") or ""),
    broker_order_id=str(broker_order_id),
    status=status,
    source_sequence=int(payload.get("source_sequence") or 0),
  )


async def _process_execution_report(payload: dict[str, Any]) -> None:
  trade = _body(payload, "execution")
  broker_order_id = trade.get("order_id") or trade.get("broker_order_id")
  if broker_order_id is None:
    raise ValueError("execution_report 缺少 broker order id")
  order = await OrderService(str(trade.get("account_id", ""))).get_order_by_id(
    int(broker_order_id)
  )
  if order is None:
    raise RetryableReportError("对应 order_report 尚未收敛")
  trade["order_id"] = int(broker_order_id)
  trade.setdefault("traded_id", trade.get("execution_id"))
  trade.setdefault("order_sysid", order.sysid)
  trade.setdefault("order_type", int(order.type))
  trade.setdefault("traded_time", int(time_utils.now().timestamp()))
  trade.setdefault(
    "traded_amount",
    float(trade.get("traded_price") or 0) * int(trade.get("traded_volume") or 0),
  )
  if not trade.get("traded_id"):
    raise ValueError("execution_report 缺少 execution id")
  await TradeService(str(trade.get("account_id", ""))).upsert_report(trade)
  await _consume_exact_auto_entry_fill(payload, trade)
  await _update_pending(
    str(payload.get("client_order_id") or "") or None,
    status=str(payload.get("order_status") or "PARTIAL_FILLED"),
    broker_order_id=str(broker_order_id),
    source_sequence=int(payload.get("source_sequence") or 0),
    source_event_at=_parse_report_time(payload.get("source_event_at")),
  )
  await AutoExitPlanService().apply_order_event_for_report(
    client_order_id=str(payload.get("client_order_id") or ""),
    broker_order_id=str(broker_order_id),
    status=str(payload.get("order_status") or "PARTIAL_FILLED"),
    source_sequence=int(payload.get("source_sequence") or 0),
  )
  await AutoExitPlanService().apply_execution_for_report(
    execution_id=str(trade.get("execution_id") or trade.get("traded_id") or ""),
    client_order_id=str(payload.get("client_order_id") or ""),
    broker_order_id=str(broker_order_id),
    volume=int(trade.get("traded_volume") or 0),
    price=float(trade.get("traded_price") or 0.0),
  )


async def _consume_exact_auto_entry_fill(
  payload: dict[str, Any],
  trade: dict[str, Any],
) -> None:
  """Debit an exact managed-entry grant only for a durable LIVE BUY trade.

  Command acknowledgements and order reports never call this function.  The
  QMT execution id is the idempotency key, so inbox retries and full-snapshot
  replay cannot consume authorization twice.
  """

  client_order_id = str(
    payload.get("client_order_id") or trade.get("client_order_id") or ""
  )
  broker_order_id = str(trade.get("order_id") or trade.get("broker_order_id") or "")
  async with AsyncSessionLocal() as db:
    pending = (
      await db.get(PendingTradeOrder, client_order_id) if client_order_id else None
    )
    if pending is None and broker_order_id:
      pending = (
        await db.execute(
          select(PendingTradeOrder).where(
            PendingTradeOrder.broker_order_id == broker_order_id
          )
        )
      ).scalar_one_or_none()
    if (
      pending is None
      or str(pending.execution_mode or "").lower() != "live"
      or str(pending.side or "").upper() != "BUY"
    ):
      return
    reported_account_id = str(trade.get("account_id") or "")
    reported_instrument = str(
      trade.get("stock_code") or trade.get("instrument_code") or ""
    )
    if (reported_account_id and reported_account_id != str(pending.account_id)) or (
      reported_instrument and reported_instrument != str(pending.instrument_code)
    ):
      raise ValueError("LIVE 自动买入成交账户或标的与权威命令不匹配")
    metadata = dict(pending.request_metadata or {})
    plan_id = str(metadata.get("entry_plan_id") or "")
    run_id = str(pending.strategy_run_id or "")
    grant_id = str(metadata.get("auto_entry_authorization_grant_id") or "")
    if not plan_id or not grant_id:
      return
    if not run_id or not bool(metadata.get("exact_auto_entry_authorized")):
      raise ValueError("LIVE 自动买入成交缺少已验证的计划授权关联")
    intent = await db.get(TradeIntentRecord, str(pending.intent_id or ""))
    intent_metadata = dict(intent.intent_metadata or {}) if intent is not None else {}
    if (
      intent is None
      or str(intent.strategy_run_id or "") != run_id
      or str(intent.direction or "").upper() != "BUY"
      or str(intent_metadata.get("entry_plan_id") or plan_id) != plan_id
      or str(intent_metadata.get("execution_mode") or "").upper() != "AUTO"
      or str(intent_metadata.get("auto_entry_authorization_grant_id") or "") != grant_id
    ):
      raise ValueError("LIVE 自动买入成交与权威意图授权不匹配")
    execution_id = str(
      trade.get("execution_id") or trade.get("traded_id") or ""
    ).strip()
    price = Decimal(str(trade.get("traded_price") or trade.get("price") or 0))
    volume = int(trade.get("traded_volume") or trade.get("volume") or 0)
    if not execution_id or not price.is_finite() or price <= 0 or volume <= 0:
      raise ValueError("LIVE 自动买入成交事实无效")
    filled_at = _parse_report_time(trade.get("traded_time") or trade.get("trade_time"))
    await EntryPlanAuthorizationService(db).consume_real_fill(
      grant_id=grant_id,
      trade_business_key=f"qmt-entry:{pending.account_id}:{execution_id}"[:160],
      filled_amount_cny=price * volume,
      filled_volume=volume,
      fill_price=price,
      filled_at=filled_at,
    )


async def _upsert_account(value: dict[str, Any]) -> None:
  account_id = str(value.get("account_id") or "")
  if not account_id:
    raise ValueError("账户快照缺少 account_id")
  raw_type = value.get("account_type", 2)
  account_type = (
    raw_type
    if isinstance(raw_type, AccountType)
    else AccountType.from_int(int(raw_type))
  )
  if account_type is None:
    account_type = AccountType.STOCK
  account = Account(
    id=md5(f"{account_id}:{account_type.value}".encode("utf-8")).hexdigest(),
    account_id=account_id,
    account_type=account_type,
    total_asset=value.get("total_asset", 0),
    cash=value.get("cash", 0),
    market_value=value.get("market_value", 0),
    frozen_cash=value.get("frozen_cash", 0),
  )
  async with AsyncSessionLocal() as db:
    await AccountRepository(db).save(account)


async def _fail_closed_incomplete_snapshot(
  device_id: str,
  payload: dict[str, Any],
  *,
  reported_at: datetime,
  failure_kind: str,
  failure_reason: str,
  account_ids_override: Optional[set[str]] = None,
) -> None:
  """Invalidate live authority after an attempted incomplete full snapshot."""

  scope_validation_failed = False
  if account_ids_override is not None:
    try:
      override_count = len(account_ids_override)
    except TypeError as exc:
      raise RetryableReportError("完整账户快照 override 账户范围不可有界复制") from exc
    if override_count > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
      raise RetryableReportError(
        f"完整账户快照 override 账户范围超过限制: {_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
      )
    account_ids = {
      str(value).strip() for value in account_ids_override if str(value).strip()
    }
  else:
    try:
      account_ids = _report_account_ids(payload)
    except RetryableReportError:
      # A malformed full report can fail scope discovery before the ordinary
      # device-scope fallback below. Preserve fail-closed behavior by using
      # the authenticated scope rather than abandoning the stale marker.
      account_ids = set()
      scope_validation_failed = True
  if not account_ids:
    # A malformed report may omit every account field.  The authenticated
    # device scope is the only safe fallback for the personal single-account
    # deployment; silently returning would leave the previous complete
    # snapshot and in-memory entry authority usable.
    async with AsyncSessionLocal() as lookup_db:
      try:
        device = await lookup_db.get(AgentDevice, device_id)
      except Exception as exc:
        raise RetryableReportError(
          "无法读取 Agent 授权账户，拒绝忽略无账户快照失败"
        ) from exc
      if device is not None and device.revoked_at is None:
        authorized_values = device.authorized_account_ids or []
        if isinstance(authorized_values, str):
          authorized_values = [authorized_values]
        try:
          authorized_count = len(authorized_values)
        except TypeError as exc:
          raise RetryableReportError("Agent 授权账户范围不可有界复制") from exc
        if authorized_count > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
          raise RetryableReportError(
            f"Agent 授权账户范围超过限制: {_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
          )
        account_ids = {
          str(value).strip() for value in list(authorized_values) if str(value).strip()
        }
    if not account_ids:
      raise RetryableReportError(
        "完整账户快照失败但无法从报告或 Agent 授权范围确定账户"
      )
  snapshot_id = str(payload.get("snapshot_id") or "").strip() or None
  raw_section_values = payload.get("section_completeness_by_account")
  section_values = (
    raw_section_values
    if isinstance(raw_section_values, dict)
    and len(raw_section_values) <= _MAX_SNAPSHOT_ACCOUNT_SCOPE
    else None
  )
  raw_unavailable_accounts = payload.get("unavailable_accounts") or []
  try:
    unavailable_count = len(raw_unavailable_accounts)
  except TypeError:
    unavailable_count = _MAX_SNAPSHOT_ACCOUNT_SCOPE + 1
  unavailable_accounts = (
    {str(value).strip() for value in raw_unavailable_accounts if str(value).strip()}
    if unavailable_count <= _MAX_SNAPSHOT_ACCOUNT_SCOPE
    else set()
  )
  blocked_accounts: list[str] = []
  authority_failures: list[str] = []
  snapshot_failures: list[str] = []
  position_service = PositionService()
  async with AsyncSessionLocal() as db:
    for account_id in sorted(account_ids):
      raw_sections = section_values.get(account_id) if section_values else None
      incomplete_sections = [
        section
        for section in _REQUIRED_SNAPSHOT_SECTIONS
        if not isinstance(raw_sections, dict) or raw_sections.get(section) is not True
      ]
      discrepancy = {
        "kind": failure_kind,
        "reason": failure_reason,
        "business_id": account_id,
      }
      if scope_validation_failed:
        discrepancy["scopeValidation"] = "BOUNDED_DEVICE_FALLBACK"
      if failure_kind == "SNAPSHOT_SECTION_INCOMPLETE":
        discrepancy.update(
          {
            "sections": incomplete_sections,
            "unavailable": account_id in unavailable_accounts,
          }
        )
      discrepancies = [discrepancy]
      # Account coordination is intentionally scoped to the snapshot/failure
      # authority boundary.  Order/trade convergence remains outside this
      # lock, while monitor/candidate/approval cannot observe the old complete
      # snapshot or old V3 entry authority during this transition.
      async with t_trade_account_coordination_lock(account_id):
        try:
          await position_service.mark_snapshot_failure(
            account_id,
            f"{failure_kind}:{failure_reason}",
          )
        except Exception as exc:
          snapshot_failures.append(f"{account_id}: {exc}")
        try:
          await _invalidate_t_trade_entry_authority_for_account(
            account_id,
            reason=f"{failure_kind}:{failure_reason}",
          )
        except Exception as exc:
          authority_failures.append(f"{account_id}: {exc}")

        rollout = await db.get(
          AccountExecutionControl,
          account_id,
          with_for_update=True,
        )
        if rollout is None:
          rollout = AccountExecutionControl(account_id=account_id)
          db.add(rollout)
        previous_state = str(rollout.authorization_state)
        window_was_active = bool(rollout.controlled_window_active)
        rollout.reconcile_status = "RECONCILE_REQUIRED"
        if rollout.authorization_state != "KILLED":
          rollout.authorization_state = "PAUSED"
        rollout.state_version = int(rollout.state_version or 0) + 1
        rollout.paused_reason = json.dumps(
          discrepancies,
          ensure_ascii=False,
          default=str,
        )[:2000]
        if window_was_active:
          rollout.controlled_window_active = False
          rollout.controlled_window_snapshot_id = None
          rollout.controlled_window_snapshot_hash = None
          rollout.controlled_window_started_at = None
          rollout.controlled_window_started_by_user_id = None
          rollout.controlled_window_external_order_ids = []
          rollout.controlled_window_external_trade_ids = []
        db.add(
          AccountExecutionControlEvent(
            event_id=str(uuid.uuid4()),
            account_id=account_id,
            event_type="SNAPSHOT_INCOMPLETE",
            previous_state=previous_state,
            next_state=str(rollout.authorization_state),
            snapshot_id=snapshot_id,
            details={
              "deviceId": device_id,
              "reportedAt": reported_at.isoformat(),
              "discrepancies": discrepancies,
              "controlledWindowInvalidated": window_was_active,
            },
            created_at=utcnow(),
          )
        )
        blocked_accounts.append(account_id)
        # Commit the durable fail-closed rollout marker before releasing this
        # account lock.  ``mark_snapshot_failure`` normally commits through a
        # separate PositionService session; if that write failed, this marker
        # is the remaining durable barrier against monitor re-authorization.
        await db.commit()

    heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{device_id}",
    )
    if heartbeat is not None:
      details = dict(heartbeat.details or {})
      details["incompleteSnapshotAccounts"] = blocked_accounts
      details["incompleteSnapshotAt"] = reported_at.isoformat()
      heartbeat.details = details
      if str(heartbeat.status or "").upper() in {"READY", "RECONCILING"}:
        heartbeat.status = "RECONCILE_REQUIRED"
      heartbeat.updated_at = utcnow()
    await db.commit()
  if snapshot_failures or authority_failures:
    details = [*snapshot_failures, *authority_failures]
    raise RetryableReportError(
      "完整账户快照失败边界未完全收敛: " + "; ".join(details[:20])
    )


async def _process_delta_report(
  device_id: str,
  payload: dict[str, Any],
  *,
  protocol_version: str = "1.0",
) -> None:
  full_attempt_state: dict[str, set[str]] = {}
  try:
    await _process_delta_report_inner(
      device_id,
      payload,
      protocol_version=protocol_version,
      _full_attempt_state=full_attempt_state,
    )
  except Exception:
    # Once a protocol-1.1 full payload has passed identity/completeness
    # validation, *any* later failure (including order/trade convergence,
    # sequence conversion, position promotion, or rollout projection) must
    # invalidate the snapshot.  This closes the old-complete/new-complete
    # ambiguity before the report is retried or dead-lettered.
    full_snapshot_attempt = bool(
      payload.get("is_complete") is True
      or "section_completeness_by_account" in payload
      or "unavailable_accounts" in payload
    )
    scope_validation_failed = False
    try:
      authoritative_accounts = _authoritative_full_snapshot_account_ids(
        payload,
        protocol_version=protocol_version,
      )
    except Exception:
      # Scope validation itself may fail before the inner handler reaches its
      # normal invalid-full fail-close path (for example, a 4097-account
      # positions map).  Do not repeat the unbounded parser here.  Passing an
      # explicit empty override makes the failure helper derive the bounded,
      # authenticated device scope instead.
      authoritative_accounts = None
      scope_validation_failed = True
    if not scope_validation_failed and full_snapshot_attempt:
      try:
        _report_account_ids(payload)
      except RetryableReportError:
        # Identity validation can return ``None`` before it reaches the
        # account-scope parser (for example, a missing hash on an oversized
        # full payload).  Recheck the bounded scope independently so malformed
        # reports still take the authenticated-device fail-close path.
        scope_validation_failed = True
    stale_accounts = set(full_attempt_state.get("stale_accounts") or set())
    failure_accounts = (
      authoritative_accounts - stale_accounts
      if authoritative_accounts is not None
      else set()
    )
    if failure_accounts or (scope_validation_failed and full_snapshot_attempt):
      await _fail_closed_incomplete_snapshot(
        device_id,
        payload,
        reported_at=_safe_snapshot_failure_time(payload.get("source_event_at")),
        failure_kind="SNAPSHOT_APPLY_FAILED",
        failure_reason="FULL_SNAPSHOT_APPLY_FAILED",
        account_ids_override=failure_accounts,
      )
    raise


async def _process_delta_report_inner(
  device_id: str,
  payload: dict[str, Any],
  *,
  protocol_version: str = "1.0",
  _full_attempt_state: Optional[dict[str, set[str]]] = None,
) -> None:
  full_attempt_state = _full_attempt_state if _full_attempt_state is not None else {}
  stale_full_accounts: set[str] = set()
  full_attempt_state["stale_accounts"] = stale_full_accounts
  declared_complete = payload.get("is_complete") is True
  full_snapshot_attempt = bool(
    declared_complete
    or "section_completeness_by_account" in payload
    or "unavailable_accounts" in payload
  )
  complete_account_ids = (
    _complete_snapshot_account_ids(payload) if declared_complete else None
  )
  snapshot_id = str(payload.get("snapshot_id") or "")
  snapshot_hash = str(payload.get("snapshot_hash") or "")
  snapshot_identity_error = ""
  identity_valid = False
  if declared_complete:
    if not snapshot_id or len(snapshot_hash) != 64:
      snapshot_identity_error = "完整账户快照缺少协议 1.1 身份"
    else:
      hash_input = {
        key: value for key, value in payload.items() if key != "snapshot_hash"
      }
      expected_hash = sha256(
        json.dumps(
          hash_input,
          sort_keys=True,
          separators=(",", ":"),
          default=str,
        ).encode("utf-8")
      ).hexdigest()
      if expected_hash != snapshot_hash:
        snapshot_identity_error = "完整账户快照哈希校验失败"
      else:
        identity_valid = True
  authoritative = bool(
    declared_complete
    and protocol_version == "1.1"
    and complete_account_ids is not None
    and identity_valid
  )
  reported_at = (
    _parse_authoritative_snapshot_time(payload.get("source_event_at"))
    if authoritative
    else (
      _safe_snapshot_failure_time(payload.get("source_event_at"))
      if full_snapshot_attempt
      else _parse_report_time(payload.get("source_event_at"))
    )
  )
  position_service = PositionService()
  full_snapshot_sequence: Optional[int] = None
  full_snapshot_groups: dict[str, list[Any]] = {}
  if authoritative:
    # Parse and snapshot the account groups before any order/trade convergence.
    # A malformed sequence is an authoritative full-attempt failure and is
    # handled by the outer fail-closed boundary.
    full_snapshot_sequence = _parse_authoritative_snapshot_sequence(payload)
    groups_value = payload.get("positions_by_account")
    if isinstance(groups_value, dict):
      full_snapshot_groups = {
        str(account_id or "").strip(): list(positions or [])
        for account_id, positions in groups_value.items()
        if str(account_id or "").strip()
      }
    else:
      account_id = str(payload.get("account_id") or "").strip()
      if account_id:
        full_snapshot_groups[account_id] = list(payload.get("positions") or [])
  if full_snapshot_attempt and not authoritative:
    if protocol_version != "1.1":
      failure_kind = "SNAPSHOT_PROTOCOL_INVALID"
      failure_reason = "PROTOCOL_1_1_REQUIRED"
    elif snapshot_identity_error:
      failure_kind = "SNAPSHOT_IDENTITY_INVALID"
      failure_reason = (
        "SNAPSHOT_HASH_MISMATCH"
        if "哈希" in snapshot_identity_error
        else "SNAPSHOT_IDENTITY_MISSING"
      )
    else:
      failure_kind = "SNAPSHOT_SECTION_INCOMPLETE"
      failure_reason = "SECTION_PROOF_MISSING_OR_INCOMPLETE"
    # Close the durable trading gate before processing any partial section.
    # A concurrent order enqueue must never observe the prior READY rollout.
    await _fail_closed_incomplete_snapshot(
      device_id,
      payload,
      reported_at=reported_at,
      failure_kind=failure_kind,
      failure_reason=failure_reason,
    )
  if snapshot_identity_error:
    raise ValueError(snapshot_identity_error)

  if authoritative:
    begin_full_snapshot_attempt = getattr(
      position_service,
      "begin_full_snapshot_attempt",
      None,
    )
    invalidate_reason = "SNAPSHOT_APPLY_IN_PROGRESS"
    # Full snapshots first CAS their observed generation into a durable
    # in-progress state.  This closes the old-complete window before any
    # order/trade convergence runs outside the account lock.  A duplicate
    # sequence is deliberately a no-op: the newer complete snapshot must
    # remain usable and must never be downgraded.
    if not callable(begin_full_snapshot_attempt):
      raise RetryableReportError("完整快照缺少持久化 begin attempt 边界")
    for account_id in sorted(full_snapshot_groups):
      async with t_trade_account_coordination_lock(account_id):
        result = await begin_full_snapshot_attempt(
          account_id=account_id,
          sequence=int(full_snapshot_sequence or 0),
          reported_at=reported_at,
          source="QMT_AGENT",
        )
        result_dict = dict(result) if isinstance(result, dict) else {}
        if result_dict.get("reason") == "STALE_SEQUENCE":
          stale_full_accounts.add(account_id)
          continue
        if not result_dict.get("applied", False):
          raise RetryableReportError(
            "完整快照 begin attempt 未完成: "
            + str(result_dict.get("reason") or "UNKNOWN")
          )
        await _invalidate_t_trade_entry_authority_for_account(
          account_id,
          reason=invalidate_reason,
        )

  all_authoritative_accounts = set(full_snapshot_groups)

  def skip_stale_full_item(
    value: Any,
    *,
    unknown_account_is_stale: bool = False,
  ) -> bool:
    if not authoritative or not stale_full_accounts:
      return False
    raw_account_id = value.get("account_id") if isinstance(value, dict) else ""
    account_id = str(raw_account_id or "").strip()
    return bool(
      account_id in stale_full_accounts
      or (not account_id and (unknown_account_is_stale or bool(stale_full_accounts)))
    )

  for order in payload.get("orders") or []:
    if skip_stale_full_item(order):
      continue
    await _process_order_report(
      {
        "client_order_id": order.get("client_order_id"),
        "source_sequence": order.get("source_sequence")
        or payload.get("source_sequence")
        or payload.get("sequence"),
        "source_event_at": order.get("source_event_at")
        or payload.get("source_event_at"),
        "order": dict(order),
      }
    )
  for trade in payload.get("trades") or []:
    if skip_stale_full_item(trade):
      continue
    await _process_execution_report(
      {
        "client_order_id": trade.get("client_order_id"),
        "source_sequence": trade.get("source_sequence")
        or payload.get("source_sequence")
        or payload.get("sequence"),
        "source_event_at": trade.get("source_event_at")
        or payload.get("source_event_at"),
        "order_status": trade.get("order_status") or "FILLED",
        "execution": dict(trade),
      }
    )
  for account in payload.get("accounts") or []:
    account_value = dict(account)
    account_id = str(account_value.get("account_id") or "").strip()
    if skip_stale_full_item(account_value):
      continue
    if full_snapshot_attempt and not _snapshot_section_is_complete(
      payload,
      account_id,
      "account",
    ):
      continue
    await _upsert_account(account_value)
  for error in payload.get("order_errors") or []:
    if skip_stale_full_item(error, unknown_account_is_stale=True):
      continue
    reason = str(error.get("error_msg") or error.get("reason") or "")
    terminal_status = (
      "EXPIRED"
      if str(error.get("reason") or "").strip().lower() == "command_expired"
      else "REJECTED"
    )
    client_order_id = str(error.get("client_order_id") or "")
    broker_order_id = str(error.get("order_id") or error.get("broker_order_id") or "")
    if client_order_id:
      await _update_pending(
        client_order_id,
        status=terminal_status,
        reason=reason,
      )
    else:
      await _update_pending_by_broker(
        broker_order_id,
        status=terminal_status,
        reason=reason,
      )
    await AutoExitPlanService().apply_order_event_for_report(
      client_order_id=client_order_id,
      broker_order_id=broker_order_id,
      status="REJECTED",
      source_sequence=int(
        error.get("source_sequence")
        or payload.get("source_sequence")
        or payload.get("sequence")
        or 0
      ),
    )
  for error in payload.get("cancel_errors") or []:
    if skip_stale_full_item(error, unknown_account_is_stale=True):
      continue
    reason = str(error.get("error_msg") or error.get("reason") or "")
    client_order_id = str(error.get("client_order_id") or "")
    if client_order_id:
      await _update_pending(
        client_order_id,
        status="CANCEL_REJECTED",
        reason=reason,
      )
    else:
      await _update_pending_by_broker(
        error.get("order_id") or error.get("broker_order_id"),
        status="CANCEL_REJECTED",
        reason=reason,
      )

  full_snapshot_results: dict[str, dict[str, Any]] = {}
  if not authoritative and not full_snapshot_attempt:
    default_account_id = str(payload.get("account_id") or "")
    deltas = payload.get("position_deltas")
    if deltas is None:
      deltas = payload.get("positions") or []
    for position in deltas:
      value = dict(position)
      account_id = str(value.get("account_id") or default_account_id)
      if not account_id:
        raise ValueError("持仓增量缺少 account_id")
      await _run_account_snapshot_mutation(
        account_id,
        lambda value=value, account_id=account_id: (
          position_service.apply_position_delta(
            value,
            account_id,
          )
        ),
        reason="BROKER_POSITION_DELTA_APPLIED",
      )

  if authoritative:
    ready_accounts: list[str] = []
    blocked_accounts: list[str] = []
    reconciliation_accounts: dict[str, dict[str, Any]] = {}
    account_ids = {
      str(item.get("account_id") or "")
      for item in payload.get("accounts") or []
      if str(item.get("account_id") or "")
    }
    account_ids.update(
      str(value)
      for value in (payload.get("positions_by_account") or {}).keys()
      if str(value)
    )
    for account_id in sorted(account_ids):
      if account_id in stale_full_accounts:
        # A duplicate full report must not re-project an older snapshot over
        # the current rollout or heartbeat state.
        full_snapshot_results[account_id] = {"reason": "STALE_SEQUENCE"}
        continue
      async with t_trade_account_coordination_lock(account_id):
        (
          result,
          is_blocked,
          summary,
        ) = await _reconcile_authoritative_full_account_locked(
          account_id,
          payload,
          snapshot_id=snapshot_id,
          snapshot_hash=snapshot_hash,
          reported_at=reported_at,
          sequence=int(full_snapshot_sequence or 0),
          positions=full_snapshot_groups.get(account_id, []),
          position_service=position_service,
        )
        full_snapshot_results[account_id] = result
        if result.get("reason") == "STALE_SEQUENCE":
          stale_full_accounts.add(account_id)
          continue
        if is_blocked:
          blocked_accounts.append(account_id)
        else:
          ready_accounts.append(account_id)
        reconciliation_accounts[account_id] = summary

    if stale_full_accounts and stale_full_accounts == all_authoritative_accounts:
      return

    async with AsyncSessionLocal() as db:
      heartbeat = await db.get(
        RuntimeComponentHeartbeat,
        f"qmt-agent:{device_id}",
      )
      if heartbeat is not None:
        details = dict(heartbeat.details or {})
        account_details = dict(details.get("accountReconciliation") or {})
        account_details.update(reconciliation_accounts)
        details.update(
          {
            "readyAccounts": ready_accounts,
            "blockedAccounts": blocked_accounts,
            "accountReconciliation": account_details,
          }
        )
        if not stale_full_accounts:
          details.update(
            {
              "snapshotId": snapshot_id,
              "snapshotHash": snapshot_hash,
              "snapshotAt": reported_at.isoformat(),
            }
          )
        observed_at = utcnow()
        heartbeat.details = details
        if not stale_full_accounts and _snapshot_can_promote_heartbeat(
          heartbeat.status
        ):
          next_status = "READY" if not blocked_accounts else "RECONCILE_REQUIRED"
          heartbeat.status = next_status
          if next_status == "READY":
            device = await db.get(AgentDevice, device_id)
            if device is not None and device.revoked_at is None:
              revoked_ids = await converge_ready_agent(
                db,
                device=device,
                observed_at=observed_at,
              )
              if revoked_ids:
                details = {
                  **details,
                  "completedHandoverDeviceIds": revoked_ids,
                  "completedHandoverAt": observed_at.isoformat(),
                }
                heartbeat.details = details
        heartbeat.updated_at = observed_at
        await db.commit()


async def _reconcile_authoritative_full_account_locked(
  account_id: str,
  payload: dict[str, Any],
  *,
  snapshot_id: str,
  snapshot_hash: str,
  reported_at: datetime,
  sequence: int,
  positions: list[Any],
  position_service: PositionService,
) -> tuple[dict[str, Any], bool, dict[str, Any]]:
  """Prepare, project, and finalize one full account under its lock."""

  prepare = getattr(position_service, "prepare_full_snapshot", None)
  if not callable(prepare):
    raise RetryableReportError("完整快照缺少持久化 prepare 边界")
  result = await prepare(
    account_id=account_id,
    positions=list(positions),
    sequence=sequence,
    reported_at=reported_at,
    source="QMT_AGENT",
  )
  result_dict = dict(result) if isinstance(result, dict) else {}
  if result_dict.get("reason") == "STALE_SEQUENCE":
    # A concurrent writer should already be impossible under the shared
    # account lock.  Keep this defensive branch so a stale duplicate can never
    # rewrite rollout snapshot metadata or trigger a downgrade.
    return result_dict, False, {}

  async with AsyncSessionLocal() as db:
    existing_rollout = await db.get(AccountExecutionControl, account_id)
    controlled_window_active = bool(
      existing_rollout and existing_rollout.controlled_window_active
    )
    acknowledged_external_order_ids = (
      {
        str(value)
        for value in list(existing_rollout.controlled_window_external_order_ids or [])
      }
      if existing_rollout
      else set()
    )
    acknowledged_external_trade_ids = (
      {
        str(value)
        for value in list(existing_rollout.controlled_window_external_trade_ids or [])
      }
      if existing_rollout
      else set()
    )
    allow_external_activity = bool(
      existing_rollout is None
      or (
        str(existing_rollout.authorization_state).upper() in {"DISABLED", "PAUSED"}
        and not controlled_window_active
      )
    )
  reconciliation = await _snapshot_discrepancies(
    account_id,
    payload,
    allow_external_activity=allow_external_activity,
    acknowledged_external_order_ids=acknowledged_external_order_ids,
    acknowledged_external_trade_ids=acknowledged_external_trade_ids,
  )
  discrepancies = list(reconciliation["blocking_discrepancies"])
  async with AsyncSessionLocal() as db:
    rollout = await db.get(
      AccountExecutionControl,
      account_id,
      with_for_update=True,
    )
    if rollout is None:
      rollout = AccountExecutionControl(account_id=account_id)
      db.add(rollout)
    rollout.last_snapshot_id = snapshot_id or None
    rollout.last_snapshot_hash = snapshot_hash or None
    rollout.last_snapshot_at = to_naive_utc(reported_at)
    rollout.reconcile_status = "READY" if not discrepancies else "RECONCILE_REQUIRED"
    if discrepancies:
      window_was_active = bool(rollout.controlled_window_active)
      previous_state = str(rollout.authorization_state)
      if rollout.authorization_state != "KILLED":
        rollout.authorization_state = "PAUSED"
      rollout.state_version = int(rollout.state_version or 0) + 1
      rollout.paused_reason = json.dumps(
        discrepancies[:20],
        ensure_ascii=False,
        default=str,
      )[:2000]
      if window_was_active:
        rollout.controlled_window_active = False
        rollout.controlled_window_snapshot_id = None
        rollout.controlled_window_snapshot_hash = None
        rollout.controlled_window_started_at = None
        rollout.controlled_window_started_by_user_id = None
        rollout.controlled_window_external_order_ids = []
        rollout.controlled_window_external_trade_ids = []
        db.add(
          AccountExecutionControlEvent(
            event_id=str(uuid.uuid4()),
            account_id=account_id,
            event_type="CONTROLLED_WINDOW_INVALIDATED",
            previous_state=previous_state,
            next_state=str(rollout.authorization_state),
            snapshot_id=snapshot_id or None,
            details={"discrepancies": discrepancies[:20]},
            created_at=utcnow(),
          )
        )
    else:
      if bool(rollout.controlled_window_active):
        rollout.controlled_window_snapshot_id = snapshot_id or None
        rollout.controlled_window_snapshot_hash = snapshot_hash or None
      if str(
        rollout.authorization_state
      ).upper() == "PAUSED" and _was_automatic_reconciliation_pause(
        rollout.paused_reason
      ):
        # A recovered automatic pause returns to the read-only preparation
        # stage.  It never silently resumes CANARY/LIVE order authority.
        rollout.authorization_state = "DISABLED"
        rollout.state_version = int(rollout.state_version or 0) + 1
        rollout.paused_reason = None
    summary = {
      "snapshotId": snapshot_id,
      "snapshotAt": reported_at.isoformat(),
      "status": rollout.reconcile_status,
      "manualCoexistence": allow_external_activity,
      "externalOrderCount": len(reconciliation["external_orders"]),
      "externalTradeCount": len(reconciliation["external_trades"]),
      "newExternalOrderCount": sum(
        not bool(item.get("acknowledged")) for item in reconciliation["external_orders"]
      ),
      "newExternalTradeCount": sum(
        not bool(item.get("acknowledged")) for item in reconciliation["external_trades"]
      ),
      "workingExternalOrderCount": sum(
        str(item.get("status") or "") in {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}
        for item in reconciliation["external_orders"]
      ),
      "controlledWindowActive": bool(rollout.controlled_window_active),
      "blockingDiscrepancyCount": len(discrepancies),
    }
    await db.commit()

  try:
    await _invalidate_t_trade_entry_authority_for_account(
      account_id,
      reason="BROKER_POSITION_SNAPSHOT_UPDATED",
    )
  except Exception as authority_error:
    # Keep the prepared broker marker incomplete and block rollout while the
    # same account lock is still held, before surfacing a retryable error.
    marker_error: Optional[Exception] = None
    try:
      await position_service.mark_snapshot_failure(
        account_id,
        "SNAPSHOT_AUTHORITY_INVALIDATION_FAILED",
      )
    except Exception as exc:
      marker_error = exc
    async with AsyncSessionLocal() as db:
      rollout = await db.get(
        AccountExecutionControl,
        account_id,
        with_for_update=True,
      )
      if rollout is None:
        rollout = AccountExecutionControl(account_id=account_id)
        db.add(rollout)
      rollout.reconcile_status = "RECONCILE_REQUIRED"
      if rollout.authorization_state != "KILLED":
        rollout.authorization_state = "PAUSED"
      rollout.state_version = int(rollout.state_version or 0) + 1
      rollout.paused_reason = json.dumps(
        [
          {
            "kind": "SNAPSHOT_AUTHORITY_INVALIDATION_FAILED",
            "reason": str(authority_error),
          }
        ],
        ensure_ascii=False,
        default=str,
      )[:2000]
      await db.commit()
    detail = str(authority_error)
    if marker_error is not None:
      detail += f"; marker={marker_error}"
    raise RetryableReportError(
      "完整快照 authority 失效边界未完成: " + detail
    ) from authority_error

  if not discrepancies:
    finalize = getattr(position_service, "finalize_full_snapshot", None)
    if not callable(finalize):
      raise RetryableReportError("完整快照缺少持久化 finalize 边界")
    finalized = await finalize(
      account_id=account_id,
      sequence=sequence,
      reported_at=reported_at,
      source="QMT_AGENT",
    )
    if not isinstance(finalized, dict) or not finalized.get("applied", False):
      reason = finalized.get("reason") if isinstance(finalized, dict) else "UNKNOWN"
      if reason == "STALE_SEQUENCE" and isinstance(finalized, dict):
        # A direct/concurrent durable writer may have advanced or invalidated
        # this generation after prepare. Treat it as a stale duplicate so the
        # outer failure boundary cannot downgrade the newer current marker.
        return finalized, False, {}
      raise RetryableReportError(
        "完整快照 finalize 未完成: " + str(reason or "UNKNOWN")
      )
    result_dict = finalized

  return result_dict, bool(discrepancies), summary


async def _snapshot_discrepancies(
  account_id: str,
  payload: dict[str, Any],
  *,
  allow_external_activity: bool,
  acknowledged_external_order_ids: set[str] | None = None,
  acknowledged_external_trade_ids: set[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
  acknowledged_external_order_ids = acknowledged_external_order_ids or set()
  acknowledged_external_trade_ids = acknowledged_external_trade_ids or set()
  snapshot_orders = [
    dict(item)
    for item in payload.get("orders") or []
    if str(item.get("account_id") or "") == account_id
  ]
  snapshot_trades = [
    dict(item)
    for item in payload.get("trades") or []
    if str(item.get("account_id") or "") == account_id
  ]
  async with AsyncSessionLocal() as db:
    pending = (
      (
        await db.execute(
          select(PendingTradeOrder).where(PendingTradeOrder.account_id == account_id)
        )
      )
      .scalars()
      .all()
    )
  by_client = {str(item.client_order_id): item for item in pending}
  by_broker = {
    str(item.broker_order_id): item for item in pending if item.broker_order_id
  }
  discrepancies: list[dict[str, str]] = []
  external_orders: list[dict[str, Any]] = []
  external_trades: list[dict[str, Any]] = []
  seen_broker_ids: set[str] = set()
  for order in snapshot_orders:
    client_id = str(order.get("client_order_id") or "")
    broker_id = str(order.get("order_id") or order.get("broker_order_id") or "")
    if broker_id:
      seen_broker_ids.add(broker_id)
    if not by_client.get(client_id) and not by_broker.get(broker_id):
      observation = {
        "kind": "EXTERNAL_BROKER_ORDER",
        "business_id": broker_id or client_id or "unknown",
        "status": _normalized_order_status(
          order.get("effective_order_status")
          or order.get("order_status", order.get("status"))
        ),
        "raw_status": _normalized_order_status(
          order.get("order_status", order.get("status"))
        ),
        "status_reason": str(order.get("effective_status_reason") or ""),
      }
      observation["acknowledged"] = (
        observation["business_id"] in acknowledged_external_order_ids
      )
      external_orders.append(observation)
      if not allow_external_activity and not observation["acknowledged"]:
        discrepancies.append(
          {
            "kind": "UNKNOWN_BROKER_ORDER",
            "business_id": observation["business_id"],
          }
        )
  for trade in snapshot_trades:
    client_id = str(trade.get("client_order_id") or "")
    broker_id = str(trade.get("order_id") or trade.get("broker_order_id") or "")
    if not by_client.get(client_id) and not by_broker.get(broker_id):
      observation = {
        "kind": "EXTERNAL_BROKER_TRADE",
        "business_id": str(
          trade.get("execution_id")
          or trade.get("traded_id")
          or trade.get("trade_id")
          or broker_id
          or "unknown"
        ),
        "status": "FILLED",
      }
      observation["acknowledged"] = (
        observation["business_id"] in acknowledged_external_trade_ids
      )
      external_trades.append(observation)
      if not allow_external_activity and not observation["acknowledged"]:
        discrepancies.append(
          {
            "kind": "UNKNOWN_BROKER_TRADE",
            "business_id": observation["business_id"],
          }
        )
  for item in pending:
    if (
      item.broker_order_id
      and str(item.status).upper() in {"SUBMITTED", "PARTIAL_FILLED", "PENDING"}
      and str(item.broker_order_id) not in seen_broker_ids
    ):
      discrepancies.append(
        {
          "kind": "MISSING_WORKING_ORDER",
          "business_id": str(item.client_order_id),
        }
      )
  return {
    "blocking_discrepancies": discrepancies,
    "external_orders": external_orders,
    "external_trades": external_trades,
  }


async def _process(report: AgentReportInbox) -> None:
  if report.message_type == "order_report":
    await _process_order_report(report.payload)
  elif report.message_type == "execution_report":
    await _process_execution_report(report.payload)
  elif report.message_type == "delta_report":
    await _process_delta_report(
      report.device_id,
      report.payload,
      protocol_version=str(report.protocol_version or "1.0"),
    )
  else:
    raise ValueError(f"未知 Agent report 类型: {report.message_type}")


def _normalized_order_status(value: Any) -> str:
  if hasattr(value, "name"):
    value = value.name
  special_status = str(value or "").strip().upper()
  if special_status in _SPECIAL_RUNTIME_ORDER_STATUSES:
    return special_status
  try:
    return _ORDER_STATUS_NAMES[int(value)]
  except (TypeError, ValueError, KeyError):
    pass
  text = normalize_order_status(value)
  return text if text in OrderStatus.__members__ else "PENDING"


def _parse_report_time(value: Any) -> datetime:
  if isinstance(value, datetime):
    return value
  if isinstance(value, (int, float)):
    return datetime.fromtimestamp(float(value), tz=time_utils.now().tzinfo)
  if isinstance(value, str) and value:
    try:
      return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
      pass
  return time_utils.now()


def _parse_authoritative_snapshot_time(value: Any) -> datetime:
  """Parse a full-snapshot timestamp without turning bad input into ``now``."""

  if value is None or value == "":
    return time_utils.now()
  if isinstance(value, datetime):
    return value
  if isinstance(value, (int, float)) and not isinstance(value, bool):
    numeric = float(value)
    if not math.isfinite(numeric):
      raise ValueError("完整账户快照 source_event_at 不是有限时间")
    try:
      return datetime.fromtimestamp(numeric, tz=time_utils.now().tzinfo)
    except (OverflowError, OSError, ValueError) as exc:
      raise ValueError("完整账户快照 source_event_at 不可解析") from exc
  if isinstance(value, str) and value.strip():
    try:
      return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
      raise ValueError("完整账户快照 source_event_at 不可解析") from exc
  raise ValueError("完整账户快照 source_event_at 类型无效")


def _safe_snapshot_failure_time(value: Any) -> datetime:
  """Return a local failure timestamp even when the report timestamp is bad."""

  try:
    return _parse_authoritative_snapshot_time(value)
  except (TypeError, ValueError, OverflowError, OSError):
    return time_utils.now()


def _parse_authoritative_snapshot_sequence(payload: dict[str, Any]) -> int:
  """Require an explicit positive integer generation for protocol-1.1 full data."""

  if "source_sequence" in payload:
    raw_value = payload.get("source_sequence")
  elif "sequence" in payload:
    raw_value = payload.get("sequence")
  else:
    raise ValueError("完整账户快照缺少正整数 sequence")
  if raw_value is None or isinstance(raw_value, bool):
    raise ValueError("完整账户快照 sequence 必须是正整数")
  if isinstance(raw_value, float) and not raw_value.is_integer():
    raise ValueError("完整账户快照 sequence 必须是正整数")
  if isinstance(raw_value, Decimal) and raw_value != raw_value.to_integral_value():
    raise ValueError("完整账户快照 sequence 必须是正整数")
  try:
    sequence = int(raw_value)
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError("完整账户快照 sequence 必须是正整数") from exc
  if sequence <= 0:
    raise ValueError("完整账户快照 sequence 必须是正整数")
  return sequence


_FILL_TERMINAL_ORDER_STATUSES = {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}


def _reported_cumulative_fill(report: dict[str, Any]) -> Optional[int]:
  """Read every supplied cumulative-fill field without truthy short-circuiting."""

  values: list[int] = []
  for key in ("traded_volume", "filled_volume"):
    if key not in report:
      continue
    try:
      value = int(report.get(key))
    except (TypeError, ValueError, OverflowError):
      return None
    if value < 0:
      return None
    values.append(value)
  return max(values) if values else None


async def _terminal_order_fill_projection(
  db,
  correlation: StrategyOrderCorrelation,
  intent: TradeIntentRecord,
  *,
  current_order: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
  """Return the terminal order target and execution-report progress for one intent."""
  pending = await db.get(PendingTradeOrder, correlation.client_order_id)
  terminal_reports: list[tuple[dict[str, Any], str]] = []
  if current_order is None:
    candidates = (
      await db.execute(
        select(StrategyRuntimeEvent)
        .where(
          StrategyRuntimeEvent.client_order_id == correlation.client_order_id,
          StrategyRuntimeEvent.event_type == "ORDER",
        )
        .order_by(
          StrategyRuntimeEvent.created_at.desc(),
          StrategyRuntimeEvent.event_id.desc(),
        )
        .limit(20)
      )
    ).scalars()
    for candidate in candidates:
      candidate_report = dict(dict(candidate.payload or {}).get("report") or {})
      candidate_status = _normalized_order_status(
        candidate_report.get("effective_order_status")
        or candidate_report.get("status")
        or candidate_report.get("order_status")
      )
      if candidate_status in _FILL_TERMINAL_ORDER_STATUSES:
        terminal_reports.append((candidate_report, candidate_status))
    if not terminal_reports and pending is not None:
      pending_status = _normalized_order_status(pending.status)
      if pending_status in _FILL_TERMINAL_ORDER_STATUSES:
        terminal_reports.append(({}, pending_status))
  else:
    report = dict(current_order)
    status = _normalized_order_status(
      report.get("effective_order_status")
      or report.get("status")
      or report.get("order_status")
    )
    if status in _FILL_TERMINAL_ORDER_STATUSES:
      terminal_reports.append((report, status))

  if not terminal_reports:
    return None
  received = max(0, int(intent.executed_volume or 0))
  role = str(correlation.t_trade_role or "").strip().upper()
  selected: Optional[dict[str, Any]] = None
  for report, status in terminal_reports:
    requested = max(
      0,
      int(
        (pending.volume if pending is not None else None)
        or intent.target_volume
        or report.get("order_volume")
        or report.get("volume")
        or 0
      ),
    )
    reported = _reported_cumulative_fill(report)
    reported_field_present = any(
      key in report for key in ("traded_volume", "filled_volume")
    )
    expected = (
      max(1, requested)
      if reported is None and reported_field_present
      else (
        int(reported or 0)
        if int(reported or 0) > 0
        else (max(1, requested) if status == "FILLED" else 0)
      )
    )
    projection = {
      "status": status,
      "expected": expected,
      "received": received,
      "role": role,
      "reason": str(
        report.get("effective_status_reason") or report.get("status_msg") or ""
      ),
    }
    if selected is None or expected > int(selected["expected"]):
      selected = projection
  return selected


def _fill_projection_note(projection: dict[str, Any]) -> str:
  role = str(projection.get("role") or "ORDER")
  return (
    f"AWAITING_{role}_EXECUTION_REPORT: "
    f"terminal={projection.get('status')}, "
    f"expected={int(projection.get('expected') or 0)}, "
    f"received={int(projection.get('received') or 0)}"
  )


def _report_items(report: AgentReportInbox) -> list[tuple[str, dict[str, Any]]]:
  payload = dict(report.payload or {})
  if report.message_type == "order_report":
    return [("ORDER", _body(payload, "order"))]
  if report.message_type == "execution_report":
    return [("TRADE", _body(payload, "execution"))]
  if report.message_type == "delta_report":
    rejected_orders = []
    for error in payload.get("order_errors") or []:
      terminal_status = (
        "EXPIRED"
        if str(error.get("reason") or "").strip().lower() == "command_expired"
        else "REJECTED"
      )
      rejected_orders.append(
        {
          **dict(error),
          "order_status": terminal_status,
          "status": terminal_status,
          "status_msg": error.get("error_msg") or error.get("reason") or "",
          "broker_order_id": error.get("broker_order_id") or error.get("order_id"),
        }
      )
    return [
      *(("ORDER", dict(item)) for item in payload.get("orders") or []),
      *(("TRADE", dict(item)) for item in payload.get("trades") or []),
      *(("ORDER", item) for item in rejected_orders),
    ]
  return []


def _authoritative_snapshot_identity(
  report: AgentReportInbox,
) -> Optional[tuple[str, str]]:
  """Return a verified protocol 1.1 full-snapshot identity.

  ``_process_delta_report`` performs the same validation before updating the
  account rollout.  Repeating it here prevents a direct or replayed staging
  call from manufacturing a zero-fill proof from an unverified payload.
  """

  payload = dict(report.payload or {})
  if (
    report.message_type != "delta_report"
    or str(report.protocol_version or "") != "1.1"
    or payload.get("is_complete") is not True
  ):
    return None
  try:
    if _complete_snapshot_account_ids(payload) is None:
      return None
    _parse_authoritative_snapshot_sequence(payload)
    _parse_authoritative_snapshot_time(payload.get("source_event_at"))
  except (
    RetryableReportError,
    TypeError,
    ValueError,
    OverflowError,
    OSError,
  ):
    return None
  snapshot_id = str(payload.get("snapshot_id") or "").strip()
  snapshot_hash = str(payload.get("snapshot_hash") or "").strip().lower()
  if not snapshot_id or len(snapshot_hash) != 64:
    return None
  hash_input = {key: value for key, value in payload.items() if key != "snapshot_hash"}
  expected_hash = sha256(
    json.dumps(
      hash_input,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  if expected_hash != snapshot_hash:
    return None
  return snapshot_id, snapshot_hash


def _snapshot_fully_covers_account(
  payload: dict[str, Any],
  account_id: str,
) -> bool:
  """Require explicit completeness for every authoritative account section."""

  account_ids = _complete_snapshot_account_ids(payload)
  return bool(account_ids is not None and account_id in account_ids)


def _snapshot_has_order_execution_detail(
  payload: dict[str, Any],
  *,
  account_id: str,
  instrument_code: str,
  client_order_id: str,
  broker_order_id: str,
) -> bool:
  """Conservatively detect a current-snapshot execution for one order."""

  for raw_trade in payload.get("trades") or []:
    if not isinstance(raw_trade, dict):
      continue
    trade = dict(raw_trade)
    if str(trade.get("account_id") or "") != account_id:
      continue
    trade_client_id = str(trade.get("client_order_id") or "")
    trade_broker_id = str(trade.get("order_id") or trade.get("broker_order_id") or "")
    if trade_client_id == client_order_id or trade_broker_id == broker_order_id:
      return True
    trade_instrument = str(
      trade.get("stock_code") or trade.get("instrument_code") or ""
    ).upper()
    if (
      trade_instrument == instrument_code
      and not trade_client_id
      and not trade_broker_id
    ):
      # An execution for the same account/instrument without an order identity
      # cannot safely be distinguished from this managed entry.
      return True
  return False


async def _full_snapshot_zero_fill_items(
  db,
  report: AgentReportInbox,
) -> list[tuple[str, dict[str, Any]]]:
  """Prove broker-terminal managed BUY orders had no execution.

  A terminal order report alone is deliberately insufficient: QMT execution
  reports may arrive after it.  The proof is emitted only after a verified
  protocol 1.1 full snapshot has become the account's READY reconciliation
  checkpoint and both snapshot and durable execution stores are empty for the
  order.  The resulting synthetic ORDER event is replayable and auditable.
  """

  identity = _authoritative_snapshot_identity(report)
  if identity is None:
    return []
  snapshot_id, snapshot_hash = identity
  payload = dict(report.payload or {})
  try:
    snapshot_sequence = _parse_authoritative_snapshot_sequence(payload)
  except (TypeError, ValueError, OverflowError):
    return []

  results: list[tuple[str, dict[str, Any]]] = []
  seen_orders: set[tuple[str, str]] = set()
  checkpoint_by_account: dict[str, bool] = {}

  async def has_current_ready_checkpoint(account_id: str) -> bool:
    normalized_account = str(account_id or "").strip()
    if not normalized_account:
      return False
    if normalized_account not in checkpoint_by_account:
      rollout = await db.get(AccountExecutionControl, normalized_account)
      checkpoint_by_account[normalized_account] = bool(
        rollout is not None
        and str(rollout.last_snapshot_id or "") == snapshot_id
        and str(rollout.last_snapshot_hash or "") == snapshot_hash
        and str(rollout.reconcile_status or "").upper() == "READY"
      )
    return checkpoint_by_account[normalized_account]

  for raw_order in payload.get("orders") or []:
    if not isinstance(raw_order, dict):
      continue
    order = dict(raw_order)
    terminal_status = _normalized_order_status(
      order.get("effective_order_status")
      or order.get("status")
      or order.get("order_status")
    )
    if terminal_status not in _ZERO_FILL_RECONCILABLE_ORDER_STATUSES:
      continue
    reported_fill = _reported_cumulative_fill(order)
    if reported_fill is None:
      continue
    if reported_fill != 0:
      continue

    client_order_id = str(order.get("client_order_id") or "").strip()
    broker_order_id = str(
      order.get("order_id") or order.get("broker_order_id") or ""
    ).strip()
    account_id = str(order.get("account_id") or "").strip()
    instrument_code = (
      str(order.get("stock_code") or order.get("instrument_code") or "").strip().upper()
    )
    if (
      not client_order_id
      or not broker_order_id
      or not account_id
      or not instrument_code
      or (client_order_id, broker_order_id) in seen_orders
      or not _snapshot_fully_covers_account(payload, account_id)
      or not await has_current_ready_checkpoint(account_id)
    ):
      continue
    seen_orders.add((client_order_id, broker_order_id))

    rollout = await db.get(
      AccountExecutionControl,
      account_id,
      with_for_update=True,
    )
    if (
      rollout is None
      or str(rollout.reconcile_status or "").upper() != "READY"
      or str(rollout.last_snapshot_id or "") != snapshot_id
      or str(rollout.last_snapshot_hash or "").lower() != snapshot_hash
      or rollout.last_snapshot_at is None
    ):
      continue

    correlation = await _correlation_for_report(
      db,
      client_order_id=client_order_id,
      broker_order_id=broker_order_id,
    )
    if correlation is None:
      continue
    correlation = await db.get(
      StrategyOrderCorrelation,
      correlation.id,
      with_for_update=True,
    )
    pending = await db.get(
      PendingTradeOrder,
      client_order_id,
      with_for_update=True,
    )
    if correlation is None or pending is None:
      continue
    request_metadata = {
      **dict(pending.request_metadata or {}),
      **dict(correlation.request_metadata or {}),
    }
    plan_id = str(request_metadata.get("entry_plan_id") or "").strip()
    run_id = str(pending.strategy_run_id or "").strip()
    if (
      not plan_id
      or not run_id
      or run_id != str(correlation.strategy_run_id or "")
      or str(correlation.account_id or "") != account_id
      or str(pending.account_id or "") != account_id
      or str(pending.instrument_code or "").upper() != instrument_code
      or str(pending.side or "").upper() != "BUY"
      or str(pending.broker_order_id or "") != broker_order_id
      or _normalized_order_status(pending.status) != terminal_status
      or snapshot_sequence < max(0, int(pending.last_source_sequence or 0))
    ):
      continue
    if (
      pending.last_source_event_at is not None
      and to_naive_utc(pending.last_source_event_at) > rollout.last_snapshot_at
    ):
      continue

    intent = await db.get(
      TradeIntentRecord,
      correlation.intent_id,
      with_for_update=True,
    )
    intent_metadata = dict(intent.intent_metadata or {}) if intent is not None else {}
    try:
      executed_volume = int(intent.executed_volume or 0) if intent else -1
      executed_price = Decimal(str(intent.executed_price or 0)) if intent else None
    except (TypeError, ValueError, ArithmeticError):
      continue
    if (
      intent is None
      or str(intent.strategy_run_id or "") != run_id
      or str(intent.direction or "").upper() != "BUY"
      or str(intent.instrument_code or "").upper() != instrument_code
      or (intent.account_id and str(intent.account_id) != account_id)
      or str(intent_metadata.get("entry_plan_id") or "") != plan_id
      or executed_volume != 0
      or executed_price is None
      or not executed_price.is_finite()
      or executed_price > 0
      or intent.executed_time is not None
    ):
      continue
    if _snapshot_has_order_execution_detail(
      payload,
      account_id=account_id,
      instrument_code=instrument_code,
      client_order_id=client_order_id,
      broker_order_id=broker_order_id,
    ):
      continue

    durable_runtime_events = list(
      (
        await db.execute(
          select(StrategyRuntimeEvent)
          .where(
            StrategyRuntimeEvent.event_type.in_({"ORDER", "TRADE"}),
            or_(
              StrategyRuntimeEvent.client_order_id == client_order_id,
              StrategyRuntimeEvent.broker_order_id == broker_order_id,
            ),
          )
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    execution_announced = False
    for runtime_event in durable_runtime_events:
      if runtime_event.event_type == "TRADE":
        execution_announced = True
        break
      historical_order = dict(dict(runtime_event.payload or {}).get("report") or {})
      historical_fill = _reported_cumulative_fill(historical_order)
      if historical_fill is None and any(
        key in historical_order for key in ("traded_volume", "filled_volume")
      ):
        execution_announced = True
        break
      if int(historical_fill or 0) > 0:
        execution_announced = True
        break
      historical_projection = await _terminal_order_fill_projection(
        db,
        correlation,
        intent,
        current_order=historical_order,
      )
      if historical_projection and int(historical_projection["expected"]) > 0:
        execution_announced = True
        break
    if execution_announced:
      continue
    try:
      numeric_broker_order_id = int(broker_order_id)
    except (TypeError, ValueError, OverflowError):
      continue
    durable_trade = (
      await db.execute(
        select(Trade.id)
        .where(
          Trade.account_id == account_id,
          Trade.order_id == numeric_broker_order_id,
        )
        .limit(1)
      )
    ).scalar_one_or_none()
    if durable_trade is not None:
      continue

    audit = {
      "source": "QMT_PROTOCOL_1_1_FULL_SNAPSHOT",
      "snapshot_id": snapshot_id,
      "snapshot_hash": snapshot_hash,
      "snapshot_at": rollout.last_snapshot_at.isoformat(),
      "source_sequence": snapshot_sequence,
      "broker_terminal_status": terminal_status,
      "expected_filled_volume": 0,
      "received_execution_volume": 0,
      "reconciled_at": utcnow().isoformat(),
    }
    results.append(
      (
        "ORDER",
        {
          **order,
          "effective_order_status": "RECONCILED_ZERO_FILL",
          "effective_status_reason": ("QMT_FULL_SNAPSHOT_ZERO_FILL_RECONCILIATION"),
          "traded_volume": 0,
          "filled_volume": 0,
          "zero_fill_reconciliation": audit,
        },
      )
    )
  return results


async def _correlation_for_report(
  db,
  *,
  client_order_id: str,
  broker_order_id: str,
) -> Optional[StrategyOrderCorrelation]:
  clauses = []
  if client_order_id:
    clauses.append(StrategyOrderCorrelation.client_order_id == client_order_id)
  if broker_order_id:
    clauses.append(StrategyOrderCorrelation.broker_order_id == broker_order_id)
  if not clauses:
    return None
  return (
    await db.execute(select(StrategyOrderCorrelation).where(or_(*clauses)))
  ).scalar_one_or_none()


async def _command_expired_entry_zero_fill_reconciliation(
  db,
  correlation: StrategyOrderCorrelation,
  item: dict[str, Any],
) -> Optional[dict[str, Any]]:
  """Prove an Agent command-expiry error happened before Broker.execute()."""

  status = _normalized_order_status(
    item.get("effective_order_status") or item.get("status") or item.get("order_status")
  )
  reason = str(
    item.get("reason")
    or item.get("effective_status_reason")
    or item.get("status_msg")
    or ""
  ).strip()
  broker_order_id = str(
    item.get("order_id") or item.get("broker_order_id") or ""
  ).strip()
  if status != "EXPIRED" or reason.lower() != "command_expired" or broker_order_id:
    return None

  pending = await db.get(
    PendingTradeOrder,
    correlation.client_order_id,
    with_for_update=True,
  )
  intent = await db.get(
    TradeIntentRecord,
    correlation.intent_id,
    with_for_update=True,
  )
  if pending is None or intent is None:
    return None
  request_metadata = {
    **dict(pending.request_metadata or {}),
    **dict(correlation.request_metadata or {}),
  }
  intent_metadata = dict(intent.intent_metadata or {})
  plan_id = str(
    request_metadata.get("entry_plan_id") or intent_metadata.get("entry_plan_id") or ""
  ).strip()
  run_id = str(pending.strategy_run_id or "").strip()
  try:
    executed_volume = int(intent.executed_volume or 0)
    executed_price = Decimal(str(intent.executed_price or 0))
  except (TypeError, ValueError, ArithmeticError):
    return None
  if (
    not plan_id
    or not run_id
    or run_id != str(correlation.strategy_run_id or "")
    or run_id != str(intent.strategy_run_id or "")
    or str(pending.side or "").upper() != "BUY"
    or str(intent.direction or "").upper() != "BUY"
    or str(intent_metadata.get("entry_plan_id") or "") != plan_id
    or pending.broker_order_id
    or correlation.broker_order_id
    or str(pending.status or "").upper()
    not in {
      "QUEUED",
      "EXPIRED",
      "RECONCILE_REQUIRED",
      "RECONCILED_ZERO_FILL",
    }
    or executed_volume != 0
    or not executed_price.is_finite()
    or executed_price > 0
    or intent.executed_time is not None
  ):
    return None

  prior_events = list(
    (
      await db.execute(
        select(StrategyRuntimeEvent)
        .where(
          StrategyRuntimeEvent.client_order_id == correlation.client_order_id,
          StrategyRuntimeEvent.event_type.in_({"ORDER", "TRADE"}),
        )
        .with_for_update()
      )
    )
    .scalars()
    .all()
  )
  for prior_event in prior_events:
    if prior_event.event_type == "TRADE":
      return None
    prior_report = dict(dict(prior_event.payload or {}).get("report") or {})
    prior_fill = _reported_cumulative_fill(prior_report)
    if (
      prior_fill is None
      and any(key in prior_report for key in ("traded_volume", "filled_volume"))
    ) or int(prior_fill or 0) > 0:
      return None

  return {
    "source": "QMT_AGENT_COMMAND_EXPIRED_PRE_EXECUTION",
    "command_reason": reason,
    "command_message_id": str(
      dict(correlation.request_metadata or {}).get("command_message_id") or ""
    ),
    "expected_filled_volume": 0,
    "received_execution_volume": 0,
    "reconciled_at": utcnow().isoformat(),
  }


def _runtime_business_key(
  event_type: str,
  correlation: StrategyOrderCorrelation,
  item: dict[str, Any],
) -> str:
  broker_order_id = str(item.get("order_id") or item.get("broker_order_id") or "")
  if event_type == "TRADE":
    execution_id = str(item.get("execution_id") or item.get("traded_id") or "")
    if not execution_id:
      execution_id = (
        f"{broker_order_id}:{item.get('traded_time')}:"
        f"{item.get('traded_price')}:{item.get('traded_volume')}"
      )
    return f"trade:{correlation.account_id}:{execution_id}"[:192]
  cumulative_fill = _reported_cumulative_fill(item)
  fill_field_present = any(key in item for key in ("traded_volume", "filled_volume"))
  fill_component = (
    "INVALID"
    if cumulative_fill is None and fill_field_present
    else str(int(cumulative_fill or 0))
  )
  return (
    f"order:{correlation.client_order_id}:{broker_order_id}:"
    f"{_normalized_order_status(item.get('effective_order_status') or item.get('status') or item.get('order_status'))}:"
    f"{fill_component}"
  )[:192]


def _event_payload(
  correlation: StrategyOrderCorrelation,
  item: dict[str, Any],
  *,
  business_key: str,
) -> dict[str, Any]:
  metadata = {
    **dict(correlation.request_metadata or {}),
    "strategy_run_id": correlation.strategy_run_id,
    "strategy_order_id": correlation.strategy_order_id,
    "intent_id": correlation.intent_id,
    "t_batch_id": correlation.batch_id or "",
    "bucket": correlation.bucket,
    "t_trade_role": str(correlation.t_trade_role or "").lower(),
    "risk_decision_id": correlation.risk_decision_id or "",
    "trace_id": correlation.trace_id,
    "substitution_plan": correlation.substitution_plan,
    "execution_mode": correlation.execution_mode,
    "runtime_event_key": business_key,
  }
  zero_fill_reconciliation = item.get("zero_fill_reconciliation")
  if isinstance(zero_fill_reconciliation, dict):
    metadata["qmt_zero_fill_reconciliation"] = dict(zero_fill_reconciliation)
  return {"report": item, "metadata": metadata}


async def _project_trade_intent_event(
  db,
  correlation: StrategyOrderCorrelation,
  *,
  event_type: str,
  item: dict[str, Any],
) -> Optional[dict[str, Any]]:
  """Project one uniquely staged broker event into durable intent audit truth."""
  intent = await db.get(TradeIntentRecord, correlation.intent_id, with_for_update=True)
  if intent is None:
    return None
  intent.order_id = correlation.strategy_order_id or intent.order_id
  if correlation.risk_decision_id:
    intent.risk_decision_id = correlation.risk_decision_id
  if event_type == "ORDER":
    order_status = _normalized_order_status(
      item.get("effective_order_status")
      or item.get("status")
      or item.get("order_status")
    )
    projection = await _terminal_order_fill_projection(
      db,
      correlation,
      intent,
      current_order=item,
    )
    historical_projection = await _terminal_order_fill_projection(
      db,
      correlation,
      intent,
    )
    if historical_projection and (
      projection is None
      or int(historical_projection["expected"]) > int(projection["expected"])
    ):
      projection = historical_projection
    if order_status == "RECONCILED_ZERO_FILL":
      reconciliation = item.get("zero_fill_reconciliation")
      if isinstance(reconciliation, dict):
        intent.intent_metadata = {
          **dict(intent.intent_metadata or {}),
          "qmt_zero_fill_reconciliation": dict(reconciliation),
        }
      intent.status = order_status
      intent.notes = str(
        item.get("effective_status_reason")
        or "QMT_FULL_SNAPSHOT_ZERO_FILL_RECONCILIATION"
      )
    elif projection and int(projection["expected"]) > int(projection["received"]):
      intent.status = "RECONCILE_REQUIRED"
      intent.notes = _fill_projection_note(projection)
    else:
      intent.status = order_status
      intent.notes = (
        str(item.get("effective_status_reason") or item.get("status_msg") or "") or None
      )
    return projection

  fill_volume = max(0, int(item.get("traded_volume") or item.get("volume") or 0))
  fill_price = float(item.get("traded_price") or item.get("price") or 0.0)
  if fill_volume <= 0 or fill_price <= 0:
    return None
  previous_volume = max(0, int(intent.executed_volume or 0))
  previous_price = float(intent.executed_price or 0.0)
  total_volume = previous_volume + fill_volume
  intent.executed_volume = total_volume
  intent.executed_price = (
    previous_price * previous_volume + fill_price * fill_volume
  ) / total_volume
  intent.executed_time = to_naive_utc(
    _parse_report_time(item.get("traded_time") or item.get("trade_time"))
  )
  pending = await db.get(PendingTradeOrder, correlation.client_order_id)
  requested_volume = max(
    0,
    int((pending.volume if pending is not None else None) or intent.target_volume or 0),
  )
  projection = await _terminal_order_fill_projection(db, correlation, intent)
  if projection and int(projection["expected"]) > int(projection["received"]):
    intent.status = "RECONCILE_REQUIRED"
    intent.notes = _fill_projection_note(projection)
  elif projection:
    intent.status = str(projection["status"])
    intent.notes = str(projection.get("reason") or "") or None
  elif str(intent.status or "").upper() not in {"REJECTED", "CANCELLED", "EXPIRED"}:
    intent.status = (
      "FILLED"
      if requested_volume > 0 and total_volume >= requested_volume
      else "PARTIAL_FILLED"
    )
  return projection


async def _project_t_trade_event(
  batch: TTradeBatch,
  *,
  event_type: str,
  role: str,
  item: dict[str, Any],
  terminal_projection: Optional[dict[str, Any]] = None,
) -> None:
  previous_projection = (
    batch.status,
    batch.exception_reason,
    batch.entry_broker_order_id,
    batch.exit_broker_order_id,
    int(batch.entry_filled_volume or 0),
    float(batch.entry_avg_price or 0.0),
    int(batch.exit_filled_volume or 0),
    float(batch.exit_avg_price or 0.0),
  )
  broker_order_id = str(item.get("order_id") or item.get("broker_order_id") or "")
  if event_type == "ORDER":
    status = _normalized_order_status(
      item.get("effective_order_status")
      or item.get("status")
      or item.get("order_status")
    )
    if role == "ENTRY":
      batch.entry_broker_order_id = broker_order_id or batch.entry_broker_order_id
    elif role == "EXIT":
      batch.exit_broker_order_id = broker_order_id or batch.exit_broker_order_id
    if status == "RECONCILE_REQUIRED":
      batch.status = "RECONCILE_REQUIRED"
      batch.exception_reason = str(
        item.get("effective_status_reason")
        or item.get("status_msg")
        or "RECONCILE_REQUIRED"
      )
    elif terminal_projection and int(terminal_projection["expected"]) > int(
      terminal_projection["received"]
    ):
      batch.status = "RECONCILE_REQUIRED"
      batch.exception_reason = _fill_projection_note(terminal_projection)
    elif role == "ENTRY":
      batch.status = {
        "PENDING": "ENTRY_QUEUED",
        "SUBMITTED": "ENTRY_SUBMITTED",
        "PARTIAL_FILLED": "ENTRY_PARTIAL",
        "FILLED": "OPEN",
        "REJECTED": "ENTRY_REJECTED",
        "CANCELLED": "ENTRY_REJECTED",
        "EXPIRED": "ENTRY_EXPIRED",
      }.get(status, batch.status)
      batch.exception_reason = (
        str(item.get("effective_status_reason") or item.get("status_msg") or "") or None
      )
    elif role == "EXIT":
      batch.status = {
        "PENDING": "EXIT_TRIGGERED",
        "SUBMITTED": "EXIT_SUBMITTED",
        "PARTIAL_FILLED": "EXIT_PARTIAL",
        "FILLED": "CLOSED",
        "REJECTED": "EXIT_REJECTED",
        "CANCELLED": "EXIT_REJECTED",
        "EXPIRED": "EXIT_REJECTED",
      }.get(status, batch.status)
      if status in {"REJECTED", "CANCELLED", "EXPIRED"}:
        batch.exception_reason = str(
          item.get("effective_status_reason") or item.get("status_msg") or status
        )
      else:
        batch.exception_reason = None
  else:
    volume = max(0, int(item.get("traded_volume") or item.get("volume") or 0))
    price = float(item.get("traded_price") or item.get("price") or 0.0)
    if role == "ENTRY":
      previous = int(batch.entry_filled_volume or 0)
      total = previous + volume
      if total:
        batch.entry_avg_price = (
          float(batch.entry_avg_price or 0.0) * previous + price * volume
        ) / total
      batch.entry_filled_volume = total
      if terminal_projection and int(terminal_projection["expected"]) > int(
        terminal_projection["received"]
      ):
        batch.status = "RECONCILE_REQUIRED"
        batch.exception_reason = _fill_projection_note(terminal_projection)
      else:
        batch.status = (
          "OPEN" if total >= int(batch.target_volume or total) else "ENTRY_PARTIAL"
        )
        batch.exception_reason = None
    elif role == "EXIT":
      previous = int(batch.exit_filled_volume or 0)
      total = previous + volume
      if total:
        batch.exit_avg_price = (
          float(batch.exit_avg_price or 0.0) * previous + price * volume
        ) / total
      batch.exit_filled_volume = total
      if terminal_projection and int(terminal_projection["expected"]) > int(
        terminal_projection["received"]
      ):
        batch.status = "RECONCILE_REQUIRED"
        batch.exception_reason = _fill_projection_note(terminal_projection)
      else:
        batch.status = (
          "CLOSED"
          if total >= int(batch.entry_filled_volume or total)
          else "EXIT_PARTIAL"
        )
        batch.exception_reason = None
  current_projection = (
    batch.status,
    batch.exception_reason,
    batch.entry_broker_order_id,
    batch.exit_broker_order_id,
    int(batch.entry_filled_volume or 0),
    float(batch.entry_avg_price or 0.0),
    int(batch.exit_filled_volume or 0),
    float(batch.exit_avg_price or 0.0),
  )
  if current_projection != previous_projection:
    batch.version = int(batch.version or 0) + 1


async def _reconcile_t_trade_batch_after_runtime_event(
  db,
  event: StrategyRuntimeEvent,
) -> None:
  """Restore the staged batch projection after a transient apply failure."""
  payload = dict(event.payload or {})
  metadata = dict(payload.get("metadata") or {})
  report = dict(payload.get("report") or {})
  batch_id = str(metadata.get("t_batch_id") or metadata.get("batch_id") or "")
  role = str(metadata.get("t_trade_role") or "").strip().upper()
  if not batch_id or role not in {"ENTRY", "EXIT"}:
    return
  batch = await db.get(TTradeBatch, batch_id)
  if batch is None:
    return
  correlation = (
    await db.execute(
      select(StrategyOrderCorrelation).where(
        StrategyOrderCorrelation.client_order_id == event.client_order_id
      )
    )
  ).scalar_one_or_none()
  terminal_projection = None
  if correlation is not None:
    intent = await db.get(TradeIntentRecord, correlation.intent_id)
    if intent is not None:
      terminal_projection = await _terminal_order_fill_projection(
        db,
        correlation,
        intent,
        current_order=report if event.event_type == "ORDER" else None,
      )

  previous = (batch.status, batch.exception_reason)
  order_status = (
    _normalized_order_status(
      report.get("effective_order_status")
      or report.get("status")
      or report.get("order_status")
    )
    if event.event_type == "ORDER"
    else ""
  )
  if order_status == "RECONCILE_REQUIRED":
    batch.status = "RECONCILE_REQUIRED"
    batch.exception_reason = str(
      report.get("effective_status_reason")
      or report.get("status_msg")
      or metadata.get("approval_reason")
      or "RECONCILE_REQUIRED"
    )
  elif terminal_projection and int(terminal_projection["expected"]) > int(
    terminal_projection["received"]
  ):
    batch.status = "RECONCILE_REQUIRED"
    batch.exception_reason = _fill_projection_note(terminal_projection)
  elif role == "ENTRY":
    filled = max(0, int(batch.entry_filled_volume or 0))
    target = max(0, int(batch.target_volume or 0))
    if filled > 0:
      batch.status = "OPEN" if filled >= (target or filled) else "ENTRY_PARTIAL"
      batch.exception_reason = None
    elif event.event_type == "ORDER":
      batch.status = {
        "PENDING": "ENTRY_QUEUED",
        "SUBMITTED": "ENTRY_SUBMITTED",
        "PARTIAL_FILLED": "ENTRY_PARTIAL",
        "FILLED": "OPEN",
        "REJECTED": "ENTRY_REJECTED",
        "CANCELLED": "ENTRY_REJECTED",
        "EXPIRED": "ENTRY_EXPIRED",
      }.get(order_status, batch.status)
      batch.exception_reason = (
        str(report.get("effective_status_reason") or report.get("status_msg") or "")
        or None
      )
  else:
    exited = max(0, int(batch.exit_filled_volume or 0))
    entered = max(0, int(batch.entry_filled_volume or 0))
    if exited > 0:
      batch.status = "CLOSED" if exited >= (entered or exited) else "EXIT_PARTIAL"
      batch.exception_reason = None
    elif event.event_type == "ORDER":
      batch.status = {
        "PENDING": "EXIT_TRIGGERED",
        "SUBMITTED": "EXIT_SUBMITTED",
        "PARTIAL_FILLED": "EXIT_PARTIAL",
        "FILLED": "CLOSED",
        "REJECTED": "EXIT_REJECTED",
        "CANCELLED": "EXIT_REJECTED",
        "EXPIRED": "EXIT_REJECTED",
      }.get(order_status, batch.status)
      batch.exception_reason = (
        str(report.get("effective_status_reason") or report.get("status_msg") or "")
        or None
      )
  if previous != (batch.status, batch.exception_reason):
    batch.version = int(batch.version or 0) + 1


async def _insert_runtime_event(db, event: StrategyRuntimeEvent) -> None:
  async with db.begin_nested():
    db.add(event)
    await db.flush()


async def _stage_runtime_events(report: AgentReportInbox) -> None:
  from .strategy_manager import strategy_manager

  executor = strategy_manager.executor
  arm_barrier = getattr(
    executor,
    "arm_durable_event_barrier",
    None,
  )
  refresh_barrier = getattr(
    executor,
    "refresh_durable_event_barrier",
    None,
  )
  barrier_checked_runs: set[str] = set()
  affected_run_ids: set[str] = set()
  payload = dict(report.payload or {})
  try:
    db = AsyncSessionLocal()
    try:
      # Establish the outer transaction before any SAVEPOINT. Without this,
      # SQLite can release the first nested savepoint as a standalone commit,
      # breaking the event+projection atomicity exercised by local tests.
      await db.begin()
      runtime_items = _report_items(report)
      authoritative_identity = _authoritative_snapshot_identity(report)
      if authoritative_identity is not None:
        snapshot_id, snapshot_hash = authoritative_identity
        checkpoint_by_account: dict[str, bool] = {}
        filtered_runtime_items: list[tuple[str, dict[str, Any]]] = []
        for event_type, item in runtime_items:
          account_id = str(item.get("account_id") or "").strip()
          if not account_id:
            # A full snapshot item without an account cannot be safely tied to
            # a current account after a mixed-generation replay.
            continue
          if account_id not in checkpoint_by_account:
            rollout = await db.get(AccountExecutionControl, account_id)
            checkpoint_by_account[account_id] = bool(
              rollout is not None
              and str(rollout.last_snapshot_id or "") == snapshot_id
              and str(rollout.last_snapshot_hash or "") == snapshot_hash
            )
          if checkpoint_by_account[account_id]:
            filtered_runtime_items.append((event_type, item))
        runtime_items = filtered_runtime_items
      runtime_items.extend(await _full_snapshot_zero_fill_items(db, report))
      last_staged_at: Optional[datetime] = None
      for event_type, raw_item in runtime_items:
        item = dict(raw_item)
        broker_order_id = str(item.get("order_id") or item.get("broker_order_id") or "")
        client_order_id = str(
          item.get("client_order_id")
          or report.client_order_id
          or payload.get("client_order_id")
          or ""
        )
        correlation = await _correlation_for_report(
          db,
          client_order_id=client_order_id,
          broker_order_id=broker_order_id,
        )
        if correlation is None:
          continue
        command_expiry_reconciliation = (
          await _command_expired_entry_zero_fill_reconciliation(
            db,
            correlation,
            item,
          )
        )
        if command_expiry_reconciliation is not None:
          item.update(
            {
              "effective_order_status": "RECONCILED_ZERO_FILL",
              "effective_status_reason": "command_expired",
              "traded_volume": 0,
              "filled_volume": 0,
              "zero_fill_reconciliation": command_expiry_reconciliation,
            }
          )
        run_id = correlation.strategy_run_id
        affected_run_ids.add(run_id)
        if arm_barrier is not None and run_id not in barrier_checked_runs:
          earliest_backlog_key = (
            await db.execute(
              select(StrategyRuntimeEvent.business_key)
              .where(
                StrategyRuntimeEvent.strategy_run_id == run_id,
                StrategyRuntimeEvent.application_status != "APPLIED",
              )
              .order_by(
                StrategyRuntimeEvent.created_at,
                StrategyRuntimeEvent.event_id,
              )
              .limit(1)
            )
          ).scalar_one_or_none()
          if earliest_backlog_key:
            arm_barrier(run_id, earliest_backlog_key)
          barrier_checked_runs.add(run_id)

        if event_type == "ORDER":
          proposed_status = _normalized_order_status(
            item.get("effective_order_status")
            or item.get("status")
            or item.get("order_status")
          )
          if proposed_status not in _SPECIAL_RUNTIME_ORDER_STATUSES:
            pending = await db.get(
              PendingTradeOrder,
              correlation.client_order_id,
              with_for_update=True,
            )
            if pending is not None:
              source_sequence = max(
                0,
                int(
                  item.get("source_sequence")
                  or payload.get("source_sequence")
                  or payload.get("sequence")
                  or 0
                ),
              )
              stored_sequence = max(0, int(pending.last_source_sequence or 0))
              if (
                source_sequence
                and stored_sequence
                and source_sequence < stored_sequence
              ) or not can_transition_order_status(
                pending.status,
                proposed_status,
              ):
                continue
          item["effective_order_status"] = proposed_status

        if broker_order_id and not correlation.broker_order_id:
          correlation.broker_order_id = broker_order_id
        business_key = _runtime_business_key(event_type, correlation, item)
        existing = (
          await db.execute(
            select(StrategyRuntimeEvent).where(
              StrategyRuntimeEvent.business_key == business_key
            )
          )
        ).scalar_one_or_none()
        if existing is not None:
          if existing.application_status != "APPLIED" and arm_barrier is not None:
            arm_barrier(existing.strategy_run_id, existing.business_key)
          continue
        created_at = utcnow()
        if last_staged_at is not None and created_at <= last_staged_at:
          created_at = last_staged_at + timedelta(microseconds=1)
        last_staged_at = created_at
        runtime_event = StrategyRuntimeEvent(
          event_id=str(uuid.uuid4()),
          business_key=business_key,
          strategy_run_id=run_id,
          client_order_id=correlation.client_order_id,
          broker_order_id=broker_order_id or None,
          event_type=event_type,
          payload=_event_payload(
            correlation,
            item,
            business_key=business_key,
          ),
          application_status="PENDING",
          application_attempts=0,
          created_at=created_at,
        )
        if arm_barrier is not None:
          arm_barrier(run_id, business_key)
        try:
          await _insert_runtime_event(db, runtime_event)
        except IntegrityError:
          # Another producer won the durable business-key race. Its event and
          # projections are authoritative; do not apply this report twice.
          if arm_barrier is not None:
            arm_barrier(run_id, business_key)
          continue
        terminal_projection = await _project_trade_intent_event(
          db,
          correlation,
          event_type=event_type,
          item=item,
        )
        if correlation.batch_id:
          batch = await db.get(TTradeBatch, correlation.batch_id)
          if batch is not None:
            await _project_t_trade_event(
              batch,
              event_type=event_type,
              role=str(correlation.t_trade_role or "").upper(),
              item=item,
              terminal_projection=terminal_projection,
            )
      await db.commit()
    except Exception:
      await db.rollback()
      raise
    finally:
      await db.close()
  except Exception as exc:
    if refresh_barrier is not None:
      for run_id in affected_run_ids:
        try:
          await refresh_barrier(run_id)
        except Exception as refresh_exc:
          logger.error(
            "Failed to reconcile durable barrier after staging rollback: "
            "run_id=%s error=%s",
            run_id,
            refresh_exc,
          )
    raise RetryableReportError(str(exc)) from exc

  if refresh_barrier is not None:
    for run_id in affected_run_ids:
      await refresh_barrier(run_id)


async def _apply_runtime_event(event: StrategyRuntimeEvent) -> None:
  from .strategy_manager import strategy_manager

  payload = dict(event.payload or {})
  report = dict(payload.get("report") or {})
  metadata = {
    **dict(payload.get("metadata") or {}),
    "runtime_event_key": event.business_key,
  }
  order_id = str(metadata.get("strategy_order_id") or "")
  side = str(report.get("side") or report.get("order_type") or "").upper()
  order_type = OrderType.SELL if side in {"SELL", "24", "ORDER_SELL"} else OrderType.BUY
  if event.event_type == "ORDER":
    request = OrderRequest(
      instrument_code=str(
        report.get("stock_code")
        or report.get("instrument_code")
        or metadata.get("instrument_code")
        or ""
      ),
      order_type=order_type,
      price_type=PriceType.LIMIT,
      volume=int(
        report.get("order_volume")
        or report.get("volume")
        or metadata.get("requested_entry_volume")
        or 0
      ),
      price=float(report.get("price") or report.get("limit_price") or 0.0),
      metadata=metadata,
    )
    status_name = _normalized_order_status(
      report.get("effective_order_status")
      or report.get("status")
      or report.get("order_status")
    )
    order_status: OrderStatus | str = (
      status_name
      if status_name in _SPECIAL_RUNTIME_ORDER_STATUSES
      else OrderStatus[status_name]
    )
    order = OrderResponse(
      order_id=order_id,
      request=request,
      status=order_status,
      submit_time=_parse_report_time(
        report.get("order_time") or report.get("submit_time")
      ),
      filled_volume=int(report.get("traded_volume") or 0),
      filled_amount=float(report.get("traded_price") or 0.0)
      * int(report.get("traded_volume") or 0),
      avg_price=float(report.get("traded_price") or 0.0),
      error_message=str(report.get("status_msg") or ""),
      last_update_time=_parse_report_time(
        report.get("updated_at") or report.get("order_time")
      ),
    )
    await strategy_manager.executor.apply_durable_order_report(
      event.strategy_run_id,
      order,
    )
    return

  price = float(report.get("traded_price") or report.get("price") or 0.0)
  volume = int(report.get("traded_volume") or report.get("volume") or 0)
  trade = TradeRecord(
    trade_id=str(
      report.get("execution_id") or report.get("traded_id") or event.business_key
    ),
    order_id=order_id,
    instrument_code=str(
      report.get("stock_code")
      or report.get("instrument_code")
      or metadata.get("instrument_code")
      or ""
    ),
    trade_type=order_type,
    price=price,
    volume=volume,
    amount=price * volume,
    commission=0.0,
    trade_time=_parse_report_time(
      report.get("traded_time") or report.get("trade_time")
    ),
    metadata=metadata,
  )
  await strategy_manager.executor.apply_durable_trade_report(
    event.strategy_run_id,
    trade,
  )


async def _drain_runtime_events() -> None:
  async with _runtime_event_drain_lock:
    # The previous drain may have applied/checkpointed an event and then failed
    # to open the compensating session that returns it to PENDING. Reclaim such
    # rows on every active/idle pass rather than only after an Engine restart.
    await _recover_stuck_runtime_events(
      application_error="recovered before Engine runtime-event drain"
    )
    await _drain_runtime_events_locked()


async def _drain_runtime_events_locked() -> None:
  from .strategy_executor import RuntimeConsumerUnavailable
  from .strategy_manager import strategy_manager

  blocked_run_ids: set[str] = set()
  first_retry_error: Optional[RetryableReportError] = None
  while True:
    async with AsyncSessionLocal() as db:
      earlier_event = aliased(StrategyRuntimeEvent)
      unapplied_earlier_event = (
        select(earlier_event.event_id)
        .where(
          earlier_event.strategy_run_id == StrategyRuntimeEvent.strategy_run_id,
          earlier_event.application_status != "APPLIED",
          or_(
            earlier_event.created_at < StrategyRuntimeEvent.created_at,
            and_(
              earlier_event.created_at == StrategyRuntimeEvent.created_at,
              earlier_event.event_id < StrategyRuntimeEvent.event_id,
            ),
          ),
        )
        .exists()
      )
      statement = select(StrategyRuntimeEvent).where(
        StrategyRuntimeEvent.application_status == "PENDING",
        ~unapplied_earlier_event,
      )
      if blocked_run_ids:
        statement = statement.where(
          StrategyRuntimeEvent.strategy_run_id.not_in(blocked_run_ids)
        )
      event = (
        await db.execute(
          statement.order_by(
            StrategyRuntimeEvent.created_at,
            StrategyRuntimeEvent.event_id,
          )
          .limit(1)
          .with_for_update(skip_locked=True)
        )
      ).scalar_one_or_none()
      if event is None:
        if first_retry_error is not None:
          raise first_retry_error
        return
      require_consumer = getattr(
        strategy_manager.executor,
        "require_durable_event_consumer",
        None,
      )
      if require_consumer is not None:
        try:
          require_consumer(event.strategy_run_id)
        except RuntimeConsumerUnavailable:
          blocked_run_ids.add(event.strategy_run_id)
          continue
      prior_attempts = int(event.application_attempts or 0)
      prior_error = event.application_error
      event.application_status = "PROCESSING"
      event.application_attempts = prior_attempts + 1
      await db.commit()
      event_id = event.event_id
      event_run_id = event.strategy_run_id
    try:
      async with AsyncSessionLocal() as db:
        event = await db.get(StrategyRuntimeEvent, event_id)
        if event is None:
          continue
        arm_barrier = getattr(
          strategy_manager.executor,
          "arm_durable_event_barrier",
          None,
        )
        if arm_barrier is not None:
          arm_barrier(event.strategy_run_id, event.business_key)
        await _apply_runtime_event(event)
        await _reconcile_t_trade_batch_after_runtime_event(db, event)
        event.application_status = "APPLIED"
        event.applied_at = utcnow()
        event.application_error = None
        await db.commit()
        advance_barrier = getattr(
          strategy_manager.executor,
          "advance_durable_event_barrier",
          None,
        )
        if advance_barrier is not None:
          await advance_barrier(
            event.strategy_run_id,
            event.business_key,
          )
    except RuntimeConsumerUnavailable:
      # Pause/stop/startup gaps are expected availability states, not failed
      # applications. Leave the event untouched for resume and keep draining
      # other runs without violating this run's event order.
      async with AsyncSessionLocal() as db:
        event = await db.get(StrategyRuntimeEvent, event_id)
        if event is not None:
          event.application_status = "PENDING"
          event.application_attempts = prior_attempts
          event.application_error = prior_error
          await db.commit()
      blocked_run_ids.add(event_run_id)
      continue
    except Exception as exc:
      async with AsyncSessionLocal() as db:
        event = await db.get(StrategyRuntimeEvent, event_id)
        if event is not None:
          event.application_status = "PENDING"
          event.application_error = str(exc)[:2000]
          event_metadata = dict(dict(event.payload or {}).get("metadata") or {})
          batch_id = str(
            event_metadata.get("t_batch_id") or event_metadata.get("batch_id") or ""
          )
          if batch_id:
            batch = await db.get(TTradeBatch, batch_id)
            if batch is not None and batch.status != "CLOSED":
              batch.status = "RECONCILE_REQUIRED"
              batch.exception_reason = f"策略运行时事件应用失败：{str(exc)[:1000]}"
          await db.commit()
      blocked_run_ids.add(event_run_id)
      if first_retry_error is None:
        first_retry_error = RetryableReportError(str(exc))
        first_retry_error.__cause__ = exc
      continue


def _broker_order_ids(report: AgentReportInbox) -> list[int]:
  """Return the persisted orders touched by a successfully converged report."""
  payload = report.payload or {}
  values: list[Any] = []
  if report.message_type == "order_report":
    values.append(_body(payload, "order").get("order_id"))
    values.append(_body(payload, "order").get("broker_order_id"))
  elif report.message_type == "execution_report":
    values.append(_body(payload, "execution").get("order_id"))
    values.append(_body(payload, "execution").get("broker_order_id"))
  elif report.message_type == "delta_report":
    for item in [*(payload.get("orders") or []), *(payload.get("trades") or [])]:
      values.extend([item.get("order_id"), item.get("broker_order_id")])

  order_ids: list[int] = []
  for value in values:
    if value is None:
      continue
    try:
      order_id = int(value)
    except (TypeError, ValueError):
      continue
    if order_id not in order_ids:
      order_ids.append(order_id)
  return order_ids


async def _supersede_obsolete_pending_full_snapshots(
  db,
  oldest: AgentReportInbox,
) -> int:
  """Coalesce queued full snapshots while preserving interleaved deltas.

  A newer structurally verified full snapshot subsumes older full snapshots
  from the same personal-account Agent. Only full snapshots are superseded;
  order, execution, and position deltas retain FIFO processing before the
  newer snapshot is claimed.
  """

  payload = dict(oldest.payload or {})
  if (
    oldest.message_type != "delta_report"
    or str(oldest.protocol_version or "") != "1.1"
    or payload.get("is_complete") is not True
  ):
    return 0

  newer = await db.scalar(
    select(AgentReportInbox)
    .where(
      AgentReportInbox.device_id == oldest.device_id,
      AgentReportInbox.message_type == "delta_report",
      AgentReportInbox.protocol_version == "1.1",
      AgentReportInbox.processing_status == "PENDING",
      AgentReportInbox.received_at > oldest.received_at,
      or_(
        AgentReportInbox.next_attempt_at.is_(None),
        AgentReportInbox.next_attempt_at <= utcnow(),
      ),
      AgentReportInbox.payload["is_complete"].as_boolean().is_(True),
    )
    .order_by(AgentReportInbox.received_at.desc())
    .limit(1)
  )
  if newer is None or _authoritative_snapshot_identity(newer) is None:
    return 0

  superseded_at = utcnow()
  result = await db.execute(
    update(AgentReportInbox)
    .where(
      AgentReportInbox.device_id == oldest.device_id,
      AgentReportInbox.message_type == "delta_report",
      AgentReportInbox.protocol_version == "1.1",
      AgentReportInbox.processing_status == "PENDING",
      AgentReportInbox.received_at < newer.received_at,
      AgentReportInbox.payload["is_complete"].as_boolean().is_(True),
    )
    .values(
      processing_status="SUPERSEDED",
      processing_error="superseded by newer verified complete snapshot",
      processed_at=superseded_at,
      next_attempt_at=None,
    )
  )
  superseded_count = int(result.rowcount or 0)
  if superseded_count:
    await db.commit()
    logger.info(
      "Coalesced obsolete Agent full snapshots: device_id=%s count=%s "
      "newest_message_id=%s",
      oldest.device_id,
      superseded_count,
      newer.message_id,
    )
  return superseded_count


async def _claim() -> Optional[str]:
  now = utcnow()
  async with AsyncSessionLocal() as db:
    while True:
      result = await db.execute(
        select(AgentReportInbox)
        .where(
          AgentReportInbox.processing_status == "PENDING",
          or_(
            AgentReportInbox.next_attempt_at.is_(None),
            AgentReportInbox.next_attempt_at <= now,
          ),
        )
        .order_by(AgentReportInbox.received_at)
        .limit(1)
        .with_for_update(skip_locked=True)
      )
      report = result.scalar_one_or_none()
      if report is None:
        return None
      if await _supersede_obsolete_pending_full_snapshots(db, report):
        now = utcnow()
        continue
      report.processing_status = "PROCESSING"
      report.processing_attempts = (report.processing_attempts or 0) + 1
      await db.commit()
      return report.message_id


async def _recover_stuck_reports() -> None:
  """Return all interrupted claims after this Engine acquires its singleton lease."""
  async with AsyncSessionLocal() as db:
    await db.execute(
      update(AgentReportInbox)
      .where(AgentReportInbox.processing_status == "PROCESSING")
      .values(
        processing_status="PENDING",
        next_attempt_at=utcnow(),
        processing_error="recovered after Engine restart",
      )
    )
    await db.commit()


async def _recover_stuck_runtime_events(
  *,
  application_error: str = "recovered after Engine restart",
) -> None:
  async with AsyncSessionLocal() as db:
    await db.execute(
      update(StrategyRuntimeEvent)
      .where(StrategyRuntimeEvent.application_status == "PROCESSING")
      .values(
        application_status="PENDING",
        application_error=application_error,
      )
    )
    await db.commit()


async def _supersede_prior_complete_snapshot_failures(
  db,
  report: AgentReportInbox,
  *,
  resolved_at: datetime,
) -> int:
  """Close obsolete full-snapshot dead letters after newer state converges.

  Complete protocol 1.1 snapshots are authoritative account-state checkpoints.
  Once a newer checkpoint for the same device and accounts succeeds, an older
  failed complete snapshot no longer represents an unresolved state gap. The
  raw report and its error remain stored with a SUPERSEDED audit status.
  """
  payload = dict(report.payload or {})
  current_accounts = _report_account_ids(payload)
  if (
    report.message_type != "delta_report"
    or str(report.protocol_version or "") != "1.1"
    or not bool(payload.get("is_complete"))
    or not current_accounts
  ):
    return 0
  failures = list(
    (
      await db.execute(
        select(AgentReportInbox).where(
          AgentReportInbox.device_id == report.device_id,
          AgentReportInbox.message_id != report.message_id,
          AgentReportInbox.message_type == "delta_report",
          AgentReportInbox.protocol_version == "1.1",
          AgentReportInbox.processing_status == "FAILED",
          AgentReportInbox.received_at <= report.received_at,
        )
      )
    )
    .scalars()
    .all()
  )
  superseded = [
    item
    for item in failures
    if (covered_accounts := _report_account_ids(dict(item.payload or {})))
    and covered_accounts.issubset(current_accounts)
    and bool(dict(item.payload or {}).get("is_complete"))
  ]
  if not superseded:
    return 0
  message_ids = [item.message_id for item in superseded]
  for item in superseded:
    item.processing_status = "SUPERSEDED"
    item.processed_at = resolved_at
    item.next_attempt_at = None
  await db.execute(
    update(OperationalAlert)
    .where(
      OperationalAlert.code == "AGENT_REPORT_DEAD_LETTER",
      OperationalAlert.business_id.in_(message_ids),
      OperationalAlert.status != "RESOLVED",
    )
    .values(
      status="RESOLVED",
      resolved_by="SYSTEM_RECONCILIATION",
      resolved_at=resolved_at,
      resolution=("后续协议 1.1 完整账户快照已成功收敛；旧失败快照已由权威状态取代"),
    )
  )
  return len(superseded)


async def _finish(
  message_id: str,
  *,
  error: Optional[Exception] = None,
) -> None:
  async with AsyncSessionLocal() as db:
    report = await db.get(AgentReportInbox, message_id)
    if report is None:
      return
    if error is None:
      finished_at = utcnow()
      report.processing_status = "PROCESSED"
      report.processed_at = finished_at
      report.processing_error = None
      report.next_attempt_at = None
      superseded_count = await _supersede_prior_complete_snapshot_failures(
        db,
        report,
        resolved_at=finished_at,
      )
      if superseded_count:
        logger.info(
          "Authoritative Agent snapshot superseded %s prior dead letters",
          superseded_count,
        )
    else:
      attempts = int(report.processing_attempts or 1)
      report.processing_error = str(error)[:2000]
      if attempts >= 10 or not isinstance(error, RetryableReportError):
        report.processing_status = "FAILED"
        account_ids = sorted(_report_account_ids(dict(report.payload or {})))
        for account_id in account_ids or [None]:
          await OperationalAlertService(db).raise_alert(
            severity="SEV2",
            source="ENGINE",
            code="AGENT_REPORT_DEAD_LETTER",
            account_id=account_id,
            business_id=report.message_id,
            message=(
              f"Agent report 永久失败：{report.message_type} / "
              f"{error.__class__.__name__}"
            ),
            details={
              "message_id": report.message_id,
              "message_type": report.message_type,
              "protocol_version": report.protocol_version,
              "payload_hash": report.raw_payload_hash,
              "attempts": attempts,
              "error_class": error.__class__.__name__,
              "error": str(error)[:2000],
            },
            commit=False,
          )
      else:
        report.processing_status = "PENDING"
        report.next_attempt_at = utcnow() + timedelta(seconds=min(60, 2**attempts))
    await db.commit()


async def _wait_for_database_retry(
  stopped: asyncio.Event,
  *,
  delay: float,
) -> bool:
  """Return true when shutdown was requested while waiting to retry."""

  try:
    await asyncio.wait_for(stopped.wait(), timeout=delay)
  except asyncio.TimeoutError:
    return False
  return True


async def _recover_consumer_state(stopped: asyncio.Event) -> bool:
  """Recover interrupted inbox work without exiting on pool contention."""

  delay = _DATABASE_CONTENTION_RETRY_SECONDS
  while not stopped.is_set():
    try:
      await _recover_stuck_reports()
      await _recover_stuck_runtime_events()
      return True
    except SQLAlchemyTimeoutError as exc:
      logger.warning(
        "Engine report recovery deferred by database pool contention: "
        "retry_in=%.2fs error=%s",
        delay,
        exc,
      )
      if await _wait_for_database_retry(stopped, delay=delay):
        return False
      delay = min(delay * 2, _DATABASE_CONTENTION_MAX_RETRY_SECONDS)
  return False


async def _finish_with_database_retry(
  stopped: asyncio.Event,
  message_id: str,
  *,
  error: Optional[Exception] = None,
) -> bool:
  """Persist report completion without crashing on a busy shared pool."""

  delay = _DATABASE_CONTENTION_RETRY_SECONDS
  while not stopped.is_set():
    try:
      await _finish(message_id, error=error)
      return True
    except SQLAlchemyTimeoutError as exc:
      logger.warning(
        "Engine report completion deferred by database pool contention: "
        "message_id=%s retry_in=%.2fs error=%s",
        message_id,
        delay,
        exc,
      )
      if await _wait_for_database_retry(stopped, delay=delay):
        return False
      delay = min(delay * 2, _DATABASE_CONTENTION_MAX_RETRY_SECONDS)
  return False


async def _open_wakeup_subscription() -> Optional[RedisChannelSubscription]:
  try:
    return await redis_pubsub.open_subscription(AGENT_REPORT_WAKE_CHANNEL)
  except Exception as exc:
    logger.debug(
      "Agent report Redis wake-up unavailable; using database polling: %s",
      exc.__class__.__name__,
    )
    return None


async def _wait_for_work(
  stopped: asyncio.Event,
  subscription: Optional[RedisChannelSubscription],
) -> Optional[RedisChannelSubscription]:
  if subscription is None:
    try:
      await asyncio.wait_for(stopped.wait(), timeout=1.0)
    except asyncio.TimeoutError:
      pass
    return await _open_wakeup_subscription()
  try:
    await subscription.wait_for_message(timeout=1.0)
    return subscription
  except Exception as exc:
    logger.debug(
      "Agent report Redis wake-up interrupted; using database polling: %s",
      exc.__class__.__name__,
    )
    try:
      await subscription.close()
    except Exception:
      pass
    return None


async def _refresh_runtime_event_barriers() -> None:
  from .strategy_manager import strategy_manager

  refresh = getattr(
    strategy_manager.executor,
    "refresh_armed_durable_event_barriers",
    None,
  )
  if refresh is not None:
    await refresh()


async def run_report_consumer(stopped: asyncio.Event) -> None:
  if not await _recover_consumer_state(stopped):
    return
  subscription = await _open_wakeup_subscription()
  retry_delay = _DATABASE_CONTENTION_RETRY_SECONDS
  try:
    while not stopped.is_set():
      try:
        message_id = await _claim()
      except SQLAlchemyTimeoutError as exc:
        logger.warning(
          "Engine report claim deferred by database pool contention: "
          "retry_in=%.2fs error=%s",
          retry_delay,
          exc,
        )
        if await _wait_for_database_retry(stopped, delay=retry_delay):
          return
        retry_delay = min(
          retry_delay * 2,
          _DATABASE_CONTENTION_MAX_RETRY_SECONDS,
        )
        continue
      retry_delay = _DATABASE_CONTENTION_RETRY_SECONDS
      if message_id is None:
        try:
          await _refresh_runtime_event_barriers()
          await _drain_runtime_events()
        except Exception as exc:
          logger.debug(
            "Durable runtime barrier refresh/drain deferred: %s",
            exc,
          )
          pass
        subscription = await _wait_for_work(stopped, subscription)
        continue
      try:
        async with AsyncSessionLocal() as db:
          report = await db.get(AgentReportInbox, message_id)
          if report is None:
            continue
          db.expunge(report)
        # Do not pin an inbox read transaction while projections perform their
        # own database work. The detached row contains only eagerly loaded
        # scalar columns and is immutable for this processing attempt.
        await _process(report)
        await _stage_runtime_events(report)
        await _drain_runtime_events()
        events = [
          {
            "message_id": report.message_id,
            "message_type": report.message_type,
            "client_order_id": report.client_order_id,
            "broker_order_id": order_id,
          }
          for order_id in _broker_order_ids(report)
        ]
        if not await _finish_with_database_retry(stopped, message_id):
          return
        for event in events:
          try:
            await redis_pubsub.publish(TRADING_EVENT_CHANNEL, event)
          except Exception as exc:
            logger.debug("Redis wake-up failed: %s", exc.__class__.__name__)
      except SQLAlchemyTimeoutError as exc:
        retryable_error = RetryableReportError(
          f"database pool contention while applying Agent report: {exc}"
        )
        logger.warning(
          "Agent report deferred by database pool contention: "
          "message_id=%s error=%s",
          message_id,
          exc,
        )
        if not await _finish_with_database_retry(
          stopped,
          message_id,
          error=retryable_error,
        ):
          return
      except Exception as exc:
        logger.warning(
          "Agent report processing failed: message_id=%s error=%s",
          message_id,
          exc,
        )
        if not await _finish_with_database_retry(
          stopped,
          message_id,
          error=exc,
        ):
          return
  finally:
    if subscription is not None:
      try:
        await subscription.close()
      except Exception:
        pass
