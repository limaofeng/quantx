"""财务指标快照仓储。"""

from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.relational_base import BaseRepository
from models.financial import FinancialBalanceSheet, FinancialIncomeStatement
from models.financial_metric_snapshot import FinancialMetricSnapshot


class FinancialMetricSnapshotRepository(BaseRepository[FinancialMetricSnapshot]):
  """财务指标快照仓储。"""

  model_class = FinancialMetricSnapshot

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def upsert(self, data: Dict[str, Any]) -> None:
    stmt = insert(FinancialMetricSnapshot).values(**data)
    stmt = stmt.on_conflict_do_update(
      index_elements=["code", "as_of_date", "report_date"],
      set_={
        key: value
        for key, value in data.items()
        if key not in {"code", "as_of_date", "report_date"}
      },
    )
    await self.db.execute(stmt)

  async def bulk_upsert(self, records: List[Dict[str, Any]]) -> int:
    if not records:
      return 0
    for record in records:
      await self.upsert(record)
    return len(records)

  async def delete_by_codes(self, codes: List[str]) -> int:
    if not codes:
      return 0
    result = await self.db.execute(
      delete(FinancialMetricSnapshot).where(
        FinancialMetricSnapshot.code.in_(codes)
      )
    )
    return int(result.rowcount or 0)

  async def find_distinct_income_codes(self) -> List[str]:
    result = await self.db.execute(
      select(FinancialIncomeStatement.stock_code)
      .distinct()
      .order_by(FinancialIncomeStatement.stock_code.asc())
    )
    return [row[0] for row in result.all()]

  async def find_income_rows(
    self,
    stock_codes: Optional[List[str]] = None,
  ) -> List[FinancialIncomeStatement]:
    stmt = select(FinancialIncomeStatement).order_by(
      FinancialIncomeStatement.stock_code.asc(),
      FinancialIncomeStatement.report_date.asc(),
    )
    if stock_codes:
      stmt = stmt.where(FinancialIncomeStatement.stock_code.in_(stock_codes))
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_balance_rows(
    self,
    stock_codes: Optional[List[str]] = None,
  ) -> List[FinancialBalanceSheet]:
    stmt = select(FinancialBalanceSheet).order_by(
      FinancialBalanceSheet.stock_code.asc(),
      FinancialBalanceSheet.report_date.asc(),
    )
    if stock_codes:
      stmt = stmt.where(FinancialBalanceSheet.stock_code.in_(stock_codes))
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def latest_as_of_date(self, code: str) -> Optional[date]:
    result = await self.db.execute(
      select(func.max(FinancialMetricSnapshot.as_of_date)).where(
        FinancialMetricSnapshot.code == code
      )
    )
    return result.scalar_one_or_none()
