"""Idempotent convergence from the durable QMT Agent report inbox."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from hashlib import md5, sha256
from typing import Any, Optional

from quantx_contracts import (
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
  AccountTradingRollout,
  AccountTradingRolloutEvent,
  AgentReportInbox,
  OperationalAlert,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyOrderCorrelation,
  StrategyRuntimeEvent,
  TTradeBatch,
)
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.repositories.account_repository import AccountRepository
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.operational_alert_service import (
  OperationalAlertService,
)
from quantx_infrastructure.services.order_service import OrderService
from quantx_infrastructure.services.position_service import PositionService
from quantx_infrastructure.services.runtime_subscription_bridge import (
  TRADING_EVENT_CHANNEL,
)
from quantx_infrastructure.services.trade_service import TradeService
from sqlalchemy import or_, select, update

logger = logging.getLogger(__name__)

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
  "UNKNOWN_BROKER_ORDER",
  "UNKNOWN_BROKER_TRADE",
}


class RetryableReportError(RuntimeError):
  pass


def _snapshot_can_promote_heartbeat(status: Any) -> bool:
  """Only reconciliation snapshots may promote an Agent heartbeat.

  A delayed snapshot must never mask a newer runtime failure such as a lost
  XTTrading or XTData connection.
  """
  return (
    str(status or "").strip().upper()
    in _SNAPSHOT_PROMOTABLE_HEARTBEAT_STATUSES
  )


def _body(payload: dict[str, Any], key: str) -> dict[str, Any]:
  nested = payload.get(key)
  return dict(nested) if isinstance(nested, dict) else dict(payload)


def _report_account_ids(payload: dict[str, Any]) -> set[str]:
  """Return every funding account covered by one Agent report payload."""
  account_ids: set[str] = set()

  def add(value: Any) -> None:
    normalized = str(value or "").strip()
    if normalized:
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
    for item in payload.get(collection_name) or []:
      if isinstance(item, dict):
        add(item.get("account_id"))
  positions_by_account = payload.get("positions_by_account")
  if isinstance(positions_by_account, dict):
    for account_id in positions_by_account:
      add(account_id)
  return account_ids


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
    proposed_status = (
      str(pending.status or "PENDING")
      if cancel_rejected
      else _normalized_order_status(status)
    )
    stored_sequence = int(pending.last_source_sequence or 0)
    sequence = max(0, int(source_sequence or 0))
    stale_sequence = bool(sequence and stored_sequence and sequence < stored_sequence)
    transition_allowed = (
      not stale_sequence
      and can_transition_order_status(pending.status, proposed_status)
    )
    if transition_allowed:
      pending.status = proposed_status[:24]
      if sequence:
        pending.last_source_sequence = sequence
      if source_event_at is not None:
        pending.last_source_event_at = to_naive_utc(source_event_at)
    pending.broker_order_id = broker_order_id or pending.broker_order_id
    if stale_sequence:
      pending.status_reason = "ignored stale broker report"
    elif cancel_rejected:
      pending.status_reason = (reason or "cancel rejected")[:256]
    elif not transition_allowed:
      pending.status_reason = (
        f"ignored non-monotonic status {proposed_status}"
      )[:256]
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
    sequence = max(0, int(source_sequence or 0))
    stored_sequence = int(pending.last_source_sequence or 0)
    if (
      (not sequence or not stored_sequence or sequence >= stored_sequence)
      and can_transition_order_status(pending.status, proposed_status)
    ):
      pending.status = proposed_status[:24]
      if sequence:
        pending.last_source_sequence = sequence
      if source_event_at is not None:
        pending.last_source_event_at = to_naive_utc(source_event_at)
    pending.status_reason = reason[:256] or None
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
    reason=str(
      order.get("effective_status_reason") or order.get("status_msg") or ""
    ),
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
    float(trade.get("traded_price") or 0)
    * int(trade.get("traded_volume") or 0),
  )
  if not trade.get("traded_id"):
    raise ValueError("execution_report 缺少 execution id")
  await TradeService(str(trade.get("account_id", ""))).upsert_report(trade)
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


async def _process_delta_report(
  device_id: str,
  payload: dict[str, Any],
  *,
  protocol_version: str = "1.0",
) -> None:
  is_complete = bool(payload.get("is_complete", True))
  authoritative = is_complete and protocol_version == "1.1"
  snapshot_id = str(payload.get("snapshot_id") or "")
  snapshot_hash = str(payload.get("snapshot_hash") or "")
  if authoritative:
    if not snapshot_id or len(snapshot_hash) != 64:
      raise ValueError("完整账户快照缺少协议 1.1 身份")
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
      raise ValueError("完整账户快照哈希校验失败")

  for order in payload.get("orders") or []:
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
    await _upsert_account(dict(account))
  for error in payload.get("order_errors") or []:
    reason = str(error.get("error_msg") or error.get("reason") or "")
    client_order_id = str(error.get("client_order_id") or "")
    broker_order_id = str(
      error.get("order_id") or error.get("broker_order_id") or ""
    )
    if client_order_id:
      await _update_pending(
        client_order_id,
        status="REJECTED",
        reason=reason,
      )
    else:
      await _update_pending_by_broker(
        broker_order_id,
        status="REJECTED",
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

  sequence = int(
    payload.get("source_sequence")
    or payload.get("sequence")
    or time_utils.now().timestamp() * 1_000_000
  )
  reported_at = _parse_report_time(payload.get("source_event_at"))
  if is_complete:
    groups_value = payload.get("positions_by_account")
    if isinstance(groups_value, dict):
      groups = groups_value.items()
    else:
      account_id = str(payload.get("account_id") or "")
      groups = (
        [(account_id, payload.get("positions") or [])]
        if account_id
        else []
      )
    for account_id, positions in groups:
      await PositionService().apply_full_snapshot(
        account_id=str(account_id),
        positions=list(positions),
        sequence=sequence,
        reported_at=reported_at,
        source="QMT_AGENT",
        is_complete=True,
      )
  else:
    default_account_id = str(payload.get("account_id") or "")
    deltas = payload.get("position_deltas")
    if deltas is None:
      deltas = payload.get("positions") or []
    for position in deltas:
      value = dict(position)
      account_id = str(value.get("account_id") or default_account_id)
      if not account_id:
        raise ValueError("持仓增量缺少 account_id")
      await PositionService().apply_position_delta(value, account_id)

  if is_complete and protocol_version == "1.1":
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
      async with AsyncSessionLocal() as db:
        existing_rollout = await db.get(AccountTradingRollout, account_id)
        controlled_window_active = bool(
          existing_rollout and existing_rollout.controlled_window_active
        )
        acknowledged_external_order_ids = {
          str(value)
          for value in list(
            existing_rollout.controlled_window_external_order_ids or []
          )
        } if existing_rollout else set()
        acknowledged_external_trade_ids = {
          str(value)
          for value in list(
            existing_rollout.controlled_window_external_trade_ids or []
          )
        } if existing_rollout else set()
        allow_external_activity = bool(
          existing_rollout is None
          or (
            not existing_rollout.enabled
            and not existing_rollout.kill_switch
            and str(existing_rollout.stage).upper() in {"SHADOW", "PAUSED"}
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
          AccountTradingRollout,
          account_id,
          with_for_update=True,
        )
        if rollout is None:
          rollout = AccountTradingRollout(account_id=account_id)
          db.add(rollout)
        rollout.last_snapshot_id = snapshot_id or None
        rollout.last_snapshot_hash = snapshot_hash or None
        rollout.last_snapshot_at = to_naive_utc(reported_at)
        rollout.reconcile_status = (
          "READY"
          if authoritative and not discrepancies
          else "RECONCILE_REQUIRED"
        )
        if not authoritative:
          discrepancies.insert(
            0,
            {
              "kind": "PROTOCOL_1_1_REQUIRED",
              "business_id": account_id,
            },
          )
        if discrepancies:
          window_was_active = bool(rollout.controlled_window_active)
          previous_stage = str(rollout.stage)
          rollout.enabled = False
          if not rollout.kill_switch:
            rollout.stage = "PAUSED"
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
              AccountTradingRolloutEvent(
                event_id=str(uuid.uuid4()),
                account_id=account_id,
                event_type="CONTROLLED_WINDOW_INVALIDATED",
                previous_stage=previous_stage,
                next_stage=str(rollout.stage),
                snapshot_id=snapshot_id or None,
                details={"discrepancies": discrepancies[:20]},
                created_at=utcnow(),
              )
            )
          blocked_accounts.append(account_id)
        else:
          if (
            str(rollout.stage).upper() == "PAUSED"
            and _was_automatic_reconciliation_pause(rollout.paused_reason)
          ):
            # A recovered automatic pause returns to the read-only preparation
            # stage. It never silently resumes CANARY/LIVE order authority.
            rollout.stage = "SHADOW"
            rollout.enabled = False
            rollout.paused_reason = None
          ready_accounts.append(account_id)
        reconciliation_accounts[account_id] = {
          "snapshotId": snapshot_id,
          "snapshotAt": reported_at.isoformat(),
          "status": rollout.reconcile_status,
          "manualCoexistence": allow_external_activity,
          "externalOrderCount": len(reconciliation["external_orders"]),
          "externalTradeCount": len(reconciliation["external_trades"]),
          "newExternalOrderCount": sum(
            not bool(item.get("acknowledged"))
            for item in reconciliation["external_orders"]
          ),
          "newExternalTradeCount": sum(
            not bool(item.get("acknowledged"))
            for item in reconciliation["external_trades"]
          ),
          "workingExternalOrderCount": sum(
            str(item.get("status") or "")
            in {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}
            for item in reconciliation["external_orders"]
          ),
          "controlledWindowActive": bool(rollout.controlled_window_active),
          "blockingDiscrepancyCount": len(discrepancies),
        }
        await db.commit()

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
            "snapshotId": snapshot_id,
            "snapshotHash": snapshot_hash,
            "snapshotAt": reported_at.isoformat(),
            "readyAccounts": ready_accounts,
            "blockedAccounts": blocked_accounts,
            "accountReconciliation": account_details,
          }
        )
        heartbeat.details = details
        if _snapshot_can_promote_heartbeat(heartbeat.status):
          heartbeat.status = (
            "READY" if not blocked_accounts else "RECONCILE_REQUIRED"
          )
        heartbeat.updated_at = utcnow()
        await db.commit()


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
      await db.execute(
        select(PendingTradeOrder).where(
          PendingTradeOrder.account_id == account_id
        )
      )
    ).scalars().all()
  by_client = {str(item.client_order_id): item for item in pending}
  by_broker = {
    str(item.broker_order_id): item
    for item in pending
    if item.broker_order_id
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
      and str(item.status).upper()
      in {"SUBMITTED", "PARTIAL_FILLED", "PENDING"}
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


def _report_items(report: AgentReportInbox) -> list[tuple[str, dict[str, Any]]]:
  payload = dict(report.payload or {})
  if report.message_type == "order_report":
    return [("ORDER", _body(payload, "order"))]
  if report.message_type == "execution_report":
    return [("TRADE", _body(payload, "execution"))]
  if report.message_type == "delta_report":
    rejected_orders = []
    for error in payload.get("order_errors") or []:
      rejected_orders.append(
        {
          **dict(error),
          "order_status": "REJECTED",
          "status": "REJECTED",
          "status_msg": error.get("error_msg") or error.get("reason") or "",
          "broker_order_id": error.get("broker_order_id")
          or error.get("order_id"),
        }
      )
    return [
      *(("ORDER", dict(item)) for item in payload.get("orders") or []),
      *(("TRADE", dict(item)) for item in payload.get("trades") or []),
      *(("ORDER", item) for item in rejected_orders),
    ]
  return []


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


def _runtime_business_key(
  event_type: str,
  correlation: StrategyOrderCorrelation,
  item: dict[str, Any],
) -> str:
  broker_order_id = str(
    item.get("order_id") or item.get("broker_order_id") or ""
  )
  if event_type == "TRADE":
    execution_id = str(item.get("execution_id") or item.get("traded_id") or "")
    if not execution_id:
      execution_id = (
        f"{broker_order_id}:{item.get('traded_time')}:"
        f"{item.get('traded_price')}:{item.get('traded_volume')}"
      )
    return f"trade:{correlation.account_id}:{execution_id}"[:192]
  return (
    f"order:{correlation.client_order_id}:{broker_order_id}:"
    f"{_normalized_order_status(item.get('effective_order_status') or item.get('status') or item.get('order_status'))}:"
    f"{int(item.get('traded_volume') or 0)}"
  )[:192]


def _event_payload(
  correlation: StrategyOrderCorrelation,
  item: dict[str, Any],
) -> dict[str, Any]:
  metadata = {
    **dict(correlation.request_metadata or {}),
    "strategy_run_id": correlation.strategy_run_id,
    "strategy_order_id": correlation.strategy_order_id,
    "intent_id": correlation.intent_id,
    "t_batch_id": correlation.batch_id or "",
    "bucket": correlation.bucket,
    "t_trade_role": correlation.t_trade_role or "",
    "risk_decision_id": correlation.risk_decision_id or "",
    "trace_id": correlation.trace_id,
    "substitution_plan": correlation.substitution_plan,
    "execution_mode": correlation.execution_mode,
  }
  return {"report": item, "metadata": metadata}


async def _project_t_trade_event(
  batch: TTradeBatch,
  *,
  event_type: str,
  role: str,
  item: dict[str, Any],
) -> None:
  broker_order_id = str(
    item.get("order_id") or item.get("broker_order_id") or ""
  )
  if event_type == "ORDER":
    status = _normalized_order_status(
      item.get("effective_order_status")
      or item.get("status")
      or item.get("order_status")
    )
    if role == "ENTRY":
      batch.entry_broker_order_id = broker_order_id or batch.entry_broker_order_id
      batch.status = {
        "PENDING": "ENTRY_QUEUED",
        "SUBMITTED": "ENTRY_SUBMITTED",
        "PARTIAL_FILLED": "ENTRY_PARTIAL",
        "FILLED": "OPEN",
        "REJECTED": "ENTRY_REJECTED",
        "CANCELLED": "ENTRY_REJECTED",
        "EXPIRED": "ENTRY_EXPIRED",
      }.get(status, batch.status)
    elif role == "EXIT":
      batch.exit_broker_order_id = broker_order_id or batch.exit_broker_order_id
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
          item.get("effective_status_reason")
          or item.get("status_msg")
          or status
        )
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
      batch.status = (
        "OPEN" if total >= int(batch.target_volume or total) else "ENTRY_PARTIAL"
      )
    elif role == "EXIT":
      previous = int(batch.exit_filled_volume or 0)
      total = previous + volume
      if total:
        batch.exit_avg_price = (
          float(batch.exit_avg_price or 0.0) * previous + price * volume
        ) / total
      batch.exit_filled_volume = total
      batch.status = (
        "CLOSED"
        if total >= int(batch.entry_filled_volume or total)
        else "EXIT_PARTIAL"
      )
    batch.version = int(batch.version or 0) + 1


async def _stage_runtime_events(report: AgentReportInbox) -> None:
  async with AsyncSessionLocal() as db:
    for event_type, item in _report_items(report):
      broker_order_id = str(
        item.get("order_id") or item.get("broker_order_id") or ""
      )
      client_order_id = str(
        item.get("client_order_id")
        or report.client_order_id
        or report.payload.get("client_order_id")
        or ""
      )
      correlation = await _correlation_for_report(
        db,
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
      )
      if correlation is None:
        continue
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
        continue
      db.add(
        StrategyRuntimeEvent(
          event_id=str(uuid.uuid4()),
          business_key=business_key,
          strategy_run_id=correlation.strategy_run_id,
          client_order_id=correlation.client_order_id,
          broker_order_id=broker_order_id or None,
          event_type=event_type,
          payload=_event_payload(correlation, item),
          application_status="PENDING",
          application_attempts=0,
          created_at=utcnow(),
        )
      )
      if correlation.batch_id:
        batch = await db.get(TTradeBatch, correlation.batch_id)
        if batch is not None:
          await _project_t_trade_event(
            batch,
            event_type=event_type,
            role=str(correlation.t_trade_role or "").upper(),
            item=item,
          )
    await db.commit()


async def _apply_runtime_event(event: StrategyRuntimeEvent) -> None:
  from .strategy_manager import strategy_manager

  payload = dict(event.payload or {})
  report = dict(payload.get("report") or {})
  metadata = dict(payload.get("metadata") or {})
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
      report.get("status") or report.get("order_status")
    )
    order = OrderResponse(
      order_id=order_id,
      request=request,
      status=OrderStatus[status_name],
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
      report.get("execution_id")
      or report.get("traded_id")
      or event.business_key
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
  while True:
    async with AsyncSessionLocal() as db:
      event = (
        await db.execute(
          select(StrategyRuntimeEvent)
          .where(StrategyRuntimeEvent.application_status == "PENDING")
          .order_by(StrategyRuntimeEvent.created_at)
          .limit(1)
          .with_for_update(skip_locked=True)
        )
      ).scalar_one_or_none()
      if event is None:
        return
      event.application_status = "PROCESSING"
      event.application_attempts = int(event.application_attempts or 0) + 1
      await db.commit()
      event_id = event.event_id
    try:
      async with AsyncSessionLocal() as db:
        event = await db.get(StrategyRuntimeEvent, event_id)
        if event is None:
          continue
        await _apply_runtime_event(event)
        event.application_status = "APPLIED"
        event.applied_at = utcnow()
        event.application_error = None
        await db.commit()
    except Exception as exc:
      async with AsyncSessionLocal() as db:
        event = await db.get(StrategyRuntimeEvent, event_id)
        if event is not None:
          event.application_status = "PENDING"
          event.application_error = str(exc)[:2000]
          if event.batch_id:
            batch = await db.get(TTradeBatch, event.batch_id)
            if batch is not None and batch.status != "CLOSED":
              batch.status = "RECONCILE_REQUIRED"
              batch.exception_status = "RECONCILE_REQUIRED"
              batch.exception_reason = (
                f"策略运行时事件应用失败：{str(exc)[:1000]}"
              )
          await db.commit()
      raise RetryableReportError(str(exc)) from exc


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


async def _claim() -> Optional[str]:
  now = utcnow()
  async with AsyncSessionLocal() as db:
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


async def _recover_stuck_runtime_events() -> None:
  async with AsyncSessionLocal() as db:
    await db.execute(
      update(StrategyRuntimeEvent)
      .where(StrategyRuntimeEvent.application_status == "PROCESSING")
      .values(
        application_status="PENDING",
        application_error="recovered after Engine restart",
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
    ).scalars().all()
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
      resolution=(
        "后续协议 1.1 完整账户快照已成功收敛；旧失败快照已由权威状态取代"
      ),
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
        report.next_attempt_at = utcnow() + timedelta(
          seconds=min(60, 2**attempts)
        )
    await db.commit()


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


async def run_report_consumer(stopped: asyncio.Event) -> None:
  await _recover_stuck_reports()
  await _recover_stuck_runtime_events()
  subscription = await _open_wakeup_subscription()
  try:
    while not stopped.is_set():
      message_id = await _claim()
      if message_id is None:
        try:
          await _drain_runtime_events()
        except RetryableReportError:
          pass
        subscription = await _wait_for_work(stopped, subscription)
        continue
      try:
        async with AsyncSessionLocal() as db:
          report = await db.get(AgentReportInbox, message_id)
          if report is None:
            continue
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
        await _finish(message_id)
        for event in events:
          try:
            await redis_pubsub.publish(TRADING_EVENT_CHANNEL, event)
          except Exception as exc:
            logger.debug("Redis wake-up failed: %s", exc.__class__.__name__)
      except Exception as exc:
        logger.warning(
          "Agent report processing failed: message_id=%s error=%s",
          message_id,
          exc,
        )
        await _finish(message_id, error=exc)
  finally:
    if subscription is not None:
      try:
        await subscription.close()
      except Exception:
        pass
