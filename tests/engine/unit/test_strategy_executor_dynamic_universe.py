from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from quantx_domain.brokers.backtest import BacktestBroker
from quantx_domain.brokers.base import Position
from quantx_domain.brokers.simulator import SimulatorBroker
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.ashare_limit_up_board_assistant import (
  AshareLimitUpBoardAssistantStrategy,
)
from quantx_domain.strategies.base import (
  StrategyCadence,
  StrategyContext,
  StrategyRunMode,
)
from quantx_engine import strategy_executor as strategy_executor_module
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager


class FakeRealtimeAdapter:
  def __init__(self):
    self.subscribed = []
    self.unsubscribed = []

  async def subscribe_tick(self, instrument_code, callback):
    self.subscribed.append(instrument_code)
    return f"tick:{instrument_code}"

  async def unsubscribe(self, subscription_id):
    self.unsubscribed.append(subscription_id)


def _v3_runtime(*, mode=StrategyRunMode.PAPER, instruments=None):
  context = StrategyContext(
    run_id="run-emission-snapshot",
    mode=mode,
    instruments=list(instruments or ["600000.SH"]),
    parameters={
      "account_id": "account-emission",
      "target_trade_amount": 1_000.0,
      "max_concurrent_batches": 3,
      "max_total_t_exposure_pct": 0.2,
      "t_trade_opportunity_v3": True,
      "t_trade_replay": mode == StrategyRunMode.BACKTEST,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="emission-snapshot",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    status=ExecutionStatus.RUNNING,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = SimpleNamespace(
    get_account_quota=lambda: {"total_asset": 100_000.0},
    get_all_positions=lambda: {},
    get_bucket_ledger_snapshot=lambda: {},
    record_decision_trace=lambda _trace: None,
    settle_trading_day=lambda _date: None,
  )
  return runtime


def _v3_profile_service():
  return SimpleNamespace(
    load_reference_profile=AsyncMock(
      return_value={
        "profile_version": "test-profile",
        "profile_schema_version": 1,
        "as_of_trade_date": "2026-08-23",
        "profile_fingerprint": "a" * 64,
        "pullback_threshold_pct": 0.8,
        "momentum_rise_threshold_pct": 0.9,
        "momentum_amount_velocity_ratio": 2.1,
        "pullback_max_spread_ticks": 3,
        "momentum_max_spread_ticks": 10,
      }
    )
  )


async def _ready_v3_tick_runtime():
  executor = StrategyExecutor(opportunity_runtime_service=_v3_profile_service())
  runtime = _v3_runtime()
  await runtime.strategy.initialize()
  runtime.data_adapter = FakeRealtimeAdapter()
  await executor._apply_realtime_instrument_reconcile(
    runtime,
    ["600000.SH"],
    instrument_metadata={"600000.SH": {"eligible": True}},
  )
  return executor, runtime


@pytest.mark.asyncio
async def test_board_replay_keeps_open_position_in_dynamic_universe_as_draining():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-board-replay-sticky",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"limit_up_board_replay": True},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="board-replay-sticky",
    strategy_id=1,
    strategy_class=AshareLimitUpBoardAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareLimitUpBoardAssistantStrategy(context)
  await runtime.strategy.initialize()
  runtime.broker = BacktestBroker()
  runtime.broker.positions["000001.SZ"] = Position(
    instrument_code="000001.SZ",
    long_volume=100,
    available_volume=0,
    today_buy_volume=100,
    long_avg_price=10.0,
    last_price=10.0,
    market_value=1_000.0,
  )

  result = await executor._apply_backtest_instrument_reconcile(
    runtime,
    [],
    instrument_metadata={},
  )

  assert result["instruments"] == ["000001.SZ"]
  state = runtime.strategy.state["instrument_states"]["000001.SZ"]
  assert state["draining"] is True
  assert state["entry_eligible"] is False


@pytest.mark.asyncio
async def test_reconcile_updates_tick_subscriptions_without_restarting_run():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-universe",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"account_id": "account-1"},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="dynamic-universe",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  await runtime.strategy.initialize()
  runtime.data_adapter = FakeRealtimeAdapter()

  first = await executor._apply_realtime_instrument_reconcile(
    runtime,
    ["600000.SH", "000001.SZ"],
    instrument_metadata={
      "600000.SH": {"eligible": True, "policy_volume": 100},
      "000001.SZ": {"eligible": True, "policy_volume": 200},
    },
  )
  second = await executor._apply_realtime_instrument_reconcile(
    runtime,
    ["000001.SZ"],
    instrument_metadata={
      "000001.SZ": {"eligible": True, "policy_volume": 200}
    },
  )

  assert first["added"] == ["000001.SZ"]
  assert set(runtime.data_adapter.subscribed) == {"600000.SH", "000001.SZ"}
  assert second["removed"] == ["600000.SH"]
  assert runtime.data_adapter.unsubscribed == ["tick:600000.SH"]
  assert runtime.context.instruments == ["000001.SZ"]
  assert set(runtime.realtime_subscription_ids) == {"000001.SZ"}


@pytest.mark.asyncio
async def test_v3_reconcile_injects_engine_emission_context_into_real_tick_input():
  executor = StrategyExecutor(opportunity_runtime_service=_v3_profile_service())
  gate_execute = Mock(wraps=executor._intent_emission_gate.execute)
  executor._intent_emission_gate.execute = gate_execute
  runtime = _v3_runtime()
  await runtime.strategy.initialize()
  runtime.data_adapter = FakeRealtimeAdapter()
  captured_inputs = []
  original_step = runtime.strategy.step

  async def capture_step(strategy_input):
    captured_inputs.append(strategy_input)
    return await original_step(strategy_input)

  runtime.strategy.step = capture_step
  await executor._apply_realtime_instrument_reconcile(
    runtime,
    ["600000.SH"],
    instrument_metadata={"600000.SH": {"eligible": True}},
  )

  await executor._process_tick(
    runtime,
    SimpleNamespace(
      stock_code="600000.SH",
      time=datetime(2026, 8, 23, 10, 0),
      last_price=10.0,
    ),
  )

  tick_inputs = [
    item for item in captured_inputs if item.cadence.value == "TICK"
  ]
  assert tick_inputs
  assert gate_execute.call_count >= 1
  assert all(call.args[0].instrument_code == "600000.SH" for call in gate_execute.call_args_list)
  tick_input = tick_inputs[-1]
  assert tick_input.market_context["t_trade_intent_emission"] == {
    "allowed": True,
    "blockers": [],
  }
  assert "INTENT_EMISSION_CONTEXT_MISSING" not in (
    runtime.strategy._intent_emission_blockers(
      tick_input,
      runtime.strategy.state["instrument_states"]["600000.SH"],
    )
  )


@pytest.mark.asyncio
async def test_v3_tick_gate_reads_same_pending_and_other_account_facts():
  executor, runtime = await _ready_v3_tick_runtime()
  state = runtime.strategy.state["instrument_states"]
  state["600000.SH"].update(
    entry_order_status="AWAITING_APPROVAL",
    pending_entry_intent_id="same-instrument-intent",
  )
  same = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 23, 10, 0),
    event=SimpleNamespace(stock_code="600000.SH"),
  )
  assert same.market_context["t_trade_intent_emission"] == {
    "allowed": False,
    "blockers": ["T_TRADE_SAME_INSTRUMENT_PENDING_INTENT_EXISTS"],
  }

  state["600000.SH"].update(
    entry_order_status="",
    pending_entry_intent_id="",
  )
  state["000001.SZ"] = {
    "instrument_code": "000001.SZ",
    "entry_order_status": "AWAITING_APPROVAL",
    "pending_entry_intent_id": "other-intent",
  }
  other_pending = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 23, 10, 0),
    event=SimpleNamespace(stock_code="600000.SH"),
  )
  assert other_pending.market_context["t_trade_intent_emission"] == {
    "allowed": True,
    "blockers": [],
  }
  assert set(other_pending.market_context["t_trade_intent_emission"]) == {
    "allowed",
    "blockers",
  }


