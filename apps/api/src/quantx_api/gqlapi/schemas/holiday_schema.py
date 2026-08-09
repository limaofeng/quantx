"""
Holiday GraphQL Schema
节假日管理 GraphQL 接口
"""

from datetime import date
from typing import List, Optional

import strawberry
from quantx_infrastructure.core.utils import time_utils

from quantx_api.gqlapi.resolvers.holidays import HolidayResolver
from quantx_api.gqlapi.types.holiday_types import (
    HolidayInput,
    HolidayMutationResult,
    HolidayQueryResult,
)


@strawberry.type(description="节假日查询")
class HolidayQuery:
    @strawberry.field(description="获取指定市场年度的节假日列表")
    async def holidays(
        self,
        market: str = "CN",
        year: Optional[int] = None,
    ) -> HolidayQueryResult:
        if year is None:
            year = time_utils.today().year
        return await HolidayResolver.get_holidays(market, year)


@strawberry.type(description="节假日变更")
class HolidayMutation:
    @strawberry.mutation(description="添加节假日")
    async def add_holiday(
        self,
        market: str,
        holiday_date: date,
        description: Optional[str] = None,
    ) -> HolidayMutationResult:
        return await HolidayResolver.add_holiday(market, holiday_date, description)

    @strawberry.mutation(description="删除节假日")
    async def delete_holiday(self, id: int) -> HolidayMutationResult:
        return await HolidayResolver.delete_holiday(id)

    @strawberry.mutation(description="批量保存节假日（覆盖整年）")
    async def bulk_save_holidays(
        self,
        market: str,
        year: int,
        holidays: List[HolidayInput],
    ) -> HolidayMutationResult:
        holidays_data = [
            {"date": h.date, "description": h.description}
            for h in holidays
        ]
        return await HolidayResolver.bulk_save_holidays(market, year, holidays_data)
