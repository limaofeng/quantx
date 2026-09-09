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
      text("ALTER TABLE development_data_export ADD COLUMN error text")
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
  monkeypatch.setattr(
    importer,
    "ingest_uploaded_bar_request",
    AsyncMock(return_value={"records_verified": 1}),
  )

  def reject_receipt(connection, cursor, statement, parameters, context, executemany):
    if fail_receipt and "SET state='LOCAL_VERIFIED'" in statement:
      raise RuntimeError("receipt rejected")

  event.listen(store.engine.sync_engine, "before_cursor_execute", reject_receipt)
  try:
    if fail_receipt:
      with pytest.raises(RuntimeError, match="receipt rejected"):
        await importer._import_partition_owned(request)
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
  assert local["state"] == ("QUEUED" if fail_receipt else "LOCAL_VERIFIED")
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
