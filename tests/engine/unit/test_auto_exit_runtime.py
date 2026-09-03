from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from quantx_contracts import ExecutionOwnerRef, ExecutionOwnerType
from quantx_domain.brokers.base import PriceType
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.ashare_managed_exit_plan import (
  MANAGED_EXIT_RUNTIME_KEY,
  AshareManagedExitPlanStrategy,
)
from quantx_domain.strategies.base import (
  OrderStateEvent,
  StrategyContext,
  StrategyRunMode,
  TradeExecutionEvent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
  TradeIntentPriority,
)
from quantx_domain.trading.exit_plan import (
  EXIT_PLAN_BOOK_STATE_KEY,
  ExitEvaluationContext,
  ExitExecutionPolicy,
  ExitPlan,
  ExitPlanStatus,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
  ExitT1Policy,
)
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.risk_checker import (
  OrderRiskDecision,
  TradingRiskChecker,
)
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_infrastructure.services.auto_exit_plan_service import (
  AutoExitPlanService,
)
from quantx_infrastructure.services.exit_plan_authorization_service import (
  AutoExitAuthorizationGuard,
  ExitPlanAuthorizationValidation,
)


class FakeStateManager:
  def __init__(self):
    self.custom = {}
    self.records = []

  def set_custom(self, key, value):
    self.custom[key] = value

  async def record_trade_intent(self, intent, status="PENDING"):
    self.records.append((intent, status))

  async def record_trade_intents(self, records):
    self.records.extend(records)


class ExecutionStateManager(FakeStateManager):
  def __init__(self):
    super().__init__()
    self.enable_reserve = False
    self.update_trade_intent_status = AsyncMock()
    self.release_order_resources = MagicMock()

  @staticmethod
  def get_account_quota():
    return {"available_cash": 100_000.0, "total_asset": 100_000.0}

  @staticmethod
  def get_position(_instrument_code):
    return {
      "long_volume": 1_000,
      "available_volume": 1_000,
      "frozen_volume": 0,
      "today_buy_volume": 0,
    }

  @classmethod
  def get_all_positions(cls):
    return {"600000.SH": cls.get_position("600000.SH")}

  @staticmethod
  def get_bucket_ledger_snapshot():
    return {}

  @staticmethod
  def reserve_bucket_order(_reservation_key, _request):
    return True


def make_runtime():
  context = StrategyContext(
    run_id="run-auto-exit",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="auto-exit",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = FakeStateManager()
  runtime.status = ExecutionStatus.RUNNING
  return runtime


def make_executor():
  executor = StrategyExecutor()
  executor._persist_runtime_exit_plan_states = AsyncMock()
  return executor


def test_managed_exit_strategy_is_rejected_for_daily_paper_or_live_runtime():
  executor = StrategyExecutor()

  for mode in (StrategyRunMode.PAPER, StrategyRunMode.LIVE):
    context = StrategyContext(
      run_id=f"managed-exit-{mode.value.lower()}",
      mode=mode,
      instruments=["600000.SH"],
      parameters={},
    )
    with pytest.raises(ValueError, match="仅允许用于独立回放或回测"):
      executor.create(
        run_id=context.run_id,
        strategy_id=2,
        strategy_class=AshareManagedExitPlanStrategy,
        context=context,
      )


def test_managed_exit_strategy_remains_available_for_backtest_replay():
  context = StrategyContext(
    run_id="managed-exit-backtest",
    mode=StrategyRunMode.BACKTEST,
    instruments=["600000.SH"],
    parameters={},
  )

  runtime = StrategyExecutor().create(
    run_id=context.run_id,
    strategy_id=2,
    strategy_class=AshareManagedExitPlanStrategy,
    context=context,
  )

  assert runtime.strategy_class is AshareManagedExitPlanStrategy
  assert runtime.context.mode == StrategyRunMode.BACKTEST


def make_template(*, price_type: str = "LIMIT"):
  return ExitPlanTemplate(
    plan_id="exit-plan-1",
    source_type="T_TRADE_BATCH",
    source_id="t-batch-1",
    account_id="account-1",
    instrument_code="600000.SH",
    bucket="swing",
    run_id="run-auto-exit",
    strategy_id="1",
    rules=[
      ExitRuleSpec(
        rule_id="hard-stop",
        strategy=ExitRuleType.HARD_STOP,
        priority=1000,
        parameters={"stop_loss_pct": -1.0},
      )
    ],
    t1_policy=ExitT1Policy.WAIT_UNTIL_SELLABLE,
    execution=ExitExecutionPolicy(
      price_type=price_type,
      protected_limit=price_type == "LIMIT",
    ),
    auto_exit_authorized=True,
  )


async def prepare_pending_runtime_exit(*, price_type: str = "LIMIT"):
  executor = make_executor()
  runtime = make_runtime()
  runtime.state_manager = ExecutionStateManager()
  runtime.context.current_time = datetime(2026, 8, 29, 10, 0)
  market_data = MarketDataSnapshot(
    instrument_code="600000.SH",
    timestamp=runtime.context.current_time,
    price=9.8,
    close=10.0,
    price_tick=0.01,
    limit_up=11.0,
    limit_down=9.0,
    bid_price=[9.79],
    ask_price=[9.8],
    is_trading=True,
    suspended=False,
    source="QMT_WHOLE_QUOTE",
  )
  runtime.latest_market_data["600000.SH"] = market_data
  runtime.data_adapter = SimpleNamespace(
    subscription_manager=SimpleNamespace(
      hub=SimpleNamespace(is_ready=True),
    )
  )
  runtime.broker = SimpleNamespace(
    commission_rate=0.0003,
    min_commission=5.0,
    orders={},
    place_order=AsyncMock(),
  )
  plan = runtime.exit_plan_book.register_entry_fill(
    make_template(price_type=price_type),
    volume=100,
    price=10.0,
  )
  executor._process_trade_intent = AsyncMock()
  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="600000.SH",
    timestamp=runtime.context.current_time,
    market_data=market_data,
  )
  intent = executor._process_trade_intent.await_args.args[1]
  executor._process_trade_intent.reset_mock()
  return executor, runtime, plan, intent, market_data


