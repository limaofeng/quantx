"""Read-only reproduction against the isolated PostgreSQL test database."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import pytest
from quantx_ai_runtime import database
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.services import engine_command_service as service_module
from quantx_infrastructure.services.engine_command_service import EngineCommandService
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _readonly_engine():
  database_name = make_url(settings.database_url).database or ""
  assert database_name.endswith("_test") or database_name.startswith("test_")
  return create_async_engine(
    settings.database_url,
    pool_size=2,
    max_overflow=1,
    pool_timeout=0.05,
    connect_args={
      "server_settings": {
        "application_name": "quantx-ai-pool-regression",
        "default_transaction_read_only": "on",
        "statement_timeout": "5000",
      }
    },
  )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_three_connection_pool_exhaustion_and_admitted_recovery(monkeypatch):
  engine = _readonly_engine()
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(database, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(
    database, "database_admission", database.DatabaseAdmission(3, timeout_seconds=10)
  )
  try:
    # Reproduce the old failure using the real asyncpg/SQLAlchemy checkout path.
    async with AsyncExitStack() as stack:
      for _ in range(3):
        session = await stack.enter_async_context(sessions())
        await session.execute(text("SELECT 1"))
      assert engine.pool.checkedout() == 3
      with pytest.raises(PoolTimeout):
        async with sessions() as fourth:
          await fourth.execute(text("SELECT 1"))
    assert engine.pool.checkedout() == 0

    # Business work can no longer consume the heartbeat's third connection.
    async with AsyncExitStack() as stack:
      for _ in range(2):
        session = await stack.enter_async_context(database.database_session())
        await session.execute(text("SELECT 1"))
      async with database.database_session(heartbeat=True) as heartbeat:
        assert await heartbeat.scalar(text("SELECT 1")) == 1
        assert engine.pool.checkedout() == 3
    assert engine.pool.checkedout() == 0

    async def work():
      async with database.database_session() as session:
        assert await session.scalar(text("SELECT 1")) == 1
        await asyncio.sleep(0.01)

    async def heartbeats():
      for _ in range(12):
        async with database.database_session(heartbeat=True) as session:
          assert await session.scalar(text("SELECT 1")) == 1
        await asyncio.sleep(0)

    await asyncio.wait_for(
      asyncio.gather(*(work() for _ in range(24)), heartbeats()), timeout=20
    )
    assert engine.pool.checkedout() == 0
  finally:
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_cancelled_closes_keep_real_connections_within_admission_budget(
  monkeypatch, cancel_count
):
  engine = _readonly_engine()
  closing = asyncio.Event()
  allow_close = asyncio.Event()
  close_tasks = []

  class DelayedCloseSession(AsyncSession):
    async def close(self):
      if self.info.get("delay_close"):
        close_tasks.append(asyncio.current_task())
        if len(close_tasks) == 2:
          closing.set()
        await allow_close.wait()
      await super().close()

  monkeypatch.setattr(
    database,
    "AsyncSessionLocal",
    async_sessionmaker(engine, class_=DelayedCloseSession, expire_on_commit=False),
  )
  monkeypatch.setattr(
    database, "database_admission", database.DatabaseAdmission(3, timeout_seconds=0.05)
  )

  async def work():
    async with database.database_session() as db:
      db.info["delay_close"] = True
      await db.execute(text("SELECT 1"))

  tasks = [asyncio.create_task(work()) for _ in range(2)]
  try:
    await asyncio.wait_for(closing.wait(), timeout=10)
    for _ in range(cancel_count):
      for task in tasks:
        task.cancel()
      await asyncio.sleep(0)
      assert not any(task.done() for task in tasks)
    assert engine.pool.checkedout() == 2
    with pytest.raises(database.DatabaseCapacityTimeout):
      async with database.database_session():
        pytest.fail("replacement admitted before old connections were returned")
    async with database.database_session(heartbeat=True) as heartbeat:
      assert await heartbeat.scalar(text("SELECT 1")) == 1
      assert engine.pool.checkedout() == 3
    allow_close.set()
    results = await asyncio.wait_for(
      asyncio.gather(*tasks, return_exceptions=True), timeout=5
    )
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert engine.pool.checkedout() == 0

    # Both work permits and the heartbeat permit are reusable after cleanup.
    async with AsyncExitStack() as stack:
      for heartbeat in (False, False, True):
        db = await stack.enter_async_context(
          database.database_session(heartbeat=heartbeat)
        )
        assert await db.scalar(text("SELECT 1")) == 1
      assert engine.pool.checkedout() == 3
    assert engine.pool.checkedout() == 0
  finally:
    allow_close.set()
    await asyncio.gather(*tasks, *close_tasks, return_exceptions=True)
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cancelled_engine_command_keeps_work_slot_until_real_session_closes(
  monkeypatch,
):
  engine = _readonly_engine()
  closing = asyncio.Event()
  allow_close = asyncio.Event()
  closed = asyncio.Event()

  class DelayedCommandSession(AsyncSession):
    def add(self, instance, _warn=True):
      self.info["command"] = instance

    async def commit(self):
      raise IntegrityError("synthetic idempotency conflict", {}, RuntimeError())

    async def rollback(self):
      return None

    async def scalar(self, _statement, *args, **kwargs):
      assert await super().scalar(text("SELECT 1")) == 1
      return self.info["command"]

    async def close(self):
      closing.set()
      await allow_close.wait()
      await super().close()
      closed.set()

  sessions = async_sessionmaker(
    engine,
    class_=DelayedCommandSession,
    expire_on_commit=False,
  )
  monkeypatch.setattr(service_module, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(
    database, "database_admission", database.DatabaseAdmission(3, timeout_seconds=0.05)
  )

  async def enqueue():
    async with database.database_work_slot():
      await EngineCommandService().enqueue(
        "BACKTEST_RERUN",
        {"run_id": "run-cancelled"},
        aggregate_id="run-cancelled",
        idempotency_key="rerun:run-cancelled",
      )

  task = asyncio.create_task(enqueue())
  try:
    await asyncio.wait_for(closing.wait(), timeout=10)
    assert engine.pool.checkedout() == 1

    task.cancel("tool cancelled")
    await asyncio.sleep(0)
    assert not task.done()
    assert not closed.is_set()

    # The cancelled command still owns one of the two business permits until
    # its real SQLAlchemy session has returned the physical connection.
    async with database.database_work_slot():
      with pytest.raises(database.DatabaseCapacityTimeout):
        async with database.database_work_slot():
          pytest.fail("work permit was released before session close completed")

    allow_close.set()
    with pytest.raises(asyncio.CancelledError, match="tool cancelled"):
      await asyncio.wait_for(task, timeout=5)
    assert closed.is_set()
    assert engine.pool.checkedout() == 0

    async with AsyncExitStack() as stack:
      for _ in range(2):
        await stack.enter_async_context(database.database_work_slot())
  finally:
    allow_close.set()
    await asyncio.gather(task, return_exceptions=True)
    await engine.dispose()
