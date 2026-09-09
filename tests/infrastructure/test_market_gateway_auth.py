"""Gateway authentication through real signed tokens and persisted device rows."""

import time
from datetime import datetime

import pytest
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_infrastructure.auth import tokens
from quantx_infrastructure.auth.errors import AuthError
from quantx_infrastructure.config.settings import Settings
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import AgentDevice
from quantx_infrastructure.models.auth import AuthUser
from quantx_market_data import agent_stream
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def gateway_auth(monkeypatch):
  configuration = Settings(
    _env_file=None,
    ENV="testing",
    database_url="postgresql+asyncpg://test:test@localhost/test",
    secret_key="gateway-test-signing-key-" + "x" * 48,
    access_token_expire_minutes=5,
  )
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(
        sync, tables=[AuthUser.__table__, AgentDevice.__table__]
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  async with sessions() as session:
    session.add(
      AgentDevice(id="device", user_id="user", name="test", secret_hash="unused")
    )
    await session.commit()
  monkeypatch.setattr(agent_stream, "settings", configuration)
  monkeypatch.setattr(agent_stream, "AsyncSessionLocal", sessions)
  try:
    yield configuration, sessions
  finally:
    await engine.dispose()


def auth_envelope(token, *, device="device"):
  return AgentEnvelope(
    message_type=AgentMessageType.AUTH,
    payload={"access_token": token, "device_id": device},
  )


async def test_valid_gateway_token_reaches_real_identity_verifier(gateway_auth):
  configuration, _ = gateway_auth
  token, expires = tokens.issue_access_token("user", "device", configuration)
  session = await agent_stream._authenticate(auth_envelope(token))
  assert session.device.id == "device"
  assert session.device.user_id == "user"
  assert session.expires_at == expires


@pytest.mark.parametrize(
  "scopes",
  [{"agent:history"}, {"market-data:read"}, {"agent:history", "market-data:read"}],
)
async def test_other_token_purposes_cannot_open_realtime_stream(gateway_auth, scopes):
  configuration, _ = gateway_auth
  token, _ = tokens.issue_access_token("user", "device", configuration, scopes=scopes)
  with pytest.raises(AuthError):
    await agent_stream._authenticate(auth_envelope(token))


@pytest.mark.parametrize(
  "user,token_device,claimed_device",
  [
    ("other-user", "device", "device"),
    ("user", "other-device", "device"),
    ("user", "missing", "missing"),
    ("user", "device", "other-device"),
  ],
)
async def test_gateway_rejects_mismatched_or_missing_identity(
  gateway_auth, user, token_device, claimed_device
):
  configuration, _ = gateway_auth
  token, _ = tokens.issue_access_token(user, token_device, configuration)
  with pytest.raises(AuthError):
    await agent_stream._authenticate(auth_envelope(token, device=claimed_device))


async def test_gateway_reads_current_persisted_revocation(gateway_auth):
  configuration, sessions = gateway_auth
  token, _ = tokens.issue_access_token("user", "device", configuration)
  assert (
    await agent_stream._authenticate(auth_envelope(token))
  ).device.revoked_at is None
  async with sessions() as session:
    device = await session.get(AgentDevice, "device")
    device.revoked_at = datetime.now()
    await session.commit()
  with pytest.raises(AuthError):
    await agent_stream._authenticate(auth_envelope(token))


async def test_gateway_rejects_expired_token(gateway_auth, monkeypatch):
  configuration, _ = gateway_auth
  earlier = time.time() - 600
  with monkeypatch.context() as clock:
    clock.setattr(tokens.time, "time", lambda: earlier)
    token, _ = tokens.issue_access_token("user", "device", configuration)
  with pytest.raises(AuthError):
    await agent_stream._authenticate(auth_envelope(token))


async def test_gateway_requires_auth_as_first_message(gateway_auth):
  configuration, _ = gateway_auth
  token, _ = tokens.issue_access_token("user", "device", configuration)
  envelope = auth_envelope(token)
  envelope.message_type = AgentMessageType.HEARTBEAT
  with pytest.raises(AuthError):
    await agent_stream._authenticate(envelope)
  with pytest.raises(AuthError):
    await agent_stream._authenticate(
      AgentEnvelope(message_type=AgentMessageType.AUTH, payload={})
    )


async def test_gateway_rejects_altered_signature(gateway_auth):
  configuration, _ = gateway_auth
  token, _ = tokens.issue_access_token("user", "device", configuration)
  header, payload, signature = token.split(".")
  signature = ("a" if signature[0] != "a" else "b") + signature[1:]
  with pytest.raises(AuthError):
    await agent_stream._authenticate(
      auth_envelope(".".join((header, payload, signature)))
    )
