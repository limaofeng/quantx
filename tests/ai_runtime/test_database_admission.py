from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import pytest
from quantx_ai_runtime import database
from quantx_ai_runtime.database import DatabaseAdmission, DatabaseCapacityTimeout
from sqlalchemy.ext.asyncio import AsyncSession


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

  class Session(AsyncSession):
    def __init__(self):
      super().__init__()
      events.append("open")

    async def close(self):
      with pytest.raises(DatabaseCapacityTimeout):
        async with admission.slot():
          pytest.fail("permit released before rollback/close")
      await super().close()
      events.append("closed")

  monkeypatch.setattr(database, "AsyncSessionLocal", Session)
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

  def sessions():
    raise RuntimeError("session open failed")

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

  class Session(AsyncSession):
    async def close(self):
      await super().close()
      closed.set()

  monkeypatch.setattr(database, "AsyncSessionLocal", Session)

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


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_real_session_close_keeps_admission_despite_cancellation(
  monkeypatch, cancel_count
) -> None:
  admission = DatabaseAdmission(2, timeout_seconds=0.03)
  monkeypatch.setattr(database, "database_admission", admission)
  closing = asyncio.Event()
  allow_close = asyncio.Event()
  closed = asyncio.Event()
  close_tasks = []

  class DelayedSession(AsyncSession):
    async def close(self):
      close_tasks.append(asyncio.current_task())
      closing.set()
      await allow_close.wait()
      await super().close()
      closed.set()

  monkeypatch.setattr(database, "AsyncSessionLocal", DelayedSession)

  async def work():
    async with database.database_session():
      pass

  task = asyncio.create_task(work())
  try:
    await asyncio.wait_for(closing.wait(), timeout=1)
    for _ in range(cancel_count):
      task.cancel()
      await asyncio.sleep(0)
      assert not task.done(), "caller left while SQLAlchemy was still closing"
    with pytest.raises(DatabaseCapacityTimeout):
      async with admission.slot():
        pytest.fail("work permit released while session was still closing")
    async with admission.slot(heartbeat=True):
      pass
    allow_close.set()
    with pytest.raises(asyncio.CancelledError):
      await asyncio.wait_for(task, timeout=1)
    assert closed.is_set()
    async with admission.slot():
      pass
  finally:
    allow_close.set()
    await asyncio.gather(task, *close_tasks, return_exceptions=True)
