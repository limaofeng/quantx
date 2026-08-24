import json
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
  TTradeStatus,
  TTradeTimeExitMode,
)
from quantx_domain.strategies.base import (
  MarketDataContext,
  MarketDataSession,
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
from quantx_domain.trading.t_trade_opportunity_engine import (
  DataHealth,
  OpportunityPolicy,
)
from quantx_infrastructure.models.tick import Tick

REFERENCE_PROFILE = {
  "profile_version": "profile-20260712",
  "profile_schema_version": 1,
  "as_of_trade_date": "2026-07-12",
  "pullback_threshold_pct": 0.8,
  "momentum_rise_threshold_pct": 0.8,
  "momentum_amount_velocity_ratio": 2.0,
  "pullback_max_spread_ticks": 3,
  "momentum_max_spread_ticks": 10,
  "profile_fingerprint": "profile-fingerprint-20260712",
}


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
  continuity_generation: int = 1,
  tick_ordinal: int | None = None,
  profile=REFERENCE_PROFILE,
  emission_allowed: bool = True,
  run_id: str = "run-1",
) -> StrategyInput:
  local_time = timestamp.time()
  session = (
    MarketDataSession.CONTINUOUS_AM
    if datetime.min.time().replace(hour=9, minute=30)
    <= local_time
    <= datetime.min.time().replace(hour=11, minute=30)
    else MarketDataSession.CONTINUOUS_PM
    if datetime.min.time().replace(hour=13)
    <= local_time
    < datetime.min.time().replace(hour=14, minute=57)
    else MarketDataSession.CLOSED
  )
  return StrategyInput(
    run_id=run_id,
    strategy_id="1",
    timestamp=timestamp,
    cadence=StrategyCadence.TICK,
    instrument_code=tick.stock_code,
    event=tick,
    market_data_context=MarketDataContext(
      source="TEST",
      stream_id="test-stream",
      continuity_generation=continuity_generation,
      source_sequence=int(tick_ordinal or timestamp.timestamp() * 1000),
      source_time_ms=int(timestamp.timestamp() * 1000),
      tick_ordinal=int(tick_ordinal or timestamp.timestamp() * 1000),
      received_at_ms=int(timestamp.timestamp() * 1000),
      quote_stale=False,
      session=session,
      trade_date=timestamp.date(),
    ),
    market_context={
      "t_trade_instrument_profile": profile,
      "t_trade_intent_emission": {
        "allowed": emission_allowed,
        "blockers": [] if emission_allowed else ["TEST_EMISSION_BLOCKED"],
      },
    },
    exit_plans=list(exit_plans or []),
  )


async def reconcile(strategy, metadata, **event_flags):
  codes = list(metadata)
  strategy.context.instruments = codes
  output = await strategy.step(
    StrategyInput(
      run_id=strategy.context.run_id,
      strategy_id="1",
      timestamp=datetime(2026, 7, 13, 9, 29),
      cadence=StrategyCadence.RECONCILE,
      instrument_code="",
      event={
        "instruments": codes,
        "instrument_metadata": metadata,
        **event_flags,
      },
    )
  )
  strategy.state.update(output.runtime_state_patch.set)
  return output


async def process_tick(strategy, input):
  output = await strategy.step(input)
  if output.runtime_state_patch is not None:
    strategy.state.update(output.runtime_state_patch.set)
  return output


def make_strategy(
  *,
  run_id: str = "run-1",
  mode: StrategyRunMode = StrategyRunMode.PAPER,
):
  context = StrategyContext(
    run_id=run_id,
    mode=mode,
    instruments=[],
    parameters={
      "account_id": "account-1",
      "target_trade_amount": 10_000.0,
      "max_trade_amount": 12_000.0,
      "signal_policy": OpportunityPolicy().to_dict(),
      "hard_stop_enabled": False,
      "time_exit_mode": TTradeTimeExitMode.UNLIMITED,
      "time_exit_time": "14:50",
      "max_holding_trading_days": 5,
      "target_profit_pct": 2.0,
    },
  )
  return AshareIntradayTAssistantStrategy(context)


async def latch_pullback_candidate(
  strategy: AshareIntradayTAssistantStrategy,
  start: datetime,
):
  output = None
  for seconds, price, amount, volume in [
    (0, 100.0, 0.0, 0.0),
    (60, 99.0, 0.0, 0.0),
    (80, 99.3, 995_000.0, 10_000.0),
    (83, 99.31, 1_000_000.0, 10_100.0),
  ]:
    observed_at = start + timedelta(seconds=seconds)
    output = await process_tick(
      strategy,
      make_input(
        observed_at,
        make_tick(observed_at, price, amount=amount, volume=volume),
        run_id=strategy.context.run_id,
      ),
    )
  assert output is not None
  [intent] = output.trade_intents
  return intent


