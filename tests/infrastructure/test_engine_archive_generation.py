"""Real PG singleton ownership and persistent archive generations, isolated schema."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from quantx_infrastructure.services.engine_archive_generation import (
  ENGINE_LOCK_NAME,
  register_engine_archive_generation,
  verify_engine_archive_generation,
)
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
async def archive_db():
  url = make_url(os.environ["DATABASE_URL"])
  assert url.host in {"localhost", "127.0.0.1", "::1"}
  assert url.database.endswith("_test") or url.database.startswith("test_")
  schema = "archive_test_" + uuid4().hex
  admin = create_async_engine(url)
  async with admin.begin() as db:
    await db.execute(text(f'CREATE SCHEMA "{schema}"'))
  engine = create_async_engine(
    url, connect_args={"server_settings": {"search_path": schema + ",public"}}
  )
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260910_0084_engine_archive_generation.py"
  )
  spec = importlib.util.spec_from_file_location("archive_migration", path)
  migration = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(migration)
  try:
    async with engine.begin() as db:

      def upgrade(connection):
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

      await db.run_sync(upgrade)
    yield SimpleNamespace(engine=engine, migration=migration)
  finally:
    await engine.dispose()
    async with admin.begin() as db:
      await db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin.dispose()


async def lock(db):
  assert await db.scalar(
    text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": ENGINE_LOCK_NAME}
  )
  await db.commit()


async def unlock(db):
  await db.rollback()
  assert await db.scalar(
    text("SELECT pg_advisory_unlock(hashtext(:name))"), {"name": ENGINE_LOCK_NAME}
  )
  await db.commit()


async def test_only_actual_lock_session_can_allocate(archive_db):
  async with (
    archive_db.engine.connect() as owner,
    archive_db.engine.connect() as outsider,
  ):
    await lock(owner)
    try:
      with pytest.raises(RuntimeError, match="SINGLETON_REQUIRED"):
        await register_engine_archive_generation(outsider, str(uuid4()))
      await outsider.rollback()
      generation = await register_engine_archive_generation(owner, str(uuid4()))
      await verify_engine_archive_generation(outsider, generation)
    finally:
      await unlock(owner)
    with pytest.raises(RuntimeError, match="GENERATION_INACTIVE"):
      await verify_engine_archive_generation(outsider, generation)


async def test_registration_replay_and_restart_preserve_monotonicity(archive_db):
  registration = str(uuid4())
  async with archive_db.engine.connect() as first:
    await lock(first)
    try:
      old = await register_engine_archive_generation(first, registration)
      assert await register_engine_archive_generation(first, registration) == old
      assert (
        await first.scalar(text("SELECT count(*) FROM engine_archive_generation")) == 1
      )
    finally:
      # A process-owned detached lease is physically closed at Engine shutdown.
      await first.invalidate()
  async with archive_db.engine.connect() as second:
    await lock(second)
    try:
      with pytest.raises(RuntimeError, match="REGISTRATION_CONFLICT"):
        await register_engine_archive_generation(second, registration)
      await second.rollback()
      new = await register_engine_archive_generation(second, str(uuid4()))
      assert new > old
      with pytest.raises(RuntimeError, match="GENERATION_INACTIVE"):
        await verify_engine_archive_generation(second, old)
      await verify_engine_archive_generation(second, new)
      assert (
        await second.scalar(text("SELECT count(*) FROM engine_archive_generation")) == 2
      )
    finally:
      await unlock(second)


async def test_latest_generation_fences_previous_registration_on_same_backend(
  archive_db,
):
  async with archive_db.engine.connect() as db:
    await lock(db)
    try:
      first = await register_engine_archive_generation(db, str(uuid4()))
      second = await register_engine_archive_generation(db, str(uuid4()))
      assert second > first
      with pytest.raises(RuntimeError, match="GENERATION_INACTIVE"):
        await verify_engine_archive_generation(db, first)
      await verify_engine_archive_generation(db, second)
    finally:
      await unlock(db)


async def test_downgrade_cannot_erase_allocated_generations(archive_db):
  async with archive_db.engine.connect() as db:
    await lock(db)
    try:
      await register_engine_archive_generation(db, str(uuid4()))

      def downgrade(connection):
        archive_db.migration.op = Operations(MigrationContext.configure(connection))
        archive_db.migration.downgrade()

      with pytest.raises(RuntimeError, match="cannot remove persisted"):
        await db.run_sync(downgrade)
      assert (
        await db.scalar(text("SELECT count(*) FROM engine_archive_generation")) == 1
      )
    finally:
      await unlock(db)


async def test_reused_pid_with_different_backend_start_is_rejected(archive_db):
  async with archive_db.engine.connect() as db:
    await lock(db)
    try:
      generation = await register_engine_archive_generation(db, str(uuid4()))
      await db.execute(
        text(
          "UPDATE engine_archive_generation SET backend_start=backend_start - INTERVAL '1 second'"
        )
      )
      await db.commit()
      with pytest.raises(RuntimeError, match="GENERATION_INACTIVE"):
        await verify_engine_archive_generation(db, generation)
    finally:
      await unlock(db)
