"""
持仓仓储层
处理持仓相关的数据访问
"""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.relational_base import BaseRepository
from models import Position


class PositionRepository(BaseRepository[Position]):
  """持仓仓储实现"""

  model_class = Position

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_all(self) -> List[Position]:
    """获取所有持仓"""
    result = await self.db.execute(select(Position))
    return list(result.scalars().all())

  async def find_by_stock_code(self, stock_code: str) -> Optional[Position]:
    """获取某只股票的持仓"""
    stmt = select(Position).filter(Position.stock_code == stock_code)
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()

  async def delete(self, position_id: int) -> bool:
    """删除持仓"""
    position = await self.find_by_id(position_id)
    if position:
      await self.db.delete(position)
      await self.db.commit()
      return True
    return False
