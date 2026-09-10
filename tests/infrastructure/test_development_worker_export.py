"""Production export ownership, file publication and recovery on isolated PG."""
# ruff: noqa: F811

import asyncio
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_infrastructure.services import development_history_export as exporter
from quantx_infrastructure.services.development_delivery_execution import (
  DeliveryOwnershipLost,
  run_delivery_execution,
)
from quantx_market_data import worker
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401
from tests.worker.test_development_data_export import bar


@pytest.fixture
async def export_case(workers, tmp_path, monkeypatch):
  (first, second), _ = workers
  assert await first.acquire()
  monkeypatch.setenv("ENV", "production")
  monkeypatch.setenv("QUANTX_DATA_EXPORT_ROOT", str(tmp_path))
  request = HistoryPartitionRequest(
    instrument="600000.SH", period="1m", trading_date="2026-09-07"
  )
  records = exporter.partition_records([[bar()]], request)
  chunks = exporter.publish(records)
  files = [
    {**chunk, "storage_reference": str(exporter.content_path(chunk["checksum_sha256"]))}
    for chunk in chunks
  ]
  async with first.engine.begin() as db:
    await db.execute(
      text("ALTER TABLE development_data_export ADD COLUMN manifest json")
    )
    await db.execute(
      text("ALTER TABLE development_data_export ADD COLUMN source_request_id text")
    )
    await db.execute(
      text("""
      INSERT INTO development_data_export(id,request,state,source_request_id,updated_at)
      VALUES ('export',CAST(:request AS JSON),'QUEUED','original','2000-01-01')
    """),
      {"request": request.model_dump_json()},
    )
  from quantx_infrastructure.services.immutable_bar_storage import (
    prepare_native_bar_bundle,
  )

  bundle = await prepare_native_bar_bundle(request.agent_payload(), files)
  source = {
    "status": "COMPLETED",
    "request_payload": request.agent_payload(),
    "created_at": datetime(2026, 9, 7, 8),
    "development_only": False,
    "ingestion_result": {
      "native_storage_version": bundle.storage_version,
      "records_verified": bundle.records,
      "persistence_verification": {
        "status": "verified",
        "records_verified": bundle.records,
      },
      "content_verification": {
        "schema_version": 1,
        "records_verified": bundle.records,
        "fields_verified": 8,
        "storage_version": bundle.storage_version,
        "source_sha256": bundle.content_sha256,
        "persisted_sha256": bundle.content_sha256,
      },
      "day_coverage": [
        {
          "instrument_code": request.instrument,
          "period": request.period,
          "trading_date": str(request.trading_date),
          "point_count": 1,
          "content_sha256": bundle.coverage[0][4],
        }
      ],
    },
  }
  monkeypatch.setattr(first, "market_data_request", AsyncMock(return_value=source))
  monkeypatch.setattr(second, "market_data_request", first.market_data_request)
  monkeypatch.setattr(
    exporter, "load_uploaded_request_manifest", AsyncMock(return_value=({}, {}, files))
  )
  monkeypatch.setattr(
    exporter, "export_reference", AsyncMock(return_value={"fixture": "reference"})
  )
  lock_engine = create_async_engine(first.engine.url, pool_size=1, max_overflow=0)

  async def locked(request, _factory, execute, *, worker_owner=None, lock_key=None):
    # Temporary business tables live on their original connection. The lock is
    # a real separate PG backend; only its worker-lease query is routed back.
    class RoutedOwner:
      async def _guard_ingestion_owner(self, db, *, lock=True):
        if getattr(db, "bind", getattr(db, "engine", None)) is lock_engine:
          async with first.engine.connect() as business:
            await worker_owner._guard_ingestion_owner(business, lock=False)
        else:
          await worker_owner._guard_ingestion_owner(db, lock=lock)

    return await run_delivery_execution(
      request,
      async_sessionmaker(lock_engine),
      execute,
      worker_owner=RoutedOwner(),
      lock_key=lock_key,
    )

  monkeypatch.setattr(exporter, "run_delivery_execution", locked)
  try:
    yield SimpleNamespace(
      first=first,
      second=second,
      request=request,
      source=source,
      root=tmp_path,
      lock_engine=lock_engine,
    )
  finally:
    await lock_engine.dispose()


