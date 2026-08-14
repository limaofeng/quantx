"""Supervised entry point for the independent QuantX AI Runtime."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid

from quantx_infrastructure.database.relational_connection import close_database

from .config import (
  AiRuntimeConfigController,
  config_refresh_loop,
  load_config,
  runtime_status,
)
from .observability import heartbeat_loop, write_heartbeat

logger = logging.getLogger(__name__)


async def run_runtime() -> None:
  controller = AiRuntimeConfigController(load_config())
  try:
    await controller.refresh()
  except Exception as exc:
    logger.warning(
      "AI Runtime initial config refresh failed: %s",
      exc.__class__.__name__,
    )
  config = controller.snapshot()
  instance_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
  stopped = asyncio.Event()
  loop = asyncio.get_running_loop()
  for signal_name in ("SIGINT", "SIGTERM"):
    process_signal = getattr(signal, signal_name, None)
    if process_signal is None:
      continue
    try:
      loop.add_signal_handler(process_signal, stopped.set)
    except NotImplementedError:
      signal.signal(
        process_signal,
        lambda *_: loop.call_soon_threadsafe(stopped.set),
      )

  consumer = None
  research_consumer = None
  dependencies_available = False
  if config.provider_configured:
    try:
      from agents import set_tracing_disabled

      from .runtime.consumer import run_consumer
      from .runtime.limit_up_research import run_limit_up_research_consumer

      os.environ["OPENAI_API_KEY"] = config.api_key
      set_tracing_disabled(not config.tracing_enabled)
      consumer = run_consumer
      research_consumer = run_limit_up_research_consumer
      dependencies_available = True
    except ModuleNotFoundError as exc:
      if exc.name != "agents":
        raise
      logger.error(
        "AI Runtime dependency is unavailable; run uv sync or configure "
        "QUANTX_AI_RUNTIME_PYTHON_EXE"
      )
  await write_heartbeat(
    instance_id=instance_id,
    config=config,
    status=runtime_status(
      config,
      dependencies_available=dependencies_available,
    ),
  )
  tasks = [
    asyncio.create_task(
      config_refresh_loop(stopped, controller=controller),
      name="ai-runtime-config-refresh",
    ),
    asyncio.create_task(
      heartbeat_loop(
        stopped,
        instance_id=instance_id,
        controller=controller,
        dependencies_available=dependencies_available,
      ),
      name="ai-runtime-heartbeat",
    )
  ]
  if consumer is not None:
    tasks.append(
      asyncio.create_task(
        consumer(stopped, instance_id=instance_id, controller=controller),
        name="ai-runtime-consumer",
      )
    )
    tasks.append(
      asyncio.create_task(
        research_consumer(stopped, instance_id=instance_id, controller=controller),
        name="limit-up-research-consumer",
      )
    )
  try:
    stop_waiter = asyncio.create_task(stopped.wait(), name="ai-runtime-stop")
    done, _ = await asyncio.wait(
      [stop_waiter, *tasks],
      return_when=asyncio.FIRST_COMPLETED,
    )
    failed = [
      task
      for task in tasks
      if task in done and not task.cancelled() and task.exception()
    ]
    if failed:
      raise RuntimeError(f"AI Runtime task failed: {failed[0].get_name()}") from failed[
        0
      ].exception()
  finally:
    stopped.set()
    for task in tasks:
      task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    try:
      await write_heartbeat(
        instance_id=instance_id,
        config=controller.snapshot(),
        status="offline",
      )
    finally:
      await close_database()


def main() -> None:
  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
  )
  asyncio.run(run_runtime())


if __name__ == "__main__":
  main()