def make_managed_exit_runtime():
  context = StrategyContext(
    run_id="run-managed-exit",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "_managed_plan_binding": {
        "plan_id": "manual-plan-1",
        "plan_kind": "EXIT",
        "config_version": 1,
      },
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="managed-exit",
    strategy_id=2,
    strategy_class=AshareManagedExitPlanStrategy,
    context=context,
  )
  runtime.strategy = AshareManagedExitPlanStrategy(context)
  runtime.state_manager = FakeStateManager()
  runtime.status = ExecutionStatus.RUNNING
  return runtime


@pytest.mark.asyncio
async def test_dedicated_auto_exit_crash_gap_is_restored_for_one_route(monkeypatch):
  runtime = make_managed_exit_runtime()
  executor = StrategyExecutor()
  recovery = {
    "action": "ROUTE_AUTO",
    "durable_status": "PENDING",
    "plan_id": "manual-plan-1",
    "intent_id": "exit-intent-1",
    "strategy_id": "2",
    "strategy_run_id": runtime.run_id,
    "instrument_code": "600000.SH",
    "bucket": "manual",
    "reason": "AUTO_EXIT_STOP",
    "priority": "HIGH",
    "target_volume": 100,
    "limit_price_hint": 9.8,
    "metadata": {
      "owner_type": "EXIT_PLAN",
      "owner_id": "manual-plan-1",
      "exit_plan_id": "manual-plan-1",
    },
  }
  monkeypatch.setattr(
    AutoExitPlanService,
    "load_managed_pending_exit_intent",
    AsyncMock(return_value=recovery),
  )

  await executor._restore_runtime_exit_plan_intents(runtime)

  restored = runtime.exit_plan_recovery_intents["exit-intent-1"]
  assert restored.execution_mode == TradeIntentExecutionMode.AUTO
  assert restored.metadata["exit_plan_recovery_durable_status"] == "PENDING"

  executor._process_trade_intent = AsyncMock()
  market_data = MarketDataSnapshot(
    instrument_code="600000.SH",
    price=9.8,
    bid_price=[9.79],
    ask_price=[9.8],
  )
  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 29, 10, 0),
    market_data=market_data,
  )
  executor._process_trade_intent.assert_awaited_once_with(runtime, restored)
  assert runtime.exit_plan_recovery_intents == {}

  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 29, 10, 0, 1),
    market_data=market_data,
  )
  executor._process_trade_intent.assert_awaited_once()


