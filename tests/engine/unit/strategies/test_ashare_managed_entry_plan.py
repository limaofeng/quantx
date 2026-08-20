from datetime import datetime, timedelta

import pytest
from quantx_domain.enums import StrategyRunMode
from quantx_domain.strategies.ashare_managed_entry_plan import (
  ENTRY_PLAN_ENABLED_KEY,
  MANAGED_ENTRY_STATE_KEY,
  AshareManagedEntryPlanStrategy,
  _build_evaluation_context,
  _exit_plan_template_for_stage,
)
from quantx_domain.strategies.base import (
  OrderStateEvent,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  TradeExecutionEvent,
  TradeIntentDirection,
  TradeIntentType,
)
from quantx_domain.trading.entry_plan import (
  EntryGapCalculator,
  EntryPlanStatus,
  ManagedEntryPlanState,
)

NOW = datetime(2026, 8, 20, 10, 0)


def plan_parameters(*, mode="INCREMENTAL_AMOUNT_CNY", target_value=30_000):
  target = {
    "mode": mode,
    "target_position_pct": target_value if mode == "TARGET_POSITION_PCT" else None,
    "incremental_amount_cny": target_value
    if mode == "INCREMENTAL_AMOUNT_CNY"
    else None,
    "additional_volume": target_value if mode == "ADDITIONAL_VOLUME" else None,
    "max_total_amount_cny": 50_000,
    "max_position_pct": 0.6,
    "baseline_snapshot": {
      "position_volume": 0,
      "market_value_cny": 0,
      "total_asset_cny": 100_000,
      "reference_price": 10,
      "account_snapshot_version": "v1",
    },
  }
  return {
    ENTRY_PLAN_ENABLED_KEY: True,
    MANAGED_ENTRY_STATE_KEY: {
      "template_version": 1,
      "config_version": 2,
      "instrument_code": "600000.SH",
      "bucket": "core",
      "target_policy": target,
      "trigger_rules": [
        {
          "rule_id": "manual",
          "rule_type": "MANUAL_TRIGGER",
          "priority": 100,
          "parameters": {},
        }
      ],
      "pacing_policy": {
        "tranche_count": 3,
        "max_single_intent_amount_cny": 20_000,
        "max_daily_filled_amount_cny": 30_000,
        "max_orders_per_day": 3,
        "max_open_orders": 1,
      },
      "execution_policy": {
        "environment": "PAPER",
        "authorization_mode": "AUTO",
        "price_reference": "ASK1_PROTECTED_LIMIT",
        "approval_ttl_ms": 60_000,
        "max_price_deviation_bps": 50,
      },
      "completion_policy": {"max_buy_price": 12},
    }
  }


async def strategy(parameters=None):
  item = AshareManagedEntryPlanStrategy(
    StrategyContext(
      run_id="run-1",
      mode=StrategyRunMode.PAPER,
      instruments=["600000.SH"],
      parameters=parameters or plan_parameters(),
      current_time=NOW,
    )
  )
  await item.initialize()
  return item


def input_snapshot(**changes):
  values = dict(
    run_id="run-1",
    strategy_id="ashare_managed_entry_plan",
    timestamp=NOW,
    cadence=StrategyCadence.RECONCILE,
    instrument_code="600000.SH",
    market_data={"ask_prices": [10], "data_quality": "OK"},
    event={"type": "MANUAL_ENTRY_TRIGGER", "rule_id": "manual"},
    portfolio_state={
      "account": {"total_equity_cny": 100_000},
      "positions": {"600000.SH": {"total_volume": 0, "market_value_cny": 0}},
    },
    market_context={"data_quality": "OK", "market_ready": True},
    risk_caps={"allow_buy": True, "max_buy_amount_cny": 50_000},
    position_profile={"allow_bucket_buy": {"core": True}},
  )
  values.update(changes)
  return StrategyInput(**values)


