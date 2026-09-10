"""Only allocation/entry ports are spies; scope/audit storage is isolated SQLite."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_engine.t_assistant_live_supervisor import TAssistantLiveSupervisor
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import select

from tests.engine.unit.test_t_assistant_live_supervisor import (
  NOW,
  UNIVERSE,
  FakeWholeQuoteHub,
  seed,
  sessions,
)

_FIXTURES = sessions


@pytest.fixture
async def ready(sessions):
  key = await seed(sessions)
  hub = FakeWholeQuoteHub()
  supervisor = TAssistantLiveSupervisor(quote_hub=hub, session_factory=sessions, clock=lambda: NOW)
  await supervisor.start()
  await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
  async with sessions() as db, db.begin():
    row = await db.get(TAssistantExecutionRecord, key)
    row.status, row.started_at = "RUNNING", NOW
    row.entry_readiness, row.entry_readiness_reasons = "READY", []
    row.state_version += 1
    await db.flush()
    supervisor._bindings[key].execution = await TAssistantExecutionRepository(db).get_domain(key)
  binding = supervisor._bindings[key]
  binding.ready_market_identity = (hub.stream_id, str(hub.generation))
  binding.rewarm.clear()
  supervisor.runtime.run_cycle = AsyncMock(return_value=SimpleNamespace(
    committed=True, cycle_id="cycle",
    output=SimpleNamespace(trade_intents=(), symbol_state_patches=()),
  ))
  supervisor._try_activate = AsyncMock()
  supervisor.allocation_runtime.dispatch = AsyncMock()
  supervisor.entry_runtime.dispatch = AsyncMock()
  try:
    yield supervisor, key, hub
  finally:
    await supervisor.stop()


async def test_pending_triggers_use_latest_fence_and_flush_without_another_quote(ready, sessions):
  supervisor, key, hub = ready
  await hub.emit(1)
  await hub.emit(2)
  timer = supervisor._allocation_timers[key]
  await hub.emit(3)
  assert supervisor.runtime.run_cycle.await_count == 3
  assert supervisor.allocation_runtime.dispatch.await_count == 1
  assert supervisor._allocation_timers[key] is timer
  await asyncio.wait_for(timer, 2)
  assert supervisor.allocation_runtime.dispatch.await_count == 2
  assert supervisor.entry_runtime.dispatch.await_count == 2
  assert not supervisor._allocation_triggers and not supervisor._allocation_timers
  async with sessions() as db:
    event = (await db.scalars(select(TAssistantExecutionEventRecord).where(
      TAssistantExecutionEventRecord.execution_id == key,
      TAssistantExecutionEventRecord.event_type == "ALLOCATION_TRIGGERS_COALESCED",
    ))).one()
    assert event.payload["first_fence"] == 2 and event.payload["last_fence"] == 3
    assert event.payload["trigger_count"] == 2


async def test_real_step_commits_every_tick_while_allocation_triggers_are_coalesced(ready, sessions):
  supervisor, key, hub = ready
  supervisor.runtime.run_cycle = type(supervisor.runtime).run_cycle.__get__(supervisor.runtime)
  for sequence in (1, 2, 3):
    await hub.emit(sequence)
  timers = tuple(supervisor._allocation_timers.values())
  if timers:
    await asyncio.wait_for(asyncio.gather(*timers), 2)
  assert supervisor.runtime.symbol_states(key)["600000.SH"].cursor.accepted_sequence == 3
  assert supervisor.allocation_runtime.dispatch.await_count == 2
  async with sessions() as db:
    cycles = list(await db.scalars(select(TAssistantDecisionCycleRecord).where(
      TAssistantDecisionCycleRecord.execution_id == key,
    )))
    assert len(cycles) == 3 and all(row.status == "PROPOSALS_COMMITTED" for row in cycles)


async def test_material_transition_flushes_pending_trigger_immediately(ready):
  supervisor, key, hub = ready
  await hub.emit(1)
  await hub.emit(2)
  timer = supervisor._allocation_timers[key]
  supervisor.runtime.run_cycle.return_value.output.symbol_state_patches = (SimpleNamespace(material=True),)
  await hub.emit(3)
  assert supervisor.allocation_runtime.dispatch.await_count == 2
  await asyncio.gather(timer, return_exceptions=True)
  assert timer.cancelled() and key not in supervisor._allocation_triggers


async def test_unchanged_reconcile_preserves_pending_trigger_without_new_quotes(ready):
  supervisor, key, hub = ready
  await hub.emit(1)
  await hub.emit(2)
  timer = supervisor._allocation_timers[key]
  last_dispatch = supervisor._last_allocation_at[key]
  await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
  await asyncio.gather(timer, return_exceptions=True)
  assert timer.cancelled()
  assert supervisor._last_allocation_at[key] == last_dispatch
  await asyncio.wait_for(supervisor._allocation_timers[key], 2)
  assert supervisor.allocation_runtime.dispatch.await_count == 2


async def test_coalesced_audit_failure_prevents_dispatch(ready, monkeypatch):
  supervisor, key, hub = ready
  await hub.emit(1)
  await hub.emit(2)
  await hub.emit(3)
  timer = supervisor._allocation_timers[key]
  original = TAssistantExecutionRepository.append_event

  async def fail_audit(repository, event):
    if event.event_type == "ALLOCATION_TRIGGERS_COALESCED":
      raise RuntimeError("synthetic audit failure")
    return await original(repository, event)

  monkeypatch.setattr(TAssistantExecutionRepository, "append_event", fail_audit)
  await asyncio.wait_for(timer, 2)
  assert key not in supervisor._bindings
  assert supervisor.allocation_runtime.dispatch.await_count == 1
  assert supervisor.entry_runtime.dispatch.await_count == 1


@pytest.mark.parametrize("action", ["stop", "market_loss", "dispatch_failure"])
async def test_deferred_trigger_cannot_bypass_stop_or_failed_market(ready, action):
  supervisor, key, hub = ready
  await hub.emit(1)
  await hub.emit(2)
  timer = supervisor._allocation_timers[key]
  if action == "stop":
    await supervisor.stop()
  elif action == "market_loss":
    hub.is_ready = False
  else:
    supervisor.allocation_runtime.dispatch.side_effect = RuntimeError("synthetic failure")
  await asyncio.wait_for(asyncio.gather(timer, return_exceptions=True), 2)
  assert key not in supervisor._bindings
  assert supervisor.entry_runtime.dispatch.await_count == 1
  assert supervisor.allocation_runtime.dispatch.await_count == (2 if action == "dispatch_failure" else 1)
