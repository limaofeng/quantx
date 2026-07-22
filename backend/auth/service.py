"""Database-backed login, refresh rotation, revocation, and principal loading."""

import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings, settings
from models.auth import (
  AuthAuditEvent,
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)

from .errors import AuthError, unauthenticated
from .passwords import hash_password, verify_password
from .principal import Principal
from .rate_limit import login_rate_limiter
from .tokens import (
  decode_access_token,
  digest_refresh_token,
  issue_access_token,
  issue_refresh_token,
  refresh_expiry,
  require_signing_key,
  utcnow,
)

logger = logging.getLogger(__name__)
_DUMMY_PASSWORD_HASH = hash_password("quantx-dummy-password-never-used")


@dataclass(frozen=True)
class SessionGrant:
  access_token: str
  refresh_token: str
  access_token_expires_at: datetime
  refresh_token_expires_at: datetime
  principal: Principal


class AuthService:
  def __init__(
    self,
    db: AsyncSession,
    auth_settings: Optional[Settings] = None,
  ):
    self.db = db
    self.settings = auth_settings or settings

  @staticmethod
  async def bootstrap_from_settings(
    db: AsyncSession, auth_settings: Optional[Settings] = None
  ) -> bool:
    """Create the first local user only when complete env configuration exists."""
    auth_settings = auth_settings or settings
    username = auth_settings.auth_bootstrap_username.strip().lower()
    password = auth_settings.auth_bootstrap_password
    if not username and not password:
      return False
    if not username or not password:
      raise RuntimeError(
        "AUTH_BOOTSTRAP_USERNAME 与 AUTH_BOOTSTRAP_PASSWORD 必须同时配置"
      )
    if len(username) > 80 or len(password) < 12:
      raise RuntimeError("认证引导用户名或密码不满足安全要求")
    require_signing_key(auth_settings)

    result = await db.execute(select(AuthUser).where(AuthUser.username == username))
    if result.scalar_one_or_none() is not None:
      logger.info("认证引导用户已存在，未覆盖密码或权限")
      return False

    user_id = str(uuid.uuid4())
    permissions = sorted(
      {
        value.strip()
        for value in auth_settings.auth_bootstrap_permissions
        if value.strip()
      }
    )
    account_ids = tuple(
      dict.fromkeys(
        value.strip()
        for value in auth_settings.auth_bootstrap_account_ids
        if value.strip()
      )
    )
    db.add(
      AuthUser(
        id=user_id,
        username=username,
        display_name=auth_settings.auth_bootstrap_display_name.strip() or "QuantX 用户",
        password_hash=hash_password(password),
        is_active=True,
        permissions=permissions,
      )
    )
    for index, account_id in enumerate(account_ids):
      db.add(
        AuthUserAccountAccess(
          user_id=user_id,
          account_id=account_id,
          is_default=index == 0,
        )
      )
    await db.commit()
    logger.info(
      "认证引导用户创建完成（只记录授权账户数量=%d、权限数量=%d）",
      len(account_ids),
      len(permissions),
    )
    return True

  async def login(
    self,
    username: str,
    password: str,
    *,
    device_name: Optional[str],
    client_fingerprint: str,
    request_id: str,
  ) -> SessionGrant:
    require_signing_key(self.settings)
    normalized_username = username.strip().lower()
    subject_fingerprint = self._subject_fingerprint(
      normalized_username, client_fingerprint
    )
    recent_failure_count = await self._recent_login_failure_count(subject_fingerprint)
    if recent_failure_count >= max(
      1, self.settings.auth_login_rate_limit_attempts
    ) or await login_rate_limiter.is_limited(
      subject_fingerprint,
      self.settings.auth_login_rate_limit_attempts,
      self.settings.auth_login_rate_limit_window_seconds,
    ):
      await self._audit(
        "LOGIN",
        "DENIED",
        request_id=request_id,
        reason_code="RATE_LIMITED",
        subject_fingerprint=subject_fingerprint,
      )
      await self.db.commit()
      raise AuthError(
        "RATE_LIMITED",
        "登录尝试过于频繁，请稍后重试",
        status_code=429,
        retryable=True,
      )

    result = await self.db.execute(
      select(AuthUser).where(AuthUser.username == normalized_username)
    )
    user = result.scalar_one_or_none()
    password_valid = verify_password(
      password, user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    )
    if user is None or not password_valid or not user.is_active:
      await login_rate_limiter.record_failure(
        subject_fingerprint,
        self.settings.auth_login_rate_limit_window_seconds,
      )
      await self._audit(
        "LOGIN",
        "DENIED",
        request_id=request_id,
        reason_code="INVALID_CREDENTIALS",
        user_id=user.id if user is not None else None,
        subject_fingerprint=subject_fingerprint,
      )
      await self.db.commit()
      raise unauthenticated("用户名或密码错误")

    await login_rate_limiter.clear(subject_fingerprint)
    now = utcnow()
    refresh_token = issue_refresh_token()
    session = AuthDeviceSession(
      id=str(uuid.uuid4()),
      user_id=user.id,
      refresh_token_hash=digest_refresh_token(refresh_token, self.settings),
      expires_at=refresh_expiry(self.settings),
      revoked_at=None,
      last_used_at=now,
      device_name=(device_name or "").strip()[:120] or None,
    )
    self.db.add(session)
    await self.db.flush()
    access_token, access_expiry = issue_access_token(user.id, session.id, self.settings)
    principal = await self._principal(user, session, access_expiry)
    await self._audit(
      "LOGIN",
      "SUCCEEDED",
      request_id=request_id,
      user_id=user.id,
      device_session_id=session.id,
      subject_fingerprint=subject_fingerprint,
    )
    await self.db.commit()
    return SessionGrant(
      access_token=access_token,
      refresh_token=refresh_token,
      access_token_expires_at=access_expiry,
      refresh_token_expires_at=session.expires_at,
      principal=principal,
    )

  async def refresh(self, refresh_token: str, request_id: str) -> SessionGrant:
    require_signing_key(self.settings)
    token_hash = digest_refresh_token(refresh_token, self.settings)
    result = await self.db.execute(
      select(AuthDeviceSession)
      .where(AuthDeviceSession.refresh_token_hash == token_hash)
      .with_for_update()
    )
    session = result.scalar_one_or_none()
    now = utcnow()
    if session is None or session.revoked_at is not None or session.expires_at <= now:
      await self._audit(
        "REFRESH",
        "DENIED",
        request_id=request_id,
        reason_code="INVALID_REFRESH_TOKEN",
        device_session_id=session.id if session is not None else None,
      )
      await self.db.commit()
      raise unauthenticated("刷新令牌无效或已过期")

    user_result = await self.db.execute(
      select(AuthUser).where(AuthUser.id == session.user_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
      session.revoked_at = now
      await self._audit(
        "REFRESH",
        "DENIED",
        request_id=request_id,
        reason_code="USER_DISABLED",
        user_id=session.user_id,
        device_session_id=session.id,
      )
      await self.db.commit()
      raise unauthenticated()

    rotated_refresh_token = issue_refresh_token()
    session.refresh_token_hash = digest_refresh_token(
      rotated_refresh_token, self.settings
    )
    session.expires_at = refresh_expiry(self.settings)
    session.last_used_at = now
    access_token, access_expiry = issue_access_token(user.id, session.id, self.settings)
    principal = await self._principal(user, session, access_expiry)
    await self._audit(
      "REFRESH",
      "SUCCEEDED",
      request_id=request_id,
      user_id=user.id,
      device_session_id=session.id,
    )
    await self.db.commit()
    return SessionGrant(
      access_token=access_token,
      refresh_token=rotated_refresh_token,
      access_token_expires_at=access_expiry,
      refresh_token_expires_at=session.expires_at,
      principal=principal,
    )

  async def authenticate(self, access_token: str) -> Principal:
    claims = decode_access_token(access_token, self.settings)
    session_result = await self.db.execute(
      select(AuthDeviceSession).where(AuthDeviceSession.id == claims.device_session_id)
    )
    session = session_result.scalar_one_or_none()
    now = utcnow()
    if (
      session is None
      or session.user_id != claims.user_id
      or session.revoked_at is not None
      or session.expires_at <= now
    ):
      raise unauthenticated()
    user_result = await self.db.execute(
      select(AuthUser).where(AuthUser.id == claims.user_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
      raise unauthenticated()
    return await self._principal(user, session, claims.expires_at)

  async def logout(
    self, principal: Principal, *, all_devices: bool, request_id: str
  ) -> None:
    now = utcnow()
    if all_devices:
      result = await self.db.execute(
        select(AuthDeviceSession).where(
          AuthDeviceSession.user_id == principal.user_id,
          AuthDeviceSession.revoked_at.is_(None),
        )
      )
      for session in result.scalars().all():
        session.revoked_at = now
    else:
      result = await self.db.execute(
        select(AuthDeviceSession).where(
          AuthDeviceSession.id == principal.device_session_id
        )
      )
      session = result.scalar_one_or_none()
      if session is not None:
        session.revoked_at = now
    await self._audit(
      "LOGOUT_ALL" if all_devices else "LOGOUT",
      "SUCCEEDED",
      request_id=request_id,
      user_id=principal.user_id,
      device_session_id=principal.device_session_id,
    )
    await self.db.commit()

  async def _principal(
    self,
    user: AuthUser,
    session: AuthDeviceSession,
    access_expiry: datetime,
  ) -> Principal:
    access_result = await self.db.execute(
      select(AuthUserAccountAccess.account_id)
      .where(AuthUserAccountAccess.user_id == user.id)
      .order_by(
        AuthUserAccountAccess.is_default.desc(),
        AuthUserAccountAccess.created_at.asc(),
        AuthUserAccountAccess.account_id.asc(),
      )
    )
    account_ids: Tuple[str, ...] = tuple(access_result.scalars().all())
    return Principal(
      user_id=user.id,
      username=user.username,
      display_name=user.display_name,
      device_session_id=session.id,
      access_token_expires_at=access_expiry,
      permissions=frozenset(str(value) for value in (user.permissions or [])),
      authorized_account_ids=account_ids,
    )

  def _subject_fingerprint(self, username: str, client_fingerprint: str) -> str:
    key = require_signing_key(self.settings)
    return hmac.new(
      key,
      f"{username}\n{client_fingerprint}".encode("utf-8"),
      hashlib.sha256,
    ).hexdigest()

  async def _recent_login_failure_count(self, subject_fingerprint: str) -> int:
    window_start = utcnow() - timedelta(
      seconds=max(1, self.settings.auth_login_rate_limit_window_seconds)
    )
    result = await self.db.execute(
      select(func.count())
      .select_from(AuthAuditEvent)
      .where(
        AuthAuditEvent.event_type == "LOGIN",
        AuthAuditEvent.outcome == "DENIED",
        AuthAuditEvent.subject_fingerprint == subject_fingerprint,
        AuthAuditEvent.occurred_at >= window_start,
      )
    )
    return int(result.scalar_one() or 0)

  async def _audit(
    self,
    event_type: str,
    outcome: str,
    *,
    request_id: str,
    reason_code: Optional[str] = None,
    user_id: Optional[str] = None,
    device_session_id: Optional[str] = None,
    subject_fingerprint: Optional[str] = None,
  ) -> None:
    self.db.add(
      AuthAuditEvent(
        id=str(uuid.uuid4()),
        event_type=event_type,
        outcome=outcome,
        reason_code=reason_code,
        user_id=user_id,
        device_session_id=device_session_id,
        subject_fingerprint=subject_fingerprint,
        request_id=request_id[:64],
        occurred_at=utcnow(),
      )
    )
