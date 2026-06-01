from typing import List, Optional

from database.connection import get_async_db
from database.relational_base import WhereBuilder
from database.types import Pageable, Pagination, Sort
from models.instrument import Instrument
from repositories.instrument_repository import InstrumentRepository
from repositories.instrument_where_builder import InstrumentWhereBuilder


class InstrumentService:
  """Instrument service"""

  async def find_by_id(self, id: int) -> Optional[Instrument]:
    async for db in get_async_db():
      repo = InstrumentRepository(db)
      return await repo.find_by_id(id)

  async def find_by_code(self, code: str) -> Optional[Instrument]:
    async for db in get_async_db():
      repo = InstrumentRepository(db)
      where = WhereBuilder().eq(Instrument.code, code)
      return await repo.find_one(where=where)

  async def find_page(
    self, pageable: Pageable, where: Optional[InstrumentWhereBuilder] = None
  ) -> Pagination[Instrument]:
    async for db in get_async_db():
      repo = InstrumentRepository(db)
      return await repo.find_page(pageable=pageable, where=where)

  async def find_all(
    self,
    where: Optional[InstrumentWhereBuilder] = None,
    sort: Optional[Sort] = None,
    limit: Optional[int] = None,
    skip: Optional[int] = None,
  ) -> List[Instrument]:
    async for db in get_async_db():
      repo = InstrumentRepository(db)
      return await repo.find_all(where=where, sort=sort, limit=limit, skip=skip)

  async def save(self, instrument: Instrument) -> Instrument:
    async for db in get_async_db():
      repo = InstrumentRepository(db)
      return await repo.save(instrument)

  async def save_batch(self, instruments: List[Instrument]) -> int:
    """批量保存"""
    async for db in get_async_db():
      repo = InstrumentRepository(db)
      result = await repo.bulk_save(instruments)
      return result.saved_count

  async def delete(self, id: int) -> bool:
    async for db in get_async_db():
      repo = InstrumentRepository(db)
      return await repo.delete_by_id(id)
