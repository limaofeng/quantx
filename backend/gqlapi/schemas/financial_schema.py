import strawberry
from typing import Optional

from ..resolvers.financial import FinancialResolver
from ..types import (
  FinancialOverview,
  FinancialReportPage,
  FinancialStatements,
  FinancialSummary,
)


@strawberry.type(description="财务数据相关查询")
class FinancialQuery:
  @strawberry.field(description="获取财务数据页整体统计")
  async def financial_overview(self) -> FinancialOverview:
    return await FinancialResolver.get_financial_overview()

  @strawberry.field(description="获取最新财报列表")
  async def financial_reports(
    self,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
  ) -> FinancialReportPage:
    return await FinancialResolver.get_financial_reports(search, limit, offset)

  @strawberry.field(description="获取单票最新财务摘要")
  async def financial_summary(self, stock_code: str) -> FinancialSummary:
    return await FinancialResolver.get_financial_summary(stock_code)

  @strawberry.field(description="获取单票财务四表")
  async def financial_statements(
    self,
    stock_code: str,
    limit: int = 20,
  ) -> FinancialStatements:
    return await FinancialResolver.get_financial_statements(stock_code, limit)
