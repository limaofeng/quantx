"""Reference reads, drift rejection and optional content-addressed snapshots."""

from datetime import date

import pytest
from quantx_engine.t_assistant_backtest_data import (
  BacktestDataset,
  acquire_backtest_dataset,
)
from quantx_engine.t_assistant_backtest_run import execute_backtest

from tests.engine.unit.test_t_assistant_backtest_evaluation import Calendar, History
from tests.engine.unit.test_t_assistant_backtest_runtime import CODES, runtime


async def acquire(root, history, *, freeze=False, codes=CODES):
  return await acquire_backtest_dataset(
    history=history,
    calendar=Calendar(),
    source_version="test-influx",
    instruments=codes,
    start=date(2026, 9, 3),
    end=date(2026, 9, 3),
    root=root,
    latency_ms=0,
    freeze=freeze,
  )


async def test_reference_replays_real_chain_without_persisting_ticks(tmp_path):
  history = History()
  dataset = await acquire(tmp_path / "data", history)
  assert dataset.manifest["material"]["storage"] == "REFERENCE"
  assert list((tmp_path / "data").rglob("*.json")) == [
    dataset.directory / "dataset.json"
  ]
  loaded = BacktestDataset(dataset.directory, history=history)
  _, run, result = await execute_backtest(
    request=runtime(request_only=True),
    events=loaded,
    code_manifest={"fixture": "v1"},
    root=tmp_path / "runs",
  )
  assert len(run.broker.orders) == 4
  assert result["material"]["result"]["strategy_admission"] == "NOT_EVALUATED"
  _, _, replay = await execute_backtest(
    request=runtime(request_only=True),
    events=loaded,
    code_manifest={"fixture": "v1"},
    root=tmp_path / "runs",
  )
  assert (
    result["material"]["result"]["economic_hash"]
    == replay["material"]["result"]["economic_hash"]
  )
  assert (await acquire(tmp_path / "data", history)).directory == dataset.directory


async def test_changed_source_rejected_before_emitting_affected_day(tmp_path):
  history = History()
  dataset = await acquire(tmp_path, history)
  history.broken = True
  with pytest.raises(ValueError, match="SOURCE_CHANGED"):
    await anext(dataset.events())
  with pytest.raises(ValueError, match="HISTORY_READER_REQUIRED"):
    await anext(BacktestDataset(dataset.directory).events())


async def test_snapshot_reuses_objects_across_overlapping_universes(tmp_path):
  history = History()
  dataset = await acquire(tmp_path, history, freeze=True)
  objects = sorted((tmp_path / "objects").glob("*.json"))
  before = {p: p.stat().st_mtime_ns for p in objects}
  same = await acquire(tmp_path, history, freeze=True)
  subset = await acquire(tmp_path, history, freeze=True, codes=(CODES[0],))
  assert same.directory == dataset.directory
  assert {
    p: p.stat().st_mtime_ns for p in (tmp_path / "objects").glob("*.json")
  } == before
  history.broken = True
  assert len([e async for e in BacktestDataset(subset.directory).events()]) == 12
  assert len([e async for e in BacktestDataset(dataset.directory).events()]) == 24
  objects[0].write_text('{"rows": []}', encoding="utf-8")
  with pytest.raises(ValueError, match="PART_CORRUPT"):
    await anext(dataset.events())


async def test_corrupt_shared_snapshot_is_never_overwritten(tmp_path):
  history = History()
  dataset = await acquire(tmp_path, history, freeze=True)
  part = dataset.manifest["material"]["parts"][0]
  path = tmp_path / "objects" / (part["hash"] + ".json")
  path.write_text('{"rows": []}', encoding="utf-8")
  with pytest.raises(ValueError, match="SHARED_OBJECT_CORRUPT"):
    await acquire(tmp_path, history, freeze=True)
  assert path.read_text(encoding="utf-8") == '{"rows": []}'


async def test_async_timeline_rejects_duplicate_and_regression():
  from quantx_engine.t_assistant_backtest_timeline import async_tick_frames

  from tests.engine.unit.test_t_assistant_backtest_runtime import ticks

  items = sorted(ticks(), key=lambda item: item.key)

  async def stream(values):
    for value in values:
      yield value

  for values, reason in [
    ([items[0], items[0]], "DUPLICATE"),
    ([items[-1], items[0]], "NON_MONOTONIC"),
  ]:
    with pytest.raises(ValueError, match=reason):
      [frame async for frame in async_tick_frames(stream(values), presorted=True)]