@pytest.mark.asyncio
async def test_opportunity_decision_and_candidate_identity_are_mode_invariant():
  snapshots = {}
  start = datetime(2026, 7, 13, 9, 30)
  sequence = [
    (0, 100.0, 0.0, 0.0),
    (60, 99.0, 0.0, 0.0),
    (80, 99.3, 995_000.0, 10_000.0),
    (83, 99.31, 1_000_000.0, 10_100.0),
  ]

  for mode in (
    StrategyRunMode.BACKTEST,
    StrategyRunMode.PAPER,
    StrategyRunMode.LIVE,
  ):
    strategy = make_strategy(run_id="mode-invariant-run", mode=mode)
    await strategy.initialize()
    await reconcile(
      strategy,
      {
        "600000.SH": {
          "eligible": True,
          "policy_volume": 100,
          "position_shares": 1_000,
          "position_available_shares": 1_000,
        }
      },
    )
    decisions = []
    for ordinal, (seconds, price, amount, volume) in enumerate(sequence, start=1):
      observed_at = start + timedelta(seconds=seconds)
      output = await process_tick(
        strategy,
        make_input(
          observed_at,
          make_tick(observed_at, price, amount=amount, volume=volume),
          tick_ordinal=ordinal,
          run_id=strategy.context.run_id,
        ),
      )
      opportunity = deepcopy(
        strategy.state["instrument_states"]["600000.SH"]["opportunity"]
      )
      decisions.append(
        {
          "opportunity": opportunity,
          "intents": [
            {
              "intent_id": intent.intent_id,
              "direction": intent.direction.value,
              "reason": intent.reason,
              "execution_mode": intent.execution_mode.value,
              "target_amount": intent.target_amount,
              "limit_price_hint": intent.limit_price_hint,
              "metadata": deepcopy(intent.metadata),
            }
            for intent in output.trade_intents
          ],
        }
      )
    snapshots[mode.value] = decisions

  assert snapshots[StrategyRunMode.BACKTEST.value] == snapshots[
    StrategyRunMode.PAPER.value
  ]
  assert snapshots[StrategyRunMode.PAPER.value] == snapshots[
    StrategyRunMode.LIVE.value
  ]
  final = snapshots[StrategyRunMode.LIVE.value][-1]
  assert final["opportunity"]["candidate"] is not None
  assert final["opportunity"]["latest_evaluation"]["candidate_id"] == (
    final["opportunity"]["candidate"]["candidate_id"]
  )
  assert final["intents"][0]["execution_mode"] == (
    TradeIntentExecutionMode.MANUAL_CONFIRM.value
  )


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

  await process_tick(strategy, make_input(start, make_tick(start, 100.0)))
  await process_tick(strategy, make_input(start, make_tick(start, 20.0, stock_code="000001.SZ")))
  await process_tick(
    strategy,
    make_input(
      start + timedelta(seconds=60), make_tick(start + timedelta(seconds=60), 99.0)
    )
  )
  await process_tick(
    strategy,
    make_input(
      start + timedelta(seconds=80),
      make_tick(start + timedelta(seconds=80), 99.3, amount=995_000, volume=10_000),
    )
  )
  signal_at = start + timedelta(seconds=83)
  signal_output = await process_tick(
    strategy,
    make_input(
      signal_at,
      make_tick(signal_at, 99.31, amount=1_000_000, volume=10_100),
    ),
  )

  assert len(signal_output.trade_intents) == 1
  entry_intent = signal_output.trade_intents[0]
  assert entry_intent.direction == TradeIntentDirection.BUY
  assert entry_intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
  assert entry_intent.target_amount == 10_000.0
  assert entry_intent.target_volume is None
  candidate_id = entry_intent.metadata["candidate_id"]
  hook_patch = strategy.mark_candidate_awaiting_approval(
    "600000.SH",
    candidate_id,
    entry_intent.intent_id,
    source_time_ms=int(signal_at.timestamp() * 1000),
  )
  states = strategy.state.get("instrument_states")
  assert states["600000.SH"]["pending_entry_intent_id"] == entry_intent.intent_id
  assert states["000001.SZ"]["pending_entry_intent_id"] == ""
  assert states["600000.SH"]["requested_entry_amount"] == 10_000.0
  assert (
    states["600000.SH"]["opportunity"]["state_version"]
    == entry_intent.metadata["candidate_state_version"]
  )
  assert entry_intent.metadata["candidate_status"] == "AWAITING_APPROVAL"
  assert entry_intent.metadata["opportunity_schema_version"] >= 3
  [intent_link_event] = hook_patch.append_events
  assert intent_link_event["record_kind"] == "MATERIAL"
  assert intent_link_event["event_type"] == "INTENT_LINKED"
  assert intent_link_event["signal_snapshot"]["candidate_status"] == (
    "AWAITING_APPROVAL"
  )

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
  armed_output = await process_tick(
    strategy,
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
async def test_non_continuous_tick_is_reduced_but_session_gate_blocks_candidate():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(strategy, {"600000.SH": {"eligible": True}})
  auction_at = datetime(2026, 7, 13, 9, 25)

  output = await process_tick(
    strategy,
    make_input(auction_at, make_tick(auction_at, 100.0)),
  )

  opportunity = strategy.state["instrument_states"]["600000.SH"]["opportunity"]
  evaluation = opportunity["latest_evaluation"]
  assert output.trade_intents == []
  assert len(opportunity["samples"]) == 1
  assert any(
    gate["code"] == "CONTINUOUS_SESSION" and gate["passed"] is False
    for gate in evaluation["hard_gates"]
  )


@pytest.mark.asyncio
async def test_pending_cooldown_active_and_ineligible_states_still_reduce_every_tick():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(
    strategy,
    {"600000.SH": {"eligible": False, "reason": "POSITION_NOT_ELIGIBLE"}},
  )
  start = datetime(2026, 7, 13, 9, 30)

  first = await process_tick(strategy, make_input(start, make_tick(start, 100.0)))
  assert first.trade_intents == []
  state = strategy.state["instrument_states"]["600000.SH"]
  assert len(state["opportunity"]["samples"]) == 1

  state["pending_entry_intent_id"] = "pending-1"
  state["entry_order_status"] = "AWAITING_APPROVAL"
  second_at = start + timedelta(seconds=1)
  second = await process_tick(
    strategy,
    make_input(second_at, make_tick(second_at, 99.9)),
  )
  assert second.trade_intents == []
  state = strategy.state["instrument_states"]["600000.SH"]
  assert len(state["opportunity"]["samples"]) == 2
  assert "INTENT_PENDING" in state["opportunity"]["latest_evaluation"][
    "external_blockers"
  ]

  state.update(
    {
      "pending_entry_intent_id": "",
      "entry_order_status": "",
      "entry_filled_volume": 100,
      "exit_filled_volume": 0,
      "exit_plan_id": "missing-plan",
    }
  )
  third_at = start + timedelta(seconds=2)
  active = await process_tick(
    strategy,
    make_input(third_at, make_tick(third_at, 100.1)),
  )
  assert active.trace_payload["reason"] == "WAITING_FOR_EXIT_PLAN_REGISTRATION"
  state = strategy.state["instrument_states"]["600000.SH"]
  assert len(state["opportunity"]["samples"]) == 3
  assert "ACTIVE_T_BATCH_EXISTS" in state["opportunity"]["latest_evaluation"][
    "external_blockers"
  ]
  for forbidden in (
    "position_shares",
    "position_available_shares",
    "available_shares",
    "requested_entry_volume",
    "final_volume",
  ):
    assert forbidden not in state


@pytest.mark.asyncio
async def test_missing_prior_profile_fails_closed_with_complete_v3_snapshot():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(strategy, {"600000.SH": {"eligible": True}})
  timestamp = datetime(2026, 7, 13, 9, 30)

  output = await process_tick(
    strategy,
    make_input(timestamp, make_tick(timestamp, 100.0), profile=None),
  )

  evaluation = strategy.state["instrument_states"]["600000.SH"]["opportunity"][
    "latest_evaluation"
  ]
  assert output.trade_intents == []
  assert evaluation["data_health"] == DataHealth.INSUFFICIENT.value
  assert evaluation["opportunity_score"] is None
  assert {
    "evaluated_at_ms",
    "source_time_ms",
    "tick_ordinal",
    "continuity_generation",
    "features",
    "pullback",
    "momentum",
    "preview_threshold",
    "candidate_threshold",
    "revalidate_threshold",
    "rearm_threshold",
    "signal_version",
    "candidate_state_version",
    "state_schema_version",
    "feature_schema_version",
    "policy_version",
    "config_version",
  } <= evaluation.keys()
  assert any(
    gate["code"] == "DATA_READY" and gate["passed"] is False
    for gate in evaluation["hard_gates"]
  )


@pytest.mark.asyncio
async def test_invalid_last_price_reaches_reducer_and_publishes_material_insufficient_snapshot():
  """Invalid prices must replace, rather than leave, an older actionable view."""
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(strategy, {"600000.SH": {"eligible": True}})
  timestamp = datetime(2026, 7, 13, 9, 30)

  output = await process_tick(
    strategy,
    make_input(timestamp, make_tick(timestamp, 0.0), tick_ordinal=1),
  )

  evaluation = strategy.state["instrument_states"]["600000.SH"]["opportunity"][
    "latest_evaluation"
  ]
  assert output.trade_intents == []
  assert evaluation["data_health"] == DataHealth.INSUFFICIENT.value
  assert "INVALID_PRICE" in evaluation["data_health_reasons"]
  assert evaluation["candidate_status"] != "AWAITING_APPROVAL"
  assert any(
    event["record_kind"] == "MATERIAL"
    and event["signal_snapshot"]["data_health"] == DataHealth.INSUFFICIENT.value
    and "INVALID_PRICE" in event["signal_snapshot"]["data_health_reasons"]
    for event in output.runtime_state_patch.append_events
  )


@pytest.mark.asyncio
async def test_duplicate_and_out_of_order_ticks_are_ignored_without_patch_or_intent():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(strategy, {"600000.SH": {"eligible": True}})
  start = datetime(2026, 7, 13, 9, 30)
  observations = [
    (0, 100.0, 0.0, 0.0),
    (60, 99.0, 0.0, 0.0),
    (80, 99.3, 995_000.0, 10_000.0),
    (83, 99.31, 1_000_000.0, 10_100.0),
  ]
  last_input = None
  for seconds, price, amount, volume in observations:
    observed_at = start + timedelta(seconds=seconds)
    last_input = make_input(
      observed_at,
      make_tick(observed_at, price, amount=amount, volume=volume),
    )
    output = await process_tick(strategy, last_input)
  assert last_input is not None
  assert output.trade_intents
  before = strategy.state.to_dict()

  duplicate = last_input
  same_timestamp = last_input.timestamp
  out_of_order_ordinal = make_input(
    same_timestamp,
    make_tick(same_timestamp, 99.31, amount=1_000_000.0, volume=10_100.0),
    tick_ordinal=last_input.market_data_context.tick_ordinal - 1,
  )
  older_timestamp = start + timedelta(seconds=82)
  out_of_order_time = make_input(
    older_timestamp,
    make_tick(older_timestamp, 99.30, amount=1_010_000.0, volume=10_200.0),
    tick_ordinal=10_000_000,
  )

  for ignored_input, reason in (
    (duplicate, "DUPLICATE_SOURCE_IDENTITY"),
    (out_of_order_ordinal, "OUT_OF_ORDER_SOURCE_IDENTITY"),
    (out_of_order_time, "OUT_OF_ORDER_SOURCE_IDENTITY"),
  ):
    ignored = await strategy.step(ignored_input)
    assert ignored.trade_intents == []
    assert ignored.runtime_state_patch is None
    assert ignored.decision_tags == ["opportunity_tick_ignored", "no_trade"]
    assert ignored.trace_payload["accepted"] is False
    assert ignored.trace_payload["ignored"] is True
    assert ignored.trace_payload["reason"] == reason
    assert strategy.state.to_dict() == before

  next_at = start + timedelta(seconds=84)
  next_output = await process_tick(
    strategy,
    make_input(
      next_at,
      make_tick(next_at, 99.32, amount=1_010_000.0, volume=10_200.0),
    ),
  )
  assert next_output.runtime_state_patch is not None
  opportunity = strategy.state["instrument_states"]["600000.SH"]["opportunity"]
  assert opportunity["samples"][-1]["source_time_ms"] == int(next_at.timestamp() * 1000)
  assert opportunity["candidate_status"] == "LATCHED"


@pytest.mark.asyncio
async def test_sparse_tick_keeps_window_but_generation_change_invalidates_it():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(strategy, {"600000.SH": {"eligible": True}})
  start = datetime(2026, 7, 13, 9, 30)

  await process_tick(strategy, make_input(start, make_tick(start, 100.0)))
  sparse_at = start + timedelta(seconds=120)
  await process_tick(
    strategy,
    make_input(sparse_at, make_tick(sparse_at, 99.9)),
  )
  opportunity = strategy.state["instrument_states"]["600000.SH"]["opportunity"]
  assert len(opportunity["samples"]) == 2
  assert opportunity["latest_evaluation"]["data_health"] != (
    DataHealth.CONTINUITY_LOST.value
  )

  changed_at = sparse_at + timedelta(seconds=1)
  await process_tick(
    strategy,
    make_input(
      changed_at,
      make_tick(changed_at, 99.91),
      continuity_generation=2,
    ),
  )
  opportunity = strategy.state["instrument_states"]["600000.SH"]["opportunity"]
  assert len(opportunity["samples"]) == 1
  assert opportunity["latest_evaluation"]["data_health"] == (
    DataHealth.CONTINUITY_LOST.value
  )


@pytest.mark.asyncio
async def test_rejected_approval_suppresses_candidate_for_current_episode():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(strategy, {"600000.SH": {"eligible": True}})
  start = datetime(2026, 7, 13, 9, 30)
  observations = [
    (0, 100.0, 0.0, 0.0),
    (60, 99.0, 0.0, 0.0),
    (80, 99.3, 995_000.0, 10_000.0),
    (83, 99.31, 1_000_000.0, 10_100.0),
  ]
  output = None
  for seconds, price, amount, volume in observations:
    observed_at = start + timedelta(seconds=seconds)
    output = await process_tick(
      strategy,
      make_input(
        observed_at,
        make_tick(observed_at, price, amount=amount, volume=volume),
      ),
    )
  assert output is not None
  [intent] = output.trade_intents
  signal_at = start + timedelta(seconds=83)
  strategy.mark_candidate_awaiting_approval(
    "600000.SH",
    intent.metadata["candidate_id"],
    intent.intent_id,
    source_time_ms=int(signal_at.timestamp() * 1000),
  )
  before_version = strategy.state["instrument_states"]["600000.SH"][
    "opportunity"
  ]["state_version"]

  patch = await strategy.on_order(
    OrderStateEvent(
      order_id=None,
      status="REJECTED",
      filled_volume=0,
      timestamp=signal_at,
      metadata={
        "intent_id": intent.intent_id,
        "t_trade_role": "entry",
        "instrument_code": "600000.SH",
        "approval_reason": "USER_REJECTED",
      },
    )
  )

  assert patch is not None
  state = strategy.state["instrument_states"]["600000.SH"]
  assert state["pending_entry_intent_id"] == ""
  assert state["requested_entry_amount"] == 0.0
  assert state["opportunity"]["candidate_status"] == "SUPPRESSED"
  assert state["opportunity"]["state_version"] == before_version + 1
  [suppression_event] = patch.append_events
  assert suppression_event["record_kind"] == "MATERIAL"
  assert suppression_event["event_type"] == "CANDIDATE_SUPPRESSED"

  next_at = signal_at + timedelta(seconds=1)
  replay = await process_tick(
    strategy,
    make_input(
      next_at,
      make_tick(next_at, 99.32, amount=1_010_000, volume=10_200),
    ),
  )
  assert replay.trade_intents == []


@pytest.mark.asyncio
async def test_linked_candidate_does_not_self_block_cross_tick_revalidation():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(strategy, {"600000.SH": {"eligible": True}})
  start = datetime(2026, 7, 13, 9, 30)
  intent = await latch_pullback_candidate(strategy, start)
  signal_at = start + timedelta(seconds=83)
  strategy.mark_candidate_awaiting_approval(
    "600000.SH",
    intent.metadata["candidate_id"],
    intent.intent_id,
    source_time_ms=int(signal_at.timestamp() * 1000),
  )

  next_at = signal_at + timedelta(seconds=1)
  next_tick = make_tick(
    next_at,
    99.32,
    amount=1_010_000.0,
    volume=10_200.0,
  )
  await process_tick(strategy, make_input(next_at, next_tick))
  evaluation = strategy.state["instrument_states"]["600000.SH"]["opportunity"][
    "latest_evaluation"
  ]

  assert evaluation["candidate_status"] == "AWAITING_APPROVAL"
  assert evaluation["selected_path"] == "PULLBACK_REBOUND"
  assert "INTENT_PENDING" not in evaluation["external_blockers"]
  assert strategy.validate_manual_approval(intent, next_tick) is None

  blocked_at = next_at + timedelta(seconds=1)
  blocked_tick = make_tick(
    blocked_at,
    99.33,
    amount=1_020_000.0,
    volume=10_300.0,
  )
  await process_tick(
    strategy,
    make_input(blocked_at, blocked_tick, emission_allowed=False),
  )
  blocked_evaluation = strategy.state["instrument_states"]["600000.SH"][
    "opportunity"
  ]["latest_evaluation"]
  assert "INTENT_PENDING" not in blocked_evaluation["external_blockers"]
  assert "TEST_EMISSION_BLOCKED" in blocked_evaluation["external_blockers"]
  rejection = strategy.validate_manual_approval(intent, blocked_tick)
  assert rejection is not None
  assert rejection[0] == "T_TRADE_REVALIDATION_BLOCKED"


@pytest.mark.asyncio
async def test_manual_revalidation_rejects_candidate_path_projection_mismatch():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(strategy, {"600000.SH": {"eligible": True}})
  start = datetime(2026, 7, 13, 9, 30)
  intent = await latch_pullback_candidate(strategy, start)
  signal_at = start + timedelta(seconds=83)
  strategy.mark_candidate_awaiting_approval(
    "600000.SH",
    intent.metadata["candidate_id"],
    intent.intent_id,
    source_time_ms=int(signal_at.timestamp() * 1000),
  )
  state = strategy.state["instrument_states"]["600000.SH"]
  state["opportunity"]["latest_evaluation"]["selected_path"] = (
    "MOMENTUM_ACCELERATION"
  )

  rejection = strategy.validate_manual_approval(
    intent,
    make_tick(signal_at, 99.31, amount=1_000_000.0, volume=10_100.0),
  )

  assert rejection is not None
  assert rejection[0] == "T_TRADE_CANDIDATE_NOT_LATEST"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["REJECTED", "EXPIRED"])
