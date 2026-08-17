import uuid

import pytest
from quantx_api.auth.errors import AuthError
from quantx_api.auth.service import AuthService
from quantx_api.auth.tokens import issue_access_token
from quantx_infrastructure.config.settings import Settings
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.auth import (
  AuthAuditEvent,
  AuthConsumedRefreshToken,
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

AUTH_TABLES = [
  AuthUser.__table__,
  AuthUserAccountAccess.__table__,
  AuthDeviceSession.__table__,
  AuthAuditEvent.__table__,
  AuthConsumedRefreshToken.__table__,
]


def _settings(**overrides) -> Settings:
  values = {
    "ENV": "development",
    "debug": False,
    "database_url": "postgresql+asyncpg://test:test@localhost/test",
    "secret_key": "test-signing-key-" + "x" * 48,
    "access_token_expire_minutes": 5,
    "refresh_token_expire_days": 7,
    "auth_bootstrap_username": "ios-developer",
    "auth_bootstrap_password": "local-test-password-only",
    "auth_bootstrap_display_name": "iOS Developer",
    "auth_bootstrap_account_ids": ["TEST-ACCOUNT-1"],
    "auth_bootstrap_permissions": ["portfolio:read", "orders:read"],
    "auth_development_auto_login": True,
    "auth_development_username": "ios-developer",
    "auth_login_rate_limit_attempts": 20,
  }
  values.update(overrides)
  return Settings(_env_file=None, **values)


@pytest.fixture
async def db():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection, tables=AUTH_TABLES
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as session:
    yield session
  await engine.dispose()


@pytest.mark.asyncio
async def test_login_refresh_rotation_and_logout_lifecycle(db):
  settings = _settings()
  assert await AuthService.bootstrap_from_settings(db, settings)
  assert not await AuthService.bootstrap_from_settings(db, settings)

  service = AuthService(db, settings)
  with pytest.raises(AuthError) as invalid_login:
    await service.login(
      "ios-developer",
      "wrong-password",
      device_name="Test iPhone",
      client_fingerprint="127.0.0.1\npytest",
      request_id="request-invalid-login",
    )
  assert invalid_login.value.code == "UNAUTHENTICATED"

  grant = await service.login(
    "ios-developer",
    "local-test-password-only",
    device_name="Test iPhone",
    client_fingerprint="127.0.0.1\npytest",
    request_id="request-login",
  )
  assert grant.principal.authorized_account_ids == ("TEST-ACCOUNT-1",)
  assert grant.principal.is_native_session
  assert grant.principal.permissions == frozenset({"portfolio:read", "orders:read"})
  assert grant.principal.require_account() == "TEST-ACCOUNT-1"

  authenticated = await service.authenticate(grant.access_token)
  assert authenticated.device_session_id == grant.principal.device_session_id

  refreshed = await service.refresh(
    grant.refresh_token,
    "request-refresh",
    require_scoped_session=True,
  )
  assert refreshed.refresh_token != grant.refresh_token
  with pytest.raises(AuthError):
    await service.refresh(grant.refresh_token, "request-replay")

  with pytest.raises(AuthError) as replay_revoked:
    await service.authenticate(refreshed.access_token)
  assert replay_revoked.value.code == "UNAUTHENTICATED"

  await service.logout(
    refreshed.principal, all_devices=False, request_id="request-logout"
  )
  with pytest.raises(AuthError) as revoked:
    await service.authenticate(refreshed.access_token)
  assert revoked.value.code == "UNAUTHENTICATED"

  audits = (await db.execute(select(AuthAuditEvent))).scalars().all()
  assert {event.event_type for event in audits} >= {"LOGIN", "REFRESH", "LOGOUT"}
  assert any(event.reason_code == "REFRESH_TOKEN_REUSED" for event in audits)
  serialized_audits = repr([event.__dict__ for event in audits])
  assert "local-test-password-only" not in serialized_audits
  assert grant.refresh_token not in serialized_audits
  assert "TEST-ACCOUNT-1" not in serialized_audits


@pytest.mark.asyncio
async def test_development_auto_login_permission_sync_is_additive_and_audited(db):
  initial = _settings(auth_bootstrap_permissions=["portfolio:read"])
  assert await AuthService.bootstrap_from_settings(db, initial)
  configured = _settings(auth_bootstrap_permissions=["portfolio:read", "trade:approve"])

  assert await AuthService.reconcile_development_auto_login_permissions(
    db,
    configured,
  )
  assert not await AuthService.reconcile_development_auto_login_permissions(
    db,
    configured,
  )

  user = (
    await db.execute(
      select(AuthUser).where(AuthUser.username == configured.auth_development_username)
    )
  ).scalar_one()
  assert user.permissions == ["portfolio:read", "trade:approve"]
  audits = (
    (
      await db.execute(
        select(AuthAuditEvent).where(
          AuthAuditEvent.event_type == "DEVELOPMENT_PERMISSION_SYNC"
        )
      )
    )
    .scalars()
    .all()
  )
  assert len(audits) == 1
  assert audits[0].reason_code == "CONFIGURED_ADDITIVE_GRANT"


@pytest.mark.asyncio
async def test_production_never_syncs_auto_login_permissions(db):
  initial = _settings(auth_bootstrap_permissions=["portfolio:read"])
  assert await AuthService.bootstrap_from_settings(db, initial)
  production = _settings(
    ENV="production",
    auth_bootstrap_permissions=["portfolio:read", "trade:approve"],
  )

  assert not await AuthService.reconcile_development_auto_login_permissions(
    db,
    production,
  )
  user = (
    await db.execute(
      select(AuthUser).where(AuthUser.username == production.auth_development_username)
    )
  ).scalar_one()
  assert user.permissions == ["portfolio:read"]


@pytest.mark.asyncio
async def test_logout_all_devices_revokes_every_access_token(db):
  settings = _settings(auth_bootstrap_username=f"user-{uuid.uuid4().hex[:8]}")
  await AuthService.bootstrap_from_settings(db, settings)
  service = AuthService(db, settings)

  first = await service.login(
    settings.auth_bootstrap_username,
    settings.auth_bootstrap_password,
    device_name="iPhone",
    client_fingerprint="device-one",
    request_id="login-one",
  )
  second = await service.login(
    settings.auth_bootstrap_username,
    settings.auth_bootstrap_password,
    device_name="iPad",
    client_fingerprint="device-two",
    request_id="login-two",
  )
  await service.logout(first.principal, all_devices=True, request_id="logout-all")

  with pytest.raises(AuthError):
    await service.authenticate(first.access_token)
  with pytest.raises(AuthError):
    await service.authenticate(second.access_token)


@pytest.mark.asyncio
async def test_development_login_uses_configured_database_user_only_in_development(db):
  settings = _settings()
  await AuthService.bootstrap_from_settings(db, settings)

  grant = await AuthService(db, settings).development_login(
    device_name="Development Browser",
    client_fingerprint="127.0.0.1\npytest",
    request_id="development-login",
  )

  assert grant.principal.username == settings.auth_development_username
  assert grant.principal.authorized_account_ids == ("TEST-ACCOUNT-1",)
  assert await AuthService(db, settings).authenticate(grant.access_token)
  audits = (await db.execute(select(AuthAuditEvent))).scalars().all()
  assert any(
    event.event_type == "DEVELOPMENT_LOGIN" and event.outcome == "SUCCEEDED"
    for event in audits
  )

  production_settings = _settings(ENV="production")
  with pytest.raises(AuthError) as disabled:
    await AuthService(db, production_settings).development_login(
      device_name="Production Browser",
      client_fingerprint="127.0.0.1\npytest",
      request_id="production-development-login",
    )
  assert disabled.value.code == "DEVELOPMENT_LOGIN_DISABLED"
  assert disabled.value.status_code == 404


@pytest.mark.asyncio
async def test_authenticate_loads_session_user_and_accounts_in_one_query(db):
  settings = _settings(auth_bootstrap_username="single-query-user")
  await AuthService.bootstrap_from_settings(db, settings)
  service = AuthService(db, settings)
  grant = await service.login(
    settings.auth_bootstrap_username,
    settings.auth_bootstrap_password,
    device_name="Test Browser",
    client_fingerprint="single-query-client",
    request_id="single-query-login",
  )
  statements = []

  def capture_statement(*args):
    statements.append(args[2])

  sync_engine = db.bind.sync_engine
  event.listen(sync_engine, "before_cursor_execute", capture_statement)
  try:
    principal = await service.authenticate(grant.access_token)
  finally:
    event.remove(sync_engine, "before_cursor_execute", capture_statement)

  assert principal.authorized_account_ids == ("TEST-ACCOUNT-1",)
  select_statements = [
    statement
    for statement in statements
    if statement.lstrip().upper().startswith("SELECT")
  ]
  assert len(select_statements) == 1


@pytest.mark.asyncio
async def test_native_scope_is_intersection_and_refresh_never_expands(db):
  settings = _settings(
    auth_bootstrap_username="native-scope-user",
    auth_bootstrap_permissions=[
      "portfolio:read",
      "orders:read",
      "trade:manual",
    ],
  )
  await AuthService.bootstrap_from_settings(db, settings)
  service = AuthService(db, settings)

  grant = await service.login(
    settings.auth_bootstrap_username,
    settings.auth_bootstrap_password,
    device_name="Scoped iPhone",
    client_fingerprint="native-scope-client",
    request_id="native-scope-login",
  )

  assert grant.principal.permissions == frozenset(
    {"portfolio:read", "orders:read", "trade:manual"}
  )
  assert grant.principal.authorized_account_ids == ("TEST-ACCOUNT-1",)
  assert grant.principal.is_native_session
  session = (
    await db.execute(
      select(AuthDeviceSession).where(
        AuthDeviceSession.id == grant.principal.device_session_id
      )
    )
  ).scalar_one()
  assert session.granted_permissions == [
    "orders:read",
    "portfolio:read",
    "trade:manual",
  ]

  user = (
    await db.execute(select(AuthUser).where(AuthUser.id == grant.principal.user_id))
  ).scalar_one()
  user.permissions = [
    "market:read",
    "orders:read",
    "portfolio:read",
    "trade:manual",
  ]
  await db.commit()
  refreshed = await service.refresh(
    grant.refresh_token,
    "native-scope-refresh-no-expansion",
    require_scoped_session=True,
  )
  assert refreshed.principal.permissions == frozenset(
    {"portfolio:read", "orders:read", "trade:manual"}
  )

  user.permissions = ["portfolio:read", "trade:manual"]
  await db.commit()
  authenticated = await service.authenticate(refreshed.access_token)
  assert authenticated.permissions == frozenset({"portfolio:read", "trade:manual"})
  reduced = await service.refresh(
    refreshed.refresh_token,
    "native-scope-refresh-after-revocation",
    require_scoped_session=True,
  )
  assert reduced.principal.permissions == frozenset({"portfolio:read", "trade:manual"})
  await db.refresh(session)
  assert session.granted_permissions == ["portfolio:read", "trade:manual"]
  with pytest.raises(AuthError) as stale_claims:
    await service.authenticate(refreshed.access_token)
  assert stale_claims.value.code == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_authenticate_rejects_valid_token_with_mismatched_session_claims(db):
  settings = _settings(auth_bootstrap_username="claim-binding-user")
  await AuthService.bootstrap_from_settings(db, settings)
  service = AuthService(db, settings)
  grant = await service.login(
    settings.auth_bootstrap_username,
    settings.auth_bootstrap_password,
    device_name="Scoped iPhone",
    client_fingerprint="claim-binding-client",
    request_id="claim-binding-login",
  )

  unscoped_token, _ = issue_access_token(
    grant.principal.user_id,
    grant.principal.device_session_id,
    settings,
  )
  wrong_scope_token, _ = issue_access_token(
    grant.principal.user_id,
    grant.principal.device_session_id,
    settings,
    scopes=["portfolio:read"],
  )

  for token in (unscoped_token, wrong_scope_token):
    with pytest.raises(AuthError) as mismatch:
      await service.authenticate(token)
    assert mismatch.value.code == "UNAUTHENTICATED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "invalid_scope",
  ["unknown:scope", "mutation:write", "trade:direct", "system-config:write"],
)
async def test_native_login_filters_permissions_outside_allowlist(db, invalid_scope):
  settings = _settings(
    auth_bootstrap_username=f"invalid-scope-{uuid.uuid4().hex[:8]}",
    auth_bootstrap_permissions=["portfolio:read", invalid_scope],
  )
  await AuthService.bootstrap_from_settings(db, settings)

  grant = await AuthService(db, settings).login(
    settings.auth_bootstrap_username,
    settings.auth_bootstrap_password,
    device_name="Scoped iPhone",
    client_fingerprint=f"invalid-scope-{invalid_scope}",
    request_id="filtered-native-scope",
  )

  assert grant.principal.permissions == frozenset({"portfolio:read"})


