from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from quantx_domain.strategies.base import (
  StrategyBase,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_engine.strategy_executor import ExecutionStatus, StrategyExecutor
from quantx_engine.strategy_manager import StrategyManager
from quantx_infrastructure.core.runtime_state_manager import (
  RUNTIME_RECONCILIATION_STATUS_KEY,
  RuntimeStateManager,
  RuntimeStateRestoreError,
)


class RestoreGateStrategy(StrategyBase):
  @property
  def name(self) -> str:
    return "restore-gate-test"

  @property
  def description(self) -> str:
    return "restore-gate-test"

  @property
  def version(self) -> str:
    return "1"

  @classmethod
  def get_parameter_schema(cls) -> dict:
    return {"type": "object", "properties": {}, "required": []}

  async def on_init(self) -> None:
    return None

  async def on_stop(self) -> None:
    return None

  async def step(self, _input: StrategyInput) -> StrategyOutput:
    return StrategyOutput()


def _intent(
  run_id: str,
  *,
  execution_mode: TradeIntentExecutionMode,
) -> TradeIntent:
  return TradeIntent(
    strategy_id="1",
    run_id=run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="restore-reconcile-gate-test",
    target_volume=100,
    execution_mode=execution_mode,
  )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [StrategyRunMode.PAPER, StrategyRunMode.LIVE])
async def test_restore_failure_never_starts_snapshot_broker_or_market_subscription(
  monkeypatch: pytest.MonkeyPatch,
  mode: StrategyRunMode,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  run_id = f"restore-failure-{mode.value.lower()}"
  runtime = executor.create(
    run_id=run_id,
    strategy_id=1,
    strategy_class=RestoreGateStrategy,
    context=StrategyContext(
      run_id=run_id,
      mode=mode,
      instruments=["600000.SH"],
      parameters={},
    ),
  )
  setup_broker_and_data = AsyncMock()
  monkeypatch.setattr(executor, "_setup_broker_and_data", setup_broker_and_data)
  assert runtime.log_manager is not None
  attach_handler = MagicMock()
  detach_handler = MagicMock()
  monkeypatch.setattr(runtime.log_manager, "attach_handler", attach_handler)
  monkeypatch.setattr(runtime.log_manager, "detach_handler", detach_handler)

  async def fail_restore(_manager):
    raise RuntimeStateRestoreError("database unavailable")

  monkeypatch.setattr(RuntimeStateManager, "restore", fail_restore)

  assert await executor.start(run_id) is False
  assert runtime.status == ExecutionStatus.ERROR
  assert runtime.state_manager is not None
  assert runtime.state_manager._running is False
  assert runtime.state_manager._snapshot_task is None
  assert runtime.broker is None
  assert runtime.data_adapter is None
  assert runtime.task is None
  assert runtime.event_task is None
  setup_broker_and_data.assert_not_awaited()
  attach_handler.assert_called_once()
  detach_handler.assert_called_once_with(
    run_id=run_id,
    logger=runtime.strategy.logger,
  )
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_bucket_reconcile_gate_blocks_output_direct_route_and_manual_approval(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  run_id = "bucket-reconcile-gate"
  runtime = executor.create(
    run_id=run_id,
    strategy_id=1,
    strategy_class=RestoreGateStrategy,
    context=StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.LIVE,
      instruments=["600000.SH"],
      parameters={},
    ),
  )
  runtime.status = ExecutionStatus.RUNNING
  runtime.strategy = RestoreGateStrategy(runtime.context)
  runtime.state_manager = RuntimeStateManager(
    run_id=run_id,
    persist_enabled=False,
  )
  runtime.state_manager._state["custom"][
    RUNTIME_RECONCILIATION_STATUS_KEY
  ] = "RECONCILE_REQUIRED"
  record_trade_intent = AsyncMock()
  monkeypatch.setattr(
    runtime.state_manager,
    "record_trade_intent",
    record_trade_intent,
  )

  auto_intent = _intent(
    run_id,
    execution_mode=TradeIntentExecutionMode.AUTO,
  )
  await executor._process_strategy_output(
    runtime,
    StrategyOutput(trade_intents=[auto_intent]),
  )
  record_trade_intent.assert_not_awaited()
  assert runtime.pending_approvals == {}

  runtime.broker = type(
    "Broker",
    (),
    {"place_order": AsyncMock()},
  )()
  await executor._process_trade_intent(runtime, auto_intent)
  runtime.broker.place_order.assert_not_awaited()

  manual_intent = _intent(
    run_id,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
  )
  runtime.pending_approvals[manual_intent.intent_id] = manual_intent
  result = await executor.approve_trade_intent(
    run_id,
    manual_intent.intent_id,
  )
  assert result == {
    "success": False,
    "code": "RUNTIME_RECONCILE_REQUIRED",
    "message": "持仓与 Bucket 账本不一致，等待权威对账后才能继续交易",
  }
  assert runtime.pending_approvals[manual_intent.intent_id] is manual_intent
  runtime.broker.place_order.assert_not_awaited()
  executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
async def test_restore_runs_logs_failed_start_and_continues_with_next_run(
  caplog: pytest.LogCaptureFixture,
) -> None:
  StrategyManager._instance = None
  manager = StrategyManager()
  runs = [
    SimpleNamespace(
      id=run_id,
      name=run_id,
      strategy_id=index,
      strategy=SimpleNamespace(
        class_name="RestoreGateStrategy",
        file_path="",
      ),
      parameters={},
      mode=StrategyRunMode.LIVE,
      status="RUNNING",
      instruments=["600000.SH"],
      initial_capital=100_000.0,
      metrics=None,
    )
    for index, run_id in enumerate(("restore-failed", "restore-succeeded"), 1)
  ]
  run_repo = AsyncMock()
  run_repo.find_all_active_runs.return_value = runs

  async def fake_get_async_db():
    yield AsyncMock()

  async def start_result(run_id: str) -> bool:
    if run_id == "restore-failed":
      runtime = manager.executor.get(run_id)
      assert runtime is not None
      runtime.status = ExecutionStatus.ERROR
      runtime.error_message = "durable restore failed"
      return False
    return True

  start_strategy = AsyncMock(side_effect=start_result)
  caplog.set_level(logging.INFO, logger="StrategyManager")
  with (
    patch("quantx_engine.strategy_manager.get_async_db", fake_get_async_db),
    patch(
      "quantx_engine.strategy_manager.StrategyRunRepository",
      return_value=run_repo,
    ),
    patch(
      "quantx_engine.strategy_manager.strategy_registry.get_strategy_class",
      return_value=RestoreGateStrategy,
    ),
    patch.object(manager, "start_strategy", start_strategy),
  ):
    await manager._restore_runs()

  assert start_strategy.await_args_list == [
    call("restore-failed"),
    call("restore-succeeded"),
  ]
  assert "策略运行 restore-failed 恢复启动失败: durable restore failed" in caplog.text
  assert "策略运行 restore-failed 恢复并启动成功" not in caplog.text
  assert "策略运行 restore-succeeded 恢复并启动成功" in caplog.text

  manager.executor.runs.clear()
  manager.executor.thread_pool.shutdown(wait=False)
  StrategyManager._instance = None