@pytest.mark.asyncio
async def test_v3_tick_gate_blocks_other_active_batch_and_exposure_limit():
  executor, runtime = await _ready_v3_tick_runtime()
  runtime.context.parameters["max_concurrent_batches"] = 1
  runtime.strategy.state["instrument_states"]["000001.SZ"] = {
    "instrument_code": "000001.SZ",
    "batch_id": "other-batch",
    "entry_order_status": "FILLED",
    "entry_filled_volume": 100,
    "exit_filled_volume": 0,
    "entry_avg_price": 100.0,
  }
  active = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 23, 10, 0),
    event=SimpleNamespace(stock_code="600000.SH"),
  )
  assert active.market_context["t_trade_intent_emission"] == {
    "allowed": False,
    "blockers": ["T_TRADE_ACCOUNT_CONCURRENT_BATCH_LIMIT_REACHED"],
  }

  runtime.context.parameters["max_concurrent_batches"] = 3
  runtime.context.parameters["max_total_t_exposure_pct"] = 0.1
  exposure = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 23, 10, 0),
    event=SimpleNamespace(stock_code="600000.SH"),
  )
  assert exposure.market_context["t_trade_intent_emission"] == {
    "allowed": False,
    "blockers": ["T_TRADE_ACCOUNT_TOTAL_EXPOSURE_LIMIT_REACHED"],
  }


