import pytest
from quantx_api.auth.agent_service import AgentAuthService
from quantx_infrastructure.auth.errors import AuthError
from quantx_infrastructure.auth.tokens import utcnow
from quantx_infrastructure.config.settings import Settings
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  AgentEnrollmentCode,
  RuntimeComponentHeartbeat,
)
from quantx_infrastructure.models.auth import AuthUser
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

AGENT_AUTH_TABLES = [
  AuthUser.__table__,
  AgentEnrollmentCode.__table__,
  AgentDevice.__table__,
  RuntimeComponentHeartbeat.__table__,
]


def _settings() -> Settings:
  return Settings(
    _env_file=None,
    ENV="testing",
    database_url="postgresql+asyncpg://test:test@localhost/test",
    secret_key="agent-test-signing-key-" + "x" * 48,
    access_token_expire_minutes=5,
  )


@pytest.fixture
async def db():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=AGENT_AUTH_TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as session:
    yield session
  await engine.dispose()


@pytest.mark.asyncio
async def test_agent_enrollment_is_one_time_and_device_secret_is_hashed(db):
  service = AgentAuthService(db, _settings())
  enrollment = await service.create_enrollment(
    user_id="user-1",
    name="QMT workstation",
    authorized_account_ids=["account-1", "account-1"],
  )
  credential = await service.exchange_enrollment(enrollment.code)
  device = await db.get(AgentDevice, credential.device_id)

  assert device is not None
  assert device.authorized_account_ids == ["account-1"]
  assert credential.device_secret not in device.secret_hash

  with pytest.raises(AuthError):
    await service.exchange_enrollment(enrollment.code)


@pytest.mark.asyncio
async def test_agent_access_session_tracks_expiry_and_revoke_is_immediate(db):
  service = AgentAuthService(db, _settings())
  enrollment = await service.create_enrollment(
    user_id="user-1",
    name="QMT workstation",
    authorized_account_ids=["account-1"],
  )
  credential = await service.exchange_enrollment(enrollment.code)
  grant = await service.issue_agent_token(
    device_id=credential.device_id,
    device_secret=credential.device_secret,
  )
  session = await service.authenticate_agent_session(
    token=grant.access_token,
    expected_device_id=credential.device_id,
  )

  assert session.device.id == credential.device_id
  assert session.expires_at == grant.expires_at
  assert await service.revoke(device_id=credential.device_id, user_id="user-1")

  with pytest.raises(AuthError):
    await service.authenticate_agent(token=grant.access_token)


@pytest.mark.asyncio
async def test_new_ready_agent_atomically_replaces_previous_device(db):
  service = AgentAuthService(db, _settings())
  first_enrollment = await service.create_enrollment(
    user_id="user-1",
    name="current",
    authorized_account_ids=["account-1"],
  )
  first_credential = await service.exchange_enrollment(first_enrollment.code)
  first = await db.get(AgentDevice, first_credential.device_id)
  first.last_seen_at = service_module_now = utcnow()
  db.add(
    RuntimeComponentHeartbeat(
      component=f"qmt-agent:{first.id}",
      instance_id=first.id,
      status="READY",
      details={},
      updated_at=service_module_now,
    )
  )
  await db.commit()

  replacement_enrollment = await service.create_enrollment(
    user_id="user-1",
    name="replacement",
    authorized_account_ids=["account-1"],
  )
  replacement_credential = await service.exchange_enrollment(
    replacement_enrollment.code
  )
  replacement = await db.get(AgentDevice, replacement_credential.device_id)

  assert replacement.replaces_device_id == first.id
  assert (
    await service.converge_ready_device(
      device=first,
      observed_at=utcnow(),
    )
    == []
  )
  assert first.revoked_at is None

  revoked = await service.converge_ready_device(
    device=replacement,
    observed_at=utcnow(),
  )
  await db.commit()

  assert revoked == [first.id]
  assert first.revoked_at is not None
  heartbeat = await db.get(RuntimeComponentHeartbeat, f"qmt-agent:{first.id}")
  assert heartbeat.status == "REVOKED"


@pytest.mark.asyncio
async def test_cancel_handover_invalidates_pending_code(db):
  service = AgentAuthService(db, _settings())
  enrollment = await service.create_enrollment(
    user_id="user-1",
    name="pending",
    authorized_account_ids=["account-1"],
  )

  cancelled = await service.cancel_handover(user_id="user-1")

  assert cancelled.deleted_enrollment_count == 1
  assert cancelled.revoked_device_ids == ()
  with pytest.raises(AuthError):
    await service.exchange_enrollment(enrollment.code)


@pytest.mark.asyncio
async def test_cancel_handover_revokes_candidate_when_api_generation_changed(db):
  service = AgentAuthService(db, _settings())
  first_enrollment = await service.create_enrollment(
    user_id="user-1",
    name="current",
    authorized_account_ids=["account-1"],
  )
  first_credential = await service.exchange_enrollment(first_enrollment.code)
  replacement_enrollment = await service.create_enrollment(
    user_id="user-1",
    name="replacement",
    authorized_account_ids=["account-1"],
  )
  replacement_credential = await service.exchange_enrollment(
    replacement_enrollment.code
  )

  replacement = await db.get(AgentDevice, replacement_credential.device_id)
  replacement.last_seen_at = utcnow()
  await db.commit()

  cancelled = await service.cancel_handover(user_id="user-1")

  current = await db.get(AgentDevice, first_credential.device_id)
  replacement = await db.get(AgentDevice, replacement_credential.device_id)
  assert cancelled.revoked_device_ids == (replacement_credential.device_id,)
  assert current.revoked_at is None
  assert replacement.revoked_at is not None