@pytest.mark.asyncio
async def test_dedicated_auto_exit_recovery_retries_after_route_failure(monkeypatch):
  runtime = make_managed_exit_runtime()
  executor = StrategyExecutor()
  recovery = {
    "action": "ROUTE_AUTO",
    "durable_status": "PENDING",
    "plan_id": "manual-plan-1",
    "intent_id": "exit-intent-1",
    "strategy_id": "2",
    "strategy_run_id": runtime.run_id,
    "instrument_code": "600000.SH",
    "bucket": "manual",
    "reason": "AUTO_EXIT_STOP",
    "priority": "HIGH",
    "target_volume": 100,
    "limit_price_hint": 9.8,
    "metadata": {
      "owner_type": "EXIT_PLAN",
      "owner_id": "manual-plan-1",
      "exit_plan_id": "manual-plan-1",
    },
  }
  monkeypatch.setattr(
    AutoExitPlanService,
    "load_managed_pending_exit_intent",
    AsyncMock(return_value=recovery),
  )
  await executor._restore_runtime_exit_plan_intents(runtime)
  restored = runtime.exit_plan_recovery_intents["exit-intent-1"]
  executor._process_trade_intent = AsyncMock(
    side_effect=[RuntimeError("temporary route failure"), None]
  )
  market_data = MarketDataSnapshot(
    instrument_code="600000.SH",
    price=9.8,
    bid_price=[9.79],
    ask_price=[9.8],
  )

  with pytest.raises(RuntimeError, match="temporary route failure"):
    await executor._process_auto_exit_plans(
      runtime,
      instrument_code="600000.SH",
      timestamp=datetime(2026, 8, 29, 10, 0),
      market_data=market_data,
    )
  assert runtime.exit_plan_recovery_intents == {"exit-intent-1": restored}

  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 29, 10, 0, 1),
    market_data=market_data,
  )
  assert executor._process_trade_intent.await_count == 2
  assert runtime.exit_plan_recovery_intents == {}


@pytest.mark.asyncio
async def test_replayed_exit_confirmation_returns_existing_order_without_reroute(
  monkeypatch,
):
  runtime = make_runtime()
  runtime.context.parameters["account_id"] = "account-1"
  runtime.status = ExecutionStatus.PAUSED
  executor = StrategyExecutor()
  executor.runs[runtime.run_id] = runtime
  executor._notify_strategy_order = AsyncMock()
  executor._process_trade_intent = AsyncMock()
  queued_lookup = AsyncMock(
    return_value={
      "client_order_id": "client-order-1",
      "status": "QUEUED",
      "volume": 100,
    }
  )
  monkeypatch.setattr(
    AutoExitPlanService,
    "load_existing_exit_order_projection",
    queued_lookup,
  )

  result = await executor.approve_trade_intent(
    runtime.run_id,
    "exit-intent-1",
    expected_exit_plan_id="exit-plan-1",
  )

  assert result == {
    "success": True,
    "code": "EXIT_INTENT_ALREADY_QUEUED",
    "message": "退出卖单已经进入持久订单队列，本次重放未重复下单",
    "plan_id": "exit-plan-1",
    "intent_id": "exit-intent-1",
    "client_order_id": "client-order-1",
    "order_id": "client-order-1",
    "status": "QUEUED",
    "volume": 100,
  }
  queued_lookup.assert_awaited_once_with(
    strategy_run_id=runtime.run_id,
    expected_plan_id="exit-plan-1",
    intent_id="exit-intent-1",
    account_id="account-1",
  )
  executor._notify_strategy_order.assert_not_awaited()
  executor._process_trade_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_replayed_exit_confirmation_fails_closed_on_durable_binding_error(
  monkeypatch,
):
  runtime = make_runtime()
  runtime.context.parameters["account_id"] = "account-1"
  runtime.status = ExecutionStatus.PAUSED
  executor = StrategyExecutor()
  executor.runs[runtime.run_id] = runtime
  executor._notify_strategy_order = AsyncMock()
  executor._process_trade_intent = AsyncMock()
  monkeypatch.setattr(
    AutoExitPlanService,
    "load_existing_exit_order_projection",
    AsyncMock(side_effect=RuntimeError("cross-plan order binding")),
  )

  result = await executor.approve_trade_intent(
    runtime.run_id,
    "exit-intent-1",
    expected_exit_plan_id="exit-plan-1",
  )

  assert result == {
    "success": False,
    "code": "EXIT_INTENT_REPLAY_RECONCILIATION_REQUIRED",
    "message": "退出意图与既有订单的持久化绑定需要人工核对",
  }
  executor._notify_strategy_order.assert_not_awaited()
  executor._process_trade_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_dedicated_canonical_reload_restores_plan_version_and_strategy_state(
  monkeypatch,
):
  runtime = make_managed_exit_runtime()
  plan = ExitPlanTemplate(
    plan_id="manual-plan-1",
    source_type="MANUAL_POSITION",
    source_id="manual-plan-1",
    account_id="account-1",
    instrument_code="600000.SH",
    bucket="manual",
    run_id=runtime.run_id,
    rules=[
      ExitRuleSpec(
        rule_id="stop",
        strategy=ExitRuleType.STOP_PRICE,
        parameters={"stop_price": 9.8},
      )
    ],
  )
  state = runtime.exit_plan_book.register_entry_fill(
    plan,
    volume=100,
    price=10.0,
  ).to_dict()
  monkeypatch.setattr(
    AutoExitPlanService,
    "load_managed_runtime_plan",
    AsyncMock(return_value=(state, 7)),
  )

  await StrategyExecutor()._load_runtime_exit_plan_book(runtime)

  assert runtime.exit_plan_state_versions == {"manual-plan-1": 7}
  assert runtime.exit_plan_book.plans == {}
  assert runtime.strategy.state.get(MANAGED_EXIT_RUNTIME_KEY)["template"][
    "plan_id"
  ] == "manual-plan-1"


