"""Bounded annual holiday snapshot for local historical read windows."""

import asyncio

from quantx_contracts.development_reference import CalendarRequest, CalendarSnapshot
from sqlalchemy import text

from .local_history_reader import HistoryReadBusy


class LocalCalendarReader:
  def __init__(self, engine):
    self.engine = engine
    self.slot = asyncio.Lock()

  async def read(self, request: CalendarRequest):
    if self.slot.locked():
      raise HistoryReadBusy("calendar read capacity exhausted")
    async with self.slot, asyncio.timeout(3), self.engine.connect() as db:
      rows = (
        (
          await db.execute(
            text(
              "SELECT date,description FROM holidays WHERE market=:market AND year=:year ORDER BY date LIMIT 367"
            ),
            {"market": request.market, "year": request.year},
          )
        )
        .mappings()
        .all()
      )
      return CalendarSnapshot(
        **request.model_dump(), holidays=[dict(row) for row in rows]
      )
