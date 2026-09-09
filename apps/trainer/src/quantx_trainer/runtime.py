"""Explicit per-flow runtime; importing a Trainer flow never selects a database."""

import sys
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from pathlib import Path

from quantx_trainer.config import TrainerConfig, TrainerConfigurationError
from quantx_trainer.preflight import preflight

_config: ContextVar[TrainerConfig] = ContextVar("trainer_config")


def current_config() -> TrainerConfig:
  try:
    return _config.get()
  except LookupError:
    raise TrainerConfigurationError(
      "Trainer flow has no validated deployment configuration"
    ) from None


@asynccontextmanager
async def training_session(config_path: str):
  config = TrainerConfig.load(Path(config_path))
  config.validate_runtime(
    prefix=Path(sys.prefix), code_root=Path(__file__).resolve().parents[4]
  )
  await preflight(config)
  from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

  engine = create_async_engine(
    config.database_url, connect_args={"timeout": 10, "command_timeout": 30}
  )
  token = _config.set(config)
  try:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
      yield session
  finally:
    _config.reset(token)
    with suppress(Exception):
      await engine.dispose()
