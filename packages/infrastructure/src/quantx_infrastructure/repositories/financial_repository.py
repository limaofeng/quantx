"""
财务数据仓储层
处理财务报表相关的数据访问
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.financial import (
    FinancialBalanceSheet,
    FinancialCapital,
    FinancialCashFlow,
    FinancialIncomeStatement,
)
from quantx_infrastructure.models.instrument import Instrument


class FinancialBalanceSheetRepository(BaseRepository[FinancialBalanceSheet]):
    """资产负债表仓储"""

    model_class = FinancialBalanceSheet

    def __init__(self, db_session: AsyncSession):
        super().__init__(db_session)

    async def find_by_stock_code(
        self, stock_code: str, limit: int = 20
    ) -> List[FinancialBalanceSheet]:
        """根据股票代码获取资产负债表"""
        result = await self.db.execute(
            select(FinancialBalanceSheet)
            .filter(FinancialBalanceSheet.stock_code == stock_code)
            .order_by(FinancialBalanceSheet.report_date.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def upsert(self, data: Dict[str, Any]) -> None:
        """插入或更新资产负债表记录"""
        stmt = insert(FinancialBalanceSheet).values(**data)
        stmt = stmt.on_conflict_do_update(
            index_elements=["stock_code", "report_date"],
            set_={k: v for k, v in data.items() if k not in ["stock_code", "report_date"]}
        )
        await self.db.execute(stmt)


class FinancialIncomeStatementRepository(BaseRepository[FinancialIncomeStatement]):
    """利润表仓储"""

    model_class = FinancialIncomeStatement

    def __init__(self, db_session: AsyncSession):
        super().__init__(db_session)

    async def find_by_stock_code(
        self, stock_code: str, limit: int = 20
    ) -> List[FinancialIncomeStatement]:
        """根据股票代码获取利润表"""
        result = await self.db.execute(
            select(FinancialIncomeStatement)
            .filter(FinancialIncomeStatement.stock_code == stock_code)
            .order_by(FinancialIncomeStatement.report_date.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def find_latest_reports(
        self,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[tuple[FinancialIncomeStatement, Optional[str]]]:
        """获取每只股票最新利润表记录，可按代码或名称搜索"""
        latest = (
            select(
                FinancialIncomeStatement.stock_code.label("stock_code"),
                func.max(FinancialIncomeStatement.report_date).label("report_date"),
            )
            .group_by(FinancialIncomeStatement.stock_code)
            .subquery()
        )
        stmt = (
            select(FinancialIncomeStatement, Instrument.name)
            .join(
                latest,
                (FinancialIncomeStatement.stock_code == latest.c.stock_code)
                & (FinancialIncomeStatement.report_date == latest.c.report_date),
            )
            .outerjoin(Instrument, Instrument.id == FinancialIncomeStatement.stock_code)
            .order_by(
                FinancialIncomeStatement.report_date.desc(),
                FinancialIncomeStatement.stock_code.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        if search:
            pattern = f"%{search}%"
            stmt = stmt.filter(
                or_(
                    FinancialIncomeStatement.stock_code.ilike(pattern),
                    Instrument.name.ilike(pattern),
                )
            )
        result = await self.db.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def count_latest_report_stocks(self, search: Optional[str] = None) -> int:
        """统计有最新利润表记录的股票数"""
        latest = (
            select(
                FinancialIncomeStatement.stock_code.label("stock_code"),
                func.max(FinancialIncomeStatement.report_date).label("report_date"),
            )
            .group_by(FinancialIncomeStatement.stock_code)
            .subquery()
        )
        stmt = (
            select(func.count())
            .select_from(FinancialIncomeStatement)
            .join(
                latest,
                (FinancialIncomeStatement.stock_code == latest.c.stock_code)
                & (FinancialIncomeStatement.report_date == latest.c.report_date),
            )
            .outerjoin(Instrument, Instrument.id == FinancialIncomeStatement.stock_code)
        )
        if search:
            pattern = f"%{search}%"
            stmt = stmt.filter(
                or_(
                    FinancialIncomeStatement.stock_code.ilike(pattern),
                    Instrument.name.ilike(pattern),
                )
            )
        result = await self.db.execute(stmt)
        return int(result.scalar() or 0)

    async def get_overview(self) -> Dict[str, Any]:
        """获取财务数据整体统计"""
        report_count = await self.db.scalar(
            select(func.count()).select_from(FinancialIncomeStatement)
        )
        stock_count = await self.db.scalar(
            select(func.count(func.distinct(FinancialIncomeStatement.stock_code)))
        )
        latest_report_date = await self.db.scalar(
            select(func.max(FinancialIncomeStatement.report_date))
        )
        latest_announce_date = await self.db.scalar(
            select(func.max(FinancialIncomeStatement.announce_date))
        )
        return {
            "report_count": int(report_count or 0),
            "instrument_count": int(stock_count or 0),
            "latest_report_date": latest_report_date,
            "latest_announce_date": latest_announce_date,
        }

    async def upsert(self, data: Dict[str, Any]) -> None:
        """插入或更新利润表记录"""
        stmt = insert(FinancialIncomeStatement).values(**data)
        stmt = stmt.on_conflict_do_update(
            index_elements=["stock_code", "report_date"],
            set_={k: v for k, v in data.items() if k not in ["stock_code", "report_date"]}
        )
        await self.db.execute(stmt)


class FinancialCashFlowRepository(BaseRepository[FinancialCashFlow]):
    """现金流量表仓储"""

    model_class = FinancialCashFlow

    def __init__(self, db_session: AsyncSession):
        super().__init__(db_session)

    async def find_by_stock_code(
        self, stock_code: str, limit: int = 20
    ) -> List[FinancialCashFlow]:
        """根据股票代码获取现金流量表"""
        result = await self.db.execute(
            select(FinancialCashFlow)
            .filter(FinancialCashFlow.stock_code == stock_code)
            .order_by(FinancialCashFlow.report_date.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def upsert(self, data: Dict[str, Any]) -> None:
        """插入或更新现金流量表记录"""
        stmt = insert(FinancialCashFlow).values(**data)
        stmt = stmt.on_conflict_do_update(
            index_elements=["stock_code", "report_date"],
            set_={k: v for k, v in data.items() if k not in ["stock_code", "report_date"]}
        )
        await self.db.execute(stmt)


class FinancialCapitalRepository(BaseRepository[FinancialCapital]):
    """股本结构仓储"""

    model_class = FinancialCapital

    def __init__(self, db_session: AsyncSession):
        super().__init__(db_session)

    async def find_by_stock_code(
        self, stock_code: str, limit: int = 20
    ) -> List[FinancialCapital]:
        """根据股票代码获取股本结构"""
        result = await self.db.execute(
            select(FinancialCapital)
            .filter(FinancialCapital.stock_code == stock_code)
            .order_by(FinancialCapital.report_date.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def upsert(self, data: Dict[str, Any]) -> None:
        """插入或更新股本结构记录"""
        stmt = insert(FinancialCapital).values(**data)
        stmt = stmt.on_conflict_do_update(
            index_elements=["stock_code", "report_date"],
            set_={k: v for k, v in data.items() if k not in ["stock_code", "report_date"]}
        )
        await self.db.execute(stmt)
