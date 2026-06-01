from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from gqlapi.resolvers.financial import FinancialResolver


def _record(**kwargs):
  defaults = {
    "stock_code": "600519.SH",
    "report_date": date(2025, 12, 31),
    "announce_date": date(2026, 4, 20),
  }
  defaults.update(kwargs)
  return SimpleNamespace(**defaults)


def _repo(records):
  class FakeRepo:
    def __init__(self, db):
      self.db = db

    async def find_by_stock_code(self, stock_code: str, limit: int = 20):
      assert stock_code == "600519.SH"
      assert limit == 20
      return records

  return FakeRepo


async def _fake_db():
  yield object()


@pytest.mark.asyncio
async def test_financial_statements_return_four_statement_tables(monkeypatch):
  import gqlapi.resolvers.financial as financial_module

  monkeypatch.setattr(financial_module, "get_async_db", _fake_db)
  monkeypatch.setattr(
    financial_module,
    "FinancialIncomeStatementRepository",
    _repo(
      [
        _record(
          revenue=Decimal("100.5"),
          revenue_inc=Decimal("90"),
          total_operating_cost=Decimal("50"),
          oper_profit=Decimal("30"),
          tot_profit=Decimal("28"),
          net_profit_incl_min_int_inc=Decimal("26"),
          net_profit_excl_min_int_inc=Decimal("25"),
          s_fa_eps_basic=Decimal("1.23"),
        )
      ]
    ),
  )
  monkeypatch.setattr(
    financial_module,
    "FinancialBalanceSheetRepository",
    _repo(
      [
        _record(
          total_assets=Decimal("500"),
          total_current_assets=Decimal("200"),
          total_non_current_assets=Decimal("300"),
          cash_equivalents=Decimal("80"),
          inventories=Decimal("20"),
          total_liabilities=Decimal("180"),
          total_current_liability=Decimal("100"),
          non_current_liabilities=Decimal("80"),
          total_equity=Decimal("320"),
          tot_shrhldr_eqy_excl_min_int=Decimal("310"),
        )
      ]
    ),
  )
  monkeypatch.setattr(
    financial_module,
    "FinancialCashFlowRepository",
    _repo(
      [
        _record(
          net_cash_flows_oper_act=Decimal("40"),
          net_cash_flows_inv_act=Decimal("-15"),
          net_cash_flows_fnc_act=Decimal("-8"),
          net_incr_cash_cash_equ=Decimal("17"),
          cash_cash_equ_end_period=Decimal("88"),
        )
      ]
    ),
  )
  monkeypatch.setattr(
    financial_module,
    "FinancialCapitalRepository",
    _repo(
      [
        _record(
          total_capital=Decimal("125"),
          circulating_capital=Decimal("110"),
          restrict_circulating_capital=Decimal("15"),
          free_float_capital=Decimal("100"),
        )
      ]
    ),
  )

  result = await FinancialResolver.get_financial_statements("600519.SH", limit=20)

  assert result.stock_code == "600519.SH"
  assert result.income[0].revenue == 100.5
  assert result.balance[0].total_assets == 500.0
  assert result.cash_flow[0].net_cash_flows_oper_act == 40.0
  assert result.capital[0].free_float_capital == 100.0


@pytest.mark.asyncio
async def test_financial_summary_handles_empty_data(monkeypatch):
  monkeypatch.setattr(
    FinancialResolver,
    "get_financial_statements",
    lambda stock_code, limit=1: _empty_statements(stock_code),
  )

  result = await FinancialResolver.get_financial_summary("600519.SH")

  assert result.stock_code == "600519.SH"
  assert result.latest_report_date is None
  assert result.revenue is None
  assert result.income_count == 0


@pytest.mark.asyncio
async def test_financial_reports_return_latest_income_rows(monkeypatch):
  import gqlapi.resolvers.financial as financial_module

  class FakeIncomeRepo:
    def __init__(self, db):
      self.db = db

    async def find_latest_reports(self, search=None, limit=50, offset=0):
      assert search == "茅台"
      assert limit == 10
      assert offset == 0
      return [
        (
          _record(
            revenue=Decimal("46480000000"),
            net_profit_excl_min_int_inc=Decimal("24070000000"),
            s_fa_eps_basic=Decimal("19.16"),
          ),
          "贵州茅台",
        )
      ]

    async def count_latest_report_stocks(self, search=None):
      assert search == "茅台"
      return 1

  monkeypatch.setattr(financial_module, "get_async_db", _fake_db)
  monkeypatch.setattr(
    financial_module,
    "FinancialIncomeStatementRepository",
    FakeIncomeRepo,
  )

  result = await FinancialResolver.get_financial_reports(
    search=" 茅台 ",
    limit=10,
  )

  assert result.total == 1
  assert result.items[0].stock_code == "600519.SH"
  assert result.items[0].stock_name == "贵州茅台"
  assert result.items[0].revenue == 46480000000.0


@pytest.mark.asyncio
async def test_financial_overview_returns_real_counts(monkeypatch):
  import gqlapi.resolvers.financial as financial_module

  class FakeIncomeRepo:
    def __init__(self, db):
      self.db = db

    async def get_overview(self):
      return {
        "report_count": 12,
        "instrument_count": 3,
        "latest_report_date": date(2025, 12, 31),
        "latest_announce_date": date(2026, 4, 30),
      }

  monkeypatch.setattr(financial_module, "get_async_db", _fake_db)
  monkeypatch.setattr(
    financial_module,
    "FinancialIncomeStatementRepository",
    FakeIncomeRepo,
  )

  result = await FinancialResolver.get_financial_overview()

  assert result.report_count == 12
  assert result.instrument_count == 3
  assert result.latest_report_date == date(2025, 12, 31)


async def _empty_statements(stock_code):
  from gqlapi.types import FinancialStatements

  return FinancialStatements(
    stock_code=stock_code,
    income=[],
    balance=[],
    cash_flow=[],
    capital=[],
  )
