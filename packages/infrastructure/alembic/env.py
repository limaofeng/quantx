"""Alembic environment for QuantX's async PostgreSQL database."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

import quantx_infrastructure.models  # noqa: F401
from alembic import context
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.relational_base import Base
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
if config.config_file_name is not None:
  fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
  context.configure(
    url=config.get_main_option("sqlalchemy.url"),
    target_metadata=target_metadata,
    literal_binds=True,
    dialect_opts={"paramstyle": "named"},
    compare_type=True,
  )
  with context.begin_transaction():
    context.run_migrations()


def do_run_migrations(connection) -> None:
  context.configure(
    connection=connection,
    target_metadata=target_metadata,
    compare_type=True,
  )
  with context.begin_transaction():
    context.run_migrations()


async def run_async_migrations() -> None:
  existing_connection = config.attributes.get("connection")
  if existing_connection is not None:
    do_run_migrations(existing_connection)
    return
  connectable = async_engine_from_config(
    config.get_section(config.config_ini_section, {}),
    prefix="sqlalchemy.",
    poolclass=pool.NullPool,
  )
  async with connectable.connect() as connection:
    await connection.run_sync(do_run_migrations)
  await connectable.dispose()


def run_migrations_online() -> None:
  asyncio.run(run_async_migrations())


if context.is_offline_mode():
  run_migrations_offline()
else:
  run_migrations_online()
