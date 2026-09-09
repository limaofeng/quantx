"""Reference writes, repository commits and delivery receipts share one PG transaction."""
# ruff: noqa: F811

import copy
import hashlib
import json
from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_infrastructure.models.divid_factor import DividFactorTable
from quantx_infrastructure.models.holidays import Holiday
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.repositories.divid_factor_repository import (
  DividFactorRepository,
  divid_factor_rows_sha256,
)
from quantx_infrastructure.services.data_exchange_reference import (
  INSTRUMENT_FIELDS,
  import_reference_in_transaction,
  verify_imported_reference,
)
from sqlalchemy import MetaData, event, insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.schema import CreateTable

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


def reference():
  instrument = dict.fromkeys(INSTRUMENT_FIELDS)
  instrument.update(
    id="600000.SH",
    market="SH",
    instrument_id="600000",
    name="浦发银行",
    price_tick=0.01,
    volume_multiple=1,
  )
  return {
    "as_of": "2026-09-07",
    "instrument": instrument,
    "holidays": [
      {"market": "SH", "year": 2026, "date": "2026-01-01", "description": "元旦"}
    ],
    "factors": [],
    "factor_coverage": {
      "status": "VERIFIED",
      "expected_chunks": 1,
      "received_chunks": 1,
      "start_date": "20260101",
      "end_date": "20260907",
      "evidence": {
        "request_id": "source-factors",
        "stock_code": "600000.SH",
        "start_date": "2026-01-01",
        "end_date": "2026-09-07",
        "completed_at": "2026-09-07T12:00:00+08:00",
        "record_count": 0,
        "content_sha256": divid_factor_rows_sha256([]),
      },
    },
  }


@pytest.fixture
async def references(workers, monkeypatch):
  (store, _), _ = workers
  async with store.engine.begin() as connection:
    for model in (Instrument, Holiday, DividFactorTable):
      table = model.__table__.to_metadata(MetaData())
      table._prefixes = ["TEMPORARY"]
      await connection.execute(CreateTable(table))
    await connection.execute(
      text("ALTER TABLE development_data_export ADD COLUMN manifest json")
    )
    await connection.execute(
      insert(DividFactorTable).values(
        stock_code="600000.SH",
        ex_date="20260601",
        time=datetime(2026, 6, 1),
        dr=1,
      )
    )
  # All content writes/reads and repository commits remain real. Snapshot tables
  # are outside this focused temporary schema.
  monkeypatch.setattr(
    DividFactorRepository, "_invalidate_published_snapshots", AsyncMock()
  )
  assert await store.acquire()
  return store


@pytest.mark.parametrize("failure", [None, "receipt", "lease"])
async def test_reference_and_receipt_are_atomic_despite_repository_commit(
  references, monkeypatch, failure
):
  store = references
  replace = DividFactorRepository.replace_range

  async def write(repo, *args, **kwargs):
    result = await replace(repo, *args, **kwargs)
    if failure == "lease":
      await repo.db.execute(
        text(
          "UPDATE market_data_worker_lease SET expires_at=clock_timestamp()-INTERVAL '1 second'"
        )
      )
      await repo.db.commit()
    return result

  monkeypatch.setattr(DividFactorRepository, "replace_range", write)

  async def complete():
    async with store.engine.begin() as connection:
      audit = await import_reference_in_transaction(
        connection, reference(), code="600000.SH", owner=store
      )
      assert audit["instrument_records_verified"] == 1
      assert audit["calendar_records_verified"] == 1
      assert audit["factor_replacement"]["deleted_count"] == 1
      await connection.execute(
        text("""
        INSERT INTO development_data_export(id,request,state,updated_at)
        VALUES ('delivery','{}','LOCAL_VERIFIED',clock_timestamp())
      """)
      )
      if failure == "receipt":
        raise RuntimeError("receipt failure")

  if failure:
    with pytest.raises(RuntimeError):
      await complete()
  else:
    await complete()
  async with store.engine.connect() as connection:
    assert await connection.scalar(text("SELECT count(*) FROM instruments")) == (
      0 if failure else 1
    )
    assert await connection.scalar(text("SELECT count(*) FROM holidays")) == (
      0 if failure else 1
    )
    assert await connection.scalar(text("SELECT count(*) FROM divid_factors")) == (
      1 if failure else 0
    )
    assert await connection.scalar(
      text("SELECT count(*) FROM development_data_export")
    ) == (0 if failure else 2)


