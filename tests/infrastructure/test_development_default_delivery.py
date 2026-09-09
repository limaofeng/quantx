"""Default importer/API with real PG locks, references and SDK/Arrow storage.

The lock connection is separate because fixture business tables are temporary.
Only external HTTP and Influx transport are replaced; no ingestion/proof mock.
"""
# ruff: noqa: F811

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.services import data_exchange as catalog
from quantx_infrastructure.services import development_history_import as importer
from quantx_infrastructure.services import (
  local_daily_snapshot_reader,
  local_history_reader,
)
from quantx_infrastructure.services.development_delivery_execution import (
  run_delivery_execution,
)
from quantx_infrastructure.services.development_ingestion_progress import (
  DevelopmentIngestionStore,
)
from quantx_infrastructure.services.market_data_ingestion_progress import evidence_hash
from quantx_market_data.api import create_app
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.infrastructure.test_development_bar_publication import prepared  # noqa: F401
from tests.infrastructure.test_development_ingestion_progress import due
from tests.infrastructure.test_development_reference_transaction import (
  references,  # noqa: F401
)
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401
from tests.infrastructure.test_published_daily_snapshots import SnapshotStorage

pytestmark = pytest.mark.parametrize(
  "prepared", [{"period": "1d", "start_write": False}], indirect=True
)


class Storage(SnapshotStorage):
  unavailable = False

  def query(self, **kwargs):
    if self.unavailable:
      self.queries.append(kwargs)
      raise RuntimeError("temporarily unavailable")
    return super().query(**kwargs)


@pytest.fixture
async def delivery(prepared, monkeypatch):
  case = prepared
  monkeypatch.setenv("ENV", "development")
  monkeypatch.setattr(settings, "environment", "development")
  monkeypatch.setenv("QUANTX_MARKET_DATA_URL", "http://source")
  monkeypatch.setenv("QUANTX_MARKET_DATA_TOKEN", "source-token")
  monkeypatch.setattr(importer, "AsyncSessionLocal", case.factory)
  monkeypatch.setattr(catalog, "AsyncSessionLocal", case.factory)
  # Start from the actual idempotent catalog submission, before any local attempt.
  async with case.factory() as db:
    await db.execute(text("DELETE FROM development_data_ingestion"))
    await db.execute(text("DELETE FROM development_data_export WHERE id='delivery'"))
    await db.commit()
  identity = await catalog.submit(case.request)
  connection, calls = Storage(), []
  monkeypatch.setattr(importer, "get_timeseries_connection", lambda: connection)
  monkeypatch.setattr(
    local_history_reader, "get_timeseries_connection", lambda: connection
  )
  monkeypatch.setattr(
    local_daily_snapshot_reader, "get_timeseries_connection", lambda: connection
  )
  files = {
    item["checksum_sha256"]: catalog.content_path(item["checksum_sha256"]).read_bytes()
    for item in case.manifest["chunks"]
  }
  for digest in files:
    catalog.content_path(digest).unlink()

  def remote(request):
    calls.append((request.method, request.url.path))
    if "/chunks/" in request.url.path:
      return httpx.Response(200, content=files[request.url.path.rsplit("/", 1)[-1]])
    return httpx.Response(
      200, json={"id": identity, "state": "READY", "manifest": case.manifest}
    )

  client_class = httpx.AsyncClient

  def client(**kwargs):
    if kwargs.get("base_url") == "http://source":
      kwargs["transport"] = httpx.MockTransport(remote)
    return client_class(**kwargs)

  monkeypatch.setattr(importer.httpx, "AsyncClient", client)
  lock_engine = create_async_engine(case.first.engine.url, pool_size=1, max_overflow=0)

  async def locked(request, _factory, execute, *, worker_owner=None):
    return await run_delivery_execution(
      request, async_sessionmaker(lock_engine), execute, worker_owner=worker_owner
    )

  monkeypatch.setattr(importer, "run_delivery_execution", locked)
  try:
    yield SimpleNamespace(
      **vars(case), identity=identity, connection=connection, calls=calls
    )
  finally:
    await lock_engine.dispose()


async def test_default_importer_and_api_publish_and_read_same_version(delivery):
  case = delivery
  receipt = await importer.import_partition(case.request)
  version = receipt["local_verification"]["immutable_storage"]["storage_version"]
  assert (await catalog.get_export(case.identity))["state"] == "LOCAL_VERIFIED"
  assert len(case.calls) == 3  # Submit, status, and actual streamed file download.
  assert all(
    table == "kline_1d_versions" for table, _ in case.connection.points.values()
  )
  app = create_app(store=SimpleNamespace(engine=case.first.engine), token="internal")
  async with app.router.lifespan_context(app):
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app),
      base_url="http://local",
      headers={"Authorization": "Bearer internal"},
    ) as client:
      history = await client.get(
        "/market-data/internal/v1/history",
        params={
          "instrument": case.request.instrument,
          "period": "1d",
          "trading_date": "2026-09-07",
        },
      )
      stamp = datetime(2026, 9, 7, tzinfo=ZoneInfo("Asia/Shanghai"))
      daily = await client.post(
        "/market-data/internal/v1/history/latest-daily",
        json={
          "instruments": [case.request.instrument],
          "start": stamp.isoformat(),
          "end": (stamp + timedelta(hours=23)).isoformat(),
        },
      )
      assert history.status_code == daily.status_code == 200
      assert history.json()["records"][0]["storage_version"] == version
      assert daily.json()["records"][0]["close"] == 10.1
      assert "storage_version" not in daily.json()["records"][0]
      async with case.factory() as db:
        await db.execute(
          text(
            "UPDATE development_data_export SET state='WAITING_LOCAL_PROOF' WHERE id=:id"
          ),
          {"id": case.identity},
        )
        await db.commit()
      before = len(case.connection.queries)
      assert (
        await client.get(
          "/market-data/internal/v1/history",
          params={
            "instrument": case.request.instrument,
            "period": "1d",
            "trading_date": "2026-09-07",
          },
        )
      ).status_code == 503
      assert len(case.connection.queries) == before


