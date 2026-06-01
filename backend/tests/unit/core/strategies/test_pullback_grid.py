"""PullbackGridStrategy order lifecycle tests."""

import asyncio
import sys
import types
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

import pytest

if "xtquant" not in sys.modules:
  xtquant_stub = types.ModuleType("xtquant")


  class DummyConstant:
    def __getattr__(self, name):
      return 0

  xtquant_stub.xtconstant = DummyConstant()
  sys.modules["xtquant"] = xtquant_stub

if "strawberry" not in sys.modules:
  strawberry_stub = types.ModuleType("strawberry")
  strawberry_stub.enum = lambda *args, **kwargs: (lambda cls: cls)
  strawberry_stub.enum_value = lambda value, description=None: value
  sys.modules["strawberry"] = strawberry_stub

from core.strategies.pullback_grid import PullbackGridStrategy
from core.strategies.base import (
  OrderStateEvent,
  StrategyCadence,
  StrategyInput,
  TradeExecutionEvent,
  TradeIntentDirection,
)
from core.grid_book import build_grid_book_from_parameters
from models.kline import KLine

pytestmark = pytest.mark.unit


@dataclass
class DummyContext:
  run_id: str
  mode: str
  instruments: list[str]
  parameters: dict
  initial_capital: float = 1_000_000
  current_time: Optional[datetime] = None


@dataclass
class DummyTick:
  stock_code: str
  period: str
  time: datetime
  last_price: float


def make_strategy(
  grid_levels: Optional[Any] = None,
  extra_parameters: Optional[Dict[str, Any]] = None,
) -> PullbackGridStrategy:
  parameters = {
    "pullback_confirm_pct": 0.01,
    "atr_period": 2,
    "trend_ema_period": 3,
    "fast_ema_period": 2,
    "instrument_code": "000001.SZ",
    "initial_swing_shares": 1000,
    "avg_cost": 40.0,
    "grid_levels": grid_levels
    if grid_levels is not None
    else [
      {
        "id": "buy-1",
        "levelIndex": 1,
        "side": "BUY",
        "price": 10.0,
        "shares": 100,
      }
    ],
  }
  parameters.update(extra_parameters or {})
  return PullbackGridStrategy(
    DummyContext(
      run_id="grid-run",
      mode="backtest",
      instruments=["000001.SZ"],
      parameters=parameters,
    )
  )


def tick(price: float, event_time: Optional[datetime] = None) -> DummyTick:
  return DummyTick(
    stock_code="000001.SZ",
    period="tick",
    time=event_time or datetime(2024, 1, 2, 10, 0),
    last_price=price,
  )


def bar(index: int, close: float, high: Optional[float] = None, low: Optional[float] = None):
  return KLine(
    stock_code="000001.SZ",
    period="1d",
    time=datetime(2024, 1, 2 + index, 15, 0),
    open=close,
    high=high if high is not None else close + 0.5,
    low=low if low is not None else close - 0.5,
    close=close,
    pre_close=close - 0.2,
    volume=100000,
    amount=close * 100000,
    settelement_price=0.0,
    open_interest=0,
    suspend_flag=0,
  )


def bar_at(
  event_time: datetime,
  close: float,
  high: Optional[float] = None,
  low: Optional[float] = None,
):
  return KLine(
    stock_code="000001.SZ",
    period="1d",
    time=event_time,
    open=close,
    high=high if high is not None else close + 0.5,
    low=low if low is not None else close - 0.5,
    close=close,
    pre_close=close - 0.2,
    volume=100000,
    amount=close * 100000,
    settelement_price=0.0,
    open_interest=0,
    suspend_flag=0,
  )


def step_tick(
  strategy: PullbackGridStrategy,
  price: float,
  position_profile: Optional[Dict[str, Any]] = None,
  event_time: Optional[datetime] = None,
):
  event = tick(price, event_time=event_time)
  return asyncio.run(
    strategy.step(
      StrategyInput(
        run_id=strategy.context.run_id,
        strategy_id="grid",
        timestamp=event.time,
        cadence=StrategyCadence.TICK,
        instrument_code=event.stock_code,
        event=event,
        position_profile=position_profile or {},
      )
    )
  )


def step_bar(strategy: PullbackGridStrategy, event: KLine):
  return asyncio.run(
    strategy.step(
      StrategyInput(
        run_id=strategy.context.run_id,
        strategy_id="grid",
        timestamp=event.time,
        cadence=StrategyCadence.BAR,
        instrument_code=event.stock_code,
        event=event,
      )
    )
  )