async def row(case):
  async with case.first.engine.connect() as db:
    return (
      (
        await db.execute(
          text("SELECT * FROM development_data_export WHERE id='export'")
        )
      )
      .mappings()
      .one()
    )


@pytest.mark.parametrize("corrupt", [False, True])
async def test_default_dispatch_rebuilds_missing_files_from_original_fixed_version(
  export_case, monkeypatch, corrupt
):
  from quantx_infrastructure.services import local_history_reader, native_bar_ingestion
  from quantx_infrastructure.services.market_data_transfer_ingestion import (
    _iter_transfer_chunks,
    ingest_uploaded_bar_request,
  )

  from tests.infrastructure.test_immutable_bar_storage import VersionStorage
  from tests.infrastructure.test_market_data_transfer_ingestion import _summary
  from tests.infrastructure.test_native_bar_ingestion import Source

  case, storage = export_case, VersionStorage()
  monkeypatch.setenv("QUANTX_RUNTIME_DIR", str(case.root))
  monkeypatch.setattr(
    native_bar_ingestion, "get_timeseries_connection", lambda: storage
  )
  monkeypatch.setattr(
    local_history_reader, "get_timeseries_connection", lambda: storage
  )
  payload, original = case.request.agent_payload(), bar()
  source = Source(
    case.root,
    payload,
    [original, _summary([original], code=case.request.instrument, period="1m")],
  )
  audit = await ingest_uploaded_bar_request(source, source.identity)
  changed = {**original, "close": original["close"] + 0.1}
  newer = Source(
    case.root,
    payload,
    [changed, _summary([changed], code=case.request.instrument, period="1m")],
  )
  await ingest_uploaded_bar_request(newer, newer.identity)
  case.source.update(request_payload=payload, ingestion_result=audit)
  if corrupt:
    audit["content_verification"]["persisted_sha256"] = "0" * 64
  monkeypatch.setattr(
    exporter,
    "load_uploaded_request_manifest",
    AsyncMock(side_effect=FileNotFoundError()),
  )
  result = await exporter.dispatch_once(case.first)
  if corrupt:
    assert result["status"] == "incomplete"
    failed = await row(case)
    assert failed["state"] == "INCOMPLETE"
    assert failed["error"] == "NATIVE_VERSION_PROOF_INVALID"
    assert failed["source_request_id"] == "original" and failed["manifest"] is None
    return
  assert result["status"] == "processed"
  published = await row(case)
  from quantx_infrastructure.services.development_delivery_manifest import (
    validate_delivery_manifest,
  )
  from quantx_infrastructure.services.development_history_import import ImportedTransfer
  from quantx_infrastructure.services.immutable_bar_storage import (
    prepare_immutable_bar_version,
  )

  validate_delivery_manifest(published["manifest"], case.request)
  imported = await prepare_immutable_bar_version(
    ImportedTransfer(published["manifest"]), "export"
  )
  assert (
    imported.content_sha256 == published["manifest"]["source_proof"]["partition_sha256"]
  )
  assert published["state"] == "READY" and published["source_request_id"] == "original"
  files = [
    {**chunk, "storage_reference": str(exporter.content_path(chunk["checksum_sha256"]))}
    for chunk in published["manifest"]["chunks"]
  ]
  records = next(_iter_transfer_chunks(files))
  assert records[0]["close"] == original["close"]


async def test_ambiguous_reuse_does_not_create_or_bind_replacement_source(
  export_case, monkeypatch
):
  case = export_case
  async with case.first.engine.begin() as db:
    await db.execute(
      text(
        "UPDATE development_data_export SET source_request_id=NULL WHERE id='export'"
      )
    )
  create = AsyncMock()
  monkeypatch.setattr(case.first, "create_market_data_request", create)
  monkeypatch.setattr(
    exporter,
    "find_reusable_source_request",
    AsyncMock(
      side_effect=exporter.SourceReuseConflict("SOURCE_VERSION_ORDER_AMBIGUOUS")
    ),
  )
  assert (await exporter.dispatch_once(case.first))["status"] == "incomplete"
  failed = await row(case)
  assert failed["state"] == "INCOMPLETE" and failed["source_request_id"] is None
  assert failed["error"] == "SOURCE_VERSION_ORDER_AMBIGUOUS"
  create.assert_not_awaited()


