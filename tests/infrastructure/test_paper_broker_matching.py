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


async def test_restart_after_partial_fill_matches_continuous_execution_and_fees():
  continuous, restarted = matching(), matching()
  for adapter in (continuous, restarted):
    result = await adapter.place(order_id="order-1", request=request(), now=NOW)
    assert result.orders[0].status is OrderStatus.SUBMITTED
    assert result.trades == ()
    first = await adapter.process_quote(event_id="q1", quote=quote(1))
    assert first.orders[0].status is OrderStatus.PARTIAL_FILLED
    assert first.orders[0].filled_volume == 500
    assert first.trades[0].volume == 500
  restarted = restored(restarted)
  continuous_result = await continuous.process_quote(event_id="q2", quote=quote(2))
  restarted_result = await restarted.process_quote(event_id="q2", quote=quote(2))
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
  first = await adapter.process_quote(event_id="q1", quote=quote(1))
  replay = PaperBrokerMatching.restore(
    scope_execution_id="paper-execution", checkpoint=initial
  )
  assert (
    await replay.process_quote(event_id="q1", quote=quote(1))
  ).trades == first.trades
  adapter = restored(adapter)
  prior = adapter.export_checkpoint()
  repeated = await adapter.process_quote(event_id="q1", quote=quote(1))
  assert repeated.duplicate and repeated.orders == () and repeated.trades == ()
  assert adapter.export_checkpoint() == prior
  order_retry = await adapter.place(order_id="order", request=request(), now=NOW)
  assert order_retry.duplicate and order_retry.orders[0].filled_volume == 500
  with pytest.raises(ValueError, match="ORDER_IDEMPOTENCY"):
    await adapter.place(order_id="order", request=request(volume=900), now=NOW)
  with pytest.raises(ValueError, match="QUOTE_IDEMPOTENCY"):
    await adapter.process_quote(event_id="q1", quote=quote(1, depth=800))
  assert adapter.export_checkpoint() == prior


async def test_same_time_new_order_waits_until_strictly_later_quote():
  adapter = matching()
  await adapter.process_quote(event_id="baseline", quote=quote(0))
  await adapter.place(
    order_id="order", request=request(), now=NOW + timedelta(seconds=1)
  )
  same_time = await adapter.process_quote(event_id="q1", quote=quote(1))
  assert same_time.trades == ()
  assert (await adapter.process_quote(event_id="q2", quote=quote(2))).trades


@pytest.mark.parametrize("seconds", [0, 1])
async def test_old_or_duplicate_source_under_new_event_id_fails_closed(seconds):
  adapter = matching()
  await adapter.process_quote(event_id="q1", quote=quote(1))
  with pytest.raises(ValueError, match="NOT_CAUSAL"):
    await adapter.process_quote(event_id="other", quote=quote(seconds))


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
      event_id="bad", quote=replace(quote(1), **{field: value})
    )
  assert adapter.export_checkpoint() == before


async def test_expiry_is_applied_before_matching_and_is_recoverable():
  adapter = matching()
  await adapter.place(order_id="order", request=request(expires=1), now=NOW)
  adapter = restored(adapter)
  result = await adapter.process_quote(event_id="at-ttl", quote=quote(1))
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
  buy = await adapter.process_quote(event_id="buy-quote", quote=quote(1))
  assert buy.account.positions[position.instrument_code].available_volume == 800
  adapter = restored(adapter)
  await adapter.place(
    order_id="sell-old",
    request=request(side=OrderType.SELL, volume=800),
    now=NOW + timedelta(seconds=2),
  )
  sold = await adapter.process_quote(event_id="sell-quote", quote=quote(3, depth=800))
  assert sold.account.positions[position.instrument_code].available_volume == 0
  assert sold.account.positions[position.instrument_code].long_volume == 300
  adapter = restored(adapter)
  next_day = await adapter.process_quote(event_id="next-day", quote=quote(86400))
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
  partial = await adapter.process_quote(event_id="q1", quote=quote(1))
  adapter = restored(adapter)
  result = await adapter.cancel(order_id="order", now=NOW + timedelta(seconds=2))
  assert result.orders[0].status is OrderStatus.CANCELLED
  assert result.orders[0].commission == partial.orders[0].commission
  assert (await adapter.process_quote(event_id="q3", quote=quote(3))).trades == ()
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
    await adapter.process_quote(event_id="q1", quote=quote(1))
  assert adapter.export_checkpoint() == before


async def test_quote_depth_is_shared_across_restored_pending_orders():
  adapter = matching()
  await adapter.place(order_id="first", request=request(volume=200), now=NOW)
  await adapter.place(order_id="second", request=request(volume=800), now=NOW)
  adapter = restored(adapter)
  result = await adapter.process_quote(event_id="one-book", quote=quote(1))
  assert [(trade.order_id, trade.volume) for trade in result.trades] == [
    ("first", 200),
    ("second", 300),
  ]


async def test_explicit_zero_depth_gives_no_queue_credit():
  adapter = matching()
  await adapter.place(order_id="order", request=request(), now=NOW)
  result = await adapter.process_quote(event_id="empty-book", quote=quote(1, depth=0))
  assert result.trades == ()
  assert (
    await restored(adapter).process_quote(event_id="fresh-book", quote=quote(2))
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
    result = await adapter.process_quote(event_id=f"q{index:04d}", quote=quote(index))
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
  repeated = await adapter.process_quote(event_id="q1000", quote=quote(1000))
  assert repeated.duplicate and repeated.trades == ()
  with pytest.raises(ValueError, match="QUOTE_IDEMPOTENCY"):
    await adapter.process_quote(event_id="q1000", quote=quote(1000, depth=800))
  with pytest.raises(ValueError, match="NOT_CAUSAL"):
    await adapter.process_quote(event_id="q0001", quote=quote(1))


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
      event_id=f"fill-{index}", quote=quote(index * 2 + 1)
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
