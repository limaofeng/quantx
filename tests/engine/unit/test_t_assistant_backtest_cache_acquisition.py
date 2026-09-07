"""Cache extraction is distinct from executable Tick qualification."""

import json
from datetime import date

import pytest
from quantx_engine.t_assistant_backtest_data import acquire_backtest_dataset

from tests.engine.unit.test_t_assistant_backtest_evaluation import Calendar, History
from tests.engine.unit.test_t_assistant_backtest_runtime import CODES


class HistoricalCache(History):
  async def iter_tick_pages(self, **kwargs):
    async for page in super().iter_tick_pages(**kwargs):
      for row in page:
        row.price_tick = float("nan")
        row.up_stop_price = float("nan")
        row.down_stop_price = float("nan")
        row.last_close = 100.0
      yield page


async def test_raw_cache_is_preserved_without_fabricating_limit_prices(tmp_path):
  history = HistoricalCache()
  dataset = await acquire_backtest_dataset(
    history=history,
    calendar=Calendar(),
    source_version="fixture-cache-v1",
    instruments=CODES,
    start=date(2026, 9, 3),
    end=date(2026, 9, 3),
    root=tmp_path,
    latency_ms=0,
    preserve_raw=True,
    stop_on_error=True,
  )
  material = dataset.manifest["material"]
  assert material["status"] == "REFERENCE_REQUIRED"
  assert sum(p["count"] for p in material["parts"]) == 24
  assert not material["failures"] and material["unattempted_partitions"] == 0
  part = json.loads((dataset.directory / material["parts"][0]["file"]).read_text())
  assert part["rows"][0]["price_tick"] is None
  assert part["rows"][0]["up_stop_price"] is None
  assert part["rows"][0]["last_close"] == 100.0
  with pytest.raises(ValueError, match="REFERENCE_REQUIRED"):
    list(dataset.events())


async def test_source_error_stops_without_querying_other_symbols(tmp_path):
  class BrokenCache(History):
    async def iter_tick_pages(self, **kwargs):
      self.calls.append(kwargs)
      raise RuntimeError("fixture source unavailable")
      yield []

  history = BrokenCache()
  dataset = await acquire_backtest_dataset(
    history=history,
    calendar=Calendar(),
    source_version="fixture-cache-v1",
    instruments=CODES,
    start=date(2026, 9, 3),
    end=date(2026, 9, 3),
    root=tmp_path,
    latency_ms=0,
    preserve_raw=True,
    stop_on_error=True,
  )
  assert len(history.calls) == 1
  assert dataset.manifest["material"]["status"] == "INCOMPLETE"
  assert dataset.manifest["material"]["unattempted_partitions"] == 1
