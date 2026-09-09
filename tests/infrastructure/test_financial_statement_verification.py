"""Exact statement readback against real session-local PostgreSQL tables."""

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.models.financial import FinancialIncomeStatement
from quantx_infrastructure.services.financial_service import FinancialService
from quantx_infrastructure.services.financial_statement_verification import (
  read_statement_proof,
  verified_statement_upsert,
)
from sqlalchemy import MetaData, text
from sqlalchemy.schema import CreateTable

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)


@pytest.fixture
async def statements(durable_store):  # noqa: F811
  store, _ = durable_store
  table = FinancialIncomeStatement.__table__.to_metadata(MetaData())
  table._prefixes = ["TEMPORARY"]
  async with store.engine.begin() as connection:
    await connection.execute(CreateTable(table))
  return store


def rows():
  return [
    {
      "stock_code": "000001.SZ",
      "report_date": date(2026, 3, 31),
      "announce_date": None,
      "revenue": 1.23455,
    }
  ]


async def verify(db, values, upsert=FinancialService._bulk_upsert, chunk_size=250):
  return await verified_statement_upsert(
    db, FinancialIncomeStatement, values, upsert=upsert, chunk_size=chunk_size
  )


async def test_decimal_rounding_and_null_retention_are_verified(statements):
  async with statements.engine.begin() as connection:
    audit = await verify(connection, rows())
    assert audit["rows_verified"] == 1
    assert await connection.scalar(
      text("SELECT revenue FROM financial_income_statement")
    ) == Decimal("1.2346")
    values = rows()
    values[0]["revenue"] = None
    retained = await verify(connection, values)
    assert retained == audit


@pytest.mark.parametrize("value", [-0.0, -0.00001, 1.23455])
async def test_persisted_decimal_representation_keeps_original_proof(statements, value):
  values = [{**rows()[0], "revenue": value}]
  async with statements.engine.begin() as connection:
    audit = await verify(connection, values)
    actual = await read_statement_proof(
      connection, FinancialIncomeStatement, values, chunk_size=250
    )
    assert actual == audit


@pytest.mark.parametrize("failure", ["skip", "change"])
async def test_count_only_success_cannot_publish_wrong_content(statements, failure):
  async def faulty(db, model, values):
    if failure == "change":
      await FinancialService._bulk_upsert(
        db, model, [{**row, "revenue": 999} for row in values]
      )
    return len(values)

  with pytest.raises(RuntimeError, match="content mismatch"):
    async with statements.engine.begin() as connection:
      await verify(connection, rows(), upsert=faulty)
  async with statements.engine.connect() as connection:
    assert (
      await connection.scalar(text("SELECT count(*) FROM financial_income_statement"))
      == 0
    )


async def test_duplicate_source_keys_rejected_before_writing(statements):
  async with statements.engine.begin() as connection:
    with pytest.raises(ValueError, match="duplicate"):
      await verify(connection, rows() * 2)
    assert (
      await connection.scalar(text("SELECT count(*) FROM financial_income_statement"))
      == 0
    )


async def test_readback_is_bounded_to_requested_keys(statements):
  values = [{**rows()[0], "stock_code": f"{i:06}.SZ"} for i in range(5)]
  async with statements.engine.begin() as connection:
    await FinancialService._bulk_upsert(
      connection, FinancialIncomeStatement, [{**rows()[0], "stock_code": "600000.SH"}]
    )
    audit = await verify(connection, values, chunk_size=2)
    assert audit["rows_verified"] == 5
    assert (
      await connection.scalar(text("SELECT count(*) FROM financial_income_statement"))
      == 6
    )


@pytest.mark.parametrize("change", [None, "value", "delete"])
async def test_frozen_financial_recovery_checks_committed_content(
  statements, tmp_path, monkeypatch, change
):
  from quantx_infrastructure.services import financial_service as service_module
  from quantx_infrastructure.services.market_data_ingestion_progress import (
    IngestionProgress,
  )
  from quantx_infrastructure.services.market_data_transfer_ingestion import (
    ingest_uploaded_market_data_request,
  )

  from tests.infrastructure.test_market_data_transfer_ingestion import _write_chunk

  store = statements
  payload = {
    "operation": "financial_data",
    "record_format": "financial-row-v1",
    "stock_list": ["000001.SZ"],
    "table_list": ["Income"],
    "start_time": "20260101",
    "end_time": "20260910",
  }
  records = [
    {
      "record_type": "financial_row",
      "schema_version": 1,
      "code": "000001.SZ",
      "table": "Income",
      "row": {"m_timetag": "20260331", "m_anntime": "20260422", "revenue": 1.23455},
    },
    {
      "record_type": "financial_summary",
      "schema_version": 1,
      "code": "000001.SZ",
      "table_counts": {"Income": 1},
    },
  ]
  store.manifest = [_write_chunk(tmp_path, records)]
  async with store.engine.begin() as connection:
    await connection.execute(
      text("UPDATE market_data_request SET request_payload=CAST(:payload AS json)"),
      {"payload": json.dumps(payload)},
    )
  metric = SimpleNamespace(
    rebuild_for_codes=AsyncMock(return_value={"codes": 1, "records": 0})
  )
  monkeypatch.setattr(
    service_module, "FinancialMetricSnapshotService", lambda **kwargs: metric
  )
  token = await store.claim_market_data_request("request-1")
  progress = IngestionProgress(store, "request-1", token)
  await progress.apply("begin")
  audit = await ingest_uploaded_market_data_request(
    store, "request-1", progress=progress
  )
  await store.release_market_data_request_claim(
    "request-1", claim_token=token, error="completion lost"
  )
  if change:
    async with store.engine.begin() as connection:
      await connection.execute(
        text(
          "DELETE FROM financial_income_statement"
          if change == "delete"
          else "UPDATE financial_income_statement SET revenue=999"
        )
      )
  token = await store.claim_market_data_request("request-1")
  recovery = IngestionProgress(store, "request-1", token)
  await recovery.apply("begin")

  async def no_write(*args, **kwargs):
    pytest.fail("recovery must not overwrite the persisted financial version")

  monkeypatch.setattr(store, "persist_market_data_reference", no_write)
  if change:
    with pytest.raises(RuntimeError, match="persisted content changed"):
      await ingest_uploaded_market_data_request(store, "request-1", progress=recovery)
    assert (await store.market_data_request("request-1"))["status"] != "COMPLETED"
  else:
    assert (
      await ingest_uploaded_market_data_request(store, "request-1", progress=recovery)
      == audit
    )
    await store.finish_market_data_request(
      "request-1", status="COMPLETED", ingestion_result=audit, claim_token=token
    )
  assert metric.rebuild_for_codes.await_count == 1