@pytest.mark.asyncio
async def test_native_login_grants_allowed_user_permissions_automatically(db):
  settings = _settings(
    auth_bootstrap_username="automatic-scope-user",
    auth_bootstrap_permissions=["portfolio:read", "market:read"],
  )
  await AuthService.bootstrap_from_settings(db, settings)

  grant = await AuthService(db, settings).login(
    settings.auth_bootstrap_username,
    settings.auth_bootstrap_password,
    device_name="Scoped iPhone",
    client_fingerprint="automatic-scope-client",
    request_id="automatic-native-scopes",
  )

  assert grant.principal.permissions == frozenset({"portfolio:read", "market:read"})


@pytest.mark.asyncio
async def test_native_login_allows_zero_capability_session(db):
  settings = _settings(
    auth_bootstrap_username="zero-scope-user",
    auth_bootstrap_permissions=["mutation:write"],
  )
  await AuthService.bootstrap_from_settings(db, settings)

  grant = await AuthService(db, settings).login(
    settings.auth_bootstrap_username,
    settings.auth_bootstrap_password,
    device_name="Scoped iPhone",
    client_fingerprint="zero-scope-client",
    request_id="zero-scope-login",
  )

  assert grant.principal.permissions == frozenset()
  assert grant.principal.is_native_session
  session = (
    await db.execute(
      select(AuthDeviceSession).where(
        AuthDeviceSession.id == grant.principal.device_session_id
      )
    )
  ).scalar_one()
  assert session.granted_permissions == []


