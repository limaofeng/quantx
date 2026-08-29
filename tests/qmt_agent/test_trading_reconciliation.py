from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_qmt_agent import runtime as runtime_module
from quantx_qmt_agent.credentials import DeviceConfiguration
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.runtime import AgentRuntime, _FatalTradingRecoveryError


def _bare_live_runtime(broker) -> AgentRuntime:
  runtime = object.__new__(AgentRuntime)
  runtime.mode = "live"
  runtime.broker = broker
  runtime._trading_reconciliation_required = False
  runtime._trading_reconciliation_snapshot_id = None
  runtime._trading_reconciliation_snapshot_generation = None
  runtime._trading_recovery_started_monotonic = None
  runtime._trading_recovery_reason = ""
  runtime._trading_readiness_failed = False
  runtime._trading_ready_cache = False
  runtime._trading_connection_generation_cache = 0
  return runtime


def test_reconciliation_reentry_does_not_reset_recovery_deadline(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  clock = {"value": 10.0}
  gate_closes: list[None] = []
  broker = SimpleNamespace(
    require_trading_reconciliation=lambda: gate_closes.append(None)
  )
  runtime = _bare_live_runtime(broker)
  monkeypatch.setattr(
    runtime_module.time,
    "monotonic",
    lambda: clock["value"],
  )

  runtime._begin_trading_reconciliation("control_session_connected")
  clock["value"] = 80.0
  runtime._begin_trading_reconciliation("xttrading_unavailable")

  assert runtime._trading_recovery_started_monotonic == 10.0
  assert runtime._trading_recovery_reason == "xttrading_unavailable"
  assert len(gate_closes) == 2


def test_active_market_upload_does_not_mask_trading_recovery_deadline() -> None:
  runtime = _bare_live_runtime(SimpleNamespace())
  runtime._trading_reconciliation_required = True
  runtime._trading_recovery_started_monotonic = 1.0
  active = SimpleNamespace(task=SimpleNamespace(done=lambda: False))
  runtime._market_upload_tasks = {"request-1": active}

  with pytest.raises(_FatalTradingRecoveryError):
    runtime._raise_if_trading_recovery_expired()


@pytest.mark.asyncio
async def test_market_upload_keeps_live_order_gate_open() -> None:
  gate_closes: list[None] = []
  runtime = _bare_live_runtime(
    SimpleNamespace(
      require_trading_reconciliation=lambda: gate_closes.append(None)
    )
  )
  runtime._set_trading_ready = lambda ready: setattr(
    runtime,
    "_trading_ready_cache",
    ready,
  )
  runtime._trading_ready_cache = True
  started = asyncio.Event()
  release = asyncio.Event()

  async def handle(_envelope) -> None:
    started.set()
    await release.wait()

  runtime._handle_market_data_request = handle
  task = runtime._market_upload_task(
    AgentEnvelope(
      message_type=AgentMessageType.MARKET_DATA_REQUEST,
      payload={"request_id": "repair-batch-1", "operation": "bars"}
    )
  )
  await started.wait()

  assert runtime._trading_ready_cache is True
  assert runtime._trading_readiness_failed is False
  assert runtime._requires_trading_reconciliation() is False
  assert runtime._trading_recovery_started_monotonic is None
  assert gate_closes == []

  release.set()
  await task
  await asyncio.sleep(0)
  runtime.stop()


@pytest.mark.asyncio
async def test_history_qos_resumes_only_after_two_healthy_cycles(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  runtime = object.__new__(AgentRuntime)
  runtime._history_workload = "idle"
  runtime._history_workload_reason = ""
  reasons = iter(["TRADING_RECONCILING", "", ""])
  sleeps: list[tuple[str, str]] = []

  runtime._history_qos_block_reason = lambda: next(reasons)

  async def qos_sleep(seconds: float) -> None:
    assert seconds == runtime_module.HISTORY_QOS_CHECK_SECONDS
    sleeps.append(
      (runtime._history_workload, runtime._history_workload_reason)
    )

  monkeypatch.setattr(runtime_module.asyncio, "sleep", qos_sleep)

  await runtime._wait_for_history_dispatch()

  assert sleeps == [
    ("paused", "TRADING_RECONCILING"),
    ("paused", "TRADING_RECONCILING"),
  ]
  assert runtime._history_workload == "running"
  assert runtime._history_workload_reason == ""


def test_history_qos_pauses_immediately_when_control_session_disconnects() -> None:
  runtime = _bare_live_runtime(
    SimpleNamespace(is_market_data_ready=lambda: True)
  )
  runtime._ensure_market_upload_state()
  runtime._control_session_authenticated = False
  runtime._trading_ready_cache = True

  assert runtime._history_qos_block_reason() == "CONTROL_CONNECTION_UNHEALTHY"


@pytest.mark.asyncio
async def test_trade_command_does_not_block_control_receiver() -> None:
  runtime = object.__new__(AgentRuntime)
  runtime._ensure_market_upload_state()
  command_started = asyncio.Event()
  release_command = asyncio.Event()

  async def handle_command(_socket, _envelope) -> None:
    command_started.set()
    await release_command.wait()

  runtime._handle_command = handle_command
  worker = asyncio.create_task(
    runtime._command_request_loop(SimpleNamespace())
  )
  command = AgentEnvelope(
    message_type=AgentMessageType.COMMAND,
    payload={"command_kind": "PLACE_ORDER"},
  )
  await runtime._handle_message(None, command.model_dump_json())
  await asyncio.wait_for(command_started.wait(), timeout=0.2)

  heartbeat_id = "heartbeat-during-command"
  runtime._heartbeat_sent_monotonic[heartbeat_id] = time.monotonic()
  await asyncio.wait_for(
    runtime._handle_message(
      None,
      AgentEnvelope(
        message_type=AgentMessageType.HEARTBEAT_ACK,
        payload={"heartbeat_message_id": heartbeat_id},
      ).model_dump_json(),
    ),
    timeout=0.2,
  )

  assert heartbeat_id not in runtime._heartbeat_sent_monotonic
  assert runtime._active_command_count == 1

  release_command.set()
  await runtime._command_requests.join()
  worker.cancel()
  await asyncio.gather(worker, return_exceptions=True)


def test_stale_snapshot_ack_keeps_local_reconciliation_gate_closed() -> None:
  class Broker:
    generation = 2
    reconciled_generation = -1

    def require_trading_reconciliation(self) -> None:
      self.reconciled_generation = -1

    def mark_trading_reconciled(self, generation: int) -> bool:
      if generation != self.generation:
        return False
      self.reconciled_generation = generation
      return True

  broker = Broker()
  runtime = _bare_live_runtime(broker)
  runtime._trading_reconciliation_required = True
  runtime._trading_reconciliation_snapshot_id = "snapshot-1"
  runtime._trading_reconciliation_snapshot_generation = 1
  runtime._trading_recovery_started_monotonic = 10.0

  assert runtime._acknowledge_trading_reconciliation_snapshot("snapshot-1") is False
  assert runtime._requires_trading_reconciliation() is True
  assert runtime._trading_reconciliation_snapshot_id is None
  assert runtime._trading_reconciliation_snapshot_generation is None
  assert runtime._trading_recovery_started_monotonic == 10.0
  assert broker.reconciled_generation == -1


@pytest.mark.asyncio
async def test_complete_snapshot_ack_opens_only_its_captured_generation(
  tmp_path,
) -> None:
  class Broker:
    generation = 3
    reconciled_generation = -1

    def require_trading_reconciliation(self) -> None:
      self.reconciled_generation = -1

    def capture_full_snapshot(self):
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
        self.generation,
      )

    def mark_trading_reconciled(self, generation: int) -> bool:
      if generation != self.generation:
        return False
      self.reconciled_generation = generation
      return True

  broker = Broker()
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="device-1",
    ),
    device_secret="unused",
    mode="live",
    allowed_accounts={"account-1"},
    broker=broker,
    journal=journal,
    market_spool_base_directory=tmp_path,
  )

  message_id = await runtime._queue_full_snapshot(reconciliation=True)
  report = AgentEnvelope.model_validate_json(journal.pending_reports()[0])

  assert report.message_id == message_id
  assert runtime._trading_reconciliation_snapshot_generation == 3
  assert runtime._acknowledge_trading_reconciliation_snapshot(message_id)
  assert broker.reconciled_generation == 3
  assert runtime._requires_trading_reconciliation() is False


