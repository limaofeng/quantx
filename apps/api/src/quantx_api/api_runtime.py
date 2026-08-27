"""Process identity for server-authoritative Agent session generations."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import RuntimeComponentHeartbeat
from quantx_infrastructure.services.agent_session_guard import (
  API_HEARTBEAT_COMPONENT,
  parse_utc_timestamp,
  utc_iso,
)

from quantx_api.auth.tokens import utcnow

API_INSTANCE_ID = str(uuid.uuid4())
API_STARTED_AT: datetime = utcnow()
API_HEARTBEAT_INTERVAL_SECONDS = 15.0
logger = logging.getLogger(__name__)


async def record_api_heartbeat(*, status: str = "READY") -> None:
  now = utcnow()
  details = {
    "apiInstanceId": API_INSTANCE_ID,
    "serverStartedAt": utc_iso(API_STARTED_AT),
  }
  async with AsyncSessionLocal() as db:
    heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      API_HEARTBEAT_COMPONENT,
      with_for_update=True,
    )
    if heartbeat is None:
      heartbeat = RuntimeComponentHeartbeat(
        component=API_HEARTBEAT_COMPONENT,
        instance_id=API_INSTANCE_ID,
        status=status,
        details=details,
        updated_at=now,
      )
      db.add(heartbeat)
    else:
      if heartbeat.instance_id != API_INSTANCE_ID:
        if status != "READY":
          return
        existing_started_at = parse_utc_timestamp(
          dict(heartbeat.details or {}).get("serverStartedAt")
        )
        if existing_started_at is not None and existing_started_at >= API_STARTED_AT:
          # A superseded process may finish a delayed refresh or shutdown after
          # the replacement is already serving. It must never overwrite the
          # newer API generation and invalidate that process's Agent sessions.
          return
      heartbeat.instance_id = API_INSTANCE_ID
      heartbeat.status = status
      heartbeat.details = details
      heartbeat.updated_at = now
    await db.commit()


async def run_api_heartbeat(stopped) -> None:
  while not stopped.is_set():
    try:
      await record_api_heartbeat()
    except asyncio.CancelledError:
      raise
    except Exception as exc:
      # A transient database outage must fail closed, but it must not kill the
      # refresher permanently after the database recovers.
      logger.warning(
        "无法刷新 API 实例心跳: %s",
        exc.__class__.__name__,
      )
    try:
      await asyncio.wait_for(
        stopped.wait(),
        timeout=API_HEARTBEAT_INTERVAL_SECONDS,
      )
    except TimeoutError:
      pass