@pytest.mark.asyncio
async def test_history_token_is_separate_from_control_and_rechecks_revocation(db):
  from quantx_infrastructure.auth.agent_access import authenticate_agent_session
  from quantx_infrastructure.auth.tokens import decode_access_token
  from sqlalchemy import update

  settings = _settings()
  service = AgentAuthService(db, settings)
  enrollment = await service.create_enrollment(
    user_id="user-1",
    name="history",
    authorized_account_ids=["account-1"],
  )
  credential = await service.exchange_enrollment(enrollment.code)
  history = await service.issue_agent_token(
    device_id=credential.device_id,
    device_secret=credential.device_secret,
    history=True,
  )
  control = await service.issue_agent_token(
    device_id=credential.device_id,
    device_secret=credential.device_secret,
  )
  assert decode_access_token(history.access_token, settings).scopes == frozenset(
    {"agent:history"}
  )
  session = await authenticate_agent_session(
    db, settings, token=history.access_token, history=True
  )
  assert session.device.id == credential.device_id
  with pytest.raises(AuthError, match="用途"):
    await service.authenticate_agent(token=history.access_token)
  with pytest.raises(AuthError, match="用途"):
    await authenticate_agent_session(
      db, settings, token=control.access_token, history=True
    )
  with pytest.raises(AuthError, match="不匹配"):
    await authenticate_agent_session(
      db,
      settings,
      token=history.access_token,
      expected_device_id="another-device",
      history=True,
    )
  # Bypass identity-map synchronization, reproducing revocation by another process.
  await db.execute(
    update(AgentDevice)
    .where(AgentDevice.id == credential.device_id)
    .values(revoked_at=utcnow())
    .execution_options(synchronize_session=False)
  )
  await db.commit()
  with pytest.raises(AuthError, match="凭证"):
    await service.issue_agent_token(
      device_id=credential.device_id,
      device_secret=credential.device_secret,
      history=True,
    )
  with pytest.raises(AuthError, match="撤销"):
    await authenticate_agent_session(
      db, settings, token=history.access_token, history=True
    )


@pytest.mark.asyncio
async def test_history_auth_rejects_extra_scope_expiry_and_database_failure(
  db, monkeypatch
):
  from unittest.mock import AsyncMock

  from quantx_infrastructure.auth import tokens
  from quantx_infrastructure.auth.agent_access import authenticate_agent_session
  from sqlalchemy.exc import OperationalError

  settings = _settings()
  token, _ = tokens.issue_access_token(
    "user-1", "device-1", settings, scopes={"agent:history", "orders:write"}
  )
  with pytest.raises(AuthError, match="用途"):
    await authenticate_agent_session(db, settings, token=token, history=True)
  token, _ = tokens.issue_access_token(
    "user-1", "device-1", settings, scopes={"agent:history"}
  )
  original_time = tokens.time.time()
  with monkeypatch.context() as patch:
    patch.setattr(tokens.time, "time", lambda: original_time + 3600)
    with pytest.raises(AuthError):
      await authenticate_agent_session(db, settings, token=token, history=True)
  monkeypatch.setattr(
    db, "execute", AsyncMock(side_effect=OperationalError("", {}, Exception()))
  )
  with pytest.raises(OperationalError):
    await authenticate_agent_session(db, settings, token=token, history=True)


@pytest.mark.asyncio
async def test_history_token_rest_endpoint_uses_device_credentials(db, monkeypatch):
  import quantx_api.auth.router as router
  from fastapi import FastAPI
  from httpx import ASGITransport, AsyncClient
  from quantx_infrastructure.auth.tokens import decode_access_token

  settings = _settings()
  service = AgentAuthService(db, settings)
  enrollment = await service.create_enrollment(
    user_id="user-1",
    name="history",
    authorized_account_ids=["account-1"],
  )
  credential = await service.exchange_enrollment(enrollment.code)
  app = FastAPI()
  app.include_router(router.auth_router)

  async def database():
    yield db

  app.dependency_overrides[router._database] = database
  monkeypatch.setattr(
    router, "AgentAuthService", lambda session: AgentAuthService(session, settings)
  )
  async with AsyncClient(
    transport=ASGITransport(app), base_url="http://test"
  ) as client:
    body = {"deviceId": credential.device_id, "deviceSecret": credential.device_secret}
    response = await client.post("/auth/agent/history-token", json=body)
    assert response.status_code == 200
    assert decode_access_token(
      response.json()["accessToken"], settings
    ).scopes == frozenset({"agent:history"})
    assert (
      credential.device_secret not in response.text and "account-1" not in response.text
    )
    body["deviceSecret"] = "invalid" * 8
    assert (
      await client.post("/auth/agent/history-token", json=body)
    ).status_code == 401
