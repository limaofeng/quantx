"""Exact statement readback against real session-local PostgreSQL tables."""

from datetime import date
from decimal import Decimal

import pytest
from quantx_infrastructure.models.financial import FinancialIncomeStatement
from quantx_infrastructure.services.financial_service import FinancialService
from quantx_infrastructure.services.financial_statement_verification import (
  verified_statement_upsert,
)
from sqlalchemy import text

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)


@pytest.fixture
async def statements(durable_store):  # noqa: F811
  store, _ = durable_store
  async with store.engine.begin() as connection:
    await connection.execute(
      text("""
      CREATE TEMP TABLE financial_income_statement (
        id serial PRIMARY KEY, stock_code varchar(20), report_date date,
        announce_date date, revenue numeric(20,4),
        created_at timestamp DEFAULT now(), updated_at timestamp DEFAULT now(),
        UNIQUE(stock_code,report_date)
      )
    """)
    )
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
