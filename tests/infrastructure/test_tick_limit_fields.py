"""Realtime tick limit-price transport tests."""

from datetime import datetime

import pytest
from quantx_domain.trading.instrument_master import InstrumentMaster
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_infrastructure.models.tick import Tick

pytestmark = pytest.mark.unit


def test_xtquant_tick_preserves_limit_prices_for_strategy_and_risk_layers():
  tick = Tick.from_xtquant(
    "000001.SZ",
    {
      "time": int(datetime(2026, 7, 31, 10, 0).timestamp() * 1000),
      "lastPrice": 10.99,
      "open": 10.50,
      "high": 10.99,
      "low": 10.40,
      "lastClose": 10.00,
      "amount": 200_000_000,
      "volume": 20_000_000,
      "upperLimit": 11.00,
      "lowerLimit": 9.00,
      "priceTick": 0.01,
      "askPrice": [11.00, 0, 0, 0, 0],
      "bidPrice": [10.99, 10.98, 0, 0, 0],
      "askVol": [1000, 0, 0, 0, 0],
      "bidVol": [100000, 50000, 0, 0, 0],
    },
  )

  snapshot = MarketDataSnapshot.from_tick(tick)

  assert tick.up_stop_price == pytest.approx(11.00)
  assert tick.down_stop_price == pytest.approx(9.00)
  assert tick.price_tick == pytest.approx(0.01)
  assert snapshot.limit_up == pytest.approx(11.00)
  assert snapshot.limit_down == pytest.approx(9.00)
  assert snapshot.price_tick == pytest.approx(0.01)


def test_instrument_master_accepts_persisted_stop_price_field_names():
  snapshot = InstrumentMaster().build_snapshot(
    instrument_code="000001.SZ",
    market_data=MarketDataSnapshot(instrument_code="000001.SZ"),
    instrument={
      "up_stop_price": 11.0,
      "down_stop_price": 9.0,
    },
  )

  assert snapshot.limit_up == pytest.approx(11.0)
  assert snapshot.limit_down == pytest.approx(9.0)
  assert snapshot.data_quality == "OK"


def test_limit_prices_are_derived_only_when_backtest_rate_is_explicit():
  tick = Tick.from_xtquant(
    "000001.SZ",
    {
      "time": int(datetime(2026, 7, 31, 10, 0).timestamp() * 1000),
      "lastPrice": 10.99,
      "lastClose": 10.00,
    },
  )

  strict_snapshot = MarketDataSnapshot.from_tick(tick)
  derived_snapshot = MarketDataSnapshot.from_tick(tick, limit_rate=0.10)

  assert strict_snapshot.limit_up is None
  assert strict_snapshot.limit_down is None
  assert strict_snapshot.source == "tick"
  assert derived_snapshot.limit_up == pytest.approx(11.0)
  assert derived_snapshot.limit_down == pytest.approx(9.0)
  assert derived_snapshot.source == "tick_derived_limits"
