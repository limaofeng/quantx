import asyncio
from datetime import datetime

from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
)


class _CapturingTickRepository:
  def __init__(self) -> None:
    self.kwargs = None

  def find_all(self, **kwargs):
    self.kwargs = kwargs
    return []


def test_get_tick_data_expands_end_to_last_ordinal_microsecond() -> None:
  repository = _CapturingTickRepository()
  service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
  service.tick_repo = repository
  end_time = datetime(2026, 8, 14, 9, 30, 0, 123000)

  result = asyncio.run(
    service.get_tick_data(
      stock_code="601318.SH",
      start_time=datetime(2026, 8, 14, 9, 30),
      end_time=end_time,
    )
  )

  assert result == []
  assert repository.kwargs is not None
  assert repository.kwargs["end_time"] == datetime(
    2026, 8, 14, 9, 30, 0, 123999
  )


def test_get_tick_data_forwards_replay_pagination_offset() -> None:
  repository = _CapturingTickRepository()
  service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
  service.tick_repo = repository

  result = asyncio.run(
    service.get_tick_data(
      stock_code="601318.SH",
      start_time=datetime(2026, 8, 14, 9, 30),
      end_time=datetime(2026, 8, 14, 15, 0),
      limit=6_000,
      offset=6_000,
      order="asc",
    )
  )

  assert result == []
  assert repository.kwargs is not None
  assert repository.kwargs["limit"] == 6_000
  assert repository.kwargs["offset"] == 6_000
  assert repository.kwargs["order_by"] == "time ASC"
