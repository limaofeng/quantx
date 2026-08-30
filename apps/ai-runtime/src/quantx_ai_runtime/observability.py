"""Heartbeat reporting without message or credential content."""

from __future__ import annotations

import asyncio
import os
import socket

from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_connection import database_pool_snapshot
from quantx_infrastructure.models.agent_runtime import RuntimeComponentHeartbeat
from sqlalchemy.exc import TimeoutError as PoolTimeout

from .config import AiRuntimeConfig, AiRuntimeConfigController, runtime_status
from .database import database_session, log_database_pressure, wait_for_database_retry


async def write_heartbeat(
  *,
  instance_id: str,
  config: AiRuntimeConfig,
  status: str,
) -> None:
  pool_snapshot = database_pool_snapshot()
  async with database_session(heartbeat=True) as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "ai-runtime")
    details = {
      "pid": os.getpid(),
      "host": socket.gethostname(),
      "model": config.model,
      "maxConcurrentRuns": config.max_concurrent_runs,
      "externalSearchDefault": False,
      "configVersion": config.version,
      "configSource": config.source,
      "enabled": config.enabled,
      "apiKeyConfigured": config.provider_configured,
      "databasePool": pool_snapshot,
    }
    if heartbeat is None:
      heartbeat = RuntimeComponentHeartbeat(
        component="ai-runtime",
        instance_id=instance_id,
        status=status,
        details=details,
        updated_at=utcnow(),
      )
      db.add(heartbeat)
    else:
      heartbeat.instance_id = instance_id
      heartbeat.status = status
      heartbeat.details = details
      heartbeat.updated_at = utcnow()
    await db.commit()


async def heartbeat_loop(
  stopped: asyncio.Event,
  *,
  instance_id: str,
  controller: AiRuntimeConfigController,
  dependencies_available: bool,
) -> None:
  while not stopped.is_set():
    config = controller.snapshot()
    try:
      await write_heartbeat(
        instance_id=instance_id,
        config=config,
        status=runtime_status(
          config,
          dependencies_available=dependencies_available,
        ),
      )
    except PoolTimeout as exc:
      # Do not advance freshness on failure; the API can still report stale.
      log_database_pressure("heartbeat", exc)
      await wait_for_database_retry(stopped)
      continue
    try:
      await asyncio.wait_for(stopped.wait(), timeout=15.0)
    except asyncio.TimeoutError:
      pass
