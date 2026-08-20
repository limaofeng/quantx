from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.base import (
  OrderStateEvent,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
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
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_infrastructure.core.utils import time_utils


class FakeStateManager:
  def __init__(self):
    self.records = []
    self.updates = []
    self.durable_snapshot = None
    self.custom = {}

  async def record_trade_intent(self, intent, status="PENDING"):
    self.records.append((intent.intent_id, status))

  async def update_trade_intent_status(self, intent_id, status, **updates):
    self.updates.append((intent_id, status, updates))

  async def restore_manual_trade_intent(self, intent_id):
    return getattr(self, "restored_intent", None)

  async def get_trade_intent_snapshot(self, intent_id):
    return self.durable_snapshot

  def set_custom(self, key, value):
    self.custom[key] = value

  def update_custom_state(self, updates):
    self.custom.update(updates)

  def get_account_quota(self):
    return {"total_asset": 100_000.0}


def make_durable_entry_snapshot(
  intent_id,
  *,
  status,
  order_id=None,
  executed_volume=0,
  executed_price=0.0,
  metadata=None,
):
  return {
    "id": intent_id,
    "instrument_code": "600000.SH",
    "status": status,
    "order_id": order_id,
    "executed_volume": executed_volume,
    "executed_price": executed_price,
    "executed_time": datetime(2026, 7, 13, 10, 0).isoformat(),
    "metadata": dict(metadata or {}),
  }


def make_t_entry_metadata(strategy, *, batch_id, plan_id):
  policy = strategy._exit_policy_snapshot()
  template = strategy.build_exit_plan_template(
    instrument_code="600000.SH",
    batch_id=batch_id,
    plan_id=plan_id,
    policy=policy,
  )
  return {
    "t_trade_role": "entry",
    "instrument_code": "600000.SH",
    "t_batch_id": batch_id,
    "exit_plan_id": plan_id,
    "exit_plan_template": template.to_dict(),
  }


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


@pytest.mark.asyncio
async def test_manual_approval_fails_closed_while_durable_barrier_is_active():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-durable-approval-barrier",
    mode=StrategyRunMode.LIVE,
    instruments=["600000.SH"],
    parameters={},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="durable-approval-barrier",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    status=ExecutionStatus.RUNNING,
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="durable_barrier",
    target_volume=100,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
  )
  runtime.pending_approvals[intent.intent_id] = intent
  runtime.durable_event_barrier_key = "trade:pending-report"
  executor.runs[runtime.run_id] = runtime

  result = await executor.approve_trade_intent(runtime.run_id, intent.intent_id)

  assert result["success"] is False
  assert result["code"] == "DURABLE_RECONCILIATION_REQUIRED"
  assert intent.intent_id in runtime.pending_approvals


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


@pytest.mark.parametrize("role", ["entry", "exit"])
def test_t_trade_approval_fails_closed_while_any_role_needs_reconciliation(role):
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-reconciliation-gate",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH", "000001.SZ"],
    parameters={"max_concurrent_batches": 3, "max_total_t_exposure_pct": 1.0},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="reconciliation-gate",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          f"pending_{role}_intent_id": "intent-needs-reconcile",
          f"{role}_order_status": "RECONCILE_REQUIRED",
        }
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="000001.SZ",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="blocked_by_reconciliation",
    target_volume=100,
    limit_price_hint=10.0,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    metadata={"t_trade_role": "entry"},
  )

  failure = executor._t_trade_portfolio_approval_failure(runtime, intent)

  assert failure and failure[0] == "T_TRADE_RECONCILIATION_REQUIRED"


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
  runtime.status = ExecutionStatus.RUNNING
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
async def test_restore_converges_expired_durable_intent_and_clears_snapshot_gate():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-expired-approval",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"instrument_code": "600000.SH", "position_shares": 1000},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="expired-approval",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  intent_id = "intent-expired-before-restart"
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "pending_entry_intent_id": intent_id,
          "entry_order_status": "AWAITING_APPROVAL",
          "status": "AWAITING_APPROVAL",
          "batch_id": "batch-expired-before-restart",
        }
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  runtime.state_manager.durable_snapshot = make_durable_entry_snapshot(
    intent_id,
    status="EXPIRED",
  )

  await executor._restore_pending_manual_approvals(runtime)

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert runtime.pending_approvals == {}
  assert state["pending_entry_intent_id"] == ""
  assert state["entry_order_status"] == "EXPIRED"
  assert state["status"] == "OBSERVING"
  assert state["batch_id"] == ""


