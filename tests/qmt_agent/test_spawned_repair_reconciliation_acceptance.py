from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from quantx_qmt_agent import historical_worker
from quantx_qmt_agent import runtime as runtime_module
from quantx_qmt_agent.credentials import DeviceConfiguration
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.runtime import AgentRuntime

SYNTHETIC_UNIVERSE_SIZE = 7_552
OUTER_BATCH_SIZE = 300
INNER_WORK_UNIT_SIZE = 20


def _spawned_acceptance_worker(connection, worker_kind: str) -> None:
  """Spawn-safe XTData protocol double; native SDK calls remain disabled."""
  assert worker_kind == historical_worker.XTDATA_HISTORICAL_WORKER_KIND
  try:
    while True:
      request = connection.recv()
      if request.get("type") == "shutdown":
        return
      request_id = str(request["request_id"])
      symbols = list(request["payload"].get("stock_list") or [])
      units = [
        symbols[offset : offset + INNER_WORK_UNIT_SIZE]
        for offset in range(0, len(symbols), INNER_WORK_UNIT_SIZE)
      ]
      connection.send(
        {
          "type": "started",
          "request_id": request_id,
          "total_units": len(units),
        }
      )
      spool = Path(request["spool_directory"])
      chunks: list[dict[str, Any]] = []
      uncompressed_bytes = 0
      for index, unit in enumerate(units):
        raw = json.dumps(
          [
            {
              "code": code,
              "period": "1d",
              "time": 1_788_192_000_000,
              "close": 10.0,
            }
            for code in unit
          ],
          separators=(",", ":"),
        ).encode()
        path = spool / f"chunk-{index:06d}.json.gz"
        temporary = path.with_suffix(".json.gz.tmp")
        with temporary.open("xb") as handle:
          with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0) as out:
            out.write(raw)
          handle.flush()
          os.fsync(handle.fileno())
        temporary.replace(path)
        compressed = path.read_bytes()
        chunk = {
          "path": str(path),
          "record_count": len(unit),
          "digest": hashlib.sha256(compressed).hexdigest(),
          "compressed_bytes": len(compressed),
        }
        chunks.append(chunk)
        uncompressed_bytes += len(raw)
        connection.send(
          {
            "type": "chunk",
            "request_id": request_id,
            "chunk_index": index,
            "chunk": chunk,
          }
        )
        if index + 1 < len(units):
          connection.send(
            {
              "type": "checkpoint",
              "request_id": request_id,
              "completed_units": index + 1,
              "total_units": len(units),
            }
          )
          continuation = connection.recv()
          assert set(continuation) == {"type", "request_id", "max_spool_bytes"}
          assert continuation["type"] == "continue"
          assert continuation["request_id"] == request_id
          assert continuation["max_spool_bytes"] >= 0
      connection.send(
        {
          "type": "ok",
          "request_id": request_id,
          "worker_pid": os.getpid(),
          "manifest": {
            "spool_directory": str(spool),
            "chunks": chunks,
            "compressed_bytes": sum(
              int(chunk["compressed_bytes"]) for chunk in chunks
            ),
            "uncompressed_bytes": uncompressed_bytes,
            "record_count": len(symbols),
          },
        }
      )
  finally:
    connection.close()


class _LiveBrokerDouble:
  def __init__(self) -> None:
    self.native_calls: list[str] = []

  @staticmethod
  def historical_market_data_worker_kind() -> str:
    return "xtdata"

  @staticmethod
  def is_market_data_ready() -> bool:
    return True

  @staticmethod
  def is_trading_ready() -> bool:
    return True

  @staticmethod
  def trading_connection_generation() -> int:
    return 1

  @staticmethod
  def require_trading_reconciliation() -> None:
    return None

  @staticmethod
  def mark_trading_reconciled(
    generation: int,
    _callback_failure_generation: int,
  ) -> bool:
    return generation == 1

  def capture_full_snapshot(self) -> tuple[dict[str, Any], int]:
    self.native_calls.append("reconciliation")
    return (
      {
        "accounts": [{"account_id": "account-1"}],
        "positions_by_account": {"account-1": []},
        "orders": [],
        "trades": [],
        "sequence": time.time_ns(),
        "is_complete": True,
        "unavailable_accounts": [],
        "section_completeness_by_account": {
          "account-1": {
            "account": True,
            "positions": True,
            "orders": True,
            "trades": True,
          }
        },
        "mode": "live",
      },
      1,
    )