@pytest.mark.asyncio
async def test_strategy_maps_manual_rule_to_incremental_target_amount_only():
  item = await strategy()

  output = await item.step(input_snapshot())

  [intent] = output.trade_intents
  assert intent.direction == TradeIntentDirection.BUY
  assert intent.intent_type == TradeIntentType.TARGET_AMOUNT
  assert intent.target_amount == 20_000
  assert intent.target_position_pct is None
  assert intent.target_volume is None
  assert intent.metadata["owner_type"] == "STRATEGY_RUN"
  assert intent.metadata["entry_plan_id"] == "run-1"
  assert (
    output.runtime_state_patch.set[MANAGED_ENTRY_STATE_KEY]["pending_intent_id"]
    == intent.intent_id
  )


@pytest.mark.asyncio
async def test_position_pct_is_converted_to_gap_after_holdings_and_open_buy():
  item = await strategy(plan_parameters(mode="TARGET_POSITION_PCT", target_value=0.5))
  snapshot = input_snapshot(
    portfolio_state={
      "account": {"total_equity_cny": 100_000},
      "positions": {"600000.SH": {"total_volume": 1_000, "market_value_cny": 10_000}},
    },
    open_orders=[
      {
        "instrument_code": "600000.SH",
        "side": "BUY",
        "status": "ACCEPTED",
        "requested_volume": 500,
        "filled_volume": 0,
        "limit_price": 10,
      }
    ],
  )

  [intent] = (await item.step(snapshot)).trade_intents

  assert intent.intent_type == TradeIntentType.TARGET_AMOUNT
  assert intent.target_amount == 20_000
  assert intent.target_position_pct is None


@pytest.mark.asyncio
async def test_additional_volume_emits_target_volume_without_lot_sizing():
  item = await strategy(plan_parameters(mode="ADDITIONAL_VOLUME", target_value=1_550))

  [intent] = (await item.step(input_snapshot())).trade_intents

  assert intent.intent_type == TradeIntentType.TARGET_VOLUME
  assert intent.target_volume == 1_550
  assert intent.target_amount is None


@pytest.mark.asyncio
async def test_cumulative_real_fills_reduce_the_next_target_gap_after_pending_clears():
  item = await strategy()
  state = ManagedEntryPlanState(
    filled_volume=2_000,
    filled_amount_cny=20_000,
    rule_filled_volumes={"manual": 2_000},
    rule_filled_amounts_cny={"manual": 20_000},
  )

  evaluation_context = _build_evaluation_context(
    input_snapshot(
      portfolio_state={
        "account": {"total_equity_cny": 100_000},
        "positions": {
          "600000.SH": {
            "total_volume": 2_000,
            "market_value_cny": 20_000,
          }
        },
      }
    ),
    item._require_config(),
    state,
  )

  assert evaluation_context.plan_filled_volume == 2_000
  assert evaluation_context.plan_filled_amount_cny == 20_000
  gap = EntryGapCalculator.calculate(
    item._require_config().target_policy,
    evaluation_context,
  )
  assert gap.remaining_amount_cny == 10_000


@pytest.mark.asyncio
async def test_strategy_rejects_future_market_observation_instead_of_using_it():
  item = await strategy()
  future = int((NOW + timedelta(minutes=1)).timestamp() * 1000)
  snapshot = input_snapshot(
    market_data={
      "ask_prices": [10],
      "data_quality": "OK",
      "daily_observations": [{"timestamp_ms": future, "close": 99}],
    }
  )

  output = await item.step(snapshot)

  assert output.trade_intents == []
  assert output.trace_payload["reason"] == "ENTRY_FUTURE_DATA_REJECTED"


@pytest.mark.asyncio
async def test_strategy_fails_closed_when_account_or_price_snapshot_is_missing():
  item = await strategy()
  output = await item.step(
    input_snapshot(
      market_data={}, portfolio_state={}, market_context={"data_quality": "OK"}
    )
  )

  assert output.trade_intents == []
  assert output.trace_payload["reason"] == "ENTRY_SNAPSHOT_INCOMPLETE"