@pytest.mark.asyncio
async def test_restore_converges_submitted_durable_intent_without_duplicate_entry():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-submitted-approval",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"instrument_code": "600000.SH", "position_shares": 1000},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="submitted-approval",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  intent_id = "intent-submitted-before-restart"
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "pending_entry_intent_id": intent_id,
          "entry_order_status": "AWAITING_APPROVAL",
          "status": "AWAITING_APPROVAL",
        }
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  runtime.state_manager.durable_snapshot = make_durable_entry_snapshot(
    intent_id,
    status="SUBMITTED",
    order_id="broker-order-submitted",
  )

  await executor._restore_pending_manual_approvals(runtime)

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert runtime.pending_approvals == {}
  assert state["pending_entry_intent_id"] == intent_id
  assert state["entry_order_status"] == "SUBMITTED"
  assert state["status"] == "ENTRY_SUBMITTED"


@pytest.mark.asyncio
async def test_restore_requires_reconciliation_for_approved_without_order_id():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-orphaned-approved",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"instrument_code": "600000.SH", "position_shares": 1000},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="orphaned-approved",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  intent_id = "intent-approved-without-order"
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "pending_entry_intent_id": intent_id,
          "entry_order_status": "AWAITING_APPROVAL",
          "status": "AWAITING_APPROVAL",
          "batch_id": "batch-orphaned-approved",
        }
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  runtime.state_manager.durable_snapshot = make_durable_entry_snapshot(
    intent_id,
    status="APPROVED",
  )

  await executor._restore_pending_manual_approvals(runtime)

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert state["pending_entry_intent_id"] == intent_id
  assert state["entry_order_status"] == "RECONCILE_REQUIRED"
  assert state["status"] == "RECONCILE_REQUIRED"
  assert state["batch_id"] == "batch-orphaned-approved"
  assert state["reconciliation_reason"] == (
    "APPROVED_WITHOUT_DURABLE_ORDER_CORRELATION"
  )
  assert runtime.state_manager.updates == []


@pytest.mark.asyncio
async def test_restore_filled_intent_waits_for_idempotent_inbox_replay():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-filled-recovery",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"instrument_code": "600000.SH", "position_shares": 1000},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="filled-recovery",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  intent_id = "intent-filled-before-snapshot"
  batch_id = "batch-filled-before-snapshot"
  plan_id = "t-exit-filled-before-snapshot"
  metadata = make_t_entry_metadata(
    runtime.strategy,
    batch_id=batch_id,
    plan_id=plan_id,
  )
  policy = runtime.strategy._exit_policy_snapshot()
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "pending_entry_intent_id": intent_id,
          "entry_order_status": "AWAITING_APPROVAL",
          "status": "AWAITING_APPROVAL",
          "batch_id": batch_id,
          "exit_plan_id": plan_id,
          "requested_entry_volume": 200,
          "entry_filled_volume": 100,
          "entry_avg_price": 10.0,
          "exit_policy_snapshot": policy,
        }
      }
    }
  )
  runtime.exit_plan_book.register_entry_fill(
    metadata["exit_plan_template"],
    volume=100,
    price=10.0,
    trade_time=datetime(2026, 7, 13, 9, 59),
  )
  runtime.state_manager = FakeStateManager()
  runtime.state_manager.durable_snapshot = make_durable_entry_snapshot(
    intent_id,
    status="FILLED",
    order_id="broker-order-filled",
    executed_volume=200,
    executed_price=10.5,
    metadata=metadata,
  )

  await executor._restore_pending_manual_approvals(runtime)

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  plan = runtime.exit_plan_book.plans[plan_id]
  assert state["pending_entry_intent_id"] == intent_id
  assert state["entry_order_status"] == "RECONCILE_REQUIRED"
  assert state["status"] == "RECONCILE_REQUIRED"
  assert state["reconciliation_reason"].startswith(
    "DURABLE_FILL_AWAITS_IDEMPOTENT_INBOX_REPLAY"
  )
  assert state["entry_filled_volume"] == 100
  assert state["entry_avg_price"] == pytest.approx(10.0)
  assert plan.entry_filled_volume == 100
  assert plan.entry_avg_price == pytest.approx(10.0)

  await executor._notify_strategy_trade(
    runtime,
    TradeExecutionEvent(
      order_id="broker-order-filled",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=11.0,
      volume=100,
      trade_time=datetime(2026, 7, 13, 10, 0),
      metadata=metadata,
    ),
  )
  await executor._notify_strategy_order(
    runtime,
    OrderStateEvent(
      order_id="broker-order-filled",
      status="FILLED",
      filled_volume=200,
      metadata={**metadata, "intent_id": intent_id},
    ),
  )

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert state["pending_entry_intent_id"] == ""
  assert state["entry_order_status"] == "FILLED"
  assert state["reconciliation_reason"] == ""
  assert state["entry_filled_volume"] == 200
  assert state["entry_avg_price"] == pytest.approx(10.5)
  assert runtime.exit_plan_book.plans[plan_id].entry_filled_volume == 200