@pytest.mark.asyncio
async def test_repair_preparation_market_ingress_and_reconciliation_run_concurrently(
  tmp_path,
) -> None:
  history_started = threading.Event()
  history_release = threading.Event()

  class Broker:
    generation = 4
    reconciled_generation = -1

    @staticmethod
    def iter_market_data(_payload):
      history_started.set()
      history_release.wait(timeout=2)
      return iter(
        [
          {
            "code": "000001.SZ",
            "period": "1d",
            "time": 1_735_776_000_000,
            "close": 10.0,
          }
        ]
      )

    def require_trading_reconciliation(self) -> None:
      self.reconciled_generation = -1

    def capture_full_snapshot(self):
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
        self.generation,
      )

    def mark_trading_reconciled(self, generation: int) -> bool:
      if generation != self.generation:
        return False
      self.reconciled_generation = generation
      return True

  broker = Broker()
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="device-concurrent",
    ),
    device_secret="unused",
    mode="live",
    allowed_accounts={"account-1"},
    broker=broker,
    journal=LocalJournal(tmp_path / "journal-concurrent.sqlite3"),
    market_spool_base_directory=tmp_path,
  )
  runtime._session_loop = asyncio.get_running_loop()
  runtime._set_trading_ready(True)
  runtime._trading_connection_generation_cache = broker.generation
  preparation = asyncio.create_task(
    runtime._prepared_market_data_chunks(
      "repair-market-data-batch",
      {
        "request_id": "repair-market-data-batch",
        "operation": "bars",
      },
    )
  )
  assert await asyncio.to_thread(history_started.wait, 1)

  def emit_live_market_events() -> None:
    for sequence in range(5):
      runtime._enqueue_market_event({"sequence": sequence})

  await asyncio.to_thread(emit_live_market_events)
  snapshot_id = await asyncio.wait_for(
    runtime._queue_full_snapshot(reconciliation=True),
    timeout=0.5,
  )
  await asyncio.sleep(0)

  assert preparation.done() is False
  assert runtime._market_events.qsize() == 5
  assert runtime._acknowledge_trading_reconciliation_snapshot(snapshot_id)
  assert runtime._requires_trading_reconciliation() is False
  assert broker.reconciled_generation == broker.generation

  history_release.set()
  await asyncio.wait_for(preparation, timeout=1)
  runtime.stop()