def fill_sell_intent(
  strategy: PullbackGridStrategy,
  intent,
  order_id: str,
  price: float,
  volume: int,
  trade_time: Optional[datetime] = None,
) -> None:
  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id=order_id,
        status="SUBMITTED",
        metadata=intent.metadata,
      )
    )
  )
  asyncio.run(
    strategy.on_trade(
      TradeExecutionEvent(
        order_id=order_id,
        instrument_code="000001.SZ",
        trade_type="SELL",
        price=price,
        volume=volume,
        trade_time=trade_time or datetime(2024, 1, 2, 10, 5),
        metadata=intent.metadata,
      )
    )
  )


def fill_buy_intent(
  strategy: PullbackGridStrategy,
  intent,
  order_id: str,
  price: float,
  volume: int,
  trade_time: Optional[datetime] = None,
) -> None:
  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id=order_id,
        status="SUBMITTED",
        metadata=intent.metadata,
      )
    )
  )
  asyncio.run(
    strategy.on_trade(
      TradeExecutionEvent(
        order_id=order_id,
        instrument_code="000001.SZ",
        trade_type="BUY",
        price=price,
        volume=volume,
        trade_time=trade_time or datetime(2024, 1, 2, 10, 1),
        metadata=intent.metadata,
      )
    )
  )


def test_grid_not_filled_until_trade_callback():
  strategy = make_strategy()
  asyncio.run(strategy.start())
  strategy.state.last_trend_state = "up"

  assert step_tick(strategy, 10.0).trade_intents == []
  output = step_tick(strategy, 10.2)

  assert len(output.trade_intents) == 1
  assert output.trade_intents[0].reason == "grid_pullback_buy"
  grid = strategy.grids[0]
  assert grid.is_pending is True
  assert grid.is_filled is False

  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id=None,
        status="REJECTED",
        metadata={"grid_id": "buy-1", "grid_level": 1},
      )
    )
  )
  assert grid.is_pending is False
  assert grid.is_filled is False

  grid.is_pending = True
  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id="order-1",
        status="SUBMITTED",
        metadata={"grid_id": "buy-1", "grid_level": 1},
      )
    )
  )
  asyncio.run(
    strategy.on_trade(
      TradeExecutionEvent(
        order_id="order-1",
        instrument_code="000001.SZ",
        trade_type="BUY",
        price=10.2,
        volume=100,
        trade_time=datetime(2024, 1, 2, 10, 1),
      )
    )
  )
  assert grid.is_filled is True
  assert grid.is_pending is False
  assert grid.entry_price == 10.2


def test_position_profile_can_block_swing_buy():
  strategy = make_strategy()
  asyncio.run(strategy.start())
  strategy.state.last_trend_state = "up"

  assert step_tick(strategy, 10.0).trade_intents == []
  output = step_tick(
    strategy,
    10.2,
    position_profile={
      "allow_bucket_buy": {"swing": False},
      "allow_bucket_sell": {"swing": True},
    },
  )

  assert output.trade_intents == []
  assert strategy.grids[0].is_pending is False
  assert output.trace_payload["position_profile"]["allow_swing_buy"] is False
  assert (
    output.trace_payload["position_profile"]["buy_disabled_reason"]
    == "position_profile_disallows_swing_buy"
  )


def test_duplicate_intents_are_blocked_while_pending():
  strategy = make_strategy()
  asyncio.run(strategy.start())
  strategy.state.last_trend_state = "up"

  assert step_tick(strategy, 10.0).trade_intents == []

  first = step_tick(strategy, 10.2)
  assert len(first.trade_intents) == 1
  grid = strategy.grids[0]
  assert grid.is_pending is True

  # 下单回报后仍应保持 pending，避免重复下单
  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id="order-pending",
        status="SUBMITTED",
        metadata={"grid_id": "buy-1", "grid_level": 1},
      )
    )
  )
  second = step_tick(strategy, 10.2)
  assert second.trade_intents == []
  assert grid.is_pending is True

  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id="order-pending",
        status="REJECTED",
        metadata={"grid_id": "buy-1", "grid_level": 1},
      )
    )
  )

  step_tick(strategy, 10.0)
  third = step_tick(strategy, 10.2)
  assert len(third.trade_intents) == 1
  assert grid.is_pending is True