async def test_conflicting_calendar_cannot_produce_successful_reference_receipt(
  references,
):
  store = references
  async with store.engine.begin() as connection:
    await connection.execute(
      insert(Holiday).values(
        market="SH",
        year=2026,
        date=datetime(2026, 1, 1).date(),
        description="wrong",
      )
    )
  with pytest.raises(ValueError, match="calendar readback mismatch"):
    async with store.engine.begin() as connection:
      await import_reference_in_transaction(
        connection, reference(), code="600000.SH", owner=store
      )
  async with store.engine.connect() as connection:
    assert await connection.scalar(text("SELECT count(*) FROM instruments")) == 0
    assert await connection.scalar(text("SELECT count(*) FROM divid_factors")) == 1


@pytest.mark.parametrize("change", ["duplicate", "year"])
async def test_calendar_scope_rejected_before_writing(references, change):
  item = copy.deepcopy(reference())
  if change == "duplicate":
    item["holidays"] *= 2
  else:
    item["holidays"][0]["year"] = 2025
  with pytest.raises(ValueError):
    async with references.engine.begin() as connection:
      await import_reference_in_transaction(
        connection, item, code="600000.SH", owner=references
      )


@pytest.mark.parametrize(
  "change", ["instrument", "calendar", "factor", "receipt", "missing"]
)
async def test_reference_recheck_rejects_changes_without_repair(references, change):
  store = references
  item = reference()
  async with store.engine.begin() as connection:
    audit = await import_reference_in_transaction(
      connection, item, code="600000.SH", owner=store
    )
  async with store.engine.begin() as connection:
    await verify_imported_reference(connection, item, audit, code="600000.SH")
    if change == "instrument":
      await connection.execute(text("UPDATE instruments SET price_tick=2"))
    elif change == "calendar":
      await connection.execute(text("UPDATE holidays SET description='changed'"))
    elif change == "factor":
      await connection.execute(
        insert(DividFactorTable).values(
          stock_code="600000.SH",
          ex_date="20260601",
          time=datetime(2026, 6, 1),
          dr=2,
        )
      )
    elif change == "missing":
      await connection.execute(text("DELETE FROM instruments"))
    else:
      audit["reference_sha256"] = "0" * 64

  def reject_writes(connection, cursor, statement, parameters, context, executemany):
    assert statement.lstrip().split()[0].upper() not in {"INSERT", "UPDATE", "DELETE"}

  event.listen(store.engine.sync_engine, "before_cursor_execute", reject_writes)
  try:
    with pytest.raises(ValueError):
      async with store.engine.begin() as connection:
        await verify_imported_reference(connection, item, audit, code="600000.SH")
  finally:
    event.remove(store.engine.sync_engine, "before_cursor_execute", reject_writes)


