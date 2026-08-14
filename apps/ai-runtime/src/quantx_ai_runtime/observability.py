"""Heartbeat reporting without message or credential content."""

from __future__ import annotations

import asyncio
import os
import socket

from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import RuntimeComponentHeartbeat

from .config import AiRuntimeConfig, AiRuntimeConfigController, runtime_status


async def write_heartbeat(
  *,
  instance_id: str,
  config: AiRuntimeConfig,
  status: str,
) -> None:
  async with AsyncSessionLocal() as db:
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
    await write_heartbeat(
      instance_id=instance_id,
      config=config,
      status=runtime_status(
        config,
        dependencies_available=dependencies_available,
      ),
    )
    try:
      await asyncio.wait_for(stopped.wait(), timeout=15.0)
    except asyncio.TimeoutError:
      pass