@pytest.mark.asyncio
async def test_native_trading_timeout_is_fatal_without_event_loop_block(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  release = threading.Event()

  class Broker:
    @staticmethod
    def ensure_trading_ready() -> bool:
      release.wait()
      return True

    @staticmethod
    def trading_connection_generation() -> int:
      return 0

    @staticmethod
    def is_trading_ready() -> bool:
      raise AssertionError("event loop must use its readiness cache")

    @staticmethod
    def require_trading_reconciliation() -> None:
      return None

  runtime = _bare_live_runtime(Broker())
  monkeypatch.setattr(
    runtime_module,
    "XTTRADING_RECONNECT_TIMEOUT_SECONDS",
    0.01,
  )

  try:
    with pytest.raises(
      _FatalTradingRecoveryError,
      match="readiness timed out",
    ):
      await runtime._ensure_trading_ready()
  finally:
    release.set()

  assert runtime._trading_ready_cache is False
  assert runtime._trading_readiness_failed is True
  assert runtime._requires_trading_reconciliation() is True


@pytest.mark.asyncio
async def test_periodic_live_snapshot_preserves_ready_reconciliation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class StopHeartbeat(Exception):
    pass

  runtime = _bare_live_runtime(SimpleNamespace())
  snapshot_modes: list[bool] = []
  heartbeat_statuses: list[str] = []

  async def queue_snapshot(*, reconciliation: bool = False) -> str:
    snapshot_modes.append(reconciliation)
    runtime._trading_reconciliation_required = reconciliation
    return "snapshot-1"

  async def checkpoint(_socket, *, status: str) -> None:
    heartbeat_statuses.append(status)
    if len(heartbeat_statuses) == 1:
      runtime._heartbeat_wakeup.set()
    if len(heartbeat_statuses) == 2:
      raise StopHeartbeat

  monkeypatch.setattr(runtime, "_queue_full_snapshot", queue_snapshot)
  monkeypatch.setattr(runtime, "_heartbeat_checkpoint", checkpoint)
  runtime._ensure_market_upload_state()
  runtime._heartbeat_wakeup.set()

  with pytest.raises(StopHeartbeat):
    await runtime._heartbeat_loop(SimpleNamespace())

  assert snapshot_modes == [False]
  assert heartbeat_statuses == ["READY", "READY"]
