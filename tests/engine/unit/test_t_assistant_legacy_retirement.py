"""Certified retirement uses actual lifecycle cleanup with isolated resource ports."""

import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from quantx_domain.strategies import AshareIntradayTAssistantStrategy
from quantx_engine import strategy_executor as module
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord

from tests.engine.unit.test_t_assistant_legacy_completion import (
  complete,
  seed_completion,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "state", ["ready", "no_cut", "new_obligation", "wrong_account"]
)
async def test_retirement_requires_committed_and_current_proof(monkeypatch, state):
  engine, sessions, clock = await seed_completion(monkeypatch)

  class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
      return clock.astimezone(tz) if tz else clock.replace(tzinfo=None)

  monkeypatch.setattr(module, "datetime", FrozenDateTime)
  monkeypatch.setattr(module, "AsyncSessionLocal", sessions)
  executor = module.StrategyExecutor.__new__(module.StrategyExecutor)
  executor.logger = logging.getLogger("legacy-retirement-test")
  executor._shutdown_event = asyncio.Event()
  executor.candidate_outcome_facade = None
  executor.opportunity_observability = SimpleNamespace(forget_run=Mock())
  # Diagnostics/checkpoint ports are synthetic; task/subscription teardown and
  # strategy/broker cleanup below run through the real _stop_runtime method.
  executor._coordinate_terminal_session_checkpoint = AsyncMock()
  executor._coordinate_backtest_terminal_checkpoint = AsyncMock()
  executor._flush_t_trade_opportunity_diagnostics = AsyncMock()
  executor._runtime_lifecycle_blocker = Mock(return_value="ACTIVE_EXIT_PLAN")
  strategy = SimpleNamespace(stop=AsyncMock(), logger=executor.logger)
  broker = SimpleNamespace(
    disconnect=AsyncMock(), cancel_order=AsyncMock(), place_order=AsyncMock()
  )
  runtime = module.StrategyRuntime(
    run_id="plan-1",
    name="old T",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=SimpleNamespace(
      mode=module.StrategyRunMode.LIVE, parameters={"account_id": "account-1"}
    ),
    strategy=strategy,
    broker=broker,
    legacy_t_draining=True,
  )
  runtime.status = module.ExecutionStatus.RUNNING
  executor.runs = {runtime.run_id: runtime}
  try:
    if state != "no_cut":
      async with sessions() as db, db.begin():
        await complete(db, clock)
    if state == "new_obligation":
      async with sessions() as db, db.begin():
        (await db.get(PendingTradeOrder, "client-1")).status = "UNKNOWN"
    success = await executor.retire_completed_legacy_run(
      "plan-1",
      account_id="other" if state == "wrong_account" else "account-1",
    )
    assert success is (state == "ready")
    if state == "ready":
      assert runtime.status == module.ExecutionStatus.STOPPED
      strategy.stop.assert_awaited_once()
      broker.disconnect.assert_awaited_once()
      assert await executor.retire_completed_legacy_run(
        "plan-1", account_id="account-1"
      )
      broker.disconnect.assert_awaited_once()
    else:
      assert runtime.status == module.ExecutionStatus.RUNNING
      strategy.stop.assert_not_awaited()
      broker.disconnect.assert_not_awaited()
    broker.cancel_order.assert_not_awaited()
    broker.place_order.assert_not_awaited()
    async with sessions() as db:
      plan = await db.get(AutoExitPlanRecord, "exit-1")
      assert plan.status == "ACTIVE" and plan.remaining_volume == 40
      assert plan.source_execution_owner_id == "plan-1"
  finally:
    await engine.dispose()
