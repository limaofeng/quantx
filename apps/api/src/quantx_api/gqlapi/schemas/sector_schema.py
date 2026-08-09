from typing import List, Optional

import strawberry

from ..resolvers.sectors import SectorResolver
from ..types.sector import Sector, SectorQueryResult, SectorStats


@strawberry.type(description="板块数据查询")
class SectorQuery:
    @strawberry.field(description="获取所有板块 (带分页与搜索)")
    async def sectors(
        self, 
        classification: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> SectorQueryResult:
        return await SectorResolver.get_sectors_paginated(classification, search, limit, offset)

    @strawberry.field(description="获取板块统计信息")
    async def sector_stats(self) -> List[SectorStats]:
        return await SectorResolver.get_sector_stats()

    @strawberry.field(description="获取根板块")
    async def root_sectors(self, classification: Optional[str] = None) -> List[Sector]:
        return await SectorResolver.get_root_sectors(classification)

    @strawberry.field(description="根据代码获取板块")
    async def sector(self, code: str) -> Optional[Sector]:
        return await SectorResolver.get_sector(code)

    @strawberry.field(description="获取股票所属板块")
    async def stock_sectors(self, stock_code: str) -> List[Sector]:
        return await SectorResolver.get_sectors_by_stock(stock_code)