async def test_exact_latched_candidate_compensation_suppresses_without_pending_intent(
  terminal_status,
):
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(strategy, {"600000.SH": {"eligible": True}})
  start = datetime(2026, 7, 13, 9, 30)
  output = None
  for seconds, price, amount, volume in [
    (0, 100.0, 0.0, 0.0),
    (60, 99.0, 0.0, 0.0),
    (80, 99.3, 995_000.0, 10_000.0),
    (83, 99.31, 1_000_000.0, 10_100.0),
  ]:
    observed_at = start + timedelta(seconds=seconds)
    output = await process_tick(
      strategy,
      make_input(
        observed_at,
        make_tick(observed_at, price, amount=amount, volume=volume),
      ),
    )
  assert output is not None
  [intent] = output.trade_intents
  state = strategy.state["instrument_states"]["600000.SH"]
  assert state["pending_entry_intent_id"] == ""
  assert state["opportunity"]["candidate_status"] == "LATCHED"
  before_version = state["opportunity"]["state_version"]

  patch = await strategy.on_order(
    OrderStateEvent(
      order_id=None,
      status=terminal_status,
      filled_volume=0,
      timestamp=start + timedelta(seconds=83),
      metadata={
        **intent.metadata,
        "intent_id": intent.intent_id,
        "instrument_code": "600000.SH",
        "candidate_id": intent.metadata["candidate_id"],
        "approval_reason": "CANDIDATE_PERSISTENCE_FAILED",
      },
    )
  )

  assert patch is not None
  state = strategy.state["instrument_states"]["600000.SH"]
  assert state["opportunity"]["candidate_status"] == "SUPPRESSED"
  assert state["opportunity"]["state_version"] == before_version + 1
  assert state["pending_entry_intent_id"] == ""
  [suppression_event] = patch.append_events
  assert suppression_event["event_type"] == "CANDIDATE_SUPPRESSED"
  assert suppression_event["transition"]["candidate_id"] == intent.metadata["candidate_id"]


