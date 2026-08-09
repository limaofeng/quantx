"""
节假日服务层
提供节假日相关的业务逻辑
"""

from datetime import date
from typing import List

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.holidays import Holiday
from quantx_infrastructure.repositories.holiday_repository import HolidayRepository


class HolidayService:
  """节假日服务"""

  def __init__(self):
    pass

  async def get_holidays(self, market: str, year: int) -> List[Holiday]:
    """根据市场和年度获取节假日列表"""
    async for db in get_async_db():
      repo = HolidayRepository(db)
      return await repo.find_all_by_market_and_year(market, year)

  async def is_holiday(self, market: str, date: date) -> bool:
    """检查指定市场和日期是否为节假日"""
    async for db in get_async_db():
      repo = HolidayRepository(db)
      return await repo.exists_by_market_and_date(market, date)

  async def bulk_save_holidays(
    self, market: str, year: int, holidays_data: List[dict]
  ) -> List[Holiday]:
    """批量保存节假日（先删除该市场该年度的所有节假日，再批量添加新的）"""
    async for db in get_async_db():
      repo = HolidayRepository(db)

      # 先删除该市场该年度的所有节假日
      await repo.delete_by_market_and_year(market, year)

      # 批量创建新的节假日
      holidays = []
      for data in holidays_data:
        # 确保数据包含必要的字段
        holiday_data = Holiday(
          market=market,
          year=year,
          date=data["date"],
          description=data["description"],
        )
        holiday = await repo.create(holiday_data)
        holidays.append(holiday)

      return holidays
