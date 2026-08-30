from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager

import pytest
from quantx_ai_runtime import database
from quantx_ai_runtime.database import DatabaseAdmission, DatabaseCapacityTimeout


@pytest.mark.asyncio
async def test_work_budget_reserves_capacity_for_heartbeat() -> None:
  admission = DatabaseAdmission(3, timeout_seconds=0.03)
  async with admission.slot(), admission.slot():
    async with admission.slot(heartbeat=True):
      with pytest.raises(DatabaseCapacityTimeout):
        async with admission.slot():
          pytest.fail("third business operation consumed heartbeat capacity")
    # An expired waiter must not leak either admission permit.
    async with admission.slot(heartbeat=True):
      pass
  async with admission.slot(), admission.slot(), admission.slot(heartbeat=True):
    pass


@pytest.mark.asyncio
async def test_one_connection_override_serializes_all_work() -> None:
  admission = DatabaseAdmission(1, timeout_seconds=0.03)
  async with admission.slot(heartbeat=True):
    # This waiter acquires a work permit, then times out on total capacity.
    with pytest.raises(DatabaseCapacityTimeout):
      async with admission.slot():
        pytest.fail("one-connection budget was exceeded")
  async with admission.slot():
    with pytest.raises(DatabaseCapacityTimeout):
      async with admission.slot(heartbeat=True):
        pytest.fail("heartbeat bypassed total capacity")
  async with admission.slot(heartbeat=True):
    pass


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_release_unowned_permits() -> None:
  admission = DatabaseAdmission(3, timeout_seconds=1)
  waiting = asyncio.Event()

  async def waiter():
    waiting.set()
    async with admission.slot():
      pytest.fail("waiter unexpectedly acquired a permit")

  async with admission.slot(), admission.slot():
    task = asyncio.create_task(waiter())
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
      await task
    async with admission.slot(heartbeat=True):
      pass
  async with admission.slot(), admission.slot(), admission.slot(heartbeat=True):
    pass


@pytest.mark.asyncio
async def test_session_closes_before_work_permit_is_released(monkeypatch) -> None:
  events = []
  admission = DatabaseAdmission(2, timeout_seconds=0.03)
  monkeypatch.setattr(database, "database_admission", admission)

  @asynccontextmanager
  async def sessions():
    events.append("open")
    try:
      yield object()
    finally:
      with pytest.raises(DatabaseCapacityTimeout):
        async with admission.slot():
          pytest.fail("permit released before rollback/close")
      events.append("closed")

  monkeypatch.setattr(database, "AsyncSessionLocal", sessions)
  with pytest.raises(ValueError, match="business failure"):
    async with database.database_session():
      raise ValueError("business failure")
  assert events == ["open", "closed"]
  async with admission.slot():
    pass


@pytest.mark.asyncio
async def test_session_open_failure_releases_admission(monkeypatch) -> None:
  admission = DatabaseAdmission(2, timeout_seconds=0.03)
  monkeypatch.setattr(database, "database_admission", admission)

  @asynccontextmanager
  async def sessions():
    raise RuntimeError("session open failed")
    yield  # pragma: no cover

  monkeypatch.setattr(database, "AsyncSessionLocal", sessions)
  with pytest.raises(RuntimeError, match="session open failed"):
    async with database.database_session():
      pytest.fail("session unexpectedly opened")
  async with admission.slot():
    pass


@pytest.mark.asyncio
async def test_database_work_slot_budgets_services_with_internal_sessions(monkeypatch):
  admission = DatabaseAdmission(2, timeout_seconds=0.03)
  monkeypatch.setattr(database, "database_admission", admission)
  async with database.database_work_slot():
    with pytest.raises(DatabaseCapacityTimeout):
      async with admission.slot():
        pytest.fail("service bypassed the work budget")
    async with admission.slot(heartbeat=True):
      pass


@pytest.mark.asyncio
async def test_body_timeout_is_not_relabelled_as_admission_timeout() -> None:
  admission = DatabaseAdmission(2, timeout_seconds=0.03)
  with pytest.raises(TimeoutError, match="operation deadline"):
    async with admission.slot():
      raise TimeoutError("operation deadline")


@pytest.mark.asyncio
async def test_cancelled_holder_releases_session_and_admission(monkeypatch) -> None:
  admission = DatabaseAdmission(2, timeout_seconds=0.03)
  monkeypatch.setattr(database, "database_admission", admission)
  entered = asyncio.Event()
  closed = asyncio.Event()

  @asynccontextmanager
  async def sessions():
    try:
      yield object()
    finally:
      closed.set()

  monkeypatch.setattr(database, "AsyncSessionLocal", sessions)

  async def hold():
    async with database.database_session():
      entered.set()
      await asyncio.Event().wait()

  task = asyncio.create_task(hold())
  await entered.wait()
  task.cancel()
  with pytest.raises(asyncio.CancelledError):
    await task
  assert closed.is_set()
  async with admission.slot():
    pass


@pytest.mark.asyncio
async def test_capacity_uses_the_configured_pool_budget() -> None:
  admission = DatabaseAdmission(5, timeout_seconds=0.03)
  async with AsyncExitStack() as stack:
    for _ in range(4):
      await stack.enter_async_context(admission.slot())
    await stack.enter_async_context(admission.slot(heartbeat=True))
    with pytest.raises(DatabaseCapacityTimeout):
      async with admission.slot(heartbeat=True):
        pytest.fail("configured total capacity was exceeded")