@pytest.mark.asyncio
async def test_latched_candidate_compensation_ignores_mismatched_candidate_identity():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(strategy, {"600000.SH": {"eligible": True}})
  start = datetime(2026, 7, 13, 9, 30)
  output = None
  for seconds, price, amount, volume in [
    (0, 100.0, 0.0, 0.0),
    (60, 99.0, 0.0, 0.0),
    (80, 99.3, 995_000.0, 10_000.0),
    (83, 99.31, 1_000_000.0, 10_100.0),
  ]:
    observed_at = start + timedelta(seconds=seconds)
    output = await process_tick(
      strategy,
      make_input(
        observed_at,
        make_tick(observed_at, price, amount=amount, volume=volume),
      ),
    )
  assert output is not None
  [intent] = output.trade_intents

  patch = await strategy.on_order(
    OrderStateEvent(
      order_id=None,
      status="REJECTED",
      filled_volume=0,
      timestamp=start + timedelta(seconds=83),
      metadata={
        **intent.metadata,
        "intent_id": intent.intent_id,
        "instrument_code": "600000.SH",
        "candidate_id": "different-candidate",
        "approval_reason": "CANDIDATE_PERSISTENCE_FAILED",
      },
    )
  )

  assert patch is None
  state = strategy.state["instrument_states"]["600000.SH"]
  assert state["opportunity"]["candidate_status"] == "LATCHED"


