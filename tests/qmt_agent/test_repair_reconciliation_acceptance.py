from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import orjson
import pytest
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_qmt_agent.credentials import DeviceConfiguration
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.runtime import (
  REPORT_SEND_WINDOW,
  XTTRADING_PRIORITY_CANCEL,
  XTTRADING_PRIORITY_ORDER,
  XTTRADING_PRIORITY_PERIODIC_SNAPSHOT,
  AgentRuntime,
  _PriorityControlSocketWriter,
)


def _complete_snapshot(*, generation: int) -> tuple[dict[str, Any], int]:
  return (
    {
      "accounts": [],
      "positions_by_account": {},
      "orders": [],
      "trades": [],
      "sequence": 1,
      "is_complete": True,
      "unavailable_accounts": [],
      "section_completeness_by_account": {},
      "mode": "live",
    },
    generation,
  )


class _ConcurrentBrokerDouble:
  """Test-only native boundary; it never imports or calls QMT."""

  generation = 7

  def __init__(self) -> None:
    self.history_started = threading.Event()
    self.release_first_history = threading.Event()
    self.snapshot_started = threading.Event()
    self.release_snapshot = threading.Event()
    self.xtdata_xttrading_overlap = threading.Event()
    self.lock = threading.Lock()
    self.xtdata_active = 0
    self.xtdata_max_active = 0
    self.xttrading_active = 0
    self.xttrading_max_active = 0
    self.history_batch_sizes: list[int] = []
    self.trading_completion_order: list[str] = []
    self.reconciled_generation = -1

  def iter_market_data(self, payload: dict[str, Any]):
    stocks = list(payload["stock_list"])
    with self.lock:
      self.xtdata_active += 1
      self.xtdata_max_active = max(self.xtdata_max_active, self.xtdata_active)
      self.history_batch_sizes.append(len(stocks))
      first = len(self.history_batch_sizes) == 1
    self.history_started.set()
    try:
      if first:
        assert self.release_first_history.wait(timeout=5)
      yield {
        "code": stocks[0],
        "period": "1d",
        "time": 1_735_776_000_000,
        "close": 10.0,
      }
    finally:
      with self.lock:
        self.xtdata_active -= 1

  def capture_full_snapshot(self) -> tuple[dict[str, Any], int]:
    self._enter_trading("reconciliation-snapshot")
    try:
      self.snapshot_started.set()
      assert self.release_snapshot.wait(timeout=5)
      return _complete_snapshot(generation=self.generation)
    finally:
      self._leave_trading("reconciliation-snapshot")

  def native_probe(self, name: str) -> str:
    self._enter_trading(name)
    try:
      time.sleep(0.002)
      return name
    finally:
      self._leave_trading(name)

  def _enter_trading(self, _name: str) -> None:
    with self.lock:
      self.xttrading_active += 1
      self.xttrading_max_active = max(
        self.xttrading_max_active,
        self.xttrading_active,
      )
      if self.xtdata_active:
        self.xtdata_xttrading_overlap.set()

  def _leave_trading(self, name: str) -> None:
    with self.lock:
      self.trading_completion_order.append(name)
      self.xttrading_active -= 1

  def require_trading_reconciliation(self) -> None:
    self.reconciled_generation = -1

  def mark_trading_reconciled(
    self,
    generation: int,
    _callback_failure_generation: int,
  ) -> bool:
    if generation != self.generation:
      return False
    self.reconciled_generation = generation
    return True


def _live_runtime(
  tmp_path: Path,
  *,
  broker: Any,
  device_id: str,
) -> AgentRuntime:
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id=device_id,
    ),
    device_secret="not-used-by-the-test",
    mode="live",
    allowed_accounts={"account-1"},
    broker=broker,
    journal=LocalJournal(tmp_path / f"{device_id}.sqlite3"),
    market_spool_base_directory=tmp_path,
  )
  runtime._session_loop = asyncio.get_running_loop()
  runtime._runtime_loop = asyncio.get_running_loop()
  runtime._set_market_data_ready(True)
  runtime._set_trading_ready(True)
  runtime._trading_connection_generation_cache = int(getattr(broker, "generation", 0))
  runtime._control_session_authenticated = True
  runtime._set_market_stream_status("READY")
  runtime._whole_market_subscription_active = True
  runtime._whole_market_subscription_ready.set()
  runtime._whole_market_native_reset.clear()
  runtime._last_complete_account_snapshot_monotonic = time.monotonic()
  return runtime


