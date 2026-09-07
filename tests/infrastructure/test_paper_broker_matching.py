"""Strict PAPER matching reuses domain economics across checkpoint boundaries."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.backtest import BacktestBroker
from quantx_domain.brokers.base import (
  OrderRequest,
  OrderStatus,
  OrderType,
  Position,
  PriceType,
)
from quantx_domain.trading.exit_plan import estimate_buy_fee_cny
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_infrastructure.services.paper_broker_matching import PaperBrokerMatching

NOW = datetime(2026, 9, 7, 2, tzinfo=UTC)


def matching(*, positions=None, cash=100_000):
  return PaperBrokerMatching(
    scope_execution_id="paper-execution",
    cash=cash,
    non_trading_asset_value=50,
    positions=positions or {},
    now=NOW,
  )


def request(*, side=OrderType.BUY, volume=800, expires=60):
  return OrderRequest(
    instrument_code="600000.SH",
    order_type=side,
    price_type=PriceType.LIMIT,
    volume=volume,
    price=10.10 if side is OrderType.BUY else 9.9,
    execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", "paper-execution")
    if side is OrderType.BUY
    else ExecutionOwnerRef("EXIT_PLAN", "exit-plan"),
    environment=ExecutionEnvironment.PAPER,
    metadata={
      "order_expire_at_ms": int((NOW + timedelta(seconds=expires)).timestamp() * 1000),
      "intent_id": "intent",
      "nested": {"important": [1, "literal"]},
    },
  )


def quote(seconds, *, depth=400):
  return MarketDataSnapshot(
    instrument_code="600000.SH",
    timestamp=NOW + timedelta(seconds=seconds),
    price=10,
    price_tick=0.01,
    source="accepted-paper-quote",
    limit_up=11,
    limit_down=9,
    bid_price=[9.99, 9.98, 9.97, 9.96, 9.95],
    ask_price=[10, 10.01, 10.02, 10.03, 10.04],
    bid_vol=[depth] * 5,
    ask_vol=[depth] * 5,
  )


def restored(adapter):
  return PaperBrokerMatching.restore(
    scope_execution_id="paper-execution", checkpoint=adapter.export_checkpoint()
  )


def test_v1_checkpoint_is_not_restored_under_the_v2_policy():
  from quantx_domain.trading.t_assistant_execution import stable_manifest_hash

  checkpoint = matching().export_checkpoint()
  checkpoint["material"]["policy_version"] = "paper-strict-book-v1"
  checkpoint["checkpoint_hash"] = stable_manifest_hash(checkpoint["material"])
  with pytest.raises(ValueError, match="CHECKPOINT"):
    PaperBrokerMatching.restore(
      scope_execution_id="paper-execution", checkpoint=checkpoint
    )


async def test_cross_symbol_source_can_arrive_later_without_old_liquidity_for_new_order():
  adapter = matching()
  await adapter.process_quote(
    event_id="a", quote=quote(2), accepted_at=quote(2).timestamp
  )
  other = "000001.SZ"
  await adapter.place(
    order_id="b-order",
    request=replace(request(volume=100), instrument_code=other),
    now=quote(2).timestamp,
  )
  old = replace(quote(1), instrument_code=other)
  result = await adapter.process_quote(
    event_id="late-b", quote=old, accepted_at=quote(3).timestamp
  )
  assert not result.trades
  checkpoint = adapter.export_checkpoint()
  assert (
    datetime.fromisoformat(
      checkpoint["material"]["market_snapshots"][other]["timestamp"]
    )
    == old.timestamp
  )
  adapter = restored(adapter)
  assert (
    await adapter.process_quote(
      event_id="late-b", quote=old, accepted_at=quote(3).timestamp
    )
  ).duplicate
  with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
    await adapter.process_quote(
      event_id="late-b", quote=old, accepted_at=quote(4).timestamp
    )
  fresh = replace(quote(3), instrument_code=other)
  result = await adapter.process_quote(
    event_id="b-fresh", quote=fresh, accepted_at=quote(4).timestamp
  )
  assert result.trades[0].volume == 100
  assert result.trades[0].trade_time == quote(4).timestamp
  assert old.timestamp == quote(1).timestamp


async def test_acceptance_expires_order_before_a_quote_that_was_sourced_before_ttl():
  adapter = matching()
  await adapter.place(order_id="order", request=request(expires=5), now=NOW)
  result = await adapter.process_quote(
    event_id="late", quote=quote(4), accepted_at=quote(5).timestamp
  )
  assert not result.trades
  assert result.orders[0].status is OrderStatus.EXPIRED


async def test_previous_day_quote_does_not_supply_new_day_liquidity_or_reverse_settlement():
  adapter = matching(
    positions={
      "600000.SH": Position(
        "600000.SH",
        long_volume=200,
        available_volume=100,
        today_buy_volume=100,
        long_avg_price=10,
        last_price=10,
      )
    }
  )
  await adapter.process_quote(event_id="baseline", quote=quote(0), accepted_at=NOW)
  placed = await adapter.place(
    order_id="sell",
    request=request(side=OrderType.SELL, volume=100, expires=172800),
    now=NOW,
  )
  assert placed.orders[0].status is OrderStatus.SUBMITTED, placed.orders[0]
  next_day = NOW + timedelta(days=1)
  result = await adapter.process_quote(
    event_id="yesterday", quote=quote(1), accepted_at=next_day
  )
  assert not result.trades
  assert result.account.positions["600000.SH"].today_buy_volume == 0
  assert result.account.positions["600000.SH"].available_volume == 200
  adapter = restored(adapter)
  result = await adapter.process_quote(
    event_id="today",
    quote=replace(quote(1), timestamp=next_day + timedelta(seconds=1)),
    accepted_at=next_day + timedelta(seconds=2),
  )
  assert result.trades[0].volume == 100
  assert result.trades[0].trade_time == next_day + timedelta(seconds=2)


@pytest.mark.parametrize(
  "accepted", [NOW.replace(tzinfo=None), NOW - timedelta(seconds=1)]
)
async def test_acceptance_requires_aware_non_future_source(accepted):
  adapter = matching()
  before = adapter.export_checkpoint()
  with pytest.raises(ValueError, match="AWARE_TIME_REQUIRED|SOURCE_AFTER_ACCEPTANCE"):
    await adapter.process_quote(event_id="future", quote=quote(0), accepted_at=accepted)
  assert adapter.export_checkpoint() == before


async def test_restart_after_partial_fill_matches_continuous_execution_and_fees():
  continuous, restarted = matching(), matching()
  for adapter in (continuous, restarted):
    result = await adapter.place(order_id="order-1", request=request(), now=NOW)
    assert result.orders[0].status is OrderStatus.SUBMITTED
    assert result.trades == ()
    first = await adapter.process_quote(
      event_id="q1", accepted_at=(quote(1)).timestamp, quote=quote(1)
    )
    assert first.orders[0].status is OrderStatus.PARTIAL_FILLED
    assert first.orders[0].filled_volume == 500
    assert first.trades[0].volume == 500
  restarted = restored(restarted)
  continuous_result = await continuous.process_quote(
    event_id="q2", accepted_at=(quote(2)).timestamp, quote=quote(2)
  )
  restarted_result = await restarted.process_quote(
    event_id="q2", accepted_at=(quote(2)).timestamp, quote=quote(2)
  )
  assert restarted_result == continuous_result
  assert restarted.export_checkpoint() == continuous.export_checkpoint()
  order = restarted_result.orders[0]
  assert order.status is OrderStatus.FILLED and order.filled_volume == 800
  expected_cost = BacktestBroker()._calculate_costs(order.filled_amount, OrderType.BUY)[
    "total"
  ]
  assert order.commission == pytest.approx(expected_cost)
  assert order.commission == pytest.approx(
    estimate_buy_fee_cny(price=order.avg_price, volume=order.filled_volume)
  )
  assert restarted_result.account.cash == pytest.approx(
    100_000 - order.filled_amount - expected_cost
  )
  assert restarted_result.account.positions["600000.SH"].available_volume == 0
  assert restarted_result.trades[0].execution_ref == request().execution_ref
  assert restarted_result.trades[0].environment is ExecutionEnvironment.PAPER
  assert restarted_result.trades[0].metadata["nested"] == {"important": [1, "literal"]}


async def test_repeat_events_orders_and_stable_fill_ids_after_restore():
  adapter = matching()
  await adapter.place(order_id="order", request=request(), now=NOW)
  initial = adapter.export_checkpoint()
  first = await adapter.process_quote(
    event_id="q1", accepted_at=(quote(1)).timestamp, quote=quote(1)
  )
  replay = PaperBrokerMatching.restore(
    scope_execution_id="paper-execution", checkpoint=initial
  )
  assert (
    await replay.process_quote(
      event_id="q1", accepted_at=(quote(1)).timestamp, quote=quote(1)
    )
  ).trades == first.trades
  adapter = restored(adapter)
  prior = adapter.export_checkpoint()
  repeated = await adapter.process_quote(
    event_id="q1", accepted_at=(quote(1)).timestamp, quote=quote(1)
  )
  assert repeated.duplicate and repeated.orders == () and repeated.trades == ()
  assert adapter.export_checkpoint() == prior
  order_retry = await adapter.place(order_id="order", request=request(), now=NOW)
  assert order_retry.duplicate and order_retry.orders[0].filled_volume == 500
  with pytest.raises(ValueError, match="ORDER_IDEMPOTENCY"):
    await adapter.place(order_id="order", request=request(volume=900), now=NOW)
  with pytest.raises(ValueError, match="QUOTE_IDEMPOTENCY"):
    await adapter.process_quote(
      event_id="q1",
      accepted_at=(quote(1, depth=800)).timestamp,
      quote=quote(1, depth=800),
    )
  assert adapter.export_checkpoint() == prior


async def test_same_time_new_order_waits_until_strictly_later_quote():
  adapter = matching()
  await adapter.process_quote(
    event_id="baseline", accepted_at=(quote(0)).timestamp, quote=quote(0)
  )
  await adapter.place(
    order_id="order", request=request(), now=NOW + timedelta(seconds=1)
  )
  same_time = await adapter.process_quote(
    event_id="q1", accepted_at=(quote(1)).timestamp, quote=quote(1)
  )
  assert same_time.trades == ()
  assert (
    await adapter.process_quote(
      event_id="q2", accepted_at=(quote(2)).timestamp, quote=quote(2)
    )
  ).trades


async def test_old_source_under_new_event_id_fails_closed():
  adapter = matching()
  await adapter.process_quote(
    event_id="q1", accepted_at=(quote(1)).timestamp, quote=quote(1)
  )
  with pytest.raises(ValueError, match="NOT_CAUSAL"):
    await adapter.process_quote(
      event_id="other", accepted_at=quote(2).timestamp, quote=quote(0)
    )


async def test_same_source_changes_book_without_replenishing_liquidity_after_restore():
  adapter = matching()
  await adapter.place(order_id="order", request=request(), now=NOW)
  first = await adapter.process_quote(
    event_id="first", quote=quote(1), accepted_at=quote(1).timestamp
  )
  assert first.orders[0].filled_volume == 500
  adapter = restored(adapter)
  changed = replace(
    quote(1, depth=800), price=10.01, ask_price=[10.01, 10.02, 10.03, 10.04, 10.05]
  )
  same = await adapter.process_quote(
    event_id="same-source", quote=changed, accepted_at=quote(2).timestamp
  )
  assert same.trades == ()
  assert same.reason_codes == ("PAPER_QUOTE_SAME_SOURCE_NO_NEW_LIQUIDITY",)
  assert adapter._broker.orders["order"].filled_volume == 500
  assert adapter._broker.market_snapshots["600000.SH"].ask_price == changed.ask_price
  assert adapter._broker.positions["600000.SH"].last_price == 10.01
  adapter = restored(adapter)
  retry = await adapter.process_quote(
    event_id="same-source", quote=changed, accepted_at=quote(2).timestamp
  )
  assert retry.duplicate and retry.reason_codes == same.reason_codes
  fresh = await adapter.process_quote(
    event_id="fresh", quote=quote(2), accepted_at=quote(3).timestamp
  )
  assert fresh.trades[0].volume == 300 and fresh.reason_codes == ()


async def test_same_source_acceptance_still_expires_pending_orders():
  adapter = matching()
  await adapter.place(order_id="order", request=request(expires=3), now=NOW)
  await adapter.process_quote(
    event_id="first", quote=quote(1), accepted_at=quote(1).timestamp
  )
  expired = await adapter.process_quote(
    event_id="same-at-ttl", quote=quote(1), accepted_at=quote(3).timestamp
  )
  assert not expired.trades and expired.orders[0].status is OrderStatus.EXPIRED
  assert expired.orders[0].filled_volume == 500
  assert expired.reason_codes == ("PAPER_QUOTE_SAME_SOURCE_NO_NEW_LIQUIDITY",)


@pytest.mark.parametrize(
  "field,value",
  [
    ("ask_price", []),
    ("bid_vol", [400] * 4),
    ("ask_vol", None),
    ("ask_price", [float("nan")] * 5),
    ("bid_vol", [None] * 5),
    ("ask_price", [0] * 5),
    ("bid_price", [11] * 5),
  ],
)
async def test_incomplete_or_invalid_book_never_invents_liquidity(field, value):
  adapter = matching()
  await adapter.place(order_id="order", request=request(), now=NOW)
  before = adapter.export_checkpoint()
  with pytest.raises(ValueError):
    await adapter.process_quote(
      event_id="bad",
      accepted_at=(replace(quote(1), **{field: value})).timestamp,
      quote=replace(quote(1), **{field: value}),
    )
  assert adapter.export_checkpoint() == before


async def test_expiry_is_applied_before_matching_and_is_recoverable():
  adapter = matching()
  await adapter.place(order_id="order", request=request(expires=1), now=NOW)
  adapter = restored(adapter)
  result = await adapter.process_quote(
    event_id="at-ttl", accepted_at=(quote(1)).timestamp, quote=quote(1)
  )
  assert result.trades == ()
  assert result.orders[0].status is OrderStatus.EXPIRED
  assert result.account.cash == 100_000


async def test_t_plus_one_and_explicit_initial_positions_survive_restart():
  position = Position(
    "600000.SH",
    long_volume=1000,
    available_volume=800,
    today_buy_volume=200,
    long_avg_price=9,
    market_value=10000,
    last_price=10,
  )
  adapter = matching(positions={position.instrument_code: position})
  assert (
    adapter.export_checkpoint()["material"]["positions"][position.instrument_code][
      "long_volume"
    ]
    == 1000
  )
  rejected = await adapter.place(
    order_id="oversell", request=request(side=OrderType.SELL, volume=900), now=NOW
  )
  assert rejected.orders[0].status is OrderStatus.REJECTED
  await adapter.place(order_id="buy", request=request(volume=100), now=NOW)
  buy = await adapter.process_quote(
    event_id="buy-quote", accepted_at=(quote(1)).timestamp, quote=quote(1)
  )
  assert buy.account.positions[position.instrument_code].available_volume == 800
  adapter = restored(adapter)
  await adapter.place(
    order_id="sell-old",
    request=request(side=OrderType.SELL, volume=800),
    now=NOW + timedelta(seconds=2),
  )
  sold = await adapter.process_quote(
    event_id="sell-quote",
    accepted_at=(quote(3, depth=800)).timestamp,
    quote=quote(3, depth=800),
  )
  assert sold.account.positions[position.instrument_code].available_volume == 0
  assert sold.account.positions[position.instrument_code].long_volume == 300
  adapter = restored(adapter)
  next_day = await adapter.process_quote(
    event_id="next-day", accepted_at=(quote(86400)).timestamp, quote=quote(86400)
  )
  assert next_day.account.positions[position.instrument_code].available_volume == 300


async def test_pending_cash_and_sell_reservations_are_restored():
  adapter = matching(cash=15_000)
  await adapter.place(order_id="first", request=request(volume=1000), now=NOW)
  adapter = restored(adapter)
  result = await adapter.place(order_id="second", request=request(volume=1000), now=NOW)
  assert result.orders[0].status is OrderStatus.REJECTED
  position = Position(
    "600000.SH",
    long_volume=1000,
    available_volume=1000,
    long_avg_price=10,
    market_value=10000,
    last_price=10,
  )
  adapter = matching(positions={position.instrument_code: position})
  await adapter.place(
    order_id="first", request=request(side=OrderType.SELL, volume=800), now=NOW
  )
  adapter = restored(adapter)
  result = await adapter.place(
    order_id="second", request=request(side=OrderType.SELL, volume=300), now=NOW
  )
  assert result.orders[0].status is OrderStatus.REJECTED


async def test_cancel_after_partial_fill_preserves_economics_and_no_more_fill():
  adapter = matching()
  await adapter.place(order_id="order", request=request(), now=NOW)
  partial = await adapter.process_quote(
    event_id="q1", accepted_at=(quote(1)).timestamp, quote=quote(1)
  )
  adapter = restored(adapter)
  result = await adapter.cancel(order_id="order", now=NOW + timedelta(seconds=2))
  assert result.orders[0].status is OrderStatus.CANCELLED
  assert result.orders[0].commission == partial.orders[0].commission
  assert (
    await adapter.process_quote(
      event_id="q3", accepted_at=(quote(3)).timestamp, quote=quote(3)
    )
  ).trades == ()
  assert adapter.export_checkpoint()["material"]["orders"] == {}
  # Historical cancellation deduplication belongs to persisted PAPER facts.
  with pytest.raises(ValueError, match="PAPER_ORDER_NOT_FOUND"):
    await adapter.cancel(order_id="order", now=NOW + timedelta(seconds=3))


@pytest.mark.parametrize(
  "environment", [ExecutionEnvironment.LIVE, ExecutionEnvironment.BACKTEST]
)
async def test_only_explicit_paper_requests_are_accepted(environment):
  with pytest.raises(ValueError, match="ENVIRONMENT"):
    await matching().place(
      order_id="order", request=replace(request(), environment=environment), now=NOW
    )


async def test_owner_market_price_and_checkpoint_scope_are_closed():
  with pytest.raises(ValueError, match="OWNER"):
    await matching().place(
      order_id="order",
      request=replace(
        request(), execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", "other")
      ),
      now=NOW,
    )
  with pytest.raises(ValueError, match="LIMIT"):
    await matching().place(
      order_id="order", request=replace(request(), price_type=PriceType.MARKET), now=NOW
    )
  with pytest.raises(ValueError, match="CHECKPOINT"):
    PaperBrokerMatching.restore(
      scope_execution_id="other", checkpoint=matching().export_checkpoint()
    )


async def test_callback_failure_is_not_swallowed_and_operation_rolls_back(monkeypatch):
  adapter = matching()
  await adapter.place(order_id="order", request=request(), now=NOW)
  before = adapter.export_checkpoint()
  with pytest.raises(RuntimeError, match="CALLBACKS_FORBIDDEN"):
    adapter._broker.subscribe_trade_updates(lambda trade: None)

  async def injected_failure(trade):
    raise RuntimeError("must propagate")

  monkeypatch.setattr(adapter._broker, "emit_trade_update", injected_failure)
  with pytest.raises(RuntimeError, match="must propagate"):
    await adapter.process_quote(
      event_id="q1", accepted_at=(quote(1)).timestamp, quote=quote(1)
    )
  assert adapter.export_checkpoint() == before


async def test_quote_depth_is_shared_across_restored_pending_orders():
  adapter = matching()
  await adapter.place(order_id="first", request=request(volume=200), now=NOW)
  await adapter.place(order_id="second", request=request(volume=800), now=NOW)
  adapter = restored(adapter)
  result = await adapter.process_quote(
    event_id="one-book", accepted_at=(quote(1)).timestamp, quote=quote(1)
  )
  assert [(trade.order_id, trade.volume) for trade in result.trades] == [
    ("first", 200),
    ("second", 300),
  ]


async def test_explicit_zero_depth_gives_no_queue_credit():
  adapter = matching()
  await adapter.place(order_id="order", request=request(), now=NOW)
  result = await adapter.process_quote(
    event_id="empty-book",
    accepted_at=(quote(1, depth=0)).timestamp,
    quote=quote(1, depth=0),
  )
  assert result.trades == ()
  assert (
    await restored(adapter).process_quote(
      event_id="fresh-book", accepted_at=(quote(2)).timestamp, quote=quote(2)
    )
  ).trades


async def test_new_order_step_returns_expirations_of_preexisting_orders():
  adapter = matching()
  await adapter.place(order_id="old", request=request(expires=1), now=NOW)
  result = await adapter.place(
    order_id="new", request=request(), now=NOW + timedelta(seconds=1)
  )
  assert [(order.order_id, order.status) for order in result.orders] == [
    ("old", OrderStatus.EXPIRED),
    ("new", OrderStatus.SUBMITTED),
  ]


async def test_thousand_quotes_keep_checkpoint_bounded_with_fixed_active_state():
  adapter = matching()
  resting = replace(request(expires=2000), price=9.9)
  await adapter.place(order_id="resting", request=resting, now=NOW)
  sizes = []
  for index in range(1, 1001):
    result = await adapter.process_quote(
      event_id=f"q{index:04d}", accepted_at=(quote(index)).timestamp, quote=quote(index)
    )
    assert result.trades == ()
    checkpoint = adapter.export_checkpoint()
    if index in (1, 100, 500, 1000):
      sizes.append(len(json.dumps(checkpoint, sort_keys=True)))
    if index % 100 == 0:
      adapter = restored(adapter)
  material = adapter.export_checkpoint()["material"]
  assert max(sizes) - min(sizes) < 64
  assert len(material["latest_quote_events"]) == 1
  assert len(material["orders"]) == len(material["order_witnesses"]) == 1
  assert "trades" not in material
  assert not {"equity_curve", "daily_returns", "replay_curve"} & set(material["state"])
  assert adapter._broker.trades == []
  assert len(adapter._broker.equity_curve) <= 1
  assert adapter._broker.replay_curve == []
  repeated = await adapter.process_quote(
    event_id="q1000", accepted_at=(quote(1000)).timestamp, quote=quote(1000)
  )
  assert repeated.duplicate and repeated.trades == ()
  with pytest.raises(ValueError, match="QUOTE_IDEMPOTENCY"):
    await adapter.process_quote(
      event_id="q1000",
      accepted_at=(quote(1000, depth=800)).timestamp,
      quote=quote(1000, depth=800),
    )
  with pytest.raises(ValueError, match="NOT_CAUSAL"):
    await adapter.process_quote(
      event_id="q0001", accepted_at=(quote(1)).timestamp, quote=quote(1)
    )


async def test_terminal_orders_and_fills_are_returned_then_removed_from_checkpoint():
  adapter = matching()
  trade_ids = []
  for index in range(40):
    placed = await adapter.place(
      order_id=f"order-{index}",
      request=request(volume=100, expires=500),
      now=NOW + timedelta(seconds=index * 2),
    )
    assert placed.orders[0].status is OrderStatus.SUBMITTED
    result = await adapter.process_quote(
      event_id=f"fill-{index}",
      accepted_at=(quote(index * 2 + 1)).timestamp,
      quote=quote(index * 2 + 1),
    )
    assert result.orders[0].status is OrderStatus.FILLED
    assert len(result.trades) == 1
    trade_ids.append(result.trades[0].trade_id)
    checkpoint = adapter.export_checkpoint()
    assert checkpoint["material"]["orders"] == {}
    assert checkpoint["material"]["order_witnesses"] == {}
    assert "trades" not in checkpoint["material"]
    adapter = restored(adapter)
  assert len(set(trade_ids)) == 40
  assert adapter.export_checkpoint()["material"]["state"]["total_trades"] == 40
