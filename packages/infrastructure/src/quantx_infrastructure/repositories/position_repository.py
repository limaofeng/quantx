"""
持仓仓储层
处理持仓相关的数据访问
"""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models import Position
from quantx_infrastructure.models.enums import AccountType


class PositionRepository(BaseRepository[Position]):
  """持仓仓储实现"""

  model_class = Position

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_all(self, account_id: Optional[str] = None) -> List[Position]:
    """获取所有持仓"""
    stmt = select(Position)
    if account_id:
      stmt = stmt.filter(Position.account_id == account_id)
    result = await self.db.execute(stmt)
    return list(result.scalars().all())

  async def find_by_stock_code(
    self,
    stock_code: str,
    account_id: Optional[str] = None,
    account_type: Optional[AccountType] = None,
  ) -> Optional[Position]:
    """获取某只股票的持仓"""
    stmt = select(Position).filter(Position.stock_code == stock_code)
    if account_id:
      stmt = stmt.filter(Position.account_id == account_id)
    if account_type:
      stmt = stmt.filter(Position.account_type == account_type)
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