@pytest.mark.parametrize("failure", ["readback", "receipt"])
async def test_default_importer_recovers_without_network_or_rewriting(
  delivery, failure
):
  case = delivery
  case.connection.unavailable = failure == "readback"

  def reject(_connection, _cursor, statement, *_args):
    if failure == "receipt" and "SET state='LOCAL_VERIFIED'" in statement:
      raise RuntimeError("receipt rejected")

  event.listen(case.first.engine.sync_engine, "before_cursor_execute", reject)
  try:
    pending = await importer.import_partition(case.request)
  finally:
    event.remove(case.first.engine.sync_engine, "before_cursor_execute", reject)
  assert pending["status"] == "WAITING_LOCAL_INGESTION"
  assert pending["reason"] == "LOCAL_READBACK_UNAVAILABLE"
  store = DevelopmentIngestionStore(case.factory, case.identity)
  state = (await store.status())["progress"]
  assert state["phase"] == "READBACK"
  async with case.factory() as db:
    assert await db.scalar(text("SELECT count(*) FROM instruments")) == 0
    assert await db.scalar(text("SELECT count(*) FROM divid_factors")) == 1
    assert (
      await db.scalar(text("SELECT proof FROM development_data_bar_version")) is None
    )
  assert await importer.import_partition(case.request) == pending
  reads, calls, writes = (
    len(case.connection.queries),
    len(case.calls),
    len(case.connection.lines),
  )
  await due(case.first.engine)
  case.connection.unavailable = False
  receipt = await importer.import_partition(case.request)
  assert receipt["local_verification"]["records_verified"] == 1
  assert len(case.connection.queries) == reads + 1
  assert len(case.calls) == calls and len(case.connection.lines) == writes == 1
  state = (await store.status())["progress"]
  assert state["phase"] == "VERIFIED"
  # Completion response loss goes through the actual readonly version recovery.
  assert await importer.import_partition(case.request) == receipt
  assert len(case.calls) == calls and len(case.connection.lines) == writes


async def test_default_completed_recheck_waits_then_detects_reference_change(delivery):
  case = delivery
  receipt = await importer.import_partition(case.request)
  case.connection.unavailable = True
  pending = await importer.import_partition(case.request)
  assert pending["status"] == "WAITING_LOCAL_PROOF"
  reads = len(case.connection.queries)
  assert await importer.import_partition(case.request) == pending
  assert len(case.connection.queries) == reads
  case.connection.unavailable = False
  async with case.factory() as db:
    await db.execute(
      text(
        "UPDATE development_data_download_budget SET next_probe_at=clock_timestamp()"
      )
    )
    await db.commit()
  assert await importer.import_partition(case.request) == receipt
  async with case.factory() as db:
    await db.execute(text("UPDATE instruments SET price_tick=2"))
    await db.commit()
  blocked = await importer.import_partition(case.request)
  assert blocked["reason"] == "LOCAL_DELIVERY_PROOF_INVALID"
  assert await importer.import_partition(case.request) == blocked
  assert len(case.connection.lines) == 1 and len(case.calls) == 3
  async with case.factory() as db:
    assert await db.scalar(text("SELECT price_tick FROM instruments")) == 2


@pytest.mark.parametrize("legacy", ["receipt", "checkpoint"])
async def test_legacy_evidence_blocks_without_mutation_or_external_io(delivery, legacy):
  case = delivery
  # Restore the already downloaded files as a historical delivery would have them.
  # Their bytes and original manifest must survive the blocked migration decision.
  receipt = await importer.import_partition(case.request)
  async with case.factory() as db:
    if legacy == "receipt":
      del receipt["local_verification"]["immutable_storage"]
      await db.execute(
        text(
          "UPDATE development_data_export SET manifest=CAST(:receipt AS JSON) WHERE id=:id"
        ),
        {"receipt": json.dumps(receipt), "id": case.identity},
      )
    else:
      await db.execute(
        text(
          "UPDATE development_data_export SET state='WAITING_LOCAL_INGESTION',manifest=CAST(:manifest AS JSON) WHERE id=:id"
        ),
        {"manifest": json.dumps(case.manifest), "id": case.identity},
      )
      digest = evidence_hash(
        {"payload": case.manifest["payload"], "chunks": case.manifest["chunks"]}
      )
      await db.execute(
        text(
          "UPDATE development_data_ingestion SET progress=jsonb_set(jsonb_set(progress,'{phase}','\"READBACK\"'),'{manifest_hash}',CAST(:digest AS JSONB))"
        ),
        {"digest": json.dumps(digest)},
      )
    await db.commit()
  store = DevelopmentIngestionStore(case.factory, case.identity)
  before = (await store.status())["progress"]
  old_receipt = (await catalog.get_export(case.identity))["manifest"]
  calls = (len(case.calls), len(case.connection.lines), len(case.connection.queries))
  result = await importer.import_partition(case.request)
  assert result["reason"] == "LOCAL_STORAGE_VERSION_MIGRATION_REQUIRED"
  assert result["status"] == "BLOCKED"
  assert await importer.import_partition(case.request) == result
  assert (await store.status())["progress"] == before
  assert (await catalog.get_export(case.identity))["manifest"] == old_receipt
  assert (
    len(case.calls),
    len(case.connection.lines),
    len(case.connection.queries),
  ) == calls
  for item in case.manifest["chunks"]:
    assert catalog.content_path(item["checksum_sha256"]).is_file()
