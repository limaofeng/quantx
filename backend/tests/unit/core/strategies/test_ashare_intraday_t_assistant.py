from datetime import datetime, timedelta

import pytest

from core.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
  TTradeStatus,
  TTradeTimeExitMode,
)
from core.strategies.base import (
  OrderStateEvent,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyRunMode,
  TradeExecutionEvent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from models.tick import Tick


def make_tick(
  timestamp: datetime,
  price: float,
  *,
  stock_code: str = "600000.SH",
  amount: float = 0.0,
  volume: float = 0.0,
):
  return Tick(
    stock_code=stock_code,
    period="tick",
    time=timestamp,
    last_price=price,
    open=100.0,
    high=100.0,
    low=99.0,
    last_close=100.0,
    amount=amount,
    volume=volume,
    pvolume=volume,
    tickvol=100,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=1,
    ask_price=[price],
    bid_price=[price - 0.01],
    ask_vol=[1000],
    bid_vol=[1000],
  )


def make_input(timestamp: datetime, tick: Tick) -> StrategyInput:
  return StrategyInput(
    run_id="run-1",
    strategy_id="1",
    timestamp=timestamp,
    cadence=StrategyCadence.TICK,
    instrument_code=tick.stock_code,
    event=tick,
  )


async def reconcile(strategy, metadata):
  codes = list(metadata)
  strategy.context.instruments = codes
  output = await strategy.step(
    StrategyInput(
      run_id="run-1",
      strategy_id="1",
      timestamp=datetime(2026, 7, 13, 9, 29),
      cadence=StrategyCadence.RECONCILE,
      instrument_code="",
      event={"instruments": codes, "instrument_metadata": metadata},
    )
  )
  strategy.state.update(output.runtime_state_patch.set)
  return output


def make_strategy():
  context = StrategyContext(
    run_id="run-1",
    mode=StrategyRunMode.PAPER,
    instruments=[],
    parameters={
      "account_id": "account-1",
      "target_trade_amount": 10_000.0,
      "max_trade_amount": 12_000.0,
      "pullback_threshold_pct": 0.8,
      "rebound_threshold_pct": 0.2,
      "stabilization_seconds": 15,
      "hard_stop_enabled": False,
      "time_exit_mode": TTradeTimeExitMode.UNLIMITED,
      "time_exit_time": "14:50",
      "max_holding_trading_days": 5,
      "target_profit_pct": 2.0,
    },
  )
  return AshareIntradayTAssistantStrategy(context)


@pytest.mark.asyncio
async def test_multi_instrument_manual_entry_then_trailing_auto_exit():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(
    strategy,
    {
      "600000.SH": {
        "eligible": True,
        "policy_volume": 100,
        "position_shares": 1000,
        "position_available_shares": 1000,
      },
      "000001.SZ": {
        "eligible": True,
        "policy_volume": 100,
        "position_shares": 1000,
        "position_available_shares": 1000,
      },
    },
  )
  start = datetime(2026, 7, 13, 9, 30)

  await strategy.step(make_input(start, make_tick(start, 100.0)))
  await strategy.step(
    make_input(start, make_tick(start, 20.0, stock_code="000001.SZ"))
  )
  await strategy.step(
    make_input(start + timedelta(seconds=60), make_tick(start + timedelta(seconds=60), 99.0))
  )
  signal_output = await strategy.step(
    make_input(
      start + timedelta(seconds=80),
      make_tick(start + timedelta(seconds=80), 99.3, amount=995_000, volume=10_000),
    )
  )

  assert len(signal_output.trade_intents) == 1
  entry_intent = signal_output.trade_intents[0]
  assert entry_intent.direction == TradeIntentDirection.BUY
  assert entry_intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
  strategy.state.update(signal_output.runtime_state_patch.set)
  states = strategy.state.get("instrument_states")
  assert states["600000.SH"]["pending_entry_intent_id"] == entry_intent.intent_id
  assert states["000001.SZ"]["pending_entry_intent_id"] == ""

  await strategy.on_trade(
    TradeExecutionEvent(
      order_id="entry-order",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=99.3,
      volume=100,
      trade_time=start + timedelta(seconds=90),
      metadata={
        "intent_id": entry_intent.intent_id,
        "t_trade_role": "entry",
        "instrument_code": "600000.SH",
      },
    )
  )
  await strategy.on_order(
    OrderStateEvent(
      order_id="entry-order",
      status="FILLED",
      metadata={
        "intent_id": entry_intent.intent_id,
        "t_trade_role": "entry",
        "instrument_code": "600000.SH",
      },
    )
  )
  strategy.context.parameters["target_profit_pct"] = 9.0

  armed_output = await strategy.step(
    make_input(start + timedelta(seconds=100), make_tick(start + timedelta(seconds=100), 102.0))
  )
  strategy.state.update(armed_output.runtime_state_patch.set)
  state = strategy.state.get("instrument_states")["600000.SH"]
  assert state["profit_armed"] is True
  assert state["exit_policy_snapshot"]["target_profit_pct"] == 2.0

  exit_output = await strategy.step(
    make_input(start + timedelta(seconds=110), make_tick(start + timedelta(seconds=110), 100.0))
  )
  assert len(exit_output.trade_intents) == 1
  exit_intent = exit_output.trade_intents[0]
  assert exit_intent.direction == TradeIntentDirection.SELL
  assert exit_intent.execution_mode == TradeIntentExecutionMode.AUTO
  assert exit_intent.metadata["price_type"] == "MARKET"
  assert (
    exit_output.runtime_state_patch.set["instrument_states"]["600000.SH"]["status"]
    == TTradeStatus.EXIT_TRIGGERED
  )


@pytest.mark.asyncio
async def test_removed_active_instrument_is_retained_for_draining():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(
    strategy,
    {"600000.SH": {"eligible": True, "policy_volume": 100}},
  )
  states = strategy.state.get("instrument_states")
  states["600000.SH"].update(
    {"entry_filled_volume": 100, "entry_avg_price": 10.0}
  )
  strategy.state.update({"instrument_states": states})

  await reconcile(strategy, {})

  state = strategy.state.get("instrument_states")["600000.SH"]
  assert state["draining"] is True
  assert state["entry_eligible"] is False
  assert state["status"] == TTradeStatus.DRAINING


@pytest.mark.asyncio
async def test_import_external_entry_uses_existing_auto_exit_policy():
  strategy = make_strategy()
  strategy.context.parameters["hard_stop_enabled"] = True
  await strategy.initialize()
  await reconcile(
    strategy,
    {
      "600000.SH": {
        "eligible": True,
        "policy_volume": 200,
        "position_shares": 1000,
        "position_available_shares": 800,
      }
    },
  )

  patch = strategy.import_external_entry("600000.SH", 200, 10.0, "trade-1")
  strategy.state.update(patch.set)
  state = strategy.state.get("instrument_states")["600000.SH"]
  assert state["status"] == TTradeStatus.MONITORING
  assert state["entry_order_status"] == "EXTERNAL_FILLED"
  assert state["entry_filled_volume"] == 200
  assert state["entry_avg_price"] == 10.0
  assert state["current_signal"]["source"] == "MANUAL_EXTERNAL_ENTRY"

  output = await strategy.step(
    make_input(
      datetime(2026, 7, 13, 10, 0),
      make_tick(datetime(2026, 7, 13, 10, 0), 9.8),
    )
  )
  assert len(output.trade_intents) == 1
  assert output.trade_intents[0].direction == TradeIntentDirection.SELL
  assert output.trade_intents[0].execution_mode == TradeIntentExecutionMode.AUTO


@pytest.mark.asyncio
async def test_unlimited_mode_does_not_force_exit_at_end_of_day():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(
    strategy,
    {"600000.SH": {"position_shares": 1000, "position_available_shares": 800}},
  )
  patch = strategy.import_external_entry("600000.SH", 100, 10.0, "trade-unlimited")
  strategy.state.update(patch.set)

  timestamp = datetime(2026, 7, 13, 14, 55)
  output = await strategy.step(make_input(timestamp, make_tick(timestamp, 9.8)))

  assert output.trade_intents == []
  assert output.trace_payload["time_exit_mode"] == TTradeTimeExitMode.UNLIMITED


@pytest.mark.asyncio
async def test_end_of_day_mode_exits_at_configured_time():
  strategy = make_strategy()
  strategy.context.parameters.update(
    {
      "time_exit_mode": TTradeTimeExitMode.END_OF_DAY,
      "time_exit_time": "14:50",
    }
  )
  await strategy.initialize()
  await reconcile(
    strategy,
    {"600000.SH": {"position_shares": 1000, "position_available_shares": 800}},
  )
  patch = strategy.import_external_entry("600000.SH", 100, 10.0, "trade-eod")
  strategy.state.update(patch.set)

  before = datetime(2026, 7, 13, 14, 49, 59)
  assert (
    await strategy.step(make_input(before, make_tick(before, 10.0)))
  ).trade_intents == []
  at_exit = datetime(2026, 7, 13, 14, 50)
  output = await strategy.step(make_input(at_exit, make_tick(at_exit, 10.0)))

  assert len(output.trade_intents) == 1
  assert output.trade_intents[0].metadata["exit_reason"] == "END_OF_DAY_FLATTEN"


@pytest.mark.asyncio
async def test_max_holding_days_counts_observed_trading_dates():
  strategy = make_strategy()
  strategy.context.parameters.update(
    {
      "time_exit_mode": TTradeTimeExitMode.MAX_HOLDING_DAYS,
      "time_exit_time": "14:50",
      "max_holding_trading_days": 2,
    }
  )
  await strategy.initialize()
  await reconcile(
    strategy,
    {"600000.SH": {"position_shares": 1000, "position_available_shares": 800}},
  )
  patch = strategy.import_external_entry("600000.SH", 100, 10.0, "trade-max-days")
  strategy.state.update(patch.set)

  friday = datetime(2026, 7, 17, 14, 55)
  friday_output = await strategy.step(make_input(friday, make_tick(friday, 10.0)))
  assert friday_output.trade_intents == []
  strategy.state.update(friday_output.runtime_state_patch.set)

  monday_before = datetime(2026, 7, 20, 14, 49)
  monday_output = await strategy.step(
    make_input(monday_before, make_tick(monday_before, 10.0))
  )
  assert monday_output.trade_intents == []
  strategy.state.update(monday_output.runtime_state_patch.set)
  state = strategy.state.get("instrument_states")["600000.SH"]
  assert state["holding_trading_days"] == 2

  monday_exit = datetime(2026, 7, 20, 14, 50)
  exit_output = await strategy.step(
    make_input(monday_exit, make_tick(monday_exit, 10.0))
  )
  assert len(exit_output.trade_intents) == 1
  assert (
    exit_output.trade_intents[0].metadata["exit_reason"]
    == "MAX_HOLDING_DAYS_REACHED"
  )


@pytest.mark.asyncio
async def test_active_batch_refreshes_exit_policy_with_audit_event():
  strategy = make_strategy()
  strategy.context.parameters["global_config_version"] = 1
  await strategy.initialize()
  await reconcile(
    strategy,
    {"600000.SH": {"position_shares": 1000, "position_available_shares": 800}},
  )
  patch = strategy.import_external_entry("600000.SH", 100, 10.0, "trade-refresh")
  strategy.state.update(patch.set)
  strategy.context.parameters.update(
    {
      "global_config_version": 2,
      "hard_stop_enabled": True,
      "time_exit_mode": TTradeTimeExitMode.MAX_HOLDING_DAYS,
      "max_holding_trading_days": 5,
    }
  )

  timestamp = datetime(2026, 7, 13, 10, 0)
  output = await strategy.step(make_input(timestamp, make_tick(timestamp, 10.0)))

  state = output.runtime_state_patch.set["instrument_states"]["600000.SH"]
  assert state["exit_policy_snapshot"]["config_version"] == 2
  assert state["exit_policy_snapshot"]["hard_stop_enabled"] is True
  assert output.runtime_state_patch.append_events[0]["type"] == (
    "T_TRADE_EXIT_POLICY_UPDATED"
  )
  audit_event = output.runtime_state_patch.append_events[0]
  assert audit_event["previous_policy"]["config_version"] == 1
  assert audit_event["policy"]["config_version"] == 2
  assert audit_event["policy"]["time_exit_mode"] == (
    TTradeTimeExitMode.MAX_HOLDING_DAYS
  )


@pytest.mark.asyncio
async def test_policy_refresh_does_not_replace_pending_exit_intent():
  strategy = make_strategy()
  strategy.context.parameters["global_config_version"] = 1
  await strategy.initialize()
  await reconcile(
    strategy,
    {"600000.SH": {"position_shares": 1000, "position_available_shares": 800}},
  )
  patch = strategy.import_external_entry("600000.SH", 100, 10.0, "trade-pending")
  strategy.state.update(patch.set)
  states = strategy.state.get("instrument_states")
  states["600000.SH"].update(
    {
      "pending_exit_intent_id": "existing-exit-intent",
      "exit_order_status": "ACCEPTED",
    }
  )
  strategy.state.update({"instrument_states": states})
  strategy.context.parameters.update(
    {
      "global_config_version": 2,
      "hard_stop_enabled": True,
      "hard_stop_pct": -0.8,
    }
  )

  timestamp = datetime(2026, 7, 13, 10, 0)
  output = await strategy.step(make_input(timestamp, make_tick(timestamp, 9.8)))

  state = output.runtime_state_patch.set["instrument_states"]["600000.SH"]
  assert output.trade_intents == []
  assert state["pending_exit_intent_id"] == "existing-exit-intent"
  assert state["exit_order_status"] == "ACCEPTED"
  assert output.runtime_state_patch.append_events[0]["type"] == (
    "T_TRADE_EXIT_POLICY_UPDATED"
  )


@pytest.mark.asyncio
async def test_import_external_entry_rejects_duplicate_active_batch():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(
    strategy,
    {"600000.SH": {"position_shares": 1000, "position_available_shares": 800}},
  )
  patch = strategy.import_external_entry("600000.SH", 100, 10.0, "trade-1")
  strategy.state.update(patch.set)

  with pytest.raises(ValueError, match="已有未完成"):
    strategy.import_external_entry("600000.SH", 100, 9.9, "trade-2")


@pytest.mark.asyncio
async def test_import_external_entry_rejects_reused_trade_id():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(
    strategy,
    {"600000.SH": {"position_shares": 1000, "position_available_shares": 800}},
  )
  patch = strategy.import_external_entry("600000.SH", 100, 10.0, "trade-1")
  strategy.state.update(patch.set)
  strategy.state.update({"runtime_events": patch.append_events})
  states = strategy.state.get("instrument_states")
  states["600000.SH"].update({"entry_filled_volume": 0, "exit_filled_volume": 0})
  strategy.state.update({"instrument_states": states})

  with pytest.raises(ValueError, match="已经加入"):
    strategy.import_external_entry("600000.SH", 100, 10.0, "trade-1")