async def test_default_dispatch_publishes_once_with_original_source(
  export_case, monkeypatch
):
  case = export_case
  publish = exporter.publish
  calls = []

  def counted(records):
    calls.append(1)
    return publish(records)

  monkeypatch.setattr(exporter, "publish", counted)
  assert (await exporter.dispatch_once(case.first))["status"] == "processed"
  published = await row(case)
  assert published["state"] == "READY"
  assert published["source_request_id"] == "original"
  assert published["manifest"]["source_request_id"] == "original"
  assert published["manifest"]["rows"] == 1
  assert published["expires_at"] is not None
  assert (await exporter.dispatch_once(case.first))["status"] == "idle"
  assert calls == [1]


async def test_unverified_link_and_malformed_request_do_not_block_next_partition(
  export_case,
):
  case = export_case
  case.source["ingestion_result"]["day_coverage"] = []
  assert (await exporter.dispatch_once(case.first))["status"] == "incomplete"
  assert (await row(case))["error"] == "SOURCE_COVERAGE_UNVERIFIED"
  assert (await row(case))["source_request_id"] == "original"
  async with case.first.engine.begin() as db:
    await db.execute(
      text(
        "INSERT INTO development_data_export(id,request,state,updated_at) VALUES ('bad','{}','QUEUED','2000-01-01')"
      )
    )
  assert (await exporter.dispatch_once(case.first))["status"] == "incomplete"
  assert (await exporter.dispatch_once(case.first))["status"] == "idle"


async def test_cleanup_deferral_does_not_block_publication(export_case, monkeypatch):
  monkeypatch.setattr(
    exporter,
    "cleanup_expired",
    AsyncMock(side_effect=exporter.ExportCleanupDeferred("unresolved evidence")),
  )
  assert (await exporter.dispatch_once(export_case.first))["status"] == "processed"


async def test_old_owner_cannot_publish_after_file_write(export_case, monkeypatch):
  case = export_case

  async def reference(*_):
    await case.first.release()
    assert await case.second.acquire()
    return {"reference": "new"}

  monkeypatch.setattr(exporter, "export_reference", reference)
  with pytest.raises(DeliveryOwnershipLost):
    await exporter.dispatch_once(case.first)
  pending = await row(case)
  assert pending["state"] == "WAITING_SOURCE" and pending["manifest"] is None
  assert pending["source_request_id"] == "original"
  monkeypatch.setattr(
    exporter, "export_reference", AsyncMock(return_value={"reference": "new"})
  )
  assert (await exporter.dispatch_once(case.second))["status"] == "processed"


async def test_cancellation_holds_global_lock_until_real_write_finishes(
  export_case, monkeypatch
):
  case = export_case
  entered, release = threading.Event(), threading.Event()
  publish = exporter.publish

  def blocked(records):
    entered.set()
    assert release.wait(5)
    return publish(records)

  monkeypatch.setattr(exporter, "publish", blocked)
  task = asyncio.create_task(exporter.dispatch_once(case.first))
  observer = create_async_engine(case.first.engine.url)
  try:
    async with asyncio.timeout(3):
      while not entered.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    async with observer.begin() as db:
      assert not await db.scalar(text("SELECT pg_try_advisory_lock(817234591)"))
    release.set()
    with pytest.raises(asyncio.CancelledError):
      await task
    async with observer.begin() as db:
      assert await db.scalar(text("SELECT pg_try_advisory_lock(817234591)"))
      assert await db.scalar(text("SELECT pg_advisory_unlock(817234591)"))
    assert (await row(case))["manifest"] is None
  finally:
    release.set()
    await asyncio.gather(task, return_exceptions=True)
    await observer.dispose()


async def test_default_worker_runs_exports_alongside_receipts(export_case, monkeypatch):
  case = export_case
  await case.first.release()
  stop = asyncio.Event()
  finished = asyncio.Event()

  async def check_receipts():
    if (await row(case))["state"] == "READY":
      finished.set()
      stop.set()
    return 0

  async def sweep(*args, **kwargs):
    await asyncio.Event().wait()

  monkeypatch.setattr(worker, "sweep", sweep)
  monkeypatch.setattr(case.first, "dispatch_history_collection", AsyncMock())
  monkeypatch.setattr(case.first, "consume_collection_receipts", check_receipts)
  from quantx_infrastructure.services import market_data_staging_cleanup

  monkeypatch.setattr(
    market_data_staging_cleanup, "run_market_data_staging_sweeper", sweep
  )
  await asyncio.wait_for(worker.run(case.first, stop), 5)
  assert finished.is_set()


