"""
金融产品仓储层
处理金融产品相关的数据访问
"""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.database.relational_base import BaseRepository
from quantx_infrastructure.models import Instrument, Sector, SectorStock
from quantx_infrastructure.models.enums import InstrumentType


class InstrumentRepository(BaseRepository[Instrument]):
  """金融产品仓储实现"""

  model_class = Instrument

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_by_ids(self, codes: List[str]) -> List[Instrument]:
    """根据代码列表获取金融产品"""
    result = await self.db.execute(select(Instrument).filter(Instrument.id.in_(codes)))
    return list(result.scalars().all())

  async def find_by_code(self, code: str) -> Optional[Instrument]:
    """根据代码获取金融产品"""
    result = await self.db.execute(select(Instrument).filter(Instrument.code == code))
    return result.scalar_one_or_none()

  async def find_all_by_type(self, instrument_type: InstrumentType) -> List[Instrument]:
    """根据类型获取金融产品"""
    result = await self.db.execute(
      select(Instrument).filter(Instrument.type == instrument_type)
    )
    return list(result.scalars().all())

  async def find_all_by_sector(self, sector: str) -> List[Instrument]:
    """根据板块获取金融产品"""
    stmt = (
      select(Instrument)
      .join(SectorStock, Instrument.id == SectorStock.stock_code)
      .join(Sector, SectorStock.sector_id == Sector.id)
      .filter(Sector.name == sector)
    )

    result = await self.db.execute(stmt)
    return list(result.scalars().all())
