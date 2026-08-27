"""Device enrollment and short-lived access tokens for QMT agents."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from quantx_infrastructure.config.settings import Settings, settings
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  AgentEnrollmentCode,
  RuntimeComponentHeartbeat,
)
from quantx_infrastructure.services.agent_handover import (
  converge_ready_agent,
)
from quantx_infrastructure.services.agent_session_guard import (
  API_HEARTBEAT_COMPONENT,
  REMOTE_AGENT_OFFLINE,
  evaluate_agent_session,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_api.auth.errors import unauthenticated
from quantx_api.auth.tokens import (
  decode_access_token,
  digest_refresh_token,
  issue_access_token,
  utcnow,
)


@dataclass(frozen=True)
class AgentEnrollment:
  code: str
  expires_at: object


@dataclass(frozen=True)
class AgentCredential:
  device_id: str
  device_secret: str


@dataclass(frozen=True)
class AgentAccessGrant:
  access_token: str
  expires_at: object
  device: AgentDevice


@dataclass(frozen=True)
class AgentHandoverCancellation:
  deleted_enrollment_count: int
  revoked_device_ids: tuple[str, ...]


@dataclass(frozen=True)
class AuthenticatedAgentSession:
  device: AgentDevice
  expires_at: object
  access_token_fingerprint: str


class AgentAuthService:
  def __init__(
    self,
    db: AsyncSession,
    auth_settings: Optional[Settings] = None,
  ) -> None:
    self.db = db
    self.settings = auth_settings or settings

  async def create_enrollment(
    self,
    *,
    user_id: str,
    name: str,
    authorized_account_ids: list[str],
  ) -> AgentEnrollment:
    now = utcnow()
    await self.db.execute(
      delete(AgentEnrollmentCode).where(AgentEnrollmentCode.expires_at <= now)
    )
    await self.db.execute(
      delete(AgentEnrollmentCode).where(
        AgentEnrollmentCode.user_id == user_id,
        AgentEnrollmentCode.consumed_at.is_(None),
      )
    )
    current = await self.current_device(user_id=user_id)
    if current is not None:
      candidates = (
        await self.db.execute(
          select(AgentDevice).where(
            AgentDevice.user_id == user_id,
            AgentDevice.revoked_at.is_(None),
            AgentDevice.replaces_device_id == current.id,
          )
        )
      ).scalars()
      for candidate in candidates:
        candidate.revoked_at = now
    code = secrets.token_urlsafe(32)
    expires_at = now + timedelta(minutes=10)
    self.db.add(
      AgentEnrollmentCode(
        code_hash=digest_refresh_token(code, self.settings),
        user_id=user_id,
        name=name.strip()[:120] or "QuantX QMT Agent",
        authorized_account_ids=list(dict.fromkeys(authorized_account_ids)),
        created_at=now,
        expires_at=expires_at,
        consumed_at=None,
        replaces_device_id=current.id if current is not None else None,
      )
    )
    await self.db.commit()
    return AgentEnrollment(code=code, expires_at=expires_at)

  async def exchange_enrollment(self, code: str) -> AgentCredential:
    now = utcnow()
    code_hash = digest_refresh_token(code, self.settings)
    result = await self.db.execute(
      select(AgentEnrollmentCode)
      .where(AgentEnrollmentCode.code_hash == code_hash)
      .with_for_update()
    )
    enrollment = result.scalar_one_or_none()
    if (
      enrollment is None
      or enrollment.consumed_at is not None
      or enrollment.expires_at <= now
    ):
      raise unauthenticated("Agent 登记码无效或已过期")
    device_secret = secrets.token_urlsafe(48)
    device = AgentDevice(
      id=str(uuid.uuid4()),
      user_id=enrollment.user_id,
      name=enrollment.name,
      secret_hash=digest_refresh_token(device_secret, self.settings),
      authorized_account_ids=enrollment.authorized_account_ids,
      capabilities=[],
      last_seen_at=None,
      revoked_at=None,
      replaces_device_id=enrollment.replaces_device_id,
    )
    enrollment.consumed_at = now
    self.db.add(device)
    await self.db.commit()
    return AgentCredential(device_id=device.id, device_secret=device_secret)

  async def current_device(self, *, user_id: str) -> AgentDevice | None:
    devices = list(
      (
        await self.db.execute(
          select(AgentDevice).where(
            AgentDevice.user_id == user_id,
            AgentDevice.revoked_at.is_(None),
          )
        )
      ).scalars()
    )
    if not devices:
      return None
    heartbeat_rows = list(
      (
        await self.db.execute(
          select(RuntimeComponentHeartbeat).where(
            RuntimeComponentHeartbeat.component.in_(
              [f"qmt-agent:{device.id}" for device in devices]
            )
          )
        )
      ).scalars()
    )
    heartbeat_by_device_id = {
      str(row.component).removeprefix("qmt-agent:"): row for row in heartbeat_rows
    }
    api_heartbeat = await self.db.get(
      RuntimeComponentHeartbeat,
      API_HEARTBEAT_COMPONENT,
    )
    observed_at = utcnow()
    replacement_target_ids = {
      str(device.replaces_device_id) for device in devices if device.replaces_device_id
    }

    def timestamp(value: datetime | None) -> float:
      if value is None:
        return 0.0
      if value.tzinfo is None:
        value = value.replace(tzinfo=utcnow().tzinfo)
      return value.timestamp()

    def key(device: AgentDevice) -> tuple[int, int, float, float]:
      session_state = evaluate_agent_session(
        heartbeat_by_device_id.get(str(device.id)),
        api_heartbeat,
        now=observed_at,
        acceptable_statuses={"READY"},
      )
      return (
        int(session_state.current),
        int(str(device.id) in replacement_target_ids),
        timestamp(device.last_seen_at),
        timestamp(device.created_at),
      )

    return max(devices, key=key)

  async def cancel_handover(self, *, user_id: str) -> AgentHandoverCancellation:
    now = utcnow()
    deleted = await self.db.execute(
      delete(AgentEnrollmentCode).where(
        AgentEnrollmentCode.user_id == user_id,
        AgentEnrollmentCode.consumed_at.is_(None),
      )
    )
    current = await self.current_device(user_id=user_id)
    revoked_device_ids: list[str] = []
    if current is not None:
      candidates = (
        await self.db.execute(
          select(AgentDevice).where(
            AgentDevice.user_id == user_id,
            AgentDevice.revoked_at.is_(None),
            AgentDevice.replaces_device_id == current.id,
          )
        )
      ).scalars()
      for candidate in candidates:
        candidate.revoked_at = now
        heartbeat = await self.db.get(
          RuntimeComponentHeartbeat,
          f"qmt-agent:{candidate.id}",
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
          heartbeat.updated_at = now
        revoked_device_ids.append(str(candidate.id))
    await self.db.commit()
    return AgentHandoverCancellation(
      deleted_enrollment_count=int(deleted.rowcount or 0),
      revoked_device_ids=tuple(revoked_device_ids),
    )

  async def converge_ready_device(
    self,
    *,
    device: AgentDevice,
    observed_at: datetime,
  ) -> list[str]:
    """Revoke superseded credentials only after this device is truly READY."""
    return await converge_ready_agent(
      self.db,
      device=device,
      observed_at=observed_at,
    )

  async def issue_agent_token(
    self,
    *,
    device_id: str,
    device_secret: str,
  ) -> AgentAccessGrant:
    result = await self.db.execute(
      select(AgentDevice).where(AgentDevice.id == device_id)
    )
    device = result.scalar_one_or_none()
    if (
      device is None
      or device.revoked_at is not None
      or not secrets.compare_digest(
        device.secret_hash,
        digest_refresh_token(device_secret, self.settings),
      )
    ):
      raise unauthenticated("Agent 设备凭证无效")
    token, expires_at = issue_access_token(
      device.user_id,
      device.id,
      self.settings,
    )
    return AgentAccessGrant(
      access_token=token,
      expires_at=expires_at,
      device=device,
    )

  async def authenticate_agent(
    self,
    *,
    token: str,
    expected_device_id: Optional[str] = None,
  ) -> AgentDevice:
    session = await self.authenticate_agent_session(
      token=token,
      expected_device_id=expected_device_id,
    )
    return session.device

  async def authenticate_agent_session(
    self,
    *,
    token: str,
    expected_device_id: Optional[str] = None,
  ) -> AuthenticatedAgentSession:
    claims = decode_access_token(token, self.settings)
    if expected_device_id and claims.device_session_id != expected_device_id:
      raise unauthenticated("Agent Token 与设备不匹配")
    result = await self.db.execute(
      select(AgentDevice).where(AgentDevice.id == claims.device_session_id)
    )
    device = result.scalar_one_or_none()
    if (
      device is None
      or device.user_id != claims.user_id
      or device.revoked_at is not None
    ):
      raise unauthenticated("Agent 设备已撤销或不存在")
    return AuthenticatedAgentSession(
      device=device,
      expires_at=claims.expires_at,
      access_token_fingerprint=hashlib.sha256(token.encode("utf-8")).hexdigest(),
    )

  async def revoke(self, *, device_id: str, user_id: str) -> bool:
    result = await self.db.execute(
      select(AgentDevice)
      .where(AgentDevice.id == device_id, AgentDevice.user_id == user_id)
      .with_for_update()
    )
    device = result.scalar_one_or_none()
    if device is None:
      return False
    if device.revoked_at is None:
      now = utcnow()
      device.revoked_at = now
      heartbeat = await self.db.get(
        RuntimeComponentHeartbeat,
        f"qmt-agent:{device_id}",
        with_for_update=True,
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
        heartbeat.updated_at = now
      await self.db.commit()
    return True
