import threading
from datetime import datetime

import pytest
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
)


class _ThreadRecordingRepository:
  def __init__(self) -> None:
    self.thread_ids: list[int] = []

  def find_all(self, **_kwargs):
    self.thread_ids.append(threading.get_ident())
    return []


@pytest.mark.asyncio
async def test_historical_kline_repository_read_runs_off_event_loop_thread() -> None:
  repository = _ThreadRecordingRepository()
  service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
  service.kline_repo = repository

  values = await service.get_kline_data(
    stock_code="600000.SH",
    period="1m",
    start_time=datetime(2026, 8, 20, 9, 30),
    end_time=datetime(2026, 8, 20, 15, 0),
  )

  assert values == []
  assert repository.thread_ids
  assert repository.thread_ids[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_historical_tick_repository_read_runs_off_event_loop_thread() -> None:
  repository = _ThreadRecordingRepository()
  service = HistoricalMarketDataService.__new__(HistoricalMarketDataService)
  service.tick_repo = repository

  values = await service.get_tick_data(
    stock_code="600000.SH",
    start_time=datetime(2026, 8, 20, 9, 15),
    end_time=datetime(2026, 8, 20, 9, 30),
  )

  assert values == []
  assert repository.thread_ids
  assert repository.thread_ids[0] != threading.get_ident()