def test_partial_fill_keeps_grid_open():
  strategy = make_strategy()
  asyncio.run(strategy.start())
  strategy.state.last_trend_state = "up"

  assert step_tick(strategy, 10.0).trade_intents == []
  first = step_tick(strategy, 10.2)
  assert len(first.trade_intents) == 1
  grid = strategy.grids[0]
  assert grid.is_pending is True

  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id="order-2",
        status="SUBMITTED",
        metadata={"grid_id": "buy-1", "grid_level": 1},
      )
    )
  )

  asyncio.run(
    strategy.on_trade(
      TradeExecutionEvent(
        order_id="order-2",
        instrument_code="000001.SZ",
        trade_type="BUY",
        price=10.2,
        volume=40,
        trade_time=datetime(2024, 1, 2, 10, 2),
      )
    )
  )

  assert grid.filled_volume == 40
  assert grid.is_filled is False
  assert grid.is_pending is True

  asyncio.run(
    strategy.on_trade(
      TradeExecutionEvent(
        order_id="order-2",
        instrument_code="000001.SZ",
        trade_type="BUY",
        price=10.2,
        volume=60,
        trade_time=datetime(2024, 1, 2, 10, 3),
      )
    )
  )

  assert grid.filled_volume == 100
  assert grid.is_filled is True
  assert grid.is_pending is False


def test_sell_grid_generates_intent_and_tracks_pending():
  strategy = make_strategy(
    [
      {
        "id": "sell-1",
        "levelIndex": 1,
        "side": "SELL",
        "price": 45.0,
        "shares": 120,
      }
    ]
  )
  asyncio.run(strategy.start())
  strategy.state.last_trend_state = "up"

  output = step_tick(strategy, 45.0)
  assert len(output.trade_intents) == 1
  intent = output.trade_intents[0]
  assert intent.direction == TradeIntentDirection.SELL
  assert intent.reason == "grid_sell"
  grid = strategy.grids[0]
  assert grid.is_pending is True
  assert grid.pending_volume == 120


def test_sell_grid_partial_fill_and_retry_after_cancel():
  strategy = make_strategy(
    [
      {
        "id": "sell-1",
        "levelIndex": 1,
        "side": "SELL",
        "price": 45.0,
        "shares": 100,
      }
    ]
  )
  asyncio.run(strategy.start())
  strategy.state.last_trend_state = "up"

  assert len(step_tick(strategy, 45.0).trade_intents) == 1
  grid = strategy.grids[0]

  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id="sell-order-1",
        status="SUBMITTED",
        metadata={"grid_id": "sell-1", "grid_level": 1},
      )
    )
  )

  asyncio.run(
    strategy.on_trade(
      TradeExecutionEvent(
        order_id="sell-order-1",
        instrument_code="000001.SZ",
        trade_type="SELL",
        price=45.0,
        volume=30,
        trade_time=datetime(2024, 1, 2, 10, 2),
      )
    )
  )
  assert grid.filled_volume == 30
  assert grid.is_filled is False
  assert grid.is_pending is True

  # 已有未完成仓位时，部分成交后仍不应重复下单
  assert step_tick(strategy, 45.0).trade_intents == []

  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id="sell-order-1",
        status="CANCELLED",
        metadata={"grid_id": "sell-1", "grid_level": 1},
      )
    )
  )

  retry = step_tick(strategy, 45.0)
  assert len(retry.trade_intents) == 1
  assert retry.trade_intents[0].metadata["requested_volume"] == 70
  assert retry.trade_intents[0].reason == "grid_sell"
  assert retry.trade_intents[0].direction == TradeIntentDirection.SELL

  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id="sell-order-2",
        status="SUBMITTED",
        metadata={"grid_id": "sell-1", "grid_level": 1},
      )
    )
  )
  asyncio.run(
    strategy.on_trade(
      TradeExecutionEvent(
        order_id="sell-order-2",
        instrument_code="000001.SZ",
        trade_type="SELL",
        price=45.0,
        volume=70,
        trade_time=datetime(2024, 1, 2, 10, 4),
      )
    )
  )
  assert grid.filled_volume == 100
  assert grid.status == "FILLED"
  assert grid.is_filled is True
  assert grid.is_pending is False
  assert grid.is_day_locked is True


