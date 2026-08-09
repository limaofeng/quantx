from typing import Dict, List, Optional

from pypinyin import lazy_pinyin
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.sector import Sector
from quantx_infrastructure.models.sector_stock import SectorStock
from quantx_infrastructure.repositories.sector_repository import SectorRepository


class SectorService:
    def __init__(self):
        pass

    async def get_sector(self, sector_id: int) -> Optional[Sector]:
        async for db in get_async_db():
            repo = SectorRepository(db)
            return await repo.find_by_id(sector_id)

    async def get_sector_by_code(self, code: str) -> Optional[Sector]:
        async for db in get_async_db():
            repo = SectorRepository(db)
            return await repo.find_by_code(code)

    async def list_sectors(
        self, 
        classification: Optional[str] = None, 
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Sector]:
        from sqlalchemy.orm import selectinload
        async for db in get_async_db():
            query = select(Sector).options(selectinload(Sector.sector_stocks))
            
            if classification:
                query = query.filter(Sector.classification == classification)
            
            if search:
                query = query.filter(
                    (Sector.name.ilike(f"%{search}%")) | 
                    (Sector.code.ilike(f"%{search}%"))
                )
            
            # 始终按名称排序以保证分页稳定性
            query = query.order_by(Sector.name).limit(limit).offset(offset)
            
            result = await db.execute(query)
            return list(result.scalars().all())

    async def get_classification_stats(self) -> Dict[str, int]:
        """获取各分类下的板块数量统计"""
        from sqlalchemy import func
        async for db in get_async_db():
            query = select(Sector.classification, func.count(Sector.id)).group_by(Sector.classification)
            result = await db.execute(query)
            stats = {row[0]: row[1] for row in result}
            return stats

    async def get_total_count(self, classification: Optional[str] = None, search: Optional[str] = None) -> int:
        """获取符合条件的板块总数"""
        from sqlalchemy import func
        async for db in get_async_db():
            query = select(func.count(Sector.id))
            if classification:
                query = query.filter(Sector.classification == classification)
            if search:
                query = query.filter(
                    (Sector.name.ilike(f"%{search}%")) | 
                    (Sector.code.ilike(f"%{search}%"))
                )
            result = await db.execute(query)
            return result.scalar() or 0

    async def save_sector(
        self,
        name: str,
        code: str = None,
        description: str = None,
        classification: str = "SW",
        market: str = "CN",
        level: int = 1,
        parent_id: int = None,
        stock_list: List[str] = None,
    ) -> Sector:
        """保存或更新板块"""
        if code is None:
            code = "".join(lazy_pinyin(name))

        async for db in get_async_db():
            repo = SectorRepository(db)

            # 仅以 code 作为唯一标识进行检索
            existing = await repo.find_by_code(code)

            if existing:
                # 更新所有字段，允许重名
                existing.name = name
                existing.code = code
                existing.description = description
                existing.classification = classification
                existing.market = market
                existing.level = level
                existing.parent_id = parent_id
                sector = await repo.save(existing)
            else:
                # 真正的全新记录
                sector = Sector(
                    name=name,
                    code=code,
                    description=description,
                    classification=classification,
                    market=market,
                    level=level,
                    parent_id=parent_id
                )
                sector = await repo.save(sector)

            if stock_list is not None:
                await self._update_sector_stocks_in_db(db, sector.id, stock_list)
            
            return sector

    async def _update_sector_stocks_in_db(
        self, db: AsyncSession, sector_id: int, stock_codes: List[str]
    ) -> None:
        """更新板块的成分股列表"""
        # 删除不再在列表中的成分股
        await db.execute(
            delete(SectorStock).where(
                (SectorStock.sector_id == sector_id) & 
                (~SectorStock.stock_code.in_(stock_codes))
            )
        )

        # 获取现有的股票代码
        result = await db.execute(
            select(SectorStock.stock_code).where(SectorStock.sector_id == sector_id)
        )
        existing_codes = {row[0] for row in result}

        # 添加新股票
        for stock_code in stock_codes:
            if stock_code not in existing_codes:
                sector_stock = SectorStock(sector_id=sector_id, stock_code=stock_code)
                db.add(sector_stock)

        await db.commit()

    async def get_sectors_by_stock_code(self, stock_code: str) -> List[Sector]:
        """根据股票代码查询所属板块"""
        from sqlalchemy.orm import selectinload
        async for db in get_async_db():
            result = await db.execute(
                select(Sector).options(selectinload(Sector.sector_stocks))
                .join(SectorStock).where(SectorStock.stock_code == stock_code)
            )
            return list(result.scalars().all())

    async def delete_sectors_not_in_list(self, current_codes: List[str]) -> List[str]:
        """删除不在列表中的板块"""
        async for db in get_async_db():
            repo = SectorRepository(db)
            all_sectors = await repo.find_all(limit=10000)
            
            deleted_names = []
            for sector in all_sectors:
                if sector.code not in current_codes:
                    deleted_names.append(sector.name)
                    await repo.delete_by_id(sector.id)
            return deleted_names