@pytest.mark.asyncio
async def test_dedicated_trade_marker_reloads_canonical_before_callback(
  monkeypatch,
):
  runtime = make_managed_exit_runtime()
  template = ExitPlanTemplate(
    plan_id="manual-plan-1",
    source_type="MANUAL_POSITION",
    source_id="manual-plan-1",
    account_id="account-1",
    instrument_code="600000.SH",
    bucket="manual",
    run_id=runtime.run_id,
    rules=[
      ExitRuleSpec(
        rule_id="stop",
        strategy=ExitRuleType.STOP_PRICE,
        parameters={"stop_price": 9.8},
      )
    ],
  )
  canonical = ExitPlan(template=template)
  canonical.register_entry_fill(volume=100, price=10.0)
  canonical.exited_volume = 40
  canonical.exit_avg_price = 9.8
  canonical.status = ExitPlanStatus.PARTIALLY_EXITED
  canonical_state = canonical.to_dict()
  runtime.strategy.apply_state_snapshot(
    {MANAGED_EXIT_RUNTIME_KEY: canonical_state}
  )
  runtime.exit_plan_state_versions = {canonical.plan_id: 2}
  marker = AsyncMock(return_value=True)
  load = AsyncMock(return_value=(canonical_state, 2))
  persist = AsyncMock()
  monkeypatch.setattr(AutoExitPlanService, "strategy_plan_event_applied", marker)
  monkeypatch.setattr(AutoExitPlanService, "load_managed_runtime_plan", load)
  monkeypatch.setattr(
    AutoExitPlanService,
    "persist_managed_runtime_transition",
    persist,
  )
  event = TradeExecutionEvent(
    order_id="order-1",
    instrument_code="600000.SH",
    trade_type="SELL",
    price=9.8,
    volume=40,
    execution_ref=ExecutionOwnerRef(
      ExecutionOwnerType.EXIT_PLAN,
      canonical.plan_id,
    ),
    metadata={
      "exit_rule_id": "stop",
      "intent_id": "intent-1",
      "runtime_event_key": "trade:account-1:trade-1",
    },
  )

  patch = await StrategyExecutor()._notify_strategy_trade(
    runtime,
    event,
    raise_on_error=True,
  )

  restored = ExitPlan.from_dict(
    runtime.strategy.state.get(MANAGED_EXIT_RUNTIME_KEY)
  )
  assert restored.exited_volume == 40
  assert runtime.exit_plan_state_versions == {canonical.plan_id: 2}
  assert patch.set[MANAGED_EXIT_RUNTIME_KEY]["exited_volume"] == 40
  marker.assert_awaited_once_with(
    plan_id=canonical.plan_id,
    business_key=StrategyExecutor._exit_plan_runtime_event_business_key(
      canonical.plan_id,
      event.metadata,
    ),
  )
  load.assert_awaited_once()
  persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_engine_registers_filled_entry_and_routes_generic_exit_intent():
  executor = make_executor()
  runtime = make_runtime()
  executor.runs[runtime.run_id] = runtime
  executor._process_trade_intent = AsyncMock()
  template = make_template()

  await executor._notify_strategy_trade(
    runtime,
    TradeExecutionEvent(
      order_id="entry-order",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10.0,
      volume=100,
      trade_time=datetime(2026, 7, 30, 9, 35),
      metadata={"exit_plan_template": template.to_dict()},
    ),
  )

  plan = runtime.exit_plan_book.plans[template.plan_id]
  assert plan.status == ExitPlanStatus.ACTIVE
  assert plan.entry_filled_volume == 100
  assert EXIT_PLAN_BOOK_STATE_KEY not in runtime.state_manager.custom

  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 7, 30, 10, 0),
    market_data=MarketDataSnapshot(
      instrument_code="600000.SH",
      price=9.8,
      bid_price=[9.79],
      ask_price=[9.8],
    ),
  )

  routed = executor._process_trade_intent.await_args.args[1]
  assert routed.direction == TradeIntentDirection.SELL
  assert routed.target_volume == 100
  assert routed.execution_ref == ExecutionOwnerRef(
    ExecutionOwnerType.EXIT_PLAN,
    template.plan_id,
  )
  assert "exit_plan_id" not in routed.metadata
  assert "owner_type" not in routed.metadata
  assert "owner_id" not in routed.metadata
  assert "strategy_run_id" not in routed.metadata
  assert routed.metadata["exit_rule_type"] == ExitRuleType.HARD_STOP.value
  assert routed.metadata["allow_t1_substitution"] is False
  assert routed.max_price_deviation_bps == 30.0
  assert plan.status == ExitPlanStatus.EXIT_PENDING


