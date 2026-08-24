"""
StrategyExecutor 单元测试

测试策略执行器的核心功能:
- create: 创建策略运行实例
- start: 启动策略运行
- stop: 停止策略运行
- pause: 暂停策略运行
- resume: 恢复策略运行
- delete: 删除策略运行
- get: 获取策略运行
- get_all: 获取所有运行
- get_running: 获取运行中的策略
"""

import asyncio
import json
from collections import UserDict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import quantx_engine.strategy_executor as strategy_executor_module
from quantx_application.t_trade_v3 import MaterializeEvaluationAfterCAS
from quantx_domain.brokers.base import (
  OrderRequest,
  OrderResponse,
  OrderStatus,
  OrderType,
  PriceType,
)
from quantx_domain.brokers.simulator import SimulatorBroker
from quantx_domain.market import Tick
from quantx_domain.strategies.base import (
  OrderStateEvent,
  RuntimeStatePatch,
  StrategyBase,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
)
from quantx_domain.trading import MarketDataSnapshot
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_infrastructure.core.runtime_state_manager import (
  RuntimeStateManager,
  RuntimeStateRestoreResult,
  RuntimeStateRestoreStatus,
)
from quantx_infrastructure.models.enums import StrategyRunMode


class MockStrategy(StrategyBase):
  """测试用的模拟策略"""

  @property
  def name(self) -> str:
    return "MockExecutorStrategy"

  @property
  def description(self) -> str:
    return "用于测试执行器的模拟策略"

  @property
  def version(self) -> str:
    return "1.0.0"

  @classmethod
  def get_parameter_schema(cls) -> dict:
    return {
      "type": "object",
      "properties": {
        "period": {"type": "integer", "default": 20},
        "threshold": {"type": "number", "default": 0.02},
      },
      "required": []
    }

  async def on_init(self):
    self.initialized = True

  async def on_start(self):
    self.started = True

  async def on_stop(self):
    self.stopped = True

  async def step(self, input: StrategyInput) -> StrategyOutput:
    return StrategyOutput()


class PatchCallbackStrategy(MockStrategy):
  async def on_order(self, event: OrderStateEvent) -> RuntimeStatePatch:
    return RuntimeStatePatch(set={"order_seen": str(event.status)})

  async def on_trade(self, event: TradeExecutionEvent) -> RuntimeStatePatch:
    return RuntimeStatePatch(set={"trade_seen": int(event.volume or 0)})


async def keep_running_loop(runtime):
  """测试用：让执行循环保持运行，直到任务被取消。"""
  await asyncio.Event().wait()


@pytest.fixture(autouse=True)
def isolate_executor_tests_from_runtime_state_database():
  """Executor unit tests must not read or write the durable runtime store."""

  async def restore_without_database(manager):
    status = (
      RuntimeStateRestoreStatus.PERSISTENCE_DISABLED
      if not manager.persist_enabled
      else RuntimeStateRestoreStatus.NOT_FOUND
    )
    return RuntimeStateRestoreResult(status=status, state=manager._state)

  async def checkpoint_without_database(manager):
    manager._dirty = False
    return True

  async def save_without_database(manager):
    manager._dirty = False
    return True

  async def no_unapplied_runtime_event(_manager):
    return None

  async def connect_without_shared_adapter_manager(_mode, adapter):
    if getattr(adapter, "is_connected", False) is True:
      return True
    return bool(await adapter.connect())

  original_seed_simulated_positions = (
    StrategyExecutor._seed_simulated_broker_positions
  )

  def seed_without_mocked_backtest_broker(executor, runtime) -> None:
    if not isinstance(strategy_executor_module.BacktestBroker, type):
      return
    original_seed_simulated_positions(executor, runtime)

  with (
    patch.object(RuntimeStateManager, "restore", restore_without_database),
    patch.object(
      RuntimeStateManager,
      "checkpoint_strategy_state_changes",
      checkpoint_without_database,
    ),
    patch.object(RuntimeStateManager, "save_snapshot", save_without_database),
    patch.object(
      RuntimeStateManager,
      "get_earliest_unapplied_runtime_event_key",
      no_unapplied_runtime_event,
    ),
    patch(
      "quantx_engine.strategy_executor.adapter_manager.ensure_adapter_connected_for_mode",
      new_callable=AsyncMock,
      side_effect=connect_without_shared_adapter_manager,
    ),
    patch(
      "quantx_engine.strategy_executor.adapter_manager.release_adapter_for_mode",
      new_callable=AsyncMock,
    ),
    patch.object(
      StrategyExecutor,
      "_seed_simulated_broker_positions",
      seed_without_mocked_backtest_broker,
    ),
  ):
    yield


@pytest.fixture
async def strategy_executor():
  executor = StrategyExecutor(max_workers=2)
  yield executor
  await executor.shutdown()


def test_runtime_state_persistence_scope_and_startup_checkpoint_invariants() -> None:
  def runtime(mode: StrategyRunMode, parameters: dict) -> SimpleNamespace:
    return SimpleNamespace(
      context=SimpleNamespace(mode=mode, parameters=parameters),
    )

  ordinary_backtest = runtime(StrategyRunMode.BACKTEST, {})
  replay_backtest = runtime(
    StrategyRunMode.BACKTEST,
    {"t_trade_replay": True},
  )
  paper = runtime(StrategyRunMode.PAPER, {})
  live = runtime(StrategyRunMode.LIVE, {})
  unsupported = runtime("UNSUPPORTED", {})

  assert StrategyExecutor._runtime_state_persistence_enabled(ordinary_backtest) is True
  assert StrategyExecutor._runtime_state_persistence_enabled(replay_backtest) is True
  assert StrategyExecutor._runtime_state_persistence_enabled(paper) is True
  assert StrategyExecutor._runtime_state_persistence_enabled(live) is True
  assert StrategyExecutor._runtime_state_persistence_enabled(unsupported) is False
  assert (
    StrategyExecutor._runtime_state_checkpoint_policy(ordinary_backtest)
    == "DAY_BATCH"
  )
  assert (
    StrategyExecutor._runtime_state_checkpoint_policy(paper)
    == "SESSION_BOUNDARY"
  )

  assert StrategyExecutor._requires_startup_runtime_state_checkpoint(
    ordinary_backtest
  ) is True
  assert StrategyExecutor._requires_startup_runtime_state_checkpoint(
    replay_backtest
  ) is True
  assert StrategyExecutor._requires_startup_runtime_state_checkpoint(paper) is True
  assert StrategyExecutor._requires_startup_runtime_state_checkpoint(live) is True
  assert StrategyExecutor._requires_startup_runtime_state_checkpoint(unsupported) is False


def _session_checkpoint_runtime(
  *,
  mode: StrategyRunMode = StrategyRunMode.PAPER,
) -> tuple[StrategyRuntime, SimpleNamespace]:
  context = StrategyContext(
    run_id="session-checkpoint-run",
    mode=mode,
    instruments=["600000.SH"],
    parameters={},
  )
  diagnostic_outbox: dict[str, dict] = {}
  prepared_holder: dict[str, SimpleNamespace | None] = {"value": None}

  def enqueue(events: list[dict]) -> None:
    for event in events:
      event_key = str(event.get("event_key") or "").strip()
      if event_key:
        diagnostic_outbox.setdefault(event_key, dict(event))

  def pending() -> list[dict]:
    return [dict(event) for event in diagnostic_outbox.values()]

  async def prepare(**kwargs) -> SimpleNamespace:
    materialization_events = [
      dict(event) for event in kwargs["materialization_events"]
    ]
    for event in materialization_events:
      event_key = str(event.get("event_key") or "").strip()
      if event_key:
        diagnostic_outbox.setdefault(event_key, event)
    checkpoint = SimpleNamespace(
      checkpoint_id="prepared-1",
      trade_date=kwargs["trade_date"].isoformat(),
      session=kwargs["session"],
      completeness={
        **dict(kwargs["completeness"]),
        "materialization_event_keys": list(
          str(event.get("event_key") or "").strip()
          for event in materialization_events
          if str(event.get("event_key") or "").strip()
        ),
      },
      processed_watermark=dict(kwargs["processed_watermark"]),
    )
    prepared_holder["value"] = checkpoint
    return checkpoint

  async def finalize(**kwargs) -> SimpleNamespace | None:
    checkpoint = prepared_holder["value"]
    if checkpoint is None or kwargs["prepared_checkpoint_id"] != checkpoint.checkpoint_id:
      return None
    for event_key in kwargs["materialization_event_keys"]:
      diagnostic_outbox.pop(event_key, None)
    prepared_holder["value"] = None
    return SimpleNamespace(
      checkpoint_id="checkpoint-1",
      processed_watermark=dict(checkpoint.processed_watermark),
    )

  manager = SimpleNamespace(
    persist_enabled=True,
    _running=False,
    _snapshot_task=None,
    _state_sync_task=None,
    _state_queue=None,
    drain_strategy_state_changes=AsyncMock(return_value=True),
    enqueue_t_trade_diagnostic_events=MagicMock(side_effect=enqueue),
    pending_t_trade_diagnostic_events=MagicMock(side_effect=pending),
    prepare_checkpoint=AsyncMock(side_effect=prepare),
    finalize_prepared_checkpoint=AsyncMock(side_effect=finalize),
    latest_prepared_checkpoint=MagicMock(
      side_effect=lambda: prepared_holder["value"]
    ),
    has_prepared_checkpoint=MagicMock(
      side_effect=lambda: prepared_holder["value"] is not None
    ),
    checkpoint_strategy_state_changes=AsyncMock(return_value=True),
    force_save=AsyncMock(return_value=True),
    stop_state_sync=AsyncMock(return_value=None),
    stop=AsyncMock(return_value=None),
    settle_trading_day=MagicMock(),
    get_account_quota=MagicMock(return_value={}),
    get_all_positions=MagicMock(return_value={}),
    get_bucket_ledger_snapshot=MagicMock(return_value={}),
    record_decision_trace=MagicMock(),
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="session-checkpoint",
    strategy_id=1,
    strategy_class=MockStrategy,
    context=context,
    status=ExecutionStatus.RUNNING,
    state_manager=manager,
  )
  return runtime, manager


def _whole_quote_checkpoint_status(
  *,
  captured_at: datetime,
  sequence: int = 17,
) -> dict:
  return {
    "status": "READY",
    "stream_id": "whole-quote-stream",
    "generation": 3,
    "sequence": sequence,
    "captured_at": captured_at,
    "queue_depth": 0,
    "lagging_consumers": 0,
  }


def _configure_terminal_hot_summary(
  executor: StrategyExecutor,
  runtime: StrategyRuntime,
  *,
  suffix: str,
) -> tuple[dict, SimpleNamespace]:
  runtime.context.parameters["account_id"] = "account-1"
  source_time = datetime(2026, 8, 24, 15, 1, tzinfo=timezone(timedelta(hours=8)))
  event = {
    "event_key": f"{runtime.run_id}:600000.SH:{suffix}",
    "record_kind": "COALESCED_DIAGNOSTIC",
    "instrument_code": "600000.SH",
    "evaluated_at_ms": int(source_time.timestamp() * 1000),
    "signal_snapshot": {"source_time_ms": int(source_time.timestamp() * 1000)},
  }
  service = SimpleNamespace(
    materialize_checkpoint_batch=AsyncMock(
      return_value=SimpleNamespace(persisted_event_keys=(event["event_key"],))
    ),
    flush_diagnostics_with_receipt=AsyncMock(
      return_value=SimpleNamespace(persisted_event_keys=())
    ),
  )
  executor.opportunity_runtime_service = service
  executor._evaluation_materializer = MaterializeEvaluationAfterCAS(service)
  executor._defer_checkpoint_diagnostics(runtime, [event])
  runtime._checkpoint_processed_watermark = {
    "stream_id": "whole-quote-stream",
    "generation": 3,
    "sequence": 17,
    "source_time_ms": int(source_time.timestamp() * 1000),
  }
  return event, service


