import pytest
import quantx_api.auth.router as auth_router_module
import quantx_api.auth.service as auth_service_module
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from quantx_api.auth.router import _database, auth_router
from quantx_api.auth.service import AuthService
from quantx_infrastructure.config.settings import Settings
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.auth import (
  AuthAuditEvent,
  AuthConsumedRefreshToken,
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
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
    "secret_key": "router-test-signing-key-" + "x" * 48,
    "auth_bootstrap_username": "ios-router-user",
    "auth_bootstrap_password": "router-test-password",
    "auth_bootstrap_account_ids": ["TEST-ACCOUNT-1"],
    "auth_bootstrap_permissions": ["portfolio:read"],
    "auth_web_allowed_origins": [
      "http://127.0.0.1:8080",
      "https://quantx.test",
    ],
    "auth_web_cookie_secure": True,
    "auth_development_auto_login": True,
    "auth_development_username": "ios-router-user",
  }
  values.update(overrides)
  return Settings(
    _env_file=None,
    **values,
  )


@pytest.mark.asyncio
async def test_session_rest_contract_and_refresh_replay_rejection(monkeypatch):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection, tables=AUTH_TABLES
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  settings = _settings()

  async with session_factory() as db:
    await AuthService.bootstrap_from_settings(db, settings)
    monkeypatch.setattr(auth_service_module, "settings", settings)

    app = FastAPI()
    app.include_router(auth_router)

    async def override_database():
      yield db

    app.dependency_overrides[_database] = override_database
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
      login = await client.post(
        "/auth/session",
        json={
          "username": "ios-router-user",
          "password": "router-test-password",
          "deviceName": "Test iPhone",
          "requestedScopes": ["portfolio:read", "orders:read"],
        },
      )
      assert login.status_code == 200
      assert login.headers["cache-control"] == "no-store"
      assert login.headers["pragma"] == "no-cache"
      login_payload = login.json()
      assert login_payload["tokenType"] == "Bearer"
      assert login_payload["activeAccountId"] == "TEST-ACCOUNT-1"
      assert login_payload["grantedScopes"] == ["portfolio:read"]
      assert login_payload["user"]["permissions"] == ["portfolio:read"]
      assert login_payload["user"]["authorizedAccountIds"] == ["TEST-ACCOUNT-1"]

      current = await client.get(
        "/auth/session",
        headers={"Authorization": f"Bearer {login_payload['accessToken']}"},
      )
      assert current.status_code == 200
      assert current.headers["cache-control"] == "no-store"
      assert current.json()["deviceSessionId"] == login_payload["deviceSessionId"]
      assert current.json()["activeAccountId"] == "TEST-ACCOUNT-1"
      assert current.json()["grantedScopes"] == ["portfolio:read"]

      refresh = await client.post(
        "/auth/session/refresh",
        json={"refreshToken": login_payload["refreshToken"]},
      )
      assert refresh.status_code == 200
      assert refresh.headers["cache-control"] == "no-store"
      refresh_payload = refresh.json()
      assert refresh_payload["refreshToken"] != login_payload["refreshToken"]
      assert refresh_payload["activeAccountId"] == "TEST-ACCOUNT-1"
      assert refresh_payload["grantedScopes"] == ["portfolio:read"]

      replay = await client.post(
        "/auth/session/refresh",
        json={"refreshToken": login_payload["refreshToken"]},
      )
      assert replay.status_code == 401
      assert replay.json()["detail"]["code"] == "UNAUTHENTICATED"
      assert replay.json()["detail"]["requestId"]

      revoked = await client.get(
        "/auth/session",
        headers={"Authorization": f"Bearer {refresh_payload['accessToken']}"},
      )
      assert revoked.status_code == 401

  await engine.dispose()


