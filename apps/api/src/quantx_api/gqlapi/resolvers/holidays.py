"""
Holiday Resolver
节假日数据解析器
"""

from datetime import date
from typing import List, Optional

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.holidays import Holiday as HolidayModel
from quantx_infrastructure.repositories.holiday_repository import HolidayRepository

from quantx_api.gqlapi.types.holiday_types import (
    Holiday,
    HolidayMutationResult,
    HolidayQueryResult,
)


class HolidayResolver:
    """节假日数据解析器"""

    @staticmethod
    async def get_holidays(market: str, year: int) -> HolidayQueryResult:
        """获取指定市场和年度的节假日列表"""
        async for db in get_async_db():
            repo = HolidayRepository(db)
            holidays = await repo.find_all_by_market_and_year(market, year)
            return HolidayQueryResult(
                items=[Holiday.from_model(h) for h in holidays],
                total=len(holidays),
            )

    @staticmethod
    async def add_holiday(
        market: str, holiday_date: date, description: Optional[str] = None
    ) -> HolidayMutationResult:
        """添加单个节假日"""
        async for db in get_async_db():
            repo = HolidayRepository(db)
            
            # 检查是否已存在
            exists = await repo.exists_by_market_and_date(market, holiday_date)
            if exists:
                return HolidayMutationResult(
                    success=False,
                    message=f"节假日 {holiday_date} 已存在",
                    holiday=None,
                )
            
            # 创建新记录
            holiday = HolidayModel(
                market=market,
                year=holiday_date.year,
                date=holiday_date,
                description=description or "",
            )
            created = await repo.create(holiday)
            await db.commit()
            
            return HolidayMutationResult(
                success=True,
                message=f"节假日 {holiday_date} 添加成功",
                holiday=Holiday.from_model(created),
            )

    @staticmethod
    async def delete_holiday(holiday_id: int) -> HolidayMutationResult:
        """删除节假日"""
        async for db in get_async_db():
            repo = HolidayRepository(db)
            
            # 查找节假日
            holiday = await repo.find_by_id(holiday_id)
            if not holiday:
                return HolidayMutationResult(
                    success=False,
                    message=f"节假日 ID {holiday_id} 不存在",
                    holiday=None,
                )
            
            # 删除记录
            await db.delete(holiday)
            await db.commit()
            
            return HolidayMutationResult(
                success=True,
                message=f"节假日 {holiday.date} 删除成功",
                holiday=None,
            )

    @staticmethod
    async def bulk_save_holidays(
        market: str, year: int, holidays_data: List[dict]
    ) -> HolidayMutationResult:
        """批量保存节假日（覆盖整年）"""
        async for db in get_async_db():
            repo = HolidayRepository(db)
            
            # 先删除该年度的所有节假日
            await repo.delete_by_market_and_year(market, year)
            
            # 批量创建新记录
            created_count = 0
            for data in holidays_data:
                holiday = HolidayModel(
                    market=market,
                    year=year,
                    date=data["date"],
                    description=data.get("description", ""),
                )
                await repo.create(holiday)
                created_count += 1
            
            await db.commit()
            
            return HolidayMutationResult(
                success=True,
                message=f"成功保存 {created_count} 个节假日",
                holiday=None,
            )