async def test_permanent_readback_capacity_keeps_its_reason(references, monkeypatch):
  from quantx_infrastructure.services import development_history_import as importer
  from quantx_infrastructure.services.market_data_persistence_verification import (
    MarketDataPersistenceBlockedError,
  )

  from tests.infrastructure.test_development_delivery_manifest import REQUEST, manifest

  store = references
  async with store.engine.begin() as connection:
    await connection.execute(
      text("ALTER TABLE development_data_export ADD COLUMN IF NOT EXISTS error text")
    )
    await connection.execute(
      text("""
      INSERT INTO development_data_export(id,request,state,updated_at)
      VALUES ('delivery','{}','LOCAL_VERIFIED',clock_timestamp())
    """)
    )
  monkeypatch.setattr(importer, "AsyncSessionLocal", async_sessionmaker(store.engine))
  monkeypatch.setattr(
    importer,
    "verify_uploaded_bar_request",
    AsyncMock(
      side_effect=MarketDataPersistenceBlockedError(
        "DEPENDENCY_READBACK_CAPACITY_BLOCKED"
      ),
    ),
  )
  result = await importer._recheck_local_partition(
    "delivery",
    REQUEST,
    manifest(),
    importer.DevelopmentDownloadBudget(async_sessionmaker(store.engine), "delivery"),
  )
  assert result == {
    "id": "delivery",
    "status": "BLOCKED",
    "reason": "DEPENDENCY_READBACK_CAPACITY_BLOCKED",
  }


