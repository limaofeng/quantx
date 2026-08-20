from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.base import (
  StrategyCadence,
  StrategyContext,
  StrategyRunMode,
)
from quantx_domain.trading import MarketDataSnapshot
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)


@pytest.mark.asyncio
async def test_manual_entry_event_reaches_reconcile_step_and_output_chain(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  runtime = StrategyRuntime(
    run_id="plan-1",
    name="managed-entry",
    strategy_id=81,
    strategy_class=object,
    context=StrategyContext(
      run_id="plan-1",
      mode=StrategyRunMode.PAPER,
      instruments=["605499.SH"],
      parameters={"entry_plan_enabled": True},
    ),
  )
  runtime.status = ExecutionStatus.RUNNING
  observed: dict[str, object] = {}
  strategy_output = SimpleNamespace(trade_intents=[])

  class Strategy:
    async def step(self, strategy_input: object) -> object:
      observed["step_input"] = strategy_input
      return strategy_output

  runtime.strategy = Strategy()
  built_input = SimpleNamespace(cadence=StrategyCadence.RECONCILE)

  def build_strategy_input(
    _runtime: StrategyRuntime,
    **kwargs: object,
  ) -> object:
    observed["build_kwargs"] = kwargs
    return built_input

  process_output = AsyncMock()
  monkeypatch.setattr(executor, "_build_strategy_input", build_strategy_input)
  monkeypatch.setattr(executor, "_process_strategy_output", process_output)

  timestamp = datetime(2026, 8, 20, 10, 15)
  market_data = MarketDataSnapshot(
    instrument_code="605499.SH",
    timestamp=timestamp,
    price=125.0,
    ask_price=[125.01],
  )
  event = {
    "type": "ENTRY_PLAN_MANUAL_TRIGGER",
    "rule_id": "manual-1",
    "instrument_code": "605499.SH",
    "market_data": market_data,
  }
  await runtime.event_queue.put(("entry_plan_evaluate", event))
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  try:
    await asyncio.wait_for(runtime.event_queue.join(), timeout=1.0)
  finally:
    runtime.status = ExecutionStatus.STOPPED
    await asyncio.wait_for(runtime.event_task, timeout=2.0)
    executor.thread_pool.shutdown(wait=False)

  assert observed["step_input"] is built_input
  assert observed["build_kwargs"] == {
    "cadence": StrategyCadence.RECONCILE,
    "instrument_code": "605499.SH",
    "timestamp": timestamp,
    "market_data": market_data,
    "event": event,
  }
  process_output.assert_awaited_once_with(runtime, strategy_output, built_input)
