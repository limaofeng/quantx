"""Independent API/worker lifecycle, exercised against local temporary PG tables."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import ASGITransport, AsyncClient
from quantx_infrastructure.services import (
  market_data_persistence_verification as verification,
)
from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion
from quantx_infrastructure.services.market_data_ingestion_progress import (
  IngestionProgress,
)
from quantx_infrastructure.services.market_data_worker_store import (
  MarketDataWorkerStore,
)
from quantx_market_data import worker
from quantx_market_data.api import create_app
from sqlalchemy import text

from tests.infrastructure.test_market_data_durable_progress import (  # noqa: F401
  Store,
  durable_store,
)
from tests.infrastructure.test_market_data_transfer_ingestion import (
  _summary,
  _tick_row,
  _write_chunk,
)


class WorkerStore(MarketDataWorkerStore, Store):
  pass


@pytest.fixture
async def workers(durable_store):  # noqa: F811 - imported pytest fixture
  original, clock = durable_store
  async with original.engine.begin() as connection:
    await connection.execute(
      text("ALTER TABLE market_data_request DROP COLUMN processing_worker_epoch")
    )
    path = (
      Path(__file__).resolve().parents[2]
      / "packages/infrastructure/alembic/versions/20260909_0062_market_data_worker_lease.py"
    )
    spec = importlib.util.spec_from_file_location("worker_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def upgrade(sync_connection):
      operations = Operations(MigrationContext.configure(sync_connection))
      migration.op = SimpleNamespace(
        add_column=operations.add_column,
        create_table=lambda *args, **kwargs: operations.create_table(
          *args, prefixes=["TEMPORARY"], **kwargs
        ),
      )
      migration.upgrade()

    await connection.run_sync(upgrade)
  result = []
  for owner in ("owner-a", "owner-b"):
    store = object.__new__(WorkerStore)
    store.engine, store.manifest = original.engine, []
    store.owner_id, store.epoch = owner, None
    result.append(store)
  yield result, clock


async def test_expired_worker_is_fenced_and_successor_recovers_immediately(workers):
  (first, second), _ = workers
  assert await first.acquire()
  assert not await second.acquire()
  token = await first.claim_market_data_request("request-1")
  await first.mutate_market_data_ingestion(
    "request-1", claim_token=token, action="begin"
  )
  async with first.engine.begin() as connection:
    await connection.execute(
      text(
        "UPDATE market_data_worker_lease SET expires_at = clock_timestamp() - INTERVAL '1 second'"
      )
    )
  assert await second.acquire() and second.epoch == first.epoch + 1
  assert not await first.renew()
  with pytest.raises(RuntimeError, match="worker lease was lost"):
    await first.mutate_market_data_ingestion(
      "request-1", claim_token=token, action="manifest", values={"sha256": "a" * 64}
    )
  assert await second.recoverable_market_data_request_ids() == ["request-1"]
  replacement = await second.claim_market_data_request("request-1")
  assert replacement and replacement != token
  state = await second.mutate_market_data_ingestion(
    "request-1", claim_token=replacement, action="begin"
  )
  assert state["executions"] == 2 and not state["blocked"]
  await first.release()
  assert await second.renew()


async def test_worker_sweep_publishes_proof_without_prefect(workers, tmp_path):
  (store, _), _ = workers
  assert await store.acquire()

  row = _tick_row()
  store.manifest = [_write_chunk(tmp_path, [row, _summary([row])])]
  writes = []

  async def save(**kwargs):
    writes.append(kwargs["period"])
    return {"status": "success", "saved_count": 1}

  async def verify(**kwargs):
    batches = [batch async for batch in kwargs["expected_key_batches"]]
    assert sum(len(batch.keys) for batch in batches) == 1
    summary = kwargs["code_summaries"][0]
    return {
      "status": "verified",
      "records_verified": 1,
      "groups_verified": 1,
      "code_summaries": [
        {
          k: summary[k]
          for k in ("code", "period", "row_count", "min_time", "max_time", "key_sha256")
        }
      ],
    }

  async def ingest(_store, request_id, *, progress):
    return await ingestion.ingest_uploaded_bar_request(
      _store,
      request_id,
      progress=progress,
      save_period=save,
      verify_persistence=verify,
    )

  assert await worker.sweep(store, ingest=ingest) == 1
  value = await store.market_data_request("request-1")
  assert (
    value["status"] == "COMPLETED"
    and value["ingestion_progress"]["phase"] == "VERIFIED"
  )
  assert value["ingestion_result"]["records_verified"] == 1
  assert writes == ["tick"]
  assert await worker.sweep(store, ingest=ingest) == 0


async def test_restart_reuses_verified_groups_without_requery(workers, monkeypatch):
  from tests.infrastructure.test_market_data_grouped_readback import batch
  from tests.infrastructure.test_market_data_readback_concurrency import verify

  (store, _), _ = workers
  assert await store.acquire()
  token = await store.claim_market_data_request("request-1")
  progress = IngestionProgress(store, "request-1", token)
  await progress.apply("begin")
  await progress.apply("manifest", sha256="a" * 64)
  await progress.apply("advance", phase="WRITE")
  await progress.apply("advance", phase="READBACK", write_result={})
  monkeypatch.setattr(verification, "MARKET_DATA_READBACK_GROUP_CODES", 2)
  groups = [batch(code) for code in ("A0", "A1", "B0", "B1")]
  confirmed = asyncio.Event()
  save_checkpoint = progress.confirm_readback

  async def confirm(*args):
    await save_checkpoint(*args)
    confirmed.set()

  progress.confirm_readback = confirm
  calls = []

  async def read(_function, **kwargs):
    code = kwargs["batches"][0].code
    calls.append(code)
    if code == "B0" and calls.count(code) == 1:
      await asyncio.wait_for(confirmed.wait(), 1)
      raise verification.MarketDataPersistenceQueryError("transient read error")
    return {"records_verified": 4, "existing_rows_observed": 0}

  monkeypatch.setattr(verification, "_await_readback", read)
  with pytest.raises(verification.MarketDataPersistenceQueryError):
    await verify(groups, progress=progress, max_attempts=1, retry_delays=())
  restored = IngestionProgress(store, "request-1", token)
  result = await verify(groups, progress=restored, max_attempts=1, retry_delays=())
  assert result["records_verified"] == 8
  assert calls == ["A0", "B0", "B0"]
  assert result["attempts_by_group"]["A0/1m"] == 0


async def test_api_status_is_read_only_and_resume_preserves_identity(workers):
  (store, _), _ = workers
  assert await store.acquire()
  token = await store.claim_market_data_request("request-1")
  await store.mutate_market_data_ingestion(
    "request-1", claim_token=token, action="begin"
  )
  before = await store.mutate_market_data_ingestion(
    "request-1",
    claim_token=token,
    action="defer",
    values={"reason_code": "DEPENDENCY_AUTH_BLOCKED", "blocked": True},
  )
  app = create_app(store=store, token="test-token")
  async with app.router.lifespan_context(app):
    async with AsyncClient(
      transport=ASGITransport(app), base_url="http://test"
    ) as client:
      path = "/market-data/internal/v1/requests/request-1"
      assert (
        await client.post(path + "/resume", json={"reason": "fixed"})
      ).status_code == 401
      client.headers["Authorization"] = "Bearer test-token"
      for _ in range(2):
        value = (await client.get(path)).json()
        assert (
          value["status"] == "BLOCKED"
          and value["reason_code"] == "DEPENDENCY_AUTH_BLOCKED"
        )
        assert "diagnostic" not in value and "processing_error" not in value
      assert (await store.market_data_request("request-1"))[
        "ingestion_progress"
      ] == before
      assert (
        await client.post(path + "/resume", json={"reason": "  "})
      ).status_code == 422
      value = (
        await client.post(path + "/resume", json={"reason": "repaired permission"})
      ).json()
      assert value == {"request_id": "request-1", "status": "UPLOADED", "attempt": 2}


async def test_worker_joins_active_ingestion_before_releasing_lease(monkeypatch):
  entered, finished = asyncio.Event(), asyncio.Event()
  stop = asyncio.Event()

  async def sweep(_store):
    entered.set()
    try:
      await asyncio.Event().wait()
    finally:
      finished.set()

  class Lease:
    async def acquire(self):
      return True

    async def release(self):
      assert finished.is_set()

  monkeypatch.setattr(worker, "sweep", sweep)
  task = asyncio.create_task(worker.run(Lease(), stop))
  await asyncio.wait_for(entered.wait(), 1)
  stop.set()
  await asyncio.wait_for(task, 1)
  assert finished.is_set()


async def test_reference_backlog_does_not_hide_supported_work(workers):
  (store, _), _ = workers
  async with store.engine.begin() as connection:
    await connection.execute(
      text("""
      INSERT INTO market_data_request(request_id,status,request_payload,updated_at,created_at)
      SELECT 'reference-' || i,'UPLOADED','{"operation":"divid_factors"}'::json,
             '2020-01-01'::timestamp,'2020-01-01'::timestamp
      FROM generate_series(1,30) AS i
    """)
    )
  assert await store.recoverable_market_data_request_ids(limit=1) == ["request-1"]


async def test_api_submission_validates_scope_and_only_persists_demand():
  store = SimpleNamespace(
    create_market_data_request=AsyncMock(return_value="request-1")
  )
  app = create_app(store=store, token="test-token")
  async with app.router.lifespan_context(app):
    async with AsyncClient(
      transport=ASGITransport(app),
      base_url="http://test",
      headers={"Authorization": "Bearer test-token"},
    ) as client:
      response = await client.post(
        "/market-data/internal/v1/requests",
        json={
          "instrument": "600000.SH",
          "period": "tick",
          "trading_date": "2026-09-01",
        },
      )
      assert response.status_code == 202 and response.json() == {
        "request_id": "request-1"
      }
      assert store.create_market_data_request.await_args.args[0] == {
        "operation": "bars",
        "download": True,
        "stock_list": ["600000.SH"],
        "periods": ["tick"],
        "start_time": "20260901",
        "end_time": "20260901",
      }
      response = await client.post(
        "/market-data/internal/v1/requests",
        json={
          "instrument": "600000.SH",
          "period": "tick",
          "trading_date": "2026-09-01",
          "account_id": "forbidden",
        },
      )
      assert response.status_code == 422
      store.create_market_data_request.assert_awaited_once()
