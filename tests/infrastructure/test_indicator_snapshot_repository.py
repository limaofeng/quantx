from datetime import date

import pytest
from quantx_infrastructure.models.enums import InstrumentType
from quantx_infrastructure.repositories.indicator_snapshot_repository import (
  MAX_BULK_UPSERT_RECORDS,
  IndicatorSnapshotRepository,
  _normalize_instrument_type,
)


def test_instrument_type_normalization_unwraps_strawberry_enum_metadata() -> None:
  assert _normalize_instrument_type(InstrumentType.STOCK) == "stock"
  assert _normalize_instrument_type(InstrumentType.ETF) == "etf"


class _FakeSession:
  def __init__(self) -> None:
    self.statements = []
    self.commits = 0

  async def execute(self, statement):
    self.statements.append(statement)

  async def commit(self):
    self.commits += 1


@pytest.mark.asyncio
async def test_snapshot_bulk_upsert_uses_bounded_multi_row_statements() -> None:
  session = _FakeSession()
  repo = IndicatorSnapshotRepository(session)
  count = MAX_BULK_UPSERT_RECORDS * 2 + 1
  records = [
    {
      "code": f"{index:06d}.SZ",
      "snapshot_date": date(2026, 7, 29),
      "instrument_type": "stock",
      "name": f"股票{index}",
    }
    for index in range(count)
  ]

  saved = await repo.bulk_upsert(records)

  assert saved == count
  assert len(session.statements) == 3
  assert session.commits == 1
  assert all("ON CONFLICT" in str(statement) for statement in session.statements)