@pytest.mark.asyncio
async def test_product_pause_blocks_only_new_entry_evaluation():
  parameters = plan_parameters()
  parameters[ENTRY_PLAN_ENABLED_KEY] = False
  item = await strategy(parameters)

  output = await item.step(input_snapshot())

  assert output.trade_intents == []
  assert output.runtime_state_patch is None
  assert output.trace_payload["reason"] == "ENTRY_PLAN_PAUSED"


@pytest.mark.asyncio
async def test_order_terminal_before_trade_keeps_pending_until_real_trade_report():
  item = await strategy()
  output = await item.step(input_snapshot())
  intent = output.trade_intents[0]
  item.apply_state_snapshot(output.runtime_state_patch.set)
  metadata = dict(intent.metadata)

  order_patch = await item.on_order(
    OrderStateEvent(
      order_id="order-1",
      status="FILLED",
      filled_volume=300,
      metadata=metadata,
      timestamp=NOW,
    )
  )
  assert order_patch is not None
  assert (
    order_patch.set[MANAGED_ENTRY_STATE_KEY]["pending_intent_id"] == intent.intent_id
  )

  trade_patch = await item.on_trade(
    TradeExecutionEvent(
      order_id="order-1",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10,
      volume=300,
      trade_time=NOW,
      metadata={**metadata, "trade_id": "trade-1"},
    )
  )
  assert trade_patch is not None
  assert trade_patch.set[MANAGED_ENTRY_STATE_KEY]["pending_intent_id"] == ""
  assert trade_patch.set[MANAGED_ENTRY_STATE_KEY]["phase"] == "ACCUMULATING"

  assert (
    await item.on_trade(
      TradeExecutionEvent(
        order_id="order-1",
        instrument_code="600000.SH",
        trade_type="BUY",
        price=10,
        volume=300,
        trade_time=NOW,
        metadata={**metadata, "trade_id": "trade-1"},
      )
    )
    is None
  )


@pytest.mark.asyncio
async def test_terminal_expected_fill_waits_for_all_execution_reports():
  item = await strategy()
  output = await item.step(input_snapshot())
  intent = output.trade_intents[0]
  item.apply_state_snapshot(output.runtime_state_patch.set)
  metadata = dict(intent.metadata)

  order_patch = await item.on_order(
    OrderStateEvent(
      order_id="order-multi-fill",
      status="FILLED",
      metadata={**metadata, "traded_volume": 400},
      timestamp=NOW,
    )
  )
  assert order_patch is not None
  terminal = order_patch.set[MANAGED_ENTRY_STATE_KEY]
  assert terminal["terminal_expected_filled_volume"] == 400
  assert terminal["pending_intent_id"] == intent.intent_id

  first = await item.on_trade(
    TradeExecutionEvent(
      order_id="order-multi-fill",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10,
      volume=100,
      trade_time=NOW,
      metadata={**metadata, "trade_id": "multi-fill-1"},
    )
  )
  assert first is not None
  first_state = first.set[MANAGED_ENTRY_STATE_KEY]
  assert first_state["pending_filled_volume"] == 100
  assert first_state["pending_intent_id"] == intent.intent_id
  assert first_state["terminal_expected_filled_volume"] == 400

  assert (
    await item.on_trade(
      TradeExecutionEvent(
        order_id="order-multi-fill",
        instrument_code="600000.SH",
        trade_type="BUY",
        price=10,
        volume=100,
        trade_time=NOW,
        metadata={**metadata, "trade_id": "multi-fill-1"},
      )
    )
    is None
  )
  waiting = await item.step(input_snapshot())
  assert waiting.trade_intents == []
  assert waiting.trace_payload["reason"] == "ENTRY_PENDING_EXISTS"

  second = await item.on_trade(
    TradeExecutionEvent(
      order_id="order-multi-fill",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10,
      volume=200,
      trade_time=NOW,
      metadata={**metadata, "trade_id": "multi-fill-2"},
    )
  )
  assert second is not None
  second_state = second.set[MANAGED_ENTRY_STATE_KEY]
  assert second_state["pending_filled_volume"] == 300
  assert second_state["pending_intent_id"] == intent.intent_id

  final = await item.on_trade(
    TradeExecutionEvent(
      order_id="order-multi-fill",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10,
      volume=100,
      trade_time=NOW,
      metadata={**metadata, "trade_id": "multi-fill-3"},
    )
  )
  assert final is not None
  settled = final.set[MANAGED_ENTRY_STATE_KEY]
  assert settled["pending_intent_id"] == ""
  assert settled["filled_volume"] == 400
  assert settled["pending_filled_volume"] == 0
  assert settled["terminal_expected_filled_volume"] is None

  assert (
    await item.on_trade(
      TradeExecutionEvent(
        order_id="order-multi-fill",
        instrument_code="600000.SH",
        trade_type="BUY",
        price=10,
        volume=100,
        trade_time=NOW,
        metadata={**metadata, "trade_id": "multi-fill-3"},
      )
    )
    is None
  )


