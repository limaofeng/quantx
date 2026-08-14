from datetime import datetime, timedelta

import pytest
from quantx_domain.enums import (
  StrategyInstrumentScope,
  StrategyInstrumentUniverseMode,
)
from quantx_domain.strategies.ashare_limit_up_board_assistant import (
  AshareLimitUpBoardAssistantStrategy,
)
from quantx_domain.strategies.base import (
  OrderStateEvent,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyRunMode,
  TradeExecutionEvent,
  TradeIntentExecutionMode,
)
from quantx_domain.trading.exit_plan import ExitPlanTemplate, ExitRuleType
from quantx_domain.trading.market_rules import MarketDataSnapshot

pytestmark = pytest.mark.unit


def make_strategy(**parameters) -> AshareLimitUpBoardAssistantStrategy:
  return AshareLimitUpBoardAssistantStrategy(
    StrategyContext(
      run_id="assistant-run",
      mode=StrategyRunMode.PAPER,
      instruments=[],
      parameters={
        "account_id": "account-1",
        "promotion_model_mode": "PAPER",
        "auto_exit_authorized": True,
        **parameters,
      },
    )
  )


async def reconcile(
  strategy: AshareLimitUpBoardAssistantStrategy,
  *,
  eligible: bool = True,
  score: float = 80,
  source: str = "AUTO",
  arm_version: int = 0,
) -> None:
  strategy.context.instruments = ["000001.SZ"]
  await strategy.step(
    StrategyInput(
      run_id="assistant-run",
      strategy_id="assistant",
      timestamp=datetime(2026, 8, 14, 9, 59, 59),
      cadence=StrategyCadence.RECONCILE,
      instrument_code="",
      event={
        "instruments": ["000001.SZ"],
        "instrument_metadata": {
          "000001.SZ": {
            "eligible": eligible,
            "reason": "ELIGIBLE" if eligible else "RADAR_BLOCKED",
            "source": source,
            "arm_version": arm_version,
            "radar_score": score,
            "radar_stage": "NEAR_LIMIT",
            "radar_updated_at": "2026-08-14T10:00:00",
            "radar_is_stale": False,
            "promotion_eligible": eligible,
            "promotion_score": score,
            "promotion_snapshot_version": "snapshot-v1",
            "promotion_model_version": "first-board-promotion-v2-shadow-1",
            "exit_policy_version": "first-board-exit-v2-shadow-1",
            "board_segment": "MAIN",
            "cvar95_loss_pct": 7.0,
            "expected_net_return_pct": 1.2,
            "target_position_pct": 0.02,
            "high_position_type": "BASE_BREAKOUT",
          }
        },
      },
    )
  )


def tick_input(
  strategy: AshareLimitUpBoardAssistantStrategy,
  *,
  decision_time: datetime = datetime(2026, 8, 14, 10, 0, 1),
  quote_time: datetime | None = None,
  price: float = 10.99,
) -> StrategyInput:
  quote_at = quote_time or decision_time
  market = MarketDataSnapshot(
    instrument_code="000001.SZ",
    timestamp=quote_at,
    price=price,
    open=10.5,
    high=price,
    low=10.4,
    close=price,
    amount=200_000_000,
    price_tick=0.01,
    limit_up=11,
    limit_down=9,
    bid_price=[price],
    ask_price=[11],
    bid_vol=[100_000],
    ask_vol=[10_000],
  )
  return StrategyInput(
    run_id="assistant-run",
    strategy_id="assistant",
    timestamp=decision_time,
    cadence=StrategyCadence.TICK,
    instrument_code="000001.SZ",
    market_data=market,
    event=market,
    market_context={
      "data_quality": "OK",
      "instrument_master": {"data_quality": "OK"},
    },
    risk_caps={"allow_buy": True, "allow_swing_buy": True},
    position_profile={"allow_swing_buy": True},
  )


