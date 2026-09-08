from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from quantx_contracts import HistoricalBarSummary, historical_bar_key
from quantx_qmt_agent import historical_worker
from quantx_qmt_agent import runtime as runtime_module
from quantx_qmt_agent.credentials import DeviceConfiguration
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.runtime import AgentRuntime


def _fake_spawn_historical_worker(connection, worker_kind: str) -> None:
  assert worker_kind == historical_worker.XTDATA_HISTORICAL_WORKER_KIND
  try:
    while True:
      message = connection.recv()
      if message["type"] == "shutdown":
        return
      request_id = message["request_id"]
      connection.send(
        {
          "type": "started",
          "request_id": request_id,
          "total_units": 2,
        }
      )
      spool = Path(message["spool_directory"])
      path = spool / "chunk-000000.json.gz"
      raw = json.dumps([{"request_id": request_id}]).encode("utf-8")
      with path.open("xb") as file_handle:
        with gzip.GzipFile(
          filename="",
          mode="wb",
          fileobj=file_handle,
          mtime=0,
        ) as out:
          out.write(raw)
      compressed = path.read_bytes()
      chunk = {
        "path": str(path),
        "record_count": 1,
        "digest": hashlib.sha256(compressed).hexdigest(),
        "compressed_bytes": len(compressed),
      }
      connection.send(
        {
          "type": "chunk",
          "request_id": request_id,
          "chunk_index": 0,
          "chunk": chunk,
        }
      )
      connection.send(
        {
          "type": "checkpoint",
          "request_id": request_id,
          "completed_units": 1,
          "total_units": 2,
        }
      )
      control = connection.recv()
      assert control["type"] == "continue" and control["request_id"] == request_id
      assert control["max_spool_bytes"] >= 0
      if request_id == "request-timeout":
        time.sleep(60)
      connection.send(
        {
          "type": "ok",
          "request_id": request_id,
          "worker_pid": os.getpid(),
          "manifest": {
            "spool_directory": str(spool),
            "chunks": [chunk],
            "compressed_bytes": len(compressed),
            "uncompressed_bytes": len(raw),
            "record_count": 1,
          },
        }
      )
  finally:
    connection.close()


class _Connection:
  def __init__(self, incoming) -> None:
    self.incoming = deque(incoming)
    self.messages = []
    self.closed = False

  def send(self, message) -> None:
    self.messages.append(message)

  def recv(self):
    return self.incoming.popleft()

  def close(self) -> None:
    self.closed = True


def _enable_history_dispatch(runtime: AgentRuntime) -> None:
  runtime._control_session_authenticated = True
  runtime._set_market_stream_status("READY")
  runtime._whole_market_subscription_active = True
  runtime._whole_market_subscription_ready.set()
  runtime._whole_market_native_reset.clear()
  runtime._set_market_data_ready(True)