@pytest.mark.asyncio
async def test_filled_zero_report_does_not_turn_first_late_execution_into_barrier():
  item = await strategy()
  output = await item.step(input_snapshot())
  intent = output.trade_intents[0]
  item.apply_state_snapshot(output.runtime_state_patch.set)
  metadata = dict(intent.metadata)

  terminal = await item.on_order(
    OrderStateEvent(
      order_id="order-zero-then-late-fills",
      status="FILLED",
      request={"volume": 300},
      filled_volume=0,
      metadata=metadata,
      timestamp=NOW,
    )
  )
  assert terminal is not None
  terminal_state = terminal.set[MANAGED_ENTRY_STATE_KEY]
  assert terminal_state["pending_intent_id"] == intent.intent_id
  assert terminal_state["terminal_expected_filled_volume"] == 300

  first = await item.on_trade(
    TradeExecutionEvent(
      order_id="order-zero-then-late-fills",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10,
      volume=100,
      trade_time=NOW,
      metadata={**metadata, "trade_id": "zero-then-late-1"},
    )
  )
  assert first is not None
  first_state = first.set[MANAGED_ENTRY_STATE_KEY]
  assert first_state["pending_intent_id"] == intent.intent_id
  assert first_state["pending_filled_volume"] == 100
  assert first_state["terminal_expected_filled_volume"] == 300

  final = await item.on_trade(
    TradeExecutionEvent(
      order_id="order-zero-then-late-fills",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10,
      volume=200,
      trade_time=NOW,
      metadata={**metadata, "trade_id": "zero-then-late-2"},
    )
  )
  assert final is not None
  final_state = final.set[MANAGED_ENTRY_STATE_KEY]
  assert final_state["pending_intent_id"] == ""
  assert final_state["filled_volume"] == 300


