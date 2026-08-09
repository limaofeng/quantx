"""AshareSupermarketStrategy tests for intent-only strategy semantics."""

import asyncio
import sys
import types
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd
import pytest

if "strawberry" not in sys.modules:
  strawberry_stub = types.ModuleType("strawberry")

  def enum(*args, **kwargs):
    def decorator(cls):
      return cls
    return decorator

  def enum_value(value, description=None):
    return value

  strawberry_stub.enum = enum
  strawberry_stub.enum_value = enum_value
  sys.modules["strawberry"] = strawberry_stub

from quantx_domain.strategies.ashare_supermarket import AshareSupermarketStrategy
from quantx_domain.strategies.base import (
  StrategyCadence,
  StrategyInput,
  TradeExecutionEvent,
)

pytestmark = pytest.mark.unit


@dataclass
class DummyContext:
  run_id: str
  mode: str
  instruments: list[str]
  parameters: dict
  initial_capital: float = 1_000_000
  backtest_start_time: Optional[datetime] = None
  backtest_end_time: Optional[datetime] = None
  current_time: Optional[datetime] = None


@dataclass
class DummyBar:
  stock_code: str
  period: str
  time: datetime
  open: float
  high: float
  low: float
  close: float
  volume: float = 1000.0
  amount: float = 100000.0
  suspend_flag: int = 0


def make_strategy(params=None):
  context = DummyContext(
    run_id="test-run",
    mode="backtest",
    instruments=["000001.SZ"],
    parameters=params or {},
  )
  return AshareSupermarketStrategy(context)


def make_bar(code: str, price: float, at: datetime) -> DummyBar:
  return DummyBar(
    stock_code=code,
    period="1d",
    time=at,
    open=price,
    high=price,
    low=price,
    close=price,
  )


def set_candidate(strategy: AshareSupermarketStrategy, code: str = "000001.SZ") -> None:
  strategy.set_candidates(
    pd.DataFrame(
      [
        {
          "code": code,
          "box_support": 100.0,
          "box_resistance": 110.0,
          "structure_ok": True,
          "box_valid": True,
        }
      ]
    )
  )


def step_bar(strategy: AshareSupermarketStrategy, bar: DummyBar, position_profile=None):
  effective_position_profile = (
    position_profile
    if position_profile is not None
    else {
      "allow_bucket_buy": {"swing": True},
      "allow_bucket_sell": {"swing": True},
      "bucket_caps": {"swing": {"max_pct": 1.0}},
      "profile": "TEST",
    }
  )
  return asyncio.run(
    strategy.step(
      StrategyInput(
        run_id=strategy.context.run_id,
        strategy_id="supermarket",
        timestamp=bar.time,
        cadence=StrategyCadence.BAR,
        instrument_code=bar.stock_code,
        event=bar,
        position_profile=effective_position_profile,
      )
    )
  )


def warmup_bar(strategy: AshareSupermarketStrategy, bar: DummyBar, position_profile=None):
  return asyncio.run(
    strategy.warmup(
      StrategyInput(
        run_id=strategy.context.run_id,
        strategy_id="supermarket",
        timestamp=bar.time,
        cadence=StrategyCadence.BAR,
        instrument_code=bar.stock_code,
        event=bar,
        position_profile=position_profile or {},
      )
    )
  )


def test_buy_signal_is_intent_not_private_position():
  strategy = make_strategy({"target_positions": 1})
  asyncio.run(strategy.start())
  set_candidate(strategy)

  output = step_bar(strategy, make_bar("000001.SZ", 101.0, datetime(2024, 1, 2, 10, 0)))

  assert len(output.trade_intents) == 1
  intent = output.trade_intents[0]
  assert intent.direction.value == "BUY"
  assert intent.target_position_pct is not None
  assert intent.metadata["reason"] == "box_support_buy"
  assert "000001.SZ" in strategy.pending_entry_codes
  assert not hasattr(strategy, "pending_sells")
  assert not hasattr(strategy, "cash")


