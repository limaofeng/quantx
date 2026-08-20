from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.base import (
  OrderStateEvent,
  StrategyContext,
  StrategyOutput,
  StrategyRunMode,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_engine.replay_clock import ReplayClock
from quantx_engine.strategy_executor import (
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_infrastructure.core.utils import time_utils


class FakeStateManager:
  def __init__(self):
    self.records = []
    self.updates = []

  async def record_trade_intent(self, intent, status="PENDING"):
    self.records.append((intent.intent_id, status))

  async def update_trade_intent_status(self, intent_id, status, **updates):
    self.updates.append((intent_id, status, updates))

  async def restore_manual_trade_intent(self, intent_id):
    return getattr(self, "restored_intent", None)

  def get_account_quota(self):
    return {"total_asset": 100_000.0}


def test_backtest_approval_uses_replay_clock_for_ttl_and_quote_age():
  executor = StrategyExecutor()
  signal_at = time_utils.now().replace(year=2024, month=1, day=2, hour=10)
  context = StrategyContext(
    run_id="run-historical-approval-clock",
    mode=StrategyRunMode.BACKTEST,
    instruments=["600000.SH"],
    parameters={"execution_quote_max_age_seconds": 3.0},
    current_time=signal_at + timedelta(seconds=2),
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="historical-approval-clock",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    replay_clock=ReplayClock(context.current_time),
  )
  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH",
    timestamp=signal_at,
    price=10.0,
    ask_price=[10.0],
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="historical_clock",
    target_volume=100,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    approval_ttl_ms=15_000,
    expiry_policy={
      "type": "TTL_MS",
      "expire_at_ms": int((signal_at + timedelta(seconds=15)).timestamp() * 1000),
    },
  )

  assert executor._approval_failure(runtime, intent) is None

  runtime.replay_clock.advance_to(signal_at + timedelta(seconds=4))
  assert executor._approval_failure(runtime, intent)[0] == "APPROVAL_QUOTE_STALE"

  runtime.replay_clock.advance_to(signal_at + timedelta(seconds=15))
  assert executor._approval_failure(runtime, intent)[0] == "APPROVAL_TTL_EXPIRED"


def test_manual_approval_fails_closed_for_missing_or_stale_execution_quote():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-fresh-quote",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"execution_quote_max_age_seconds": 3.0},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="fresh-quote",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="fresh_quote",
    target_volume=100,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
  )

  assert executor._approval_failure(runtime, intent)[0] == "APPROVAL_QUOTE_MISSING"

  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH",
    timestamp=time_utils.now() - timedelta(seconds=4),
    price=10.0,
    ask_price=[10.0],
  )
  assert executor._approval_failure(runtime, intent)[0] == "APPROVAL_QUOTE_STALE"


def test_t_trade_approval_rechecks_single_amount_hard_cap():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-amount-cap",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "max_trade_amount": 12_000.0,
      "max_concurrent_batches": 3,
      "max_total_t_exposure_pct": 1.0,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="amount-cap",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = FakeStateManager()
  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH", price=121.0, ask_price=[121.0]
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="amount_cap",
    target_volume=100,
    limit_price_hint=119.8,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    metadata={"t_trade_role": "entry"},
  )

  failure = executor._t_trade_portfolio_approval_failure(runtime, intent)

  assert failure and failure[0] == "T_TRADE_SINGLE_AMOUNT_LIMIT"


@pytest.mark.asyncio
async def test_manual_intent_waits_for_approval_before_routing():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-approval",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"instrument_code": "600000.SH", "position_shares": 1000},
  )
  runtime = StrategyRuntime(
    run_id="run-approval",
    name="approval",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = FakeStateManager()
  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH",
    price=10.0,
    ask_price=[10.0],
  )
  executor.runs[runtime.run_id] = runtime
  executor._process_trade_intent = AsyncMock()
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="test",
    target_volume=100,
    limit_price_hint=10.0,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    approval_ttl_ms=30_000,
    max_price_deviation_bps=30,
  )

  await executor._process_strategy_output(runtime, StrategyOutput(trade_intents=[intent]))

  executor._process_trade_intent.assert_not_awaited()
  assert runtime.state_manager.records == [(intent.intent_id, "AWAITING_APPROVAL")]
  assert runtime.pending_approvals[intent.intent_id] is intent

  result = await executor.approve_trade_intent(runtime.run_id, intent.intent_id)

  assert result["success"] is True
  executor._process_trade_intent.assert_awaited_once_with(runtime, intent)
  assert intent.intent_id not in runtime.pending_approvals


@pytest.mark.asyncio
async def test_manual_intent_is_restored_after_executor_restart():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-restored-approval",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"instrument_code": "600000.SH", "position_shares": 1000},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="restored-approval",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="test_restore",
    target_volume=100,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    approval_ttl_ms=30_000,
  )
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "pending_entry_intent_id": intent.intent_id,
          "entry_order_status": "AWAITING_APPROVAL",
        }
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  runtime.state_manager.restored_intent = intent

  await executor._restore_pending_manual_approvals(runtime)

  assert runtime.pending_approvals == {intent.intent_id: intent}


