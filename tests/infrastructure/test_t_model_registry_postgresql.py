"""Real registry lock behavior, scoped to a disposable local test schema."""

import asyncio
import os
import uuid

import pytest
from quantx_infrastructure.models.t_model_registry import (
  TModelRegistryEventRecord,
  TModelVersionRecord,
)
from quantx_infrastructure.repositories.t_model_registry_repository import (
  TModelRegistryConflict,
  TModelRegistryRepository,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.infrastructure.test_t_model_registry_repository import registration, stage

pytestmark = pytest.mark.skipif(
  os.getenv("QUANTX_RUN_MIGRATION_GATE") != "true",
  reason="explicit isolated PostgreSQL gate opt-in required",
)


@pytest.fixture
async def registry_sessions():
  root = create_async_engine(os.environ["DATABASE_URL"], echo=False)
  assert root.url.host in {"localhost", "127.0.0.1", "::1"}
  database = root.url.database or ""
  assert database.startswith("test_") or database.endswith("_test")
  schema = "quantx_t_registry_" + uuid.uuid4().hex
  scoped = None
  installed = False
  try:
    async with root.begin() as connection:
      await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
      await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
      for table in (TModelVersionRecord.__table__, TModelRegistryEventRecord.__table__):
        await connection.run_sync(table.create)
    installed = True
    scoped = create_async_engine(os.environ["DATABASE_URL"], echo=False,
      connect_args={"server_settings": {"search_path": schema, "statement_timeout": "5000"}})
    async with scoped.connect() as connection:
      assert await connection.scalar(text("SELECT current_schema()")) == schema
    yield async_sessionmaker(scoped, expire_on_commit=False)
  finally:
    if scoped is not None:
      await scoped.dispose()
    if installed:
      async with root.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
      async with root.connect() as connection:
        assert not await connection.scalar(text(
          "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=:schema)"), {"schema": schema})
    await root.dispose()


@pytest.mark.parametrize("mode", ["SHADOW", "ACTIVE"])
async def test_observation_bypasses_inference_lock_but_revocation_waits(registry_sessions, mode):
  sessions = registry_sessions
  async with sessions() as db, db.begin():
    repo = TModelRegistryRepository(db)
    await repo.append_candidate(**registration())
    await stage(repo, "SHADOW", 1)
    revision = 2
    if mode == "ACTIVE":
      await stage(repo, "ACTIVE", revision)
      revision += 1
  identity = dict(model_id="t-model", model_version="v1", expected_revision=revision,
    mode=mode, artifact_sha256="a" * 64, policy_compatibility_hash="b" * 64)
  revoker_pid = asyncio.get_running_loop().create_future()

  async def revoke():
    async with sessions() as db, db.begin():
      revoker_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
      await stage(TModelRegistryRepository(db), "SUSPENDED", revision)

  pending = None
  try:
    async with sessions() as locked, locked.begin():
      locker_pid = await locked.scalar(text("SELECT pg_backend_pid()"))
      await TModelRegistryRepository(locked).authorize(**identity)
      pending = asyncio.create_task(revoke())
      pid = await asyncio.wait_for(asyncio.shield(revoker_pid), 2)
      async with sessions() as observer, observer.begin():
        async def wait_for_real_lock():
          while True:
            blockers = await observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": pid})
            if locker_pid in blockers:
              return
            if pending.done():
              await pending
              raise AssertionError("revocation did not wait for the authorization lock")
            await asyncio.sleep(0.01)
        await asyncio.wait_for(wait_for_real_lock(), 2)
        # This would fail promptly if the observation accidentally requested a lock.
        await observer.execute(text("SET LOCAL lock_timeout = '300ms'"))
        observed = await TModelRegistryRepository(observer).read_snapshot_authorization(**identity)
        assert observed.registry_stage == mode and observed.authorization_revision == revision
        assert not pending.done()
    await asyncio.wait_for(asyncio.shield(pending), 2)
    async with sessions() as db, db.begin():
      with pytest.raises(TModelRegistryConflict, match="AUTHORIZATION_REVOKED"):
        await TModelRegistryRepository(db).read_snapshot_authorization(**identity)
  finally:
    if pending is not None:
      if not pending.done():
        pending.cancel()
      await asyncio.gather(pending, return_exceptions=True)