@pytest.mark.asyncio
async def test_session_checkpoint_uses_idle_timeout_boundary_and_global_fence(
  strategy_executor: StrategyExecutor,
) -> None:
  executor = strategy_executor
  runtime, manager = _session_checkpoint_runtime()
  executor._trading_date_helper = SimpleNamespace(
    is_trading_date=AsyncMock(return_value=True)
  )
  eligible_at = datetime(2026, 8, 24, 11, 35)
  fence = _whole_quote_checkpoint_status(
    captured_at=datetime(2026, 8, 24, 11, 31),
  )

  with patch.object(
    strategy_executor_module.whole_quote_hub,
    "status_snapshot",
    side_effect=[dict(fence), dict(fence)],
  ) as status_snapshot:
    # The existing empty-queue timeout calls this coordinator; no fresh Tick
    # is needed at 11:35 for it to attempt the AM boundary.
    await executor._maybe_coordinate_session_checkpoints(
      runtime,
      now=datetime(2026, 8, 24, 11, 34),
    )
    await executor._maybe_coordinate_session_checkpoints(runtime, now=eligible_at)

  manager.prepare_checkpoint.assert_awaited_once()
  manager.finalize_prepared_checkpoint.assert_awaited_once()
  assert status_snapshot.call_count == 2
  assert (
    manager.prepare_checkpoint.await_args.kwargs["processed_watermark"]
    == {
      "stream_id": "whole-quote-stream",
      "generation": 3,
      "sequence": 17,
      "source_time_ms": int(fence["captured_at"].timestamp() * 1000),
      "captured_at": fence["captured_at"].isoformat(),
      "queue_depth": 0,
      "lagging_consumers": 0,
    }
  )
  # A sparse instrument has no per-symbol update requirement; audit state is
  # deliberately not used as the completion predicate.
  assert runtime._checkpoint_processed_watermark == {}
  assert runtime.checkpoint_status["2026-08-24:AM"]["status"] == "COMPLETE"


@pytest.mark.asyncio
async def test_session_checkpoint_skips_non_trading_day_and_throttles_retry(
  strategy_executor: StrategyExecutor,
) -> None:
  executor = strategy_executor
  runtime, manager = _session_checkpoint_runtime()
  trading_day = AsyncMock(return_value=False)
  executor._trading_date_helper = SimpleNamespace(is_trading_date=trading_day)
  eligible_at = datetime(2026, 8, 24, 15, 5)

  with patch.object(
    strategy_executor_module.whole_quote_hub,
    "status_snapshot",
  ) as status_snapshot:
    await executor._maybe_coordinate_session_checkpoints(runtime, now=eligible_at)

  trading_day.assert_awaited_once_with("SH", eligible_at.date())
  status_snapshot.assert_not_called()
  manager.prepare_checkpoint.assert_not_awaited()
  assert {
    key: status["status"]
    for key, status in runtime.checkpoint_status.items()
  } == {
    "2026-08-24:AM": "SKIPPED",
    "2026-08-24:PM": "SKIPPED",
  }
  assert all(
    status["reason"] == "SH_NON_TRADING_DAY"
    and "next_retry_at" not in status
    for status in runtime.checkpoint_status.values()
  )
  await executor._maybe_coordinate_session_checkpoints(runtime, now=eligible_at)
  trading_day.assert_awaited_once()

  # Use a fresh run for the independently retryable trading-day checkpoint.
  runtime, manager = _session_checkpoint_runtime()
  trading_day = AsyncMock(return_value=True)
  executor._trading_date_helper = SimpleNamespace(is_trading_date=trading_day)

  retry_at = datetime(2026, 8, 24, 11, 35)
  fence = _whole_quote_checkpoint_status(
    captured_at=datetime(2026, 8, 24, 11, 31),
  )
  with (
    patch.object(strategy_executor_module.time_utils, "now", return_value=retry_at),
    patch.object(
      strategy_executor_module.whole_quote_hub,
      "status_snapshot",
      side_effect=[{"status": "OFFLINE"}, dict(fence), dict(fence)],
    ) as status_snapshot,
  ):
    await executor._maybe_coordinate_session_checkpoints(runtime, now=retry_at)
    # The saved next_retry_at prevents a busy empty-queue loop from exhausting
    # all retries before the five-second retry interval has elapsed.
    await executor._maybe_coordinate_session_checkpoints(runtime, now=retry_at)
    await executor._maybe_coordinate_session_checkpoints(
      runtime,
      now=retry_at + timedelta(seconds=6),
    )

  assert status_snapshot.call_count == 3
  manager.prepare_checkpoint.assert_awaited_once()
  manager.finalize_prepared_checkpoint.assert_awaited_once()
  assert runtime.checkpoint_status["2026-08-24:AM"]["status"] == "COMPLETE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "state_manager",
  [None, SimpleNamespace(persist_enabled=False)],
)
async def test_session_checkpoint_skips_calendar_without_enabled_state_persistence(
  strategy_executor: StrategyExecutor,
  state_manager: object | None,
) -> None:
  runtime, _manager = _session_checkpoint_runtime()
  runtime.state_manager = state_manager
  trading_day = AsyncMock(return_value=True)
  strategy_executor._trading_date_helper = SimpleNamespace(
    is_trading_date=trading_day
  )

  await strategy_executor._maybe_coordinate_session_checkpoints(
    runtime,
    now=datetime(2026, 8, 24, 15, 5),
  )

  trading_day.assert_not_awaited()
  assert runtime.checkpoint_status == {}


@pytest.mark.asyncio
async def test_session_checkpoint_filters_terminal_and_exhausted_specs_before_calendar(
  strategy_executor: StrategyExecutor,
) -> None:
  runtime, _manager = _session_checkpoint_runtime()
  current = datetime(2026, 8, 24, 15, 5)
  runtime.checkpoint_status.update(
    {
      "2026-08-24:AM": {"status": "COMPLETE", "attempts": 1},
      "2026-08-24:PM": {"status": "BLOCKED", "attempts": 60},
    }
  )
  trading_day = AsyncMock(return_value=True)
  strategy_executor._trading_date_helper = SimpleNamespace(
    is_trading_date=trading_day
  )

  await strategy_executor._maybe_coordinate_session_checkpoints(runtime, now=current)

  trading_day.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_checkpoint_throttles_calendar_failure_before_retry(
  strategy_executor: StrategyExecutor,
) -> None:
  runtime, _manager = _session_checkpoint_runtime()
  current = datetime(2026, 8, 24, 11, 35)
  trading_day = AsyncMock(side_effect=RuntimeError("calendar unavailable"))
  strategy_executor._trading_date_helper = SimpleNamespace(
    is_trading_date=trading_day
  )

  await strategy_executor._maybe_coordinate_session_checkpoints(runtime, now=current)
  await strategy_executor._maybe_coordinate_session_checkpoints(runtime, now=current)

  status = runtime.checkpoint_status["2026-08-24:AM"]
  assert status["status"] == "BLOCKED"
  assert status["reason"] == "SH_TRADING_CALENDAR_UNAVAILABLE:RuntimeError"
  assert status["attempts"] == 1
  assert status["next_retry_at"] == (
    current + timedelta(seconds=5)
  ).isoformat()
  trading_day.assert_awaited_once_with("SH", current.date())

  await strategy_executor._maybe_coordinate_session_checkpoints(
    runtime,
    now=current + timedelta(seconds=6),
  )

  assert trading_day.await_count == 2
  assert runtime.checkpoint_status["2026-08-24:AM"]["attempts"] == 2


@pytest.mark.asyncio
async def test_hot_diagnostic_coalescing_does_not_touch_runtime_state_manager(
  strategy_executor: StrategyExecutor,
) -> None:
  executor = strategy_executor
  runtime, manager = _session_checkpoint_runtime()
  event = {
    "event_key": "run:600000.SH:diagnostic:1",
    "record_kind": "COALESCED_DIAGNOSTIC",
    "instrument_code": "600000.SH",
    "evaluated_at_ms": 1_724_300_000_000,
  }

  executor._defer_checkpoint_diagnostics(runtime, [event])

  assert set(runtime._checkpoint_diagnostic_summaries) == {"600000.SH"}
  manager.checkpoint_strategy_state_changes.assert_not_awaited()
  manager.force_save.assert_not_awaited()
  manager.enqueue_t_trade_diagnostic_events.assert_not_called()


@pytest.mark.asyncio
async def test_checkpoint_batch_materializes_pure_material_before_finalizing_seal(
  strategy_executor: StrategyExecutor,
) -> None:
  executor = strategy_executor
  runtime, manager = _session_checkpoint_runtime(mode=StrategyRunMode.BACKTEST)
  runtime.context.parameters["account_id"] = "account-1"
  event = {
    "type": "T_TRADE_OPPORTUNITY_EVALUATION",
    "event_key": "session-checkpoint-run:600000.SH:material:1",
    "record_kind": "MATERIAL",
    "event_type": "STATE_TRANSITION",
    "instrument_code": "600000.SH",
    "evaluated_at_ms": 1_724_300_000_001,
    "signal_snapshot": {
      "source_time_ms": 1_724_300_000_001,
      "tick_ordinal": 1,
    },
  }
  service = SimpleNamespace(
    materialize_checkpoint_batch=AsyncMock(
      return_value=SimpleNamespace(persisted_event_keys=(event["event_key"],))
    ),
    flush_diagnostics_with_receipt=AsyncMock(
      return_value=SimpleNamespace(persisted_event_keys=())
    ),
  )
  executor.opportunity_runtime_service = service
  executor._evaluation_materializer = MaterializeEvaluationAfterCAS(service)
  executor._defer_checkpoint_diagnostics(runtime, [event])

  sealed = await executor._seal_runtime_checkpoint(
    runtime,
    trade_date=datetime(2026, 8, 24).date(),
    session=None,
    boundary_source_time=datetime(2026, 8, 24, 15, 0),
    processed_watermark={
      "stream_id": f"backtest:{runtime.run_id}",
      "generation": 1,
      "sequence": 1,
      "source_time_ms": 1_724_300_000_001,
    },
    continuity_generation=1,
    completeness={"complete": True, "reason": "SERIAL_REPLAY_DRAINED"},
    force=True,
  )

  assert sealed is True
  manager.prepare_checkpoint.assert_awaited_once()
  manager.finalize_prepared_checkpoint.assert_awaited_once()
  service.materialize_checkpoint_batch.assert_awaited_once()
  requests = service.materialize_checkpoint_batch.await_args.kwargs["events"]
  assert requests == [event]
  service.flush_diagnostics_with_receipt.assert_awaited_once_with(
    account_id="account-1",
    strategy_run_id=runtime.run_id,
  )
  assert runtime._checkpoint_diagnostic_summaries == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [StrategyRunMode.PAPER, StrategyRunMode.LIVE])