@pytest.mark.asyncio
async def test_v3_tick_gate_quota_unknown_is_primary_stable_blocker():
  executor, runtime = await _ready_v3_tick_runtime()
  runtime.state_manager.get_account_quota = lambda: {}

  strategy_input = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 23, 10, 0),
    event=SimpleNamespace(stock_code="600000.SH"),
  )

  emission = strategy_input.market_context["t_trade_intent_emission"]
  assert emission["allowed"] is False
  assert emission["blockers"][0] == "T_TRADE_PORTFOLIO_SNAPSHOT_STALE"


@pytest.mark.asyncio
async def test_v3_reconcile_evicts_profile_cache_for_departed_instrument():
  executor = StrategyExecutor(opportunity_runtime_service=_v3_profile_service())
  runtime = _v3_runtime(instruments=["600000.SH", "000001.SZ"])
  await runtime.strategy.initialize()
  runtime.data_adapter = FakeRealtimeAdapter()
  removed_key = ("600000.SH", "2026-08-23")
  retained_key = ("000001.SZ", "2026-08-23")
  runtime._t_trade_opportunity_profiles.update(
    {
      removed_key: None,
      retained_key: {"profile_version": "retained"},
    }
  )
  runtime._t_trade_opportunity_profile_errors[removed_key] = "PROFILE_LOOKUP_FAILED"
  runtime._t_trade_opportunity_profile_retry_after[removed_key] = 1.0

  await executor._apply_realtime_instrument_reconcile(
    runtime,
    ["000001.SZ"],
    instrument_metadata={"000001.SZ": {"eligible": True}},
  )

  assert removed_key not in runtime._t_trade_opportunity_profiles
  assert removed_key not in runtime._t_trade_opportunity_profile_errors
  assert removed_key not in runtime._t_trade_opportunity_profile_retry_after
  assert runtime._t_trade_opportunity_profiles[retained_key] == {
    "profile_version": "retained"
  }


@pytest.mark.asyncio
async def test_v3_emission_snapshot_removed_instrument_and_scope_changes_fail_closed():
  executor = StrategyExecutor(opportunity_runtime_service=_v3_profile_service())
  runtime = _v3_runtime()
  await runtime.strategy.initialize()
  runtime.data_adapter = FakeRealtimeAdapter()
  await executor._apply_realtime_instrument_reconcile(
    runtime,
    ["600000.SH"],
    instrument_metadata={"600000.SH": {"eligible": True}},
  )

  timestamp = datetime(2026, 8, 23, 10, 0)
  allowed = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=timestamp,
    event=SimpleNamespace(stock_code="600000.SH"),
  )
  assert allowed.market_context["t_trade_intent_emission"]["allowed"] is True

  await executor._apply_realtime_instrument_reconcile(
    runtime,
    ["600000.SH"],
    instrument_metadata={
      "600000.SH": {
        "eligible": True,
        "account_id": "another-account",
        "run_id": runtime.run_id,
      }
    },
  )
  explicit_scope_conflict = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=timestamp,
    event=SimpleNamespace(stock_code="600000.SH"),
  )
  assert explicit_scope_conflict.market_context["t_trade_intent_emission"][
    "allowed"
  ] is False
  assert explicit_scope_conflict.market_context["t_trade_intent_emission"][
    "blockers"
  ] == [
    "T_TRADE_INTENT_EMISSION_SCOPE_MISMATCH",
    "T_TRADE_INTENT_EMISSION_ACCOUNT_SCOPE_MISMATCH",
  ]
  assert "T_TRADE_INTENT_EMISSION_ACCOUNT_SCOPE_MISMATCH" in (
    explicit_scope_conflict.market_context["t_trade_intent_emission"]["blockers"]
  )

  await executor._apply_realtime_instrument_reconcile(
    runtime,
    ["600000.SH"],
    instrument_metadata={"600000.SH": {"eligible": True}},
  )

  await executor._apply_realtime_instrument_reconcile(
    runtime,
    [],
    instrument_metadata={},
  )
  removed = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=timestamp,
    event=SimpleNamespace(stock_code="600000.SH"),
  )
  assert removed.market_context["t_trade_intent_emission"]["allowed"] is False
  assert removed.market_context["t_trade_intent_emission"]["blockers"] == [
    "UNIVERSE_ELIGIBILITY_UNAVAILABLE",
  ]
  assert "UNIVERSE_ELIGIBILITY_UNAVAILABLE" in removed.market_context[
    "t_trade_intent_emission"
  ]["blockers"]

  await executor._apply_realtime_instrument_reconcile(
    runtime,
    ["600000.SH"],
    instrument_metadata={"600000.SH": {"eligible": True}},
  )
  runtime.context.parameters["account_id"] = "other-account"
  wrong_account = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=timestamp,
    event=SimpleNamespace(stock_code="600000.SH"),
  )
  assert wrong_account.market_context["t_trade_intent_emission"]["allowed"] is False
  assert wrong_account.market_context["t_trade_intent_emission"]["blockers"] == [
    "T_TRADE_INTENT_EMISSION_SCOPE_MISMATCH",
    "T_TRADE_INTENT_EMISSION_ACCOUNT_SCOPE_MISMATCH",
  ]
  assert "T_TRADE_INTENT_EMISSION_ACCOUNT_SCOPE_MISMATCH" in wrong_account.market_context[
    "t_trade_intent_emission"
  ]["blockers"]

  runtime.context.parameters["account_id"] = "account-emission"
  runtime.context.run_id = "other-run"
  wrong_run = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=timestamp,
    event=SimpleNamespace(stock_code="600000.SH"),
  )
  assert wrong_run.market_context["t_trade_intent_emission"]["allowed"] is False
  assert wrong_run.market_context["t_trade_intent_emission"]["blockers"] == [
    "T_TRADE_INTENT_EMISSION_SCOPE_MISMATCH",
    "T_TRADE_INTENT_EMISSION_RUN_SCOPE_MISMATCH",
  ]
  assert "T_TRADE_INTENT_EMISSION_RUN_SCOPE_MISMATCH" in wrong_run.market_context[
    "t_trade_intent_emission"
  ]["blockers"]