@pytest.mark.asyncio
async def test_account_assistant_uses_dynamic_radar_universe_and_risk_position():
  strategy = make_strategy()
  await strategy.start()
  await reconcile(strategy)

  output = await strategy.step(tick_input(strategy))

  assert strategy.INSTRUMENT_SCOPE == StrategyInstrumentScope.MULTI
  assert (
    strategy.INSTRUMENT_UNIVERSE_MODE
    == StrategyInstrumentUniverseMode.RADAR_CANDIDATES
  )
  assert len(output.trade_intents) == 1
  intent = output.trade_intents[0]
  assert intent.target_amount is None
  assert intent.target_position_pct == pytest.approx(0.02)
  assert intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
  assert intent.approval_ttl_ms == 15_000
  assert intent.metadata["max_single_position_pct"] == pytest.approx(0.02)
  assert intent.metadata["liquidity_cap_amount"] == 0
  template = ExitPlanTemplate.from_dict(intent.metadata["exit_plan_template"])
  assert template.auto_exit_authorized is True
  assert [rule.strategy for rule in template.rules] == [
    ExitRuleType.LIMIT_UP_TOUCH.value,
    ExitRuleType.HARD_STOP.value,
    ExitRuleType.LIMIT_UP_BREAK.value,
    ExitRuleType.TRAILING_PRICE_DRAWDOWN.value,
    ExitRuleType.MAX_HOLDING_DAYS.value,
  ]
  assert template.rules[1].parameters == {
    "min_holding_trading_days": 2,
    "stop_loss_pct": -7.0,
    "reason": "FIRST_BOARD_T1_TAIL_LOSS",
  }
  assert template.rules[3].parameters["min_holding_trading_days"] == 2


@pytest.mark.asyncio
async def test_daily_exposure_reserves_fills_and_reduces_next_target():
  strategy = make_strategy(max_daily_exposure_pct=0.05)
  await strategy.start()
  await reconcile(strategy)
  states = strategy._instrument_states()
  states["000002.SZ"] = {
    **strategy._empty_instrument_state(),
    "trade_date": "2026-08-14",
    "last_entry_trade_date": "2026-08-14",
    "last_entry_price": 20.0,
    "last_entry_volume": 200,
    "target_position_pct": 0.04,
  }
  strategy.state.update({"instrument_states": states})
  candidate = tick_input(strategy)
  candidate.portfolio_state = {
    "account": {"total_asset": 100_000},
    "positions": {},
  }

  output = await strategy.step(candidate)

  intent = output.trade_intents[0]
  assert intent.target_position_pct == pytest.approx(0.01)
  assert intent.metadata["daily_exposure_used_pct"] == pytest.approx(0.04)
  assert intent.metadata["daily_exposure_cap_pct"] == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_multiple_buy_reports_accumulate_daily_filled_exposure():
  strategy = make_strategy()
  await strategy.start()
  await reconcile(strategy)

  await strategy.on_trade(
    TradeExecutionEvent(
      order_id="order-1",
      instrument_code="000001.SZ",
      trade_type="BUY",
      volume=100,
      price=10.0,
      trade_time=datetime(2026, 8, 14, 10, 0, 2),
    )
  )
  await strategy.on_trade(
    TradeExecutionEvent(
      order_id="order-1",
      instrument_code="000001.SZ",
      trade_type="BUY",
      volume=200,
      price=11.0,
      trade_time=datetime(2026, 8, 14, 10, 0, 3),
    )
  )

  state = strategy._instrument_states()["000001.SZ"]
  assert state["last_entry_volume"] == 300
  assert state["last_entry_price"] == pytest.approx(32 / 3)

  await strategy.on_trade(
    TradeExecutionEvent(
      order_id="exit-1",
      instrument_code="000001.SZ",
      trade_type="SELL",
      volume=100,
      price=12.0,
      trade_time=datetime(2026, 8, 15, 10, 0, 3),
    )
  )
  assert strategy._instrument_states()["000001.SZ"]["last_entry_volume"] == 200
  await strategy.on_trade(
    TradeExecutionEvent(
      order_id="exit-2",
      instrument_code="000001.SZ",
      trade_type="SELL",
      volume=200,
      price=12.1,
      trade_time=datetime(2026, 8, 15, 10, 0, 4),
    )
  )
  closed = strategy._instrument_states()["000001.SZ"]
  assert closed["last_entry_volume"] == 0
  assert closed["draining"] is False