@pytest.mark.asyncio
async def test_strategy_live_auto_exit_carries_complete_exact_authorization_audit(
  monkeypatch,
):
  executor = make_executor()
  runtime = make_runtime()
  runtime.context.mode = StrategyRunMode.LIVE
  runtime.exit_plan_book.register_entry_fill(
    make_template(),
    volume=100,
    price=10.0,
  )
  executor._process_trade_intent = AsyncMock()
  authorized_at = datetime(2026, 8, 29, 9, 30)
  expires_at = datetime(2026, 9, 5, 9, 30)
  monkeypatch.setattr(
    AutoExitAuthorizationGuard,
    "validate_or_invalidate",
    AsyncMock(
      return_value=ExitPlanAuthorizationValidation(
        valid=True,
        code="AUTHORIZED",
        message="精确授权有效",
        fingerprint="f" * 64,
        authorization_user_id="user-1",
        config_version=1,
        challenge_id="challenge-1",
        device_session_id="session-1",
        authorized_at=authorized_at,
        authorization_expires_at=expires_at,
      )
    ),
  )

  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 29, 10, 0),
    market_data=MarketDataSnapshot(
      instrument_code="600000.SH",
      price=9.8,
      bid_price=[9.79],
      ask_price=[9.8],
    ),
  )

  routed = executor._process_trade_intent.await_args.args[1]
  assert routed.execution_mode == TradeIntentExecutionMode.AUTO
  assert routed.metadata["exact_auto_exit_authorized"] is True
  assert routed.metadata["auto_exit_authorization_user_id"] == "user-1"
  assert routed.metadata["auto_exit_authorization_fingerprint"] == "f" * 64
  assert routed.metadata["auto_exit_authorization_challenge_id"] == "challenge-1"
  assert routed.metadata["auto_exit_authorization_device_session_id"] == "session-1"
  assert routed.metadata["auto_exit_authorized_at"] == authorized_at.isoformat()
  assert routed.metadata["auto_exit_authorization_expires_at"] == (
    expires_at.isoformat()
  )


@pytest.mark.asyncio
async def test_strategy_owned_exit_stays_in_runtime_when_monitor_is_running(
  monkeypatch,
):
  import quantx_engine.exit_plan_monitor as monitor_module

  executor = make_executor()
  runtime = make_runtime()
  template = make_template()
  runtime.exit_plan_book.register_entry_fill(
    template,
    volume=100,
    price=10.0,
  )
  executor._process_trade_intent = AsyncMock()
  sync_strategy_plan_book = AsyncMock()
  monkeypatch.setattr(
    AutoExitPlanService,
    "sync_strategy_plan_book",
    sync_strategy_plan_book,
  )
  monkeypatch.setattr(
    monitor_module.exit_plan_monitor,
    "_task",
    SimpleNamespace(done=lambda: False),
  )

  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 7, 30, 10, 0),
    market_data=MarketDataSnapshot(
      instrument_code="600000.SH",
      price=9.8,
      bid_price=[9.79],
      ask_price=[9.8],
    ),
  )

  sync_strategy_plan_book.assert_not_awaited()
  executor._process_trade_intent.assert_awaited_once()
  routed = executor._process_trade_intent.await_args.args[1]
  assert routed.direction == TradeIntentDirection.SELL
  assert routed.execution_ref == ExecutionOwnerRef(
    ExecutionOwnerType.EXIT_PLAN,
    template.plan_id,
  )
  assert "exit_plan_id" not in routed.metadata
  assert "owner_type" not in routed.metadata
  assert "owner_id" not in routed.metadata
  assert "strategy_run_id" not in routed.metadata


