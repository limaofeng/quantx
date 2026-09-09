"""Apply 0060 against real PostgreSQL constraints in a rolled-back random schema."""

import os
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.infrastructure.test_execution_owner_persistence import (
  _load_execution_owner_revision,
)


@pytest.mark.skipif(
  os.getenv("QUANTX_RUN_MIGRATION_GATE") != "true", reason="isolated migration opt-in"
)
async def test_postgresql_migration_preserves_original_owners_and_accepts_independent_t():
  engine = create_async_engine(os.environ["DATABASE_URL"])
  assert (engine.url.database or "").endswith("_test") or (
    engine.url.database or ""
  ).startswith("test_")
  schema = "quantx_t_order_identity_" + uuid.uuid4().hex
  old = _load_execution_owner_revision()
  new = _load_execution_owner_revision("20260909_0060_t_assistant_order_identity.py")
  try:
    async with engine.connect() as connection:
      transaction = await connection.begin()
      try:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
        tables = (
          ("pending_trade_orders", "ck_pending_trade_order_strategy_identity"),
          ("order_correlations", "ck_order_correlation_strategy_identity"),
        )
        for table, name in tables:
          check = dict(old._CHECKS[table])[name]
          await connection.execute(
            text(
              f"CREATE TABLE {table} (owner_type text NOT NULL, intent_id text, "
              f"strategy_run_id text, strategy_order_id text, CONSTRAINT {name} CHECK ({check}))"
            )
          )
          await connection.execute(
            text(
              f"INSERT INTO {table} VALUES ('STRATEGY_RUN','intent-old','run-old','order-old')"
            )
          )

        def apply(sync):
          with Operations.context(MigrationContext.configure(sync)):
            new.upgrade()

        await connection.run_sync(apply)
        for table, _ in tables:
          for owner, intent, run, order, valid in (
            ("T_ASSISTANT_EXECUTION", "intent-new", None, None, True),
            ("T_ASSISTANT_EXECUTION", None, None, None, False),
            ("T_ASSISTANT_EXECUTION", "intent-new", "fake-run", None, False),
            ("T_ASSISTANT_EXECUTION", "intent-new", None, "fake-order", False),
            ("EXIT_PLAN", "exit-intent", None, None, True),
            ("MANUAL_COMMAND", None, None, None, True),
          ):
            nested = await connection.begin_nested()
            try:
              statement = text(
                f"INSERT INTO {table} VALUES (:owner,:intent,:run,:order)"
              )
              params = dict(owner=owner, intent=intent, run=run, order=order)
              if valid:
                await connection.execute(statement, params)
              else:
                with pytest.raises(IntegrityError):
                  await connection.execute(statement, params)
            finally:
              await nested.rollback()
          assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 1
      finally:
        await transaction.rollback()
      assert not await connection.scalar(
        text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=:schema)"),
        {"schema": schema},
      )
  finally:
    await engine.dispose()