@pytest.mark.asyncio
async def test_cancel_terminal_before_late_fill_stays_cancelled_after_settlement():
  item = await strategy()
  output = await item.step(input_snapshot())
  intent = output.trade_intents[0]
  item.apply_state_snapshot(output.runtime_state_patch.set)
  state = ManagedEntryPlanState.from_dict(
    item.state.get(MANAGED_ENTRY_STATE_KEY)
  )
  state.request_terminal(EntryPlanStatus.CANCELLED, reason="USER_CANCELLED")
  item.apply_state_snapshot({MANAGED_ENTRY_STATE_KEY: state.to_dict()})
  metadata = dict(intent.metadata)

  order_patch = await item.on_order(
    OrderStateEvent(
      order_id="order-cancel-before-fill",
      status="CANCELLED",
      filled_volume=300,
      metadata=metadata,
      timestamp=NOW,
    )
  )
  assert order_patch is not None
  draining = order_patch.set[MANAGED_ENTRY_STATE_KEY]
  assert draining["phase"] == "DRAINING"
  assert draining["pending_intent_id"] == intent.intent_id

  trade_patch = await item.on_trade(
    TradeExecutionEvent(
      order_id="order-cancel-before-fill",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10,
      volume=300,
      trade_time=NOW,
      metadata={**metadata, "trade_id": "late-cancel-fill"},
    )
  )

  assert trade_patch is not None
  settled = trade_patch.set[MANAGED_ENTRY_STATE_KEY]
  assert settled["phase"] == "CANCELLED"
  assert settled["terminal_requested"] == "CANCELLED"
  assert settled["terminal_request_reason"] == "USER_CANCELLED"
  assert settled["pending_intent_id"] == ""
  assert settled["filled_volume"] == 300
  assert settled["filled_amount_cny"] == 3_000


@pytest.mark.asyncio
async def test_expiry_fill_before_terminal_stays_draining_then_expires():
  item = await strategy()
  output = await item.step(input_snapshot())
  intent = output.trade_intents[0]
  item.apply_state_snapshot(output.runtime_state_patch.set)
  state = ManagedEntryPlanState.from_dict(
    item.state.get(MANAGED_ENTRY_STATE_KEY)
  )
  state.request_terminal(EntryPlanStatus.EXPIRED, reason="ENTRY_PLAN_EXPIRED")
  item.apply_state_snapshot({MANAGED_ENTRY_STATE_KEY: state.to_dict()})
  metadata = dict(intent.metadata)

  trade_patch = await item.on_trade(
    TradeExecutionEvent(
      order_id="order-fill-before-expiry-terminal",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10,
      volume=300,
      trade_time=NOW,
      metadata={**metadata, "trade_id": "fill-before-expiry-terminal"},
    )
  )
  assert trade_patch is not None
  draining = trade_patch.set[MANAGED_ENTRY_STATE_KEY]
  assert draining["phase"] == "DRAINING"
  assert draining["pending_intent_id"] == intent.intent_id
  assert draining["filled_volume"] == 300

  order_patch = await item.on_order(
    OrderStateEvent(
      order_id="order-fill-before-expiry-terminal",
      status="FILLED",
      filled_volume=300,
      metadata=metadata,
      timestamp=NOW,
    )
  )

  assert order_patch is not None
  settled = order_patch.set[MANAGED_ENTRY_STATE_KEY]
  assert settled["phase"] == "EXPIRED"
  assert settled["terminal_requested"] == "EXPIRED"
  assert settled["terminal_request_reason"] == "ENTRY_PLAN_EXPIRED"
  assert settled["pending_intent_id"] == ""
  assert settled["filled_volume"] == 300
  assert settled["filled_amount_cny"] == 3_000


@pytest.mark.asyncio
async def test_cancel_zero_fill_reconcile_finishes_cancelled_without_rearming():
  item = await strategy()
  output = await item.step(input_snapshot())
  intent = output.trade_intents[0]
  item.apply_state_snapshot(output.runtime_state_patch.set)
  state = ManagedEntryPlanState.from_dict(
    item.state.get(MANAGED_ENTRY_STATE_KEY)
  )
  state.request_terminal(EntryPlanStatus.CANCELLED, reason="USER_CANCELLED")
  item.apply_state_snapshot({MANAGED_ENTRY_STATE_KEY: state.to_dict()})
  metadata = dict(intent.metadata)

  first = await item.on_order(
    OrderStateEvent(
      order_id="order-zero-fill",
      status="CANCELLED",
      filled_volume=0,
      metadata=metadata,
      timestamp=NOW,
    )
  )
  assert first is not None
  assert first.set[MANAGED_ENTRY_STATE_KEY]["phase"] == "DRAINING"
  assert (
    first.set[MANAGED_ENTRY_STATE_KEY]["terminal_expected_filled_volume"] is None
  )

  reconciled = await item.on_order(
    OrderStateEvent(
      order_id="order-zero-fill",
      status="RECONCILED_ZERO_FILL",
      metadata=metadata,
      timestamp=NOW,
    )
  )

  assert reconciled is not None
  settled = reconciled.set[MANAGED_ENTRY_STATE_KEY]
  assert settled["phase"] == "CANCELLED"
  assert settled["terminal_requested"] == "CANCELLED"
  assert settled["pending_intent_id"] == ""
  assert settled["filled_volume"] == 0
  assert settled["filled_amount_cny"] == 0