@pytest.mark.asyncio
async def test_web_session_uses_httponly_cookie_and_never_returns_refresh_token(
  monkeypatch,
):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection, tables=AUTH_TABLES
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  settings = _settings()

  async with session_factory() as db:
    await AuthService.bootstrap_from_settings(db, settings)
    monkeypatch.setattr(auth_service_module, "settings", settings)
    monkeypatch.setattr(auth_router_module, "settings", settings)

    app = FastAPI()
    app.include_router(auth_router)

    async def override_database():
      yield db

    app.dependency_overrides[_database] = override_database
    transport = ASGITransport(app=app)
    async with AsyncClient(
      transport=transport, base_url="https://quantx.test"
    ) as client:
      development_login = await client.post(
        "/auth/web/session/development",
        headers={"Origin": "http://127.0.0.1:8080"},
      )
      assert development_login.status_code == 200
      assert development_login.json()["user"]["username"] == "ios-router-user"
      assert "refreshToken" not in development_login.json()
      assert "HttpOnly" in development_login.headers["set-cookie"]

      denied_origin = await client.post(
        "/auth/web/session",
        headers={"Origin": "https://evil.test"},
        json={"username": "ios-router-user", "password": "router-test-password"},
      )
      assert denied_origin.status_code == 403
      assert denied_origin.json()["detail"]["code"] == "FORBIDDEN_ORIGIN"

      login = await client.post(
        "/auth/web/session",
        headers={"Origin": "https://quantx.test"},
        json={
          "username": "ios-router-user",
          "password": "router-test-password",
          "deviceName": "Browser Test",
          "requestedAccountId": "IGNORED-CROSS-ACCOUNT",
          "requestedScopes": ["mutation:write"],
        },
      )
      assert login.status_code == 200
      assert "refreshToken" not in login.json()
      assert login.json()["user"]["permissions"] == ["portfolio:read"]
      assert login.json()["user"]["authorizedAccountIds"] == ["TEST-ACCOUNT-1"]
      cookie = login.headers["set-cookie"]
      assert "quantx_refresh=" in cookie
      assert "HttpOnly" in cookie
      assert "Secure" in cookie
      assert "SameSite=strict" in cookie
      assert "Path=/auth/web/session" in cookie

      refreshed = await client.post(
        "/auth/web/session/refresh",
        headers={"Origin": "https://quantx.test"},
      )
      assert refreshed.status_code == 200
      assert refreshed.json()["accessToken"] != login.json()["accessToken"]
      assert "refreshToken" not in refreshed.json()

      logout = await client.delete(
        "/auth/web/session",
        headers={"Origin": "https://quantx.test"},
      )
      assert logout.status_code == 204
      assert "Max-Age=0" in logout.headers["set-cookie"]

      after_logout = await client.post(
        "/auth/web/session/refresh",
        headers={"Origin": "https://quantx.test"},
      )
      assert after_logout.status_code == 401
      assert after_logout.json()["detail"]["code"] == "UNAUTHENTICATED"

    async with AsyncClient(
      transport=transport,
      base_url="http://192.168.1.20:8080",
    ) as lan_client:
      lan_login = await lan_client.post(
        "/auth/web/session/development",
        headers={"Origin": "http://192.168.1.20:8080"},
      )
      assert lan_login.status_code == 200
      assert lan_login.json()["user"]["username"] == "ios-router-user"

  await engine.dispose()


@pytest.mark.asyncio
async def test_development_web_session_is_hidden_outside_development(monkeypatch):
  settings = _settings(ENV="production")
  monkeypatch.setattr(auth_router_module, "settings", settings)

  app = FastAPI()
  app.include_router(auth_router)

  async def override_database():
    yield None

  app.dependency_overrides[_database] = override_database
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="https://quantx.test") as client:
    response = await client.post(
      "/auth/web/session/development",
      headers={"Origin": "https://quantx.test"},
    )

  assert response.status_code == 404
  assert response.json()["detail"]["code"] == "DEVELOPMENT_LOGIN_DISABLED"

  async with AsyncClient(
    transport=transport,
    base_url="http://192.168.1.20:8080",
  ) as lan_client:
    response = await lan_client.post(
      "/auth/web/session",
      headers={"Origin": "http://192.168.1.20:8080"},
      json={"username": "user", "password": "password"},
    )

  assert response.status_code == 403
  assert response.json()["detail"]["code"] == "FORBIDDEN_ORIGIN"