@pytest.mark.asyncio
async def test_v3_emission_gate_preserves_draining_and_invalid_entry_blockers_exactly():
  executor = StrategyExecutor(opportunity_runtime_service=_v3_profile_service())
  runtime = _v3_runtime()
  await runtime.strategy.initialize()
  runtime.data_adapter = FakeRealtimeAdapter()

  await executor._apply_realtime_instrument_reconcile(
    runtime,
    ["600000.SH"],
    instrument_metadata={"600000.SH": {"eligible": True, "draining": True}},
  )
  draining = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 23, 10, 0),
    event=SimpleNamespace(stock_code="600000.SH"),
  )
  assert draining.market_context["t_trade_intent_emission"]["blockers"] == [
    "INSTRUMENT_DRAINING",
  ]

  runtime.t_trade_intent_emission_by_instrument["600000.SH"] = []
  invalid = executor._t_trade_intent_emission_context(runtime, "600000.SH")
  assert invalid == {
    "allowed": False,
    "blockers": ["T_TRADE_INTENT_EMISSION_CONTEXT_INVALID"],
  }


@pytest.mark.asyncio
async def test_v3_reconcile_failure_clears_old_emission_authority_atomically():
  executor = StrategyExecutor(opportunity_runtime_service=_v3_profile_service())
  runtime = _v3_runtime()
  await runtime.strategy.initialize()
  runtime.data_adapter = FakeRealtimeAdapter()
  await executor._apply_realtime_instrument_reconcile(
    runtime,
    ["600000.SH"],
    instrument_metadata={"600000.SH": {"eligible": True}},
  )
  assert runtime.t_trade_intent_emission_by_instrument["600000.SH"]["allowed"] is True

  async def fail_reconcile(_input):
    raise RuntimeError("reconcile failed")

  runtime.strategy.step = fail_reconcile
  with pytest.raises(RuntimeError, match="reconcile failed"):
    await executor._apply_realtime_instrument_reconcile(
      runtime,
      [],
      instrument_metadata={},
    )
  assert runtime.t_trade_intent_emission_by_instrument == {}


