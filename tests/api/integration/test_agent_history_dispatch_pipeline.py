"""Real PostgreSQL device-row locking, in a disposable test-only schema."""

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from quantx_api import agent_api
from quantx_infrastructure.models.agent_runtime import MarketDataRequest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DEVICE = "22222222-2222-4222-8222-222222222222"


@asynccontextmanager
async def database(monkeypatch, *, window_open=False):
  from quantx_infrastructure.services import development_history_window

  async def window():
    return window_open

  monkeypatch.setattr(development_history_window, "history_window_open", window)
  url = os.environ["DATABASE_URL"]
  name = make_url(url).database
  assert name and (name.endswith("_test") or name.startswith("test_"))
  schema = "history_dispatch_" + uuid.uuid4().hex
  engine = create_async_engine(
    url,
    connect_args={
      "timeout": 10,
      "server_settings": {"search_path": schema},
    },
  )
  try:
    async with engine.begin() as conn:
      await conn.execute(text(f"CREATE SCHEMA {schema}"))
      await conn.execute(text("CREATE TABLE market_data_history_session (device_id VARCHAR(36), expires_at TIMESTAMPTZ)"))
      await conn.execute(
        text(
          "CREATE TABLE agent_devices (id VARCHAR(36) PRIMARY KEY, revoked_at TIMESTAMP)"
        )
      )
      await conn.execute(
        text("""CREATE TABLE runtime_component_heartbeats (
        component VARCHAR(48) PRIMARY KEY, instance_id VARCHAR(64), status VARCHAR(32),
        details JSON, updated_at TIMESTAMP)""")
      )
      await conn.execute(
        text("""CREATE TABLE market_data_request (
        request_id VARCHAR(36) PRIMARY KEY, device_id VARCHAR(36), idempotency_key VARCHAR(128),
        request_payload JSON, development_only BOOLEAN NOT NULL DEFAULT FALSE,
        status VARCHAR(24), expected_chunks INTEGER, received_chunks INTEGER,
        completed_at TIMESTAMP, processing_error TEXT, processing_claim_token VARCHAR(36),
        ingestion_result JSON, ingestion_progress JSONB, processing_worker_epoch BIGINT, created_at TIMESTAMP, updated_at TIMESTAMP)""")
      )
      now = agent_api.utcnow()
      details = json.dumps(
        {
          "apiInstanceId": "api-1",
          "agentSessionId": "session-1",
          "serverReceivedAt": now.isoformat(),
          "agentSentAt": now.isoformat(),
          "sessionActive": True,
        }
      )
      await conn.execute(
        text("INSERT INTO agent_devices(id) VALUES (:device)"), {"device": DEVICE}
      )
      await conn.execute(
        text("""INSERT INTO runtime_component_heartbeats
        (component,instance_id,status,details,updated_at) VALUES
        ('api','api-1','READY',:api,:now), (:component,:device,'READY',:agent,:now)"""),
        {
          "api": json.dumps({"apiInstanceId": "api-1"}),
          "component": f"qmt-agent:{DEVICE}",
          "device": DEVICE,
          "agent": details,
          "now": now,
        },
      )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(agent_api, "AsyncSessionLocal", sessions)
    control = agent_api.AgentControlSession(
      device_id=DEVICE,
      capabilities={"market-data"},
      authorized_account_ids=frozenset(),
      queue=asyncio.Queue(),
      api_instance_id="api-1",
      agent_session_id="session-1",
      server_connected_at=now,
      remote_address_summary="local-test",
      revoked=asyncio.Event(),
    )
    yield sessions, control
  finally:
    async with engine.begin() as conn:
      await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    await engine.dispose()


async def seed(sessions, status, *, development=False, age=0):
  identity = str(uuid.uuid4())
  now = agent_api.utcnow()
  async with sessions() as db:
    db.add(
      MarketDataRequest(
        request_id=identity,
        device_id=DEVICE,
        idempotency_key=identity,
        request_payload={"operation": "bars", "stock_list": ["600000.SH"]},
        development_only=development,
        status=status,
        received_chunks=1,
        expected_chunks=1,
        created_at=now - timedelta(seconds=age),
        updated_at=now,
      )
    )
    await db.commit()
  return identity


async def test_dedicated_history_session_prevents_legacy_dispatch(monkeypatch):
  async with database(monkeypatch) as (sessions, control):
    request_id = await seed(sessions, "QUEUED")
    async with sessions() as db:
      await db.execute(text("""
        INSERT INTO market_data_history_session(device_id,expires_at)
        VALUES (:device,clock_timestamp()+INTERVAL '15 seconds')
      """), {"device": DEVICE})
      await db.commit()
    assert await agent_api._next_market_data_request(control) is None
    async with sessions() as db:
      request = await db.get(MarketDataRequest, request_id)
      assert request.status == "QUEUED"


@pytest.mark.parametrize(
  "status,allowed",
  [
    ("DELIVERED", False),
    ("RECEIVING", False),
    ("UPLOADED", True),
    ("PROCESSING", True),
  ],
)
async def test_native_occupancy_differs_from_ingestion(monkeypatch, status, allowed):
  async with database(monkeypatch) as (sessions, control):
    await seed(sessions, status)
    queued = await seed(sessions, "QUEUED")
    envelope = await agent_api._next_market_data_request(control)
    assert bool(envelope) is allowed
    if envelope:
      assert envelope.message_id == queued


@pytest.mark.parametrize("terminal", ["COMPLETED", "FAILED"])
async def test_concurrent_dispatch_is_bounded_and_recovers_after_terminal(
  monkeypatch, terminal
):
  async with database(monkeypatch) as (sessions, control):
    previous = await seed(sessions, "PROCESSING")
    queued = [await seed(sessions, "QUEUED", age=3 - i) for i in range(3)]
    outcomes = await asyncio.gather(
      *(agent_api._next_market_data_request(control) for _ in range(6))
    )
    assert [e.message_id for e in outcomes if e] == [queued[0]]
    async with sessions() as db:
      first = await db.get(MarketDataRequest, queued[0])
      first.status = "UPLOADED"
      await db.commit()
    assert await agent_api._next_market_data_request(control) is None
    async with sessions() as db:
      row = await db.get(MarketDataRequest, previous)
      row.status = terminal
      await db.commit()
    outcomes = await asyncio.gather(
      *(agent_api._next_market_data_request(control) for _ in range(6))
    )
    assert [e.message_id for e in outcomes if e] == [queued[1]]


@pytest.mark.parametrize("window_open", [False, True])
async def test_production_priority_and_development_window_are_preserved(
  monkeypatch, window_open
):
  async with database(monkeypatch, window_open=window_open) as (sessions, control):
    await seed(sessions, "PROCESSING")
    development = await seed(sessions, "QUEUED", development=True, age=60)
    production = await seed(sessions, "QUEUED")
    assert (await agent_api._next_market_data_request(control)).message_id == production
    async with sessions() as db:
      row = await db.get(MarketDataRequest, production)
      row.status = "COMPLETED"
      await db.commit()
    envelope = await agent_api._next_market_data_request(control)
    assert bool(envelope) is window_open
    if envelope:
      assert envelope.message_id == development
