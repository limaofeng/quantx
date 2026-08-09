"""GraphQL device management for outbound-only QMT Agents."""

from __future__ import annotations

from datetime import timedelta
from typing import List, Sequence

import strawberry
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice as AgentDeviceModel,
)
from quantx_infrastructure.models.agent_runtime import (
  RuntimeComponentHeartbeat,
)
from sqlalchemy import select

from quantx_api.auth.agent_service import AgentAuthService
from quantx_api.auth.tokens import utcnow

from ..security import principal_from_context
from ..types.agent_types import (
  AgentDevice,
  AgentDeviceMutationResult,
  AgentEnrollment,
)

AGENT_TTL = timedelta(seconds=90)


async def _to_graphql(
  db,
  device: AgentDeviceModel,
) -> AgentDevice:
  heartbeat = await db.get(
    RuntimeComponentHeartbeat,
    f"qmt-agent:{device.id}",
  )
  now = utcnow()
  online = (
    device.revoked_at is None
    and device.last_seen_at is not None
    and now - device.last_seen_at <= AGENT_TTL
  )
  status = "REVOKED" if device.revoked_at else "OFFLINE"
  if online:
    status = str(heartbeat.status if heartbeat is not None else "ONLINE")
  return AgentDevice(
    id=device.id,
    name=device.name,
    status=status,
    authorized_account_ids=list(device.authorized_account_ids or []),
    capabilities=list(device.capabilities or []),
    last_seen_at=device.last_seen_at,
    revoked_at=device.revoked_at,
    requires_reconciliation=online and status != "READY",
  )


@strawberry.type(description="QMT Agent 设备查询")
class AgentQuery:
  @strawberry.field(description="当前用户登记的 Agent 设备")
  async def agent_devices(
    self,
    info: strawberry.types.Info,
    include_revoked: bool = False,
  ) -> List[AgentDevice]:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      query = select(AgentDeviceModel).where(
        AgentDeviceModel.user_id == principal.user_id
      )
      if not include_revoked:
        query = query.where(AgentDeviceModel.revoked_at.is_(None))
      devices = (await db.execute(query)).scalars().all()
      return [await _to_graphql(db, device) for device in devices]


@strawberry.type(description="QMT Agent 设备管理")
class AgentMutation:
  @strawberry.mutation(description="创建十分钟内有效的一次性设备登记码")
  async def create_agent_enrollment(
    self,
    info: strawberry.types.Info,
    name: str = "QuantX QMT Agent",
    authorized_account_ids: Sequence[str] = (),
  ) -> AgentEnrollment:
    principal = principal_from_context(info.context)
    account_ids = list(authorized_account_ids)
    for account_id in account_ids:
      principal.require_account(account_id)
    async with AsyncSessionLocal() as db:
      enrollment = await AgentAuthService(db).create_enrollment(
        user_id=principal.user_id,
        name=name,
        authorized_account_ids=account_ids,
      )
    return AgentEnrollment(
      enrollment_code=enrollment.code,
      expires_at=enrollment.expires_at,
    )

  @strawberry.mutation(description="撤销 Agent 设备")
  async def revoke_agent_device(
    self,
    info: strawberry.types.Info,
    device_id: str,
  ) -> AgentDeviceMutationResult:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      revoked = await AgentAuthService(db).revoke(
        device_id=device_id,
        user_id=principal.user_id,
      )
    return AgentDeviceMutationResult(
      success=revoked,
      message="设备已撤销" if revoked else "设备不存在",
      device_id=device_id if revoked else None,
    )
