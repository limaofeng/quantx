"""Awaited, checkpointable PAPER matching over the domain's strict book engine.

The enclosing PAPER transaction owns scope authorization, historical event/order
idempotency checks, immutable result persistence and checkpoint commit. Only
active orders and the latest event per symbol are retained here. A pruned order
id must be resolved from those external facts before calling this adapter again.
This adapter has no database, callback, QMT, live portfolio, or background task.
"""

from __future__ import annotations

import copy
import json
import math
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef, ExecutionOwnerType
from quantx_domain.brokers.backtest import BacktestBroker
from quantx_domain.brokers.base import (
  AccountInfo,
  OrderRequest,
  OrderResponse,
  OrderStatus,
  OrderType,
  Position,
  PriceType,
  TradeRecord,
)
from quantx_domain.trading.exit_plan import TradingCostPolicy
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash

PAPER_MATCHING_POLICY_VERSION = "paper-strict-book-v2"
PAPER_QUOTE_SAME_SOURCE_NO_NEW_LIQUIDITY = "PAPER_QUOTE_SAME_SOURCE_NO_NEW_LIQUIDITY"
PAPER_TRADING_COST_POLICY = TradingCostPolicy(
  commission_rate=0.0003,
  minimum_commission=5.0,
  stamp_tax_rate=0.0005,
  transfer_fee_rate=0.00001,
)
_EXCHANGE_ZONE = ZoneInfo("Asia/Shanghai")
_ACTIVE = {OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL_FILLED}
_STATE_FIELDS = (
  "cash",
  "frozen_cash",
  "initial_capital",
  "non_trading_asset_value",
  "total_trades",
  "winning_trades",
  "losing_trades",
  "constraint_statistics",
)


def _time(value: datetime) -> datetime:
  if (
    not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
  ):
    raise ValueError("PAPER_AWARE_TIME_REQUIRED")
  return value.astimezone(_EXCHANGE_ZONE)


def _identity(value: str) -> None:
  if not isinstance(value, str) or not value.strip() or value != value.strip():
    raise ValueError("PAPER_IDENTITY_REQUIRED")


def _number(value: Any, *, positive=False) -> None:
  if (
    type(value) not in (int, float)
    or not math.isfinite(value)
    or (value <= 0 if positive else value < 0)
  ):
    raise ValueError("PAPER_INVALID_NUMBER")


def _json(value: Any) -> Any:
  """DTO values become JSON; metadata itself must already be finite JSON."""
  if isinstance(value, Enum):
    return value.value
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if hasattr(value, "__dataclass_fields__"):
    return _json(asdict(value))
  if isinstance(value, dict):
    return {key: _json(item) for key, item in value.items()}
  if isinstance(value, (list, tuple)):
    return [_json(item) for item in value]
  return value


def _request(raw):
  values = copy.deepcopy(raw)
  values["execution_ref"] = ExecutionOwnerRef.from_mapping(values["execution_ref"])
  values["environment"] = ExecutionEnvironment(values["environment"])
  values["order_type"] = OrderType(values["order_type"])
  values["price_type"] = PriceType(values["price_type"])
  return OrderRequest(**values)


def _order(raw):
  values = copy.deepcopy(raw)
  values["request"] = _request(values["request"])
  values["status"] = OrderStatus(values["status"])
  for field in ("submit_time", "last_update_time"):
    if values[field] is not None:
      values[field] = _time(datetime.fromisoformat(values[field]))
  return OrderResponse(**values)


