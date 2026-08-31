"""Account-wide LIVE capacity, evaluated under the execution-control row lock.

Broker free balances already exclude orders present in that exact snapshot.
Only commands absent from it are additional reservations. A later fill does not
release that reservation against an older snapshot: the cash has been spent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from quantx_contracts import snapshot_account_authority_is_authoritative
from quantx_domain.clock import to_naive_utc
from quantx_domain.trading.exit_plan import estimate_buy_fee_cny
from sqlalchemy import or_, select

from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AgentReportInbox,
  PendingTradeOrder,
  TTradeBatch,
)
from quantx_infrastructure.models.enums import OrderType
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  AutoExitPlanRepository,
)
from quantx_infrastructure.services.agent_session_guard import (
  AGENT_SERVER_SESSION_PAYLOAD_KEY,
)

_TERMINAL = {
  "FILLED",
  "CANCELLED",
  "CANCELED",
  "REJECTED",
  "EXPIRED",
  "RECONCILED_ZERO_FILL",
}


async def load_authoritative_account_snapshot(
  db: Any,
  control: AccountExecutionControl,
) -> dict[str, Any]:
  """Read the immutable, processed snapshot named by the locked account gate."""
  snapshot_id = str(control.last_snapshot_id or "")
  expected_hash = str(control.last_snapshot_hash or "").lower()
  report = await db.scalar(
    select(AgentReportInbox)
    .where(
      AgentReportInbox.message_type == "delta_report",
      AgentReportInbox.protocol_version == "1.1",
      AgentReportInbox.processing_status == "PROCESSED",
      AgentReportInbox.payload["snapshot_id"].as_string() == snapshot_id,
      AgentReportInbox.payload["snapshot_hash"].as_string() == expected_hash,
    )
    .order_by(AgentReportInbox.received_at.desc())
    .limit(1)
  )
  payload = dict(report.payload or {}) if report is not None else {}
  hash_input = {
    key: value
    for key, value in payload.items()
    if key not in {"snapshot_hash", AGENT_SERVER_SESSION_PAYLOAD_KEY}
  }
  actual_hash = hashlib.sha256(
    json.dumps(
      hash_input,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode()
  ).hexdigest()
  account_id = str(control.account_id)
  sections = dict(payload.get("section_completeness_by_account") or {}).get(
    account_id, {}
  )
  authority = dict(payload.get("snapshot_authority_by_account") or {}).get(account_id)
  if (
    not snapshot_id
    or actual_hash != expected_hash
    or payload.get("is_complete") is not True
    or not snapshot_account_authority_is_authoritative(authority)
    or account_id not in dict(payload.get("positions_by_account") or {})
    or account_id in list(payload.get("unavailable_accounts") or [])
    or not all(
      sections.get(section) is True
      for section in ("account", "positions", "orders", "trades")
    )
    or len(
      [
        item
        for item in payload.get("accounts", [])
        if item.get("account_id") == account_id
      ]
    )
    != 1
  ):
    raise ValueError("ACCOUNT_CAPACITY_SNAPSHOT_UNAVAILABLE:最新完整账户快照不可用")
  return payload


def _decimal(value: Any) -> Decimal:
  result = Decimal(str(value or 0))
  if not result.is_finite() or result < 0:
    raise ValueError("ACCOUNT_CAPACITY_INVALID:账户容量数据无效")
  return result


def buy_cash_required(price: Any, volume: int) -> Decimal:
  amount = _decimal(price) * max(0, volume)
  if amount <= 0:
    raise ValueError("ACCOUNT_CAPACITY_PRICE_REQUIRED:买单必须提供资金预留价格上限")
  return amount + Decimal(str(estimate_buy_fee_cny(price=float(price), volume=volume)))


@dataclass(frozen=True)
class AccountCapacity:
  snapshot_id: str
  available_cash: Decimal
  available_volume: int
  unclaimed_volume: int


class AccountCapacityService:
  def __init__(self, db: Any) -> None:
    self.db = db

  async def read(
    self,
    control: AccountExecutionControl,
    *,
    instrument_code: str,
    own_plan_id: str = "",
    own_batch_id: str = "",
  ) -> AccountCapacity:
    payload = await load_authoritative_account_snapshot(self.db, control)
    account_id = str(control.account_id)
    await self.db.scalar(
      select(Position)
      .where(
        Position.account_id == account_id,
        Position.stock_code == instrument_code,
      )
      .with_for_update()
    )
    account = next(
      item for item in payload["accounts"] if item["account_id"] == account_id
    )
    position = next(
      (
        item
        for item in payload["positions_by_account"][account_id]
        if str(item.get("stock_code") or "") == instrument_code
      ),
      {},
    )
    available_cash = _decimal(account.get("cash"))
    available_volume = max(0, int(position.get("can_use_volume") or 0))
    observed_orders = {
      str(item.get("order_id") or item.get("broker_order_id") or "")
      for item in payload.get("orders", [])
      if item.get("account_id") == account_id
    } - {""}
    observed_clients = {
      str(item.get("client_order_id") or "")
      for item in payload.get("orders", [])
      if item.get("account_id") == account_id
    } - {""}
    snapshot_at = to_naive_utc(control.last_snapshot_at)
    trading_day_start = (snapshot_at + timedelta(hours=8)).replace(
      hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(hours=8)
    pending = list(
      (
        await self.db.scalars(
          select(PendingTradeOrder).where(
            PendingTradeOrder.account_id == account_id,
            PendingTradeOrder.execution_mode == "live",
            or_(
              PendingTradeOrder.status.notin_(_TERMINAL),
              PendingTradeOrder.updated_at >= trading_day_start,
            ),
          )
        )
      ).all()
    )
    broker_ids = {str(item.broker_order_id) for item in pending if item.broker_order_id}
    numeric_broker_ids = {int(value) for value in broker_ids if value.isdecimal()}
    trades = (
      list(
        (
          await self.db.scalars(
            select(Trade).where(
              Trade.account_id == account_id,
              Trade.order_id.in_(numeric_broker_ids),
            )
          )
        ).all()
      )
      if numeric_broker_ids
      else []
    )
    filled: dict[str, int] = {}
    for trade in trades:
      key = str(trade.order_id)
      filled[key] = filled.get(key, 0) + max(0, int(trade.volume or 0))
    remaining: dict[str, int] = {}
    for item in pending:
      broker_id = str(item.broker_order_id or "")
      status = str(item.status or "").upper()
      observed = (
        broker_id in observed_orders or item.client_order_id in observed_clients
      )
      local_zero_fill = status == "RECONCILED_ZERO_FILL" or (
        not broker_id and status in {"CANCELLED", "CANCELED", "REJECTED", "EXPIRED"}
      )
      # ORDER terminal counters are not fills. Keep an unobserved terminal
      # claim until a complete snapshot or persisted TRADE facts cover it.
      remaining[item.client_order_id] = (
        0
        if local_zero_fill or (observed and status in _TERMINAL - {"FILLED"})
        else max(0, int(item.volume) - filled.get(broker_id, 0))
      )
      if observed or local_zero_fill:
        continue
      # Reserve the full accepted quantity until the complete snapshot covers
      # it, including fills since the snapshot. Never refund on command ACK.
      if str(item.side).upper() == "BUY":
        available_cash -= buy_cash_required(item.limit_price, int(item.volume))
      elif item.instrument_code == instrument_code:
        available_volume -= int(item.volume)

    # Broker-originated orders may arrive outside QuantX. Unobserved ones must
    # also consume capacity; correlated orders above are counted only once.
    external = list(
      (
        await self.db.scalars(
          select(Order).where(
            Order.account_id == account_id,
            Order.time >= trading_day_start,
          )
        )
      ).all()
    )
    for item in external:
      if str(item.id) in observed_orders | broker_ids:
        continue
      if item.type == OrderType.BUY:
        available_cash -= buy_cash_required(item.price, int(item.volume))
      elif item.stock_code == instrument_code:
        available_volume -= int(item.volume)

    batches = list(
      (
        await self.db.scalars(
          select(TTradeBatch).where(
            TTradeBatch.account_id == account_id,
            TTradeBatch.instrument_code == instrument_code,
            TTradeBatch.execution_mode == "live",
          )
        )
      ).all()
    )
    claims = 0
    for batch in batches:
      if batch.batch_id == own_batch_id:
        continue
      working_entry = sum(
        remaining[item.client_order_id]
        for item in pending
        if item.batch_id == batch.batch_id and str(item.t_trade_role).upper() == "ENTRY"
      )
      working_exit = sum(
        remaining[item.client_order_id]
        for item in pending
        if item.batch_id == batch.batch_id and str(item.t_trade_role).upper() == "EXIT"
      )
      entry_filled = max(
        int(batch.entry_filled_volume or 0),
        sum(
          filled.get(str(item.broker_order_id or ""), 0)
          for item in pending
          if item.batch_id == batch.batch_id
          and str(item.t_trade_role).upper() == "ENTRY"
        ),
      )
      exit_filled = max(
        int(batch.exit_filled_volume or 0),
        sum(
          filled.get(str(item.broker_order_id or ""), 0)
          for item in pending
          if item.batch_id == batch.batch_id
          and str(item.t_trade_role).upper() == "EXIT"
        ),
      )
      claims += max(
        0,
        entry_filled + working_entry - exit_filled - working_exit,
      )
    plans = await AutoExitPlanRepository(self.db).find_reserving(
      account_id=account_id,
      instrument_code=instrument_code,
    )
    batch_ids = {batch.batch_id for batch in batches}
    for plan in plans:
      if plan.plan_id == own_plan_id or (
        plan.source_type == "T_TRADE_BATCH" and plan.source_id in batch_ids
      ):
        continue
      working_exit = sum(
        remaining[item.client_order_id]
        for item in pending
        if str(dict(item.request_metadata or {}).get("exit_plan_id") or "")
        == plan.plan_id
        and str(item.side).upper() == "SELL"
      )
      claims += max(0, int(plan.remaining_volume or 0) - working_exit)
    return AccountCapacity(
      snapshot_id=str(control.last_snapshot_id),
      available_cash=max(Decimal(0), available_cash),
      available_volume=max(0, available_volume),
      unclaimed_volume=max(0, available_volume - claims),
    )