@pytest.mark.asyncio
async def test_local_pre_broker_rejected_exit_returns_plan_to_monitoring():
  executor = make_executor()
  runtime = make_runtime()
  template = make_template()
  plan = runtime.exit_plan_book.register_entry_fill(
    template,
    volume=100,
    price=10.0,
  )
  [decision] = runtime.exit_plan_book.evaluate(
    "600000.SH",
    ExitEvaluationContext(
      timestamp=datetime(2026, 7, 30, 10, 0),
      current_price=9.8,
    ),
  )
  runtime.exit_plan_book.mark_intent(decision, "exit-intent-1")

  await executor._notify_strategy_order(
    runtime,
    OrderStateEvent(
      order_id=None,
      status="REJECTED",
      filled_volume=0,
      execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.EXIT_PLAN,
        template.plan_id,
      ),
      metadata={
        "intent_id": "exit-intent-1",
        "execution_terminal_source": "LOCAL_PRE_BROKER_REJECTION",
      },
    ),
  )

  assert plan.status == ExitPlanStatus.ACTIVE
  assert plan.pending_intent_id == ""


@pytest.mark.asyncio
async def test_unexpected_pre_broker_failure_releases_exit_with_local_proof(
  monkeypatch,
):
  executor, runtime, plan, intent, _market_data = (
    await prepare_pending_runtime_exit()
  )
  monkeypatch.setattr(
    TradingRiskChecker,
    "evaluate_order",
    AsyncMock(side_effect=RuntimeError("risk checker unavailable")),
  )

  await StrategyExecutor._process_trade_intent(executor, runtime, intent)

  assert plan.status == ExitPlanStatus.ACTIVE
  assert plan.pending_intent_id == ""
  runtime.broker.place_order.assert_not_awaited()
  update = runtime.state_manager.update_trade_intent_status.await_args
  assert update.args[1] == "REJECTED"
  assert update.kwargs["metadata"]["execution_terminal_source"] == (
    "LOCAL_PRE_BROKER_REJECTION"
  )


@pytest.mark.asyncio
async def test_broker_boundary_exception_keeps_exit_pending_and_blocks_resell(
  monkeypatch,
):
  executor, runtime, plan, intent, market_data = (
    await prepare_pending_runtime_exit()
  )

  async def allow_order(_checker, request, **_kwargs):
    return OrderRiskDecision.allow(request)

  monkeypatch.setattr(TradingRiskChecker, "evaluate_order", allow_order)
  runtime.broker.place_order.side_effect = RuntimeError("broker result unknown")

  await StrategyExecutor._process_trade_intent(executor, runtime, intent)

  assert runtime.broker.place_order.await_count == 1
  routed_request = runtime.broker.place_order.await_args.args[0]
  assert routed_request.metadata["idempotency_key"] == (
    f"strategy-exit:{plan.plan_id}:{intent.intent_id}"
  )
  assert plan.status == ExitPlanStatus.EXIT_PENDING
  assert plan.pending_intent_id == intent.intent_id
  assert runtime.state_manager.release_order_resources.call_count == 0
  update = runtime.state_manager.update_trade_intent_status.await_args
  assert update.args[1] == "RECONCILE_REQUIRED"
  assert update.kwargs["metadata"]["execution_terminal_source"] == (
    "BROKER_BOUNDARY_UNKNOWN"
  )

  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 29, 10, 0, 1),
    market_data=market_data,
  )
  executor._process_trade_intent.assert_not_awaited()
  assert runtime.broker.place_order.await_count == 1


@pytest.mark.asyncio
async def test_t_trade_market_exit_reaches_runtime_broker_as_market_order(
  monkeypatch,
):
  executor, runtime, _plan, intent, _market_data = (
    await prepare_pending_runtime_exit(price_type="MARKET")
  )

  async def allow_order(_checker, request, **_kwargs):
    return OrderRiskDecision.allow(request)

  monkeypatch.setattr(TradingRiskChecker, "evaluate_order", allow_order)
  runtime.broker.place_order.side_effect = RuntimeError("broker result unknown")

  await StrategyExecutor._process_trade_intent(executor, runtime, intent)

  routed_request = runtime.broker.place_order.await_args.args[0]
  assert routed_request.price_type is PriceType.MARKET
  assert routed_request.metadata["price_type"] == "MARKET"
  assert "exit_plan_id" not in routed_request.metadata
  assert routed_request.execution_ref == ExecutionOwnerRef(
    ExecutionOwnerType.EXIT_PLAN,
    "exit-plan-1",
  )
  assert "strategy_run_id" not in routed_request.metadata