async def test_terminal_session_prefix_seals_hot_diagnostics_for_live_and_paper(
  strategy_executor: StrategyExecutor,
  mode: StrategyRunMode,
) -> None:
  executor = strategy_executor
  runtime, manager = _session_checkpoint_runtime(mode=mode)
  runtime.context.parameters["account_id"] = "account-1"
  source_time = datetime(2026, 8, 24, 15, 1, tzinfo=timezone(timedelta(hours=8)))
  event = {
    "event_key": f"session-checkpoint-run:600000.SH:{mode.value}:terminal",
    "record_kind": "COALESCED_DIAGNOSTIC",
    "instrument_code": "600000.SH",
    "evaluated_at_ms": int(source_time.timestamp() * 1000),
    "signal_snapshot": {"source_time_ms": int(source_time.timestamp() * 1000)},
  }
  service = SimpleNamespace(
    materialize_checkpoint_batch=AsyncMock(
      return_value=SimpleNamespace(persisted_event_keys=(event["event_key"],))
    ),
    flush_diagnostics_with_receipt=AsyncMock(
      return_value=SimpleNamespace(persisted_event_keys=())
    ),
  )
  executor.opportunity_runtime_service = service
  executor._evaluation_materializer = MaterializeEvaluationAfterCAS(service)
  executor._defer_checkpoint_diagnostics(runtime, [event])
  runtime._checkpoint_processed_watermark = {
    "stream_id": "whole-quote-stream",
    "generation": 3,
    "sequence": 17,
    "source_time_ms": int(source_time.timestamp() * 1000),
  }

  await executor._coordinate_terminal_session_checkpoint(
    runtime,
    cause="COMPLETED",
  )

  manager.prepare_checkpoint.assert_awaited_once()
  assert manager.prepare_checkpoint.await_args.kwargs["session"] == "TERMINAL"
  assert manager.prepare_checkpoint.await_args.kwargs["completeness"]["terminal"] is True
  manager.finalize_prepared_checkpoint.assert_awaited_once()
  assert runtime._checkpoint_diagnostic_summaries == {}
  assert runtime.checkpoint_status["2026-08-24:TERMINAL"]["status"] == "COMPLETE"


@pytest.mark.asyncio
async def test_terminal_session_prefix_fails_closed_without_a_processed_watermark(
  strategy_executor: StrategyExecutor,
) -> None:
  runtime, manager = _session_checkpoint_runtime(mode=StrategyRunMode.PAPER)
  strategy_executor._defer_checkpoint_diagnostics(
    runtime,
    [
      {
        "event_key": "session-checkpoint-run:600000.SH:terminal-unproven",
        "record_kind": "COALESCED_DIAGNOSTIC",
        "instrument_code": "600000.SH",
        "evaluated_at_ms": 1,
      }
    ],
  )

  with pytest.raises(
    RuntimeError,
    match="TERMINAL_SESSION_CHECKPOINT_WATERMARK_UNPROVEN",
  ):
    await strategy_executor._coordinate_terminal_session_checkpoint(
      runtime,
      cause="ERROR",
    )

  manager.prepare_checkpoint.assert_not_awaited()
  assert runtime._checkpoint_diagnostic_summaries


@pytest.mark.asyncio
async def test_backtest_day_checkpoint_excludes_acquired_first_event_of_next_day(
  strategy_executor: StrategyExecutor,
) -> None:
  """The serial replay queue seals each completed virtual day exactly once."""

  executor = strategy_executor
  runtime, manager = _session_checkpoint_runtime(mode=StrategyRunMode.BACKTEST)
  runtime.strategy = MockStrategy(runtime.context)
  first_tick = Tick(
    stock_code="600000.SH",
    time=datetime(2026, 8, 24, 14, 59),
    last_price=10.0,
    source_time_ms=1_724_508_000_000,
    tick_ordinal=1,
  )
  second_tick = Tick(
    stock_code="600000.SH",
    time=datetime(2026, 8, 25, 9, 30),
    last_price=10.1,
    source_time_ms=1_724_574_600_000,
    tick_ordinal=2,
  )

  with (
    patch.object(
      executor,
      "_ensure_t_trade_opportunity_profile",
      new_callable=AsyncMock,
    ),
    patch.object(executor, "_expire_pending_approvals", new_callable=AsyncMock),
    patch.object(
      executor,
      "_cancel_expired_strategy_orders",
      new_callable=AsyncMock,
    ),
    patch.object(
      executor,
      "_process_auto_exit_plans",
      new_callable=AsyncMock,
    ),
    patch.object(
      executor,
      "_observe_t_trade_candidate_outcomes",
      new_callable=AsyncMock,
    ),
    patch.object(
      executor,
      "_report_t_trade_replay_progress",
      new_callable=AsyncMock,
    ),
  ):
    consumer = asyncio.create_task(executor._process_event_queue(runtime))
    executor._enqueue_runtime_market_event(runtime, "tick", first_tick)
    executor._enqueue_runtime_market_event(runtime, "tick", second_tick)
    await asyncio.wait_for(runtime.event_queue.join(), timeout=2.0)
    await executor._coordinate_backtest_terminal_checkpoint(runtime, cause="TEST")
    runtime.status = ExecutionStatus.COMPLETED
    runtime._event_queue_wakeup.set()
    await asyncio.wait_for(consumer, timeout=2.0)

  assert manager.prepare_checkpoint.await_count == 2
  assert manager.finalize_prepared_checkpoint.await_count == 2
  assert [
    call.kwargs["trade_date"] for call in manager.prepare_checkpoint.await_args_list
  ] == [first_tick.time.date(), second_tick.time.date()]
  assert [
    call.kwargs["processed_watermark"]["sequence"]
    for call in manager.prepare_checkpoint.await_args_list
  ] == [1, 2]
  assert all(
    call.kwargs["session"] is None
    for call in manager.prepare_checkpoint.await_args_list
  )
  assert (
    runtime.checkpoint_status[f"{first_tick.time.date().isoformat()}:DAY"]["status"]
    == "COMPLETE"
  )
  assert (
    runtime.checkpoint_status[f"{second_tick.time.date().isoformat()}:DAY"]["status"]
    == "COMPLETE"
  )


def test_backtest_day_checkpoint_never_admits_two_acquired_queue_items() -> None:
  runtime, _manager = _session_checkpoint_runtime(mode=StrategyRunMode.BACKTEST)
  runtime.event_queue.put_nowait(("tick", object()))
  runtime.market_event_queue.put_nowait(("tick", object()))
  runtime.event_queue.get_nowait()
  runtime.market_event_queue.get_nowait()
  try:
    assert (
      StrategyExecutor._runtime_checkpoint_queues_drained(
        runtime,
        allow_current_market_event=True,
      )
      is False
    )
  finally:
    runtime.event_queue.task_done()
    runtime.market_event_queue.task_done()


@pytest.mark.asyncio
async def test_damaged_prepared_checkpoint_restores_for_diagnosis_then_fails_closed(
  strategy_executor: StrategyExecutor,
) -> None:
  runtime = strategy_executor.create(
    run_id="damaged-prepared-checkpoint",
    strategy_id=1,
    strategy_class=MockStrategy,
    context=StrategyContext(
      run_id="damaged-prepared-checkpoint",
      mode=StrategyRunMode.BACKTEST,
      instruments=["600000.SH"],
      parameters={},
    ),
  )
  fallback = SimpleNamespace(
    checkpoint_id="last-complete-day",
    trade_date="2026-08-24",
    session=None,
  )
  with (
    patch.object(RuntimeStateManager, "latest_prepared_checkpoint", return_value=None),
    patch.object(RuntimeStateManager, "has_prepared_checkpoint", return_value=True),
    patch.object(
      RuntimeStateManager,
      "restore_latest_complete_checkpoint",
      new=AsyncMock(return_value=fallback),
    ) as restore_complete,
  ):
    started = await strategy_executor.start(runtime.run_id)

  assert started is False
  restore_complete.assert_awaited_once()
  assert runtime.status == ExecutionStatus.ERROR
  assert runtime.error_message == "PREPARED_CHECKPOINT_CORRUPT_RECONCILIATION_REQUIRED"
  checkpoint_status = runtime.checkpoint_status["2026-08-24:DAY"]
  assert checkpoint_status["status"] == "BLOCKED"
  assert checkpoint_status["reason"] == (
    "PREPARED_CHECKPOINT_CORRUPT_RECONCILIATION_REQUIRED"
  )


@pytest.mark.asyncio
async def test_normal_backtest_stops_before_terminal_day_checkpoint(
  strategy_executor: StrategyExecutor,
) -> None:
  executor = strategy_executor
  runtime, manager = _session_checkpoint_runtime(mode=StrategyRunMode.BACKTEST)
  runtime.strategy = MockStrategy(runtime.context)
  runtime._checkpoint_virtual_trade_date = datetime(2026, 8, 24).date()
  runtime._checkpoint_virtual_sequence = 1
  runtime._checkpoint_processed_watermark = {
    "stream_id": f"backtest:{runtime.run_id}",
    "generation": 1,
    "sequence": 1,
    "source_time_ms": 1,
  }
  ordering: list[str] = []

  async def stop_strategy() -> None:
    ordering.append("strategy_stop")

  original_prepare = manager.prepare_checkpoint.side_effect

  async def ordered_prepare(**kwargs):
    ordering.append("prepare")
    return await original_prepare(**kwargs)

  runtime.strategy.stop = AsyncMock(side_effect=stop_strategy)
  manager.prepare_checkpoint.side_effect = ordered_prepare
  manager.get_latest_backtest_grid_book_snapshot = MagicMock(return_value=None)
  manager.get_backtest_grid_book_snapshot_count = MagicMock(return_value=0)
  manager.get_backtest_grid_book_observed_count = MagicMock(return_value=0)
  manager.finalize_backtest = AsyncMock(return_value="backtest.json")

  with (
    patch.object(
      executor,
      "_initialize_backtest_dynamic_universe",
      new_callable=AsyncMock,
    ),
    patch.object(executor, "_run_backtest_loop", new_callable=AsyncMock),
    patch.object(executor, "_finalize_t_trade_replay", new_callable=AsyncMock),
    patch.object(
      executor,
      "_finalize_t_trade_candidate_outcomes",
      new_callable=AsyncMock,
    ),
    patch.object(
      executor,
      "_flush_t_trade_opportunity_diagnostics",
      new_callable=AsyncMock,
    ),
  ):
    await executor._run_strategy_loop(runtime)

  assert runtime.status == ExecutionStatus.COMPLETED
  runtime.strategy.stop.assert_awaited_once()
  manager.prepare_checkpoint.assert_awaited_once()
  assert ordering.index("strategy_stop") < ordering.index("prepare")


