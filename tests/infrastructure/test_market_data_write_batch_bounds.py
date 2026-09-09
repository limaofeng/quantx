import asyncio
import threading

import pytest
from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion

from tests.infrastructure.test_market_data_transfer_ingestion import (
  SHANGHAI_DAY_START_MS,
  _kline_row,
  _summary,
  _write_chunk,
)


@pytest.mark.parametrize("count", [9999, 10000, 10001])
async def test_kline_write_batches_and_key_batches_have_separate_bounds(
  monkeypatch, count
):
  rows = [_kline_row(time=SHANGHAI_DAY_START_MS + i * 1000) for i in range(count)]
  records = [*rows, _summary(rows, period="1m")]
  sizes = []

  async def save_period(*, period, market_data):
    size = sum(len(frame) for frame in market_data.values())
    sizes.append(size)
    return {"status": "success", "saved_count": size}

  payload = {
    "operation": "bars",
    "stock_list": ["600000.SH"],
    "periods": ["1m"],
    "start_time": "20231115",
    "end_time": "20231115",
  }
  result = await ingestion.persist_bar_records(
    records, payload=payload, save_period=save_period
  )
  assert sizes == ([count] if count <= 10000 else [10000, 1])
  assert result["records_saved"] == count
  monkeypatch.setattr(ingestion, "_read_transfer_chunk", lambda *_: records)
  batches = [b async for b in ingestion._uploaded_key_batches([{}])]
  assert max(len(b.keys) for b in batches) <= 2000
  assert sum(len(b.keys) for b in batches) == count


@pytest.mark.parametrize("size", [None, 10000, 0, -1, True, 1.5])
def test_service_forwards_explicit_batch_and_preserves_default(size):
  from unittest.mock import Mock

  from quantx_infrastructure.services.historical_market_data_service import (
    HistoricalMarketDataService,
  )

  service = object.__new__(HistoricalMarketDataService)
  service.kline_repo = Mock()
  records = object()
  if size is not None and (
    isinstance(size, bool) or not isinstance(size, int) or size < 1
  ):
    with pytest.raises(ValueError):
      service.bulk_save_klines("1m", records, batch_size=size)
    service.kline_repo.bulk_save.assert_not_called()
  else:
    kwargs = {} if size is None else {"batch_size": size}
    service.bulk_save_klines("1m", records, **kwargs)
    service.kline_repo.bulk_save.assert_called_once_with(
      measurement="kline_1m", records=records, batch_size=5000 if size is None else size
    )


def test_real_request_record_ceiling_across_next_chunk(tmp_path):
  item = _write_chunk(tmp_path, [{"value": 1}])
  budget = ingestion._TransferBudget(records=499999)
  assert ingestion._read_transfer_chunk(item, budget) == [{"value": 1}]
  assert budget.records == 500000
  with pytest.raises(ingestion.MarketDataValidationError, match="record count limit"):
    ingestion._read_transfer_chunk(item, budget)


async def test_write_cancel_joins_sent_write_before_claim_release(monkeypatch):
  from tests.infrastructure.test_market_data_transfer_ingestion import (
    AtomicRequestStore,
  )

  entered = threading.Event()
  release = threading.Event()
  finished = threading.Event()

  def write(**kwargs):
    entered.set()
    assert release.wait(2)
    finished.set()
    return {"status": "success", "saved_count": 1}

  monkeypatch.setattr(ingestion, "_save_market_data_period_sync", write)
  store = AtomicRequestStore()
  original = store.release_market_data_request_claim

  async def release_claim(*args, **kwargs):
    assert finished.is_set()
    return await original(*args, **kwargs)

  store.release_market_data_request_claim = release_claim

  async def ingest(_store, _request, *, progress=None):
    return await ingestion.save_market_data_period(period="1m", market_data={})

  task = asyncio.create_task(
    ingestion.claim_ingest_and_finish_market_data_request(
      store, "request-1", ingest_request=ingest
    )
  )
  assert await asyncio.to_thread(entered.wait, 2)
  task.cancel()
  await asyncio.sleep(0)
  assert store.release_count == 0
  release.set()
  with pytest.raises(asyncio.CancelledError):
    await task
  assert finished.is_set() and store.status == "UPLOADED" and store.release_count == 1
