"""Dedicated sessions in the local test database; no production locks or data."""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from quantx_infrastructure.services import development_delivery_execution as execution
from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.infrastructure.test_market_data_write_cancellation import (
  cancel_repeatedly,
  install_writer,
)


@pytest.fixture
async def sessions(monkeypatch):
  url = make_url(os.environ["DATABASE_URL"])
  assert url.host in {"localhost", "127.0.0.1", "::1"}
  assert url.database.endswith("_test") or url.database.startswith("test_")
  engine = create_async_engine(url, pool_size=3, max_overflow=0)
  monkeypatch.setattr(execution, "LOCK_PROBE_SECONDS", 0.02)
  try:
    yield engine, async_sessionmaker(engine)
  finally:
    await engine.dispose()


def request():
  identity = str(uuid4())
  return SimpleNamespace(model_dump_json=lambda: identity)


async def test_partition_lock_serializes_same_partition_only(sessions):
  _, factory = sessions
  partition = request()
  entered, release = asyncio.Event(), asyncio.Event()
  owners = []

  async def hold(owner):
    owners.append(owner)
    entered.set()
    await release.wait()
    return "held"

  async def success(owner):
    async with factory() as db:
      await owner._guard_ingestion_owner(db)
    return "other"

  task = asyncio.create_task(execution.run_delivery_execution(partition, factory, hold))
  try:
    await asyncio.wait_for(entered.wait(), 2)
    assert await execution.run_delivery_execution(partition, factory, success) == {
      "status": "IMPORT_IN_PROGRESS"
    }
    assert (
      await execution.run_delivery_execution(request(), factory, success) == "other"
    )
  finally:
    release.set()
    assert await asyncio.wait_for(task, 2) == "held"
  assert await execution.run_delivery_execution(partition, factory, success) == "other"
  async with factory() as db:
    with pytest.raises(execution.DeliveryOwnershipLost):
      await owners[0]._guard_ingestion_owner(db)


async def test_repeated_cancellation_joins_write_and_unlock_before_return(
  sessions, monkeypatch
):
  engine, factory = sessions
  partition = request()
  entered, write_release, finished = install_writer(monkeypatch)
  unlock_entered, unlock_release = asyncio.Event(), asyncio.Event()

  class SlowUnlock(AsyncSession):
    async def scalar(self, statement, *args, **kwargs):
      if "pg_advisory_unlock" in str(statement):
        assert finished.is_set()
        unlock_entered.set()
        await unlock_release.wait()
      return await super().scalar(statement, *args, **kwargs)

  async def write(owner):
    return await ingestion.save_market_data_period(period="1m", market_data={})

  async def success(owner):
    return True

  task = asyncio.create_task(
    execution.run_delivery_execution(
      partition, async_sessionmaker(engine, class_=SlowUnlock), write
    )
  )
  try:
    await asyncio.wait_for(entered.wait(), 2)
    await cancel_repeatedly(task)
    assert await execution.run_delivery_execution(partition, factory, success) == {
      "status": "IMPORT_IN_PROGRESS"
    }
    write_release.set()
    await asyncio.wait_for(unlock_entered.wait(), 2)
    await cancel_repeatedly(task)
    assert await execution.run_delivery_execution(partition, factory, success) == {
      "status": "IMPORT_IN_PROGRESS"
    }
  finally:
    write_release.set()
    unlock_release.set()
    with pytest.raises(asyncio.CancelledError):
      await asyncio.wait_for(task, 2)
  assert await execution.run_delivery_execution(partition, factory, success)


async def test_lost_lock_backend_cancels_work_and_rejects_old_owner(sessions):
  _, factory = sessions
  partition = request()
  entered, stopped = asyncio.Event(), asyncio.Event()
  owners = []

  async def work(owner):
    owners.append(owner)
    entered.set()
    try:
      await asyncio.Event().wait()
    finally:
      stopped.set()

  task = asyncio.create_task(execution.run_delivery_execution(partition, factory, work))
  try:
    await asyncio.wait_for(entered.wait(), 2)
    async with factory() as db:
      # Terminate only the backend created by this test's own execution.
      assert await db.scalar(
        text("SELECT pg_terminate_backend(:pid)"), {"pid": owners[0].pid}
      )
    with pytest.raises(execution.DeliveryOwnershipLost):
      await asyncio.wait_for(task, 2)
    assert stopped.is_set()
    async with factory() as db:
      with pytest.raises(execution.DeliveryOwnershipLost):
        await owners[0]._guard_ingestion_owner(db)
  finally:
    if not task.done():
      task.cancel()
      await asyncio.gather(task, return_exceptions=True)


async def test_cancelled_acquisition_invalidates_uncertain_locked_backend(sessions):
  engine, factory = sessions
  partition = request()
  locked = asyncio.Event()

  class UncertainAcquire(AsyncSession):
    async def scalar(self, statement, *args, **kwargs):
      result = await super().scalar(statement, *args, **kwargs)
      if "pg_try_advisory_lock" in str(statement):
        assert result
        locked.set()
        await asyncio.Event().wait()
      return result

  async def success(owner):
    return True

  task = asyncio.create_task(
    execution.run_delivery_execution(
      partition, async_sessionmaker(engine, class_=UncertainAcquire), success
    )
  )
  await asyncio.wait_for(locked.wait(), 2)
  task.cancel()
  with pytest.raises(asyncio.CancelledError):
    await asyncio.wait_for(task, 2)
  assert await execution.run_delivery_execution(partition, factory, success)


async def test_worker_lease_loss_is_supervised_without_blocking_renewal(sessions):
  _, factory = sessions
  entered, lost = asyncio.Event(), asyncio.Event()
  calls = []

  class Worker:
    async def _guard_ingestion_owner(self, db, *, lock=True):
      calls.append(lock)
      if lost.is_set():
        raise RuntimeError("worker lease lost")

  async def work(owner):
    entered.set()
    await asyncio.Event().wait()

  task = asyncio.create_task(
    execution.run_delivery_execution(request(), factory, work, worker_owner=Worker())
  )
  await asyncio.wait_for(entered.wait(), 2)
  lost.set()
  with pytest.raises(execution.DeliveryOwnershipLost):
    await asyncio.wait_for(task, 2)
  assert calls and not any(calls)


async def test_active_probe_is_not_cancelled_before_sent_write_finishes(
  sessions, monkeypatch
):
  engine, _ = sessions
  entered, write_release, finished = install_writer(monkeypatch)
  probing, probe_cancelled = asyncio.Event(), asyncio.Event()

  class PendingProbe(AsyncSession):
    probes = 0

    async def scalar(self, statement, *args, **kwargs):
      if "FROM pg_locks" in str(statement):
        self.probes += 1
        if self.probes == 2:
          probing.set()
          try:
            await asyncio.Event().wait()
          except asyncio.CancelledError:
            assert finished.is_set()
            probe_cancelled.set()
            raise
      return await super().scalar(statement, *args, **kwargs)

  async def write(owner):
    return await ingestion.save_market_data_period(period="1m", market_data={})

  task = asyncio.create_task(
    execution.run_delivery_execution(
      request(), async_sessionmaker(engine, class_=PendingProbe), write
    )
  )
  try:
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.wait_for(probing.wait(), 2)
    await cancel_repeatedly(task)
    assert not probe_cancelled.is_set()
  finally:
    write_release.set()
    with pytest.raises(asyncio.CancelledError):
      await asyncio.wait_for(task, 2)
  assert probe_cancelled.is_set()