@pytest.mark.asyncio
async def test_normal_paper_completion_checkpoints_post_stop_state(
  strategy_executor: StrategyExecutor,
) -> None:
  executor = strategy_executor
  runtime, manager = _session_checkpoint_runtime(mode=StrategyRunMode.PAPER)
  runtime.strategy = MockStrategy(runtime.context)
  runtime.strategy.stop = AsyncMock()
  runtime.context.parameters["account_id"] = "account-1"
  source_time = datetime(2026, 8, 24, 15, 1, tzinfo=timezone(timedelta(hours=8)))
  event = {
    "event_key": "session-checkpoint-run:600000.SH:paper-normal-terminal",
    "record_kind": "COALESCED_DIAGNOSTIC",
    "instrument_code": "600000.SH",
    "evaluated_at_ms": int(source_time.timestamp() * 1000),
    "signal_snapshot": {"source_time_ms": int(source_time.timestamp() * 1000)},
  }
  service = SimpleNamespace(
    materialize_checkpoint_batch=AsyncMock(
      return_value=SimpleNamespace(persisted_event_keys=(event["event_key"],))
    ),
    flush_diagnostics_with_receipt=AsyncMock(
      return_value=SimpleNamespace(persisted_event_keys=())
    ),
  )
  executor.opportunity_runtime_service = service
  executor._evaluation_materializer = MaterializeEvaluationAfterCAS(service)
  executor._defer_checkpoint_diagnostics(runtime, [event])
  runtime._checkpoint_processed_watermark = {
    "stream_id": "whole-quote-stream",
    "generation": 3,
    "sequence": 17,
    "source_time_ms": int(source_time.timestamp() * 1000),
  }

  with (
    patch.object(
      executor,
      "_initialize_backtest_dynamic_universe",
      new_callable=AsyncMock,
    ),
    patch.object(executor, "_run_realtime_loop", new_callable=AsyncMock),
  ):
    await executor._run_strategy_loop(runtime)

  assert runtime.status == ExecutionStatus.COMPLETED
  runtime.strategy.stop.assert_awaited_once()
  manager.prepare_checkpoint.assert_awaited_once()
  assert manager.prepare_checkpoint.await_args.kwargs["session"] == "TERMINAL"
  manager.finalize_prepared_checkpoint.assert_awaited_once()
  assert runtime._checkpoint_diagnostic_summaries == {}
  manager.checkpoint_strategy_state_changes.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [StrategyRunMode.PAPER, StrategyRunMode.LIVE])
async def test_explicit_stop_finalizes_terminal_prefix_before_state_manager_stop(
  strategy_executor: StrategyExecutor,
  mode: StrategyRunMode,
) -> None:
  executor = strategy_executor
  runtime, manager = _session_checkpoint_runtime(mode=mode)
  runtime.strategy = MockStrategy(runtime.context)
  _event, _service = _configure_terminal_hot_summary(
    executor,
    runtime,
    suffix=f"{mode.value}:explicit-stop",
  )
  ordering: list[str] = []
  original_prepare = manager.prepare_checkpoint.side_effect
  original_finalize = manager.finalize_prepared_checkpoint.side_effect

  async def ordered_prepare(**kwargs):
    ordering.append("prepare")
    return await original_prepare(**kwargs)

  async def ordered_finalize(**kwargs):
    ordering.append("finalize")
    return await original_finalize(**kwargs)

  async def stop_state_sync(_strategy) -> None:
    ordering.append("stop_state_sync")

  async def stop_manager() -> None:
    ordering.append("manager_stop")

  manager.prepare_checkpoint.side_effect = ordered_prepare
  manager.finalize_prepared_checkpoint.side_effect = ordered_finalize
  manager.stop_state_sync.side_effect = stop_state_sync
  manager.stop.side_effect = stop_manager
  executor.runs[runtime.run_id] = runtime

  stopped = await executor.stop(runtime.run_id, force=True)

  assert stopped is True
  assert runtime.status == ExecutionStatus.STOPPED
  assert runtime._checkpoint_diagnostic_summaries == {}
  assert manager.prepare_checkpoint.await_args.kwargs["session"] == "TERMINAL"
  assert ordering.index("prepare") < ordering.index("finalize")
  assert ordering.index("finalize") < ordering.index("stop_state_sync")
  assert ordering.index("stop_state_sync") < ordering.index("manager_stop")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [StrategyRunMode.PAPER, StrategyRunMode.LIVE])
async def test_error_cleanup_finalizes_terminal_prefix_before_state_manager_stop(
  strategy_executor: StrategyExecutor,
  mode: StrategyRunMode,
) -> None:
  executor = strategy_executor
  runtime, manager = _session_checkpoint_runtime(mode=mode)
  runtime.strategy = MockStrategy(runtime.context)
  _event, _service = _configure_terminal_hot_summary(
    executor,
    runtime,
    suffix=f"{mode.value}:error-cleanup",
  )
  ordering: list[str] = []
  original_prepare = manager.prepare_checkpoint.side_effect
  original_finalize = manager.finalize_prepared_checkpoint.side_effect

  async def ordered_prepare(**kwargs):
    ordering.append("prepare")
    return await original_prepare(**kwargs)

  async def ordered_finalize(**kwargs):
    ordering.append("finalize")
    return await original_finalize(**kwargs)

  async def stop_state_sync(_strategy) -> None:
    ordering.append("stop_state_sync")

  async def stop_manager() -> None:
    ordering.append("manager_stop")

  manager.prepare_checkpoint.side_effect = ordered_prepare
  manager.finalize_prepared_checkpoint.side_effect = ordered_finalize
  manager.stop_state_sync.side_effect = stop_state_sync
  manager.stop.side_effect = stop_manager

  await executor._cleanup_runtime_after_error(runtime)

  assert runtime.status == ExecutionStatus.ERROR
  assert runtime._checkpoint_diagnostic_summaries == {}
  assert manager.prepare_checkpoint.await_args.kwargs["session"] == "TERMINAL"
  assert ordering.index("prepare") < ordering.index("finalize")
  assert ordering.index("finalize") < ordering.index("stop_state_sync")
  assert ordering.index("stop_state_sync") < ordering.index("manager_stop")


def test_strategy_output_trace_content_addresses_hot_runtime_state(
  strategy_executor: StrategyExecutor,
) -> None:
  """A long hot run retains one bounded audit summary per evaluated output."""

  context = StrategyContext(
    run_id="trace-hot-state-run",
    mode=StrategyRunMode.BACKTEST,
    instruments=["600000.SH"],
    parameters={},
  )
  runtime = strategy_executor.create(
    run_id=context.run_id,
    strategy_id=701,
    strategy_class=MockStrategy,
    context=context,
  )
  runtime.state_manager = RuntimeStateManager(
    run_id=context.run_id,
    persist_enabled=True,
    is_backtest=True,
  )
  input_snapshot = StrategyInput(
    run_id=context.run_id,
    strategy_id="701",
    timestamp=datetime(2026, 8, 24, 10, 0),
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
  )
  instrument_states = {
    code: {
      "samples": [
        {"sequence": sequence, "marker": "hot-state-sentinel" * 8}
        for sequence in range(48)
      ]
    }
    for code in [
      "600000.SH",
      "000001.SZ",
      "000002.SZ",
      "000333.SZ",
      "300750.SZ",
      "601318.SH",
      "601398.SH",
      "601899.SH",
    ]
  }
  event = {
    "event_key": "trace-hot-state:600000.SH:1",
    "type": "T_TRADE_OPPORTUNITY_EVALUATION",
    "record_kind": "COALESCED_DIAGNOSTIC",
    "event_type": "T_TRADE_OPPORTUNITY_EVALUATION",
    "signal_snapshot": {"marker": "event-payload-must-not-be-copied" * 32},
  }
  patch = RuntimeStatePatch(
    set={
      "algorithm_phase": "EVALUATED",
      "instrument_states": instrument_states,
    },
    unset=["superseded_phase"],
    append_events=[event],
  )
  output = StrategyOutput(
    runtime_state_patch=patch,
    decision_tags=["HOT_OUTPUT"],
    trace_payload={"reason": "HOT_TICK"},
  )

  for index in range(1_000):
    input_snapshot.trace_id = f"trace-hot-state-{index}"
    strategy_executor._record_strategy_output_trace(
      runtime,
      output,
      input_snapshot,
    )

  records = runtime.state_manager._pending_decision_trace_records
  assert len(records) == 1_000
  first_patch = records[0]["state_patch"]
  assert first_patch["format"] == "CONTENT_ADDRESSED_RUNTIME_STATE_PATCH_V1"
  assert first_patch["set_keys"] == ["algorithm_phase", "instrument_states"]
  assert first_patch["set"]["algorithm_phase"]["value"] == "EVALUATED"
  instrument_summary = first_patch["set"]["instrument_states"]
  assert "value" not in instrument_summary
  assert instrument_summary["current_instrument"]["instrument_code"] == "600000.SH"
  assert "value" not in instrument_summary["current_instrument"]
  assert first_patch["append_events"] == [
    {
      "event_key": "trace-hot-state:600000.SH:1",
      "type": "T_TRADE_OPPORTUNITY_EVALUATION",
      "record_kind": "COALESCED_DIAGNOSTIC",
      "event_type": "T_TRADE_OPPORTUNITY_EVALUATION",
      "sha256": first_patch["append_events"][0]["sha256"],
      "json_bytes": first_patch["append_events"][0]["json_bytes"],
    }
  ]
  encoded_records = [
    json.dumps(record, default=str, sort_keys=True, separators=(",", ":")).encode(
      "utf-8"
    )
    for record in records
  ]
  assert max(len(record) for record in encoded_records) < 8_192
  assert all(b"hot-state-sentinel" not in record for record in encoded_records)
  assert all(
    b"event-payload-must-not-be-copied" not in record
    for record in encoded_records
  )
  assert all(
    record["state_patch"]["full_patch_sha256"]
    == first_patch["full_patch_sha256"]
    for record in records
  )

  same_patch = RuntimeStatePatch(
    set={
      "instrument_states": {
        code: instrument_states[code]
        for code in reversed(list(instrument_states))
      },
      "algorithm_phase": "EVALUATED",
    },
    unset=["superseded_phase"],
    append_events=[dict(reversed(list(event.items())))],
  )
  changed_patch = RuntimeStatePatch(
    set={
      "algorithm_phase": "EVALUATED",
      "instrument_states": {
        **instrument_states,
        "600000.SH": {"samples": [{"sequence": 999, "marker": "changed"}]},
      },
    },
    unset=["superseded_phase"],
    append_events=[event],
  )
  same_summary = strategy_executor_module._compact_runtime_state_patch_for_audit(
    same_patch,
    instrument_code="600000.SH",
  )
  changed_summary = strategy_executor_module._compact_runtime_state_patch_for_audit(
    changed_patch,
    instrument_code="600000.SH",
  )

  assert same_summary["full_patch_sha256"] == first_patch["full_patch_sha256"]
  assert changed_summary["full_patch_sha256"] != first_patch["full_patch_sha256"]
  assert (
    changed_summary["set"]["instrument_states"]["current_instrument"]["sha256"]
    != instrument_summary["current_instrument"]["sha256"]
  )


def test_strategy_output_trace_compacts_decimal_deterministically() -> None:
  patch = RuntimeStatePatch(set={"threshold": Decimal("1.2300")})

  first = strategy_executor_module._compact_runtime_state_patch_for_audit(
    patch,
    instrument_code="600000.SH",
  )
  second = strategy_executor_module._compact_runtime_state_patch_for_audit(
    patch,
    instrument_code="600000.SH",
  )

  assert first["full_patch_sha256"] == second["full_patch_sha256"]
  assert first["set"]["threshold"]["value"] == "1.2300"