class _SpoolUploadClient:
  def __init__(self) -> None:
    self.puts = 0
    self.posts = 0
    self.max_active = 0
    self._active = 0
    self.first_upload = asyncio.Event()

  async def put(self, url: str, *, content, headers, **_kwargs):
    self._active += 1
    self.max_active = max(self.max_active, self._active)
    try:
      body = bytearray()
      async for block in content:
        body.extend(block)
      assert hashlib.sha256(body).hexdigest() == headers["X-Content-SHA256"]
      assert len(body) == int(headers["Content-Length"])
      self.puts += 1
      self.first_upload.set()
      await asyncio.sleep(0)
      return httpx.Response(200, request=httpx.Request("PUT", url))
    finally:
      self._active -= 1

  async def post(self, url: str, **_kwargs):
    self.posts += 1
    return httpx.Response(200, request=httpx.Request("POST", url))

  async def aclose(self) -> None:
    return None


def _universe() -> list[str]:
  return [
    f"{index:06d}.{'SH' if index % 2 else 'SZ'}"
    for index in range(1, SYNTHETIC_UNIVERSE_SIZE + 1)
  ]


@pytest.mark.skipif(os.name != "nt", reason="Windows spawn acceptance")
@pytest.mark.asyncio
async def test_spawned_7552_repair_keeps_reconciliation_and_upload_live(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  """Synthetic load over production spawn/Pipe/spool/journal scheduling paths."""
  monkeypatch.setattr(
    runtime_module,
    "run_historical_market_data_worker",
    _spawned_acceptance_worker,
  )
  monkeypatch.setattr(runtime_module, "HISTORY_QOS_CHECK_SECONDS", 0.01)
  broker = _LiveBrokerDouble()
  journal = LocalJournal(tmp_path / "acceptance.sqlite3")
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="spawned-7552-acceptance",
    ),
    device_secret="unused",
    mode="live",
    allowed_accounts={"account-1"},
    broker=broker,
    journal=journal,
    market_spool_base_directory=tmp_path,
  )
  uploader = _SpoolUploadClient()
  runtime._access_token = "synthetic-agent-token"
  runtime._market_data_http_client = uploader
  runtime._control_session_authenticated = True
  runtime._set_market_stream_status("READY")
  runtime._whole_market_subscription_active = True
  runtime._whole_market_subscription_ready.set()
  runtime._whole_market_native_reset.clear()
  runtime._set_trading_ready(True)
  runtime._trading_readiness_failed = False
  runtime._last_complete_account_snapshot_monotonic = time.monotonic()

  symbols = _universe()
  batches = [
    symbols[offset : offset + OUTER_BATCH_SIZE]
    for offset in range(0, len(symbols), OUTER_BATCH_SIZE)
  ]
  assert len(batches) == 26
  assert [len(batch) for batch in batches] == [300] * 25 + [52]

  async def repair() -> tuple[int, list[int]]:
    records = 0
    unit_sizes: list[int] = []
    for index, batch in enumerate(batches):
      request_id = f"repair-{index:02d}"
      chunks = await runtime._prepared_market_data_chunks(
        request_id,
        {
          "request_id": request_id,
          "operation": "bars",
          "stock_list": batch,
          "periods": ["1d"],
          "start_time": "20260828",
          "end_time": "20260828",
        },
      )
      unit_sizes.extend(chunk.record_count for chunk in chunks)
      records += sum(chunk.record_count for chunk in chunks)
      await runtime._complete_market_upload(request_id)
    return records, unit_sizes

  repair_task = asyncio.create_task(repair())
  await asyncio.wait_for(uploader.first_upload.wait(), timeout=10)
  snapshot_id = await asyncio.wait_for(
    runtime._queue_full_snapshot(reconciliation=True),
    timeout=2,
  )
  journal.acknowledge_report(snapshot_id)
  runtime._acknowledge_trading_reconciliation_snapshot(snapshot_id)
  records, unit_sizes = await asyncio.wait_for(repair_task, timeout=45)

  assert records == SYNTHETIC_UNIVERSE_SIZE
  assert len(unit_sizes) == sum(
    math.ceil(len(batch) / INNER_WORK_UNIT_SIZE) for batch in batches
  )
  assert max(unit_sizes) <= INNER_WORK_UNIT_SIZE
  assert broker.native_calls == ["reconciliation"]
  assert uploader.puts == len(unit_sizes)
  assert uploader.posts == len(batches)
  assert uploader.max_active <= runtime_module.MAX_CONCURRENT_HISTORY_UPLOADS
  assert runtime._fatal_market_data_error is None
  assert runtime._fatal_trading_error is None
  assert runtime._historical_worker_process is not None
  assert runtime._historical_worker_process.is_alive()

  await runtime._shutdown_historical_worker()
  await runtime._close_market_data_upload_client()
  runtime._xttrading_worker.close()
  runtime._journal_worker.close()
  runtime._historical_ipc_executor.shutdown(wait=True, cancel_futures=True)
  runtime._history_upload_io_executor.shutdown(wait=True, cancel_futures=True)
