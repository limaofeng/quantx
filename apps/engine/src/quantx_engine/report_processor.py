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
  AgentReportInbox,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyOrderCorrelation,
  StrategyRuntimeEvent,
  TTradeBatch,
)
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.repositories.account_repository import AccountRepository
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
    order.get("status") or order.get("order_status") or "SUBMITTED"
  )
  await _update_pending(
    str(payload.get("client_order_id") or "") or None,
    status=status,
    broker_order_id=str(broker_order_id),
    reason=str(order.get("status_msg") or ""),
    source_sequence=int(payload.get("source_sequence") or 0),
    source_event_at=_parse_report_time(payload.get("source_event_at")),
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
    if client_order_id:
      await _update_pending(
        client_order_id,
        status="REJECTED",
        reason=reason,
      )
    else:
      await _update_pending_by_broker(
        error.get("order_id") or error.get("broker_order_id"),
        status="REJECTED",
        reason=reason,
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
      discrepancies = await _snapshot_discrepancies(account_id, payload)
      async with AsyncSessionLocal() as db:
        rollout = await db.get(AccountTradingRollout, account_id)
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
          rollout.enabled = False
          if not rollout.kill_switch:
            rollout.stage = "PAUSED"
          rollout.paused_reason = json.dumps(
            discrepancies[:20],
            ensure_ascii=False,
            default=str,
          )[:2000]
          blocked_accounts.append(account_id)
        else:
          ready_accounts.append(account_id)
        await db.commit()

    async with AsyncSessionLocal() as db:
      heartbeat = await db.get(
        RuntimeComponentHeartbeat,
        f"qmt-agent:{device_id}",
      )
      if heartbeat is not None and _snapshot_can_promote_heartbeat(
        heartbeat.status
      ):
        heartbeat.status = (
          "READY" if not blocked_accounts else "RECONCILE_REQUIRED"
        )
        details = dict(heartbeat.details or {})
        details.update(
          {
            "snapshotId": snapshot_id,
            "snapshotHash": snapshot_hash,
            "snapshotAt": reported_at.isoformat(),
            "readyAccounts": ready_accounts,
            "blockedAccounts": blocked_accounts,
          }
        )
        heartbeat.details = details
        heartbeat.updated_at = utcnow()
        await db.commit()


async def _snapshot_discrepancies(
  account_id: str,
  payload: dict[str, Any],
) -> list[dict[str, str]]:
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
  seen_broker_ids: set[str] = set()
  for order in snapshot_orders:
    client_id = str(order.get("client_order_id") or "")
    broker_id = str(order.get("order_id") or order.get("broker_order_id") or "")
    if broker_id:
      seen_broker_ids.add(broker_id)
    if not by_client.get(client_id) and not by_broker.get(broker_id):
      discrepancies.append(
        {
          "kind": "UNKNOWN_BROKER_ORDER",
          "business_id": broker_id or client_id or "unknown",
        }
      )
  for trade in snapshot_trades:
    client_id = str(trade.get("client_order_id") or "")
    broker_id = str(trade.get("order_id") or trade.get("broker_order_id") or "")
    if not by_client.get(client_id) and not by_broker.get(broker_id):
      discrepancies.append(
        {
          "kind": "UNKNOWN_BROKER_TRADE",
          "business_id": str(
            trade.get("execution_id")
            or trade.get("traded_id")
            or broker_id
            or "unknown"
          ),
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
  return discrepancies


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
    f"{_normalized_order_status(item.get('status') or item.get('order_status'))}:"
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
      item.get("status") or item.get("order_status")
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
        batch.exception_reason = str(item.get("status_msg") or status)
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
      report.processing_status = "PROCESSED"
      report.processed_at = utcnow()
      report.processing_error = None
      report.next_attempt_at = None
    else:
      attempts = int(report.processing_attempts or 1)
      report.processing_error = str(error)[:2000]
      if attempts >= 10 or not isinstance(error, RetryableReportError):
        report.processing_status = "FAILED"
        payload = dict(report.payload or {})
        body = (
          payload.get("execution")
          or payload.get("order")
          or payload
        )
        account_id = (
          str(body.get("account_id") or "")
          if isinstance(body, dict)
          else ""
        )
        await OperationalAlertService(db).raise_alert(
          severity="SEV2",
          source="ENGINE",
          code="AGENT_REPORT_DEAD_LETTER",
          account_id=account_id or None,
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