@pytest.mark.asyncio
async def test_native_login_requires_exactly_one_authorized_account(db):
  settings = _settings(auth_bootstrap_username="multi-account-user")
  await AuthService.bootstrap_from_settings(db, settings)
  user = (
    await db.execute(
      select(AuthUser).where(AuthUser.username == settings.auth_bootstrap_username)
    )
  ).scalar_one()
  db.add(
    AuthUserAccountAccess(
      user_id=user.id,
      account_id="TEST-ACCOUNT-2",
      is_default=False,
    )
  )
  await db.commit()
  service = AuthService(db, settings)

  with pytest.raises(AuthError) as ambiguous:
    await service.login(
      settings.auth_bootstrap_username,
      settings.auth_bootstrap_password,
      device_name="Scoped iPhone",
      client_fingerprint="ambiguous-account-client",
      request_id="ambiguous-account",
    )
  assert ambiguous.value.code == "SINGLE_ACCOUNT_REQUIRED"


@pytest.mark.asyncio
async def test_native_login_rejects_user_without_authorized_account(db):
  settings = _settings(
    auth_bootstrap_username="no-account-user",
    auth_bootstrap_account_ids=[],
  )
  await AuthService.bootstrap_from_settings(db, settings)

  with pytest.raises(AuthError) as rejected:
    await AuthService(db, settings).login(
      settings.auth_bootstrap_username,
      settings.auth_bootstrap_password,
      device_name="Scoped iPhone",
      client_fingerprint="no-account-client",
      request_id="no-account-login",
    )

  assert rejected.value.code == "SINGLE_ACCOUNT_REQUIRED"


