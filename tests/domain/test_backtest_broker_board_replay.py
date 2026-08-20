from datetime import datetime, timedelta

import pytest
from quantx_domain.brokers.backtest import BacktestBroker
from quantx_domain.brokers.base import (
  OrderRequest,
  OrderStatus,
  OrderType,
  PriceType,
)
from quantx_domain.trading.market_rules import MarketDataSnapshot


def _book(timestamp, *, asks=None, ask_volumes=None, price=10.0):
  return MarketDataSnapshot(
    instrument_code="000001.SZ",
    timestamp=timestamp,
    price=price,
    high=price,
    low=price,
    volume=100_000,
    limit_up=11.0,
    limit_down=9.0,
    ask_price=list(asks or [10.0, 10.1, 10.2, 10.3, 10.4]),
    ask_vol=list(ask_volumes or [100, 100, 100, 100, 100]),
    bid_price=[9.99, 9.98, 9.97, 9.96, 9.95],
    bid_vol=[100, 100, 100, 100, 100],
  )


def _broker(initial_capital=100_000.0):
  return BacktestBroker(
    initial_capital=initial_capital,
    commission_rate=0.0,
    min_commission=0.0,
    transfer_fee_rate=0.0,
    stamp_tax_rate=0.0,
    slippage_rate=0.0,
    book_depth_participation_pct=1.0,
    strict_book_depth=True,
    no_queue_credit=True,
    defer_new_orders_until_next_quote=True,
  )


@pytest.mark.asyncio
async def test_strict_market_order_cannot_fill_on_signal_tick_and_uses_book_vwap():
  broker = _broker()
  signal_at = datetime(2024, 1, 2, 10, 0)
  await broker.update_market_data(
    "000001.SZ",
    10.0,
    signal_at,
    market_data=_book(signal_at),
  )

  order = await broker.place_order(
    OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.BUY,
      price_type=PriceType.MARKET,
      volume=150,
      price=10.1,
    )
  )

  assert order.status == OrderStatus.SUBMITTED
  assert broker.trades == []

  next_tick = signal_at + timedelta(milliseconds=500)
  await broker.update_market_data(
    "000001.SZ",
    10.0,
    next_tick,
    market_data=_book(next_tick),
  )

  assert order.status == OrderStatus.FILLED
  assert order.filled_volume == 150
  assert order.avg_price == pytest.approx((100 * 10.0 + 50 * 10.1) / 150)
  assert broker.trades[0].trade_time == next_tick


@pytest.mark.asyncio
async def test_strict_orders_share_one_ticks_visible_book_capacity():
  broker = _broker()
  first = await broker.place_order(
    OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=100,
      price=10.0,
    )
  )
  second = await broker.place_order(
    OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=100,
      price=10.0,
    )
  )
  timestamp = datetime(2024, 1, 2, 10, 0)

  await broker.update_market_data(
    "000001.SZ",
    10.0,
    timestamp,
    market_data=_book(
      timestamp,
      asks=[10.0, 10.1, 10.2, 10.3, 10.4],
      ask_volumes=[100, 0, 0, 0, 0],
    ),
  )

  assert first.status == OrderStatus.FILLED
  assert second.status == OrderStatus.SUBMITTED
  assert len(broker.trades) == 1


@pytest.mark.asyncio
async def test_strict_depth_never_falls_back_to_total_tick_volume():
  broker = _broker()
  order = await broker.place_order(
    OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=100,
      price=10.0,
    )
  )
  timestamp = datetime(2024, 1, 2, 10, 0)
  incomplete = _book(timestamp)
  incomplete.ask_price = [10.0]
  incomplete.ask_vol = [100_000]

  await broker.update_market_data(
    "000001.SZ",
    10.0,
    timestamp,
    market_data=incomplete,
  )

  assert order.status == OrderStatus.SUBMITTED
  assert broker.trades == []
  assert broker.get_constraint_statistics()["missing_book_depth_blocked"] == 1


@pytest.mark.asyncio
async def test_sealed_limit_has_no_queue_credit():
  broker = _broker()
  order = await broker.place_order(
    OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=100,
      price=11.0,
    )
  )
  timestamp = datetime(2024, 1, 2, 10, 0)

  await broker.update_market_data(
    "000001.SZ",
    11.0,
    timestamp,
    market_data=_book(
      timestamp,
      price=11.0,
      asks=[0, 0, 0, 0, 0],
      ask_volumes=[0, 0, 0, 0, 0],
    ),
  )

  assert order.status == OrderStatus.SUBMITTED
  assert broker.trades == []
  stats = broker.get_constraint_statistics()
  assert stats["limit_up_buy_blocked"] == 1
  assert stats["no_queue_credit_blocked"] == 1


@pytest.mark.asyncio
async def test_deferred_orders_reserve_shared_cash_before_fill():
  broker = _broker(initial_capital=1_500.0)
  first = await broker.place_order(
    OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=100,
      price=10.0,
    )
  )
  second = await broker.place_order(
    OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=100,
      price=10.0,
    )
  )

  assert first.status == OrderStatus.SUBMITTED
  assert second.status == OrderStatus.REJECTED