class _StrictPaperBroker(BacktestBroker):
  def __init__(self, scope_execution_id, cash):
    super().__init__(
      account_id="paper:" + scope_execution_id,
      initial_capital=cash,
      commission_rate=PAPER_TRADING_COST_POLICY.commission_rate,
      min_commission=PAPER_TRADING_COST_POLICY.minimum_commission,
      stamp_tax_rate=PAPER_TRADING_COST_POLICY.stamp_tax_rate,
      transfer_fee_rate=PAPER_TRADING_COST_POLICY.transfer_fee_rate,
      slippage_rate=0.0001,
      participation_cap_pct=0.05,
      book_depth_participation_pct=0.25,
      strict_book_depth=True,
      no_queue_credit=True,
      defer_new_orders_until_next_quote=True,
    )
    self.scope_execution_id = scope_execution_id
    self.next_order_id = None
    self.quote_event_id = None
    self.quote_has_new_liquidity = True
    self.filling_order = None

  def generate_order_id(self):
    if self.next_order_id is None:
      raise RuntimeError("PAPER_STABLE_ORDER_ID_REQUIRED")
    return self.next_order_id

  def generate_trade_id(self):
    if self.quote_event_id is None or self.filling_order is None:
      raise RuntimeError("PAPER_STABLE_QUOTE_EVENT_REQUIRED")
    return str(
      uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"quantx:paper:{self.scope_execution_id}:{self.quote_event_id}:"
        f"{self.filling_order.order_id}:{self.filling_order.filled_volume}",
      )
    )

  async def _execute_trade(self, order, price, volume):
    self.filling_order = order
    try:
      await super()._execute_trade(order, price, volume)
    finally:
      self.filling_order = None

  async def _process_pending_orders(self, instrument_code, price):
    if not self.quote_has_new_liquidity:
      return
    pending = self.pending_orders
    # A different symbol may share a timestamp. Never grant a new order the
    # same timestamp's liquidity, even if an event id happens to differ.
    source_time = self.market_snapshots[instrument_code].timestamp
    self.pending_orders = [
      order
      for order in pending
      if order.submit_time < source_time
      and source_time.date() == self.current_time.date()
    ]
    try:
      await super()._process_pending_orders(instrument_code, price)
    finally:
      self.pending_orders = [order for order in pending if order.status in _ACTIVE]

  async def _update_equity_curve(self):
    # Historical reporting belongs to immutable PAPER events, not matcher state.
    pass

  def _record_replay_curve(self):
    pass

  def _update_ghost_dca(self, price):
    pass

  def subscribe_order_updates(self, callback):
    raise RuntimeError("PAPER_CALLBACKS_FORBIDDEN")

  def subscribe_trade_updates(self, callback):
    raise RuntimeError("PAPER_CALLBACKS_FORBIDDEN")

  async def emit_order_update(self, order):
    if self.on_order_callbacks:
      raise RuntimeError("PAPER_CALLBACKS_FORBIDDEN")

  async def emit_trade_update(self, trade):
    if self.on_trade_callbacks:
      raise RuntimeError("PAPER_CALLBACKS_FORBIDDEN")
    trade.metadata["paper_quote_event_id"] = self.quote_event_id
    trade.metadata["paper_matching_policy"] = PAPER_MATCHING_POLICY_VERSION


@dataclass(frozen=True)
class PaperMatchingResult:
  orders: tuple[OrderResponse, ...]
  trades: tuple[TradeRecord, ...]
  account: AccountInfo
  duplicate: bool = False
  reason_codes: tuple[str, ...] = ()