@pytest.mark.asyncio
async def test_generic_exit_uses_terminal_actual_fill_target_after_sizing():
  executor = make_executor()
  runtime = make_runtime()
  template = make_template()
  plan = runtime.exit_plan_book.register_entry_fill(
    template,
    volume=100,
    price=10.0,
  )
  [decision] = runtime.exit_plan_book.evaluate(
    "600000.SH",
    ExitEvaluationContext(
      timestamp=datetime(2026, 7, 30, 10, 0),
      current_price=9.8,
    ),
  )
  runtime.exit_plan_book.mark_intent(decision, "exit-intent-sized")
  metadata = {
    "exit_rule_id": decision.rule_id,
    "intent_id": "exit-intent-sized",
  }

  await executor._notify_strategy_order(
    runtime,
    OrderStateEvent(
      order_id="order-sized",
      status="FILLED",
      filled_volume=40,
      execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.EXIT_PLAN,
        template.plan_id,
      ),
      metadata=metadata,
    ),
  )

  assert plan.status == ExitPlanStatus.EXIT_PENDING
  assert plan.pending_requested_volume == 100
  assert plan.pending_terminal_cumulative_fill == 40

  await executor._notify_strategy_trade(
    runtime,
    TradeExecutionEvent(
      order_id="order-sized",
      instrument_code="600000.SH",
      trade_type="SELL",
      price=9.8,
      volume=40,
      execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.EXIT_PLAN,
        template.plan_id,
      ),
      metadata=metadata,
    ),
  )

  assert plan.status == ExitPlanStatus.PARTIALLY_EXITED
  assert plan.pending_intent_id == ""
  assert plan.exited_volume == 40


@pytest.mark.asyncio
async def test_active_exit_plan_prevents_normal_pause_and_stop():
  executor = make_executor()
  runtime = make_runtime()
  runtime.status = ExecutionStatus.RUNNING
  runtime.exit_plan_book.register_entry_fill(
    make_template(),
    volume=100,
    price=10.0,
  )
  executor.runs[runtime.run_id] = runtime

  assert await executor.pause(runtime.run_id) is False
  assert await executor.stop(runtime.run_id) is False
  assert runtime.status == ExecutionStatus.RUNNING


@pytest.mark.asyncio
async def test_limit_up_break_uses_configured_instrument_limits_and_routes_urgent():
  executor = make_executor()
  runtime = make_runtime()
  runtime.context.parameters["instrument_master"] = {
    "up_stop_price": 11.0,
    "down_stop_price": 9.0,
    "price_tick": 0.01,
  }
  template = ExitPlanTemplate(
    plan_id="board-exit-plan",
    source_type="LIMIT_UP_BOARD",
    source_id="board-entry",
    account_id="account-1",
    instrument_code="600000.SH",
    bucket="swing",
    run_id=runtime.run_id,
    strategy_id="1",
    rules=[
      ExitRuleSpec(
        strategy=ExitRuleType.LIMIT_UP_BREAK,
        priority=1000,
        parameters={
          "break_ticks": 1,
          "min_seal_seconds": 0,
          "min_holding_trading_days": 2,
        },
      )
    ],
    t1_policy=ExitT1Policy.WAIT_UNTIL_SELLABLE,
    auto_exit_authorized=True,
  )
  runtime.exit_plan_book.register_entry_fill(
    template,
    volume=100,
    price=10.0,
    trade_time=datetime(2026, 7, 30, 10, 0),
  )
  executor._process_trade_intent = AsyncMock()

  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 7, 31, 10, 0, 0),
    market_data=MarketDataSnapshot(
      instrument_code="600000.SH",
      price=11.0,
      bid_price=[11.0],
      ask_price=[0.0],
    ),
  )
  executor._process_trade_intent.assert_not_awaited()

  await executor._process_auto_exit_plans(
    runtime,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 7, 31, 10, 0, 1),
    market_data=MarketDataSnapshot(
      instrument_code="600000.SH",
      price=10.98,
      bid_price=[10.98],
      ask_price=[10.99],
    ),
  )

  routed = executor._process_trade_intent.await_args.args[1]
  assert routed.metadata["exit_rule_type"] == ExitRuleType.LIMIT_UP_BREAK.value
  assert routed.priority == TradeIntentPriority.URGENT


