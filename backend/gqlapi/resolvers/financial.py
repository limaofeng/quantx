from datetime import date
from typing import Optional

from database.connection import get_async_db
from repositories.financial_repository import (
  FinancialBalanceSheetRepository,
  FinancialCapitalRepository,
  FinancialCashFlowRepository,
  FinancialIncomeStatementRepository,
)

from ..types import (
  FinancialBalanceSheetData,
  FinancialCapitalData,
  FinancialCashFlowData,
  FinancialIncomeStatementData,
  FinancialOverview,
  FinancialReportPage,
  FinancialReportSummary,
  FinancialStatements,
  FinancialSummary,
)


class FinancialResolver:
  """Financial statement GraphQL resolver."""

  @staticmethod
  async def get_financial_overview() -> FinancialOverview:
    async for db in get_async_db():
      income_repo = FinancialIncomeStatementRepository(db)
      overview = await income_repo.get_overview()
      return FinancialOverview(**overview)

    return FinancialOverview(
      report_count=0,
      instrument_count=0,
      latest_report_date=None,
      latest_announce_date=None,
    )

  @staticmethod
  async def get_financial_reports(
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
  ) -> FinancialReportPage:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    normalized_search = search.strip() if search else None

    async for db in get_async_db():
      income_repo = FinancialIncomeStatementRepository(db)
      rows = await income_repo.find_latest_reports(
        search=normalized_search,
        limit=limit,
        offset=offset,
      )
      total = await income_repo.count_latest_report_stocks(
        search=normalized_search,
      )
      return FinancialReportPage(
        total=total,
        items=[
          FinancialReportSummary.from_model(model, stock_name)
          for model, stock_name in rows
        ],
      )

    return FinancialReportPage(items=[], total=0)

  @staticmethod
  async def get_financial_statements(
    stock_code: str, limit: int = 20
  ) -> FinancialStatements:
    limit = max(1, min(limit, 80))

    async for db in get_async_db():
      income_repo = FinancialIncomeStatementRepository(db)
      balance_repo = FinancialBalanceSheetRepository(db)
      cash_flow_repo = FinancialCashFlowRepository(db)
      capital_repo = FinancialCapitalRepository(db)

      income = await income_repo.find_by_stock_code(stock_code, limit=limit)
      balance = await balance_repo.find_by_stock_code(stock_code, limit=limit)
      cash_flow = await cash_flow_repo.find_by_stock_code(stock_code, limit=limit)
      capital = await capital_repo.find_by_stock_code(stock_code, limit=limit)

      return FinancialStatements(
        stock_code=stock_code,
        income=[FinancialIncomeStatementData.from_model(item) for item in income],
        balance=[FinancialBalanceSheetData.from_model(item) for item in balance],
        cash_flow=[FinancialCashFlowData.from_model(item) for item in cash_flow],
        capital=[FinancialCapitalData.from_model(item) for item in capital],
      )

    return FinancialStatements(
      stock_code=stock_code,
      income=[],
      balance=[],
      cash_flow=[],
      capital=[],
    )

  @staticmethod
  async def get_financial_summary(stock_code: str) -> FinancialSummary:
    statements = await FinancialResolver.get_financial_statements(
      stock_code=stock_code,
      limit=1,
    )
    income = statements.income[0] if statements.income else None
    balance = statements.balance[0] if statements.balance else None
    cash_flow = statements.cash_flow[0] if statements.cash_flow else None
    capital = statements.capital[0] if statements.capital else None

    latest_report_date = _max_date(
      income.report_date if income else None,
      balance.report_date if balance else None,
      cash_flow.report_date if cash_flow else None,
      capital.report_date if capital else None,
    )
    latest_announce_date = _max_date(
      income.announce_date if income else None,
      balance.announce_date if balance else None,
      cash_flow.announce_date if cash_flow else None,
      capital.announce_date if capital else None,
    )

    return FinancialSummary(
      stock_code=stock_code,
      latest_report_date=latest_report_date,
      latest_announce_date=latest_announce_date,
      revenue=income.revenue if income else None,
      net_profit_excl_min_int_inc=income.net_profit_excl_min_int_inc
      if income
      else None,
      eps_basic=income.eps_basic if income else None,
      total_assets=balance.total_assets if balance else None,
      total_liabilities=balance.total_liabilities if balance else None,
      total_equity=balance.total_equity if balance else None,
      operating_cash_flow=cash_flow.net_cash_flows_oper_act
      if cash_flow
      else None,
      cash_balance=cash_flow.cash_cash_equ_end_period if cash_flow else None,
      total_capital=capital.total_capital if capital else None,
      circulating_capital=capital.circulating_capital if capital else None,
      income_count=len(statements.income),
      balance_count=len(statements.balance),
      cash_flow_count=len(statements.cash_flow),
      capital_count=len(statements.capital),
    )


def _max_date(*values: Optional[date]) -> Optional[date]:
  dates = [value for value in values if value is not None]
  return max(dates) if dates else None