@pytest.mark.asyncio
async def test_7552_symbol_repair_and_reconciliation_are_concurrent(
  tmp_path: Path,
) -> None:
  """Equivalent 26/26 acceptance without a broker account or native QMT."""

  universe = [f"{index:06d}.SZ" for index in range(7_552)]
  batches = [universe[offset : offset + 300] for offset in range(0, 7_552, 300)]
  assert len(batches) == 26
  assert [len(batch) for batch in batches] == [300] * 25 + [52]

  broker = _ConcurrentBrokerDouble()
  runtime = _live_runtime(
    tmp_path,
    broker=broker,
    device_id="acceptance-concurrency",
  )
  outer_slots = asyncio.Semaphore(2)
  outer_active = 0
  outer_max_active = 0
  two_outer_batches_active = asyncio.Event()

  async def prepare_batch(index: int, stocks: list[str]) -> tuple[int, int]:
    nonlocal outer_active, outer_max_active
    async with outer_slots:
      outer_active += 1
      outer_max_active = max(outer_max_active, outer_active)
      if outer_active == 2:
        two_outer_batches_active.set()
      try:
        request_id = f"repair-market-data-{index:02d}"
        chunks = await runtime._prepared_market_data_chunks(
          request_id,
          {
            "request_id": request_id,
            "operation": "bars",
            "stock_list": stocks,
            "periods": ["1d"],
            "start_time": "20250102",
            "end_time": "20250102",
          },
        )
        record_count = sum(chunk.record_count for chunk in chunks)
        # The real uploader retires its durable spool after the manifest ACK.
        # Doing the same here keeps the runtime's intentionally small, bounded
        # recovery cache under load instead of turning this into a cache-limit
        # test.
        await runtime._complete_market_upload(request_id)
        return index, record_count
      finally:
        outer_active -= 1

  repair_tasks = [
    asyncio.create_task(
      prepare_batch(index, stocks),
      name=f"acceptance-repair-{index:02d}",
    )
    for index, stocks in enumerate(batches)
  ]
  try:
    assert await asyncio.to_thread(broker.history_started.wait, 1)
    await asyncio.wait_for(two_outer_batches_active.wait(), timeout=1)

    reconciliation = asyncio.create_task(
      runtime._queue_full_snapshot(reconciliation=True),
      name="acceptance-reconciliation",
    )
    assert await asyncio.to_thread(broker.snapshot_started.wait, 1)

    queued_periodic = asyncio.create_task(
      runtime._run_native_xttrading(
        "periodic-probe",
        broker.native_probe,
        "periodic-probe",
        timeout=1,
        priority=XTTRADING_PRIORITY_PERIODIC_SNAPSHOT,
      )
    )
    queued_order = asyncio.create_task(
      runtime._run_native_xttrading(
        "order-probe",
        broker.native_probe,
        "order-probe",
        timeout=1,
        priority=XTTRADING_PRIORITY_ORDER,
      )
    )
    queued_cancel = asyncio.create_task(
      runtime._run_native_xttrading(
        "cancel-probe",
        broker.native_probe,
        "cancel-probe",
        timeout=1,
        priority=XTTRADING_PRIORITY_CANCEL,
      )
    )
    await asyncio.sleep(0)
    broker.release_snapshot.set()

    snapshot_id = await asyncio.wait_for(reconciliation, timeout=1)
    assert runtime._acknowledge_trading_reconciliation_snapshot(snapshot_id)
    assert runtime._requires_trading_reconciliation() is False
    assert broker.reconciled_generation == broker.generation
    assert broker.release_first_history.is_set() is False

    assert await asyncio.wait_for(queued_cancel, timeout=1) == "cancel-probe"
    assert await asyncio.wait_for(queued_order, timeout=1) == "order-probe"
    assert await asyncio.wait_for(queued_periodic, timeout=1) == "periodic-probe"
    broker.release_first_history.set()
    results = await asyncio.wait_for(asyncio.gather(*repair_tasks), timeout=10)
  finally:
    broker.release_snapshot.set()
    broker.release_first_history.set()
    for task in repair_tasks:
      if not task.done():
        task.cancel()
    await asyncio.gather(*repair_tasks, return_exceptions=True)
    runtime._xttrading_worker.close()

  assert results == [(index, 1) for index in range(26)]
  assert outer_max_active == 2
  assert broker.history_batch_sizes == [300] * 25 + [52]
  assert broker.xtdata_max_active == 1
  assert broker.xtdata_xttrading_overlap.is_set()
  assert broker.xttrading_max_active == 1
  assert broker.trading_completion_order == [
    "reconciliation-snapshot",
    "cancel-probe",
    "order-probe",
    "periodic-probe",
  ]
  assert runtime._fatal_market_data_error is None
  assert runtime._fatal_trading_error is None