@pytest.mark.asyncio
async def test_restore_cancelled_partial_fill_keeps_open_lot_and_blocks_new_entry():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-cancelled-partial-recovery",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"instrument_code": "600000.SH", "position_shares": 1000},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="cancelled-partial-recovery",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  await runtime.strategy.initialize()
  intent_id = "intent-cancelled-after-partial-fill"
  batch_id = "batch-cancelled-after-partial-fill"
  plan_id = "t-exit-cancelled-after-partial-fill"
  metadata = make_t_entry_metadata(
    runtime.strategy,
    batch_id=batch_id,
    plan_id=plan_id,
  )
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "pending_entry_intent_id": intent_id,
          "entry_order_status": "AWAITING_APPROVAL",
          "status": "AWAITING_APPROVAL",
          "batch_id": batch_id,
          "exit_plan_id": plan_id,
          "requested_entry_volume": 200,
          "entry_filled_volume": 0,
          "entry_avg_price": 0.0,
          "exit_policy_snapshot": runtime.strategy._exit_policy_snapshot(),
        }
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  runtime.state_manager.durable_snapshot = make_durable_entry_snapshot(
    intent_id,
    status="CANCELLED",
    order_id="broker-order-cancelled",
    executed_volume=100,
    executed_price=10.0,
    metadata=metadata,
  )

  await executor._restore_pending_manual_approvals(runtime)

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert state["pending_entry_intent_id"] == intent_id
  assert state["entry_order_status"] == "RECONCILE_REQUIRED"
  assert state["status"] == "RECONCILE_REQUIRED"
  assert state["entry_filled_volume"] == 0
  start = datetime(2026, 7, 13, 9, 30)
  for seconds, price in ((0, 100.0), (60, 99.0), (80, 99.3)):
    tick_at = start + timedelta(seconds=seconds)
    output = await runtime.strategy.step(
      StrategyInput(
        run_id=runtime.run_id,
        strategy_id="1",
        timestamp=tick_at,
        cadence=StrategyCadence.TICK,
        instrument_code="600000.SH",
        event=SimpleNamespace(
          last_price=price,
          bid_price=[price - 0.01],
          ask_price=[price],
          amount=995_000.0,
          pvolume=10_000.0,
        ),
      )
    )
    assert output.trade_intents == []

  await executor._notify_strategy_trade(
    runtime,
    TradeExecutionEvent(
      order_id="broker-order-cancelled",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10.0,
      volume=100,
      trade_time=datetime(2026, 7, 13, 10, 0),
      metadata=metadata,
    ),
  )
  await executor._notify_strategy_order(
    runtime,
    OrderStateEvent(
      order_id="broker-order-cancelled",
      status="CANCELLED",
      filled_volume=100,
      metadata={**metadata, "intent_id": intent_id},
    ),
  )

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert state["pending_entry_intent_id"] == ""
  assert state["entry_order_status"] == "CANCELLED"
  assert state["status"] == "MONITORING"
  assert state["entry_filled_volume"] == 100


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
    status=ExecutionStatus.RUNNING,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = FakeStateManager()
  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH",
    timestamp=time_utils.now(),
    price=10.0,
    ask_price=[10.0],
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
          "current_signal": {"triggered": True, "signal_price": 10.0},
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
    status=ExecutionStatus.RUNNING,
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
    instrument_code="600000.SH",
    timestamp=time_utils.now(),
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
    reason="cap",
    target_volume=100,
    limit_price_hint=10.0,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    approval_ttl_ms=30_000,
    metadata={"t_trade_role": "entry", "instrument_code": "600000.SH"},
  )
  runtime.pending_approvals[intent.intent_id] = intent
  instrument_states = dict(runtime.strategy.state.get("instrument_states") or {})
  instrument_states["600000.SH"] = {
    "pending_entry_intent_id": intent.intent_id,
    "entry_order_status": "AWAITING_APPROVAL",
    "current_signal": {"triggered": True, "signal_price": 10.0},
  }
  runtime.strategy.state.set("instrument_states", instrument_states)

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
  assert failure and failure[0] == "T_TRADE_RECONCILIATION_REQUIRED"

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
