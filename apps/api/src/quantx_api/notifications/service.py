"""Durable APNs registration and opaque event routing without network I/O."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Optional

from cryptography.fernet import Fernet, InvalidToken
from quantx_infrastructure.models.auth import AuthDeviceSession
from quantx_infrastructure.models.ios_notifications import (
  NOTIFICATION_ROUTE_TYPES,
  PUSH_CATEGORIES,
  PUSH_ENVIRONMENTS,
  IosNotificationEvent,
  IosNotificationOutbox,
  IosPushCategoryPreference,
  IosPushRegistration,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_api.auth.errors import AuthError
from quantx_api.auth.tokens import utcnow

DEFAULT_PUSH_PREFERENCES: Mapping[str, bool] = {
  "ACTION_REQUIRED": True,
  "ORDER_UPDATE": True,
  "RISK_SAFETY": True,
  "AUTOMATION_ERROR": True,
  "CONNECTION_DATA": False,
}
_BUNDLE_ID = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
_APP_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,63}$")
_HEX_TOKEN = re.compile(r"^[0-9a-fA-F]+$")


@dataclass(frozen=True)
class PushPreferenceSnapshot:
  category: str
  enabled: bool


@dataclass(frozen=True)
class PushRegistrationSnapshot:
  id: str
  device_install_id: str
  app_bundle_id: str
  app_version: str
  apns_environment: str
  registered_at: datetime
  updated_at: datetime
  preferences: tuple[PushPreferenceSnapshot, ...]


@dataclass(frozen=True)
class NotificationRouteSnapshot:
  event_id: str
  category: str
  route_type: str
  occurred_at: datetime
  expires_at: datetime
  expired: bool


@dataclass(frozen=True)
class QueuedNotification:
  event_id: str
  outbox_id: str
  registration_id: str


def _invalid_device() -> AuthError:
  return AuthError(
    "INVALID_PUSH_DEVICE",
    "推送设备注册信息无效",
    status_code=400,
  )


def _normalize_install_id(value: str) -> str:
  try:
    return str(uuid.UUID((value or "").strip()))
  except (AttributeError, ValueError):
    raise _invalid_device() from None


def _normalize_bundle_id(value: str) -> str:
  normalized = (value or "").strip()
  if len(normalized) > 255 or not _BUNDLE_ID.fullmatch(normalized):
    raise _invalid_device()
  return normalized


def _normalize_app_version(value: str) -> str:
  normalized = (value or "").strip()
  if not _APP_VERSION.fullmatch(normalized):
    raise _invalid_device()
  return normalized


def _normalize_environment(value: str) -> str:
  normalized = (value or "").strip().upper()
  if normalized not in PUSH_ENVIRONMENTS:
    raise _invalid_device()
  return normalized


def _normalize_device_token(value: str) -> str:
  # Apple explicitly warns providers not to assume a fixed token size. The
  # transport representation remains even-length hexadecimal, with a generous
  # storage ceiling used only to reject abusive requests.
  normalized = "".join((value or "").split()).lower()
  if (
    not normalized
    or len(normalized) > 2048
    or len(normalized) % 2 != 0
    or not _HEX_TOKEN.fullmatch(normalized)
  ):
    raise _invalid_device()
  return normalized


def _normalize_category(value: str) -> str:
  normalized = (value or "").strip().upper()
  if normalized not in PUSH_CATEGORIES:
    raise AuthError(
      "INVALID_PUSH_CATEGORY",
      "通知类别无效",
      status_code=400,
    )
  return normalized


def _normalize_route_type(value: str) -> str:
  normalized = (value or "").strip().lower()
  if normalized not in NOTIFICATION_ROUTE_TYPES:
    raise AuthError(
      "INVALID_NOTIFICATION_ROUTE",
      "通知路由类型无效",
      status_code=400,
    )
  return normalized


def _normalize_event_id(value: str) -> str:
  try:
    return str(uuid.UUID((value or "").strip()))
  except (AttributeError, ValueError):
    raise AuthError(
      "INVALID_NOTIFICATION_EVENT",
      "通知事件标识无效",
      status_code=400,
    ) from None


def _fernet(signing_key: bytes) -> Fernet:
  derived = hashlib.sha256(b"quantx:apns-token:v1\0" + signing_key).digest()
  return Fernet(base64.urlsafe_b64encode(derived))


def _token_fingerprint(signing_key: bytes, token: str) -> str:
  return hmac.new(
    signing_key,
    b"quantx:apns-token-fingerprint:v1\0" + token.encode("ascii"),
    hashlib.sha256,
  ).hexdigest()


def build_minimal_apns_payload(
  *, event_id: str, category: str, route_type: str
) -> dict[str, object]:
  """Build a normal alert containing no account or trading facts."""

  normalized_event_id = _normalize_event_id(event_id)
  normalized_category = _normalize_category(category)
  normalized_route = _normalize_route_type(route_type)
  return {
    "aps": {
      "alert": {
        "title": "QuantX 有一项状态更新",
        "body": "打开应用查看当前状态",
      },
      "sound": "default",
    },
    "eventId": normalized_event_id,
    "category": normalized_category,
    "route": normalized_route,
  }


class PushNotificationService:
  """Transaction-scoped push persistence; callers own commit/rollback."""

  def __init__(self, db: AsyncSession, *, signing_key: bytes):
    if len(signing_key) < 32:
      raise AuthError(
        "AUTH_NOT_CONFIGURED",
        "服务端认证密钥尚未安全配置",
        status_code=503,
      )
    self.db = db
    self._signing_key = signing_key
    self._token_cipher = _fernet(signing_key)

  async def register(
    self,
    *,
    user_id: str,
    device_session_id: str,
    account_id: str,
    device_install_id: str,
    app_bundle_id: str,
    app_version: str,
    apns_environment: str,
    device_token: str,
  ) -> PushRegistrationSnapshot:
    install_id = _normalize_install_id(device_install_id)
    bundle_id = _normalize_bundle_id(app_bundle_id)
    version = _normalize_app_version(app_version)
    environment = _normalize_environment(apns_environment)
    token = _normalize_device_token(device_token)
    now = utcnow()

    install_registration = (
      await self.db.execute(
        select(IosPushRegistration)
        .where(
          IosPushRegistration.user_id == user_id,
          IosPushRegistration.app_bundle_id == bundle_id,
          IosPushRegistration.apns_environment == environment,
          IosPushRegistration.device_install_id == install_id,
        )
        .with_for_update()
      )
    ).scalar_one_or_none()
    active_session_registrations = (
      await self.db.execute(
        select(IosPushRegistration)
        .where(
          IosPushRegistration.user_id == user_id,
          IosPushRegistration.device_session_id == device_session_id,
          IosPushRegistration.app_bundle_id == bundle_id,
          IosPushRegistration.apns_environment == environment,
          IosPushRegistration.invalidated_at.is_(None),
        )
        .with_for_update()
      )
    ).scalars().all()

    registration = install_registration
    if registration is None and active_session_registrations:
      registration = active_session_registrations[0]
    for existing in active_session_registrations:
      if registration is not None and existing.id == registration.id:
        continue
      existing.invalidated_at = now
      existing.token_ciphertext = None
    if active_session_registrations:
      await self.db.flush()

    fingerprint = _token_fingerprint(self._signing_key, token)
    ciphertext = self._token_cipher.encrypt(token.encode("ascii")).decode("ascii")
    if registration is None:
      registration = IosPushRegistration(
        id=str(uuid.uuid4()),
        user_id=user_id,
        device_session_id=device_session_id,
        account_id=account_id,
        device_install_id=install_id,
        app_bundle_id=bundle_id,
        app_version=version,
        apns_environment=environment,
        token_ciphertext=ciphertext,
        token_fingerprint=fingerprint,
        registered_at=now,
        last_seen_at=now,
        invalidated_at=None,
        created_at=now,
        updated_at=now,
      )
      self.db.add(registration)
    else:
      registration.user_id = user_id
      registration.device_session_id = device_session_id
      registration.account_id = account_id
      registration.device_install_id = install_id
      registration.app_bundle_id = bundle_id
      registration.app_version = version
      registration.apns_environment = environment
      registration.token_ciphertext = ciphertext
      registration.token_fingerprint = fingerprint
      registration.registered_at = now
      registration.last_seen_at = now
      registration.invalidated_at = None
      registration.updated_at = now
    await self.db.flush()

    preferences = await self._ensure_preferences(registration.id)
    return self._snapshot(registration, preferences)

  async def update_preferences(
    self,
    *,
    user_id: str,
    device_session_id: str,
    account_id: str,
    device_install_id: str,
    app_bundle_id: str,
    apns_environment: str,
    preferences: Iterable[tuple[str, bool]],
  ) -> PushRegistrationSnapshot:
    registration = await self._active_registration(
      user_id=user_id,
      device_session_id=device_session_id,
      account_id=account_id,
      device_install_id=_normalize_install_id(device_install_id),
      app_bundle_id=_normalize_bundle_id(app_bundle_id),
      apns_environment=_normalize_environment(apns_environment),
      lock=True,
    )
    if registration is None:
      raise AuthError(
        "PUSH_DEVICE_NOT_REGISTERED",
        "当前安装尚未注册推送",
        status_code=404,
      )

    normalized: dict[str, bool] = {}
    for category, enabled in preferences:
      normalized_category = _normalize_category(category)
      if normalized_category in normalized or not isinstance(enabled, bool):
        raise AuthError(
          "INVALID_PUSH_PREFERENCES",
          "通知偏好无效",
          status_code=400,
        )
      normalized[normalized_category] = enabled
    if not normalized:
      raise AuthError(
        "INVALID_PUSH_PREFERENCES",
        "通知偏好不能为空",
        status_code=400,
      )

    existing = {
      row.category: row
      for row in (
        await self.db.execute(
          select(IosPushCategoryPreference)
          .where(IosPushCategoryPreference.registration_id == registration.id)
          .with_for_update()
        )
      ).scalars().all()
    }
    for category, enabled in normalized.items():
      preference = existing.get(category)
      if preference is None:
        preference = IosPushCategoryPreference(
          registration_id=registration.id,
          category=category,
          enabled=enabled,
          created_at=utcnow(),
          updated_at=utcnow(),
        )
        self.db.add(preference)
        existing[category] = preference
      else:
        preference.enabled = enabled
        preference.updated_at = utcnow()
    registration.last_seen_at = utcnow()
    registration.updated_at = utcnow()
    await self.db.flush()
    return self._snapshot(registration, existing.values())

  async def unregister(
    self,
    *,
    user_id: str,
    device_session_id: str,
    account_id: str,
    device_install_id: str,
    app_bundle_id: str,
    apns_environment: str,
  ) -> bool:
    registration = await self._active_registration(
      user_id=user_id,
      device_session_id=device_session_id,
      account_id=account_id,
      device_install_id=_normalize_install_id(device_install_id),
      app_bundle_id=_normalize_bundle_id(app_bundle_id),
      apns_environment=_normalize_environment(apns_environment),
      lock=True,
    )
    if registration is None:
      return False
    now = utcnow()
    registration.invalidated_at = now
    registration.last_seen_at = now
    registration.token_ciphertext = None
    registration.updated_at = now
    await self.db.execute(
      update(IosNotificationOutbox)
      .where(
        IosNotificationOutbox.registration_id == registration.id,
        IosNotificationOutbox.status.in_(("PENDING", "RETRY")),
      )
      .values(status="DISCARDED", last_error_code="DEVICE_UNREGISTERED")
    )
    await self.db.flush()
    return True

  async def resolve_event(
    self,
    *,
    event_id: str,
    user_id: str,
    device_session_id: str,
    account_id: str,
  ) -> Optional[NotificationRouteSnapshot]:
    normalized_event_id = _normalize_event_id(event_id)
    event = (
      await self.db.execute(
        select(IosNotificationEvent).where(
          IosNotificationEvent.id == normalized_event_id,
          IosNotificationEvent.user_id == user_id,
          IosNotificationEvent.device_session_id == device_session_id,
          IosNotificationEvent.account_id == account_id,
        )
      )
    ).scalar_one_or_none()
    if event is None:
      return None
    return NotificationRouteSnapshot(
      event_id=event.id,
      category=event.category,
      route_type=event.route_type,
      occurred_at=event.occurred_at,
      expires_at=event.expires_at,
      expired=event.expires_at <= utcnow(),
    )

  async def enqueue_event(
    self,
    *,
    user_id: str,
    account_id: str,
    category: str,
    route_type: str,
    occurred_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
  ) -> tuple[QueuedNotification, ...]:
    """Persist one random event per eligible active device session."""

    normalized_category = _normalize_category(category)
    normalized_route = _normalize_route_type(route_type)
    now = utcnow()
    occurred = occurred_at or now
    expires = expires_at or (now + timedelta(hours=24))
    if expires <= occurred:
      raise AuthError(
        "INVALID_NOTIFICATION_EXPIRY",
        "通知事件有效期无效",
        status_code=400,
      )

    registrations = (
      await self.db.execute(
        select(IosPushRegistration)
        .join(
          IosPushCategoryPreference,
          IosPushCategoryPreference.registration_id == IosPushRegistration.id,
        )
        .join(
          AuthDeviceSession,
          AuthDeviceSession.id == IosPushRegistration.device_session_id,
        )
        .where(
          IosPushRegistration.user_id == user_id,
          IosPushRegistration.account_id == account_id,
          IosPushRegistration.invalidated_at.is_(None),
          IosPushRegistration.token_ciphertext.is_not(None),
          IosPushCategoryPreference.category == normalized_category,
          IosPushCategoryPreference.enabled.is_(True),
          AuthDeviceSession.revoked_at.is_(None),
          AuthDeviceSession.expires_at > now,
        )
      )
    ).scalars().all()

    queued: list[QueuedNotification] = []
    for registration in registrations:
      event_id = str(uuid.uuid4())
      outbox_id = str(uuid.uuid4())
      self.db.add(
        IosNotificationEvent(
          id=event_id,
          user_id=user_id,
          device_session_id=registration.device_session_id,
          account_id=account_id,
          category=normalized_category,
          route_type=normalized_route,
          occurred_at=occurred,
          expires_at=expires,
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
        )
      )
      queued.append(
        QueuedNotification(
          event_id=event_id,
          outbox_id=outbox_id,
          registration_id=registration.id,
        )
      )
    await self.db.flush()
    return tuple(queued)

  def decrypt_device_token(self, ciphertext: str) -> str:
    """Decrypt only at the future injected transport boundary."""

    try:
      return self._token_cipher.decrypt(ciphertext.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeError, ValueError):
      raise AuthError(
        "PUSH_TOKEN_UNAVAILABLE",
        "推送设备凭据不可用",
        status_code=503,
      ) from None

  async def _ensure_preferences(
    self, registration_id: str
  ) -> tuple[IosPushCategoryPreference, ...]:
    existing = {
      row.category: row
      for row in (
        await self.db.execute(
          select(IosPushCategoryPreference).where(
            IosPushCategoryPreference.registration_id == registration_id
          )
        )
      ).scalars().all()
    }
    for category, enabled in DEFAULT_PUSH_PREFERENCES.items():
      if category not in existing:
        preference = IosPushCategoryPreference(
          registration_id=registration_id,
          category=category,
          enabled=enabled,
          created_at=utcnow(),
          updated_at=utcnow(),
        )
        self.db.add(preference)
        existing[category] = preference
    await self.db.flush()
    return tuple(existing.values())

  async def _active_registration(
    self,
    *,
    user_id: str,
    device_session_id: str,
    account_id: str,
    device_install_id: str,
    app_bundle_id: str,
    apns_environment: str,
    lock: bool,
  ) -> Optional[IosPushRegistration]:
    query = select(IosPushRegistration).where(
      IosPushRegistration.user_id == user_id,
      IosPushRegistration.device_session_id == device_session_id,
      IosPushRegistration.account_id == account_id,
      IosPushRegistration.device_install_id == device_install_id,
      IosPushRegistration.app_bundle_id == app_bundle_id,
      IosPushRegistration.apns_environment == apns_environment,
      IosPushRegistration.invalidated_at.is_(None),
    )
    if lock:
      query = query.with_for_update()
    return (await self.db.execute(query)).scalar_one_or_none()

  @staticmethod
  def _snapshot(
    registration: IosPushRegistration,
    preferences: Iterable[IosPushCategoryPreference],
  ) -> PushRegistrationSnapshot:
    return PushRegistrationSnapshot(
      id=registration.id,
      device_install_id=registration.device_install_id,
      app_bundle_id=registration.app_bundle_id,
      app_version=registration.app_version,
      apns_environment=registration.apns_environment,
      registered_at=registration.registered_at,
      updated_at=registration.updated_at or registration.registered_at,
      preferences=tuple(
        PushPreferenceSnapshot(category=row.category, enabled=bool(row.enabled))
        for row in sorted(preferences, key=lambda value: value.category)
      ),
    )