class PaperBrokerMatching:
  def __init__(
    self,
    *,
    scope_execution_id: str,
    cash: float,
    non_trading_asset_value: float,
    positions: dict[str, Position],
    now: datetime,
  ):
    _identity(scope_execution_id)
    _number(cash)
    _number(non_trading_asset_value)
    now = _time(now)
    for code, position in positions.items():
      if not isinstance(position, Position) or code != position.instrument_code:
        raise ValueError("PAPER_POSITION_SCOPE_INVALID")
      for name in (
        "long_volume",
        "short_volume",
        "available_volume",
        "frozen_volume",
        "today_buy_volume",
      ):
        value = getattr(position, name)
        if type(value) is not int or value < 0:
          raise ValueError("PAPER_POSITION_VOLUME_INVALID")
      if (
        position.short_volume
        or position.available_volume + position.today_buy_volume > position.long_volume
      ):
        raise ValueError("PAPER_POSITION_VOLUME_INVALID")
      for name in ("long_avg_price", "market_value", "last_price"):
        _number(getattr(position, name))
    self.scope_execution_id = scope_execution_id
    self._broker = _StrictPaperBroker(scope_execution_id, cash)
    self._broker.positions = copy.deepcopy(positions)
    self._broker.configure_initial_portfolio(
      cash=cash,
      total_asset=cash
      + non_trading_asset_value
      + sum(position.market_value for position in positions.values()),
      positions=self._broker.positions,
    )
    self._broker.current_time = now
    self._broker.current_trading_date = now.date()
    self._order_witnesses = {}
    self._latest_quote_events = {}

  def _validate_request(self, request):
    if (
      not isinstance(request, OrderRequest)
      or request.environment is not ExecutionEnvironment.PAPER
    ):
      raise ValueError("PAPER_ORDER_ENVIRONMENT_REQUIRED")
    owner = request.execution_ref
    if not isinstance(owner, ExecutionOwnerRef) or not (
      owner.owner_type is ExecutionOwnerType.T_ASSISTANT_EXECUTION
      and owner.owner_id == self.scope_execution_id
      and request.order_type is OrderType.BUY
      or owner.owner_type is ExecutionOwnerType.EXIT_PLAN
      and request.order_type is OrderType.SELL
    ):
      raise ValueError("PAPER_ORDER_OWNER_INVALID")
    if request.price_type is not PriceType.LIMIT:
      raise ValueError("PAPER_LIMIT_ORDER_REQUIRED")
    _identity(request.instrument_code)
    _number(request.price, positive=True)
    if type(request.volume) is not int or request.volume <= 0:
      raise ValueError("PAPER_ORDER_VOLUME_INVALID")
    if (
      not isinstance(request.metadata, dict)
      or json.loads(json.dumps(request.metadata, allow_nan=False)) != request.metadata
    ):
      raise ValueError("PAPER_METADATA_MUST_BE_JSON")
    expiry = request.metadata.get("order_expire_at_ms")
    if type(expiry) is not int or expiry <= 0:
      raise ValueError("PAPER_ORDER_TTL_REQUIRED")

  async def place(
    self, *, order_id: str, request: OrderRequest, now: datetime
  ) -> PaperMatchingResult:
    _identity(order_id)
    self._validate_request(request)
    now = _time(now)
    witness = stable_manifest_hash(
      {"request": _json(request), "submitted_at": now.isoformat()}
    )
    if order_id in self._order_witnesses:
      if self._order_witnesses[order_id] != witness:
        raise ValueError("PAPER_ORDER_IDEMPOTENCY_CONFLICT")
      return await self._result(
        {key: _json(value) for key, value in self._broker.orders.items()},
        len(self._broker.trades),
        duplicate=True,
        order_id=order_id,
      )
    before = self.export_checkpoint()
    previous = {key: _json(value) for key, value in self._broker.orders.items()}
    try:
      await self._broker.advance_time(now)
      self._broker.next_order_id = order_id
      await self._broker.place_order(copy.deepcopy(request))
      await self._broker.advance_time(now)
      self._order_witnesses[order_id] = witness
      return await self._result(previous, len(self._broker.trades))
    except Exception:
      self._load(before)
      raise
    finally:
      self._broker.next_order_id = None

  async def process_quote(
    self, *, event_id: str, quote: MarketDataSnapshot, accepted_at: datetime
  ) -> PaperMatchingResult:
    _identity(event_id)
    self._validate_quote(quote)
    quote = copy.deepcopy(quote)
    quote.timestamp = _time(quote.timestamp)
    accepted_at = _time(accepted_at)
    if quote.timestamp > accepted_at:
      raise ValueError("PAPER_QUOTE_SOURCE_AFTER_ACCEPTANCE")
    witness = stable_manifest_hash(_json(quote))
    previous_event = next(
      (
        value
        for value in self._latest_quote_events.values()
        if value["event_id"] == event_id
      ),
      None,
    )
    if previous_event is not None:
      if witness != previous_event["quote_hash"] or accepted_at != _time(
        datetime.fromisoformat(previous_event["accepted_at"])
      ):
        raise ValueError("PAPER_QUOTE_IDEMPOTENCY_CONFLICT")
      return await self._result(
        {key: _json(value) for key, value in self._broker.orders.items()},
        len(self._broker.trades),
        duplicate=True,
        reason_codes=tuple(previous_event["reason_codes"]),
      )
    previous_quote = self._broker.market_snapshots.get(quote.instrument_code)
    if accepted_at < self._broker.current_time or (
      previous_quote is not None and quote.timestamp < previous_quote.timestamp
    ):
      raise ValueError("PAPER_QUOTE_NOT_CAUSAL")
    same_source = (
      previous_quote is not None and quote.timestamp == previous_quote.timestamp
    )
    reason_codes = (PAPER_QUOTE_SAME_SOURCE_NO_NEW_LIQUIDITY,) if same_source else ()
    before = self.export_checkpoint()
    previous = {key: _json(value) for key, value in self._broker.orders.items()}
    trade_count = len(self._broker.trades)
    try:
      self._broker.quote_event_id = event_id
      self._broker.quote_has_new_liquidity = not same_source
      await self._broker.update_market_data(
        quote.instrument_code, quote.price, accepted_at, market_data=quote
      )
      self._latest_quote_events[quote.instrument_code] = {
        "event_id": event_id,
        "quote_hash": witness,
        "accepted_at": accepted_at.isoformat(),
        "reason_codes": list(reason_codes),
      }
      return await self._result(previous, trade_count, reason_codes=reason_codes)
    except Exception:
      self._load(before)
      raise
    finally:
      self._broker.quote_event_id = None
      self._broker.quote_has_new_liquidity = True

  async def cancel(self, *, order_id: str, now: datetime) -> PaperMatchingResult:
    _identity(order_id)
    if order_id not in self._broker.orders:
      raise ValueError("PAPER_ORDER_NOT_FOUND")
    before = self.export_checkpoint()
    previous = {key: _json(value) for key, value in self._broker.orders.items()}
    try:
      await self._broker.advance_time(_time(now))
      changed = await self._broker.cancel_order(order_id)
      return await self._result(
        previous, len(self._broker.trades), duplicate=not changed, order_id=order_id
      )
    except Exception:
      self._load(before)
      raise

  async def _result(
    self, previous, trade_count, *, duplicate=False, order_id=None, reason_codes=()
  ):
    orders = tuple(
      copy.deepcopy(order)
      for key, order in self._broker.orders.items()
      if key == order_id or previous.get(key) != _json(order)
    )
    result = PaperMatchingResult(
      orders,
      tuple(copy.deepcopy(self._broker.trades[trade_count:])),
      copy.deepcopy(await self._broker.get_account()),
      duplicate,
      tuple(reason_codes),
    )
    # Copy the complete step result before discarding terminal/history data.
    self._broker.orders = {
      key: order
      for key, order in self._broker.orders.items()
      if order.status in _ACTIVE
    }
    self._order_witnesses = {
      key: witness
      for key, witness in self._order_witnesses.items()
      if key in self._broker.orders
    }
    self._broker.trades.clear()
    return result

  @staticmethod
  def _validate_quote(quote):
    if not isinstance(quote, MarketDataSnapshot):
      raise ValueError("PAPER_QUOTE_REQUIRED")
    _time(quote.timestamp)
    _identity(quote.instrument_code)
    _identity(quote.source)
    _number(quote.price, positive=True)
    _number(quote.price_tick, positive=True)
    if type(quote.is_trading) is not bool or type(quote.suspended) is not bool:
      raise ValueError("PAPER_QUOTE_STATUS_REQUIRED")
    for prices, volumes in (
      (quote.bid_price, quote.bid_vol),
      (quote.ask_price, quote.ask_vol),
    ):
      if (
        not isinstance(prices, (list, tuple))
        or not isinstance(volumes, (list, tuple))
        or len(prices) != 5
        or len(volumes) != 5
      ):
        raise ValueError("PAPER_COMPLETE_FIVE_LEVEL_BOOK_REQUIRED")
      for value in prices:
        _number(value, positive=True)
      for value in volumes:
        _number(value)
    if (
      list(quote.bid_price) != sorted(quote.bid_price, reverse=True)
      or list(quote.ask_price) != sorted(quote.ask_price)
      or quote.bid_price[0] > quote.ask_price[0]
    ):
      raise ValueError("PAPER_INVALID_BOOK_ORDER")
    json.dumps(_json(quote), allow_nan=False)

  def export_checkpoint(self) -> dict[str, Any]:
    broker = self._broker
    material = {
      "schema_version": 2,
      "policy_version": PAPER_MATCHING_POLICY_VERSION,
      "scope_execution_id": self.scope_execution_id,
      "state": {name: _json(getattr(broker, name)) for name in _STATE_FIELDS},
      "positions": {key: _json(value) for key, value in broker.positions.items()},
      "current_time": broker.current_time.isoformat(),
      "current_trading_date": broker.current_trading_date.isoformat(),
      "current_prices": dict(broker.current_prices),
      "market_snapshots": {
        key: _json(value) for key, value in broker.market_snapshots.items()
      },
      "orders": {key: _json(value) for key, value in broker.orders.items()},
      "pending_order_ids": [order.order_id for order in broker.pending_orders],
      "order_witnesses": dict(self._order_witnesses),
      "latest_quote_events": copy.deepcopy(self._latest_quote_events),
    }
    material = json.loads(json.dumps(material, allow_nan=False))
    return {"material": material, "checkpoint_hash": stable_manifest_hash(material)}

  @classmethod
  def restore(
    cls, *, scope_execution_id: str, checkpoint: dict[str, Any]
  ) -> PaperBrokerMatching:
    _identity(scope_execution_id)
    obj = cls.__new__(cls)
    obj.scope_execution_id = scope_execution_id
    obj._load(checkpoint)
    return obj

  def _load(self, checkpoint):
    material = copy.deepcopy(checkpoint["material"])
    if (
      checkpoint["checkpoint_hash"] != stable_manifest_hash(material)
      or material["scope_execution_id"] != self.scope_execution_id
      or material["schema_version"] != 2
      or material["policy_version"] != PAPER_MATCHING_POLICY_VERSION
    ):
      raise ValueError("PAPER_CHECKPOINT_BINDING_CONFLICT")
    broker = _StrictPaperBroker(
      self.scope_execution_id, material["state"]["initial_capital"]
    )
    for name in _STATE_FIELDS:
      setattr(broker, name, material["state"][name])
    broker.positions = {
      key: Position(**value) for key, value in material["positions"].items()
    }
    broker.current_time = _time(datetime.fromisoformat(material["current_time"]))
    broker.current_trading_date = date.fromisoformat(material["current_trading_date"])
    broker.current_prices = material["current_prices"]
    broker.market_snapshots = {
      key: MarketDataSnapshot(
        **{**value, "timestamp": _time(datetime.fromisoformat(value["timestamp"]))}
      )
      for key, value in material["market_snapshots"].items()
    }
    latest_events = material["latest_quote_events"]
    if set(latest_events) != set(broker.market_snapshots):
      raise ValueError("PAPER_CHECKPOINT_QUOTE_WITNESS_CONFLICT")
    for code, quote in broker.market_snapshots.items():
      event = latest_events[code]
      _identity(event["event_id"])
      if code != quote.instrument_code or event["quote_hash"] != stable_manifest_hash(
        _json(quote)
      ):
        raise ValueError("PAPER_CHECKPOINT_QUOTE_WITNESS_CONFLICT")
      accepted_at = _time(datetime.fromisoformat(event["accepted_at"]))
      if event["reason_codes"] not in ([], [PAPER_QUOTE_SAME_SOURCE_NO_NEW_LIQUIDITY]):
        raise ValueError("PAPER_CHECKPOINT_QUOTE_REASON_CONFLICT")
      if not quote.timestamp <= accepted_at <= broker.current_time:
        raise ValueError("PAPER_CHECKPOINT_QUOTE_TIME_CONFLICT")
    broker.orders = {key: _order(value) for key, value in material["orders"].items()}
    for key, order in broker.orders.items():
      self._validate_request(order.request)
      if key != order.order_id:
        raise ValueError("PAPER_CHECKPOINT_ORDER_ID_CONFLICT")
      expected_witness = stable_manifest_hash(
        {"request": _json(order.request), "submitted_at": order.submit_time.isoformat()}
      )
      if material["order_witnesses"].get(key) != expected_witness:
        raise ValueError("PAPER_CHECKPOINT_ORDER_WITNESS_CONFLICT")
    pending = material["pending_order_ids"]
    if len(set(pending)) != len(pending) or set(pending) != {
      key for key, order in broker.orders.items() if order.status in _ACTIVE
    }:
      raise ValueError("PAPER_CHECKPOINT_PENDING_CONFLICT")
    broker.pending_orders = [broker.orders[key] for key in pending]
    if set(material["order_witnesses"]) != set(broker.orders):
      raise ValueError("PAPER_CHECKPOINT_ORDER_WITNESS_CONFLICT")
    for order in broker.orders.values():
      if (
        order.status not in _ACTIVE
        or type(order.filled_volume) is not int
        or not 0 <= order.filled_volume < order.request.volume
      ):
        raise ValueError("PAPER_CHECKPOINT_ACTIVE_ORDER_REQUIRED")
      for amount in (order.filled_amount, order.commission, order.avg_price):
        _number(amount)
      if not math.isclose(
        order.commission,
        broker._calculate_costs(order.filled_amount, order.request.order_type)["total"],
      ):
        raise ValueError("PAPER_CHECKPOINT_FILL_TOTAL_CONFLICT")
    for value in (broker.cash, broker.frozen_cash, broker.non_trading_asset_value):
      _number(value)
    self._broker = broker
    self._order_witnesses, self._latest_quote_events = (
      material["order_witnesses"],
      material["latest_quote_events"],
    )
