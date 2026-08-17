"""Account- and session-scoped persistence for opaque iOS notifications."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from quantx_domain.clock import utcnow
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.auth import (
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
from quantx_infrastructure.models.ios_notifications import (
  NOTIFICATION_ROUTE_TYPES,
  PUSH_CATEGORIES,
  IosNotificationEvent,
  IosNotificationOutbox,
  IosPushCategoryPreference,
  IosPushRegistration,
)

NOTIFICATION_MANAGE_PERMISSION = "notification:manage"


@dataclass(frozen=True)
class EnqueuedIosNotification:
  event_id: str
  outbox_id: str
  registration_id: str


def _category(value: str) -> str:
  normalized = str(value or "").strip().upper()
  if normalized not in PUSH_CATEGORIES:
    raise ValueError("invalid iOS notification category")
  return normalized


def _route_type(value: str) -> str:
  normalized = str(value or "").strip().lower()
  if normalized not in NOTIFICATION_ROUTE_TYPES:
    raise ValueError("invalid iOS notification route")
  return normalized


def _has_notification_permission(raw_permissions: object) -> bool:
  if not isinstance(raw_permissions, list):
    return False
  return NOTIFICATION_MANAGE_PERMISSION in {
    str(value).strip() for value in raw_permissions
  }


class IosNotificationEnqueueService:
  """Write random route events for currently eligible device sessions.

  This service contains no provider transport and never accepts business
  payload details. Callers own the surrounding database transaction.
  """

  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def enqueue_event(
    self,
    *,
    account_id: str,
    category: str,
    route_type: str,
    user_id: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
  ) -> tuple[EnqueuedIosNotification, ...]:
    normalized_account_id = str(account_id or "").strip()
    normalized_user_id = str(user_id or "").strip() or None
    if not normalized_account_id:
      raise ValueError("iOS notification account is required")
    normalized_category = _category(category)
    normalized_route = _route_type(route_type)
    now = utcnow()
    occurred = occurred_at or now
    expires = expires_at or (now + timedelta(hours=24))
    if expires <= occurred:
      raise ValueError("iOS notification expiry must follow occurrence")

    query = (
      select(
        IosPushRegistration,
        AuthDeviceSession.granted_permissions,
        AuthUser.permissions,
      )
      .join(
        IosPushCategoryPreference,
        IosPushCategoryPreference.registration_id == IosPushRegistration.id,
      )
      .join(
        AuthDeviceSession,
        and_(
          AuthDeviceSession.id == IosPushRegistration.device_session_id,
          AuthDeviceSession.user_id == IosPushRegistration.user_id,
        ),
      )
      .join(
        AuthUserAccountAccess,
        and_(
          AuthUserAccountAccess.user_id == IosPushRegistration.user_id,
          AuthUserAccountAccess.account_id == IosPushRegistration.account_id,
        ),
      )
      .join(AuthUser, AuthUser.id == IosPushRegistration.user_id)
      .where(
        IosPushRegistration.account_id == normalized_account_id,
        IosPushRegistration.invalidated_at.is_(None),
        IosPushRegistration.token_ciphertext.is_not(None),
        IosPushCategoryPreference.category == normalized_category,
        IosPushCategoryPreference.enabled.is_(True),
        AuthUser.is_active.is_(True),
        AuthDeviceSession.revoked_at.is_(None),
        AuthDeviceSession.expires_at > now,
      )
      .with_for_update(of=IosPushRegistration)
    )
    if normalized_user_id is not None:
      query = query.where(IosPushRegistration.user_id == normalized_user_id)

    rows = (await self.db.execute(query)).all()
    registrations = [
      registration
      for registration, granted_permissions, user_permissions in rows
      if _has_notification_permission(granted_permissions)
      and _has_notification_permission(user_permissions)
    ]

    queued: list[EnqueuedIosNotification] = []
    for registration in registrations:
      event_id = str(uuid.uuid4())
      outbox_id = str(uuid.uuid4())
      self.db.add(
        IosNotificationEvent(
          id=event_id,
          user_id=registration.user_id,
          device_session_id=registration.device_session_id,
          account_id=normalized_account_id,
          category=normalized_category,
          route_type=normalized_route,
          occurred_at=occurred,
          expires_at=expires,
          created_at=now,
          updated_at=now,
        )
      )
      self.db.add(
        IosNotificationOutbox(
          id=outbox_id,
          event_id=event_id,
          registration_id=registration.id,
          status="PENDING",
          attempt_count=0,
          available_at=now,
          created_at=now,
          updated_at=now,
        )
      )
      queued.append(
        EnqueuedIosNotification(
          event_id=event_id,
          outbox_id=outbox_id,
          registration_id=registration.id,
        )
      )
    await self.db.flush()
    return tuple(queued)


__all__ = [
  "EnqueuedIosNotification",
  "IosNotificationEnqueueService",
  "NOTIFICATION_MANAGE_PERMISSION",
]
