"""
板块仓储层
处理板块相关的数据访问
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.sector import Sector


class SectorRepository(BaseRepository[Sector]):
  """板块仓储实现"""

  model_class = Sector

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_by_code(self, code: str) -> Optional[Sector]:
    """根据代码获取板块"""
    result = await self.db.execute(select(Sector).filter(Sector.code == code))
    return result.scalar_one_or_none()
