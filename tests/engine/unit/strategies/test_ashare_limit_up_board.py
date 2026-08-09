"""AshareLimitUpBoardStrategy and board-exit rule tests."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pytest
from quantx_domain.strategies.ashare_limit_up_board import (
  AshareLimitUpBoardStrategy,
)
from quantx_domain.strategies.base import (
  OrderStateEvent,
  StrategyCadence,
  StrategyInput,
  TradeExecutionEvent,
  TradeIntentExecutionMode,
)
from quantx_domain.trading.exit_plan import (
  ExitEvaluationContext,
  ExitPlanBook,
  ExitPlanTemplate,
  ExitRuleType,
  ExitT1Policy,
)
from quantx_domain.trading.market_rules import MarketDataSnapshot

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


def make_strategy(parameters=None) -> AshareLimitUpBoardStrategy:
  return AshareLimitUpBoardStrategy(
    DummyContext(
      run_id="board-run",
      mode="backtest",
      instruments=["000001.SZ"],
      parameters=dict(parameters or {}),
    )
  )


def make_input(
  strategy: AshareLimitUpBoardStrategy,
  *,
  timestamp: datetime = datetime(2026, 7, 31, 10, 0),
  price: float = 10.99,
  limit_up: Optional[float] = 11.0,
  open_price: float = 10.50,
  high: float = 10.99,
  low: float = 10.40,
  bid1_volume: int = 100_000,
  amount: float = 200_000_000,
  risk_caps=None,
  position_profile=None,
  portfolio_state=None,
  open_orders=None,
  exit_plans=None,
) -> StrategyInput:
  market_data = MarketDataSnapshot(
    instrument_code="000001.SZ",
    timestamp=timestamp,
    price=price,
    open=open_price,
    high=high,
    low=low,
    close=price,
    amount=amount,
    price_tick=0.01,
    limit_up=limit_up,
    limit_down=9.0,
    bid_price=[price],
    ask_price=[limit_up or price],
    bid_vol=[bid1_volume],
    ask_vol=[1000],
  )
  return StrategyInput(
    run_id=strategy.context.run_id,
    strategy_id="limit-up-board",
    timestamp=timestamp,
    cadence=StrategyCadence.TICK,
    instrument_code="000001.SZ",
    market_data=market_data,
    event=market_data,
    market_context={
      "data_quality": "OK",
      "context_score": 0.2,
      "instrument_master": {
        "limit_up": limit_up,
        "limit_down": 9.0,
        "data_quality": "OK",
      },
    },
    risk_caps=dict(risk_caps or {}),
    position_profile=dict(position_profile or {}),
    portfolio_state=dict(portfolio_state or {}),
    open_orders=list(open_orders or []),
    exit_plans=list(exit_plans or []),
  )


def step(strategy: AshareLimitUpBoardStrategy, strategy_input: StrategyInput):
  return asyncio.run(strategy.step(strategy_input))


def test_near_limit_price_creates_one_manual_entry_with_exit_plan():
  strategy = make_strategy()
  asyncio.run(strategy.start())

  output = step(strategy, make_input(strategy))

  assert len(output.trade_intents) == 1
  intent = output.trade_intents[0]
  assert intent.direction.value == "BUY"
  assert intent.bucket == "swing"
  assert intent.reason == "limit_up_board_entry"
  assert intent.target_position_pct == pytest.approx(0.05)
  assert intent.limit_price_hint == pytest.approx(11.0)
  assert intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
  assert intent.metadata["order_ttl_ms"] == 15000
  assert strategy.pending_manual_intent_ids() == [intent.intent_id]

  template = ExitPlanTemplate.from_dict(intent.metadata["exit_plan_template"])
  assert template.t1_policy == ExitT1Policy.WAIT_UNTIL_SELLABLE
  assert template.auto_exit_authorized is False
  assert [rule.strategy for rule in template.rules] == [
    ExitRuleType.LIMIT_UP_BREAK.value,
    ExitRuleType.TRAILING_PRICE_DRAWDOWN.value,
    ExitRuleType.MAX_HOLDING_DAYS.value,
  ]
  assert template.rules[0].priority == 1000
  assert template.rules[0].sizing.mode.value == "ALL_REMAINING"
  assert template.rules[1].once is True
  assert template.rules[1].sizing.value == pytest.approx(50)


@pytest.mark.parametrize(
  ("input_overrides", "expected_reason"),
  [
    ({"limit_up": None}, "missing_limit_up"),
    (
      {
        "price": 11.0,
        "open_price": 11.0,
        "high": 11.0,
        "low": 11.0,
      },
      "one_word_limit_up_blocked",
    ),
    ({"price": 10.97}, "not_near_limit_up"),
    (
      {
        "price": 11.0,
        "open_price": 10.50,
        "high": 11.0,
        "low": 10.40,
      },
      "limit_up_already_sealed",
    ),
    ({"risk_caps": {"kill_switch": True}}, "risk_kill_switch"),
    ({"risk_caps": {"allow_buy": False}}, "risk_disallow_buy"),
    (
      {"risk_caps": {"allow_intraday_swing_buy": False}},
      "risk_disallow_swing_buy",
    ),
    (
      {"position_profile": {"allow_swing_buy": False}},
      "profile_disallow_swing_buy",
    ),
    (
      {"portfolio_state": {"positions": {"000001.SZ": {"long_volume": 100}}}},
      "position_exists",
    ),
  ],
)
def test_conservative_entry_blocks_are_audited(input_overrides, expected_reason):
  strategy = make_strategy()
  asyncio.run(strategy.start())

  output = step(strategy, make_input(strategy, **input_overrides))

  assert output.trade_intents == []
  assert output.trace_payload["reason"] == expected_reason
  assert expected_reason in output.decision_tags


def test_position_target_is_capped_by_swing_profile_and_risk():
  strategy = make_strategy({"target_position_pct": 0.10})
  asyncio.run(strategy.start())

  output = step(
    strategy,
    make_input(
      strategy,
      risk_caps={"max_position_pct": 0.08},
      position_profile={
        "swing_max_pct": 0.06,
        "bucket_caps": {"swing": {"max_pct": 0.03}},
      },
    ),
  )

  assert output.trade_intents[0].target_position_pct == pytest.approx(0.03)


def test_pending_entry_blocks_duplicates_and_rejection_allows_configured_retry():
  strategy = make_strategy({"max_entry_attempts_per_day": 2})
  asyncio.run(strategy.start())
  first = step(strategy, make_input(strategy))
  intent = first.trade_intents[0]

  duplicate = step(strategy, make_input(strategy))
  assert duplicate.trade_intents == []
  assert duplicate.trace_payload["reason"] == "entry_pending"

  asyncio.run(
    strategy.on_order(
      OrderStateEvent(
        order_id=None,
        status="REJECTED",
        metadata={"intent_id": intent.intent_id},
      )
    )
  )
  retry = step(strategy, make_input(strategy))
  assert len(retry.trade_intents) == 1
  assert retry.trade_intents[0].metadata["attempt"] == 2


def test_trade_callback_only_updates_algorithm_state():
  strategy = make_strategy()
  asyncio.run(strategy.start())
  intent = step(strategy, make_input(strategy)).trade_intents[0]

  patch = asyncio.run(
    strategy.on_trade(
      TradeExecutionEvent(
        order_id="buy-order",
        instrument_code="000001.SZ",
        trade_type="BUY",
        price=11.0,
        volume=500,
        trade_time=datetime(2026, 7, 31, 10, 1),
        metadata={"intent_id": intent.intent_id},
      )
    )
  )

  assert patch is not None
  assert strategy.state["pending_entry_intent_id"] == ""
  assert strategy.state["last_entry_price"] == pytest.approx(11.0)
  assert strategy.state["last_entry_volume"] == 500
  assert not hasattr(strategy, "cash")
  assert not hasattr(strategy, "available_volume")


def test_active_exit_plan_blocks_new_entry():
  strategy = make_strategy()
  asyncio.run(strategy.start())

  output = step(
    strategy,
    make_input(
      strategy,
      exit_plans=[
        {
          "status": "ACTIVE",
          "remaining_volume": 100,
          "template": {"instrument_code": "000001.SZ"},
        }
      ],
    ),
  )

  assert output.trade_intents == []
  assert output.trace_payload["reason"] == "active_exit_plan"


def test_limit_up_break_arms_after_seal_and_triggers_after_opening():
  strategy = make_strategy(
    {
      "entry_execution_mode": "AUTO",
      "exit_min_seal_seconds": 3,
    }
  )
  asyncio.run(strategy.start())
  intent = step(strategy, make_input(strategy)).trade_intents[0]
  template = ExitPlanTemplate.from_dict(intent.metadata["exit_plan_template"])
  book = ExitPlanBook()
  book.register_entry_fill(
    template,
    volume=500,
    price=11.0,
    trade_time=datetime(2026, 7, 31, 10, 1),
  )

  assert (
    book.evaluate(
      "000001.SZ",
      ExitEvaluationContext(
        timestamp=datetime(2026, 8, 3, 10, 0, 0),
        current_price=12.10,
        limit_up=12.10,
        limit_down=9.90,
        price_tick=0.01,
      ),
    )
    == []
  )
  assert (
    book.evaluate(
      "000001.SZ",
      ExitEvaluationContext(
        timestamp=datetime(2026, 8, 3, 10, 0, 4),
        current_price=12.10,
        limit_up=12.10,
        limit_down=9.90,
        price_tick=0.01,
      ),
    )
    == []
  )

  decisions = book.evaluate(
    "000001.SZ",
    ExitEvaluationContext(
      timestamp=datetime(2026, 8, 3, 10, 0, 5),
      current_price=12.08,
      limit_up=12.10,
      limit_down=9.90,
      price_tick=0.01,
    ),
  )

  assert len(decisions) == 1
  assert decisions[0].rule_type == ExitRuleType.LIMIT_UP_BREAK.value
  assert decisions[0].reason == "LIMIT_UP_BREAK"
  assert decisions[0].volume == 500
  assert decisions[0].metrics["seal_armed"] is True
  assert decisions[0].metrics["break_price"] == pytest.approx(12.09)


def test_data_requirements_subscribe_tick_and_daily_bar():
  assert AshareLimitUpBoardStrategy.get_data_requirements() == {
    "use_tick_data": True,
    "periods": ["1d"],
  }


def test_schema_exposes_production_backtest_and_order_expiry_defaults():
  schema = AshareLimitUpBoardStrategy.get_parameter_schema()

  assert schema.properties["entry_order_ttl_ms"].default == 15000
  assert schema.properties["auto_approve_manual_intents"].default is True
  assert schema.properties["strict_market_data"].default is True
  assert schema.properties["strict_limit_data"].default is True
  assert schema.properties["participation_cap_pct"].default == pytest.approx(0.05)


def test_configuration_rejects_reversed_or_invalid_time_windows():
  with pytest.raises(ValueError, match="entry_start_time"):
    AshareLimitUpBoardStrategy.validate_configuration(
      {"entry_start_time": "14:51", "entry_end_time": "14:50"}
    )

  with pytest.raises(ValueError):
    AshareLimitUpBoardStrategy.validate_configuration(
      {"max_holding_exit_time": "25:00"}
    )