def test_v2_migration_preserves_filled_batch_but_clears_unconfirmed_entry():
  strategy = make_strategy()
  strategy.apply_state_snapshot(
    {
      "state_schema_version": 2,
      "instrument_states": {
        "600000.SH": {
          "status": TTradeStatus.AWAITING_APPROVAL,
          "pending_entry_intent_id": "legacy-intent",
          "entry_order_status": "AWAITING_APPROVAL",
          "entry_filled_volume": 100,
          "entry_avg_price": 10.0,
          "exit_filled_volume": 0,
          "batch_id": "filled-batch",
          "exit_plan_id": "filled-exit-plan",
          "exit_policy_snapshot": {"config_version": 7},
          "current_signal": {"reason": "LEGACY_SIGNAL"},
          "requested_entry_volume": 100,
          "position_shares": 1_000,
        }
      },
    }
  )

  state = strategy.state["instrument_states"]["600000.SH"]
  assert strategy.state["state_schema_version"] == 3
  assert state["status"] == TTradeStatus.MONITORING
  assert state["batch_id"] == "filled-batch"
  assert state["exit_plan_id"] == "filled-exit-plan"
  assert state["entry_filled_volume"] == 100
  assert state["opportunity"] == {}
  assert state["pending_entry_intent_id"] == ""
  assert state["entry_order_status"] == ""
  assert strategy.invalidated_manual_intent_ids() == ["legacy-intent"]
  assert "current_signal" not in state
  assert "requested_entry_volume" not in state
  assert "position_shares" not in state


@pytest.mark.asyncio
async def test_reconciliation_required_blocks_entries_across_managed_universe():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(
    strategy,
    {
      "600000.SH": {
        "eligible": True,
        "position_shares": 1000,
        "position_available_shares": 1000,
      },
      "000001.SZ": {
        "eligible": True,
        "position_shares": 1000,
        "position_available_shares": 1000,
      },
    },
  )
  states = strategy.state["instrument_states"]
  states["600000.SH"]["pending_entry_intent_id"] = "intent-needs-reconcile"
  states["600000.SH"]["entry_order_status"] = "RECONCILE_REQUIRED"
  states["600000.SH"]["status"] = "RECONCILE_REQUIRED"
  strategy.state.update({"instrument_states": states})
  start = datetime(2026, 7, 13, 9, 30)

  outputs = []
  for seconds, price in ((0, 20.0), (60, 19.8), (80, 19.86)):
    tick_at = start + timedelta(seconds=seconds)
    outputs.append(
      await process_tick(
        strategy,
        make_input(
          tick_at,
          make_tick(
            tick_at,
            price,
            stock_code="000001.SZ",
            amount=198_500.0,
            volume=10_000.0,
          ),
        )
      )
    )

  assert all(output.trade_intents == [] for output in outputs)
  assert outputs[-1].trace_payload["reason"] == "T_TRADE_RECONCILIATION_REQUIRED"


@pytest.mark.asyncio
async def test_exit_reconciliation_required_blocks_entry_on_other_instrument():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(
    strategy,
    {
      "600000.SH": {
        "eligible": True,
        "position_shares": 1000,
        "position_available_shares": 1000,
      },
      "000001.SZ": {
        "eligible": True,
        "position_shares": 1000,
        "position_available_shares": 1000,
      },
    },
  )
  states = strategy.state["instrument_states"]
  states["600000.SH"].update(
    {
      "status": "EXIT_SUBMITTED",
      "pending_exit_intent_id": "exit-intent-needs-reconcile",
      "exit_order_status": "SUBMITTED",
      "entry_filled_volume": 100,
    }
  )
  strategy.state.update({"instrument_states": states})

  patch = await strategy.on_order(
    OrderStateEvent(
      order_id="ambiguous-exit-order",
      status="RECONCILE_REQUIRED",
      metadata={
        "intent_id": "exit-intent-needs-reconcile",
        "t_trade_role": "exit",
        "instrument_code": "600000.SH",
        "approval_reason": "DELIVERED_COMMAND_OUTCOME_UNKNOWN",
      },
    )
  )
  assert patch is not None
  strategy.state.update(patch.set)
  exit_state = strategy.state["instrument_states"]["600000.SH"]
  assert exit_state["status"] == TTradeStatus.RECONCILE_REQUIRED
  assert exit_state["exit_order_status"] == "RECONCILE_REQUIRED"
  assert exit_state["reconciliation_reason"] == "DELIVERED_COMMAND_OUTCOME_UNKNOWN"

  start = datetime(2026, 7, 13, 9, 30)
  outputs = []
  for seconds, price in ((0, 20.0), (60, 19.8), (80, 19.86)):
    tick_at = start + timedelta(seconds=seconds)
    outputs.append(
      await process_tick(
        strategy,
        make_input(
          tick_at,
          make_tick(
            tick_at,
            price,
            stock_code="000001.SZ",
            amount=198_500.0,
            volume=10_000.0,
          ),
        )
      )
    )

  assert all(output.trade_intents == [] for output in outputs)
  assert outputs[-1].trace_payload["reason"] == "T_TRADE_RECONCILIATION_REQUIRED"


