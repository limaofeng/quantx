import asyncio
import threading

import pytest
from quantx_infrastructure.services import market_data_persistence_verification as v

from tests.infrastructure.test_market_data_grouped_readback import batch
from tests.infrastructure.test_market_data_persistence_verification import (
  END_EXCLUSIVE_MS,
  START_MS,
  _summary,
)


def summaries(groups):
  return [
    _summary(code=b.code, period=b.period, times=[k[0] for k in b.keys]) for b in groups
  ]


async def verify(groups, source=None, **kwargs):
  async def keys():
    for b in groups:
      yield b

  return await v.verify_persisted_bar_summaries(
    code_summaries=summaries(groups),
    expected_key_batches=source if source is not None else keys(),
    start_ms=START_MS,
    end_exclusive_ms=END_EXCLUSIVE_MS,
    **kwargs,
  )


async def test_two_groups_overlap_with_bounded_source_consumption(monkeypatch):
  monkeypatch.setattr(v, "MARKET_DATA_READBACK_GROUP_CODES", 2)
  groups = [batch(f"A{i:02}") for i in range(20)]
  entered = asyncio.Event()
  release = asyncio.Event()
  active = peak = consumed = 0

  async def read(_function, **kwargs):
    nonlocal active, peak
    active += 1
    peak = max(peak, active)
    if active == 2:
      entered.set()
    try:
      await release.wait()
      return {"records_verified": 4, "existing_rows_observed": 1}
    finally:
      active -= 1

  async def keys():
    nonlocal consumed
    for b in groups:
      consumed += 1
      yield b

  monkeypatch.setattr(v, "_await_readback", read)
  task = asyncio.create_task(verify(groups, keys()))
  await asyncio.wait_for(entered.wait(), 2)
  assert not task.done() and consumed <= 7  # two active + one pending + lookahead
  release.set()
  result = await task
  assert peak == 2 and active == 0
  assert result["records_verified"] == 40
  assert result["existing_rows_observed"] == 10


async def test_retry_only_failed_group_and_count_success_once(monkeypatch):
  monkeypatch.setattr(v, "MARKET_DATA_READBACK_GROUP_CODES", 2)
  groups = [batch(f"A{i}") for i in range(4)]
  calls = {}

  async def read(_function, **kwargs):
    key = kwargs["batches"][0].code
    calls[key] = calls.get(key, 0) + 1
    await asyncio.sleep(0)
    if key == "A0" and calls[key] == 1:
      raise v.MarketDataPersistenceMismatchError("missing key")
    return {"records_verified": 4, "existing_rows_observed": 1}

  monkeypatch.setattr(v, "_await_readback", read)
  result = await verify(groups, max_attempts=2, retry_delays=(0,))
  assert calls == {"A0": 2, "A2": 1}
  assert result["existing_rows_observed"] == 2
  assert result["attempts_by_group"] == {"A0/1m": 2, "A1/1m": 2, "A2/1m": 1, "A3/1m": 1}


async def test_worker_reserves_a_query_slot_for_the_local_api(monkeypatch):
  monkeypatch.setattr(v, "MARKET_DATA_READBACK_GROUP_CODES", 2)
  active = peak = 0

  async def read(_function, **kwargs):
    nonlocal active, peak
    active += 1
    peak = max(peak, active)
    await asyncio.sleep(0)
    active -= 1
    return {"records_verified": 4, "existing_rows_observed": 0}

  monkeypatch.setattr(v, "_await_readback", read)
  result = await verify([batch(f"A{i}") for i in range(6)], concurrency=1)
  assert result["records_verified"] == 12
  assert peak == 1


@pytest.mark.parametrize("reason", ["cancel", "source", "query"])
async def test_all_workers_join_before_failure_returns(monkeypatch, reason):
  monkeypatch.setattr(v, "MARKET_DATA_READBACK_GROUP_CODES", 2)
  groups = [batch(f"A{i}") for i in range(7)]
  entered = [threading.Event(), threading.Event()]
  release = threading.Event()
  finished = [threading.Event(), threading.Event()]
  fail = threading.Event()

  def read(*, batches, cancelled, **kwargs):
    index = 0 if batches[0].code == "A0" else 1
    entered[index].set()
    try:
      if reason == "query" and index == 0:
        assert fail.wait(2)
        raise v.MarketDataPersistenceMismatchError("missing key")
      assert release.wait(2)
      assert cancelled.is_set()
      return {"records_verified": 4, "existing_rows_observed": 0}
    finally:
      finished[index].set()

  async def keys():
    for i, b in enumerate(groups):
      if reason == "source" and i == 5:
        await asyncio.to_thread(entered[1].wait, 2)
        raise ValueError("invalid source")
      yield b

  monkeypatch.setattr(v, "_read_expected_key_group_once", read)
  task = asyncio.create_task(verify(groups, keys(), max_attempts=1, retry_delays=()))
  assert await asyncio.to_thread(entered[0].wait, 2)
  assert await asyncio.to_thread(entered[1].wait, 2)
  if reason == "cancel":
    task.cancel()
  fail.set()
  await asyncio.sleep(0.02)
  assert not task.done()
  release.set()
  expected = {
    "cancel": asyncio.CancelledError,
    "source": ValueError,
    "query": v.MarketDataPersistenceMismatchError,
  }[reason]
  with pytest.raises(expected):
    await task
  assert all(event.is_set() for event in finished)


async def test_tick_waits_for_kline_workers_and_stays_serial(monkeypatch):
  monkeypatch.setattr(v, "MARKET_DATA_READBACK_GROUP_CODES", 2)
  groups = [batch(f"A{i}") for i in range(4)]
  groups += [v.ExpectedBarKeyBatch("T", "tick", ((START_MS, 0),))]
  active = 0
  finished = 0

  async def read(_function, **kwargs):
    nonlocal active, finished
    if "batch" in kwargs and kwargs["batch"].period == "tick":
      assert active == 0 and finished == 2
    else:
      active += 1
      await asyncio.sleep(0.01)
      active -= 1
      finished += 1
    return {"records_verified": 1, "existing_rows_observed": 0}

  monkeypatch.setattr(v, "_await_readback", read)
  # Tick summaries include their ordinal in the key digest.
  import hashlib

  from quantx_contracts import historical_bar_key

  expected = summaries(groups[:-1])
  tick = {
    "code": "T",
    "period": "tick",
    "row_count": 1,
    "min_time": START_MS,
    "max_time": START_MS,
    "key_sha256": hashlib.sha256(
      historical_bar_key(
        code="T", period="tick", time_ms=START_MS, tick_ordinal=0
      ).encode()
    ).hexdigest(),
  }

  async def keys():
    for b in groups:
      yield b

  result = await v.verify_persisted_bar_summaries(
    code_summaries=[*expected, tick],
    expected_key_batches=keys(),
    start_ms=START_MS,
    end_exclusive_ms=END_EXCLUSIVE_MS,
  )
  assert result["records_verified"] == 9
