"""Read-only device validation shared by control and independent history transports."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from quantx_infrastructure.models.agent_runtime import AgentDevice

from .errors import unauthenticated
from .tokens import decode_access_token

HISTORY_AGENT_SCOPE = "agent:history"


@dataclass(frozen=True)
class AuthenticatedAgentSession:
  device: AgentDevice
  expires_at: datetime


async def authenticate_agent_session(
  db,
  settings,
  *,
  token: str,
  expected_device_id: str | None = None,
  history: bool = False,
) -> AuthenticatedAgentSession:
  claims = decode_access_token(token, settings)
  required_scopes = frozenset({HISTORY_AGENT_SCOPE}) if history else None
  # Exact purpose matching: history tokens cannot enter control/real-time sessions,
  # and unscoped control or public data tokens cannot authorize historical uploads.
  if claims.scopes != required_scopes:
    raise unauthenticated("Agent Token 用途不匹配")
  if expected_device_id and claims.device_session_id != expected_device_id:
    raise unauthenticated("Agent Token 与设备不匹配")
  result = await db.execute(
    select(AgentDevice)
    .where(AgentDevice.id == claims.device_session_id)
    .execution_options(populate_existing=True)
  )
  device = result.scalar_one_or_none()
  if (
    device is None or device.user_id != claims.user_id or device.revoked_at is not None
  ):
    raise unauthenticated("Agent 设备已撤销或不存在")
  return AuthenticatedAgentSession(device=device, expires_at=claims.expires_at)