@pytest.mark.asyncio
async def test_late_fill_that_reaches_absolute_target_completes_terminal_request():
  item = await strategy(
    plan_parameters(mode="ADDITIONAL_VOLUME", target_value=300)
  )
  output = await item.step(input_snapshot())
  intent = output.trade_intents[0]
  item.apply_state_snapshot(output.runtime_state_patch.set)
  state = ManagedEntryPlanState.from_dict(
    item.state.get(MANAGED_ENTRY_STATE_KEY)
  )
  state.request_terminal(EntryPlanStatus.CANCELLED, reason="USER_CANCELLED")
  item.apply_state_snapshot({MANAGED_ENTRY_STATE_KEY: state.to_dict()})
  metadata = dict(intent.metadata)

  await item.on_order(
    OrderStateEvent(
      order_id="order-target-fill",
      status="FILLED",
      filled_volume=300,
      metadata=metadata,
      timestamp=NOW,
    )
  )
  trade_patch = await item.on_trade(
    TradeExecutionEvent(
      order_id="order-target-fill",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10,
      volume=300,
      trade_time=NOW,
      metadata={**metadata, "trade_id": "target-fill"},
    )
  )

  assert trade_patch is not None
  settled = trade_patch.set[MANAGED_ENTRY_STATE_KEY]
  assert settled["phase"] == "COMPLETED"
  assert settled["terminal_requested"] == "CANCELLED"
  assert settled["pending_intent_id"] == ""
  assert settled["filled_volume"] == 300
  item.apply_state_snapshot(trade_patch.set)

  followup = await item.step(input_snapshot())

  assert followup.trade_intents == []
  assert followup.trace_payload["reason"] == "ENTRY_PLAN_COMPLETED"


@pytest.mark.asyncio
async def test_unrelated_or_sell_trade_cannot_advance_entry_state():
  item = await strategy()

  assert (
    await item.on_trade(
      TradeExecutionEvent(
        order_id="order-x",
        instrument_code="600000.SH",
        trade_type="SELL",
        price=10,
        volume=100,
        trade_time=NOW,
        metadata={"entry_plan_id": "run-1", "trade_id": "sell-1"},
      )
    )
    is None
  )


def test_each_entry_stage_gets_an_independent_exit_protection_identity():
  base = {
    "plan_id": "base-plan",
    "source_type": "ENTRY_PLAN",
    "source_id": "base-plan",
    "run_id": "run-1",
    "metadata": {"purpose": "protect-fill"},
  }

  first = _exit_plan_template_for_stage(
    base, plan_id="run-1", stage_id="stage-1"
  )
  repeated = _exit_plan_template_for_stage(
    base, plan_id="run-1", stage_id="stage-1"
  )
  second = _exit_plan_template_for_stage(
    base, plan_id="run-1", stage_id="stage-2"
  )

  assert first == repeated
  assert first is not None and second is not None
  assert first["plan_id"] != second["plan_id"]
  assert first["source_id"] == "stage-1"
  assert first["metadata"]["entry_plan_id"] == "run-1"
  assert first["metadata"]["entry_stage_id"] == "stage-1"
  assert base["plan_id"] == "base-plan"
