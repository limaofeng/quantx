"""Cache extraction is distinct from executable Tick qualification."""

import json
from datetime import date

import pytest
from quantx_engine.t_assistant_backtest_data import acquire_backtest_dataset

from tests.engine.unit.test_t_assistant_backtest_evaluation import Calendar, History
from tests.engine.unit.test_t_assistant_backtest_runtime import CODES


class HistoricalCache(History):
  async def get_kline_data(self, **kwargs):
    return []

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
    freeze=True,
    stop_on_error=True,
  )
  material = dataset.manifest["material"]
  assert material["status"] == "REFERENCE_REQUIRED"
  assert sum(p["count"] for p in material["parts"]) == 24
  assert not material["failures"] and material["unattempted_partitions"] == 0
  part = json.loads(
    (
      dataset.directory.parent / "objects" / (material["parts"][0]["hash"] + ".json")
    ).read_text()
  )
  assert part["rows"][0]["price_tick"] is None
  assert part["rows"][0]["up_stop_price"] is None
  assert part["rows"][0]["last_close"] == 100.0
  with pytest.raises(ValueError, match="REFERENCE_REQUIRED"):
    [event async for event in dataset.events()]


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


@pytest.mark.parametrize("preserve_raw", [True, False])
async def test_backtest_replays_without_daily_limits_and_never_uses_tick_limits(
  tmp_path, preserve_raw
):
  from quantx_engine.t_assistant_backtest_run import execute_backtest

  from tests.engine.unit.test_t_assistant_backtest_runtime import runtime

  class NoDailyReference(History):
    async def get_kline_data(self, **kwargs):
      return []

  dataset = await acquire_backtest_dataset(
    history=NoDailyReference(),
    calendar=Calendar(),
    source_version="daily-reference-v1",
    instruments=CODES,
    start=date(2026, 9, 3),
    end=date(2026, 9, 3),
    root=tmp_path,
    latency_ms=0,
    preserve_raw=preserve_raw,
  )
  assert dataset.manifest["material"]["status"] == "FROZEN"
  events = [event async for event in dataset.events()]
  assert events and all(
    event.market.limit_up is None and event.market.limit_down is None
    for event in events
  )
  _, run, result = await execute_backtest(
    request=runtime(request_only=True), events=dataset,
    code_manifest={"fixture": "missing-daily-limits-v1"}, root=tmp_path / "runs",
  )
  assert len(run.broker.orders) == 4
  _, _, replay = await execute_backtest(
    request=runtime(request_only=True), events=dataset,
    code_manifest={"fixture": "missing-daily-limits-v1"}, root=tmp_path / "runs",
  )
  assert result["material"]["result"]["economic_hash"] == replay["material"]["result"]["economic_hash"]


async def test_reference_replay_joins_daily_limits_and_detects_changes(tmp_path):
  class DailyHistory(History):
    limit = 111.0

    async def get_kline_data(self, **kwargs):
      bars = await super().get_kline_data(**kwargs)
      bars[0].up_stop_price = self.limit
      return bars

  history = DailyHistory()
  dataset = await acquire_backtest_dataset(
    history=history,
    calendar=Calendar(),
    source_version="daily-reference-v1",
    instruments=CODES,
    start=date(2026, 9, 3),
    end=date(2026, 9, 3),
    root=tmp_path,
    latency_ms=0,
    freeze=False,
  )
  events = [event async for event in dataset.events()]
  assert events and all(event.market.limit_up == 111.0 for event in events)
  history.limit = 112.0
  with pytest.raises(ValueError, match="BACKTEST_SOURCE_CHANGED"):
    [event async for event in dataset.events()]
