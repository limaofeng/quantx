"""Repository for global T-trade monitor configurations."""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig


class TTradeGlobalConfigRepository(BaseRepository[TTradeGlobalConfig]):
  model_class = TTradeGlobalConfig

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_by_account(
    self, account_id: str
  ) -> Optional[TTradeGlobalConfig]:
    result = await self.db.execute(
      select(TTradeGlobalConfig).where(
        TTradeGlobalConfig.account_id == account_id
      )
    )
    return result.scalar_one_or_none()

  async def find_all_configs(self) -> List[TTradeGlobalConfig]:
    result = await self.db.execute(
      select(TTradeGlobalConfig).order_by(TTradeGlobalConfig.created_at.asc())
    )
    return list(result.scalars().all())

  async def save(self, config: TTradeGlobalConfig) -> TTradeGlobalConfig:
    self.db.add(config)
    await self.db.commit()
    await self.db.refresh(config)
    return config
