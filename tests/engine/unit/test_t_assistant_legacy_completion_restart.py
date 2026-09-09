"""Durable consumer restart without a loaded runtime must still recheck obligations."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from quantx_engine import command_processor
from quantx_engine.strategy_executor import StrategyExecutor
from quantx_infrastructure.models.agent_runtime import (
  AgentReportInbox,
  EngineCommandOutbox,
  TTradeRolloutEvent,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig

from tests.engine.unit.test_t_assistant_legacy_completion_command import seed_command
from tests.infrastructure.test_t_entry_confirmation import signing_key as _signing_key

signing_key = _signing_key


@pytest.mark.asyncio
@pytest.mark.parametrize("late_report", [False, True])
async def test_restart_after_cut_revalidates_without_runtime(monkeypatch, late_report):
  engine, sessions, clock, _ = await seed_command(monkeypatch)
  stopped = asyncio.Event()
  executor = StrategyExecutor.__new__(StrategyExecutor)
  executor.runs = {}  # Restart has no old in-memory runtime to trigger its recheck.
  monkeypatch.setattr(command_processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(command_processor, "utcnow", lambda: clock.replace(tzinfo=None))
  monkeypatch.setattr(
    command_processor, "strategy_manager", SimpleNamespace(executor=executor)
  )
  try:
    claimed = await command_processor._claim_next()
    original = await command_processor._dispatch(
      claimed[1], claimed[2], command_id=claimed[0]
    )
    # Crash after committed cut, before recording command success. Do not call
    # _complete: the actual new consumer must recover the PROCESSING row.
    async with sessions() as db, db.begin():
      assert (
        await db.get(EngineCommandOutbox, "completion")
      ).processing_status == "PROCESSING"
      if late_report:
        db.add(
          AgentReportInbox(
            message_id="late",
            device_id="device-1",
            message_type="execution_report",
            raw_payload_hash="a" * 64,
            business_idempotency_key="late",
            payload={},
            received_at=clock.replace(tzinfo=None),
            processing_status="PENDING",
          )
        )
    retries = 0
    reschedule = command_processor._reschedule_legacy_drain

    async def deferred(message_id, **kwargs):
      nonlocal clock, retries
      retries += 1
      assert kwargs["completion_reason"] == "LEGACY_T_COMPLETION_OBLIGATIONS_PENDING"
      await reschedule(message_id, **kwargs)
      async with sessions() as db, db.begin():
        command = await db.get(EngineCommandOutbox, message_id)
        assert command.processing_status == "PENDING" and command.processed_at is None
        assert command.result is None
        # Synthetic report convergence boundary; this test covers durable restart
        # and command completion, not broker report parsing/application.
        report = await db.get(AgentReportInbox, "late")
        report.processing_status = "PROCESSED"
        report.processed_at = clock.replace(tzinfo=None)
      clock += timedelta(seconds=2)

    monkeypatch.setattr(command_processor, "_reschedule_legacy_drain", deferred)
    finish = command_processor._complete

    async def completed(message_id, **kwargs):
      await finish(message_id, **kwargs)
      stopped.set()

    monkeypatch.setattr(command_processor, "_complete", completed)
    await asyncio.wait_for(command_processor.run_command_consumer(stopped), timeout=5)
    assert retries == int(late_report)
    async with sessions() as db:
      command = await db.get(EngineCommandOutbox, "completion")
      assert command.processing_status == "SUCCEEDED"
      assert command.processing_attempts == 2 + int(late_report)
      assert command.result == original
      assert (await db.get(TTradeGlobalConfig, "head")).state_version == 3
      cut = await db.get(TTradeRolloutEvent, "legacy-t-completed:plan-1")
      assert cut.details["evidence_hash"] == original["evidence_hash"]
      plan = await db.get(AutoExitPlanRecord, "exit-1")
      assert plan.status == "ACTIVE" and plan.remaining_volume == 40
      assert plan.source_execution_owner_id == "plan-1"
  finally:
    stopped.set()
    await engine.dispose()