def test_sell_grid_rejection_cools_down_for_trade_date():
  strategy = make_strategy(
    [
      {
        "id": "sell-1",
        "levelIndex": 1,
        "side": "SELL",
        "price": 45.0,
        "shares": 100,
      }
    ]
  )
  asyncio.run(strategy.start())
  strategy.state.last_trend_state = "up"

  first = step_tick(
    strategy,
    45.0,
    event_time=datetime(2024, 1, 2, 10, 0),
  )
  assert len(first.trade_intents) == 1
  intent = first.trade_intents[0]

  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id=None,
        status="REJECTED",
        request=types.SimpleNamespace(order_type="SELL", volume=100),
        metadata=intent.metadata,
        error_message="订单资源冻结失败",
      )
    )
  )

  grid = strategy.grids[0]
  assert grid.last_rejected_side == "SELL"
  assert grid.last_rejected_date == "2024-01-02"

  blocked = step_tick(
    strategy,
    45.0,
    event_time=datetime(2024, 1, 2, 10, 1),
  )
  assert blocked.trade_intents == []
  assert blocked.trace_payload["block_events"][0]["block_reason"] == "sell_rejected_today"

  retry = step_tick(
    strategy,
    45.0,
    event_time=datetime(2024, 1, 3, 10, 0),
  )
  assert len(retry.trade_intents) == 1
  assert retry.trade_intents[0].reason == "grid_sell"


def test_sell_grid_skips_same_day_buy_lot_until_next_trade_date():
  strategy = make_strategy(
    [
      {
        "id": "sell-1",
        "levelIndex": 1,
        "side": "SELL",
        "price": 45.0,
        "shares": 100,
      }
    ],
    extra_parameters={"initial_swing_shares": 0},
  )
  asyncio.run(strategy.start())
  strategy.inventory_lots = [
    strategy._inventory_lot_from_dict(
      {
        "lot_id": "same-day-buy-lot",
        "source_level_id": "buy-1",
        "source_level_index": 1,
        "source": "BUY_FILL",
        "bucket": "swing",
        "entry_price": 40.0,
        "entry_date": "2024-01-02",
        "original_shares": 100,
        "remaining_shares": 100,
        "reserved_shares": 0,
        "target_sell_level_id": "sell-1",
        "target_sell_level_index": 1,
        "status": "OPEN",
      }
    )
  ]
  strategy.state.last_trend_state = "up"

  same_day = step_tick(
    strategy,
    45.0,
    event_time=datetime(2024, 1, 2, 10, 0),
  )
  assert same_day.trade_intents == []
  assert same_day.trace_payload["block_events"][0]["block_reason"] == "insufficient_matching_lot"

  next_day = step_tick(
    strategy,
    45.0,
    event_time=datetime(2024, 1, 3, 10, 0),
  )
  assert len(next_day.trade_intents) == 1
  assert next_day.trade_intents[0].reason == "grid_sell"


def test_initial_swing_inventory_is_split_across_sell_waterlines():
  snapshot = build_grid_book_from_parameters(
    {
      "instrument_code": "562500.SH",
      "initial_swing_shares": 20000,
      "avg_cost": 0.974,
      "grid_levels": [
        {
          "id": f"sell-{index}",
          "levelIndex": index,
          "side": "SELL",
          "price": 1.02 + index * 0.02,
          "shares": 4000,
        }
        for index in range(1, 6)
      ],
    },
    run_id="grid-run",
    instrument_code="562500.SH",
  )

  lots = snapshot["inventory_lots"]

  assert [lot["remaining_shares"] for lot in lots] == [4000] * 5
  assert [lot["target_sell_level_id"] for lot in lots] == [
    "sell-1",
    "sell-2",
    "sell-3",
    "sell-4",
    "sell-5",
  ]
  assert [lot["target_sell_level_index"] for lot in lots] == [1, 2, 3, 4, 5]


