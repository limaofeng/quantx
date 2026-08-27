"""Atomic persistence operation for a personal QMT Agent handover."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  RuntimeComponentHeartbeat,
)
from quantx_infrastructure.services.agent_session_guard import REMOTE_AGENT_OFFLINE


async def converge_ready_agent(
  db: AsyncSession,
  *,
  device: AgentDevice,
  observed_at: datetime,
) -> list[str]:
  """Revoke superseded credentials in the transaction that proves READY."""

  others = list(
    (
      await db.execute(
        select(AgentDevice).where(
          AgentDevice.user_id == device.user_id,
          AgentDevice.id != device.id,
          AgentDevice.revoked_at.is_(None),
        )
      )
    ).scalars()
  )
  revoked: list[str] = []
  for other in others:
    is_pending_replacement = other.replaces_device_id == device.id
    if not device.replaces_device_id and is_pending_replacement:
      continue
    other.revoked_at = observed_at
    heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{other.id}",
    )
    if heartbeat is not None:
      details = dict(heartbeat.details or {})
      details.update(
        {
          "sessionActive": False,
          "reasonCode": REMOTE_AGENT_OFFLINE,
        }
      )
      heartbeat.status = "REVOKED"
      heartbeat.details = details
      heartbeat.updated_at = observed_at
    revoked.append(str(other.id))
  return revoked