@pytest.mark.asyncio
async def test_first_exit_report_adopts_intent_from_matching_exit_plan():
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
  states = strategy.state["instrument_states"]
  states["600000.SH"].update(
    {
      "status": TTradeStatus.MONITORING,
      "entry_filled_volume": 100,
      "entry_avg_price": 10.0,
      "batch_id": "batch-first-exit-report",
      "exit_plan_id": "exit-plan-first-report",
      "pending_exit_intent_id": "",
    }
  )
  strategy.state.update({"instrument_states": states})

  patch = await strategy.on_order(
    OrderStateEvent(
      order_id="exit-order-first-report",
      status="RECONCILE_REQUIRED",
      metadata={
        "intent_id": "exit-intent-first-report",
        "t_trade_role": "exit",
        "instrument_code": "600000.SH",
        "exit_plan_id": "exit-plan-first-report",
        "approval_reason": "DELIVERED_COMMAND_OUTCOME_UNKNOWN",
      },
    )
  )

  assert patch is not None
  state = strategy.state["instrument_states"]["600000.SH"]
  assert state["pending_exit_intent_id"] == "exit-intent-first-report"
  assert state["exit_order_status"] == "RECONCILE_REQUIRED"
  assert state["status"] == TTradeStatus.RECONCILE_REQUIRED


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("terminal_status", "reported_fill"),
  [("FILLED", 100), ("CANCELLED", 40)],
)
async def test_terminal_entry_order_waits_for_independent_trade_report(
  terminal_status: str,
  reported_fill: int,
):
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(
    strategy,
    {
      "600000.SH": {
        "eligible": True,
        "position_shares": 1000,
        "position_available_shares": 1000,
      },
      "000001.SZ": {
        "eligible": True,
        "position_shares": 1000,
        "position_available_shares": 1000,
      },
    },
  )
  states = strategy.state["instrument_states"]
  states["600000.SH"].update(
    {
      "status": TTradeStatus.ENTRY_SUBMITTED,
      "pending_entry_intent_id": "entry-intent",
      "entry_order_status": "SUBMITTED",
      "entry_pending_fill_base": 0,
      "requested_entry_amount": 1_000.0,
      "batch_id": "batch-entry",
      "exit_plan_id": "exit-plan-entry",
    }
  )
  strategy.state.update({"instrument_states": states})

  await strategy.on_order(
    OrderStateEvent(
      order_id="entry-order",
      status=terminal_status,
      request=SimpleNamespace(volume=100),
      filled_volume=reported_fill,
      metadata={
        "intent_id": "entry-intent",
        "t_trade_role": "entry",
        "instrument_code": "600000.SH",
      },
    )
  )
  waiting = strategy.state["instrument_states"]["600000.SH"]
  assert waiting["status"] == TTradeStatus.RECONCILE_REQUIRED
  assert waiting["pending_entry_intent_id"] == "entry-intent"
  assert waiting["entry_expected_fill_volume"] == reported_fill

  first_fill = reported_fill // 2
  await strategy.on_trade(
    TradeExecutionEvent(
      order_id="entry-order",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10.0,
      volume=first_fill,
      trade_time=datetime(2026, 7, 13, 9, 31),
      metadata={
        "intent_id": "entry-intent",
        "t_trade_role": "entry",
        "instrument_code": "600000.SH",
        "t_batch_id": "batch-entry",
        "exit_plan_id": "exit-plan-entry",
      },
    )
  )
  still_waiting = strategy.state["instrument_states"]["600000.SH"]
  assert still_waiting["status"] == TTradeStatus.RECONCILE_REQUIRED
  assert still_waiting["pending_entry_intent_id"] == "entry-intent"

  start = datetime(2026, 7, 13, 9, 32)
  outputs = []
  for seconds, price in ((0, 20.0), (60, 19.8), (80, 19.86)):
    tick_at = start + timedelta(seconds=seconds)
    outputs.append(
      await process_tick(
        strategy,
        make_input(
          tick_at,
          make_tick(
            tick_at,
            price,
            stock_code="000001.SZ",
            amount=198_500.0,
            volume=10_000.0,
          ),
        )
      )
    )
  assert all(output.trade_intents == [] for output in outputs)
  assert outputs[-1].trace_payload["reason"] == "T_TRADE_RECONCILIATION_REQUIRED"

  await strategy.on_trade(
    TradeExecutionEvent(
      order_id="entry-order",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10.0,
      volume=reported_fill - first_fill,
      trade_time=datetime(2026, 7, 13, 9, 34),
      metadata={
        "intent_id": "entry-intent",
        "t_trade_role": "entry",
        "instrument_code": "600000.SH",
        "t_batch_id": "batch-entry",
        "exit_plan_id": "exit-plan-entry",
      },
    )
  )
  settled = strategy.state["instrument_states"]["600000.SH"]
  assert settled["status"] == TTradeStatus.MONITORING
  assert settled["pending_entry_intent_id"] == ""
  assert settled["entry_order_status"] == terminal_status
  assert settled["entry_expected_fill_volume"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("reported_fill", "expected_status"),
  [(40, TTradeStatus.MONITORING), (100, TTradeStatus.COOLDOWN)],
)
async def test_terminal_exit_order_waits_for_trade_and_preserves_final_state(
  reported_fill: int,
  expected_status: TTradeStatus,
):
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
  states = strategy.state["instrument_states"]
  states["600000.SH"].update(
    {
      "status": TTradeStatus.EXIT_SUBMITTED,
      "pending_exit_intent_id": "exit-intent",
      "exit_order_status": "SUBMITTED",
      "exit_pending_fill_base": 0,
      "entry_filled_volume": 100,
      "entry_avg_price": 10.0,
      "batch_id": "batch-exit",
      "exit_plan_id": "exit-plan-exit",
      "exit_policy_snapshot": {"cooldown_seconds": 300},
    }
  )
  strategy.state.update({"instrument_states": states})

  await strategy.on_order(
    OrderStateEvent(
      order_id="exit-order",
      status="CANCELLED",
      request=SimpleNamespace(volume=100),
      filled_volume=reported_fill,
      metadata={
        "intent_id": "exit-intent",
        "t_trade_role": "exit",
        "instrument_code": "600000.SH",
      },
    )
  )
  waiting = strategy.state["instrument_states"]["600000.SH"]
  assert waiting["status"] == TTradeStatus.RECONCILE_REQUIRED
  assert waiting["pending_exit_intent_id"] == "exit-intent"

  first_fill = reported_fill // 2
  await strategy.on_trade(
    TradeExecutionEvent(
      order_id="exit-order",
      instrument_code="600000.SH",
      trade_type="SELL",
      price=10.1,
      volume=first_fill,
      trade_time=datetime(2026, 7, 13, 10, 0),
      metadata={
        "intent_id": "exit-intent",
        "t_trade_role": "exit",
        "instrument_code": "600000.SH",
        "exit_plan_id": "exit-plan-exit",
      },
    )
  )
  still_waiting = strategy.state["instrument_states"]["600000.SH"]
  assert still_waiting["status"] == TTradeStatus.RECONCILE_REQUIRED
  assert still_waiting["pending_exit_intent_id"] == "exit-intent"
  assert still_waiting["batch_id"] == "batch-exit"

  await strategy.on_trade(
    TradeExecutionEvent(
      order_id="exit-order",
      instrument_code="600000.SH",
      trade_type="SELL",
      price=10.1,
      volume=reported_fill - first_fill,
      trade_time=datetime(2026, 7, 13, 10, 1),
      metadata={
        "intent_id": "exit-intent",
        "t_trade_role": "exit",
        "instrument_code": "600000.SH",
        "exit_plan_id": "exit-plan-exit",
      },
    )
  )
  settled = strategy.state["instrument_states"]["600000.SH"]
  assert settled["status"] == expected_status
  assert settled["pending_exit_intent_id"] == ""
  assert settled["exit_order_status"] == "CANCELLED"
  assert settled["exit_expected_fill_volume"] == 0
  if reported_fill == 100:
    assert settled["batch_id"] == ""
  else:
    assert settled["batch_id"] == "batch-exit"


