import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import auth.service as auth_service_module
from auth.router import _database, auth_router
from auth.service import AuthService
from config.settings import Settings
from database.relational_base import Base
from models.auth import (
  AuthAuditEvent,
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)

AUTH_TABLES = [
  AuthUser.__table__,
  AuthUserAccountAccess.__table__,
  AuthDeviceSession.__table__,
  AuthAuditEvent.__table__,
]


def _settings() -> Settings:
  return Settings(
    _env_file=None,
    debug=False,
    database_url="postgresql+asyncpg://test:test@localhost/test",
    secret_key="router-test-signing-key-" + "x" * 48,
    auth_bootstrap_username="ios-router-user",
    auth_bootstrap_password="router-test-password",
    auth_bootstrap_account_ids=["TEST-ACCOUNT-1"],
    auth_bootstrap_permissions=["portfolio:read"],
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
        },
      )
      assert login.status_code == 200
      assert login.headers["cache-control"] == "no-store"
      assert login.headers["pragma"] == "no-cache"
      login_payload = login.json()
      assert login_payload["tokenType"] == "Bearer"
      assert login_payload["user"]["authorizedAccountIds"] == ["TEST-ACCOUNT-1"]

      current = await client.get(
        "/auth/session",
        headers={"Authorization": f"Bearer {login_payload['accessToken']}"},
      )
      assert current.status_code == 200
      assert current.headers["cache-control"] == "no-store"
      assert current.json()["deviceSessionId"] == login_payload["deviceSessionId"]

      refresh = await client.post(
        "/auth/session/refresh",
        json={"refreshToken": login_payload["refreshToken"]},
      )
      assert refresh.status_code == 200
      assert refresh.headers["cache-control"] == "no-store"
      refresh_payload = refresh.json()
      assert refresh_payload["refreshToken"] != login_payload["refreshToken"]

      replay = await client.post(
        "/auth/session/refresh",
        json={"refreshToken": login_payload["refreshToken"]},
      )
      assert replay.status_code == 401
      assert replay.json()["detail"]["code"] == "UNAUTHENTICATED"
      assert replay.json()["detail"]["requestId"]

      logout = await client.delete(
        "/auth/session",
        headers={"Authorization": f"Bearer {refresh_payload['accessToken']}"},
      )
      assert logout.status_code == 204

      revoked = await client.get(
        "/auth/session",
        headers={"Authorization": f"Bearer {refresh_payload['accessToken']}"},
      )
      assert revoked.status_code == 401

  await engine.dispose()
