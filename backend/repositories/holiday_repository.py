"""
节假日仓储层
处理节假日相关的数据访问
"""

from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.relational_base import BaseRepository
from models.holidays import Holiday


class HolidayRepository(BaseRepository[Holiday]):
  """节假日仓储实现"""

  model_class = Holiday

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_all_by_market_and_year(self, market: str, year: int) -> List[Holiday]:
    """根据市场和年度获取节假日列表"""
    result = await self.db.execute(
      select(Holiday).filter(Holiday.market == market, Holiday.year == year)
    )
    return list(result.scalars().all())

  async def exists_by_market_and_date(self, market: str, date) -> bool:
    """检查指定市场和日期的节假日是否存在"""
    result = await self.db.execute(
      select(Holiday).filter(Holiday.market == market, Holiday.date == date)
    )
    return result.scalar_one_or_none() is not None

  async def delete_by_market_and_year(self, market: str, year: int) -> int:
    """删除指定市场和年度的所有节假日，返回删除的记录数"""
    from sqlalchemy import delete

    result = await self.db.execute(
      delete(Holiday).filter(Holiday.market == market, Holiday.year == year)
    )
    await self.db.commit()
    return result.rowcount
