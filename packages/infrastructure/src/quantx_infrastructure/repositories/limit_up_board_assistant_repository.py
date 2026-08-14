"""Repositories for the account-level limit-up board assistant."""

from datetime import date
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models.limit_up_board_assistant import (
  LimitUpBoardAssistantConfig,
  LimitUpBoardCandidateArm,
)


class LimitUpBoardAssistantConfigRepository(
  BaseRepository[LimitUpBoardAssistantConfig]
):
  model_class = LimitUpBoardAssistantConfig

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_by_account(
    self, account_id: str
  ) -> Optional[LimitUpBoardAssistantConfig]:
    result = await self.db.execute(
      select(LimitUpBoardAssistantConfig).where(
        LimitUpBoardAssistantConfig.account_id == account_id
      )
    )
    return result.scalar_one_or_none()

  async def find_all_configs(self) -> List[LimitUpBoardAssistantConfig]:
    result = await self.db.execute(
      select(LimitUpBoardAssistantConfig).order_by(
        LimitUpBoardAssistantConfig.created_at.asc()
      )
    )
    return list(result.scalars().all())

  async def save(
    self, config: LimitUpBoardAssistantConfig
  ) -> LimitUpBoardAssistantConfig:
    self.db.add(config)
    await self.db.commit()
    await self.db.refresh(config)
    return config


class LimitUpBoardCandidateArmRepository(BaseRepository[LimitUpBoardCandidateArm]):
  model_class = LimitUpBoardCandidateArm

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find(
    self, account_id: str, trade_date: date, instrument_code: str
  ) -> Optional[LimitUpBoardCandidateArm]:
    result = await self.db.execute(
      select(LimitUpBoardCandidateArm).where(
        LimitUpBoardCandidateArm.account_id == account_id,
        LimitUpBoardCandidateArm.trade_date == trade_date,
        LimitUpBoardCandidateArm.instrument_code == instrument_code,
      )
    )
    return result.scalar_one_or_none()

  async def list_armed(
    self, account_id: str, trade_date: date
  ) -> List[LimitUpBoardCandidateArm]:
    result = await self.db.execute(
      select(LimitUpBoardCandidateArm)
      .where(
        LimitUpBoardCandidateArm.account_id == account_id,
        LimitUpBoardCandidateArm.trade_date == trade_date,
        LimitUpBoardCandidateArm.armed.is_(True),
      )
      .order_by(LimitUpBoardCandidateArm.updated_at.desc())
    )
    return list(result.scalars().all())

  async def save(self, arm: LimitUpBoardCandidateArm) -> LimitUpBoardCandidateArm:
    self.db.add(arm)
    await self.db.commit()
    await self.db.refresh(arm)
    return arm
