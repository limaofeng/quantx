from typing import List, Optional

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.sector import Sector as SectorModel
from quantx_infrastructure.services.sector_service import SectorService
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..types import Sector as SectorGQL
from ..types.sector import SectorQueryResult, SectorStats

sector_service = SectorService()

class SectorResolver:
    @staticmethod
    async def get_sectors_paginated(
        classification: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> SectorQueryResult:
        """获取所有板块列表（支持分页与搜索）"""
        # Fetch data from service
        models = await sector_service.list_sectors(classification, search, limit, offset)
        total = await sector_service.get_total_count(classification, search)
        
        # Convert to GQL types
        items = [SectorGQL.from_model(m) for m in models]
        return SectorQueryResult(items=items, total=total)

    @staticmethod
    async def get_sector_stats() -> List[SectorStats]:
        """获取板块统计信息"""
        stats_dict = await sector_service.get_classification_stats()
        return [SectorStats(classification=k, count=v) for k, v in stats_dict.items()]

    @staticmethod
    async def get_root_sectors(classification: Optional[str] = None) -> List[SectorGQL]:
        """获取所有根板块"""
        async for db in get_async_db():
            query = select(SectorModel).filter(SectorModel.parent_id.is_(None)).options(
                selectinload(SectorModel.children),
                selectinload(SectorModel.sector_stocks)
            )
            if classification:
                query = query.filter(SectorModel.classification == classification)
            
            result = await db.execute(query)
            sectors = result.scalars().unique().all()
            return [SectorGQL.from_model(s) for s in sectors]

    @staticmethod
    async def get_sector(code: str) -> Optional[SectorGQL]:
        """根据代码获取板块"""
        async for db in get_async_db():
            query = select(SectorModel).filter(SectorModel.code == code).options(
                selectinload(SectorModel.children),
                selectinload(SectorModel.sector_stocks)
            )
            result = await db.execute(query)
            sector = result.scalar_one_or_none()
            return SectorGQL.from_model(sector) if sector else None

    @staticmethod
    async def get_sectors_by_stock(stock_code: str) -> List[SectorGQL]:
        """获取股票所属板块"""
        sectors = await sector_service.get_sectors_by_stock_code(stock_code)
        return [SectorGQL.from_model(s) for s in sectors]
