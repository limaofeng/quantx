from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.relational_base import BaseRepository
from models.t_trade_imported_entry import TTradeImportedEntry


class TTradeImportedEntryRepository(BaseRepository[TTradeImportedEntry]):
  model_class = TTradeImportedEntry

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  async def find_source(self, account_id: str, trade_id: str) -> Optional[TTradeImportedEntry]:
    result = await self.db.execute(select(TTradeImportedEntry).where(
      TTradeImportedEntry.account_id == account_id,
      TTradeImportedEntry.source_trade_id == trade_id,
    ))
    return result.scalar_one_or_none()

  async def find_by_account(self, account_id: str) -> List[TTradeImportedEntry]:
    result = await self.db.execute(select(TTradeImportedEntry).where(
      TTradeImportedEntry.account_id == account_id
    ).order_by(TTradeImportedEntry.created_at.desc()))
    return list(result.scalars().all())

  async def save(self, entry: TTradeImportedEntry) -> TTradeImportedEntry:
    self.db.add(entry)
    await self.db.commit()
    await self.db.refresh(entry)
    return entry