def test_strategy_output_trace_uses_json_encoder_for_standard_state_tree(
  monkeypatch,
) -> None:
  class AuditPhase(Enum):
    READY = "READY"

  def normalized_tree_must_not_run(_value):
    raise AssertionError("standard state tree must use the JSON encoder fast path")

  monkeypatch.setattr(
    strategy_executor_module,
    "_trace_audit_json_value",
    normalized_tree_must_not_run,
  )
  patch = RuntimeStatePatch(
    set={
      "threshold": Decimal("1.2300"),
      "as_of": datetime(2026, 8, 24, 10, 5, 1),
      "trade_date": date(2026, 8, 24),
      "phase": AuditPhase.READY,
      "samples": [{"sequence": 1, "flags": ("A", "B")}],
    },
    append_events=[
      {
        "event_key": "json-fast-path:1",
        "type": "EVALUATION",
        "record_kind": "COALESCED_DIAGNOSTIC",
        "event_type": "EVALUATION",
      }
    ],
  )

  summary = strategy_executor_module._compact_runtime_state_patch_for_audit(
    patch,
    instrument_code="600000.SH",
  )

  assert summary["set"]["threshold"]["value"] == "1.2300"
  assert summary["set"]["as_of"]["value"] == "2026-08-24T10:05:01"
  assert summary["set"]["phase"]["value"] == "READY"
  assert summary["append_events"][0]["event_key"] == "json-fast-path:1"
  assert summary["full_patch_sha256"]


def test_strategy_output_trace_normalizes_nonstandard_mapping_keys_deterministically():
  first_patch = RuntimeStatePatch(
    set=UserDict(
      [
        (10, Decimal("1.2300")),
        (2, {"samples": (1, 2, 3)}),
      ]
    )
  )
  second_patch = RuntimeStatePatch(
    set=UserDict(
      [
        (2, {"samples": (1, 2, 3)}),
        (10, Decimal("1.2300")),
      ]
    )
  )

  first = strategy_executor_module._compact_runtime_state_patch_for_audit(
    first_patch,
    instrument_code="600000.SH",
  )
  second = strategy_executor_module._compact_runtime_state_patch_for_audit(
    second_patch,
    instrument_code="600000.SH",
  )

  assert first["full_patch_sha256"] == second["full_patch_sha256"]
  assert first["set_keys"] == ["10", "2"]
  assert first["set"]["10"]["value"] == "1.2300"


