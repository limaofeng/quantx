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
        "target_entry_amount": 10_000,
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
async def test_account_assistant_uses_dynamic_radar_universe_and_manual_amount():
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
  assert intent.target_amount == pytest.approx(10_000)
  assert intent.target_position_pct is None
  assert intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
  assert intent.approval_ttl_ms == 15_000
  assert intent.metadata["max_single_position_pct"] == pytest.approx(0.05)
  template = ExitPlanTemplate.from_dict(intent.metadata["exit_plan_template"])
  assert template.auto_exit_authorized is True
  assert [rule.strategy for rule in template.rules] == [
    ExitRuleType.LIMIT_UP_BREAK.value,
    ExitRuleType.TRAILING_PRICE_DRAWDOWN.value,
    ExitRuleType.MAX_HOLDING_DAYS.value,
  ]


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
  left_band = tick_input(strategy, price=10.95).market_data
  sealed = tick_input(strategy, price=11).market_data
  sealed.ask_price = [0]

  assert strategy.validate_manual_approval(intent, left_band) == (
    "BOARD_LEFT_ENTRY_BAND",
    "股票已离开临板价位，请等待新信号",
  )
  assert strategy.validate_manual_approval(intent, sealed)[0] == "BOARD_ALREADY_AT_LIMIT"