def test_initial_swing_sell_waterlines_do_not_reuse_first_sell_level():
  strategy = make_strategy(
    [
      {
        "id": f"sell-{index}",
        "levelIndex": index,
        "side": "SELL",
        "price": 1.02 + index * 0.02,
        "shares": 4000,
      }
      for index in range(1, 6)
    ],
    extra_parameters={
      "initial_swing_shares": 20000,
      "avg_cost": 0.974,
    },
  )
  asyncio.run(strategy.start())
  strategy.state.last_trend_state = "up"

  assert [lot.target_sell_level_id for lot in strategy.inventory_lots] == [
    "sell-1",
    "sell-2",
    "sell-3",
    "sell-4",
    "sell-5",
  ]

  first = step_tick(strategy, 1.04)
  assert len(first.trade_intents) == 1
  first_intent = first.trade_intents[0]
  assert first_intent.reason == "grid_sell"
  assert first_intent.metadata["grid_id"] == "sell-1"
  assert first_intent.metadata["requested_volume"] == 4000
  assert first_intent.metadata["inventory_lot_ids"] == [
    "initial-swing-000001.SZ-sell-1"
  ]
  fill_sell_intent(strategy, first_intent, "sell-order-1", 1.04, 4000)

  repeated_first_level = step_tick(strategy, 1.05)
  assert repeated_first_level.trade_intents == []

  second = step_tick(strategy, 1.06)
  assert len(second.trade_intents) == 1
  second_intent = second.trade_intents[0]
  assert second_intent.metadata["grid_id"] == "sell-2"
  assert second_intent.metadata["requested_volume"] == 4000
  assert second_intent.metadata["inventory_lot_ids"] == [
    "initial-swing-000001.SZ-sell-2"
  ]
  fill_sell_intent(strategy, second_intent, "sell-order-2", 1.06, 4000)

  third = step_tick(strategy, 1.08)
  assert len(third.trade_intents) == 1
  assert third.trade_intents[0].metadata["grid_id"] == "sell-3"


def test_legacy_initial_swing_lot_without_target_remains_sellable():
  strategy = make_strategy(
    [
      {
        "id": "sell-1",
        "levelIndex": 1,
        "side": "SELL",
        "price": 45.0,
        "shares": 120,
      }
    ],
    extra_parameters={"initial_swing_shares": 0},
  )
  asyncio.run(strategy.start())
  strategy.inventory_lots = [
    strategy._inventory_lot_from_dict(
      {
        "lot_id": "legacy-initial-swing",
        "source": "INITIAL_SWING",
        "bucket": "swing",
        "entry_price": 40.0,
        "original_shares": 120,
        "remaining_shares": 120,
        "reserved_shares": 0,
        "status": "OPEN",
      }
    )
  ]
  strategy.state.last_trend_state = "up"

  output = step_tick(strategy, 45.0)

  assert len(output.trade_intents) == 1
  assert output.trade_intents[0].metadata["inventory_lot_ids"] == [
    "legacy-initial-swing"
  ]


def test_bar_warmup_does_not_generate_grid_or_intent():
  strategy = make_strategy(
    [],
    extra_parameters={
      "grid_count": 2,
      "grid_atr_multiplier": 1.0,
      "position_per_grid": 100,
    },
  )
  asyncio.run(strategy.start())

  output = step_bar(strategy, bar(0, 10.0))

  assert output.trade_intents == []
  assert output.decision_tags == ["warming_up"]
  assert strategy.grids == []


def test_uptrend_generates_dynamic_atr_grids():
  strategy = make_strategy(
    [],
    extra_parameters={
      "grid_count": 2,
      "grid_atr_multiplier": 1.0,
      "position_per_grid": 100,
    },
  )
  asyncio.run(strategy.start())

  for index, close in enumerate([10.0, 10.5, 11.0]):
    step_bar(strategy, bar(index, close))

  assert strategy.state.last_trend_state == "up"
  assert len(strategy.grids) == 2
  assert all(grid.side == "BUY" for grid in strategy.grids)
  assert all(grid.trigger_price < 11.0 for grid in strategy.grids)


def test_pending_buy_does_not_trigger_bar_exit_before_trade():
  strategy = make_strategy()
  asyncio.run(strategy.start())
  strategy.state.last_trend_state = "up"

  step_tick(strategy, 10.0)
  assert len(step_tick(strategy, 10.2).trade_intents) == 1
  output = None
  for index, close in enumerate([10.5, 11.0, 11.5]):
    output = step_bar(strategy, bar(index, close))

  assert output is not None
  assert output.trade_intents == []
  assert strategy.grids[0].is_pending is True
  assert strategy.grids[0].is_filled is False


def test_buy_slot_does_not_generate_atr_profit_take():
  strategy = make_strategy()
  asyncio.run(strategy.start())
  strategy.state.last_trend_state = "up"

  step_tick(strategy, 10.0)
  buy = step_tick(strategy, 10.2).trade_intents[0]
  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id="buy-order-profit",
        status="SUBMITTED",
        metadata=buy.metadata,
      )
    )
  )
  asyncio.run(
    strategy.on_trade(
      TradeExecutionEvent(
        order_id="buy-order-profit",
        instrument_code="000001.SZ",
        trade_type="BUY",
        price=10.2,
        volume=100,
        trade_time=datetime(2024, 1, 2, 10, 1),
      )
    )
  )

  output = None
  for index, close in enumerate([10.5, 11.2, 11.4]):
    output = step_bar(strategy, bar(index, close, high=close + 0.2, low=close - 0.2))

  assert output is not None
  assert output.trade_intents == []
  assert strategy.grids[0].is_pending is False
  assert strategy.grids[0].is_filled is True
  assert strategy.grids[0].filled_volume == 100


