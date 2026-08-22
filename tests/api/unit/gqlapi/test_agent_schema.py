from datetime import datetime, timedelta, timezone

import pytest
from quantx_api.gqlapi.schemas import agent_schema
from quantx_infrastructure.core.data.market_stream_transport import (
  MarketStreamState,
)
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  AgentEnrollmentCode,
  RuntimeComponentHeartbeat,
)
from quantx_infrastructure.models.auth import AuthUser
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

AGENT_VIEW_TABLES = [
  AuthUser.__table__,
  AgentDevice.__table__,
  AgentEnrollmentCode.__table__,
  RuntimeComponentHeartbeat.__table__,
]


@pytest.fixture
async def db():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=AGENT_VIEW_TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as session:
    yield session
  await engine.dispose()


def _device(
  *,
  device_id: str,
  now: datetime,
  name: str,
  replaces_device_id: str | None = None,
  revoked_at: datetime | None = None,
) -> AgentDevice:
  return AgentDevice(
    id=device_id,
    user_id="user-1",
    name=name,
    secret_hash=f"hash-{device_id}",
    authorized_account_ids=["account-1"],
    capabilities=["market-data", "live"],
    last_seen_at=now - timedelta(seconds=12),
    revoked_at=revoked_at,
    replaces_device_id=replaces_device_id,
    created_at=now - timedelta(minutes=10),
    updated_at=now - timedelta(seconds=12),
  )


@pytest.mark.asyncio
async def test_qmt_connection_keeps_ready_incumbent_during_handover(
  db,
  monkeypatch,
):
  now = datetime(2026, 8, 22, 10, 30, tzinfo=timezone.utc)
  incumbent = _device(
    device_id="agent-current",
    now=now,
    name="本机 QMT Agent",
  )
  candidate = _device(
    device_id="agent-candidate",
    now=now,
    name="新本机 QMT Agent",
    replaces_device_id=incumbent.id,
  )
  history = _device(
    device_id="agent-history",
    now=now,
    name="旧登记",
    revoked_at=now - timedelta(days=1),
  )
  db.add_all(
    [
      AuthUser(
        id="user-1",
        username="user-1",
        display_name="User One",
        password_hash="hash",
        is_active=True,
        permissions=[],
        created_at=now,
        updated_at=now,
      ),
      incumbent,
      candidate,
      history,
      RuntimeComponentHeartbeat(
        component=f"qmt-agent:{incumbent.id}",
        instance_id=incumbent.id,
        status="READY",
        details={
          "agentVersion": "1.4.0",
          "protocolVersion": "1.1",
          "xtdataStatus": "CONNECTED",
          "xttradingStatus": "CONNECTED",
          "readyAccounts": ["account-1"],
          "journalIntegrity": "ok",
          "journalSizeBytes": 4096,
          "marketStreamSequence": 31,
          "marketStreamQueueDepth": 0,
          "marketStreamResyncs": 0,
          "marketStreamAckLatencyMs": 186.4,
        },
        updated_at=now - timedelta(seconds=12),
      ),
      RuntimeComponentHeartbeat(
        component=f"qmt-agent:{candidate.id}",
        instance_id=candidate.id,
        status="RECONCILING",
        details={
          "xtdataStatus": "CONNECTED",
          "xttradingStatus": "CONNECTED",
        },
        updated_at=now - timedelta(seconds=12),
      ),
    ]
  )
  await db.commit()

  async def stream_state():
    return MarketStreamState(
      status="READY",
      stream_id="stream-1",
      sequence=31,
      captured_at=now - timedelta(seconds=12),
      updated_at=now - timedelta(seconds=12),
      instrument_count=5822,
      universe_count=5822,
      commit_phase="IDLE",
    )

  monkeypatch.setattr(agent_schema, "utcnow", lambda: now)
  monkeypatch.setattr(agent_schema.market_stream_store, "state", stream_state)

  result = await agent_schema.resolve_qmt_agent_connection(
    db,
    user_id="user-1",
    account_id="account-1",
  )

  assert result.current is not None
  assert result.current.id == incumbent.id
  assert result.current.status == "READY"
  assert result.current.account_id == "account-1"
  assert result.current.xtdata_status == "CONNECTED"
  assert result.current.market_stream.instrument_count == 5822
  assert result.current.market_stream.ack_latency_ms == 186.4
  assert result.current.diagnostics.protocol_version == "1.1"
  assert result.current.last_seen_at is not None
  assert result.current.last_seen_at.tzinfo is timezone.utc
  assert result.handover_status == "RECONCILING"
  assert result.handover_device_status == "RECONCILING"
  assert [entry.id for entry in result.history] == [history.id]
  assert result.history[0].last_seen_at is not None
  assert result.history[0].last_seen_at.tzinfo is timezone.utc
  assert result.history[0].revoked_at is not None
  assert result.history[0].revoked_at.tzinfo is timezone.utc


@pytest.mark.parametrize("account_ids", [(), ("a", "b"), ("",)])
def test_qmt_connection_rejects_non_singular_account_scope(account_ids):
  with pytest.raises(ValueError, match="只授权一个账户"):
    agent_schema._single_account_id(account_ids)


@pytest.mark.parametrize(
  ("value", "now"),
  [
    (
      datetime(2026, 8, 22, 10, 29, 48, tzinfo=timezone.utc),
      datetime(2026, 8, 22, 10, 30),
    ),
    (
      datetime(2026, 8, 22, 10, 29, 48),
      datetime(2026, 8, 22, 10, 30, tzinfo=timezone.utc),
    ),
  ],
)
def test_age_seconds_normalizes_mixed_database_timezones(value, now):
  assert agent_schema._age_seconds(value, now) == 12.0