@pytest.mark.asyncio
async def test_t_trade_rapid_reversal_routes_urgent_protective_market_exit():
  executor = make_executor()
  runtime = make_runtime()
  runtime.context.parameters.update(
    {
      "account_id": "account-1",
      "auto_exit_acknowledged": True,
    }
  )
  template = runtime.strategy.build_exit_plan_template(
    instrument_code="600000.SH",
    batch_id="t-batch-1",
    plan_id="t-exit-1",
  )
  runtime.exit_plan_book.register_entry_fill(
    template,
    volume=300,
    price=27.80,
    trade_time=datetime(2026, 8, 12, 13, 49, 57),
  )
  executor._process_trade_intent = AsyncMock()

  for timestamp, last, bid in [
    (datetime(2026, 8, 12, 14, 2, 39), 29.67, 29.67),
    (datetime(2026, 8, 12, 14, 2, 45), 29.60, 29.34),
    (datetime(2026, 8, 12, 14, 2, 48), 29.63, 29.28),
  ]:
    await executor._process_auto_exit_plans(
      runtime,
      instrument_code="600000.SH",
      timestamp=timestamp,
      market_data=MarketDataSnapshot(
        instrument_code="600000.SH",
        price=last,
        bid_price=[bid],
        ask_price=[last],
      ),
    )

  routed = executor._process_trade_intent.await_args.args[1]
  assert routed.metadata["exit_rule_type"] == (
    ExitRuleType.RAPID_PROFIT_REVERSAL.value
  )
  assert routed.metadata["price_type"] == "MARKET"
  assert routed.metadata["price_reference"] == "BID"
  assert routed.metadata["protected_limit"] is False
  assert routed.execution_ref == ExecutionOwnerRef(
    ExecutionOwnerType.EXIT_PLAN,
    template.plan_id,
  )
  assert "owner_type" not in routed.metadata
  assert "owner_id" not in routed.metadata
  assert "strategy_run_id" not in routed.metadata
  assert routed.metadata["t_trade_role"] == "exit"
  assert routed.metadata["t_batch_id"] == "t-batch-1"
  assert routed.priority == TradeIntentPriority.URGENT
  assert routed.limit_price_hint == pytest.approx(29.28)


def test_limit_price_derivation_is_backtest_only_and_explicit():
  runtime = make_runtime()
  runtime.context.parameters["backtest_limit_rate"] = 0.10

  assert StrategyExecutor._backtest_limit_rate(runtime) is None

  runtime.context.mode = StrategyRunMode.BACKTEST
  assert StrategyExecutor._backtest_limit_rate(runtime) == pytest.approx(0.10)

  runtime.context.parameters["backtest_limit_rate"] = 0
  assert StrategyExecutor._backtest_limit_rate(runtime) is None


def test_t_trade_replay_derives_strict_limits_from_event_date_and_master_facts():
  runtime = make_runtime()
  runtime.context.mode = StrategyRunMode.BACKTEST
  runtime.context.parameters.update(
    {
      "t_trade_replay": True,
      "initial_instrument_metadata": {
        "600000.SH": {
          "instrument_name": "浦发银行",
          "listing_date": "1999-11-10",
          "expiry_date": "2038-01-19",
        }
      },
    }
  )
  timestamp = datetime(2026, 8, 19, 10, 0)

  rate = StrategyExecutor._backtest_limit_rate(
    runtime,
    instrument_code="600000.SH",
    timestamp=timestamp,
  )
  snapshot = MarketDataSnapshot.from_tick(
    SimpleNamespace(
      stock_code="600000.SH",
      time=timestamp,
      last_price=11.0,
      last_close=10.0,
      stock_status=0,
    ),
    limit_rate=rate,
  )

  assert rate == pytest.approx(0.10)
  assert snapshot.limit_up == pytest.approx(11.0)
  assert snapshot.limit_down == pytest.approx(9.0)
  assert snapshot.source == "tick_derived_limits"
  StrategyExecutor._record_t_trade_replay_price_limit_source(runtime, snapshot)
  assert runtime.context.parameters["replay_price_limit_source_counts"] == {
    "DERIVED_TICK": 1
  }


def test_t_trade_replay_keeps_strict_rejection_without_lifecycle_evidence():
  runtime = make_runtime()
  runtime.context.mode = StrategyRunMode.BACKTEST
  runtime.context.parameters.update(
    {
      "t_trade_replay": True,
      "initial_instrument_metadata": {"600000.SH": {}},
    }
  )

  assert (
    StrategyExecutor._backtest_limit_rate(
      runtime,
      instrument_code="600000.SH",
      timestamp=datetime(2026, 8, 19, 10, 0),
    )
    is None
  )
