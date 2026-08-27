"""ASGI and CLI entrypoint for the standalone QuantX monitor."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request

from .api import build_router
from .config import MonitorSettings, settings
from .scheduler import MonitorScheduler
from .storage import MonitorStorage
from .targets import TARGETS


class MonitorRuntime:
  def __init__(self, runtime_settings: MonitorSettings) -> None:
    self.settings = runtime_settings
    self.storage = MonitorStorage(runtime_settings.database_path)
    self.scheduler = MonitorScheduler(runtime_settings, self.storage)

  async def start(self) -> None:
    await self.storage.open(target.target_id for target in TARGETS)
    await self.scheduler.start()

  async def stop(self) -> None:
    await self.scheduler.stop()
    await self.storage.close()


def create_app(runtime_settings: MonitorSettings | None = None) -> FastAPI:
  monitor_runtime = MonitorRuntime(runtime_settings or settings)

  @asynccontextmanager
  async def lifespan(_: FastAPI):
    await monitor_runtime.start()
    try:
      yield
    finally:
      await monitor_runtime.stop()

  app = FastAPI(
    title="QuantX Monitor",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
  )
  app.state.monitor_runtime = monitor_runtime
  app.include_router(build_router(monitor_runtime))

  @app.middleware("http")
  async def no_store(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

  return app


app = create_app()


async def _backup(destination: Path) -> None:
  storage = MonitorStorage(settings.database_path)
  await storage.backup_to(destination)


def main() -> None:
  parser = argparse.ArgumentParser(prog="quantx-monitor")
  subcommands = parser.add_subparsers(dest="command")
  backup = subcommands.add_parser("backup")
  backup.add_argument("--destination", type=Path, required=True)
  args = parser.parse_args()
  if args.command == "backup":
    asyncio.run(_backup(args.destination))
    return
  uvicorn.run(
    app,
    host=settings.host,
    port=settings.port,
    log_level="info",
    access_log=False,
  )


if __name__ == "__main__":
  main()
