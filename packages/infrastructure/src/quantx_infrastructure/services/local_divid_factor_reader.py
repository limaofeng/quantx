"""Data API owns bounded PostgreSQL factor reads; no collection or writes."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from quantx_contracts.divid_factor_read import (
  MAX_FACTOR_WINDOW_ROWS,
  DividFactorRead,
  DividFactorWindow,
)
from sqlalchemy import select

from quantx_infrastructure.models.divid_factor import DividFactorTable
from quantx_infrastructure.services.local_history_reader import HistoryReadBusy


class LocalDividFactorReader:
  def __init__(self, engine):
    self.engine = engine
    self._slot = asyncio.Lock()

  async def read(self, request: DividFactorRead) -> DividFactorWindow:
    if self._slot.locked():
      raise HistoryReadBusy("factor query capacity exhausted")
    async with self._slot, asyncio.timeout(5):
      names = (
        "stock_code",
        "time",
        "ex_date",
        "interest",
        "stock_bonus",
        "stock_gift",
        "allot_num",
        "allot_price",
        "gugai",
        "dr",
      )
      async with self.engine.connect() as connection:
        rows = (
          (
            await connection.execute(
              select(*(getattr(DividFactorTable, name) for name in names))
              .where(
                DividFactorTable.stock_code == request.instrument,
                DividFactorTable.ex_date >= request.start_date.strftime("%Y%m%d"),
                DividFactorTable.ex_date <= request.end_date.strftime("%Y%m%d"),
              )
              .order_by(DividFactorTable.ex_date)
              .limit(MAX_FACTOR_WINDOW_ROWS + 1)
            )
          )
          .mappings()
          .all()
        )
      records = []
      for row in rows:
        value = dict(row)
        stamp = value["time"]
        if not isinstance(stamp, datetime) or stamp.tzinfo is not None:
          raise ValueError("factor storage time must be naive Shanghai time")
        value["time"] = stamp.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        if len(value["ex_date"]) != 8 or not value["ex_date"].isdigit():
          raise ValueError("factor storage date must be YYYYMMDD")
        value["ex_date"] = datetime.strptime(value["ex_date"], "%Y%m%d").date()
        records.append(value)
      return DividFactorWindow(request=request, records=records)