@pytest.mark.asyncio
async def test_exit_execution_report_underfill_keeps_pending_even_after_active_zero():
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
  states = strategy.state["instrument_states"]
  states["600000.SH"].update(
    {
      "status": TTradeStatus.EXIT_SUBMITTED,
      "pending_exit_intent_id": "exit-overfill-intent",
      "exit_order_status": "SUBMITTED",
      "entry_filled_volume": 100,
      "entry_avg_price": 10.0,
      "batch_id": "batch-overfill",
      "exit_plan_id": "exit-plan-overfill",
      "exit_policy_snapshot": {"cooldown_seconds": 300},
    }
  )
  strategy.state.update({"instrument_states": states})
  await strategy.on_order(
    OrderStateEvent(
      order_id="exit-overfill-order",
      status="CANCELLED",
      request=SimpleNamespace(volume=120),
      filled_volume=120,
      metadata={
        "intent_id": "exit-overfill-intent",
        "t_trade_role": "exit",
        "instrument_code": "600000.SH",
      },
    )
  )

  await strategy.on_trade(
    TradeExecutionEvent(
      order_id="exit-overfill-order",
      instrument_code="600000.SH",
      trade_type="SELL",
      price=10.1,
      volume=100,
      trade_time=datetime(2026, 7, 13, 10, 0),
      metadata={
        "intent_id": "exit-overfill-intent",
        "t_trade_role": "exit",
        "instrument_code": "600000.SH",
        "exit_plan_id": "exit-plan-overfill",
      },
    )
  )
  state = strategy.state["instrument_states"]["600000.SH"]
  assert state["status"] == TTradeStatus.RECONCILE_REQUIRED
  assert state["pending_exit_intent_id"] == "exit-overfill-intent"
  assert state["batch_id"] == "batch-overfill"
  assert state["exit_expected_fill_volume"] == 120


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

  imported = strategy.import_external_entry(
    "300917.SZ",
    300,
    27.80,
    "tefa-service-20260812-entry",
  )
  strategy.state.update(imported.set)
  state = strategy.state["instrument_states"]["300917.SZ"]
  template = strategy.build_exit_plan_template(
    instrument_code="300917.SZ",
    batch_id=state["batch_id"],
    plan_id=state["exit_plan_id"],
    policy=state["exit_policy_snapshot"],
  ).to_dict()
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
  assert "entry_eligible" not in state
  assert state["status"] == TTradeStatus.DRAINING


@pytest.mark.asyncio
async def test_policy_change_rewarms_opportunity_and_preserves_filled_batch_state():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(strategy, {"600000.SH": {"eligible": True}})
  start = datetime(2026, 7, 13, 9, 30)
  intent = await latch_pullback_candidate(strategy, start)
  signal_at = start + timedelta(seconds=83)
  strategy.mark_candidate_awaiting_approval(
    "600000.SH",
    intent.metadata["candidate_id"],
    intent.intent_id,
    source_time_ms=int(signal_at.timestamp() * 1000),
  )
  state = strategy.state["instrument_states"]["600000.SH"]
  original_batch_id = state["batch_id"]
  original_exit_plan_id = state["exit_plan_id"]
  original_exit_policy = dict(state["exit_policy_snapshot"])
  state.update(
    {
      "entry_filled_volume": 100,
      "entry_avg_price": 99.31,
      "status": TTradeStatus.MONITORING,
    }
  )
  strategy.context.parameters["global_config_version"] = 2

  output = await reconcile(
    strategy,
    {"600000.SH": {"eligible": True}},
    policy_changed=True,
  )

  rewarmed = strategy.state["instrument_states"]["600000.SH"]
  opportunity = rewarmed["opportunity"]
  evaluation = opportunity["latest_evaluation"]
  assert opportunity["candidate"] is None
  assert opportunity["candidate_status"] == "NONE"
  assert opportunity["samples"] == []
  assert opportunity["data_health"] == DataHealth.WARMING.value
  assert opportunity["config_version"] == 2
  assert evaluation["data_health"] == DataHealth.WARMING.value
  assert evaluation["features"]["price"] is None
  assert evaluation["source_time_ms"] is None
  assert evaluation["continuity_generation"] is None
  assert rewarmed["pending_entry_intent_id"] == ""
  assert rewarmed["entry_filled_volume"] == 100
  assert rewarmed["entry_avg_price"] == 99.31
  assert rewarmed["batch_id"] == original_batch_id
  assert rewarmed["exit_plan_id"] == original_exit_plan_id
  assert rewarmed["exit_policy_snapshot"] == original_exit_policy
  assert rewarmed["status"] == TTradeStatus.MONITORING
  [event] = output.runtime_state_patch.append_events
  assert event["type"] == "T_TRADE_OPPORTUNITY_EVALUATION"
  assert event["record_kind"] == "MATERIAL"
  assert event["event_type"] == "POLICY_CHANGED"
  assert event["signal_snapshot"]["features"]["price"] is None
  assert event["transition"]["candidate_id"] == intent.metadata["candidate_id"]

  version_after_rewarm = opportunity["state_version"]
  repeated = await reconcile(
    strategy,
    {"600000.SH": {"eligible": True}},
    policy_changed=True,
  )
  assert repeated.runtime_state_patch.append_events == []
  assert (
    strategy.state["instrument_states"]["600000.SH"]["opportunity"][
      "state_version"
    ]
    == version_after_rewarm
  )

  ordinary = await reconcile(strategy, {"600000.SH": {"eligible": True}})
  assert ordinary.runtime_state_patch.append_events == []


