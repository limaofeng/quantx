"""
账户仓储层
处理账户相关的数据访问
"""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.relational_base import BaseRepository
from models import Account
from models.enums import AccountType


class AccountRepository(BaseRepository[Account]):
  """账户仓储实现"""

  model_class = Account

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_all(self) -> List[Account]:
    """获取所有账户"""
    result = await self.db.execute(select(Account))
    return list(result.scalars().all())

  async def find_default(self) -> Optional[Account]:
    """Return the most recently refreshed account without inventing an ID."""
    result = await self.db.execute(
      select(Account).order_by(Account.updated_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()

  async def find_by_account_id(
    self, account_id: str, account_type: AccountType = AccountType.STOCK
  ) -> Optional[Account]:
    """根据资金账号获取账户"""
    stmt = select(Account).filter(
      Account.account_id == account_id, Account.account_type == account_type
    )
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()
