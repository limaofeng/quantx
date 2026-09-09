"""Reference writes, repository commits and delivery receipts share one PG transaction."""
# ruff: noqa: F811

import copy
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
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