@pytest.mark.asyncio
async def test_approved_t_entry_persists_routing_state_for_restart():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-approved-restart",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "max_concurrent_batches": 3,
      "max_total_t_exposure_pct": 0.1,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="approved-restart",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = FakeStateManager()
  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH", price=10.0, ask_price=[10.0]
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="restart_window",
    target_volume=100,
    limit_price_hint=10.0,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    approval_ttl_ms=30_000,
    metadata={
      "t_trade_role": "entry",
      "t_batch_id": "batch-restart",
      "instrument_code": "600000.SH",
    },
  )
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "pending_entry_intent_id": intent.intent_id,
          "entry_order_status": "AWAITING_APPROVAL",
          "requested_entry_volume": 100,
          "batch_id": "batch-restart",
          "current_signal": {"signal_price": 10.0},
        }
      }
    }
  )
  runtime.pending_approvals[intent.intent_id] = intent
  executor.runs[runtime.run_id] = runtime
  executor._process_trade_intent = AsyncMock()

  result = await executor.approve_trade_intent(runtime.run_id, intent.intent_id)

  assert result["success"] is True
  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert state["entry_order_status"] == "PENDING"
  assert state["status"] == "ENTRY_SUBMITTED"
  assert runtime.strategy.pending_manual_intent_ids() == []
  runtime.t_trade_entry_reservations.clear()
  executor._restore_t_trade_entry_reservations(runtime)
  assert runtime.t_trade_entry_reservations[intent.intent_id]["volume"] == 100


@pytest.mark.asyncio
async def test_t_trade_account_batch_limit_keeps_signal_pending():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-cap",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH", "000001.SZ"],
    parameters={
      "account_id": "account-1",
      "max_concurrent_batches": 1,
      "max_total_t_exposure_pct": 0.1,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="cap",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "000001.SZ": {
          "entry_filled_volume": 100,
          "exit_filled_volume": 0,
          "entry_avg_price": 10.0,
        }
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH", price=10.0, ask_price=[10.0]
  )
  executor.runs[runtime.run_id] = runtime
  executor._process_trade_intent = AsyncMock()
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="cap",
    target_volume=100,
    limit_price_hint=10.0,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    approval_ttl_ms=30_000,
    metadata={"t_trade_role": "entry", "instrument_code": "600000.SH"},
  )
  runtime.pending_approvals[intent.intent_id] = intent

  result = await executor.approve_trade_intent(runtime.run_id, intent.intent_id)

  assert result["success"] is False
  assert result["code"] == "T_TRADE_CONCURRENT_BATCH_LIMIT"
  assert intent.intent_id in runtime.pending_approvals
  executor._process_trade_intent.assert_not_awaited()


def test_partial_fill_reservation_counts_as_one_batch():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-partial-cap",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH", "000001.SZ"],
    parameters={
      "account_id": "account-1",
      "max_concurrent_batches": 2,
      "max_total_t_exposure_pct": 0.1,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="partial-cap",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "000001.SZ": {
          "batch_id": "batch-partial",
          "entry_filled_volume": 100,
          "exit_filled_volume": 0,
          "entry_avg_price": 10.0,
        }
      }
    }
  )
  runtime.t_trade_entry_reservations["intent-partial"] = {
    "batch_id": "batch-partial",
    "instrument_code": "000001.SZ",
    "volume": 100,
    "price": 10.0,
    "amount": 1000.0,
  }
  runtime.state_manager = FakeStateManager()
  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH", price=10.0, ask_price=[10.0]
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="next_batch",
    target_volume=100,
    limit_price_hint=10.0,
    metadata={"t_trade_role": "entry", "t_batch_id": "batch-next"},
  )

  assert executor._t_trade_portfolio_approval_failure(runtime, intent) is None


@pytest.mark.asyncio
async def test_filled_order_keeps_exposure_reserved_until_trade_detail_arrives():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-filled-before-trade",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH", "000001.SZ"],
    parameters={
      "account_id": "account-1",
      "max_concurrent_batches": 1,
      "max_total_t_exposure_pct": 0.1,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="filled-before-trade",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = FakeStateManager()
  intent_id = "intent-filled-before-trade"
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "batch_id": "batch-filled-before-trade",
          "pending_entry_intent_id": intent_id,
          "entry_order_status": "PENDING",
          "entry_filled_volume": 0,
          "exit_filled_volume": 0,
        }
      }
    }
  )
  runtime.t_trade_entry_reservations[intent_id] = {
    "batch_id": "batch-filled-before-trade",
    "instrument_code": "600000.SH",
    "requested_volume": 100,
    "volume": 100,
    "price": 10.0,
    "amount": 1000.0,
  }
  metadata = {
    "t_trade_role": "entry",
    "t_batch_id": "batch-filled-before-trade",
    "instrument_code": "600000.SH",
    "intent_id": intent_id,
  }

  await executor._notify_strategy_order(
    runtime,
    OrderStateEvent(
      order_id="order-1",
      status="FILLED",
      filled_volume=100,
      metadata=metadata,
    ),
  )

  assert runtime.t_trade_entry_reservations[intent_id]["amount"] == 1000.0
  next_intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="000001.SZ",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="next_batch",
    target_volume=100,
    limit_price_hint=10.0,
    metadata={"t_trade_role": "entry", "t_batch_id": "batch-next"},
  )
  failure = executor._t_trade_portfolio_approval_failure(runtime, next_intent)
  assert failure and failure[0] == "T_TRADE_CONCURRENT_BATCH_LIMIT"

  await executor._notify_strategy_trade(
    runtime,
    TradeExecutionEvent(
      order_id="order-1",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10.0,
      volume=100,
      metadata=metadata,
    ),
  )

  assert intent_id not in runtime.t_trade_entry_reservations
  assert (
    runtime.strategy.state["instrument_states"]["600000.SH"][
      "entry_filled_volume"
    ]
    == 100
  )