def test_warmup_updates_box_history_without_creating_pending_entry():
  strategy = make_strategy({"target_positions": 1})
  asyncio.run(strategy.start())
  set_candidate(strategy)

  result = warmup_bar(
    strategy,
    make_bar("000001.SZ", 101.0, datetime(2024, 1, 1, 10, 0)),
    position_profile={"allow_bucket_buy": {"swing": True}},
  )

  assert result is None
  assert strategy.price_history["000001.SZ"] == [101.0]
  assert strategy.pending_entry_codes == set()
  assert strategy.daily_entry_signal_count == 0


def test_position_profile_blocks_swing_buy():
  strategy = make_strategy({"target_positions": 1})
  asyncio.run(strategy.start())
  set_candidate(strategy)

  output = step_bar(
    strategy,
    make_bar("000001.SZ", 101.0, datetime(2024, 1, 2, 10, 0)),
    position_profile={
      "allow_bucket_buy": {"swing": False},
      "allow_bucket_sell": {"swing": True},
    },
  )

  assert output.trade_intents == []
  assert "000001.SZ" not in strategy.pending_entry_codes


def test_position_profile_caps_swing_buy_size():
  strategy = make_strategy({"target_positions": 1})
  asyncio.run(strategy.start())
  set_candidate(strategy)

  output = step_bar(
    strategy,
    make_bar("000001.SZ", 101.0, datetime(2024, 1, 2, 10, 0)),
    position_profile={
      "allow_bucket_buy": {"swing": True},
      "allow_bucket_sell": {"swing": True},
      "bucket_caps": {"swing": {"max_pct": 0.02}},
      "profile": "CAUTIOUS",
    },
  )

  assert len(output.trade_intents) == 1
  assert output.trade_intents[0].target_position_pct == 0.02
  assert output.trade_intents[0].metadata["position_profile_cap"] == "CAUTIOUS"


def test_trade_callbacks_update_algorithm_tracker_only():
  strategy = make_strategy()
  asyncio.run(strategy.start())

  asyncio.run(
    strategy.on_trade(
      TradeExecutionEvent(
        order_id="buy-1",
        instrument_code="000001.SZ",
        trade_type="BUY",
        price=100.0,
        volume=200,
        trade_time=datetime(2024, 1, 2, 10, 0),
      )
    )
  )
  assert strategy.tracked_positions["000001.SZ"].volume == 200

  asyncio.run(
    strategy.on_trade(
      TradeExecutionEvent(
        order_id="sell-1",
        instrument_code="000001.SZ",
        trade_type="SELL",
        price=95.0,
        volume=200,
        trade_time=datetime(2024, 1, 3, 10, 0),
      )
    )
  )
  assert "000001.SZ" not in strategy.tracked_positions
  assert strategy.loss_streak == 1


def test_sell_signal_is_sell_all_intent_without_t1_queue():
  strategy = make_strategy({"stop_loss_pct": 0.03})
  asyncio.run(strategy.start())
  set_candidate(strategy)
  asyncio.run(
    strategy.on_trade(
      TradeExecutionEvent(
        order_id="buy-1",
        instrument_code="000001.SZ",
        trade_type="BUY",
        price=100.0,
        volume=100,
        trade_time=datetime(2024, 1, 2, 10, 0),
      )
    )
  )

  output = step_bar(strategy, make_bar("000001.SZ", 95.0, datetime(2024, 1, 2, 14, 0)))

  assert len(output.trade_intents) == 1
  assert output.trade_intents[0].direction.value == "SELL"
  assert output.trade_intents[0].metadata["sell_all"] is True
  assert output.trade_intents[0].metadata["reason"] == "stop_loss"


def test_config_validation_still_applies():
  invalid = make_strategy({"min_position_pct": 0.08, "max_position_pct": 0.06})
  with pytest.raises(ValueError):
    asyncio.run(invalid.initialize())

  strategy = make_strategy({"buy_threshold_pct_60m": 0.01})
  asyncio.run(strategy.initialize())
  assert strategy._get_period_settings("60m").buy_threshold_pct == 0.01


def test_data_requirements_use_daily_bars_only():
  assert AshareSupermarketStrategy.get_data_requirements() == {
    "use_tick_data": False,
    "periods": ["1d"],
  }
