"""Offline demand and source-link transactions against local temporary PG tables."""

# Imported fixtures are intentionally named by pytest test arguments.
# ruff: noqa: F811

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from quantx_contracts.market_data_service import HistoryDemand
from quantx_infrastructure import runtime_store
from quantx_infrastructure.services.market_data_demand_store import (
  MarketDataDemandCapacity,
)
from quantx_market_data.api import create_app
from sqlalchemy import text

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


def demand():
  return HistoryDemand(instrument="600000.SH", period="tick", trading_date="2026-09-09")


async def prepare_source(store, monkeypatch):
  monkeypatch.setattr(
    runtime_store, "_qmt_runtime_cutoff", lambda _: datetime(2026, 9, 9)
  )
  async with store.engine.begin() as connection:
    await connection.execute(
      text("""
      ALTER TABLE market_data_request ADD COLUMN idempotency_key varchar(64) UNIQUE,
      ADD COLUMN development_only boolean NOT NULL DEFAULT false
    """)
    )
    await connection.execute(
      text("""
      CREATE TEMP TABLE agent_devices(id varchar(36), user_id varchar(36), capabilities json, revoked_at timestamp)
    """)
    )
    await connection.execute(
      text("""
      CREATE TEMP TABLE market_data_history_session(
        device_id text,user_id text,capabilities json,heartbeat_at timestamptz,
        expires_at timestamptz,token_expires_at timestamptz
      )
    """)
    )
    await connection.execute(
      text("""
      INSERT INTO agent_devices VALUES ('device-1','history-user','["market-data"]',NULL)
    """)
    )
    await connection.execute(
      text("""
      INSERT INTO market_data_history_session VALUES (
        'device-1','history-user','["market-data"]','2026-09-09 12:00:00+00',
        clock_timestamp()+INTERVAL '1 hour',clock_timestamp()+INTERVAL '1 hour'
      )
    """)
    )


async def test_offline_demand_deduplicates_and_restarts_with_same_identity(
  workers, monkeypatch
):  # noqa: F811
  (first, second), _ = workers
  await prepare_source(first, monkeypatch)
  monkeypatch.setattr(runtime_store, "_qmt_runtime_cutoff", lambda _: None)
  identities = await asyncio.gather(
    *(first.submit_history_demand(demand()) for _ in range(5))
  )
  assert len(set(identities)) == 1
  identity = identities[0]
  before = await first.history_demand(identity)
  assert before["state"] == "WAITING_SOURCE"
  assert await first.acquire()
  assert await first.plan_history_demand()
  assert not await first.plan_history_demand()  # persisted dependency backoff
  waiting = await first.history_demand(identity)
  assert waiting["source_request_id"] is None
  assert waiting["reason_code"] == "HISTORY_SOURCE_OFFLINE"
  assert waiting["last_progress_at"] == before["last_progress_at"]
  await first.release()
  assert await second.acquire()
  monkeypatch.setattr(
    runtime_store, "_qmt_runtime_cutoff", lambda _: datetime(2026, 9, 9)
  )
  async with second.engine.begin() as connection:
    await connection.execute(
      text("UPDATE market_data_demand SET next_probe_at=clock_timestamp()")
    )
  assert await second.plan_history_demand()
  linked = await second.history_demand(identity)
  assert linked["state"] == "LINKED" and linked["source_status"] == "QUEUED"
  assert linked["reason_code"] is None
  assert await second.submit_history_demand(demand()) == identity
  assert not await second.plan_history_demand()
  async with second.engine.connect() as connection:
    assert (
      await connection.scalar(text("SELECT count(*) FROM market_data_request")) == 2
    )


async def test_remote_demand_only_creates_local_delivery_catalog(workers, monkeypatch):  # noqa: F811
  (store, _), _ = workers
  store.demand_source_kind = "REMOTE"
  no_agent = AsyncMock(side_effect=AssertionError("development must not select QMT"))
  monkeypatch.setattr(store, "create_market_data_request", no_agent)
  identity = await store.submit_history_demand(demand())
  assert await store.acquire()
  assert await store.plan_history_demand()
  linked = await store.history_demand(identity)
  assert linked["source_kind"] == "REMOTE" and linked["source_request_id"] is None
  assert linked["delivery_status"] == "QUEUED" and len(linked["delivery_id"]) == 64
  no_agent.assert_not_called()


