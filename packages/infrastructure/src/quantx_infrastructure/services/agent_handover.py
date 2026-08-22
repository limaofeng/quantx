"""Atomic persistence operation for a personal QMT Agent handover."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  RuntimeComponentHeartbeat,
)


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
      heartbeat.status = "REVOKED"
      heartbeat.updated_at = observed_at
    revoked.append(str(other.id))
  return revoked