@pytest.mark.unit
class TestStrategyExecutor:
  """StrategyExecutor 单元测试类"""

  @pytest.fixture
  async def strategy_executor(self):
    """创建策略执行器实例"""
    executor = StrategyExecutor(max_workers=2)
    yield executor
    await executor.shutdown()

  @pytest.mark.asyncio
  async def test_create_run(self, strategy_executor: StrategyExecutor):
    """测试 create 创建策略运行"""
    # 创建上下文
    run_id = "test-run-001"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={"period": 20, "threshold": 0.02},
      initial_capital=1000000.0,
    )

    # 创建策略运行（同步方法，不需要 await）
    runtime = strategy_executor.create(
      run_id=run_id,
      strategy_id=1,
      strategy_class=MockStrategy,
      context=context,
    )

    # 验证运行时对象已创建
    assert runtime is not None
    assert runtime.run_id == run_id
    assert runtime.strategy_id == 1
    assert runtime.strategy_class == MockStrategy
    assert runtime.context.mode == StrategyRunMode.BACKTEST
    assert runtime.context.instruments == ["000001.SZ"]
    assert runtime.context.parameters == {"period": 20, "threshold": 0.02}
    assert runtime.context.initial_capital == 1000000.0
    assert runtime.status == ExecutionStatus.PENDING
    assert runtime.metrics is not None
    assert runtime.metrics.initial_capital == 1000000.0
    assert runtime.metrics.current_capital == 1000000.0

  @pytest.mark.asyncio
  async def test_strategy_input_uses_environment_layer(self, strategy_executor):
    run_id = "test-run-environment-layer"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={
        "environment_context": {
          "market_return_1d": -0.05,
          "market_amount_ratio": 1.60,
          "advancing_count": 300,
          "declining_count": 4400,
          "limit_down_count": 120,
        }
      },
      initial_capital=1000000.0,
    )
    runtime = strategy_executor.create(
      run_id=run_id,
      strategy_id=101,
      strategy_class=MockStrategy,
      context=context,
    )
    runtime.context.current_time = datetime(2024, 1, 2, 10, 0)

    input_snapshot = strategy_executor._build_strategy_input(
      runtime,
      cadence=StrategyCadence.BAR,
      instrument_code="000001.SZ",
      timestamp=datetime(2024, 1, 2, 10, 0),
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=datetime(2024, 1, 2, 10, 0),
        price=10.0,
        close=10.0,
        volume=100_000,
        amount=1_000_000,
      ),
    )

    assert input_snapshot.market_context["market_state"] == "PANIC"
    assert input_snapshot.market_context["breadth_state"] == "EXTREME_NEGATIVE"
    assert input_snapshot.risk_caps["risk_mode"] == "PANIC"
    assert input_snapshot.position_profile["profile"] == "DEFENSIVE"

  def test_strategy_input_exposes_engine_owned_market_lineage(
    self, strategy_executor
  ):
    run_id = "test-run-market-lineage"
    timestamp = datetime(2024, 1, 2, 10, 0)
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
    )
    runtime = strategy_executor.create(
      run_id=run_id,
      strategy_id=102,
      strategy_class=MockStrategy,
      context=context,
    )
    runtime._market_continuity_generations["000001.SZ"] = 4
    tick = SimpleNamespace(
      stock_code="000001.SZ",
      source_time_ms=1_704_160_800_123,
      tick_ordinal=2,
      source_sequence=19,
      transaction_num=99,
    )

    input_snapshot = strategy_executor._build_strategy_input(
      runtime,
      cadence=StrategyCadence.TICK,
      instrument_code="000001.SZ",
      timestamp=timestamp,
      event=tick,
    )

    lineage = input_snapshot.market_data_context
    assert lineage.source == "REPLAY"
    assert lineage.continuity_generation == 4
    assert lineage.source_sequence == 19
    assert lineage.source_time_ms == 1_704_160_800_123
    assert lineage.tick_ordinal == 2
    assert lineage.received_at_ms == lineage.source_time_ms
    assert lineage.quote_stale is False
    assert lineage.session.value == "CONTINUOUS_AM"
    assert lineage.trade_date.isoformat() == "2024-01-02"

  def test_live_strategy_input_uses_authoritative_transport_lineage(
    self, strategy_executor
  ):
    timestamp = datetime(2024, 1, 2, 10, 0)
    context = StrategyContext(
      run_id="live-market-lineage",
      mode=StrategyRunMode.LIVE,
      instruments=["000001.SZ"],
      parameters={},
    )
    runtime = strategy_executor.create(
      run_id=context.run_id,
      strategy_id=103,
      strategy_class=MockStrategy,
      context=context,
    )
    runtime._market_continuity_generations["000001.SZ"] = 99
    tick = SimpleNamespace(
      stock_code="000001.SZ",
      source_time_ms=1_704_160_800_123,
      tick_ordinal=42,
      continuity_generation=7,
      market_stream_id="authority-stream-7",
      market_stream_sequence=42,
    )

    input_snapshot = strategy_executor._build_strategy_input(
      runtime,
      cadence=StrategyCadence.TICK,
      instrument_code="000001.SZ",
      timestamp=timestamp,
      event=tick,
    )

    lineage = input_snapshot.market_data_context
    assert lineage.source == "REALTIME"
    assert lineage.continuity_generation == 7
    assert lineage.stream_id == "authority-stream-7"
    assert lineage.source_sequence == 42
    assert lineage.tick_ordinal == 42

  def test_live_context_does_not_apply_execution_quote_age_gate(
    self, strategy_executor
  ):
    context = StrategyContext(
      run_id="live-policy-owned-quote-age",
      mode=StrategyRunMode.LIVE,
      instruments=["000001.SZ"],
      parameters={},
    )
    runtime = strategy_executor.create(
      run_id=context.run_id,
      strategy_id=104,
      strategy_class=MockStrategy,
      context=context,
    )
    source_time_ms = 1_704_160_800_123
    tick = SimpleNamespace(
      stock_code="000001.SZ",
      source_time_ms=source_time_ms,
      tick_ordinal=43,
      continuity_generation=7,
      market_stream_id="authority-stream-7",
      market_stream_sequence=43,
    )
    received_at = datetime.fromtimestamp(
      (source_time_ms + 4_000) / 1000,
      timezone.utc,
    )

    with patch.object(
      strategy_executor_module.time_utils,
      "now",
      return_value=received_at,
    ):
      lineage = strategy_executor._build_market_data_context(
        runtime,
        cadence=StrategyCadence.TICK,
        instrument_code="000001.SZ",
        timestamp=received_at,
        event=tick,
      )

    assert lineage.received_at_ms - lineage.source_time_ms == 4_000
    assert lineage.quote_stale is False

  @pytest.mark.asyncio
  async def test_strategy_order_and_trade_patches_are_consumed(self, strategy_executor):
    run_id = "test-run-callback-patch"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
    )
    runtime = strategy_executor.create(
      run_id=run_id,
      strategy_id=102,
      strategy_class=PatchCallbackStrategy,
      context=context,
    )
    runtime.strategy = PatchCallbackStrategy(context)

    await strategy_executor._notify_strategy_order(
      runtime,
      OrderStateEvent(order_id="order-1", status="SUBMITTED"),
    )
    await strategy_executor._notify_strategy_trade(
      runtime,
      TradeExecutionEvent(
        order_id="order-1",
        instrument_code="000001.SZ",
        trade_type="BUY",
        price=10.0,
        volume=300,
      ),
    )

    assert runtime.strategy.state.order_seen == "SUBMITTED"
    assert runtime.strategy.state.trade_seen == 300

  @pytest.mark.parametrize(
    "patch",
    (
      SimpleNamespace(
        set={"safe_update": True, "nested": {"position_shares": 500}},
        unset=["preserved"],
        append_events=[],
      ),
      SimpleNamespace(
        set={"safe_update": True},
        unset=["preserved"],
        append_events=[
          {"event_type": "RISK", "payload": {"final_volume": 100}}
        ],
      ),
    ),
  )
  def test_duck_typed_runtime_patch_rejects_account_truth_atomically(
    self,
    strategy_executor,
    patch,
  ):
    context = StrategyContext(
      run_id="test-run-duck-patch-guard",
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
    )
    runtime = strategy_executor.create(
      run_id=context.run_id,
      strategy_id=103,
      strategy_class=MockStrategy,
      context=context,
    )
    runtime.strategy = MockStrategy(context)
    runtime.strategy.state.set("preserved", "before")

    with pytest.raises(ValueError, match="cannot mutate account fields"):
      strategy_executor._apply_runtime_state_patch(runtime, patch)

    assert runtime.strategy.state.to_dict() == {"preserved": "before"}

  def test_runtime_patch_revalidates_mutated_dataclass_before_applying(
    self,
    strategy_executor,
  ):
    context = StrategyContext(
      run_id="test-run-mutated-patch-guard",
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
    )
    runtime = strategy_executor.create(
      run_id=context.run_id,
      strategy_id=104,
      strategy_class=MockStrategy,
      context=context,
    )
    runtime.strategy = MockStrategy(context)
    guarded_patch = RuntimeStatePatch(set={"algorithm_phase": "READY"})
    guarded_patch.set["payload"] = {"requested_entry_volume": 100}

    with pytest.raises(ValueError, match="requested_entry_volume"):
      strategy_executor._apply_runtime_state_patch(runtime, guarded_patch)

    assert runtime.strategy.state.to_dict() == {}

  @pytest.mark.asyncio
  async def test_synthetic_reject_consumes_order_patch(self, strategy_executor):
    run_id = "test-run-synthetic-reject-patch"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
    )
    runtime = strategy_executor.create(
      run_id=run_id,
      strategy_id=103,
      strategy_class=PatchCallbackStrategy,
      context=context,
    )
    runtime.strategy = PatchCallbackStrategy(context)
    runtime.context.current_time = datetime(2024, 1, 2, 10, 0)

    await strategy_executor._process_trade_intent(
      runtime,
      TradeIntent(
        strategy_id="103",
        run_id=run_id,
        instrument_code="000001.SZ",
        direction=TradeIntentDirection.BUY,
        bucket="swing",
        reason="below_lot_unit_test",
        target_volume=50,
        limit_price_hint=10.0,
      ),
    )

    assert runtime.strategy.state.order_seen == "REJECTED"

  @pytest.mark.asyncio
  async def test_non_ready_whole_quote_gate_rejects_paper_order(
    self,
    strategy_executor,
  ):
    context = StrategyContext(
      run_id="test-paper-market-gate",
      mode=StrategyRunMode.PAPER,
      instruments=["000001.SZ"],
      parameters={},
      current_time=datetime(2024, 1, 2, 10, 0),
    )
    runtime = strategy_executor.create(
      run_id=context.run_id,
      strategy_id=104,
      strategy_class=PatchCallbackStrategy,
      context=context,
    )
    runtime.strategy = PatchCallbackStrategy(context)
    runtime.data_adapter = SimpleNamespace(
      subscription_manager=SimpleNamespace(
        hub=SimpleNamespace(is_ready=False),
      )
    )
    runtime.broker = SimpleNamespace(place_order=AsyncMock())

    await strategy_executor._process_trade_intent(
      runtime,
      TradeIntent(
        strategy_id="104",
        run_id=context.run_id,
        instrument_code="000001.SZ",
        direction=TradeIntentDirection.BUY,
        bucket="swing",
        reason="market_gate_test",
        target_volume=100,
        limit_price_hint=10.0,
      ),
    )

    runtime.broker.place_order.assert_not_awaited()
    assert runtime.strategy.state.order_seen == "REJECTED"
    runtime.status = ExecutionStatus.STOPPED

  @pytest.mark.asyncio
  async def test_missing_whole_quote_gate_fails_closed_for_paper_order(
    self,
    strategy_executor,
  ):
    context = StrategyContext(
      run_id="test-paper-missing-market-gate",
      mode=StrategyRunMode.PAPER,
      instruments=["000001.SZ"],
      parameters={},
      current_time=datetime(2024, 1, 2, 10, 0),
    )
    runtime = strategy_executor.create(
      run_id=context.run_id,
      strategy_id=105,
      strategy_class=PatchCallbackStrategy,
      context=context,
    )
    runtime.strategy = PatchCallbackStrategy(context)
    runtime.data_adapter = SimpleNamespace()
    runtime.broker = SimpleNamespace(place_order=AsyncMock())

    await strategy_executor._process_trade_intent(
      runtime,
      TradeIntent(
        strategy_id="105",
        run_id=context.run_id,
        instrument_code="000001.SZ",
        direction=TradeIntentDirection.BUY,
        bucket="swing",
        reason="missing_market_gate_test",
        target_volume=100,
        limit_price_hint=10.0,
      ),
    )

    runtime.broker.place_order.assert_not_awaited()
    assert runtime.strategy.state.order_seen == "REJECTED"
    runtime.status = ExecutionStatus.STOPPED

  @pytest.mark.asyncio
  async def test_paper_order_ttl_requests_cancel_and_prevents_late_fill(
    self,
    strategy_executor,
  ):
    timestamp = datetime(2024, 1, 2, 10, 0)
    context = StrategyContext(
      run_id="test-paper-order-ttl",
      mode=StrategyRunMode.PAPER,
      instruments=["000001.SZ"],
      parameters={},
      current_time=timestamp,
    )
    runtime = strategy_executor.create(
      run_id=context.run_id,
      strategy_id=103,
      strategy_class=PatchCallbackStrategy,
      context=context,
    )
    broker = SimulatorBroker(delay_mean=0, delay_std=0)
    request = OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=100,
      price=10.0,
      metadata={
        "order_expire_at_ms": int(timestamp.timestamp() * 1000) - 1,
      },
    )
    order = OrderResponse(
      order_id="paper-order-1",
      request=request,
      status=OrderStatus.SUBMITTED,
      submit_time=timestamp,
    )
    broker.orders[order.order_id] = order
    broker.realtime_prices["000001.SZ"] = 10.0
    runtime.broker = broker

    await strategy_executor._cancel_expired_strategy_orders(runtime, timestamp)
    await broker._process_order_async(order)

    assert order.status == OrderStatus.CANCELLED
    assert request.metadata["expiry_cancel_requested"] is True
    assert broker.trades == []

  def test_strategy_input_includes_open_orders_and_broker_health(
    self,
    strategy_executor,
  ):
    run_id = "test-run-open-orders"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
    )
    runtime = strategy_executor.create(
      run_id=run_id,
      strategy_id=104,
      strategy_class=MockStrategy,
      context=context,
    )
    runtime.context.current_time = datetime(2024, 1, 2, 10, 0)
    request = OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=500,
      price=10.0,
      metadata={"intent_id": "intent-1", "bucket": "swing"},
    )
    order = OrderResponse(
      order_id="order-1",
      request=request,
      status=OrderStatus.SUBMITTED,
      submit_time=datetime(2024, 1, 2, 9, 59),
      filled_volume=100,
    )
    runtime.broker = SimpleNamespace(orders={"order-1": order}, pending_orders=[])
    runtime.last_order_report_at = datetime(2024, 1, 2, 9, 59)
    runtime.last_broker_report_at = datetime(2024, 1, 2, 9, 59)

    input_snapshot = strategy_executor._build_strategy_input(
      runtime,
      cadence=StrategyCadence.BAR,
      instrument_code="000001.SZ",
      timestamp=datetime(2024, 1, 2, 10, 0),
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=datetime(2024, 1, 2, 10, 0),
        price=10.0,
        close=10.0,
        limit_up=11.0,
        limit_down=9.0,
      ),
    )

    assert input_snapshot.open_orders == [
      {
        "order_id": "order-1",
        "status": "SUBMITTED",
        "instrument_code": "000001.SZ",
        "order_type": "BUY",
        "price_type": "LIMIT",
        "price": 10.0,
        "volume": 500,
        "filled_volume": 100,
        "remaining_volume": 400,
        "submit_time": "2024-01-02T09:59:00",
        "last_update_time": None,
        "metadata": {"intent_id": "intent-1", "bucket": "swing"},
      }
    ]
    metadata = input_snapshot.risk_caps["metadata"]
    assert metadata["order_state"]["open_order_count"] == 1
    assert metadata["order_state"]["buy_open_order_count"] == 1
    assert metadata["broker_report"]["report_lag_seconds"] == 60.0
    runtime.status = ExecutionStatus.STOPPED

  def test_order_risk_strict_flags_default_by_mode(self, strategy_executor):
    backtest_runtime = strategy_executor.create(
      run_id="strict-backtest",
      strategy_id=105,
      strategy_class=MockStrategy,
      context=StrategyContext(
        run_id="strict-backtest",
        mode=StrategyRunMode.BACKTEST,
        instruments=[],
        parameters={},
      ),
    )
    paper_runtime = strategy_executor.create(
      run_id="strict-paper",
      strategy_id=106,
      strategy_class=MockStrategy,
      context=StrategyContext(
        run_id="strict-paper",
        mode=StrategyRunMode.PAPER,
        instruments=[],
        parameters={},
      ),
    )
    override_runtime = strategy_executor.create(
      run_id="strict-override",
      strategy_id=107,
      strategy_class=MockStrategy,
      context=StrategyContext(
        run_id="strict-override",
        mode=StrategyRunMode.BACKTEST,
        instruments=[],
        parameters={
          "strict_market_data": "false",
          "strict_limit_data": False,
        },
      ),
    )

    assert strategy_executor._order_risk_strict_flags(backtest_runtime) == (True, True)
    assert strategy_executor._order_risk_strict_flags(paper_runtime) == (True, False)
    assert strategy_executor._order_risk_strict_flags(override_runtime) == (
      False,
      False,
    )

  def test_backtest_continuous_session_filter_excludes_call_auction_ticks(
    self,
    strategy_executor,
  ):
    """回测 tick 回放应忽略早盘和尾盘集合竞价。"""
    from types import SimpleNamespace

    events = [
      SimpleNamespace(time=datetime(2026, 5, 14, 9, 15), label="open_call"),
      SimpleNamespace(time=datetime(2026, 5, 14, 9, 25), label="open_call_end"),
      SimpleNamespace(time=datetime(2026, 5, 14, 9, 30), label="morning_open"),
      SimpleNamespace(time=datetime(2026, 5, 14, 11, 30), label="morning_close"),
      SimpleNamespace(time=datetime(2026, 5, 14, 13, 0), label="afternoon_open"),
      SimpleNamespace(time=datetime(2026, 5, 14, 14, 56, 59), label="before_close_call"),
      SimpleNamespace(time=datetime(2026, 5, 14, 14, 57), label="close_call"),
      SimpleNamespace(time=datetime(2026, 5, 14, 15, 0), label="close_call_end"),
    ]

    filtered = strategy_executor._filter_backtest_continuous_session_events(events)

    assert [event.label for event in filtered] == [
      "morning_open",
      "morning_close",
      "afternoon_open",
      "before_close_call",
    ]

  def test_backtest_intraday_period_detection(self, strategy_executor):
    """只有日内周期才按集合竞价过滤，日线不参与过滤。"""
    assert strategy_executor._is_backtest_intraday_period("1m") is True
    assert strategy_executor._is_backtest_intraday_period("60m") is True
    assert strategy_executor._is_backtest_intraday_period("1h") is True
    assert strategy_executor._is_backtest_intraday_period("1d") is False

  @pytest.mark.asyncio
  async def test_create_multiple_runs(self, strategy_executor):
    """测试创建多个策略运行"""
    run_ids = []

    for i in range(3):
      run_id = f"test-run-{i:03d}"
      context = StrategyContext(
        run_id=run_id,
        mode=StrategyRunMode.BACKTEST,
        instruments=[f"00000{i}.SZ"],
        parameters={"index": i},
        initial_capital=1000000.0,
      )

      runtime = strategy_executor.create(
        run_id=run_id,
        strategy_id=i,
        strategy_class=MockStrategy,
        context=context,
      )
      run_ids.append(run_id)

      assert runtime.run_id == run_id
      assert runtime.strategy_id == i

    # 验证所有运行都已创建
    assert len(strategy_executor.runs) == 3
    assert all(rid in strategy_executor.runs for rid in run_ids)

  @pytest.mark.asyncio
  async def test_start_run(self, strategy_executor):
    """测试 start 启动策略运行"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_data_adapter.subscribe_kline = AsyncMock(return_value="subscription-id")
      mock_get_adapter.return_value = mock_data_adapter

      # Mock BacktestBroker
      with patch('quantx_engine.strategy_executor.BacktestBroker') as mock_broker_class:
        mock_broker = AsyncMock()
        mock_broker.connect = AsyncMock()
        mock_broker_class.return_value = mock_broker

        # 创建策略运行
        run_id = "test-run-start"
        context = StrategyContext(
          run_id=run_id,
          mode=StrategyRunMode.BACKTEST,
          instruments=["000001.SZ"],
          parameters={},
          initial_capital=1000000.0,
        )

        runtime = strategy_executor.create(
          run_id=run_id,
          strategy_id=2,
          strategy_class=MockStrategy,
          context=context,
        )

        # 启动策略
        with patch.object(
          strategy_executor,
          "_run_strategy_loop",
          side_effect=keep_running_loop,
        ):
          success = await strategy_executor.start(run_id)
        assert success is True

        # 验证状态
        assert runtime.status == ExecutionStatus.RUNNING
        assert runtime.strategy is not None
        assert runtime.broker is not None
        assert runtime.data_adapter is not None
        assert runtime.task is not None

        # 验证 mock 调用
        mock_get_adapter.assert_called_once_with(StrategyRunMode.BACKTEST)
        mock_broker.connect.assert_called_once()
        mock_data_adapter.connect.assert_called_once()

  @pytest.mark.asyncio
  async def test_start_nonexistent_run(self, strategy_executor):
    """测试启动不存在的策略运行"""
    success = await strategy_executor.start("nonexistent-run-id")
    assert success is False

  @pytest.mark.asyncio
  async def test_stop_run(self, strategy_executor):
    """测试 stop 停止策略运行"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_get_adapter.return_value = mock_data_adapter

      # Mock release_adapter_for_mode
      with patch('quantx_engine.strategy_executor.adapter_manager.release_adapter_for_mode') as mock_release:
        # Mock BacktestBroker
        with patch('quantx_engine.strategy_executor.BacktestBroker') as mock_broker_class:
          mock_broker = AsyncMock()
          mock_broker.connect = AsyncMock()
          mock_broker.disconnect = AsyncMock()
          mock_broker.get_performance_metrics = MagicMock(return_value={
            "final_equity": 1050000.0,
            "max_drawdown": 0.02,
            "win_rate": 0.6,
            "sharpe_ratio": 1.5,
            "total_trades": 10,
          })
          mock_broker_class.return_value = mock_broker

          # 创建并启动策略
          run_id = "test-run-stop"
          context = StrategyContext(
            run_id=run_id,
            mode=StrategyRunMode.BACKTEST,
            instruments=["000001.SZ"],
            parameters={},
            initial_capital=1000000.0,
          )

          runtime = strategy_executor.create(
            run_id=run_id,
            strategy_id=3,
            strategy_class=MockStrategy,
            context=context,
          )

          await strategy_executor.start(run_id)
          await asyncio.sleep(0.1)  # 等待启动

          # 停止策略
          success = await strategy_executor.stop(run_id)
          assert success is True

          # 验证状态
          assert runtime.status == ExecutionStatus.STOPPED
          assert runtime.metrics.end_time is not None

          # 验证资源清理
          mock_broker.disconnect.assert_called_once()
          mock_release.assert_called_once_with("backtest")

  @pytest.mark.asyncio
  async def test_pause_and_resume_run(self, strategy_executor):
    """测试 pause 和 resume"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_data_adapter.subscribe_kline = AsyncMock(return_value="subscription-id")
      mock_data_adapter.unsubscribe = AsyncMock(return_value=True)
      mock_get_adapter.return_value = mock_data_adapter

      # Mock SimulatorBroker (PAPER 模式)
      with patch('quantx_engine.strategy_executor.SimulatorBroker') as mock_broker_class:
        mock_broker = AsyncMock()
        mock_broker.connect = AsyncMock()
        mock_broker_class.return_value = mock_broker

        # 创建并启动策略 (模拟盘模式)
        run_id = "test-run-pause-resume"
        context = StrategyContext(
          run_id=run_id,
          mode=StrategyRunMode.PAPER,
          instruments=["600519.SH"],
          parameters={},
          initial_capital=1000000.0,
        )

        runtime = strategy_executor.create(
          run_id=run_id,
          strategy_id=4,
          strategy_class=MockStrategy,
          context=context,
        )

        await strategy_executor.start(run_id)
        await asyncio.sleep(0.1)

        # 暂停策略
        success = await strategy_executor.pause(run_id)
        assert success is True
        assert runtime.status == ExecutionStatus.PAUSED

        # 恢复策略
        success = await strategy_executor.resume(run_id)
        assert success is True
        assert runtime.status == ExecutionStatus.RUNNING

  @pytest.mark.asyncio
  async def test_delete_run(self, strategy_executor):
    """测试 delete 删除策略运行"""
    # 创建策略运行
    run_id = "test-run-delete"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
      initial_capital=1000000.0,
    )

    strategy_executor.create(
      run_id=run_id,
      strategy_id=5,
      strategy_class=MockStrategy,
      context=context,
    )

    assert run_id in strategy_executor.runs

    # 删除策略运行
    success = await strategy_executor.delete(run_id)
    assert success is True

    # 验证运行时对象已删除
    assert run_id not in strategy_executor.runs

  @pytest.mark.asyncio
  async def test_delete_nonexistent_run(self, strategy_executor):
    """测试删除不存在的策略运行"""
    success = await strategy_executor.delete("nonexistent-run-id")
    assert success is False

  @pytest.mark.asyncio
  async def test_get_run(self, strategy_executor):
    """测试 get 获取策略运行"""
    # 创建策略运行
    run_id = "test-run-get"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
      initial_capital=1000000.0,
    )

    strategy_executor.create(
      run_id=run_id,
      strategy_id=6,
      strategy_class=MockStrategy,
      context=context,
    )

    # 获取策略运行
    runtime = strategy_executor.get(run_id)
    assert runtime is not None
    assert runtime.run_id == run_id

    # 获取不存在的策略运行
    nonexistent = strategy_executor.get("nonexistent-run-id")
    assert nonexistent is None

  @pytest.mark.asyncio
  async def test_get_all_runs(self, strategy_executor):
    """测试 get_all 获取所有运行"""
    # 创建多个策略运行
    for i in range(3):
      run_id = f"test-run-all-{i}"
      context = StrategyContext(
        run_id=run_id,
        mode=StrategyRunMode.BACKTEST,
        instruments=[f"00000{i}.SZ"],
        parameters={},
        initial_capital=1000000.0,
      )

      strategy_executor.create(
        run_id=run_id,
        strategy_id=i,
        strategy_class=MockStrategy,
        context=context,
      )

    # 获取所有运行
    all_runs = strategy_executor.get_all()
    assert len(all_runs) == 3
    assert all(isinstance(r, StrategyRuntime) for r in all_runs)

  @pytest.mark.asyncio
  async def test_get_running_runs(self, strategy_executor):
    """测试 get_running 获取运行中的策略"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_get_adapter.return_value = mock_data_adapter

      # Mock BacktestBroker
      with patch('quantx_engine.strategy_executor.BacktestBroker') as mock_broker_class:
        mock_broker = AsyncMock()
        mock_broker.connect = AsyncMock()
        mock_broker_class.return_value = mock_broker

        # 创建多个运行，部分启动
        run_ids = []
        for i in range(3):
          run_id = f"test-run-running-{i}"
          context = StrategyContext(
            run_id=run_id,
            mode=StrategyRunMode.BACKTEST,
            instruments=[f"00000{i}.SZ"],
            parameters={},
            initial_capital=1000000.0,
          )

          strategy_executor.create(
            run_id=run_id,
            strategy_id=i,
            strategy_class=MockStrategy,
            context=context,
          )
          run_ids.append(run_id)

        # 只启动前两个
        with patch.object(strategy_executor, "_run_strategy_loop", side_effect=keep_running_loop):
          await strategy_executor.start(run_ids[0])
          await strategy_executor.start(run_ids[1])
          await asyncio.sleep(0.1)

          # 获取运行中的策略
          running_runs = strategy_executor.get_running()
          assert len(running_runs) == 2
          assert all(r.status == ExecutionStatus.RUNNING for r in running_runs)

  @pytest.mark.asyncio
  async def test_multiple_runs_concurrent(self, strategy_executor):
    """测试多个策略并发运行"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_get_adapter.return_value = mock_data_adapter

      # Mock BacktestBroker
      with patch('quantx_engine.strategy_executor.BacktestBroker') as mock_broker_class:
        mock_broker = AsyncMock()
        mock_broker.connect = AsyncMock()
        mock_broker.disconnect = AsyncMock()
        # Mock get_performance_metrics 为同步方法
        mock_broker.get_performance_metrics = MagicMock(return_value={})
        mock_broker_class.return_value = mock_broker

        # Mock release_adapter_for_mode
        with patch('quantx_engine.strategy_executor.adapter_manager.release_adapter_for_mode'):
          # 创建多个策略运行
          run_ids = []
          for i in range(3):
            run_id = f"test-run-concurrent-{i}"
            context = StrategyContext(
              run_id=run_id,
              mode=StrategyRunMode.BACKTEST,
              instruments=[f"00000{i}.SZ"],
              parameters={"run_id": i},
              initial_capital=1000000.0,
            )

            strategy_executor.create(
              run_id=run_id,
              strategy_id=10 + i,
              strategy_class=MockStrategy,
              context=context,
            )
            run_ids.append(run_id)

          # 并发启动所有策略
          start_tasks = [
            strategy_executor.start(run_id)
            for run_id in run_ids
          ]
          results = await asyncio.gather(*start_tasks)

          # 验证所有策略都启动成功
          assert all(results) is True

          # 等待运行
          await asyncio.sleep(0.2)

          # 并发停止所有策略
          stop_tasks = [
            strategy_executor.stop(run_id)
            for run_id in run_ids
          ]
          results = await asyncio.gather(*stop_tasks)

          # 验证所有策略都停止成功
          assert all(results) is True

  @pytest.mark.asyncio
  async def test_metrics_update_on_stop(self, strategy_executor):
    """测试停止时更新指标"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_get_adapter.return_value = mock_data_adapter

      # Mock release_adapter_for_mode
      with patch('quantx_engine.strategy_executor.adapter_manager.release_adapter_for_mode'):
        # Mock BacktestBroker with performance metrics
        with patch('quantx_engine.strategy_executor.BacktestBroker') as mock_broker_class:
          mock_broker = AsyncMock()
          mock_broker.connect = AsyncMock()
          mock_broker.disconnect = AsyncMock()
          mock_broker.get_performance_metrics = MagicMock(return_value={
            "final_equity": 1050000.0,
            "max_drawdown": 0.02,
            "win_rate": 0.6,
            "sharpe_ratio": 1.5,
            "total_trades": 10,
          })
          mock_broker_class.return_value = mock_broker

          # 创建并启动策略
          run_id = "test-run-metrics"
          context = StrategyContext(
            run_id=run_id,
            mode=StrategyRunMode.BACKTEST,
            instruments=["000001.SZ"],
            parameters={},
            initial_capital=1000000.0,
          )

          runtime = strategy_executor.create(
            run_id=run_id,
            strategy_id=7,
            strategy_class=MockStrategy,
            context=context,
          )

          await strategy_executor.start(run_id)
          await asyncio.sleep(0.1)

          # 停止策略
          await strategy_executor.stop(run_id)

          # 验证指标已更新
          assert runtime.metrics is not None
          assert runtime.metrics.end_time is not None
          assert runtime.metrics.total_pnl == 50000.0  # 1050000 - 1000000
          assert runtime.metrics.max_drawdown == 0.02
          assert runtime.metrics.win_rate == 0.6
          assert runtime.metrics.sharpe_ratio == 1.5
          assert runtime.metrics.trades_executed == 10
          assert runtime.metrics.current_capital == 1050000.0

  @pytest.mark.asyncio
  async def test_error_handling_on_start(self, strategy_executor):
    """测试启动时的错误处理"""
    # Mock 数据适配器抛出异常
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_get_adapter.side_effect = Exception("数据适配器错误")

      # 创建策略
      run_id = "test-run-error"
      context = StrategyContext(
        run_id=run_id,
        mode=StrategyRunMode.BACKTEST,
        instruments=["000001.SZ"],
        parameters={},
        initial_capital=1000000.0,
      )

      runtime = strategy_executor.create(
        run_id=run_id,
        strategy_id=8,
        strategy_class=MockStrategy,
        context=context,
      )

      # 启动应该失败
      success = await strategy_executor.start(run_id)
      assert success is False

      # 验证错误状态
      assert runtime.status == ExecutionStatus.ERROR
      assert runtime.error_message is not None
      assert "数据适配器错误" in runtime.error_message

  @pytest.mark.asyncio
  async def test_different_modes(self, strategy_executor):
    """测试不同运行模式"""
    modes = [
      (StrategyRunMode.BACKTEST, "BacktestBroker"),
      (StrategyRunMode.PAPER, "SimulatorBroker"),
      (StrategyRunMode.LIVE, "LiveBroker"),
    ]

    for mode, broker_class in modes:
      with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
        mock_data_adapter = AsyncMock()
        mock_data_adapter.connect = AsyncMock()
        mock_get_adapter.return_value = mock_data_adapter

        # Mock 对应的 broker
        broker_patch_path = f"quantx_engine.strategy_executor.{broker_class}"
        with patch(broker_patch_path) as mock_broker_class:
          mock_broker = AsyncMock()
          mock_broker.connect = AsyncMock()
          mock_broker_class.return_value = mock_broker

          # 创建策略
          run_id = f"test-run-{mode.value.lower()}"
          context = StrategyContext(
            run_id=run_id,
            mode=mode,
            instruments=["000001.SZ"],
            parameters={},
            initial_capital=1000000.0,
          )

          runtime = strategy_executor.create(
            run_id=run_id,
            strategy_id=100 + modes.index((mode, broker_class)),
            strategy_class=MockStrategy,
            context=context,
          )

          # 启动策略
          with patch.object(strategy_executor, "_run_strategy_loop", side_effect=keep_running_loop):
            await strategy_executor.start(run_id)
            await asyncio.sleep(0.1)

            # 验证运行时对象模式
            assert runtime.context.mode == mode
            assert runtime.status == ExecutionStatus.RUNNING

            # 验证正确的 broker 被创建
            mock_broker_class.assert_called_once()

            # 验证正确的适配器被获取
            mock_get_adapter.assert_called_with(mode)

  @pytest.mark.asyncio
  async def test_paper_setup_uses_simulator_broker_not_live(
    self,
    strategy_executor: StrategyExecutor,
  ):
    """PAPER 模式只应创建 SimulatorBroker，不触发实盘 Broker。"""
    context = StrategyContext(
      run_id="paper-run",
      mode=StrategyRunMode.PAPER,
      instruments=["688552.SH"],
      parameters={},
      initial_capital=250000.0,
    )
    runtime = StrategyRuntime(
      run_id="paper-run",
      name="Paper Run",
      strategy_id=1,
      strategy_class=MockStrategy,
      context=context,
    )

    with (
      patch("quantx_engine.strategy_executor.LiveBroker") as live_broker,
      patch("quantx_engine.strategy_executor.SimulatorBroker") as simulator_broker,
      patch("quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode") as get_adapter,
    ):
      broker = AsyncMock()
      broker.connect = AsyncMock(return_value=True)
      broker.subscribe_order_updates = MagicMock()
      broker.subscribe_trade_updates = MagicMock()
      simulator_broker.return_value = broker

      adapter = AsyncMock()
      adapter.connect = AsyncMock(return_value=True)
      get_adapter.return_value = adapter

      await strategy_executor._setup_broker_and_data(runtime)

    simulator_broker.assert_called_once_with(
      account_id="paper-run",
      initial_capital=250000.0,
    )
    live_broker.assert_not_called()

  @pytest.mark.asyncio
  async def test_paper_broker_seeds_initial_holdings(
    self,
    strategy_executor: StrategyExecutor,
  ):
    """模拟盘 broker 应从策略参数注入初始虚拟持仓。"""
    run_id = "paper-seed-run"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.PAPER,
      instruments=["688552.SH"],
      parameters={
        "instrument_code": "688552.SH",
        "position_shares": 500,
        "locked_core_shares": 100,
        "core_shares": 200,
        "swing_shares": 200,
        "avg_cost": 40.0,
        "base_price": 42.0,
      },
      initial_capital=100000.0,
    )
    runtime = strategy_executor.create(
      run_id=run_id,
      strategy_id=2,
      strategy_class=MockStrategy,
      context=context,
    )

    with (
      patch("quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode") as get_adapter,
      patch.object(
        strategy_executor,
        "_run_strategy_loop",
        side_effect=keep_running_loop,
      ),
    ):
      adapter = AsyncMock()
      adapter.connect = AsyncMock(return_value=True)
      get_adapter.return_value = adapter

      success = await strategy_executor.start(run_id)

    assert success is True
    assert runtime.broker is not None
    position = runtime.broker.positions["688552.SH"]
    assert position.long_volume == 500
    assert position.available_volume == 500
    assert position.long_avg_price == 40.0
    assert position.last_price == 42.0
    account = await runtime.broker.get_account()
    assert account.cash == 100000.0
    assert account.total_asset == 121000.0

  @pytest.mark.asyncio
  async def test_realtime_loop_subscribes_context_instruments(
    self,
    strategy_executor: StrategyExecutor,
  ):
    """实时模式订阅应优先使用 context.instruments。"""
    context = StrategyContext(
      run_id="paper-context-instrument",
      mode=StrategyRunMode.PAPER,
      instruments=["688552.SH"],
      parameters={},
      initial_capital=100000.0,
    )
    adapter = AsyncMock()
    adapter.subscribe_kline = AsyncMock(return_value="sub-001")
    adapter.unsubscribe = AsyncMock(return_value=True)
    broker = AsyncMock()
    broker.get_position = AsyncMock(return_value={})
    broker.get_account = AsyncMock(
      return_value=SimpleNamespace(
        cash=100000.0,
        total_asset=100000.0,
        frozen_cash=0.0,
        market_value=0.0,
        total_pnl=0.0,
        daily_pnl=0.0,
      )
    )
    runtime = SimpleNamespace(
      run_id="paper-context-instrument",
      status=ExecutionStatus.RUNNING,
      context=context,
      data_adapter=adapter,
      broker=broker,
      event_queue=asyncio.Queue(),
      latest_market_data={},
      metrics=SimpleNamespace(last_heartbeat=None),
      realtime_subscription_ids={},
      realtime_subscription_lock=asyncio.Lock(),
      state_manager=None,
      strategy=None,
    )

    async def stop_after_heartbeat(_seconds):
      runtime.status = ExecutionStatus.STOPPED

    with patch("quantx_engine.strategy_executor.asyncio.sleep", side_effect=stop_after_heartbeat):
      await strategy_executor._run_realtime_loop(runtime)

    adapter.subscribe_kline.assert_awaited_once()
    assert adapter.subscribe_kline.await_args.kwargs["instrument_code"] == "688552.SH"
    adapter.unsubscribe.assert_awaited_once_with("sub-001")

  @pytest.mark.asyncio
  async def test_stop_all_runs(self, strategy_executor):
    """测试停止所有运行"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_get_adapter.return_value = mock_data_adapter

      # Mock BacktestBroker
      with patch('quantx_engine.strategy_executor.BacktestBroker') as mock_broker_class:
        mock_broker = AsyncMock()
        mock_broker.connect = AsyncMock()
        mock_broker.disconnect = AsyncMock()
        # Mock get_performance_metrics 为同步方法
        mock_broker.get_performance_metrics = MagicMock(return_value={})
        mock_broker_class.return_value = mock_broker

        # Mock release_adapter_for_mode
        with patch('quantx_engine.strategy_executor.adapter_manager.release_adapter_for_mode'):
          # 创建并启动多个策略
          run_ids = []
          for i in range(3):
            run_id = f"test-run-stopall-{i}"
            context = StrategyContext(
              run_id=run_id,
              mode=StrategyRunMode.BACKTEST,
              instruments=[f"00000{i}.SZ"],
              parameters={},
              initial_capital=1000000.0,
            )

            strategy_executor.create(
              run_id=run_id,
              strategy_id=20 + i,
              strategy_class=MockStrategy,
              context=context,
            )
            run_ids.append(run_id)

          # 启动所有策略
          for run_id in run_ids:
            await strategy_executor.start(run_id)
          await asyncio.sleep(0.1)

          # 停止所有运行
          await strategy_executor.stop_all_runs()

          # 验证所有策略都已停止
          for run_id in run_ids:
            runtime = strategy_executor.get(run_id)
            assert runtime.status == ExecutionStatus.STOPPED

  @pytest.mark.asyncio
  async def test_get_statistics(self, strategy_executor):
    """测试获取执行器统计信息"""
    # 创建不同状态的运行
    run_id_1 = "test-run-stats-1"
    context_1 = StrategyContext(
      run_id=run_id_1,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
      initial_capital=1000000.0,
    )
    strategy_executor.create(
      run_id=run_id_1,
      strategy_id=30,
      strategy_class=MockStrategy,
      context=context_1,
    )

    run_id_2 = "test-run-stats-2"
    context_2 = StrategyContext(
      run_id=run_id_2,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000002.SZ"],
      parameters={},
      initial_capital=1000000.0,
    )
    strategy_executor.create(
      run_id=run_id_2,
      strategy_id=31,
      strategy_class=MockStrategy,
      context=context_2,
    )

    # 获取统计信息
    stats = strategy_executor.get_statistics()
    assert stats["total_runs"] == 2
    assert stats["max_workers"] == 2
    assert "status_distribution" in stats
    assert stats["status_distribution"]["PENDING"] == 2
    assert stats["running_runs"] == 0
