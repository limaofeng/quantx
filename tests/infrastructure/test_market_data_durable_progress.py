"""Exercise real PG fencing and migration in session-local temporary tables."""

import importlib.util
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from quantx_application.market_data.ingestion import IngestionEvidenceConflict
from quantx_infrastructure import runtime_store
from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion
from quantx_infrastructure.services.market_data_persistence_verification import (
  MarketDataPersistenceBlockedError,
)
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.infrastructure.test_market_data_transfer_ingestion import (
  _payload,
  _summary,
  _tick_row,
  _write_chunk,
)


class Store(runtime_store.DurableRuntimeStore):
  async def market_data_request(self, request_id):
    async with self.engine.connect() as connection:
      return dict(
        (
          await connection.execute(
            text("SELECT * FROM market_data_request WHERE request_id=:id"),
            {"id": request_id},
          )
        )
        .mappings()
        .one()
      )

  async def market_data_transfers(self, request_id):
    return self.manifest


@pytest.fixture
async def durable_store(monkeypatch):
  url = make_url(os.environ["DATABASE_URL"])
  assert url.host in {"localhost", "127.0.0.1", "::1"}
  assert url.database.endswith("_test") or url.database.startswith("test_")
  engine = create_async_engine(url, pool_size=1, max_overflow=0)
  store = object.__new__(Store)
  store.engine = engine
  store.manifest = []
  clock = [datetime(2026, 9, 9, 12)]
  monkeypatch.setattr(runtime_store, "_utcnow", lambda: clock[0])
  async with engine.begin() as connection:
    await connection.execute(
      text("""
      CREATE TEMP TABLE market_data_request (
        request_id varchar(36) PRIMARY KEY, status varchar(24),
        processing_claim_token varchar(36), processing_error text, device_id varchar(36),
        ingestion_result json, request_payload json,
        created_at timestamp, updated_at timestamp, completed_at timestamp,
        expected_chunks integer, received_chunks integer
      )
    """)
    )
    path = (
      Path(__file__).resolve().parents[2]
      / "packages/infrastructure/alembic/versions/20260909_0061_market_data_ingestion_progress.py"
    )
    spec = importlib.util.spec_from_file_location("progress_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def upgrade(sync_connection):
      with Operations.context(MigrationContext.configure(sync_connection)):
        migration.upgrade()

    await connection.run_sync(upgrade)
    await connection.execute(
      text("""
      INSERT INTO market_data_request(request_id,status,request_payload,created_at,updated_at,expected_chunks,received_chunks)
      VALUES ('request-1','UPLOADED',CAST(:payload AS json),:now,:now,1,1)
    """),
      {"payload": json.dumps(_payload()), "now": clock[0]},
    )
  try:
    yield store, clock
  finally:
    await engine.dispose()


async def test_restart_resumes_write_blocks_then_only_readback(
  durable_store, tmp_path, monkeypatch
):
  store, clock = durable_store
  rows = [_tick_row(ordinal=i) for i in range(3)]
  store.manifest = [_write_chunk(tmp_path, [*rows, _summary(rows)])]
  monkeypatch.setattr(ingestion, "MARKET_DATA_TICK_WRITE_BATCH_RECORDS", 1)
  writes = []
  reads = []

  async def save_period(**kwargs):
    ordinal = int(next(iter(kwargs["market_data"].values())).iloc[0]["tick_ordinal"])
    writes.append(ordinal)
    if len(writes) == 2:
      raise ConnectionError("unknown write result")
    return {"status": "success", "saved_count": 1}

  async def verify(**kwargs):
    assert kwargs["max_attempts"] == 1 and kwargs["retry_delays"] == ()
    batches = [batch async for batch in kwargs["expected_key_batches"]]
    reads.append(sum(len(batch.keys) for batch in batches))
    if len(reads) == 1:
      raise ConnectionError("read temporarily unavailable")
    summaries = [
      {
        key: value
        for key, value in item.items()
        if key
        in {
          "code",
          "period",
          "row_count",
          "min_time",
          "max_time",
          "key_sha256",
        }
      }
      for item in kwargs["code_summaries"]
    ]
    return {
      "status": "verified",
      "records_verified": 3,
      "groups_verified": 1,
      "code_summaries": summaries,
    }

  async def ingest(s, request_id, *, progress):
    return await ingestion.ingest_uploaded_bar_request(
      s,
      request_id,
      save_period=save_period,
      verify_persistence=verify,
      progress=progress,
    )

  result = await ingestion.claim_ingest_and_finish_market_data_request(
    store, "request-1", ingest_request=ingest
  )
  assert result["status"] == "retryable"
  state = (await store.market_data_request("request-1"))["ingestion_progress"]
  assert state["phase"] == "WRITE" and list(state["checkpoints"]) == ["0"]
  assert await store.claim_market_data_request("request-1") is None
  assert await store.recoverable_market_data_request_ids() == []
  clock[0] += timedelta(seconds=5)
  # A new adapter has no in-memory progress from the previous execution.
  restarted = object.__new__(Store)
  restarted.engine, restarted.manifest = store.engine, store.manifest
  result = await ingestion.claim_ingest_and_finish_market_data_request(
    restarted, "request-1", ingest_request=ingest
  )
  assert result["status"] == "retryable"
  state = (await store.market_data_request("request-1"))["ingestion_progress"]
  assert state["phase"] == "READBACK" and len(state["checkpoints"]) == 3
  clock[0] += timedelta(seconds=5)
  result = await ingestion.claim_ingest_and_finish_market_data_request(
    restarted, "request-1", ingest_request=ingest
  )
  assert result["status"] == "completed"
  assert writes == [0, 1, 1, 2] and reads == [3, 3]
  state = (await store.market_data_request("request-1"))["ingestion_progress"]
  assert state["phase"] == "VERIFIED" and len(state["failures"]) == 2


async def test_capacity_blocks_until_explicit_resume(durable_store):
  store, _ = durable_store

  async def fail(*args, **kwargs):
    raise MarketDataPersistenceBlockedError(
      "DEPENDENCY_QUERY_CAPACITY_BLOCKED", {"query_sha256": "a" * 64}
    )

  result = await ingestion.claim_ingest_and_finish_market_data_request(
    store, "request-1", ingest_request=fail
  )
  assert result["status"] == "blocked"
  assert await store.recoverable_market_data_request_ids() == []
  assert await store.claim_market_data_request("request-1") is None
  state = await store.resume_blocked_market_data_request(
    "request-1", reason="verified failed range after repair"
  )
  assert state["attempt"] == 2 and state["executions"] == 0
  assert len(state["failures"]) == 2
  assert await store.claim_market_data_request("request-1")


async def test_retry_budget_survives_restarts(durable_store):
  store, clock = durable_store

  async def fail(*args, **kwargs):
    raise ConnectionError("password must not escape provider exception")

  for delay in (5, 30, 120, None):
    result = await ingestion.claim_ingest_and_finish_market_data_request(
      store, "request-1", ingest_request=fail
    )
    assert result["status"] == ("blocked" if delay is None else "retryable")
    assert "password" not in json.dumps(
      await store.market_data_request("request-1"), default=str
    )
    if delay is not None:
      clock[0] += timedelta(seconds=delay)
  assert await store.claim_market_data_request("request-1") is None
  state = (await store.market_data_request("request-1"))["ingestion_progress"]
  assert state["executions"] == 4 and len(state["failures"]) == 4


async def test_old_owner_and_mutated_manifest_cannot_advance(durable_store):
  store, clock = durable_store
  first = await store.claim_market_data_request("request-1")
  await store.mutate_market_data_ingestion(
    "request-1", claim_token=first, action="begin"
  )
  await store.mutate_market_data_ingestion(
    "request-1", claim_token=first, action="manifest", values={"sha256": "a" * 64}
  )
  with pytest.raises(IngestionEvidenceConflict):
    await store.mutate_market_data_ingestion(
      "request-1", claim_token=first, action="manifest", values={"sha256": "b" * 64}
    )
  clock[0] += timedelta(minutes=6)
  assert not await store.renew_market_data_request_claim("request-1", claim_token=first)
  with pytest.raises(RuntimeError):
    await store.finish_market_data_request(
      "request-1", claim_token=first, status="COMPLETED", ingestion_result={}
    )
  second = await store.claim_market_data_request("request-1")
  assert first != second
  with pytest.raises(RuntimeError, match="claim was lost"):
    await store.mutate_market_data_ingestion(
      "request-1", claim_token=first, action="advance", values={"phase": "WRITE"}
    )
  state = await store.mutate_market_data_ingestion(
    "request-1", claim_token=second, action="begin"
  )
  assert state["blocked"] and state["reason_code"] == "INGESTION_RETRY_BUDGET_EXHAUSTED"


async def test_api_dispatch_stops_on_shared_capacity_block(durable_store, monkeypatch):
  from quantx_api import agent_api

  store, _ = durable_store
  token = await store.claim_market_data_request("request-1")
  await store.mutate_market_data_ingestion(
    "request-1", claim_token=token, action="begin"
  )
  await store.mutate_market_data_ingestion(
    "request-1",
    claim_token=token,
    action="defer",
    values={
      "reason_code": "DEPENDENCY_QUERY_CAPACITY_BLOCKED",
      "blocked": True,
    },
  )

  class Session:
    async def __aenter__(self):
      self.connection = await store.engine.connect()
      return self

    async def __aexit__(self, *args):
      await self.connection.close()

    async def scalar(self, statement):
      assert "agent_devices" in str(statement)
      return "device-1"

    async def get(self, *args):
      return None

    async def scalars(self, statement):
      return (await self.connection.execute(statement)).scalars()

  monkeypatch.setattr(agent_api, "AsyncSessionLocal", Session)
  monkeypatch.setattr(
    agent_api,
    "evaluate_agent_session",
    lambda *args, **kwargs: SimpleNamespace(
      current=True,
      api_instance_id="api-1",
      agent_session_id="session-1",
    ),
  )
  assert (
    await agent_api._next_market_data_request(
      SimpleNamespace(
        device_id="device-1",
        capabilities=["market-data"],
        api_instance_id="api-1",
        agent_session_id="session-1",
      )
    )
    is None
  )
