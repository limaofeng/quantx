import pytest
from quantx_api.auth.agent_service import AgentAuthService
from quantx_api.auth.errors import AuthError
from quantx_infrastructure.config.settings import Settings
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import AgentDevice, AgentEnrollmentCode
from quantx_infrastructure.models.auth import AuthUser
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

AGENT_AUTH_TABLES = [
  AuthUser.__table__,
  AgentEnrollmentCode.__table__,
  AgentDevice.__table__,
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