async def test_lease_loss_rolls_back_source_creation_and_demand_link(
  workers, monkeypatch
):  # noqa: F811
  (store, _), _ = workers
  await prepare_source(store, monkeypatch)
  identity = await store.submit_history_demand(demand())
  assert await store.acquire()
  create = store.create_market_data_request

  async def expire_after_insert(*args, **kwargs):
    source_id = await create(*args, **kwargs)
    await kwargs["_connection"].execute(
      text("""
      UPDATE market_data_worker_lease SET expires_at=clock_timestamp() - INTERVAL '1 second'
    """)
    )
    return source_id

  monkeypatch.setattr(store, "create_market_data_request", expire_after_insert)
  with pytest.raises(RuntimeError, match="lease was lost"):
    await store.plan_history_demand()
  assert (await store.history_demand(identity))["state"] == "WAITING_SOURCE"
  async with store.engine.connect() as connection:
    assert (
      await connection.scalar(text("SELECT count(*) FROM market_data_request")) == 1
    )


async def test_api_accepts_offline_and_status_does_not_probe(workers, monkeypatch):  # noqa: F811
  (store, _), _ = workers
  forbidden = AsyncMock(
    side_effect=AssertionError("API must not create source requests")
  )
  monkeypatch.setattr(store, "create_market_data_request", forbidden)
  app = create_app(store=store, token="test-token")
  async with app.router.lifespan_context(app):
    async with AsyncClient(
      transport=ASGITransport(app),
      base_url="http://test",
      headers={"Authorization": "Bearer test-token"},
    ) as client:
      response = await client.post(
        "/market-data/internal/v1/demands", json=demand().model_dump(mode="json")
      )
      assert response.status_code == 202, response.text
      identity = response.json()["demand_id"]
      before = await store.history_demand(identity)
      for _ in range(2):
        response = await client.get(f"/market-data/internal/v1/demands/{identity}")
        assert response.status_code == 200
        assert response.json()["state"] == "WAITING_SOURCE"
      after = await store.history_demand(identity)
      assert before["next_probe_at"] == after["next_probe_at"]
      assert before["last_progress_at"] == after["last_progress_at"]
  forbidden.assert_not_called()


async def test_admission_cap_keeps_existing_identity_available(workers):  # noqa: F811
  (store, _), _ = workers
  identity = await store.submit_history_demand(demand())
  async with store.engine.begin() as connection:
    await connection.execute(
      text("""
      INSERT INTO market_data_demand(demand_id,partition,source_kind)
      SELECT 'fixture-' || i,'{}'::jsonb,'AGENT' FROM generate_series(1,4999) i
    """)
    )
  assert await store.submit_history_demand(demand()) == identity
  with pytest.raises(MarketDataDemandCapacity):
    await store.submit_history_demand(
      demand().model_copy(update={"instrument": "000001.SZ"})
    )


async def test_delivery_capacity_retains_demand_without_replacing_identity(workers):  # noqa: F811
  (store, _), _ = workers
  store.demand_source_kind = "REMOTE"
  identity = await store.submit_history_demand(demand())
  async with store.engine.begin() as connection:
    await connection.execute(
      text("""
      INSERT INTO development_data_export(id,request,state,updated_at)
      SELECT 'fixture-' || i,'{}'::json,'QUEUED',clock_timestamp()
      FROM generate_series(1,5000) i
    """)
    )
  assert await store.acquire()
  assert await store.plan_history_demand()
  result = await store.history_demand(identity)
  assert result["reason_code"] == "HISTORY_DELIVERY_CAPACITY"
  assert result["delivery_id"] is None
  assert not await store.plan_history_demand()