@pytest.mark.asyncio
async def test_unrelated_long_term_account_positions_do_not_consume_strategy_slots():
  strategy = make_strategy(max_open_positions=2)
  await strategy.start()
  await reconcile(strategy)
  candidate = tick_input(strategy)
  candidate.portfolio_state = {
    "account": {"total_asset": 100_000},
    "positions": {
      "600000.SH": {"long_volume": 1_000},
      "600519.SH": {"long_volume": 100},
    },
  }

  output = await strategy.step(candidate)

  assert len(output.trade_intents) == 1


@pytest.mark.asyncio
async def test_expired_signal_does_not_repeat_until_reentry_or_manual_rearm():
  strategy = make_strategy()
  await strategy.start()
  await reconcile(strategy)
  first = (await strategy.step(tick_input(strategy))).trade_intents[0]
  await strategy.on_order(
    OrderStateEvent(
      order_id=None,
      status="EXPIRED",
      metadata={"intent_id": first.intent_id, "instrument_code": "000001.SZ"},
    )
  )

  same_band = await strategy.step(tick_input(strategy))
  assert same_band.trade_intents == []
  assert same_band.trace_payload["reason"] == "entry_band_already_seen"

  await strategy.step(tick_input(strategy, price=10.97))
  reentered = await strategy.step(tick_input(strategy))
  assert len(reentered.trade_intents) == 1
  assert (
    reentered.trade_intents[0].metadata["exit_plan_template"]["plan_id"]
    != first.metadata["exit_plan_template"]["plan_id"]
  )

  await strategy.on_order(
    OrderStateEvent(
      order_id=None,
      status="EXPIRED",
      metadata={
        "intent_id": reentered.trade_intents[0].intent_id,
        "instrument_code": "000001.SZ",
      },
    )
  )
  await reconcile(strategy, source="MANUAL", score=50, arm_version=1)
  manually_rearmed = await strategy.step(tick_input(strategy))
  assert len(manually_rearmed.trade_intents) == 1
  assert manually_rearmed.trade_intents[0].metadata["candidate_source"] == "MANUAL"


@pytest.mark.asyncio
async def test_execution_quote_older_than_three_seconds_is_blocked():
  strategy = make_strategy()
  await strategy.start()
  await reconcile(strategy)
  now = datetime(2026, 8, 14, 10, 0, 1)

  output = await strategy.step(
    tick_input(strategy, decision_time=now, quote_time=now - timedelta(seconds=4))
  )

  assert output.trade_intents == []
  assert output.trace_payload["reason"] == "execution_market_data_stale"


@pytest.mark.asyncio
async def test_confirmed_attempt_is_limited_to_once_per_stock_per_day():
  strategy = make_strategy()
  await strategy.start()
  await reconcile(strategy)
  intent = (await strategy.step(tick_input(strategy))).trade_intents[0]
  await strategy.on_order(
    OrderStateEvent(
      order_id=None,
      status="PENDING",
      metadata={"intent_id": intent.intent_id, "instrument_code": "000001.SZ"},
    )
  )
  await strategy.on_order(
    OrderStateEvent(
      order_id=None,
      status="REJECTED",
      metadata={"intent_id": intent.intent_id, "instrument_code": "000001.SZ"},
    )
  )
  await strategy.step(tick_input(strategy, price=10.97))

  output = await strategy.step(tick_input(strategy))

  assert output.trade_intents == []
  assert output.trace_payload["reason"] == "daily_confirmed_attempt_limit"


@pytest.mark.asyncio
async def test_manual_approval_rechecks_latest_board_state():
  strategy = make_strategy()
  await strategy.start()
  await reconcile(strategy)
  intent = (await strategy.step(tick_input(strategy))).trade_intents[0]
  states = strategy._instrument_states()
  states["000001.SZ"]["promotion_snapshot_version"] = "snapshot-v2"
  strategy.state.update({"instrument_states": states})
  left_band = tick_input(strategy, price=10.95).market_data
  sealed = tick_input(strategy, price=11).market_data
  sealed.ask_price = [0]

  assert strategy.validate_manual_approval(intent, left_band) == (
    "BOARD_LEFT_ENTRY_BAND",
    "股票已离开临板价位，请等待新信号",
  )
  assert strategy.validate_manual_approval(intent, sealed)[0] == "BOARD_ALREADY_AT_LIMIT"
  assert strategy.validate_manual_approval(intent, tick_input(strategy).market_data) is None
