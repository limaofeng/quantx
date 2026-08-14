"""Multi-instance-safe assistant run consumer."""

from __future__ import annotations

import asyncio
import logging

from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.repositories.ai_assistant_repository import (
  AiAssistantRepository,
)
from quantx_infrastructure.services.ai_assistant_event_bus import (
  AI_ASSISTANT_RUN_WAKE_CHANNEL,
)

from quantx_ai_runtime.config import AiRuntimeConfig

from .runner import execute_run, settle_run_failure

logger = logging.getLogger(__name__)


async def _renew_lease(
  stopped: asyncio.Event,
  *,
  run_id: str,
  instance_id: str,
  config: AiRuntimeConfig,
) -> None:
  while not stopped.is_set():
    try:
      await asyncio.wait_for(stopped.wait(), timeout=max(5.0, config.lease_seconds / 3))
      continue
    except asyncio.TimeoutError:
      pass
    async with AsyncSessionLocal() as db:
      renewed = await AiAssistantRepository(db).renew_lease(
        run_id,
        instance_id=instance_id,
        lease_seconds=config.lease_seconds,
      )
    if not renewed:
      raise RuntimeError("AI_RUN_LEASE_LOST")


async def _execute_guarded(
  run_id: str,
  config: AiRuntimeConfig,
  instance_id: str,
) -> None:
  lease_stopped = asyncio.Event()
  lease_task = asyncio.create_task(
    _renew_lease(
      lease_stopped,
      run_id=run_id,
      instance_id=instance_id,
      config=config,
    ),
    name=f"ai-assistant-lease:{run_id}",
  )
  try:
    run_task = asyncio.create_task(execute_run(run_id, config, instance_id=instance_id))
    done, _ = await asyncio.wait(
      [run_task, lease_task], return_when=asyncio.FIRST_COMPLETED
    )
    if lease_task in done and lease_task.exception() is not None:
      run_task.cancel()
      await asyncio.gather(run_task, return_exceptions=True)
      raise lease_task.exception()
    await run_task
  except asyncio.CancelledError:
    raise
  except Exception as exc:
    logger.error(
      "AI assistant run failed: run_id=%s error=%s",
      run_id,
      exc.__class__.__name__,
    )
    try:
      await settle_run_failure(run_id, exc, instance_id=instance_id)
    except Exception:
      logger.error("Could not persist AI assistant run failure: run_id=%s", run_id)
  finally:
    lease_stopped.set()
    lease_task.cancel()
    await asyncio.gather(lease_task, return_exceptions=True)


async def run_consumer(
  stopped: asyncio.Event,
  *,
  instance_id: str,
  config: AiRuntimeConfig,
) -> None:
  subscription = await redis_pubsub.open_subscription(AI_ASSISTANT_RUN_WAKE_CHANNEL)
  tasks: set[asyncio.Task] = set()
  try:
    while not stopped.is_set():
      tasks = {task for task in tasks if not task.done()}
      while len(tasks) < config.max_concurrent_runs:
        async with AsyncSessionLocal() as db:
          run = await AiAssistantRepository(db).claim_next_run(
            instance_id=instance_id,
            lease_seconds=config.lease_seconds,
          )
        if run is None:
          break
        task = asyncio.create_task(
          _execute_guarded(run.id, config, instance_id),
          name=f"ai-assistant-run:{run.id}",
        )
        tasks.add(task)
      if tasks:
        done, _ = await asyncio.wait(tasks, timeout=0.5)
        tasks.difference_update(done)
      else:
        await subscription.wait_for_message(timeout=1.0)
  finally:
    await subscription.close()
    if tasks:
      for task in tasks:
        task.cancel()
      await asyncio.gather(*tasks, return_exceptions=True)