@pytest.mark.asyncio
async def test_v3_emission_snapshot_rejects_unbounded_reconcile(monkeypatch):
  monkeypatch.setattr(
    strategy_executor_module,
    "_T_TRADE_INTENT_EMISSION_MAX_INSTRUMENTS",
    1,
  )
  executor = StrategyExecutor(opportunity_runtime_service=_v3_profile_service())
  runtime = _v3_runtime()
  await runtime.strategy.initialize()
  runtime.data_adapter = FakeRealtimeAdapter()
  with pytest.raises(ValueError, match="有界标的上限"):
    await executor._apply_realtime_instrument_reconcile(
      runtime,
      ["600000.SH", "000001.SZ"],
      instrument_metadata={
        "600000.SH": {"eligible": True},
        "000001.SZ": {"eligible": True},
      },
    )
  assert runtime.t_trade_intent_emission_by_instrument == {}


@pytest.mark.asyncio
async def test_v3_emission_snapshot_rejects_unbounded_metadata_with_small_universe():
  executor = StrategyExecutor(opportunity_runtime_service=_v3_profile_service())
  runtime = _v3_runtime()
  await runtime.strategy.initialize()
  runtime.data_adapter = FakeRealtimeAdapter()
  metadata = {
    str(index): {"eligible": True}
    for index in range(4097)
  }

  with pytest.raises(ValueError, match="元数据超过有界标的上限"):
    await executor._apply_realtime_instrument_reconcile(
      runtime,
      ["600000.SH"],
      instrument_metadata=metadata,
    )
  assert runtime.t_trade_intent_emission_by_instrument == {}


@pytest.mark.asyncio
async def test_v3_backtest_initial_reconcile_publishes_emission_snapshot_before_ticks():
  executor = StrategyExecutor(opportunity_runtime_service=_v3_profile_service())
  runtime = _v3_runtime(mode=StrategyRunMode.BACKTEST)
  runtime.context.parameters["initial_instrument_metadata"] = {
    "600000.SH": {"eligible": True}
  }
  await runtime.strategy.initialize()

  await executor._initialize_backtest_dynamic_universe(runtime)

  assert runtime.t_trade_intent_emission_by_instrument["600000.SH"]["allowed"] is True
  strategy_input = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 23, 10, 0),
    event=SimpleNamespace(stock_code="600000.SH"),
  )
  assert strategy_input.market_context["t_trade_intent_emission"]["allowed"] is True


def test_v3_runtime_reset_drops_emission_snapshot():
  runtime = _v3_runtime()
  runtime.t_trade_intent_emission_by_instrument = {
    "600000.SH": {
      "instrument_code": "600000.SH",
      "run_id": runtime.run_id,
      "account_id": "account-emission",
      "allowed": True,
      "blockers": [],
    }
  }

  StrategyExecutor._reset_runtime_generation_transients(runtime)

  assert runtime.t_trade_intent_emission_by_instrument == {}


def test_dynamic_holding_snapshot_seeds_core_inventory_without_overwriting_active_batch():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-inventory",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"account_id": "account-1"},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="dynamic-inventory",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = RuntimeStateManager(
    run_id=context.run_id, persist_enabled=False, enable_reserve=True
  )
  runtime.broker = SimulatorBroker(account_id="paper-account")
  metadata = {
    "600000.SH": {
      "position_shares": 1000,
      "position_available_shares": 800,
      "position_frozen_shares": 100,
      "position_avg_price": 9.5,
      "position_market_value": 10_000.0,
    }
  }

  executor._sync_dynamic_holding_inventory(runtime, metadata)

  position = runtime.state_manager.get_position("600000.SH")
  ledger = runtime.state_manager.get_bucket_ledger_snapshot()
  assert position["long_volume"] == 1000
  assert position["available_volume"] == 800
  assert ledger["instruments"]["600000.SH"]["core"]["available_volume"] == 800
  assert ledger["instruments"]["600000.SH"]["swing"]["total_volume"] == 0
  assert runtime.broker.positions["600000.SH"].available_volume == 800

  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "entry_filled_volume": 100,
          "exit_filled_volume": 0,
        }
      }
    }
  )
  executor._sync_dynamic_holding_inventory(
    runtime,
    {
      "600000.SH": {
        **metadata["600000.SH"],
        "position_shares": 500,
        "position_available_shares": 500,
      }
    },
  )
  assert runtime.state_manager.get_position("600000.SH")["long_volume"] == 1000

  runtime.strategy.state["instrument_states"]["600000.SH"] = {
    "batch_id": "batch-awaiting-trade-detail",
    "entry_filled_volume": 0,
    "exit_filled_volume": 0,
  }
  executor._sync_dynamic_holding_inventory(
    runtime,
    {
      "600000.SH": {
        **metadata["600000.SH"],
        "position_shares": 600,
        "position_available_shares": 600,
      }
    },
  )
  assert runtime.state_manager.get_position("600000.SH")["long_volume"] == 1000