class _DelayedControlSocket:
  def __init__(self) -> None:
    self.sent_types: list[str] = []
    self.active_sends = 0
    self.max_active_sends = 0
    self.closed: list[tuple[int, str]] = []

  async def send(self, serialized: str) -> None:
    self.active_sends += 1
    self.max_active_sends = max(self.max_active_sends, self.active_sends)
    try:
      await asyncio.sleep(0.0005)
      envelope = orjson.loads(serialized)
      self.sent_types.append(str(envelope["message_type"]))
    finally:
      self.active_sends -= 1

  async def close(self, *, code: int, reason: str) -> None:
    self.closed.append((code, reason))


class _HeartbeatBrokerDouble:
  generation = 1

  @staticmethod
  def require_trading_reconciliation() -> None:
    return None

  @staticmethod
  def mark_trading_reconciled(
    _generation: int,
    _callback_failure_generation: int,
  ) -> bool:
    return True


def _report(index: int) -> AgentEnvelope:
  return AgentEnvelope(
    message_id=f"00000000-0000-4000-8000-{index:012d}",
    message_type=AgentMessageType.DELTA_REPORT,
    payload={"sequence": index, "is_complete": False},
  )


@pytest.mark.asyncio
async def test_market_storm_cannot_starve_heartbeat_or_durable_reports(
  tmp_path: Path,
) -> None:
  runtime = _live_runtime(
    tmp_path,
    broker=_HeartbeatBrokerDouble(),
    device_id="acceptance-control-qos",
  )
  runtime._set_market_stream_status("READY")
  socket = _DelayedControlSocket()
  writer = _PriorityControlSocketWriter(socket)
  runtime._control_socket_writer = writer
  runtime._runtime_loop = asyncio.get_running_loop()

  for index in range(REPORT_SEND_WINDOW):
    report = _report(index)
    runtime.journal.add_report(report.message_id, report.model_dump_json())

  # Single-instrument quotes belong to the independent market WebSocket. A
  # callback storm therefore cannot consume any control-writer capacity.
  for index in range(256):
    runtime._enqueue_market_event(
      {"subscription_id": "quote-1", "data": {"sequence": index}}
    )

  heartbeat = asyncio.create_task(
    runtime._heartbeat_checkpoint(socket, status="READY"),
    name="acceptance-heartbeat",
  )
  reports = asyncio.create_task(
    runtime._flush_reports(socket),
    name="acceptance-reports",
  )
  await asyncio.sleep(0)
  writer_task = asyncio.create_task(writer.run(), name="acceptance-ws-writer")

  async def keep_quotes_flowing() -> None:
    for index in range(256, 1_024):
      runtime._enqueue_market_event(
        {"subscription_id": "quote-1", "data": {"sequence": index}}
      )
      if index % 32 == 0:
        await asyncio.sleep(0)

  quote_storm = asyncio.create_task(
    keep_quotes_flowing(),
    name="acceptance-quote-storm",
  )
  try:
    await asyncio.wait_for(asyncio.gather(heartbeat, reports), timeout=2)
    assert writer_task.done() is False
    await asyncio.wait_for(quote_storm, timeout=2)
  finally:
    if not quote_storm.done():
      quote_storm.cancel()
    writer_task.cancel()
    await asyncio.gather(quote_storm, writer_task, return_exceptions=True)
    runtime._xttrading_worker.close()

  assert socket.sent_types[0] == AgentMessageType.HEARTBEAT.value
  report_positions = [
    index
    for index, message_type in enumerate(socket.sent_types)
    if message_type == AgentMessageType.DELTA_REPORT.value
  ]
  assert len(report_positions) == REPORT_SEND_WINDOW
  assert report_positions == list(range(1, REPORT_SEND_WINDOW + 1))
  assert socket.max_active_sends == 1
  assert socket.closed == []
  assert all(code not in {1006, 1011} for code, _ in socket.closed)
  assert runtime._fatal_market_data_error is None
  assert runtime._fatal_trading_error is None
  assert runtime.journal.stats()["pending_reports"] == REPORT_SEND_WINDOW
