"""Read-only reproduction against the isolated PostgreSQL test database."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import pytest
from quantx_ai_runtime import database
from quantx_infrastructure.config.settings import settings
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.integration
@pytest.mark.asyncio
async def test_three_connection_pool_exhaustion_and_admitted_recovery(monkeypatch):
  database_name = make_url(settings.database_url).database or ""
  assert database_name.endswith("_test") or database_name.startswith("test_")
  engine = create_async_engine(
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
