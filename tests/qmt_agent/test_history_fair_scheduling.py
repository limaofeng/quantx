import asyncio
import gzip
import hashlib
import json
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from quantx_contracts import HistoricalBarSummary, historical_bar_key
from quantx_qmt_agent import historical_worker as worker
from quantx_qmt_agent import runtime as runtime_module
from quantx_qmt_agent.credentials import DeviceConfiguration
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.runtime import AgentRuntime

from tests.qmt_agent.test_historical_worker import _enable_history_dispatch


def _fair_child(connection, kind):
  class Broker:
    data_manager = None

    def iter_market_data(self, unit):
      with Path(unit["trace_path"]).open("a", encoding="utf-8") as trace:
        trace.write(f"{unit['request_id']}:{unit['start_time']}\n")
      time.sleep(0.03)
      if unit.get("fail_short") and unit["request_id"] == "short":
        raise ValueError("source failed")
      code = unit["stock_list"][0]
      timestamp = int(unit["start_time"])
      yield {"code": code, "period": "1m", "time": timestamp}
      key = historical_bar_key(
        code=code, period="1m", time_ms=timestamp, tick_ordinal=None
      )
      yield HistoricalBarSummary(
        code=code,
        period="1m",
        row_count=1,
        min_time=timestamp,
        max_time=timestamp,
        key_sha256=hashlib.sha256(key.encode()).hexdigest(),
        no_data_reason=None,
      ).model_dump(mode="json")

  worker._create_historical_broker = Broker
  worker.run_historical_market_data_worker(connection, kind)


@pytest.mark.parametrize("fail_short", [False, True])
async def test_parent_round_robins_one_real_child_and_isolates_source_failure(
  tmp_path, monkeypatch, fail_short
):
  monkeypatch.setattr(runtime_module, "run_historical_market_data_worker", _fair_child)
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080", device_id="fair-test"
    ),
    device_secret="unused",
    mode="data-only",
    allowed_accounts=set(),
    broker=SimpleNamespace(
      historical_market_data_worker_kind=lambda: "xtdata",
      is_market_data_ready=lambda: True,
    ),
    journal=LocalJournal(tmp_path / "journal.sqlite3"),
    market_spool_base_directory=tmp_path,
  )
  _enable_history_dispatch(runtime)
  trace = tmp_path / "calls.txt"

  def prepare(identity, end):
    return runtime._run_isolated_market_data_preparation(
      identity,
      {
        "request_id": identity,
        "stock_list": ["000001.SZ"],
        "periods": ["1m"],
        "start_time": "20260803",
        "end_time": end,
        "trace_path": str(trace),
        "fail_short": fail_short,
      },
      worker_kind="xtdata",
      max_total_uncompressed_bytes=1_000_000,
      max_total_compressed_bytes=1_000_000,
    )

  try:
    results = await asyncio.wait_for(
      asyncio.gather(
        prepare("long", "20260804"),
        prepare("short", "20260803"),
        return_exceptions=True,
      ),
      timeout=20,
    )
    assert trace.read_text().splitlines() == [
      "long:20260803",
      "short:20260803",
      "long:20260804",
    ]
    assert results[0].record_count == 3
    if fail_short:
      assert isinstance(results[1], ValueError)
    else:
      assert results[1].record_count == 2
    assert runtime._historical_worker_process.is_alive()
  finally:
    await runtime._shutdown_historical_worker()


async def test_request_slots_remain_bounded_across_waiters_and_cancellation():
  runtime = AgentRuntime.__new__(AgentRuntime)
  runtime._ensure_market_upload_state = lambda: None
  runtime._history_request_slots = asyncio.Semaphore(4)
  ready = asyncio.Event()
  release = asyncio.Event()
  active = peak = 0

  async def handle(_envelope):
    nonlocal active, peak
    active += 1
    peak = max(peak, active)
    if active == 4:
      ready.set()
    try:
      await release.wait()
    finally:
      active -= 1

  runtime._handle_market_data_request_in_slot = handle
  tasks = [
    asyncio.create_task(runtime._handle_market_data_request(None)) for _ in range(9)
  ]
  await asyncio.wait_for(ready.wait(), 2)
  tasks[-1].cancel()
  release.set()
  await asyncio.gather(*tasks, return_exceptions=True)
  assert peak == 4 and active == 0
  assert runtime._history_request_slots._value == 4


@pytest.mark.parametrize("fail_short", [False, True])
def test_short_request_runs_between_long_request_units_without_redownload(
  tmp_path, monkeypatch, fail_short
):
  calls = []

  class Broker:
    data_manager = None

    def iter_market_data(self, unit):
      code = unit["stock_list"][0]
      calls.append((code, unit["start_time"]))
      if fail_short and code == "600000.SH":
        raise ValueError("source failed")
      timestamp = int(unit["start_time"])
      yield {"code": code, "period": "1m", "time": timestamp}
      key = historical_bar_key(
        code=code, period="1m", time_ms=timestamp, tick_ordinal=None
      )
      yield HistoricalBarSummary(
        code=code,
        period="1m",
        row_count=1,
        min_time=timestamp,
        max_time=timestamp,
        key_sha256=hashlib.sha256(key.encode()).hexdigest(),
        no_data_reason=None,
      ).model_dump(mode="json")

  monkeypatch.setattr(worker, "_create_historical_broker", Broker)

  def prepare(identity, code, end):
    folder = tmp_path / identity
    folder.mkdir()
    return {
      "type": "prepare",
      "request_id": identity,
      "spool_directory": str(folder),
      "payload": {
        "stock_list": [code],
        "periods": ["1m"],
        "start_time": "20260803",
        "end_time": end,
      },
      "max_total_uncompressed_bytes": 1_000_000,
      "max_total_compressed_bytes": 1_000_000,
    }

  incoming = deque(
    [
      prepare("long", "000001.SZ", "20260804"),
      prepare("short", "600000.SH", "20260803"),
      {"type": "continue", "request_id": "long", "max_spool_bytes": 1_000_000},
      {"type": "shutdown"},
    ]
  )
  messages = []

  class Connection:
    def recv(self):
      return incoming.popleft()

    def send(self, message):
      messages.append(message)

    def close(self):
      pass

  worker.run_historical_market_data_worker(Connection(), "xtdata")
  assert calls == [
    ("000001.SZ", "20260803"),
    ("600000.SH", "20260803"),
    ("000001.SZ", "20260804"),
  ]
  assert [
    (m["request_id"], m["type"]) for m in messages if m["type"] in {"ok", "error"}
  ] == [("short", "error" if fail_short else "ok"), ("long", "ok")]
  records = []
  for path in sorted((tmp_path / "long").glob("*.json.gz")):
    records.extend(json.loads(gzip.decompress(path.read_bytes())))
  assert [r["time"] for r in records if "record_type" not in r] == [20260803, 20260804]
  assert records[-1]["row_count"] == 2


def test_shared_disk_quota_covers_all_suspended_requests():
  shared = worker._HistoricalDiskBudget(100)
  first = worker._HistoricalDiskBudget(100, shared=shared)
  second = worker._HistoricalDiskBudget(100, shared=shared)
  first.reserve(60)
  with pytest.raises(ValueError, match="disk byte limit"):
    second.reserve(50)
  assert second.retained_bytes == 0
  first.release(30)
  second.reserve(50)
  assert shared.retained_bytes == 80