@pytest.mark.asyncio
async def test_compact_persistence_projection_omits_hot_samples_and_backtest_rewarms():
  source = make_strategy(run_id="compact-opportunity", mode=StrategyRunMode.BACKTEST)
  await source.initialize()
  await reconcile(
    source,
    {
      "600000.SH": {
        "eligible": True,
        "policy_volume": 100,
        "position_shares": 1_000,
        "position_available_shares": 1_000,
      }
    },
  )
  start = datetime(2026, 7, 13, 9, 30)
  intent = await latch_pullback_candidate(source, start)
  approval_patch = source.mark_candidate_awaiting_approval(
    "600000.SH",
    intent.metadata["candidate_id"],
    intent.intent_id,
    source_time_ms=int((start + timedelta(seconds=83)).timestamp() * 1000),
  )
  source.state.update(approval_patch.set)

  full_state = source.state["instrument_states"]["600000.SH"]
  full_state.update(
    {
      "entry_filled_volume": 100,
      "entry_avg_price": 99.31,
      "batch_id": "active-batch",
      "exit_plan_id": "exit-plan-1",
      "status": TTradeStatus.MONITORING,
    }
  )
  opportunity = full_state["opportunity"]
  seed = dict(opportunity["samples"][-1])
  opportunity["samples"] = [
    {
      **seed,
      "source_time_ms": int(start.timestamp() * 1000) + ordinal,
      "tick_ordinal": ordinal,
      "sentinel": f"hot-sample-{ordinal}",
    }
    for ordinal in range(1, 1_001)
  ]

  projection = source.persistence_state_snapshot()
  projected = projection["instrument_states"]["600000.SH"]
  projected_opportunity = projected["opportunity"]
  full_json_bytes = len(json.dumps(source.state.to_dict(), sort_keys=True).encode())
  projected_json_bytes = len(json.dumps(projection, sort_keys=True).encode())

  assert len(full_state["opportunity"]["samples"]) == 1_000
  assert full_state["opportunity"]["samples"][0]["sentinel"] == "hot-sample-1"
  assert "samples" not in projected_opportunity
  assert "hot-sample-1" not in json.dumps(projection, sort_keys=True)
  assert projected_opportunity["sample_window_persisted"] is False
  assert projected_opportunity["sample_window_restore_required"] is True
  assert projected_opportunity["sample_window_sample_count"] == 1_000
  assert projected_opportunity["sample_window_last_source_identity"] == {
    "continuity_generation": "1",
    "source_time_ms": int(start.timestamp() * 1000) + 1_000,
    "tick_ordinal": 1_000,
  }
  assert projected["entry_filled_volume"] == 100
  assert projected["batch_id"] == "active-batch"
  assert projected["pending_entry_intent_id"] == intent.intent_id
  assert projected_opportunity["candidate"]["candidate_id"] == intent.metadata[
    "candidate_id"
  ]
  assert projected_json_bytes * 20 < full_json_bytes

  restored = make_strategy(
    run_id="compact-opportunity-restored",
    mode=StrategyRunMode.BACKTEST,
  )
  restored.apply_state_snapshot(projection)
  await restored.initialize()
  assert restored.pending_manual_intent_ids() == [intent.intent_id]
  restored_state = restored.state["instrument_states"]["600000.SH"]
  assert restored_state["entry_filled_volume"] == 100
  assert restored_state["batch_id"] == "active-batch"
  assert restored_state["opportunity"]["candidate"]["candidate_id"] == (
    intent.metadata["candidate_id"]
  )

  await reconcile(restored, {"600000.SH": {"eligible": True}})
  output = await process_tick(
    restored,
    make_input(
      start + timedelta(minutes=10),
      make_tick(start + timedelta(minutes=10), 100.0),
      run_id=restored.context.run_id,
      tick_ordinal=2_000,
    ),
  )

  rebuilt = restored.state["instrument_states"]["600000.SH"]["opportunity"]
  assert output.trade_intents == []
  assert rebuilt["samples"]
  assert len(rebuilt["samples"]) == 1
  assert rebuilt["data_health"] == DataHealth.WARMING.value
  assert rebuilt["candidate"] is None
  assert "sample_window_persisted" not in rebuilt
  assert "sample_window_restore_required" not in rebuilt


@pytest.mark.asyncio
async def test_policy_change_clears_unfilled_candidate_execution_template():
  strategy = make_strategy()
  await strategy.initialize()
  await reconcile(strategy, {"600000.SH": {"eligible": True}})
  start = datetime(2026, 7, 13, 9, 30)
  intent = await latch_pullback_candidate(strategy, start)
  signal_at = start + timedelta(seconds=83)
  strategy.mark_candidate_awaiting_approval(
    "600000.SH",
    intent.metadata["candidate_id"],
    intent.intent_id,
    source_time_ms=int(signal_at.timestamp() * 1000),
  )
  strategy.context.parameters["global_config_version"] = 2

  await reconcile(
    strategy,
    {"600000.SH": {"eligible": True}},
    configuration_changed=True,
  )

  state = strategy.state["instrument_states"]["600000.SH"]
  assert state["opportunity"]["candidate"] is None
  assert state["opportunity"]["candidate_status"] == "NONE"
  assert state["pending_entry_intent_id"] == ""
  assert state["entry_order_status"] == ""
  assert state["requested_entry_amount"] == 0.0
  assert state["batch_id"] == ""
  assert state["exit_plan_id"] == ""
  assert state["entry_filled_volume"] == 0


@pytest.mark.asyncio
async def test_candidate_intent_identity_is_stable_per_run_and_distinct_across_runs():
  async def create(run_id):
    strategy = make_strategy(run_id=run_id)
    await strategy.initialize()
    await reconcile(strategy, {"600000.SH": {"eligible": True}})
    intent = await latch_pullback_candidate(
      strategy,
      datetime(2026, 7, 13, 9, 30),
    )
    return intent

  first = await create("run-a")
  retried = await create("run-a")
  other_run = await create("run-b")

  assert first.metadata["candidate_fingerprint"] == retried.metadata[
    "candidate_fingerprint"
  ]
  assert first.intent_id == retried.intent_id
  assert first.metadata["t_batch_id"] == retried.metadata["t_batch_id"]
  assert other_run.metadata["candidate_fingerprint"] == first.metadata[
    "candidate_fingerprint"
  ]
  assert other_run.intent_id != first.intent_id
  assert other_run.metadata["t_batch_id"] != first.metadata["t_batch_id"]
  exit_metadata = first.metadata["exit_plan_template"]["metadata"]
  assert exit_metadata["account_id"] == first.metadata["account_id"]
  assert exit_metadata["strategy_run_id"] == "run-a"
  assert exit_metadata["candidate_id"] == first.metadata["candidate_id"]
  assert exit_metadata["candidate_fingerprint"] == first.metadata[
    "candidate_fingerprint"
  ]
  assert exit_metadata["policy_version"] == first.metadata["policy_version"]
  assert exit_metadata["feature_schema_version"] == first.metadata[
    "feature_schema_version"
  ]


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
  assert "current_signal" not in state

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
  output = await process_tick(
    strategy,
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
  monday_output = await process_tick(
    strategy,
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
  output = await process_tick(strategy, make_input(timestamp, make_tick(timestamp, 10.0)))

  state = output.runtime_state_patch.set["instrument_states"]["600000.SH"]
  assert state["exit_policy_snapshot"]["config_version"] == 2
  assert state["exit_policy_snapshot"]["hard_stop_enabled"] is True
  audit_event = next(
    event
    for event in output.runtime_state_patch.append_events
    if event["type"] == "T_TRADE_EXIT_POLICY_UPDATED"
  )
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
  output = await process_tick(strategy, make_input(timestamp, make_tick(timestamp, 9.8)))

  state = output.runtime_state_patch.set["instrument_states"]["600000.SH"]
  assert output.trade_intents == []
  assert state["pending_exit_intent_id"] == "existing-exit-intent"
  assert state["exit_order_status"] == "ACCEPTED"
  assert any(
    event["type"] == "T_TRADE_EXIT_POLICY_UPDATED"
    for event in output.runtime_state_patch.append_events
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
