from datetime import datetime, timedelta

import pytest
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
  TTradeStatus,
  TTradeTimeExitMode,
)
from quantx_domain.strategies.base import (
  OrderStateEvent,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyRunMode,
  TradeExecutionEvent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_domain.trading.exit_plan import (
  ExitEvaluationContext,
  ExitPlanBook,
  ExitRuleType,
  ExitT1Policy,
  estimate_net_profit_pct,
)
from quantx_infrastructure.models.tick import Tick


def make_tick(
  timestamp: datetime,
  price: float,
  *,
  stock_code: str = "600000.SH",
  amount: float = 0.0,
  volume: float = 0.0,
  pvolume: float | None = None,
  bid_price: float | None = None,
  ask_price: float | None = None,
  last_close: float = 100.0,
):
  resolved_bid = price - 0.01 if bid_price is None else bid_price
  resolved_ask = price if ask_price is None else ask_price
  return Tick(
    stock_code=stock_code,
    period="tick",
    time=timestamp,
    last_price=price,
    open=100.0,
    high=100.0,
    low=99.0,
    last_close=last_close,
    amount=amount,
    volume=volume,
    pvolume=volume if pvolume is None else pvolume,
    tickvol=100,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=1,
    ask_price=[resolved_ask],
    bid_price=[resolved_bid],
    ask_vol=[1000],
    bid_vol=[1000],
  )


def make_input(
  timestamp: datetime,
  tick: Tick,
  *,
  exit_plans=None,
) -> StrategyInput:
  return StrategyInput(
    run_id="run-1",
    strategy_id="1",
    timestamp=timestamp,
    cadence=StrategyCadence.TICK,
    instrument_code=tick.stock_code,
    event=tick,
    exit_plans=list(exit_plans or []),
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
  await strategy.step(make_input(start, make_tick(start, 20.0, stock_code="000001.SZ")))
  await strategy.step(
    make_input(
      start + timedelta(seconds=60), make_tick(start + timedelta(seconds=60), 99.0)
    )
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
  exit_book = ExitPlanBook()
  exit_book.register_entry_fill(
    entry_intent.metadata["exit_plan_template"],
    volume=100,
    price=99.3,
    trade_time=start + timedelta(seconds=90),
  )
  strategy.context.parameters["target_profit_pct"] = 9.0

  assert (
    exit_book.evaluate(
      "600000.SH",
      ExitEvaluationContext(
        timestamp=start + timedelta(seconds=100),
        current_price=101.99,
      ),
    )
    == []
  )
  armed_output = await strategy.step(
    make_input(
      start + timedelta(seconds=100),
      make_tick(start + timedelta(seconds=100), 102.0),
      exit_plans=exit_book.projections("600000.SH"),
    )
  )
  strategy.state.update(armed_output.runtime_state_patch.set)
  state = strategy.state.get("instrument_states")["600000.SH"]
  assert state["profit_armed"] is True
  assert state["exit_policy_snapshot"]["target_profit_pct"] == 2.0

  [decision] = exit_book.evaluate(
    "600000.SH",
    ExitEvaluationContext(
      timestamp=start + timedelta(seconds=110),
      current_price=99.99,
    ),
  )
  assert decision.reason == "TRAILING_FLOOR_REACHED"
  assert decision.volume == 100
  assert entry_intent.metadata["exit_plan_template"]["execution"]["price_type"] == (
    "MARKET"
  )
  assert (
    entry_intent.metadata["exit_plan_template"]["execution"]["protected_limit"] is False
  )


@pytest.mark.asyncio
async def test_call_auction_ticks_do_not_generate_or_seed_entry_signal():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(
    strategy,
    {
      "600000.SH": {
        "eligible": True,
        "position_shares": 1000,
        "position_available_shares": 1000,
      }
    },
  )
  auction_start = datetime(2026, 7, 13, 9, 29)
  auction_ticks = [
    make_tick(auction_start, 100.0),
    make_tick(auction_start + timedelta(seconds=20), 99.0),
    make_tick(
      auction_start + timedelta(seconds=40),
      99.3,
      amount=995_000,
      volume=10_000,
    ),
  ]

  for tick in auction_ticks:
    output = await strategy.step(make_input(tick.time, tick))
    assert output.trade_intents == []
    assert output.trace_payload["reason"] == "OUTSIDE_CONTINUOUS_TRADING_SESSION"

  assert strategy._samples_by_instrument == {}
  assert strategy.state["instrument_states"]["600000.SH"]["monitoring_telemetry"] == {}

  market_open = datetime(2026, 7, 13, 9, 30)
  output = await strategy.step(make_input(market_open, make_tick(market_open, 99.3)))

  assert output.trade_intents == []
  assert output.trace_payload["reason"] == "INSUFFICIENT_TICKS"
  assert len(strategy._samples_by_instrument["600000.SH"]) == 1


@pytest.mark.asyncio
async def test_every_valid_tick_advances_telemetry_without_creating_false_signal():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(
    strategy,
    {
      "600000.SH": {
        "eligible": True,
        "position_shares": 1000,
        "position_available_shares": 1000,
      }
    },
  )
  start = datetime(2026, 7, 13, 9, 30)

  first = await strategy.step(make_input(start, make_tick(start, 100.0)))
  strategy.state.update(first.runtime_state_patch.set)
  state = strategy.state["instrument_states"]["600000.SH"]
  assert state["current_signal"] == {}
  assert state["monitoring_telemetry"]["processed_tick_count"] == 1
  assert state["monitoring_telemetry"]["reason"] == "INSUFFICIENT_TICKS"

  state["pending_entry_intent_id"] = "pending-1"
  waiting_at = start + timedelta(seconds=1)
  waiting = await strategy.step(
    make_input(waiting_at, make_tick(waiting_at, 100.01))
  )
  strategy.state.update(waiting.runtime_state_patch.set)
  state = strategy.state["instrument_states"]["600000.SH"]
  assert state["monitoring_telemetry"]["processed_tick_count"] == 2
  assert state["monitoring_telemetry"]["reason"] == "INTENT_PENDING"

  state["pending_entry_intent_id"] = ""
  state["cooldown_until_ms"] = int((start + timedelta(minutes=1)).timestamp() * 1000)
  cooldown_at = start + timedelta(seconds=2)
  cooling = await strategy.step(
    make_input(cooldown_at, make_tick(cooldown_at, 100.02))
  )
  strategy.state.update(cooling.runtime_state_patch.set)
  state = strategy.state["instrument_states"]["600000.SH"]
  assert state["monitoring_telemetry"]["processed_tick_count"] == 3
  assert state["monitoring_telemetry"]["reason"] == "COOLDOWN_ACTIVE"

  state["cooldown_until_ms"] = 0
  state["entry_filled_volume"] = 100
  exit_at = start + timedelta(seconds=3)
  monitoring = await strategy.step(make_input(exit_at, make_tick(exit_at, 100.03)))
  strategy.state.update(monitoring.runtime_state_patch.set)
  state = strategy.state["instrument_states"]["600000.SH"]
  assert state["monitoring_telemetry"]["processed_tick_count"] == 4
  assert state["monitoring_telemetry"]["phase"] == "EXIT_MONITOR"
  assert state["monitoring_telemetry"]["reason"] == (
    "WAITING_FOR_EXIT_PLAN_REGISTRATION"
  )

  outside_at = datetime(2026, 7, 13, 12, 0)
  outside = await strategy.step(
    make_input(outside_at, make_tick(outside_at, 100.04))
  )
  assert outside.trace_payload["reason"] == "OUTSIDE_CONTINUOUS_TRADING_SESSION"
  assert (
    strategy.state["instrument_states"]["600000.SH"]["monitoring_telemetry"]
    ["processed_tick_count"]
    == 4
  )


def test_default_entry_cutoff_keeps_signals_open_until_1450():
  strategy = make_strategy()
  before_cutoff = datetime(2026, 7, 13, 14, 49, 59)
  at_cutoff = datetime(2026, 7, 13, 14, 50)

  assert (
    strategy._should_block_new_entry(
      make_input(before_cutoff, make_tick(before_cutoff, 10.0))
    )
    is False
  )
  assert (
    strategy._should_block_new_entry(make_input(at_cutoff, make_tick(at_cutoff, 10.0)))
    is True
  )


@pytest.mark.asyncio
async def test_tefa_service_20260812_momentum_entry_and_trailing_exit_replay():
  """Regression replay for the rapid stretch studied on 2026-08-12."""

  strategy = make_strategy()
  strategy.context.parameters.update(
    {
      "hard_stop_enabled": True,
      "hard_stop_pct": -0.8,
      "time_exit_mode": TTradeTimeExitMode.END_OF_DAY,
      "limit_up_touch_exit_enabled": True,
    }
  )
  await strategy.initialize()
  await reconcile(
    strategy,
    {
      "300917.SZ": {
        "eligible": True,
        "instrument_name": "特发服务",
        "position_shares": 400,
        "position_available_shares": 400,
      }
    },
  )

  def recorded_tick(
    clock: str,
    last: float,
    amount: float,
    hands: float,
    shares: float,
    bid: float,
    ask: float,
  ) -> Tick:
    timestamp = datetime.fromisoformat(f"2026-08-12T{clock}")
    return make_tick(
      timestamp,
      last,
      stock_code="300917.SZ",
      amount=amount,
      volume=hands,
      pvolume=shares,
      bid_price=bid,
      ask_price=ask,
      last_close=26.41,
    )

  causal_entry_ticks = [
    recorded_tick("13:43:57", 27.35, 139_550_205.23, 51_520, 5_151_984, 27.31, 27.34),
    recorded_tick("13:44:57", 27.41, 140_158_282.23, 51_742, 5_174_184, 27.41, 27.44),
    recorded_tick("13:45:57", 27.50, 146_892_465.23, 54_188, 5_418_784, 27.47, 27.50),
    recorded_tick("13:46:57", 27.58, 150_553_455.23, 55_517, 5_551_684, 27.53, 27.58),
    recorded_tick("13:47:57", 27.53, 152_226_340.73, 56_125, 5_612_473, 27.53, 27.54),
    recorded_tick("13:48:57", 27.53, 154_724_134.73, 57_031, 5_703_073, 27.53, 27.55),
    recorded_tick("13:49:57", 27.78, 161_167_439.73, 59_361, 5_936_073, 27.75, 27.80),
  ]

  output = None
  for tick in causal_entry_ticks:
    output = await strategy.step(make_input(tick.time, tick))

  assert output is not None
  assert len(output.trade_intents) == 1
  intent = output.trade_intents[0]
  signal = intent.metadata["signal"]
  assert intent.reason == "T_TRADE_MOMENTUM_ACCELERATION_ENTRY"
  assert intent.direction == TradeIntentDirection.BUY
  assert intent.limit_price_hint == 27.80
  assert intent.target_volume == 300
  assert signal["signal_type"] == "MOMENTUM_ACCELERATION"
  assert signal["momentum_rise_pct"] == pytest.approx(0.9081002543)
  assert signal["momentum_amount_velocity_ratio"] == pytest.approx(2.1231497748)
  assert signal["vwap"] == pytest.approx(27.1505151183)
  assert signal["vwap_premium_pct"] == pytest.approx(2.3185006948)
  assert strategy._samples_by_instrument["300917.SZ"][-1].cumulative_volume == (
    5_936_073
  )

  template = intent.metadata["exit_plan_template"]
  assert template["t1_policy"] == (
    ExitT1Policy.ALLOW_SAME_INSTRUMENT_SUBSTITUTION.value
  )
  assert any(
    rule["strategy"] == ExitRuleType.LIMIT_UP_TOUCH.value for rule in template["rules"]
  )

  book = ExitPlanBook()
  plan = book.register_entry_fill(
    template,
    volume=300,
    price=27.80,
    trade_time=datetime(2026, 8, 12, 13, 49, 57),
  )
  exit_ticks = [
    recorded_tick("13:50:03", 27.75, 163_558_888.73, 60_223, 6_022_273, 27.63, 27.75),
    recorded_tick("14:02:00", 28.41, 251_121_735.97, 91_401, 9_140_132, 28.41, 28.46),
    recorded_tick("14:02:33", 29.20, 265_077_630.37, 96_267, 9_626_734, 29.20, 29.25),
    recorded_tick("14:02:39", 29.67, 267_840_621.37, 97_209, 9_720_934, 29.67, 29.68),
    recorded_tick("14:02:42", 29.63, 268_773_904.37, 97_525, 9_752_534, 29.49, 29.63),
    recorded_tick("14:02:45", 29.60, 269_862_691.37, 97_893, 9_789_334, 29.34, 29.60),
    recorded_tick("14:02:48", 29.63, 271_255_627.37, 98_364, 9_836_434, 29.28, 29.62),
    recorded_tick("14:02:57", 29.67, 276_109_763.37, 100_009, 10_000_934, 29.50, 29.66),
    recorded_tick("14:03:06", 29.27, 277_509_536.37, 100_483, 10_048_334, 29.27, 29.28),
    recorded_tick("14:04:09", 29.06, 286_099_201.27, 103_419, 10_341_934, 29.03, 29.06),
    recorded_tick("14:04:15", 29.02, 286_473_530.27, 103_548, 10_354_834, 29.01, 29.03),
    recorded_tick("14:04:30", 28.91, 287_217_389.27, 103_805, 10_380_534, 28.82, 28.91),
  ]
  decision = None
  for tick in exit_ticks:
    decisions = book.evaluate(
      "300917.SZ",
      ExitEvaluationContext(
        timestamp=tick.time,
        current_price=tick.last_price,
        bid_price=tick.bid_price[0],
        ask_price=tick.ask_price[0],
        limit_up=31.69,
        price_tick=0.01,
      ),
    )
    if decisions:
      [decision] = decisions
      break

  assert decision is not None
  assert decision.reason == "RAPID_PROFIT_REVERSAL"
  assert decision.rule_type == ExitRuleType.RAPID_PROFIT_REVERSAL.value
  assert plan.peak_net_profit_pct == pytest.approx(6.5473, abs=0.0001)
  assert plan.trailing_floor_pct == pytest.approx(5.3473, abs=0.0001)
  assert decision.metrics["consecutive_matches"] == 2
  assert tick.time == datetime(2026, 8, 12, 14, 2, 48)
  realized_net_pct = estimate_net_profit_pct(
    entry_price=27.80,
    exit_price=tick.bid_price[0],
    volume=300,
  )
  assert realized_net_pct == pytest.approx(5.1460, abs=0.0001)


@pytest.mark.asyncio
async def test_removed_active_instrument_is_retained_for_draining():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(
    strategy,
    {"600000.SH": {"eligible": True, "policy_volume": 100}},
  )
  states = strategy.state.get("instrument_states")
  states["600000.SH"].update({"entry_filled_volume": 100, "entry_avg_price": 10.0})
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

  book = ExitPlanBook()
  book.register_entry_fill(
    strategy.build_exit_plan_template(
      instrument_code="600000.SH",
      batch_id=state["batch_id"],
      plan_id=state["exit_plan_id"],
      policy=state["exit_policy_snapshot"],
    ),
    volume=200,
    price=10.0,
  )
  [decision] = book.evaluate(
    "600000.SH",
    ExitEvaluationContext(
      timestamp=datetime(2026, 7, 13, 10, 0),
      current_price=9.79,
    ),
  )
  assert decision.reason == "HARD_STOP"


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
  state = strategy.state.get("instrument_states")["600000.SH"]
  book = ExitPlanBook()
  book.register_entry_fill(
    strategy.build_exit_plan_template(
      instrument_code="600000.SH",
      batch_id=state["batch_id"],
      plan_id=state["exit_plan_id"],
      policy=state["exit_policy_snapshot"],
    ),
    volume=100,
    price=10.0,
  )

  timestamp = datetime(2026, 7, 13, 14, 55)
  assert (
    book.evaluate(
      "600000.SH",
      ExitEvaluationContext(timestamp=timestamp, current_price=9.79),
    )
    == []
  )
  output = await strategy.step(
    make_input(
      timestamp,
      make_tick(timestamp, 9.8),
      exit_plans=book.projections("600000.SH"),
    )
  )

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
  state = strategy.state.get("instrument_states")["600000.SH"]
  book = ExitPlanBook()
  book.register_entry_fill(
    strategy.build_exit_plan_template(
      instrument_code="600000.SH",
      batch_id=state["batch_id"],
      plan_id=state["exit_plan_id"],
      policy=state["exit_policy_snapshot"],
    ),
    volume=100,
    price=10.0,
  )

  before = datetime(2026, 7, 13, 14, 49, 59)
  assert (
    book.evaluate(
      "600000.SH",
      ExitEvaluationContext(timestamp=before, current_price=9.99),
    )
    == []
  )
  at_exit = datetime(2026, 7, 13, 14, 50)
  [decision] = book.evaluate(
    "600000.SH",
    ExitEvaluationContext(timestamp=at_exit, current_price=9.99),
  )
  assert decision.reason == "END_OF_DAY_FLATTEN"


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
  state = strategy.state.get("instrument_states")["600000.SH"]
  book = ExitPlanBook()
  book.register_entry_fill(
    strategy.build_exit_plan_template(
      instrument_code="600000.SH",
      batch_id=state["batch_id"],
      plan_id=state["exit_plan_id"],
      policy=state["exit_policy_snapshot"],
    ),
    volume=100,
    price=10.0,
  )

  friday = datetime(2026, 7, 17, 14, 55)
  assert (
    book.evaluate(
      "600000.SH",
      ExitEvaluationContext(timestamp=friday, current_price=9.99),
    )
    == []
  )

  monday_before = datetime(2026, 7, 20, 14, 49)
  assert (
    book.evaluate(
      "600000.SH",
      ExitEvaluationContext(timestamp=monday_before, current_price=9.99),
    )
    == []
  )
  monday_output = await strategy.step(
    make_input(
      monday_before,
      make_tick(monday_before, 10.0),
      exit_plans=book.projections("600000.SH"),
    )
  )
  assert monday_output.trade_intents == []
  strategy.state.update(monday_output.runtime_state_patch.set)
  state = strategy.state.get("instrument_states")["600000.SH"]
  assert state["holding_trading_days"] == 2

  monday_exit = datetime(2026, 7, 20, 14, 50)
  [decision] = book.evaluate(
    "600000.SH",
    ExitEvaluationContext(timestamp=monday_exit, current_price=9.99),
  )
  assert decision.reason == "MAX_HOLDING_DAYS_REACHED"


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
