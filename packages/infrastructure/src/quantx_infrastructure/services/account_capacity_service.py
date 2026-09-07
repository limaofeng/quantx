"""Account-wide LIVE capacity and stable obligation watermark.

Broker free balances already exclude orders present in that exact snapshot.
Only commands absent from it are additional reservations. A later fill does not
release that reservation against an older snapshot: the cash has been spent.

The final order transaction uses ``lock_rows=True``.  The admission dispatcher
uses the read-only preview before it has ranked and durably claimed the READY
set; it must not enter the account execution-control lock first.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any, Mapping

from quantx_contracts import (
  PROTOCOL_VERSION,
  ExecutionEnvironment,
  snapshot_account_authority_is_authoritative,
)
from quantx_domain.clock import to_naive_utc
from quantx_domain.trading.exit_plan import estimate_buy_fee_cny
from sqlalchemy import or_, select

from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AgentReportInbox,
  PendingTradeOrder,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.enums import OrderType
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
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
      AgentReportInbox.protocol_version == PROTOCOL_VERSION,
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
  obligation_watermark: str = ""
  available_by_bucket: Mapping[str, int] = field(default_factory=dict)
  unclaimed_by_bucket: Mapping[str, int] = field(default_factory=dict)
  protected_old_position_floor: int = 0
  old_inventory_claim_allocation: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class OldInventoryClaimResult:
  claimable_volume: int
  unclaimed_volume: int
  protected_floor: int
  allocation: Mapping[str, int]
  unclaimed_by_bucket: Mapping[str, int]


def allocate_old_inventory_claims(
  *,
  available_by_bucket: Mapping[str, int],
  required_claim_qty: int,
  protected_core_floor: int = 0,
  allow_core_claim: bool = False,
) -> OldInventoryClaimResult:
  """Apply the frozen swing -> core -> never locked_core claim order."""

  available = {
    name: max(0, int(available_by_bucket.get(name, 0) or 0))
    for name in ("locked_core", "core", "swing")
  }
  protected_core = max(0, int(protected_core_floor or 0))
  core_claimable = (
    max(0, available["core"] - protected_core) if allow_core_claim else 0
  )
  claimable = available["swing"] + core_claimable
  remaining_claim = max(0, int(required_claim_qty or 0))
  swing_claim = min(remaining_claim, available["swing"])
  remaining_claim -= swing_claim
  core_claim = min(remaining_claim, core_claimable)
  remaining_claim -= core_claim
  if remaining_claim > 0:
    raise ValueError(
      "T_TRADE_BUCKET_CAPACITY_EXCEEDED:旧仓认领将侵占保护底仓或 locked_core"
    )
  return OldInventoryClaimResult(
    claimable_volume=claimable,
    unclaimed_volume=max(0, claimable - int(required_claim_qty or 0)),
    protected_floor=available["locked_core"] + min(
      protected_core, available["core"]
    ),
    allocation={"swing": swing_claim, "core": core_claim, "locked_core": 0},
    unclaimed_by_bucket={
      "swing": max(0, available["swing"] - swing_claim),
      "core": max(0, core_claimable - core_claim),
      "locked_core": 0,
    },
  )


def _bucket_available_volume(value: Any) -> int:
  if isinstance(value, Mapping):
    return max(0, int(value.get("available_volume", 0) or 0))
  return max(0, int(value or 0))


def _obligation_watermark(payload: Mapping[str, Any]) -> str:
  return hashlib.sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode()
  ).hexdigest()


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
    bucket_inventory: Mapping[str, Any] | None = None,
    protected_core_floor: int = 0,
    allow_core_claim: bool = False,
    lock_rows: bool = True,
  ) -> AccountCapacity:
    account_id = str(control.account_id)
    locked_control = await self.db.get(
      AccountExecutionControl,
      account_id,
      with_for_update=lock_rows,
    )
    if locked_control is None:
      raise ValueError("ACCOUNT_CAPACITY_CONTROL_MISSING:账户执行控制不存在")
    if (
      str(locked_control.last_snapshot_id or "") != str(control.last_snapshot_id or "")
      or str(locked_control.last_snapshot_hash or "").lower()
      != str(control.last_snapshot_hash or "").lower()
    ):
      raise ValueError("ACCOUNT_CAPACITY_SNAPSHOT_CHANGED:账户快照水位已变化")
    payload = await load_authoritative_account_snapshot(self.db, locked_control)
    position_stmt = select(Position).where(
        Position.account_id == account_id,
        Position.stock_code == instrument_code,
      )
    await self.db.scalar(
      position_stmt.with_for_update() if lock_rows else position_stmt
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
    snapshot_at = to_naive_utc(locked_control.last_snapshot_at)
    trading_day_start = (snapshot_at + timedelta(hours=8)).replace(
      hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(hours=8)
    pending_stmt = (
      select(PendingTradeOrder).where(
        PendingTradeOrder.account_id == account_id,
        PendingTradeOrder.environment == ExecutionEnvironment.LIVE.value,
        or_(
          PendingTradeOrder.status.notin_(_TERMINAL),
          PendingTradeOrder.updated_at >= trading_day_start,
        ),
      ).order_by(PendingTradeOrder.client_order_id)
    )
    pending = list(
      (
        await self.db.scalars(
          pending_stmt.with_for_update() if lock_rows else pending_stmt
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
    external_stmt = select(Order).where(
      Order.account_id == account_id,
      Order.time >= trading_day_start,
    ).order_by(Order.id)
    external = list(
      (
        await self.db.scalars(
          external_stmt.with_for_update() if lock_rows else external_stmt
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

    batch_stmt = select(TTradeBatch).where(
      TTradeBatch.account_id == account_id,
      TTradeBatch.environment == ExecutionEnvironment.LIVE.value,
    ).order_by(TTradeBatch.batch_id)
    account_batches = list(
      (
        await self.db.scalars(
          batch_stmt.with_for_update() if lock_rows else batch_stmt
        )
      ).all()
    )
    batches = [
      item
      for item in account_batches
      if str(item.instrument_code or "").upper() == instrument_code.upper()
    ]
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
      for_update=lock_rows,
      execution_mode="live",
    )
    plan_stmt = (
      select(AutoExitPlanRecord)
      .where(
        AutoExitPlanRecord.account_id == account_id,
        AutoExitPlanRecord.environment == ExecutionEnvironment.LIVE.value,
        AutoExitPlanRecord.status.in_(
          ("PENDING_ENTRY", "ACTIVE", "EXIT_PENDING", "PARTIALLY_EXITED", "PAUSED", "ERROR")
        ),
      )
      .order_by(
        AutoExitPlanRecord.instrument_code,
        AutoExitPlanRecord.created_at,
        AutoExitPlanRecord.plan_id,
      )
    )
    account_plans = list(
      (
        await self.db.scalars(
          plan_stmt.with_for_update() if lock_rows else plan_stmt
        )
      ).all()
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
        if str(item.owner_type or "").upper() == "EXIT_PLAN"
        and str(item.owner_id or "") == str(plan.plan_id)
        and str(item.side).upper() == "SELL"
      )
      claims += max(0, int(plan.remaining_volume or 0) - working_exit)
    outbox_stmt = (
      select(TradeCommandOutbox)
      .where(
        TradeCommandOutbox.account_id == account_id,
        TradeCommandOutbox.environment == ExecutionEnvironment.LIVE.value,
        TradeCommandOutbox.delivery_status.notin_(
          ("ACKNOWLEDGED", "FAILED", "CANCELLED", "EXPIRED")
        ),
      )
      .order_by(TradeCommandOutbox.message_id)
    )
    outbox = list(
      (
        await self.db.scalars(
          outbox_stmt.with_for_update() if lock_rows else outbox_stmt
        )
      ).all()
    )
    ready_stmt = (
      select(TradeIntentRecord)
      .where(
        TradeIntentRecord.account_id == account_id,
        TradeIntentRecord.environment == ExecutionEnvironment.LIVE.value,
        TradeIntentRecord.status.in_(
          ("ALLOCATION_PENDING", "AWAITING_APPROVAL", "EXECUTION_READY")
        ),
      )
      .order_by(TradeIntentRecord.id)
    )
    ready_intents = list(
      (
        await self.db.scalars(
          ready_stmt.with_for_update() if lock_rows else ready_stmt
        )
      ).all()
    )
    watermark_payload = {
      "version": "account-obligation-watermark.v1",
      "account_snapshot_id": str(locked_control.last_snapshot_id or ""),
      "account_snapshot_hash": str(locked_control.last_snapshot_hash or "").lower(),
      "pending": [
        {
          "client_order_id": str(item.client_order_id),
          "owner_type": str(item.owner_type),
          "owner_id": str(item.owner_id),
          "side": str(item.side).upper(),
          "instrument_code": str(item.instrument_code).upper(),
          "volume": int(item.volume or 0),
          "status": str(item.status).upper(),
          "broker_order_id": str(item.broker_order_id or ""),
          "last_source_sequence": int(item.last_source_sequence or 0),
        }
        for item in pending
      ],
      "outbox": [
        {
          "message_id": str(item.message_id),
          "client_order_id": str(item.client_order_id),
          "owner_type": str(item.owner_type),
          "owner_id": str(item.owner_id),
          "delivery_status": str(item.delivery_status).upper(),
          "attempts": int(item.attempts or 0),
        }
        for item in outbox
      ],
      "external_orders": [
        {
          "order_id": str(item.id),
          "side": str(getattr(item.type, "value", item.type)),
          "instrument_code": str(item.stock_code or "").upper(),
          "price": str(item.price or 0),
          "volume": int(item.volume or 0),
        }
        for item in external
        if str(item.id) not in observed_orders | broker_ids
      ],
      "batches": [
        {
          "batch_id": str(item.batch_id),
          "status": str(item.status).upper(),
          "entry_filled": int(item.entry_filled_volume or 0),
          "exit_filled": int(item.exit_filled_volume or 0),
          "version": int(item.version or 0),
        }
        for item in account_batches
      ],
      "exit_plans": [
        {
          "plan_id": str(item.plan_id),
          "status": str(item.status).upper(),
          "remaining_volume": int(item.remaining_volume or 0),
          "state_version": int(item.state_version or 0),
        }
        for item in account_plans
      ],
      "ready_intents": [
        {
          "intent_id": str(item.id),
          "owner_type": str(item.owner_type),
          "owner_id": str(item.owner_id),
          "status": str(item.status).upper(),
        }
        for item in ready_intents
      ],
    }
    available_by_bucket: dict[str, int] = {}
    unclaimed_by_bucket: dict[str, int] = {}
    protected_floor = 0
    claim_allocation: Mapping[str, int] = {}
    unclaimed_volume = max(0, available_volume - claims)
    if bucket_inventory is not None:
      available_by_bucket = {
        name: _bucket_available_volume(bucket_inventory.get(name))
        for name in ("locked_core", "core", "swing")
      }
      if sum(available_by_bucket.values()) > available_volume:
        raise ValueError(
          "ACCOUNT_CAPACITY_BUCKET_PROJECTION_INVALID:桶级可用量超过权威可卖量"
        )
      bucket_claim = allocate_old_inventory_claims(
        available_by_bucket=available_by_bucket,
        required_claim_qty=claims,
        protected_core_floor=protected_core_floor,
        allow_core_claim=allow_core_claim,
      )
      unclaimed_volume = min(unclaimed_volume, bucket_claim.unclaimed_volume)
      unclaimed_by_bucket = dict(bucket_claim.unclaimed_by_bucket)
      protected_floor = bucket_claim.protected_floor
      claim_allocation = dict(bucket_claim.allocation)
    return AccountCapacity(
      snapshot_id=str(locked_control.last_snapshot_id),
      available_cash=max(Decimal(0), available_cash),
      available_volume=max(0, available_volume),
      unclaimed_volume=unclaimed_volume,
      obligation_watermark=_obligation_watermark(watermark_payload),
      available_by_bucket=available_by_bucket,
      unclaimed_by_bucket=unclaimed_by_bucket,
      protected_old_position_floor=protected_floor,
      old_inventory_claim_allocation=claim_allocation,
    )


__all__ = [
  "AccountCapacity",
  "AccountCapacityService",
  "OldInventoryClaimResult",
  "allocate_old_inventory_claims",
  "buy_cash_required",
  "load_authoritative_account_snapshot",
]
