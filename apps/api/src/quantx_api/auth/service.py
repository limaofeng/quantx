"""Database-backed login, refresh rotation, revocation, and principal loading."""

import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Iterable, Optional, Tuple

from quantx_infrastructure.config.settings import Settings, settings
from quantx_infrastructure.models.auth import (
  AuthAuditEvent,
  AuthConsumedRefreshToken,
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import AuthError, unauthenticated
from .passwords import hash_password, verify_password
from .principal import Principal
from .rate_limit import login_rate_limiter
from .tokens import (
  AccessClaims,
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

# Native clients receive only purpose-specific product scopes. Broad Web/admin
# permissions are intentionally absent even when the user owns them.
NATIVE_SESSION_SCOPES = frozenset(
  {
    "limit-up:control",
    "liquidation:control",
    "market:read",
    "notification:manage",
    "orders:read",
    "portfolio:read",
    "strategy:control",
    "strategy:read",
    "system-status:read",
    "t-trade:control",
    "trade:approve",
    "trade:manual",
    "watchlist:write",
  }
)


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
    native_session: bool = True,
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
    granted_permissions: Optional[Tuple[str, ...]] = None
    try:
      await self._require_personal_account(user.id)
      if native_session:
        granted_permissions = self._resolve_native_permissions(user.permissions)
    except AuthError as exc:
      await self._audit(
        "LOGIN",
        "DENIED",
        request_id=request_id,
        reason_code=exc.code,
        user_id=user.id,
        subject_fingerprint=subject_fingerprint,
      )
      await self.db.commit()
      raise
    return await self._issue_session(
      user,
      device_name=device_name,
      request_id=request_id,
      audit_event_type="LOGIN",
      subject_fingerprint=subject_fingerprint,
      granted_permissions=granted_permissions,
    )

  async def development_login(
    self,
    *,
    device_name: Optional[str],
    client_fingerprint: str,
    request_id: str,
  ) -> SessionGrant:
    """Issue a session for a configured database user in development only."""
    if (
      not self.settings.is_development or not self.settings.auth_development_auto_login
    ):
      raise AuthError(
        "DEVELOPMENT_LOGIN_DISABLED",
        "开发自动登录未启用",
        status_code=404,
      )

    normalized_username = self.settings.auth_development_username.strip().lower()
    if not normalized_username:
      raise AuthError(
        "AUTH_NOT_CONFIGURED",
        "开发自动登录用户尚未配置",
        status_code=503,
      )
    require_signing_key(self.settings)
    result = await self.db.execute(
      select(AuthUser).where(AuthUser.username == normalized_username)
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
      raise AuthError(
        "AUTH_NOT_CONFIGURED",
        "开发自动登录用户不存在或已停用",
        status_code=503,
      )

    return await self._issue_session(
      user,
      device_name=device_name or "QuantX Web Development",
      request_id=request_id,
      audit_event_type="DEVELOPMENT_LOGIN",
      subject_fingerprint=self._subject_fingerprint(
        normalized_username, client_fingerprint
      ),
    )

  async def _issue_session(
    self,
    user: AuthUser,
    *,
    device_name: Optional[str],
    request_id: str,
    audit_event_type: str,
    subject_fingerprint: Optional[str] = None,
    granted_permissions: Optional[Tuple[str, ...]] = None,
  ) -> SessionGrant:
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
      granted_permissions=(
        list(granted_permissions) if granted_permissions is not None else None
      ),
    )
    self.db.add(session)
    await self.db.flush()
    access_token, access_expiry = self._issue_access_token(user.id, session)
    principal = await self._principal(user, session, access_expiry)
    await self._audit(
      audit_event_type,
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

  async def refresh(
    self,
    refresh_token: str,
    request_id: str,
    *,
    require_scoped_session: bool = False,
  ) -> SessionGrant:
    require_signing_key(self.settings)
    token_hash = digest_refresh_token(refresh_token, self.settings)
    now = utcnow()
    await self.db.execute(
      delete(AuthConsumedRefreshToken).where(AuthConsumedRefreshToken.expires_at <= now)
    )
    result = await self.db.execute(
      select(AuthDeviceSession)
      .where(AuthDeviceSession.refresh_token_hash == token_hash)
      .with_for_update()
    )
    session = result.scalar_one_or_none()
    if session is None:
      consumed_result = await self.db.execute(
        select(AuthConsumedRefreshToken)
        .where(AuthConsumedRefreshToken.token_hash == token_hash)
        .with_for_update()
      )
      consumed = consumed_result.scalar_one_or_none()
      if consumed is not None:
        replay_session_result = await self.db.execute(
          select(AuthDeviceSession)
          .where(AuthDeviceSession.id == consumed.device_session_id)
          .with_for_update()
        )
        replay_session = replay_session_result.scalar_one_or_none()
        if replay_session is not None and replay_session.revoked_at is None:
          replay_session.revoked_at = now
        await self._audit(
          "REFRESH",
          "DENIED",
          request_id=request_id,
          reason_code="REFRESH_TOKEN_REUSED",
          user_id=replay_session.user_id if replay_session is not None else None,
          device_session_id=consumed.device_session_id,
        )
        await self.db.commit()
        raise unauthenticated("检测到刷新令牌重复使用，会话已撤销")

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

    if require_scoped_session and not self._is_native_session(session):
      await self._audit(
        "REFRESH",
        "DENIED",
        request_id=request_id,
        reason_code="SESSION_SCOPE_REQUIRED",
        user_id=session.user_id,
        device_session_id=session.id,
      )
      await self.db.commit()
      raise unauthenticated("该刷新令牌不属于原生设备会话")

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

    try:
      principal = await self._principal(user, session, now)
    except AuthError as exc:
      session.revoked_at = now
      await self._audit(
        "REFRESH",
        "DENIED",
        request_id=request_id,
        reason_code=exc.code,
        user_id=user.id,
        device_session_id=session.id,
      )
      await self.db.commit()
      raise

    previous_refresh_expiry = session.expires_at
    self.db.add(
      AuthConsumedRefreshToken(
        token_hash=token_hash,
        device_session_id=session.id,
        consumed_at=now,
        expires_at=previous_refresh_expiry,
      )
    )
    rotated_refresh_token = issue_refresh_token()
    session.refresh_token_hash = digest_refresh_token(
      rotated_refresh_token, self.settings
    )
    session.expires_at = refresh_expiry(self.settings)
    session.last_used_at = now
    if self._is_native_session(session):
      # A refresh can only remove permissions after a user-level revocation.
      # Newly granted user permissions never expand an existing device scope.
      session.granted_permissions = sorted(principal.permissions)
    access_token, access_expiry = self._issue_access_token(user.id, session)
    principal = replace(principal, access_token_expires_at=access_expiry)
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

  async def logout_by_refresh_token(
    self, refresh_token: str, *, request_id: str
  ) -> bool:
    """Revoke a browser session without requiring a still-valid access token."""
    require_signing_key(self.settings)
    token_hash = digest_refresh_token(refresh_token, self.settings)
    result = await self.db.execute(
      select(AuthDeviceSession)
      .where(AuthDeviceSession.refresh_token_hash == token_hash)
      .with_for_update()
    )
    session = result.scalar_one_or_none()
    if session is None:
      consumed_result = await self.db.execute(
        select(AuthConsumedRefreshToken.device_session_id).where(
          AuthConsumedRefreshToken.token_hash == token_hash
        )
      )
      device_session_id = consumed_result.scalar_one_or_none()
      if device_session_id:
        session_result = await self.db.execute(
          select(AuthDeviceSession)
          .where(AuthDeviceSession.id == device_session_id)
          .with_for_update()
        )
        session = session_result.scalar_one_or_none()

    if session is None:
      return False

    if session.revoked_at is None:
      session.revoked_at = utcnow()
    await self._audit(
      "LOGOUT",
      "SUCCEEDED",
      request_id=request_id,
      user_id=session.user_id,
      device_session_id=session.id,
    )
    await self.db.commit()
    return True

  @staticmethod
  async def reconcile_development_auto_login_permissions(
    db: AsyncSession,
    auth_settings: Optional[Settings] = None,
  ) -> bool:
    """Add explicitly configured permissions to the dev auto-login user.

    This is deliberately development-only and additive: production users are
    never modified, and permissions granted manually are never removed.
    """
    auth_settings = auth_settings or settings
    if (
      not auth_settings.is_development or not auth_settings.auth_development_auto_login
    ):
      return False
    username = auth_settings.auth_development_username.strip().lower()
    if not username:
      return False
    result = await db.execute(select(AuthUser).where(AuthUser.username == username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
      return False
    configured = {
      str(value).strip()
      for value in auth_settings.auth_bootstrap_permissions
      if str(value).strip()
    }
    current = {str(value) for value in list(user.permissions or [])}
    additions = configured - current
    if not additions:
      return False
    user.permissions = sorted(current | additions)
    db.add(
      AuthAuditEvent(
        id=str(uuid.uuid4()),
        event_type="DEVELOPMENT_PERMISSION_SYNC",
        outcome="SUCCESS",
        reason_code="CONFIGURED_ADDITIVE_GRANT",
        user_id=user.id,
        device_session_id=None,
        subject_fingerprint=None,
        request_id="startup-development-permission-sync",
        occurred_at=utcnow(),
      )
    )
    await db.commit()
    logger.info(
      "开发自动登录用户权限已按配置增量同步（新增权限数量=%d）",
      len(additions),
    )
    return True

  async def authenticate(self, access_token: str) -> Principal:
    claims = decode_access_token(access_token, self.settings)
    rows = (
      await self.db.execute(
        select(
          AuthDeviceSession,
          AuthUser.id,
          AuthUser.username,
          AuthUser.display_name,
          AuthUser.is_active,
          AuthUser.permissions,
          AuthUserAccountAccess.account_id,
        )
        .join(AuthUser, AuthUser.id == AuthDeviceSession.user_id)
        .outerjoin(
          AuthUserAccountAccess,
          AuthUserAccountAccess.user_id == AuthUser.id,
        )
        .where(AuthDeviceSession.id == claims.device_session_id)
        .order_by(
          AuthUserAccountAccess.is_default.desc(),
          AuthUserAccountAccess.created_at.asc(),
          AuthUserAccountAccess.account_id.asc(),
        )
      )
    ).all()
    if not rows:
      raise unauthenticated()

    (
      session,
      user_id,
      username,
      display_name,
      is_active,
      permissions,
      _,
    ) = rows[0]
    now = utcnow()
    if (
      session.user_id != claims.user_id
      or user_id != claims.user_id
      or session.revoked_at is not None
      or session.expires_at <= now
      or not is_active
    ):
      raise unauthenticated()
    account_ids = tuple(row[6] for row in rows if row[6])
    self._validate_access_claim_binding(claims, session)
    effective_permissions, effective_account_ids, is_native_session = (
      self._session_authorization(
        session,
        permissions,
        account_ids,
      )
    )
    return Principal(
      user_id=user_id,
      username=username,
      display_name=display_name,
      device_session_id=session.id,
      access_token_expires_at=claims.expires_at,
      permissions=effective_permissions,
      authorized_account_ids=effective_account_ids,
      is_native_session=is_native_session,
    )

  async def lock_and_validate_session(
    self,
    principal: Principal,
    *,
    required_permission: Optional[str] = None,
    account_id: Optional[str] = None,
  ) -> Principal:
    """Lock and revalidate a session inside a sensitive-write transaction.

    Callers keep the transaction open through their durable business write so
    a concurrent logout/revocation cannot pass authentication and then race the
    mutation. The returned principal reflects current user permission removals.
    """
    row = (
      await self.db.execute(
        select(AuthDeviceSession, AuthUser)
        .join(AuthUser, AuthUser.id == AuthDeviceSession.user_id)
        .where(
          AuthDeviceSession.id == principal.device_session_id,
          AuthDeviceSession.user_id == principal.user_id,
        )
        .with_for_update()
      )
    ).one_or_none()
    now = utcnow()
    if (
      row is None
      or row[0].revoked_at is not None
      or row[0].expires_at <= now
      or not row[1].is_active
      or principal.access_token_expires_at <= now
    ):
      raise unauthenticated()

    current = await self._principal(
      row[1],
      row[0],
      principal.access_token_expires_at,
    )
    if required_permission is not None:
      current.require_permission(required_permission)
    if account_id is not None:
      current.require_account(account_id)
    return current

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
    permissions, effective_account_ids, is_native_session = (
      self._session_authorization(session, user.permissions, account_ids)
    )
    return Principal(
      user_id=user.id,
      username=user.username,
      display_name=user.display_name,
      device_session_id=session.id,
      access_token_expires_at=access_expiry,
      permissions=permissions,
      authorized_account_ids=effective_account_ids,
      is_native_session=is_native_session,
    )

  async def _require_personal_account(
    self,
    user_id: str,
  ) -> str:
    account_ids = await self._authorized_account_ids(user_id)
    if len(account_ids) != 1:
      raise AuthError(
        "SINGLE_ACCOUNT_REQUIRED",
        "个人会话必须且只能授权一个资金账户",
        status_code=400,
      )
    return account_ids[0]

  @staticmethod
  def _resolve_native_permissions(
    user_permissions: Optional[Iterable[str]],
  ) -> Tuple[str, ...]:
    current_user_permissions = {
      str(value).strip() for value in (user_permissions or []) if str(value).strip()
    }
    return tuple(sorted(NATIVE_SESSION_SCOPES & current_user_permissions))

  async def _authorized_account_ids(self, user_id: str) -> Tuple[str, ...]:
    result = await self.db.execute(
      select(AuthUserAccountAccess.account_id)
      .where(AuthUserAccountAccess.user_id == user_id)
      .order_by(
        AuthUserAccountAccess.is_default.desc(),
        AuthUserAccountAccess.created_at.asc(),
        AuthUserAccountAccess.account_id.asc(),
      )
    )
    return tuple(result.scalars().all())

  @staticmethod
  def _is_native_session(session: AuthDeviceSession) -> bool:
    return session.granted_permissions is not None

  def _issue_access_token(
    self,
    user_id: str,
    session: AuthDeviceSession,
  ) -> tuple[str, datetime]:
    if not self._is_native_session(session):
      return issue_access_token(user_id, session.id, self.settings)
    return issue_access_token(
      user_id,
      session.id,
      self.settings,
      scopes=session.granted_permissions,
    )

  @staticmethod
  def _validate_access_claim_binding(
    claims: AccessClaims,
    session: AuthDeviceSession,
  ) -> None:
    if not AuthService._is_native_session(session):
      if claims.scopes is not None:
        raise unauthenticated("访问令牌与设备会话不匹配")
      return
    persisted_permissions = session.granted_permissions
    if (
      not isinstance(persisted_permissions, list)
      or claims.scopes
      != frozenset(
        value.strip()
        for value in persisted_permissions
        if isinstance(value, str) and value.strip()
      )
    ):
      raise unauthenticated("访问令牌与设备会话不匹配")

  @staticmethod
  def _session_authorization(
    session: AuthDeviceSession,
    user_permissions: Optional[Iterable[str]],
    account_ids: Tuple[str, ...],
  ) -> tuple[frozenset[str], Tuple[str, ...], bool]:
    granted_permissions = session.granted_permissions
    if granted_permissions is None:
      return (
        frozenset(str(value) for value in (user_permissions or [])),
        account_ids,
        False,
      )
    if not isinstance(granted_permissions, list):
      raise unauthenticated("设备会话权限上下文无效")
    persisted = {
      value.strip()
      for value in granted_permissions
      if isinstance(value, str) and value.strip()
    }
    if (
      len(persisted) != len(granted_permissions)
      or not persisted <= NATIVE_SESSION_SCOPES
    ):
      raise unauthenticated("设备会话权限上下文无效")
    current = {
      str(value).strip() for value in (user_permissions or []) if str(value).strip()
    }
    return (
      frozenset(persisted & current),
      account_ids,
      True,
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