@pytest.mark.parametrize("fail_receipt", [False, True])
async def test_importer_publishes_reference_and_local_receipt_in_one_transaction(
  references,
  monkeypatch,
  tmp_path,
  fail_receipt,
):
  from quantx_infrastructure.services import data_exchange as catalog
  from quantx_infrastructure.services import development_history_import as importer
  from quantx_worker.prefector.flows.development_data_export_flow import (
    partition_records,
    publish,
  )

  from tests.infrastructure.test_development_download_budget import (
    install_budget_schema,
  )
  from tests.worker.test_development_data_export import bar

  store = references
  await install_budget_schema(store.engine)
  async with store.engine.begin() as connection:
    await connection.execute(
      text("ALTER TABLE development_data_export ADD COLUMN IF NOT EXISTS error text")
    )
  factory = async_sessionmaker(store.engine)
  monkeypatch.setattr(importer, "AsyncSessionLocal", factory)
  monkeypatch.setattr(catalog, "AsyncSessionLocal", factory)
  monkeypatch.setenv("ENV", "development")
  monkeypatch.setenv("QUANTX_DATA_EXPORT_ROOT", str(tmp_path))
  monkeypatch.setenv("QUANTX_MARKET_DATA_URL", "http://test")
  monkeypatch.setenv("QUANTX_MARKET_DATA_TOKEN", "test")
  request = HistoryPartitionRequest(
    instrument="600000.SH", period="1m", trading_date="2026-09-07"
  )
  identity = await catalog.submit(request)
  chunks = publish(partition_records([[bar()]], request))
  ref = reference()
  manifest = {
    "version": 1,
    "payload": request.agent_payload(),
    "chunks": chunks,
    "reference": ref,
    "source_request_id": "source-bars",
    "coverage": "SOURCE_VERIFIED",
    "rows": 1,
    "data_version": hashlib.sha256(
      json.dumps(
        {"chunks": chunks, "reference": ref},
        sort_keys=True,
      ).encode()
    ).hexdigest(),
  }
  transport = httpx.MockTransport(
    lambda req: httpx.Response(
      200,
      json={
        "id": identity,
        "state": "READY",
        "manifest": manifest,
      },
    )
  )
  client = httpx.AsyncClient
  monkeypatch.setattr(
    importer.httpx,
    "AsyncClient",
    lambda **kwargs: client(**kwargs, transport=transport),
  )

  # Bar persistence has its own verification suite. This test exercises the
  # real importer after that boundary through reference storage and receipt SQL.
  async def ingest(_transfer, _identity, *, progress):
    await progress.apply("manifest", sha256="a" * 64)
    await progress.apply("advance", phase="WRITE")
    await progress.apply(
      "advance", phase="READBACK", write_result={"records_verified": 1}
    )
    return {"records_verified": 1}

  monkeypatch.setattr(
    importer,
    "ingest_uploaded_bar_request",
    AsyncMock(side_effect=ingest),
  )

  def reject_receipt(connection, cursor, statement, parameters, context, executemany):
    if fail_receipt and "SET state='LOCAL_VERIFIED'" in statement:
      raise RuntimeError("receipt rejected")

  event.listen(store.engine.sync_engine, "before_cursor_execute", reject_receipt)
  try:
    if fail_receipt:
      pending = await importer._import_partition_owned(request)
      assert pending["status"] == "WAITING_LOCAL_INGESTION"
      assert pending["reason"] == "LOCAL_READBACK_UNAVAILABLE"
    else:
      receipt = await importer._import_partition_owned(request)
      assert (
        receipt["local_verification"]["reference_verification"][
          "instrument_records_verified"
        ]
        == 1
      )
  finally:
    event.remove(store.engine.sync_engine, "before_cursor_execute", reject_receipt)
  local = await catalog.get_export(identity)
  assert local["state"] == (
    "WAITING_LOCAL_INGESTION" if fail_receipt else "LOCAL_VERIFIED"
  )
  async with store.engine.connect() as connection:
    assert await connection.scalar(text("SELECT count(*) FROM instruments")) == (
      0 if fail_receipt else 1
    )
    assert await connection.scalar(text("SELECT count(*) FROM divid_factors")) == (
      1 if fail_receipt else 0
    )
    assert await connection.scalar(
      text(
        "SELECT count(*) FROM development_data_export WHERE state='REFERENCE_VERIFIED'"
      )
    ) == (0 if fail_receipt else 1)
  if not fail_receipt:
    from quantx_infrastructure.services.market_data_persistence_verification import (
      MarketDataPersistenceQueryError,
    )

    verify = AsyncMock(
      side_effect=MarketDataPersistenceQueryError("temporarily unavailable")
    )
    monkeypatch.setattr(importer, "verify_uploaded_bar_request", verify)
    pending = await importer._import_partition_owned(request)
    assert pending["status"] == "WAITING_LOCAL_PROOF"
    assert (await catalog.get_export(identity))["state"] == "WAITING_LOCAL_PROOF"
    assert await importer._import_partition_owned(request) == pending
    assert verify.await_count == 1
    async with store.engine.begin() as connection:
      await connection.execute(
        text(
          "UPDATE development_data_download_budget SET next_probe_at=clock_timestamp()"
        )
      )
    verify.side_effect = None
    verify.return_value = {"records_verified": 1}
    assert "local_verification" in await importer._import_partition_owned(request)
    assert importer.ingest_uploaded_bar_request.await_count == 1
    async with store.engine.begin() as connection:
      await connection.execute(
        insert(DividFactorTable).values(
          stock_code="600000.SH",
          ex_date="20260601",
          time=datetime(2026, 6, 1),
          dr=2,
        )
      )
    blocked = await importer._import_partition_owned(request)
    assert blocked["status"] == "BLOCKED"
    assert (await catalog.get_export(identity))["state"] == "BLOCKED"
    assert await importer._import_partition_owned(request) == blocked
    assert importer.ingest_uploaded_bar_request.await_count == 1
    async with store.engine.connect() as connection:
      assert await connection.scalar(text("SELECT count(*) FROM divid_factors")) == 1


async def test_delivery_transaction_rolls_back_if_owner_is_lost_before_commit(
  references, monkeypatch
):
  from quantx_infrastructure.services import development_history_import as importer

  store = references
  monkeypatch.setattr(importer, "AsyncSessionLocal", async_sessionmaker(store.engine))
  with pytest.raises(RuntimeError, match="lease was lost"):
    async with importer._delivery_transaction(store) as db:
      await db.execute(text("DELETE FROM divid_factors"))
      await db.execute(
        text(
          "UPDATE market_data_worker_lease SET expires_at=clock_timestamp()-INTERVAL '1 second'"
        )
      )
  async with store.engine.connect() as connection:
    assert await connection.scalar(text("SELECT count(*) FROM divid_factors")) == 1
    await store._guard_ingestion_owner(connection)
