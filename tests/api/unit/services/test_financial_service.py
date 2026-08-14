from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from quantx_infrastructure.models.financial import FinancialIncomeStatement
from quantx_infrastructure.services.financial_service import FinancialService
from sqlalchemy.dialects import postgresql


def test_financial_date_parser_accepts_wire_and_legacy_dates() -> None:
  assert FinancialService._parse_date("20260422") == date(2026, 4, 22)
  assert FinancialService._parse_date("2026-04-22T00:00:00") == date(2026, 4, 22)
  assert FinancialService._parse_date(1_776_816_000_000) == date(2026, 4, 22)
  assert FinancialService._parse_date("not-a-date") is None


@pytest.mark.asyncio
async def test_financial_upsert_does_not_replace_existing_values_with_null() -> None:
  db = SimpleNamespace(execute=AsyncMock())

  await FinancialService._bulk_upsert(
    db,
    FinancialIncomeStatement,
    [
      {
        "stock_code": "688552.SH",
        "report_date": date(2026, 3, 31),
        "announce_date": None,
        "revenue": None,
      }
    ],
  )

  statement = db.execute.await_args.args[0]
  sql = str(statement.compile(dialect=postgresql.dialect()))
  assert "coalesce(excluded.revenue, financial_income_statement.revenue)" in sql


@pytest.mark.asyncio
async def test_financial_upsert_chunks_large_statement_batches() -> None:
  db = SimpleNamespace(execute=AsyncMock())
  records = [
    {
      "stock_code": f"{index:06d}.SZ",
      "report_date": date(2026, 3, 31),
      "revenue": index,
    }
    for index in range(501)
  ]

  saved = await FinancialService._bulk_upsert(
    db,
    FinancialIncomeStatement,
    records,
  )

  assert saved == 501
  assert db.execute.await_count == 3


@pytest.mark.asyncio
async def test_financial_batch_returns_audit_and_commits_metric_rebuild(
  monkeypatch,
) -> None:
  import quantx_infrastructure.services.financial_service as financial_module

  db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
  service = FinancialService(db_session=db)
  service._bulk_upsert = AsyncMock(side_effect=lambda _db, _model, rows: len(rows))

  metric = SimpleNamespace(
    rebuild_for_codes=AsyncMock(return_value={"codes": 1, "records": 2})
  )
  monkeypatch.setattr(
    financial_module,
    "FinancialMetricSnapshotService",
    lambda db_session: metric,
  )
  data = {
    "688552.SH": {
      "Income": pd.DataFrame(
        [
          {
            "m_timetag": "20260331",
            "m_anntime": "20260422",
            "revenue": 100,
            "net_profit_excl_min_int_inc": -4,
          }
        ]
      ),
      "Balance": pd.DataFrame(
        [
          {
            "m_timetag": "20260331",
            "m_anntime": "20260422",
            "tot_assets": 200,
          }
        ]
      ),
    }
  }

  result = await service.save_batch_financial_data_with_audit(data)

  assert result == {
    "rows_received": 2,
    "rows_upserted": 2,
    "rows_rejected": 0,
    "metric_codes_rebuilt": 1,
    "metric_rows_rebuilt": 2,
    "statement_rows_by_code": {"688552.SH": 2},
    "metric_rows_by_code": {},
  }
  metric.rebuild_for_codes.assert_awaited_once_with(
    ["688552.SH"],
    commit=False,
  )
  db.commit.assert_awaited_once()
  db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_financial_batch_rolls_back_when_metric_rebuild_fails(
  monkeypatch,
) -> None:
  import quantx_infrastructure.services.financial_service as financial_module

  db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
  service = FinancialService(db_session=db)
  service._bulk_upsert = AsyncMock(side_effect=lambda _db, _model, rows: len(rows))
  metric = SimpleNamespace(
    rebuild_for_codes=AsyncMock(side_effect=RuntimeError("metric failure"))
  )
  monkeypatch.setattr(
    financial_module,
    "FinancialMetricSnapshotService",
    lambda db_session: metric,
  )

  with pytest.raises(RuntimeError, match="metric failure"):
    await service.save_batch_financial_data_with_audit(
      {
        "688552.SH": {
          "Income": pd.DataFrame([{"m_timetag": "20260331", "m_anntime": "20260422"}])
        }
      }
    )

  db.rollback.assert_awaited_once()
  db.commit.assert_not_awaited()