@pytest.mark.asyncio
async def test_locked_session_validation_fails_after_revocation(db):
  settings = _settings(auth_bootstrap_username="session-lock-user")
  await AuthService.bootstrap_from_settings(db, settings)
  service = AuthService(db, settings)
  grant = await service.login(
    settings.auth_bootstrap_username,
    settings.auth_bootstrap_password,
    device_name="Scoped iPhone",
    client_fingerprint="session-lock-client",
    request_id="session-lock-login",
  )

  current = await service.lock_and_validate_session(
    grant.principal,
    required_permission="portfolio:read",
    account_id="TEST-ACCOUNT-1",
  )
  assert current.require_account() == "TEST-ACCOUNT-1"
  await service.logout(
    grant.principal,
    all_devices=False,
    request_id="session-lock-revoke",
  )

  with pytest.raises(AuthError) as revoked:
    await service.lock_and_validate_session(grant.principal)
  assert revoked.value.code == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_persisted_login_failure_window_enforces_rate_limit(db):
  settings = _settings(
    auth_bootstrap_username="rate-limit-user",
    auth_login_rate_limit_attempts=2,
  )
  await AuthService.bootstrap_from_settings(db, settings)
  service = AuthService(db, settings)

  for request_id in ("failure-one", "failure-two"):
    with pytest.raises(AuthError) as failed:
      await service.login(
        "rate-limit-user",
        "wrong-password",
        device_name="Test iPhone",
        client_fingerprint="same-client",
        request_id=request_id,
      )
    assert failed.value.code == "UNAUTHENTICATED"

  with pytest.raises(AuthError) as limited:
    await service.login(
      "rate-limit-user",
      settings.auth_bootstrap_password,
      device_name="Test iPhone",
      client_fingerprint="same-client",
      request_id="rate-limited",
    )
  assert limited.value.code == "RATE_LIMITED"
  assert limited.value.status_code == 429