def test_buy_slot_does_not_generate_atr_stop_loss():
  strategy = make_strategy()
  asyncio.run(strategy.start())
  strategy.state.last_trend_state = "up"

  step_tick(strategy, 10.0)
  buy = step_tick(strategy, 10.2).trade_intents[0]
  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id="buy-order-stop",
        status="SUBMITTED",
        metadata=buy.metadata,
      )
    )
  )
  asyncio.run(
    strategy.on_trade(
      TradeExecutionEvent(
        order_id="buy-order-stop",
        instrument_code="000001.SZ",
        trade_type="BUY",
        price=10.2,
        volume=100,
        trade_time=datetime(2024, 1, 2, 10, 1),
      )
    )
  )

  output = None
  for index, close in enumerate([10.0, 9.6, 9.4]):
    output = step_bar(strategy, bar(index, close, high=close + 0.2, low=close - 0.2))

  assert output is not None
  assert output.trade_intents == []
  assert strategy.grids[0].is_filled is True
  assert strategy.grids[0].filled_volume == 100


def test_buy_grid_does_not_wait_rearm_without_sell_waterline_exit():
  strategy = make_strategy()
  asyncio.run(strategy.start())
  strategy.state.last_trend_state = "up"

  step_tick(strategy, 10.0, event_time=datetime(2024, 1, 2, 10, 0))
  buy = step_tick(
    strategy,
    10.2,
    event_time=datetime(2024, 1, 2, 10, 1),
  ).trade_intents[0]
  fill_buy_intent(
    strategy,
    buy,
    "buy-order-rearm",
    10.2,
    100,
    trade_time=datetime(2024, 1, 2, 10, 2),
  )

  output = None
  for index, close in enumerate([10.0, 9.6, 9.4]):
    output = step_bar(
      strategy,
      bar_at(
        datetime(2024, 1, 3 + index, 15, 0),
        close,
        high=close + 0.2,
        low=close - 0.2,
      ),
    )

  assert output is not None
  grid = strategy.grids[0]
  assert output.trade_intents == []
  assert grid.status == "FILLED"
  assert grid.waiting_reason == "waiting_swing_inventory"


def test_sell_waterline_release_waits_for_price_recross():
  strategy = make_strategy(
    [
      {
        "id": "buy-1",
        "levelIndex": -1,
        "side": "BUY",
        "price": 10.0,
        "shares": 100,
      },
      {
        "id": "sell-1",
        "levelIndex": 1,
        "side": "SELL",
        "price": 10.5,
        "shares": 100,
      },
    ],
    extra_parameters={"initial_swing_shares": 0},
  )
  asyncio.run(strategy.start())
  strategy.state.last_trend_state = "up"

  step_tick(strategy, 10.0, event_time=datetime(2024, 1, 2, 10, 0))
  buy = step_tick(
    strategy,
    10.2,
    event_time=datetime(2024, 1, 2, 10, 1),
  ).trade_intents[0]
  fill_buy_intent(
    strategy,
    buy,
    "buy-order-waterline-rearm",
    10.2,
    100,
    trade_time=datetime(2024, 1, 2, 10, 2),
  )

  sell = step_tick(
    strategy,
    10.6,
    event_time=datetime(2024, 1, 3, 10, 0),
  ).trade_intents[0]
  assert sell.reason == "grid_sell"
  fill_sell_intent(
    strategy,
    sell,
    "sell-order-waterline-rearm",
    10.6,
    100,
    trade_time=datetime(2024, 1, 3, 10, 1),
  )

  buy_grid = next(grid for grid in strategy.grids if grid.grid_id == "buy-1")
  assert buy_grid.status == "WAIT_REARM"
  assert buy_grid.waiting_reason == "waiting_price_recross"


def test_state_schema_has_defaults():
  schema = PullbackGridStrategy.get_state_schema()
  defaults = schema.build_defaults()
  assert defaults["last_trend_state"] == "undefined"
  assert defaults["warned_no_grids"] is False
  assert defaults["warned_invalid_tick"] is False