@pytest.mark.parametrize(
  "environment,source",
  [("development", "REMOTE"), ("testing", "AGENT"), ("production", "REMOTE")],
)
async def test_export_is_disabled_outside_production_agent(
  environment, source, monkeypatch
):
  monkeypatch.setenv("ENV", environment)
  assert await exporter.dispatch_once(SimpleNamespace(demand_source_kind=source)) == {
    "status": "disabled"
  }


async def test_source_creation_and_link_rollback_and_recover_together(
  export_case, monkeypatch
):
  case = export_case
  async with case.first.engine.begin() as db:
    await db.execute(
      text("ALTER TABLE market_data_request ADD COLUMN idempotency_key text UNIQUE")
    )
    await db.execute(
      text(
        "ALTER TABLE market_data_request ADD COLUMN development_only boolean DEFAULT false"
      )
    )
    await db.execute(
      text(
        "UPDATE development_data_export SET source_request_id=NULL WHERE id='export'"
      )
    )
  monkeypatch.setattr(exporter, "history_window_open", AsyncMock(return_value=True))
  monkeypatch.setattr(
    case.first,
    "_history_source_rows",
    AsyncMock(return_value=[{"id": "device", "capabilities": ["market-data"]}]),
  )
  monkeypatch.setattr(
    case.first,
    "market_data_request",
    type(case.first).market_data_request.__get__(case.first),
  )
  create = case.first.create_market_data_request

  async def fail_after_create(*args, **kwargs):
    assert kwargs["_connection"] is not None
    await create(*args, **kwargs)
    raise RuntimeError("injected link transaction failure")

  monkeypatch.setattr(case.first, "create_market_data_request", fail_after_create)
  with pytest.raises(RuntimeError, match="link transaction failure"):
    await exporter.dispatch_once(case.first)
  assert (await row(case))["source_request_id"] is None
  async with case.first.engine.connect() as db:
    assert (
      await db.scalar(
        text(
          "SELECT count(*) FROM market_data_request WHERE idempotency_key IS NOT NULL"
        )
      )
      == 0
    )
  monkeypatch.setattr(case.first, "create_market_data_request", create)
  assert (await exporter.dispatch_once(case.first))["status"] == "waiting"
  identity = (await row(case))["source_request_id"]
  assert identity is not None
  assert (await exporter.dispatch_once(case.first))["status"] == "waiting"
  assert (await row(case))["source_request_id"] == identity
  async with case.first.engine.connect() as db:
    assert (
      await db.scalar(
        text(
          "SELECT count(*) FROM market_data_request WHERE idempotency_key IS NOT NULL"
        )
      )
      == 1
    )
    assert (
      await db.scalar(
        text("SELECT development_only FROM market_data_request WHERE request_id=:id"),
        {"id": identity},
      )
      is True
    )


async def test_bad_partition_then_valid_partition_advances(export_case):
  case = export_case
  async with case.first.engine.begin() as db:
    await db.execute(
      text(
        "INSERT INTO development_data_export(id,request,state,updated_at) VALUES ('bad','{}','QUEUED','1999-01-01')"
      )
    )
  assert (await exporter.dispatch_once(case.first))["status"] == "incomplete"
  assert (await row(case))["state"] == "QUEUED"
  assert (await exporter.dispatch_once(case.first))["status"] == "processed"
  assert (await row(case))["state"] == "READY"


async def test_changed_catalog_state_cannot_be_reported_as_published(
  export_case, monkeypatch
):
  case = export_case

  async def block_before_publication(*_):
    async with case.first.engine.begin() as db:
      await db.execute(
        text("UPDATE development_data_export SET state='BLOCKED' WHERE id='export'")
      )
    return {"reference": "unchanged"}

  monkeypatch.setattr(exporter, "export_reference", block_before_publication)
  with pytest.raises(RuntimeError, match="EXPORT_PUBLICATION_CONFLICT"):
    await exporter.dispatch_once(case.first)
  pending = await row(case)
  assert pending["state"] == "BLOCKED" and pending["manifest"] is None
