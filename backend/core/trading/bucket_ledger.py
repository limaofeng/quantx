"""In-memory bucket ledger for single-instrument A-share strategies."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

from core.brokers.base import OrderType


CORE_BUCKET = "core"
SWING_BUCKET = "swing"
LOCKED_CORE_BUCKET = "locked_core"
KNOWN_BUCKETS = (LOCKED_CORE_BUCKET, CORE_BUCKET, SWING_BUCKET)


class SubstitutionStatus(str, Enum):
  PLANNED = "PLANNED"
  RESERVED = "RESERVED"
  APPLIED = "APPLIED"
  ROLLED_BACK = "ROLLED_BACK"
  PARTIAL_APPLIED = "PARTIAL_APPLIED"


@dataclass
class BucketPositionState:
  bucket: str
  total_volume: int = 0
  available_volume: int = 0
  today_buy_volume: int = 0
  frozen_volume: int = 0
  market_value: float = 0.0
  avg_price: float = 0.0
  last_price: float = 0.0

  def normalize(self) -> "BucketPositionState":
    self.total_volume = max(0, int(self.total_volume or 0))
    self.frozen_volume = max(0, int(self.frozen_volume or 0))
    self.today_buy_volume = max(0, int(self.today_buy_volume or 0))
    self.available_volume = min(
      max(0, int(self.available_volume or 0)),
      max(0, self.total_volume - self.frozen_volume),
    )
    self.market_value = max(0.0, float(self.market_value or 0.0))
    self.avg_price = max(0.0, float(self.avg_price or 0.0))
    self.last_price = max(0.0, float(self.last_price or 0.0))
    return self

  def to_dict(self) -> Dict[str, Any]:
    self.normalize()
    return {
      "bucket": self.bucket,
      "total_volume": self.total_volume,
      "available_volume": self.available_volume,
      "today_buy_volume": self.today_buy_volume,
      "frozen_volume": self.frozen_volume,
      "market_value": self.market_value,
      "avg_price": self.avg_price,
      "last_price": self.last_price,
    }

  @classmethod
  def from_dict(cls, data: Dict[str, Any]) -> "BucketPositionState":
    return cls(
      bucket=str(data.get("bucket", CORE_BUCKET) or CORE_BUCKET),
      total_volume=int(data.get("total_volume", data.get("volume", 0)) or 0),
      available_volume=int(data.get("available_volume", 0) or 0),
      today_buy_volume=int(data.get("today_buy_volume", 0) or 0),
      frozen_volume=int(data.get("frozen_volume", 0) or 0),
      market_value=float(data.get("market_value", 0.0) or 0.0),
      avg_price=float(data.get("avg_price", data.get("long_avg_price", 0.0)) or 0.0),
      last_price=float(data.get("last_price", 0.0) or 0.0),
    ).normalize()


@dataclass
class SubstitutionPlan:
  requested_bucket: str
  sell_from_buckets: List[Dict[str, Any]]
  reattribute_buy_to_bucket: str = CORE_BUCKET
  volume: int = 0
  reason: str = ""
  enabled: bool = True
  plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
  order_id: Optional[str] = None
  status: SubstitutionStatus = SubstitutionStatus.PLANNED
  applied_volume: int = 0
  rolled_back_volume: int = 0
  metadata: Dict[str, Any] = field(default_factory=dict)

  @classmethod
  def from_dict(
    cls, data: Optional[Dict[str, Any]], *, order_id: Optional[str] = None
  ) -> Optional["SubstitutionPlan"]:
    if not data:
      return None
    status = data.get("status", SubstitutionStatus.PLANNED.value)
    if isinstance(status, str):
      status = SubstitutionStatus(status)
    return cls(
      requested_bucket=str(data.get("requested_bucket", SWING_BUCKET) or SWING_BUCKET),
      sell_from_buckets=[
        {"bucket": str(leg.get("bucket", "") or ""), "volume": int(leg.get("volume", 0) or 0)}
        for leg in list(data.get("sell_from_buckets", []) or [])
        if int(leg.get("volume", 0) or 0) > 0
      ],
      reattribute_buy_to_bucket=str(
        data.get("reattribute_buy_to_bucket", CORE_BUCKET) or CORE_BUCKET
      ),
      volume=int(data.get("volume", 0) or 0),
      reason=str(data.get("reason", "") or ""),
      enabled=bool(data.get("enabled", True)),
      plan_id=str(data.get("plan_id") or uuid.uuid4()),
      order_id=order_id or data.get("order_id"),
      status=status,
      applied_volume=int(data.get("applied_volume", 0) or 0),
      rolled_back_volume=int(data.get("rolled_back_volume", 0) or 0),
      metadata=dict(data.get("metadata", {}) or {}),
    )

  def to_dict(self) -> Dict[str, Any]:
    return {
      "enabled": self.enabled,
      "plan_id": self.plan_id,
      "order_id": self.order_id,
      "status": self.status.value,
      "requested_bucket": self.requested_bucket,
      "sell_from_buckets": [dict(leg) for leg in self.sell_from_buckets],
      "reattribute_buy_to_bucket": self.reattribute_buy_to_bucket,
      "volume": self.volume,
      "applied_volume": self.applied_volume,
      "rolled_back_volume": self.rolled_back_volume,
      "reason": self.reason,
      "metadata": dict(self.metadata),
    }


@dataclass
class BucketLedgerPatch:
  instrument_code: str
  changed_buckets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
  events: List[Dict[str, Any]] = field(default_factory=list)

  def to_dict(self) -> Dict[str, Any]:
    return {
      "instrument_code": self.instrument_code,
      "changed_buckets": self.changed_buckets,
      "events": list(self.events),
    }


@dataclass
class BucketLedgerSnapshot:
  run_id: str
  instruments: Dict[str, Dict[str, Any]] = field(default_factory=dict)
  pending_orders: Dict[str, Dict[str, Any]] = field(default_factory=dict)
  pending_substitutions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
  last_settlement_date: Optional[str] = None
  generated_at: datetime = field(default_factory=datetime.now)

  def to_dict(self) -> Dict[str, Any]:
    return {
      "run_id": self.run_id,
      "generated_at": self.generated_at.isoformat(),
      "last_settlement_date": self.last_settlement_date,
      "instruments": self.instruments,
      "pending_orders": self.pending_orders,
      "pending_substitutions": self.pending_substitutions,
    }


class BucketLedger:
  """Authoritative in-memory bucket ledger for a strategy run."""

  def __init__(self, run_id: str) -> None:
    self.run_id = run_id
    self._buckets: Dict[str, Dict[str, BucketPositionState]] = {}
    self._pending_orders: Dict[str, Dict[str, Any]] = {}
    self._pending_substitutions: Dict[str, SubstitutionPlan] = {}
    self._last_settlement_date: Optional[str] = None

  def snapshot(self) -> BucketLedgerSnapshot:
    return BucketLedgerSnapshot(
      run_id=self.run_id,
      instruments={
        code: {bucket: state.to_dict() for bucket, state in buckets.items()}
        for code, buckets in self._buckets.items()
      },
      pending_orders={key: dict(value) for key, value in self._pending_orders.items()},
      pending_substitutions={
        key: plan.to_dict() for key, plan in self._pending_substitutions.items()
      },
      last_settlement_date=self._last_settlement_date,
    )

  def to_dict(self) -> Dict[str, Any]:
    return self.snapshot().to_dict()

  @classmethod
  def from_dict(cls, data: Optional[Dict[str, Any]]) -> "BucketLedger":
    snapshot = dict(data or {})
    ledger = cls(run_id=str(snapshot.get("run_id", "") or ""))
    for code, buckets in dict(snapshot.get("instruments", {}) or {}).items():
      instrument_code = str(code or "")
      if not instrument_code:
        continue
      ledger._buckets[instrument_code] = {}
      for bucket, state_data in dict(buckets or {}).items():
        state_payload = dict(state_data or {})
        state_payload.setdefault("bucket", bucket)
        state = BucketPositionState.from_dict(state_payload)
        ledger._buckets[instrument_code][state.bucket] = state
      ledger._ensure_instrument(instrument_code)

    ledger._pending_orders = {
      str(order_id): dict(payload or {})
      for order_id, payload in dict(snapshot.get("pending_orders", {}) or {}).items()
      if order_id
    }
    ledger._pending_substitutions = {}
    for order_id, payload in dict(snapshot.get("pending_substitutions", {}) or {}).items():
      plan = SubstitutionPlan.from_dict(payload, order_id=str(order_id))
      if plan:
        ledger._pending_substitutions[str(order_id)] = plan
    last_settlement_date = snapshot.get("last_settlement_date")
    ledger._last_settlement_date = str(last_settlement_date) if last_settlement_date else None
    return ledger

  def sync_position(self, instrument_code: str, position: Optional[Dict[str, Any]]) -> None:
    if not instrument_code:
      return
    pos = dict(position or {})
    long_volume = int(pos.get("long_volume", pos.get("total_volume", 0)) or 0)
    available = int(pos.get("available_volume", 0) or 0)
    frozen = int(pos.get("frozen_volume", 0) or 0)
    today_buy = int(pos.get("today_buy_volume", 0) or 0)
    avg_price = float(pos.get("long_avg_price", pos.get("avg_price", 0.0)) or 0.0)
    last_price = float(pos.get("last_price", 0.0) or 0.0)

    if long_volume <= 0 and not self._pending_for_instrument(instrument_code):
      self._buckets.pop(instrument_code, None)
      return

    buckets = self._ensure_instrument(instrument_code)
    if not any(state.total_volume for state in buckets.values()):
      core = buckets[CORE_BUCKET]
      core.total_volume = long_volume
      core.available_volume = available
      core.frozen_volume = frozen
      core.today_buy_volume = today_buy
      core.avg_price = avg_price
      core.last_price = last_price
      core.market_value = float(pos.get("market_value", 0.0) or 0.0)
      core.normalize()
      return

    total = sum(state.total_volume for state in buckets.values())
    delta = long_volume - total
    if delta != 0:
      core = buckets[CORE_BUCKET]
      core.total_volume = max(0, core.total_volume + delta)
      if delta > 0:
        bucket_available = sum(state.available_volume for state in buckets.values())
        core.available_volume += max(0, available - bucket_available)
      core.normalize()

    market_value = float(pos.get("market_value", 0.0) or 0.0)
    self._mark_to_market(instrument_code, last_price, avg_price, market_value)

  def decorate_position(
    self, instrument_code: str, position: Optional[Dict[str, Any]]
  ) -> Dict[str, Any]:
    data = dict(position or {})
    self.sync_position(instrument_code, data)
    buckets = self._ensure_instrument(instrument_code)
    for name, state in buckets.items():
      state.normalize()
      data[f"{name}_volume"] = state.total_volume
      data[f"{name}_total_volume"] = state.total_volume
      data[f"{name}_available"] = state.available_volume
      data[f"{name}_available_volume"] = state.available_volume
      data[f"{name}_frozen_volume"] = state.frozen_volume
      data[f"{name}_today_buy_volume"] = state.today_buy_volume
    data["bucket_ledger"] = {name: state.to_dict() for name, state in buckets.items()}
    return data

  def set_instrument_buckets(
    self, instrument_code: str, bucket_states: Dict[str, Dict[str, Any]]
  ) -> None:
    """Seed or replace bucket attribution for one instrument.

    This is intended for user-confirmed initial attribution, such as assigning
    an existing Pullback Grid holding into core/swing buckets before the first
    order is placed. Order and trade updates should still flow through
    reserve_order/apply_trade.
    """
    if not instrument_code:
      return
    self._buckets[instrument_code] = {}
    for bucket in KNOWN_BUCKETS:
      payload = dict(bucket_states.get(bucket, {}) or {})
      payload.setdefault("bucket", bucket)
      self._buckets[instrument_code][bucket] = BucketPositionState.from_dict(payload)
    self._ensure_instrument(instrument_code)

  def reserve_order(
    self,
    order_id: str,
    *,
    instrument_code: str,
    order_type: OrderType,
    bucket: str,
    volume: int,
    price: float = 0.0,
    metadata: Optional[Dict[str, Any]] = None,
    substitution_plan: Optional[Dict[str, Any]] = None,
  ) -> bool:
    if not order_id or not instrument_code:
      return False
    volume = int(volume or 0)
    if volume <= 0:
      return False
    metadata = dict(metadata or {})
    bucket = self._normalize_bucket(bucket)
    self._ensure_instrument(instrument_code)

    plan = SubstitutionPlan.from_dict(substitution_plan, order_id=order_id)
    reserved_legs: List[Dict[str, Any]] = []
    if order_type == OrderType.SELL:
      if plan and plan.enabled:
        reserved_legs = self._reserve_substitution_legs(instrument_code, plan)
        if not reserved_legs:
          return False
        plan.status = SubstitutionStatus.RESERVED
        self._pending_substitutions[order_id] = plan
      else:
        if bucket == LOCKED_CORE_BUCKET:
          return False
        state = self._bucket(instrument_code, bucket)
        if state.available_volume < volume:
          return False
        state.available_volume -= volume
        state.frozen_volume += volume
        state.normalize()
        reserved_legs = [{"bucket": bucket, "volume": volume, "remaining_volume": volume}]

    self._pending_orders[order_id] = {
      "order_id": order_id,
      "instrument_code": instrument_code,
      "order_type": self._order_type_value(order_type),
      "bucket": bucket,
      "volume": volume,
      "remaining_volume": volume,
      "price": float(price or 0.0),
      "metadata": metadata,
      "reserved_legs": reserved_legs,
      "substitution_plan_id": plan.plan_id if plan else None,
      "created_at": datetime.now().isoformat(),
    }
    return True

  def transfer_order(self, old_order_id: str, new_order_id: str) -> None:
    if not old_order_id or not new_order_id or old_order_id == new_order_id:
      return
    pending = self._pending_orders.pop(old_order_id, None)
    if pending:
      pending["order_id"] = new_order_id
      self._pending_orders[new_order_id] = pending
    plan = self._pending_substitutions.pop(old_order_id, None)
    if plan:
      plan.order_id = new_order_id
      self._pending_substitutions[new_order_id] = plan

  def rollback_order(self, order_id: str, reason: str = "") -> BucketLedgerPatch:
    pending = self._pending_orders.pop(order_id, None)
    if not pending:
      return BucketLedgerPatch(instrument_code="", events=[])
    instrument_code = pending.get("instrument_code", "")
    events: List[Dict[str, Any]] = []
    for leg in list(pending.get("reserved_legs", []) or []):
      remaining = int(leg.get("remaining_volume", leg.get("volume", 0)) or 0)
      if remaining <= 0:
        continue
      state = self._bucket(instrument_code, leg.get("bucket"))
      state.frozen_volume = max(0, state.frozen_volume - remaining)
      state.available_volume += remaining
      state.normalize()
      events.append(
        {
          "event": "bucket_unfrozen",
          "bucket": state.bucket,
          "volume": remaining,
          "reason": reason,
        }
      )

    plan = self._pending_substitutions.pop(order_id, None)
    if plan:
      if plan.applied_volume > 0:
        plan.status = SubstitutionStatus.PARTIAL_APPLIED
      else:
        plan.status = SubstitutionStatus.ROLLED_BACK
      plan.rolled_back_volume += int(pending.get("remaining_volume", 0) or 0)
      events.append({"event": "substitution_rolled_back", "plan": plan.to_dict()})

    return BucketLedgerPatch(
      instrument_code=instrument_code,
      changed_buckets=self._instrument_snapshot(instrument_code),
      events=events,
    )

  def apply_trade(
    self, event: Any, order_metadata: Optional[Dict[str, Any]] = None
  ) -> BucketLedgerPatch:
    order_id = str(_extract(event, "order_id", "") or "")
    instrument_code = str(_extract(event, "instrument_code", "") or "")
    if not instrument_code:
      return BucketLedgerPatch(instrument_code="")

    volume = int(_extract(event, "volume", 0) or 0)
    price = float(_extract(event, "price", 0.0) or 0.0)
    if volume <= 0:
      return BucketLedgerPatch(instrument_code=instrument_code)

    pending = self._pending_orders.get(order_id, {})
    metadata = dict(pending.get("metadata", {}) or {})
    metadata.update(dict(order_metadata or {}))
    bucket = self._normalize_bucket(metadata.get("bucket", pending.get("bucket", CORE_BUCKET)))
    trade_type = _extract(event, "trade_type")
    events: List[Dict[str, Any]] = []

    if trade_type in [OrderType.BUY, OrderType.BUY_TO_COVER] or str(trade_type).endswith("BUY"):
      state = self._bucket(instrument_code, bucket)
      self._apply_bucket_buy(state, volume, price)
      events.append({"event": "bucket_buy_applied", "bucket": bucket, "volume": volume})
    elif trade_type == OrderType.SELL or str(trade_type).endswith("SELL"):
      self._apply_bucket_sell(order_id, instrument_code, bucket, volume, events)
      plan = self._pending_substitutions.get(order_id)
      if plan:
        self._apply_substitution_reattribute(instrument_code, plan, volume, price, events)

    self._consume_pending(order_id, volume)
    return BucketLedgerPatch(
      instrument_code=instrument_code,
      changed_buckets=self._instrument_snapshot(instrument_code),
      events=events,
    )

  def settle_trading_day(self, trading_date: date) -> None:
    key = trading_date.isoformat()
    if self._last_settlement_date == key:
      return
    self._last_settlement_date = key
    for buckets in self._buckets.values():
      for state in buckets.values():
        state.available_volume = max(0, state.total_volume - state.frozen_volume)
        state.today_buy_volume = 0
        state.normalize()

  def pending_metadata(self, order_id: str) -> Dict[str, Any]:
    pending = self._pending_orders.get(order_id, {})
    return dict(pending.get("metadata", {}) or {})

  def apply_corporate_action(
    self,
    instrument_code: str,
    *,
    volume_factor: float = 1.0,
    price_factor: Optional[float] = None,
    cash_dividend_per_share: float = 0.0,
    action_id: Optional[str] = None,
    ex_date: Optional[Any] = None,
  ) -> BucketLedgerPatch:
    """Adjust bucket inventory for split/bonus-share/dividend events."""
    if not instrument_code:
      return BucketLedgerPatch(instrument_code="")

    volume_factor = float(volume_factor or 1.0)
    if volume_factor <= 0:
      volume_factor = 1.0
    if price_factor is None:
      price_multiplier = 1.0 / volume_factor if volume_factor > 0 else 1.0
    else:
      price_multiplier = float(price_factor or 1.0)
    cash_dividend = max(0.0, float(cash_dividend_per_share or 0.0))

    events: List[Dict[str, Any]] = []
    for state in self._ensure_instrument(instrument_code).values():
      before = state.to_dict()
      state.total_volume = _scale_volume(state.total_volume, volume_factor)
      state.available_volume = _scale_volume(state.available_volume, volume_factor)
      state.today_buy_volume = _scale_volume(state.today_buy_volume, volume_factor)
      state.frozen_volume = _scale_volume(state.frozen_volume, volume_factor)
      state.avg_price = max(0.0, state.avg_price * price_multiplier - cash_dividend)
      state.last_price = max(0.0, state.last_price * price_multiplier - cash_dividend)
      state.market_value = state.total_volume * (state.last_price or state.avg_price)
      state.normalize()
      after = state.to_dict()
      if before != after:
        events.append(
          {
            "event": "corporate_action_bucket_adjusted",
            "bucket": state.bucket,
            "before": before,
            "after": after,
          }
        )

    events.append(
      {
        "event": "corporate_action_applied",
        "action_id": action_id,
        "ex_date": ex_date.isoformat() if hasattr(ex_date, "isoformat") else ex_date,
        "volume_factor": volume_factor,
        "price_factor": price_multiplier,
        "cash_dividend_per_share": cash_dividend,
      }
    )
    return BucketLedgerPatch(
      instrument_code=instrument_code,
      changed_buckets=self._instrument_snapshot(instrument_code),
      events=events,
    )

  def validate_invariants(
    self, positions: Optional[Dict[str, Dict[str, Any]]] = None
  ) -> List[str]:
    """Return reconciliation issues between bucket totals and run positions."""
    positions = dict(positions or {})
    issues: List[str] = []
    instrument_codes = set(self._buckets.keys()) | set(positions.keys())
    for code in sorted(instrument_codes):
      buckets = self._ensure_instrument(code)
      position = dict(positions.get(code, {}) or {})
      expected_total = int(position.get("long_volume", position.get("total_volume", 0)) or 0)
      actual_total = sum(state.total_volume for state in buckets.values())
      checks = {"total_volume": (actual_total, expected_total)}
      optional_fields = {
        "available_volume": sum(state.available_volume for state in buckets.values()),
        "frozen_volume": sum(state.frozen_volume for state in buckets.values()),
        "today_buy_volume": sum(state.today_buy_volume for state in buckets.values()),
      }
      for field, actual in optional_fields.items():
        if field in position:
          checks[field] = (actual, int(position.get(field, 0) or 0))
      for field, (actual, expected) in checks.items():
        if actual != expected:
          issues.append(
            f"{code}.{field}: bucket={actual}, position={expected}"
          )
    return issues

  def _ensure_instrument(self, instrument_code: str) -> Dict[str, BucketPositionState]:
    buckets = self._buckets.setdefault(instrument_code, {})
    for name in KNOWN_BUCKETS:
      buckets.setdefault(name, BucketPositionState(bucket=name))
    return buckets

  def _bucket(self, instrument_code: str, bucket: Any) -> BucketPositionState:
    name = self._normalize_bucket(bucket)
    return self._ensure_instrument(instrument_code).setdefault(
      name, BucketPositionState(bucket=name)
    )

  def _reserve_substitution_legs(
    self, instrument_code: str, plan: SubstitutionPlan
  ) -> List[Dict[str, Any]]:
    legs = [
      {
        "bucket": self._normalize_bucket(leg.get("bucket")),
        "volume": int(leg.get("volume", 0) or 0),
      }
      for leg in plan.sell_from_buckets
      if int(leg.get("volume", 0) or 0) > 0
    ]
    if not legs:
      return []
    for leg in legs:
      state = self._bucket(instrument_code, leg["bucket"])
      if state.available_volume < leg["volume"]:
        return []
    for leg in legs:
      state = self._bucket(instrument_code, leg["bucket"])
      state.available_volume -= leg["volume"]
      state.frozen_volume += leg["volume"]
      state.normalize()
      leg["remaining_volume"] = leg["volume"]
    return legs

  def _apply_bucket_buy(
    self, state: BucketPositionState, volume: int, price: float
  ) -> None:
    old_value = state.avg_price * state.total_volume
    add_value = max(0.0, float(price or 0.0)) * int(volume)
    state.total_volume += volume
    state.today_buy_volume += volume
    if state.total_volume > 0:
      state.avg_price = (old_value + add_value) / state.total_volume
    state.last_price = max(state.last_price, float(price or 0.0))
    state.market_value = state.total_volume * (state.last_price or state.avg_price)
    state.normalize()

  def _apply_bucket_sell(
    self,
    order_id: str,
    instrument_code: str,
    bucket: str,
    volume: int,
    events: List[Dict[str, Any]],
  ) -> None:
    remaining = volume
    pending = self._pending_orders.get(order_id, {})
    for leg in list(pending.get("reserved_legs", []) or []):
      if remaining <= 0:
        break
      leg_remaining = int(leg.get("remaining_volume", leg.get("volume", 0)) or 0)
      if leg_remaining <= 0:
        continue
      take = min(remaining, leg_remaining)
      state = self._bucket(instrument_code, leg.get("bucket"))
      state.frozen_volume = max(0, state.frozen_volume - take)
      state.total_volume = max(0, state.total_volume - take)
      state.market_value = state.total_volume * (state.last_price or state.avg_price)
      state.normalize()
      leg["remaining_volume"] = leg_remaining - take
      remaining -= take
      events.append({"event": "bucket_sell_applied", "bucket": state.bucket, "volume": take})

    if remaining > 0:
      state = self._bucket(instrument_code, bucket)
      take = min(remaining, state.total_volume)
      state.total_volume -= take
      state.available_volume = max(0, state.available_volume - take)
      state.market_value = state.total_volume * (state.last_price or state.avg_price)
      state.normalize()
      events.append({"event": "bucket_sell_unreserved", "bucket": state.bucket, "volume": take})

  def _apply_substitution_reattribute(
    self,
    instrument_code: str,
    plan: SubstitutionPlan,
    volume: int,
    price: float,
    events: List[Dict[str, Any]],
  ) -> None:
    requested = self._bucket(instrument_code, plan.requested_bucket)
    target = self._bucket(instrument_code, plan.reattribute_buy_to_bucket)
    movable = min(volume, requested.today_buy_volume, requested.total_volume)
    if movable > 0 and requested.bucket != target.bucket:
      requested.total_volume -= movable
      requested.today_buy_volume -= movable
      requested.market_value = requested.total_volume * (
        requested.last_price or requested.avg_price
      )
      requested.normalize()
      self._apply_bucket_buy(target, movable, price)
      events.append(
        {
          "event": "substitution_reattributed",
          "from_bucket": requested.bucket,
          "to_bucket": target.bucket,
          "volume": movable,
        }
      )
    plan.applied_volume += volume
    if plan.applied_volume >= max(plan.volume, volume):
      plan.status = SubstitutionStatus.APPLIED
      self._pending_substitutions.pop(plan.order_id or "", None)
    else:
      plan.status = SubstitutionStatus.PARTIAL_APPLIED

  def _consume_pending(self, order_id: str, volume: int) -> None:
    if not order_id or order_id not in self._pending_orders:
      return
    pending = self._pending_orders[order_id]
    remaining = max(0, int(pending.get("remaining_volume", 0) or 0) - int(volume or 0))
    pending["remaining_volume"] = remaining
    if remaining <= 0:
      self._pending_orders.pop(order_id, None)

  def _mark_to_market(
    self, instrument_code: str, last_price: float, avg_price: float, market_value: float
  ) -> None:
    buckets = self._ensure_instrument(instrument_code)
    total = sum(state.total_volume for state in buckets.values())
    for state in buckets.values():
      if avg_price > 0:
        state.avg_price = avg_price if state.avg_price <= 0 else state.avg_price
      if last_price > 0:
        state.last_price = last_price
      if total > 0 and market_value > 0:
        state.market_value = market_value * state.total_volume / total
      elif state.last_price > 0:
        state.market_value = state.total_volume * state.last_price
      state.normalize()

  def _instrument_snapshot(self, instrument_code: str) -> Dict[str, Dict[str, Any]]:
    if not instrument_code:
      return {}
    return {
      name: state.to_dict()
      for name, state in self._ensure_instrument(instrument_code).items()
    }

  def _pending_for_instrument(self, instrument_code: str) -> Iterable[Dict[str, Any]]:
    return [
      pending
      for pending in self._pending_orders.values()
      if pending.get("instrument_code") == instrument_code
    ]

  @staticmethod
  def _normalize_bucket(bucket: Any) -> str:
    name = str(bucket or CORE_BUCKET).lower()
    if name in {"locked", "lockedcore", "locked_core"}:
      return LOCKED_CORE_BUCKET
    if name == SWING_BUCKET:
      return SWING_BUCKET
    return CORE_BUCKET

  @staticmethod
  def _order_type_value(order_type: Any) -> str:
    return getattr(order_type, "value", str(order_type))


def _extract(source: Any, key: str, default: Any = None) -> Any:
  if source is None:
    return default
  if isinstance(source, dict):
    return source.get(key, default)
  return getattr(source, key, default)


def _scale_volume(value: Any, factor: float) -> int:
  return max(0, int(round(int(value or 0) * factor)))
