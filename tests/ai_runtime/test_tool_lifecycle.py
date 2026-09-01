"""Run ownership covers the whole audited tool, not just its business callback."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from quantx_ai_runtime import database
from quantx_ai_runtime.runtime import runner
from quantx_ai_runtime.tools import registry
from quantx_application.assistant.contracts import (
  AssistantExecutionContext,
  AssistantToolMetadata,
  AssistantToolRisk,
)
from quantx_infrastructure.async_lifecycle import finish_cleanup
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def audited_tool(monkeypatch):
  harness = SimpleNamespace(
    close_phase="callback",
    closing=asyncio.Event(),
    allow_close=asyncio.Event(),
    closed=asyncio.Event(),
    admission=database.DatabaseAdmission(3, timeout_seconds=0.05),
  )
  call = SimpleNamespace(id="tool-test")

  class AuditSession(AsyncSession):
    phase = None

    async def merge(self, instance, **kwargs):
      return instance

    async def close(self):
      if self.phase == harness.close_phase:
        harness.closing.set()
        await harness.allow_close.wait()
      await super().close()
      if self.phase == harness.close_phase:
        harness.closed.set()

  def repository(db):
    async def create(**kwargs):
      db.phase = "start_audit"
      return call

    async def finish(instance, **kwargs):
      db.phase = "finish_audit"
      return instance

    return SimpleNamespace(create_tool_call=create, finish_tool_call=finish)

  async def append(*, event_type, **kwargs):
    async with database.database_session() as db:
      db.phase = event_type

  async def callback():
    async with database.database_session() as db:
      db.phase = "callback"
    return {"summary": "test"}

  harness.context = registry.RuntimeRunContext(
    execution=AssistantExecutionContext(
      user_id="user-test",
      permissions=frozenset(),
      authorized_account_ids=(),
      thread_id="thread-test",
      run_id="run-test",
      request_id="request-test",
    ),
    event_writer=SimpleNamespace(append=append),
  )
  harness.metadata = AssistantToolMetadata(
    name="test_read", version="1", description="test", risk_level=AssistantToolRisk.READ
  )
  harness.callback = AsyncMock(side_effect=callback)
  monkeypatch.setattr(database, "database_admission", harness.admission)
  monkeypatch.setattr(database, "AsyncSessionLocal", AuditSession)
  monkeypatch.setattr(registry, "AiAssistantRepository", repository)
  return harness


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "phase",
  [
    "start_audit",
    "TOOL_CALL_STARTED",
    "callback",
    "finish_audit",
    "TOOL_CALL_COMPLETED",
  ],
)
async def test_tool_ownership_covers_audit_and_session_cleanup(audited_tool, phase):
  harness = audited_tool
  harness.close_phase = phase
  context = harness.context
  tool = asyncio.create_task(
    registry._invoke_audited(context, harness.metadata, {}, harness.callback)
  )
  cleanup = None
  try:
    await asyncio.wait_for(harness.closing.wait(), timeout=1)
    assert context._tool_tasks == {tool}
    context.stop_tool_calls()
    cleanup = asyncio.create_task(finish_cleanup(context.close_tool_calls()))
    async with asyncio.timeout(1):
      while not tool.cancelling():
        await asyncio.sleep(0)
    cleanup.cancel()
    await asyncio.sleep(0)
    cleanup.cancel()
    done, _ = await asyncio.wait({cleanup}, timeout=0.05)
    assert not done
    assert context._tool_tasks == {tool}
    assert harness.admission._work_slots._value == 1
    assert not harness.closed.is_set()

    harness.allow_close.set()
    with pytest.raises(asyncio.CancelledError):
      await asyncio.wait_for(cleanup, timeout=1)
    assert tool.done() and tool.cancelled()
    assert tool.cancelling() == 1
    assert harness.closed.is_set()
    assert not context._tool_tasks
    assert harness.admission._work_slots._value == 2
    assert harness.admission._all_slots._value == 3
  finally:
    harness.allow_close.set()
    if not tool.done():
      tool.cancel()
    await asyncio.gather(tool, *([cleanup] if cleanup else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_rejects_queued_tool_before_audit(monkeypatch, audited_tool):
  harness = audited_tool
  context = harness.context
  enter = asyncio.Event()
  sessions = Mock(side_effect=AssertionError("late tool opened a database session"))
  monkeypatch.setattr(registry, "database_session", sessions)

  async def queued_tool():
    await enter.wait()
    return await registry._invoke_audited(
      context, harness.metadata, {}, harness.callback
    )

  tool = asyncio.create_task(queued_tool())
  try:
    context.stop_tool_calls()
    await finish_cleanup(context.close_tool_calls())
    enter.set()
    with pytest.raises(asyncio.CancelledError, match="AI_RUN_CLOSING"):
      await asyncio.wait_for(tool, timeout=1)
    sessions.assert_not_called()
    harness.callback.assert_not_awaited()
    assert context.tool_call_count == 0
    assert not context._tool_tasks
  finally:
    enter.set()
    if not tool.done():
      tool.cancel()
    await asyncio.gather(tool, return_exceptions=True)


@pytest.mark.asyncio
async def test_stream_drain_error_still_joins_tools(audited_tool, caplog):
  harness = audited_tool
  context = harness.context
  failure = ValueError("original run failure")
  tool = asyncio.create_task(
    registry._invoke_audited(context, harness.metadata, {}, harness.callback)
  )

  async def broken_events():
    raise RuntimeError("sensitive cleanup details")
    yield  # Make this an async iterator like stream_events().

  stream = SimpleNamespace(is_complete=True, stream_events=broken_events)

  async def owner():
    try:
      raise failure
    finally:
      context.stop_tool_calls()
      await finish_cleanup(runner._finish_stream(stream, broken_events(), context))

  task = None
  try:
    await asyncio.wait_for(harness.closing.wait(), timeout=1)
    task = asyncio.create_task(owner())
    done, _ = await asyncio.wait({task}, timeout=0.05)
    assert not done
    assert not harness.closed.is_set()
    harness.allow_close.set()
    with pytest.raises(ValueError) as caught:
      await asyncio.wait_for(task, timeout=1)
    assert caught.value is failure
    assert tool.done() and tool.cancelled()
    assert harness.closed.is_set()
    assert not context._tool_tasks
    assert "error=RuntimeError" in caplog.text
    assert "sensitive cleanup details" not in caplog.text
  finally:
    harness.allow_close.set()
    if not tool.done():
      tool.cancel()
    await asyncio.gather(tool, *([task] if task else []), return_exceptions=True)