def test_xtdata_worker_prepares_spool_and_closes_its_own_client(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  closed: list[None] = []

  class Broker:
    data_manager = SimpleNamespace(close_connection=lambda: closed.append(None))

    @staticmethod
    def iter_market_data(_payload):
      row = {
        "code": "000001.SZ",
        "period": "1d",
        "time": 1_735_776_000_000,
        "close": 10.0,
      }
      key = historical_bar_key(
        code=row["code"],
        period=row["period"],
        time_ms=row["time"],
        tick_ordinal=None,
      )
      return iter(
        [
          row,
          HistoricalBarSummary(
            code=row["code"],
            period=row["period"],
            row_count=1,
            min_time=row["time"],
            max_time=row["time"],
            key_sha256=hashlib.sha256(key.encode()).hexdigest(),
            no_data_reason=None,
          ).model_dump(mode="json"),
        ]
      )

  monkeypatch.setattr(
    historical_worker,
    "_create_historical_broker",
    Broker,
  )
  spool_directory = tmp_path / "request-worker"
  spool_directory.mkdir()
  connection = _Connection(
    [
      {
        "type": "prepare",
        "request_id": "request-worker",
        "payload": {
          "request_id": "request-worker",
          "operation": "bars",
          "stock_list": ["000001.SZ"],
          "periods": ["1d"],
          "start_time": "20250102",
          "end_time": "20250102",
        },
        "spool_directory": str(spool_directory),
        "max_total_uncompressed_bytes": 1_000_000,
        "max_total_compressed_bytes": 1_000_000,
      },
      {"type": "shutdown"},
    ]
  )

  historical_worker.run_historical_market_data_worker(
    connection,
    historical_worker.XTDATA_HISTORICAL_WORKER_KIND,
  )

  assert connection.messages[0] == {
    "type": "started",
    "request_id": "request-worker",
    "total_units": 1,
  }
  completed = connection.messages[1]
  assert completed["type"] == "chunk"
  assert completed["chunk_index"] == 0
  completed = connection.messages[2]
  assert completed["type"] == "ok"
  assert completed["request_id"] == "request-worker"
  manifest = completed["manifest"]
  assert manifest["record_count"] == 2
  assert manifest["chunks"][0]["path"] == str(
    next(spool_directory.glob("chunk-*.json.gz"))
  )
  assert closed == [None]
  assert connection.closed is True


def test_bulk_bar_request_is_split_into_balanced_period_specific_units() -> None:
  codes = [f"{index:06d}.SZ" for index in range(45)]

  units = historical_worker.historical_work_units(
    {
      "operation": "bars",
      "stock_list": list(reversed(codes)),
      "periods": ["1d", "1m"],
      "start_time": "20260828",
      "end_time": "20260828",
    }
  )

  assert len(units) == 6
  assert [len(unit["stock_list"]) for unit in units] == [15] * 6
  assert [unit["periods"] for unit in units] == [
    ["1d"],
    ["1d"],
    ["1d"],
    ["1m"],
    ["1m"],
    ["1m"],
  ]
  assert [code for unit in units[:3] for code in unit["stock_list"]] == codes


def test_intraday_request_is_split_by_date_and_instrument_batch() -> None:
  codes = [f"{index:06d}.SZ" for index in range(25)]

  units = historical_worker.historical_work_units(
    {
      "operation": "bars",
      "stock_list": codes,
      "periods": ["1m"],
      "start_time": "20260827",
      "end_time": "20260829",
    }
  )

  assert len(units) == 6
  assert [len(unit["stock_list"]) for unit in units] == [13, 13, 13, 12, 12, 12]
  assert [(unit["start_time"], unit["end_time"]) for unit in units] == [
    ("20260827", "20260827"),
    ("20260828", "20260828"),
    ("20260829", "20260829"),
  ] * 2


def test_windowed_units_reassemble_canonical_series_order(tmp_path) -> None:
  codes = [f"{index:06d}.SZ" for index in range(25)]
  boundary = object()

  class Broker:
    calls: list[dict[str, object]] = []

    @classmethod
    def iter_market_data(cls, unit):
      cls.calls.append(unit)
      period = unit["periods"][0]
      source_time = int(unit["start_time"])
      for code in sorted(unit["stock_list"]):
        row = {"code": code, "period": period, "time": source_time}
        yield row
        key = historical_bar_key(
          code=code,
          period=period,
          time_ms=source_time,
          tick_ordinal=None,
        )
        yield HistoricalBarSummary(
          code=code,
          period=period,
          row_count=1,
          min_time=source_time,
          max_time=source_time,
          key_sha256=hashlib.sha256(key.encode()).hexdigest(),
          no_data_reason=None,
        ).model_dump(mode="json")

  connection = _Connection(
    [
      {"type": "continue", "request_id": "windowed"},
      {"type": "continue", "request_id": "windowed"},
      {"type": "continue", "request_id": "windowed"},
    ]
  )
  records = list(
    historical_worker._iter_request_records(
      Broker(),
      {
        "operation": "bars",
        "stock_list": codes,
        "periods": ["1m"],
        "start_time": "20260828",
        "end_time": "20260829",
      },
      connection,
      "windowed",
      boundary,
      tmp_path,
      max_staging_uncompressed_bytes=1_000_000,
      max_record_uncompressed_bytes=100_000,
    )
  )
  payload_records = [
    record
    for record in records
    if record is not boundary and record is not historical_worker.HISTORICAL_CHECKPOINT
  ]
  expected_order = [
    (code, source_time) for code in codes for source_time in (20260828, 20260829, None)
  ]

  assert [
    (record["code"], None if "record_type" in record else record["time"])
    for record in payload_records
  ] == expected_order
  assert len(Broker.calls) == 4
  assert [message["type"] for message in connection.messages] == [
    "started",
    "checkpoint",
    "checkpoint",
    "checkpoint",
  ]
  assert not list(tmp_path.glob(".series-*.jsonl"))


def test_windowed_staging_obeys_request_byte_budget(tmp_path) -> None:
  boundary = object()

  class Broker:
    @staticmethod
    def iter_market_data(unit):
      code = unit["stock_list"][0]
      source_time = int(unit["start_time"])
      yield {
        "code": code,
        "period": "1m",
        "time": source_time,
        "payload": "x" * 100,
      }
      yield HistoricalBarSummary(
        code=code,
        period="1m",
        row_count=1,
        min_time=source_time,
        max_time=source_time,
        key_sha256="0" * 64,
        no_data_reason=None,
      ).model_dump(mode="json")

  connection = _Connection([{"type": "continue", "request_id": "bounded-staging"}])
  with pytest.raises(ValueError, match="uncompressed byte limit"):
    list(
      historical_worker._iter_request_records(
        Broker(),
        {
          "operation": "bars",
          "stock_list": ["000001.SZ"],
          "periods": ["1m"],
          "start_time": "20260828",
          "end_time": "20260829",
        },
        connection,
        "bounded-staging",
        boundary,
        tmp_path,
        max_staging_uncompressed_bytes=200,
        max_record_uncompressed_bytes=1_000,
      )
    )

  assert not list(tmp_path.glob(".series-*.jsonl"))


def test_staging_and_published_chunks_share_one_disk_budget() -> None:
  disk = historical_worker._HistoricalDiskBudget(max_bytes=100)
  staging = historical_worker._HistoricalStagingBudget(
    max_bytes=1_000,
    disk_budget=disk,
  )
  staging.reserve(60)

  with pytest.raises(ValueError, match="spool disk byte limit"):
    disk.reserve(41)

  staging.release(60)
  disk.reserve(41)
  assert disk.retained_bytes == 41


def test_worker_reuses_one_xtdata_client_for_multiple_requests(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  created = 0
  closed = 0

  class Broker:
    def __init__(self) -> None:
      self.data_manager = SimpleNamespace(close_connection=self.close)

    def close(self) -> None:
      nonlocal closed
      closed += 1

    @staticmethod
    def iter_market_data(payload):
      yield {
        "request_id": payload["request_id"],
        "code": "000001.SZ",
      }

  def create_broker():
    nonlocal created
    created += 1
    return Broker()

  monkeypatch.setattr(
    historical_worker,
    "_create_historical_broker",
    create_broker,
  )
  first_spool = tmp_path / "first"
  second_spool = tmp_path / "second"
  first_spool.mkdir()
  second_spool.mkdir()

  def prepare(request_id: str, spool) -> dict[str, object]:
    return {
      "type": "prepare",
      "request_id": request_id,
      "payload": {"request_id": request_id, "operation": "instrument_details"},
      "spool_directory": str(spool),
      "max_total_uncompressed_bytes": 1_000_000,
      "max_total_compressed_bytes": 1_000_000,
    }

  connection = _Connection(
    [
      prepare("request-1", first_spool),
      prepare("request-2", second_spool),
      {"type": "shutdown"},
    ]
  )

  historical_worker.run_historical_market_data_worker(
    connection,
    historical_worker.XTDATA_HISTORICAL_WORKER_KIND,
  )

  assert created == 1
  assert closed == 1
  assert [message["type"] for message in connection.messages] == [
    "started",
    "chunk",
    "ok",
    "started",
    "chunk",
    "ok",
  ]


@pytest.mark.asyncio
async def test_runtime_selects_isolated_worker_for_production_broker() -> None:
  runtime = object.__new__(AgentRuntime)
  runtime.broker = SimpleNamespace(
    historical_market_data_worker_kind=lambda: (
      historical_worker.XTDATA_HISTORICAL_WORKER_KIND
    )
  )
  expected = SimpleNamespace()
  calls: list[dict[str, object]] = []
  payload = {
    "request_id": "repair-batch-1",
    "operation": "bars",
    "stock_list": ["000001.SZ"],
    "periods": ["1d"],
    "start_time": "20250102",
    "end_time": "20250102",
  }

  async def isolated(request_id, payload, **limits):
    calls.append(
      {
        "request_id": request_id,
        "payload": payload,
        **limits,
      }
    )
    return expected

  runtime._run_isolated_market_data_preparation = isolated
  result = await runtime._run_market_data_preparation_daemon(
    "repair-batch-1",
    payload,
    max_total_uncompressed_bytes=100,
    max_total_compressed_bytes=50,
  )

  assert result is expected
  assert calls == [
    {
      "request_id": "repair-batch-1",
      "payload": payload,
      "worker_kind": historical_worker.XTDATA_HISTORICAL_WORKER_KIND,
      "max_total_uncompressed_bytes": 100,
      "max_total_compressed_bytes": 50,
      "max_spool_bytes": runtime_module.MAX_MARKET_DATA_UPLOAD_CACHE_BYTES,
    }
  ]


@pytest.mark.asyncio
async def test_complete_request_budget_is_checked_before_worker_dispatch() -> None:
  runtime = object.__new__(AgentRuntime)
  runtime.broker = SimpleNamespace(
    historical_market_data_worker_kind=lambda: (
      historical_worker.XTDATA_HISTORICAL_WORKER_KIND
    )
  )
  dispatched = False

  async def isolated(*_args, **_kwargs):
    nonlocal dispatched
    dispatched = True
    raise AssertionError("oversized request reached isolated worker")

  runtime._run_isolated_market_data_preparation = isolated
  with pytest.raises(ValueError, match="estimated record count"):
    await runtime._run_market_data_preparation_daemon(
      "oversized-tick",
      {
        "request_id": "oversized-tick",
        "operation": "bars",
        "stock_list": [f"{index:06d}.SZ" for index in range(300)],
        "periods": ["tick"],
        "start_time": "20260828",
        "end_time": "20260828",
      },
      max_total_uncompressed_bytes=100,
      max_total_compressed_bytes=50,
    )

  assert dispatched is False


@pytest.mark.asyncio
async def test_runtime_reuses_spawned_worker_across_checkpointed_requests(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  monkeypatch.setattr(
    runtime_module,
    "run_historical_market_data_worker",
    _fake_spawn_historical_worker,
  )
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="spawn-worker-test",
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

  first = await runtime._run_isolated_market_data_preparation(
    "request-spawn-1",
    {"request_id": "request-spawn-1", "operation": "bars"},
    worker_kind="xtdata",
    max_total_uncompressed_bytes=1_000_000,
    max_total_compressed_bytes=1_000_000,
  )
  first_pid = runtime._historical_worker_process.pid
  second = await runtime._run_isolated_market_data_preparation(
    "request-spawn-2",
    {"request_id": "request-spawn-2", "operation": "bars"},
    worker_kind="xtdata",
    max_total_uncompressed_bytes=1_000_000,
    max_total_compressed_bytes=1_000_000,
  )

  assert first.record_count == 1
  assert second.record_count == 1
  assert runtime._historical_worker_process.pid == first_pid
  assert runtime._historical_worker_process.is_alive()

  await runtime._shutdown_historical_worker()
  assert runtime._historical_worker_process is None


@pytest.mark.asyncio
async def test_runtime_uploads_completed_spool_while_next_native_unit_runs(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  monkeypatch.setattr(
    runtime_module,
    "run_historical_market_data_worker",
    _fake_spawn_historical_worker,
  )
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="spawn-worker-upload-pipeline-test",
    ),
    device_secret="unused",
    mode="data-only",
    allowed_accounts=set(),
    broker=SimpleNamespace(
      historical_market_data_worker_kind=lambda: "xtdata",
      is_market_data_ready=lambda: True,
    ),
    journal=LocalJournal(tmp_path / "journal-upload-pipeline.sqlite3"),
    market_spool_base_directory=tmp_path,
  )
  _enable_history_dispatch(runtime)
  runtime._access_token = "agent-token"
  upload_started = asyncio.Event()
  allow_upload = asyncio.Event()
  second_dispatch = asyncio.Event()
  finalized: list[tuple[str, int]] = []
  upload_clients: list[object] = []
  dispatches = 0

  async def wait_for_history_dispatch() -> None:
    nonlocal dispatches
    dispatches += 1
    await asyncio.sleep(0)
    if dispatches == 2:
      second_dispatch.set()

  async def upload_chunk(client, request_id, chunk_index, chunk) -> None:
    assert client is not None
    upload_clients.append(client)
    assert request_id == "request-upload-pipeline"
    assert chunk_index == 0
    assert chunk.path.is_file()
    upload_started.set()
    await allow_upload.wait()

  async def finalize(request_id, total_chunks, *, client=None) -> None:
    assert client is not None
    upload_clients.append(client)
    finalized.append((request_id, total_chunks))

  runtime._wait_for_history_dispatch = wait_for_history_dispatch
  runtime._upload_provisional_market_data_chunk = upload_chunk
  runtime._finalize_market_data_upload = finalize

  preparation = asyncio.create_task(
    runtime._run_isolated_market_data_preparation(
      "request-upload-pipeline",
      {"request_id": "request-upload-pipeline", "operation": "bars"},
      worker_kind="xtdata",
      max_total_uncompressed_bytes=1_000_000,
      max_total_compressed_bytes=1_000_000,
    )
  )
  await upload_started.wait()
  await second_dispatch.wait()

  assert preparation.done() is False
  assert dispatches == 2

  allow_upload.set()
  prepared = await preparation
  assert finalized == [("request-upload-pipeline", 1)]
  assert len({id(client) for client in upload_clients}) == 1
  assert "request-upload-pipeline" in runtime._streamed_market_uploads
  runtime._remove_prepared_market_data(prepared)
  await runtime._shutdown_historical_worker()


@pytest.mark.asyncio
async def test_worker_timeout_rebuilds_child_without_stopping_agent(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  monkeypatch.setattr(
    runtime_module,
    "run_historical_market_data_worker",
    _fake_spawn_historical_worker,
  )
  monkeypatch.setattr(
    runtime_module,
    "HISTORICAL_WORK_UNIT_TIMEOUT_SECONDS",
    2.0,
  )
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="spawn-worker-timeout-test",
    ),
    device_secret="unused",
    mode="data-only",
    allowed_accounts=set(),
    broker=SimpleNamespace(
      historical_market_data_worker_kind=lambda: "xtdata",
      is_market_data_ready=lambda: True,
    ),
    journal=LocalJournal(tmp_path / "journal-timeout.sqlite3"),
    market_spool_base_directory=tmp_path,
  )
  _enable_history_dispatch(runtime)

  with pytest.raises(
    runtime_module._IsolatedMarketDataWorkerError,
    match="MARKET_DATA_PREPARATION_TIMEOUT",
  ):
    await runtime._run_isolated_market_data_preparation(
      "request-timeout",
      {"request_id": "request-timeout", "operation": "bars"},
      worker_kind="xtdata",
      max_total_uncompressed_bytes=1_000_000,
      max_total_compressed_bytes=1_000_000,
    )

  assert runtime._historical_worker_process is None
  assert runtime._stopped.is_set() is False

  recovered = await runtime._run_isolated_market_data_preparation(
    "request-after-timeout",
    {"request_id": "request-after-timeout", "operation": "bars"},
    worker_kind="xtdata",
    max_total_uncompressed_bytes=1_000_000,
    max_total_compressed_bytes=1_000_000,
  )
  assert recovered.record_count == 1
  assert runtime._historical_worker_process.is_alive()
  await runtime._shutdown_historical_worker()


@pytest.mark.asyncio
async def test_unterminated_worker_blocks_replacement_and_trips_agent_fatal(
  tmp_path,
) -> None:
  class UnkillableProcess:
    terminate_calls = 0
    kill_calls = 0

    @staticmethod
    def is_alive() -> bool:
      return True

    def terminate(self) -> None:
      self.terminate_calls += 1

    def kill(self) -> None:
      self.kill_calls += 1

    @staticmethod
    def join(_timeout=None) -> None:
      return None

  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="spawn-worker-unkillable-test",
    ),
    device_secret="unused",
    mode="data-only",
    allowed_accounts=set(),
    broker=SimpleNamespace(is_market_data_ready=lambda: True),
    journal=LocalJournal(tmp_path / "journal-unkillable.sqlite3"),
    market_spool_base_directory=tmp_path,
  )
  process = UnkillableProcess()
  connection = _Connection([])
  runtime._historical_worker_process = process
  runtime._historical_worker_connection = connection
  runtime._historical_worker_kind = "xtdata"

  with pytest.raises(
    runtime_module._FatalMarketDataPreparationError,
    match="could not be terminated",
  ):
    await runtime._shutdown_historical_worker(graceful=False)

  assert connection.closed is True
  assert process.terminate_calls == 1
  assert process.kill_calls == 1
  assert runtime._historical_worker_process is process
  assert runtime._historical_worker_kind == "xtdata"
  assert runtime._fatal_market_data_event.is_set()
  assert runtime._stopped.is_set()
