from __future__ import annotations

import asyncio
import queue
import threading
import time
from types import SimpleNamespace

import pytest
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_qmt_agent import runtime as runtime_module
from quantx_qmt_agent.broker import LiveBroker, _LiveReportSink
from quantx_qmt_agent.credentials import DeviceConfiguration
from quantx_qmt_agent.emergency import EmergencyStopStore
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.miniqmt.local_agent import MiniQmtLocalAgent
from quantx_qmt_agent.miniqmt.trading.trading_manager import XTTradingManager
from quantx_qmt_agent.runtime import AgentRuntime, _FatalTradingRecoveryError


def _bare_live_runtime(broker) -> AgentRuntime:
  runtime = object.__new__(AgentRuntime)
  runtime.mode = "live"
  runtime.broker = broker
  runtime._trading_reconciliation_required = False
  runtime._trading_reconciliation_snapshot_id = None
  runtime._trading_reconciliation_snapshot_generation = None
  runtime._trading_reconciliation_snapshot_callback_failure_generation = None
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


@pytest.mark.parametrize("mode", ("live", "data-only"))
def test_history_qos_pauses_immediately_when_control_session_disconnects(
  mode: str,
) -> None:
  runtime = object.__new__(AgentRuntime)
  runtime.mode = mode
  runtime._control_session_authenticated = False

  assert runtime._history_qos_block_reason() == "CONTROL_CONNECTION_UNHEALTHY"


@pytest.mark.parametrize("mode", ("live", "data-only"))
@pytest.mark.parametrize("status", ("OFFLINE", "SYNCING", "RESYNC"))
def test_history_qos_pauses_while_realtime_market_recovers(
  mode: str,
  status: str,
) -> None:
  runtime = object.__new__(AgentRuntime)
  runtime.mode = mode
  runtime._control_session_authenticated = True
  runtime._market_stream_status = status

  assert runtime._history_qos_block_reason() == "MARKET_STREAM_NOT_READY"


@pytest.mark.asyncio
async def test_data_only_history_waits_after_control_detach(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  runtime = object.__new__(AgentRuntime)
  runtime.mode = "data-only"
  runtime._control_session_authenticated = False
  runtime._market_stream_status = "READY"
  runtime._market_data_ready_cache = True
  runtime._history_workload = "running"
  runtime._history_workload_reason = ""
  sleep_states: list[tuple[str, str]] = []

  async def qos_sleep(seconds: float) -> None:
    assert seconds == runtime_module.HISTORY_QOS_CHECK_SECONDS
    sleep_states.append(
      (runtime._history_workload, runtime._history_workload_reason)
    )
    if len(sleep_states) == 1:
      runtime._control_session_authenticated = True

  monkeypatch.setattr(runtime_module.asyncio, "sleep", qos_sleep)

  await runtime._wait_for_history_dispatch()

  assert sleep_states == [
    ("paused", "CONTROL_CONNECTION_UNHEALTHY"),
    ("paused", "CONTROL_CONNECTION_UNHEALTHY"),
  ]
  assert runtime._history_workload == "running"
  assert runtime._history_workload_reason == ""


@pytest.mark.parametrize(
  ("reset", "subscription_ready", "subscription_active", "locked", "reason"),
  (
    (True, True, True, False, "MARKET_STREAM_NATIVE_RESET"),
    (False, False, False, False, "MARKET_STREAM_SUBSCRIPTION_PENDING"),
    (False, True, True, True, "XTDATA_CONTROL_PENDING"),
  ),
)
def test_history_qos_prioritizes_native_market_control(
  reset: bool,
  subscription_ready: bool,
  subscription_active: bool,
  locked: bool,
  reason: str,
) -> None:
  runtime = object.__new__(AgentRuntime)
  runtime.mode = "data-only"
  runtime._control_session_authenticated = True
  runtime._market_stream_status = "READY"
  runtime._whole_market_native_reset = SimpleNamespace(is_set=lambda: reset)
  runtime._whole_market_subscription_ready = SimpleNamespace(
    is_set=lambda: subscription_ready
  )
  runtime._whole_market_subscription_active = subscription_active
  runtime._xtdata_access_lock = SimpleNamespace(locked=lambda: locked)

  assert runtime._history_qos_block_reason() == reason


@pytest.mark.asyncio
async def test_heartbeat_uses_cached_xtdata_readiness_without_native_lock(
  tmp_path,
) -> None:
  native_probe_calls = 0

  class Broker:
    @staticmethod
    def ensure_market_data_ready() -> bool:
      return True

    @staticmethod
    def is_market_data_ready() -> bool:
      nonlocal native_probe_calls
      native_probe_calls += 1
      raise AssertionError("event loop attempted a native XTData readiness probe")

  class Socket:
    sent: list[str] = []

    async def send(self, value: str) -> None:
      self.sent.append(value)

  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="cached-readiness",
    ),
    device_secret="unused",
    mode="data-only",
    allowed_accounts=set(),
    broker=Broker(),
    journal=LocalJournal(tmp_path / "cached-readiness.sqlite3"),
    market_spool_base_directory=tmp_path,
  )
  runtime._set_market_data_ready(True)
  socket = Socket()

  await asyncio.wait_for(
    runtime._send_heartbeat(socket, status="READY"),
    timeout=0.2,
  )

  assert native_probe_calls == 0
  assert len(socket.sent) == 1
  runtime.stop()


@pytest.mark.asyncio
async def test_later_heartbeat_ack_retires_older_lost_ack() -> None:
  runtime = object.__new__(AgentRuntime)
  runtime._ensure_market_upload_state()
  runtime._heartbeat_sent_monotonic = {
    "older-lost": 1.0,
    "acknowledged": 2.0,
    "newer": 3.0,
  }

  await runtime._handle_message(
    None,
    AgentEnvelope(
      message_type=AgentMessageType.HEARTBEAT_ACK,
      payload={"heartbeat_message_id": "acknowledged"},
    ).model_dump_json(),
  )

  assert runtime._heartbeat_sent_monotonic == {"newer": 3.0}


@pytest.mark.asyncio
async def test_trade_command_does_not_block_control_receiver() -> None:
  runtime = object.__new__(AgentRuntime)
  runtime._ensure_market_upload_state()
  runtime._broker_ready.set()
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


@pytest.mark.asyncio
async def test_dispatcher_submits_cancel_while_normal_command_is_active() -> None:
  runtime = object.__new__(AgentRuntime)
  runtime._ensure_market_upload_state()
  runtime._broker_ready.set()
  active_started = asyncio.Event()
  release_active = asyncio.Event()
  handled: list[str] = []

  async def handle_command(_socket, envelope) -> None:
    kind = str(envelope.payload.get("command_kind") or "")
    handled.append(kind)
    if kind == "PLACE_ORDER:first":
      active_started.set()
      await release_active.wait()

  runtime._handle_command = handle_command
  worker = asyncio.create_task(
    runtime._command_request_loop(SimpleNamespace())
  )
  for kind, message_type in (
    ("PLACE_ORDER:first", AgentMessageType.COMMAND),
    ("PLACE_ORDER:second", AgentMessageType.COMMAND),
    ("CANCEL_ORDER", AgentMessageType.CANCEL_COMMAND),
  ):
    await runtime._handle_message(
      None,
      AgentEnvelope(
        message_type=message_type,
        payload={"command_kind": kind},
      ).model_dump_json(),
    )
    if kind == "PLACE_ORDER:first":
      await asyncio.wait_for(active_started.wait(), timeout=0.2)

  release_active.set()
  await asyncio.wait_for(runtime._command_requests.join(), timeout=0.2)
  worker.cancel()
  await asyncio.gather(worker, return_exceptions=True)

  assert handled[0] == "PLACE_ORDER:first"
  assert set(handled) == {
    "PLACE_ORDER:first",
    "PLACE_ORDER:second",
    "CANCEL_ORDER",
  }


@pytest.mark.asyncio
async def test_real_command_dispatcher_queues_cancel_ahead_of_waiting_order(
  tmp_path,
) -> None:
  active_started = threading.Event()
  release_active = threading.Event()
  native_calls: list[str] = []

  class Broker:
    @staticmethod
    def ensure_market_data_ready() -> bool:
      return True

    @staticmethod
    def execute(payload: dict) -> dict:
      native_calls.append(str(payload["command_kind"]))
      return {"accepted": True, "reason": "accepted", "reports": []}

  class Socket:
    async def send(self, _serialized: str) -> None:
      return None

  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="dispatcher-priority",
    ),
    device_secret="unused",
    mode="live",
    allowed_accounts={"account-1"},
    broker=Broker(),
    journal=LocalJournal(tmp_path / "dispatcher.sqlite3"),
    market_spool_base_directory=tmp_path,
  )
  runtime._set_market_data_ready(True)
  runtime._set_trading_ready(True)
  runtime._market_stream_status = "READY"
  runtime._trading_reconciliation_required = False
  socket = Socket()

  def active_snapshot() -> None:
    native_calls.append("active-snapshot")
    active_started.set()
    release_active.wait(timeout=2)

  active = asyncio.create_task(
    runtime._run_native_xttrading(
      "active-snapshot",
      active_snapshot,
      timeout=1,
      priority=runtime_module.XTTRADING_PRIORITY_SNAPSHOT,
    )
  )
  assert await asyncio.to_thread(active_started.wait, 1)
  dispatcher = asyncio.create_task(runtime._command_request_loop(socket))
  expires_at = "2099-01-01T00:00:00+00:00"
  place = AgentEnvelope(
    message_type=AgentMessageType.COMMAND,
    payload={
      "command_kind": "PLACE_ORDER",
      "client_order_id": "client-place",
      "instance_id": "instance-1",
      "account_id": "account-1",
      "execution_mode": "live",
      "instrument_code": "600000.SH",
      "side": "BUY",
      "order_type": "LIMIT",
      "limit_price": "10.00",
      "volume": 100,
      "bucket": "core",
      "risk_decision_id": "risk-1",
      "trace_id": "trace-place",
      "expires_at": expires_at,
    },
  )
  cancel = AgentEnvelope(
    message_type=AgentMessageType.CANCEL_COMMAND,
    payload={
      "command_kind": "CANCEL_ORDER",
      "client_order_id": "client-place",
      "account_id": "account-1",
      "execution_mode": "live",
      "broker_order_id": "broker-1",
      "trace_id": "trace-cancel",
      "expires_at": expires_at,
    },
  )

  await runtime._handle_message(socket, place.model_dump_json())
  for _ in range(100):
    if runtime.journal.stats()["processing_commands"] == 1:
      break
    await asyncio.sleep(0.005)
  assert runtime.journal.stats()["processing_commands"] == 1
  await runtime._handle_message(socket, cancel.model_dump_json())
  for _ in range(100):
    if (
      runtime.journal.stats()["processing_commands"] == 2
      and runtime._xttrading_worker.queued_calls >= 2
    ):
      break
    await asyncio.sleep(0.005)
  assert runtime.journal.stats()["processing_commands"] == 2
  assert runtime._xttrading_worker.queued_calls >= 2

  release_active.set()
  await asyncio.wait_for(runtime._command_requests.join(), timeout=1)
  await active
  dispatcher.cancel()
  await asyncio.gather(dispatcher, return_exceptions=True)

  assert native_calls == ["active-snapshot", "CANCEL_ORDER", "PLACE_ORDER"]
  runtime._xttrading_worker.close()


@pytest.mark.asyncio
async def test_partitioned_live_snapshot_restarts_after_interleaved_cancel(
  tmp_path,
) -> None:
  account_started = threading.Event()
  release_account = threading.Event()
  native_calls: list[str] = []

  class Manager:
    is_connected = True
    cancelled = False

    @staticmethod
    def query_account_status() -> int:
      return 0

    def get_account_info(self) -> dict:
      native_calls.append("account")
      account_started.set()
      release_account.wait(timeout=2)
      return {"cash": 100_000}

    @staticmethod
    def query_positions_snapshot() -> list[dict]:
      native_calls.append("positions")
      return []

    def get_orders(self, cancelable_only: bool = False) -> list[dict]:
      native_calls.append("cancelable-orders" if cancelable_only else "orders")
      if cancelable_only and self.cancelled:
        return []
      return [
        {
          "order_id": 12345,
          "order_status": 53 if self.cancelled else 50,
        }
      ]

    @staticmethod
    def get_trades() -> list[dict]:
      native_calls.append("trades")
      return []

    def cancel_order(self, order_id: int) -> bool:
      assert order_id == 12345
      native_calls.append("cancel")
      self.cancelled = True
      return True

  manager = Manager()
  broker = object.__new__(LiveBroker)
  broker.agents = {"account-1": MiniQmtLocalAgent(manager)}
  broker._trading_access_lock = threading.RLock()
  broker._trading_generation_lock = threading.Lock()
  broker._trading_connection_generation = 0
  broker._trading_mutation_generation = 0
  broker._trading_reconciled_generation = 0
  broker.ensure_trading_ready = lambda: True

  class Socket:
    async def send(self, _serialized: str) -> None:
      return None

  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="partitioned-snapshot-priority",
    ),
    device_secret="unused",
    mode="live",
    allowed_accounts={"account-1"},
    broker=broker,
    journal=LocalJournal(tmp_path / "partitioned-snapshot.sqlite3"),
    market_spool_base_directory=tmp_path,
  )
  runtime._trading_reconciliation_required = False
  snapshot_task = asyncio.create_task(
    runtime._queue_full_snapshot(reconciliation=True)
  )
  assert await asyncio.to_thread(account_started.wait, 1)
  cancel_task = asyncio.create_task(
    runtime._handle_command(
      Socket(),
      AgentEnvelope(
        message_type=AgentMessageType.CANCEL_COMMAND,
        payload={
          "command_kind": "CANCEL_ORDER",
          "client_order_id": "client-1",
          "account_id": "account-1",
          "execution_mode": "live",
          "broker_order_id": "12345",
          "trace_id": "trace-cancel",
          "expires_at": "2099-01-01T00:00:00+00:00",
        },
      ),
    )
  )
  for _ in range(100):
    if runtime._xttrading_worker.queued_calls >= 1:
      break
    await asyncio.sleep(0.005)
  assert runtime._xttrading_worker.queued_calls >= 1

  release_account.set()
  snapshot_message_id, _ = await asyncio.gather(snapshot_task, cancel_task)

  assert native_calls[-5:] == [
    "account",
    "positions",
    "orders",
    "cancelable-orders",
    "trades",
  ]
  assert native_calls.count("account") == 2
  assert native_calls.index("cancel") < len(native_calls) - 5
  assert broker.trading_mutation_generation() == 1
  reports = [
    AgentEnvelope.model_validate_json(serialized)
    for serialized in runtime.journal.pending_reports()
  ]
  snapshot_report = next(
    report for report in reports if report.message_id == snapshot_message_id
  )
  snapshot = snapshot_report.payload
  assert snapshot["is_complete"] is True
  assert snapshot["orders"][0]["effective_order_status"] == "CANCELLED"
  runtime.stop()


@pytest.mark.asyncio
async def test_durable_callback_fence_prevents_stale_snapshot_commit(
  tmp_path,
) -> None:
  trades_started = threading.Event()
  release_trades = threading.Event()

  class Manager:
    is_connected = True

    def __init__(self) -> None:
      self.account_calls = 0
      self.trade_calls = 0
      self.orders: list[dict] = []

    @staticmethod
    def query_account_status() -> int:
      return 0

    def get_account_info(self) -> dict:
      self.account_calls += 1
      return {"cash": 100_000}

    @staticmethod
    def query_positions_snapshot() -> list[dict]:
      return []

    def get_orders(self, cancelable_only: bool = False) -> list[dict]:
      _ = cancelable_only
      return list(self.orders)

    def get_trades(self) -> list[dict]:
      self.trade_calls += 1
      if self.trade_calls == 1:
        trades_started.set()
        release_trades.wait(timeout=2)
      return []

  journal = LocalJournal(tmp_path / "callback-snapshot-fence.sqlite3")
  manager = Manager()
  agent = MiniQmtLocalAgent(manager)
  broker = object.__new__(LiveBroker)
  broker.agents = {"account-1": agent}
  broker._trading_journal = journal
  broker._trading_access_lock = threading.RLock()
  broker._trading_generation_lock = threading.Lock()
  broker._trading_connection_generation = 0
  broker._trading_mutation_generation = 0
  broker._trading_reconciled_generation = 0
  broker.ensure_trading_ready = lambda: True
  sink = _LiveReportSink(
    "account-1",
    journal,
    on_callback_observed=broker._advance_trading_mutation,
  )
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="callback-snapshot-fence",
    ),
    device_secret="unused",
    mode="live",
    allowed_accounts={"account-1"},
    broker=broker,
    journal=journal,
    market_spool_base_directory=tmp_path,
  )
  runtime._trading_reconciliation_required = False
  snapshot_task = asyncio.create_task(
    runtime._queue_full_snapshot(reconciliation=True)
  )
  assert await asyncio.to_thread(trades_started.wait, 1)

  manager.orders = [
    {
      "order_id": 777,
      "stock_code": "600000.SH",
      "order_status": 50,
      "order_volume": 100,
      "traded_volume": 0,
      "order_remark": "qx:callback-order",
    }
  ]
  prepared = sink.prepare_callback(
    "order",
    SimpleNamespace(
      account_id="account-1",
      order_id=777,
      stock_code="600000.SH",
      order_status=50,
      order_volume=100,
      traded_volume=0,
      order_remark="qx:callback-order",
    ),
  )
  sink.mark_callback_observed()
  sink.persist_prepared_callback(prepared)
  release_trades.set()

  snapshot_message_id = await snapshot_task
  reports = [
    AgentEnvelope.model_validate_json(serialized)
    for serialized in journal.pending_reports()
  ]
  assert [report.message_type for report in reports] == [
    AgentMessageType.ORDER_REPORT,
    AgentMessageType.DELTA_REPORT,
  ]
  assert reports[1].message_id == snapshot_message_id
  assert reports[1].payload["orders"][0]["order_id"] == 777
  assert manager.account_calls == 2
  assert broker.trading_mutation_generation() == 1
  runtime.stop()


def test_callback_after_final_snapshot_fence_is_journaled_after_snapshot(
  tmp_path,
) -> None:
  journal = LocalJournal(tmp_path / "callback-after-snapshot-fence.sqlite3")
  broker = object.__new__(LiveBroker)
  broker.agents = {}
  broker._trading_generation_lock = threading.Lock()
  broker._trading_connection_generation = 0
  broker._trading_mutation_generation = 0
  broker._trading_reconciled_generation = -1
  runtime = _bare_live_runtime(broker)
  runtime.journal = journal
  snapshot_commit_started = threading.Event()
  callback_observed = threading.Event()
  callback_failures: list[BaseException] = []
  original_persist = runtime._persist_full_snapshot_report

  def persist_snapshot(message_id: str, serialized: str) -> None:
    # _persist_captured_full_snapshot holds journal.lock here and has already
    # passed the connection+mutation+callback-gap fence. A later callback may
    # advance the generations, but its durable row must follow this snapshot.
    snapshot_commit_started.set()
    assert callback_observed.wait(timeout=1)
    original_persist(message_id, serialized)

  runtime._persist_full_snapshot_report = persist_snapshot
  sink = _LiveReportSink(
    "account-1",
    journal,
    on_callback_observed=broker._advance_trading_mutation,
  )
  prepared = sink.prepare_callback(
    "order",
    SimpleNamespace(
      account_id="account-1",
      order_id=888,
      stock_code="600000.SH",
      order_status=48,
      order_volume=100,
      traded_volume=0,
      order_remark="qx:after-fence",
    ),
  )

  def persist_callback() -> None:
    try:
      assert snapshot_commit_started.wait(timeout=1)
      sink.mark_callback_observed()
      callback_observed.set()
      sink.persist_prepared_callback(prepared)
    except BaseException as exc:
      callback_failures.append(exc)

  callback_thread = threading.Thread(target=persist_callback)
  callback_thread.start()
  snapshot_message_id = "snapshot-after-fence"
  persisted = runtime._persist_captured_full_snapshot(
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
    snapshot_message_id,
    0,
    0,
    0,
  )
  callback_thread.join(timeout=1)

  assert callback_thread.is_alive() is False
  assert callback_failures == []
  reports = [
    AgentEnvelope.model_validate_json(serialized)
    for serialized in journal.pending_reports()
  ]
  assert [report.message_type for report in reports] == [
    AgentMessageType.DELTA_REPORT,
    AgentMessageType.ORDER_REPORT,
  ]
  assert reports[0].message_id == snapshot_message_id
  assert persisted["snapshot_id"] == snapshot_message_id
  assert broker.trading_mutation_generation() == 1
  assert broker.mark_trading_reconciled(0, 0) is True


def test_status_callback_is_linearized_after_snapshot_commit(tmp_path) -> None:
  journal = LocalJournal(tmp_path / "status-after-snapshot-fence.sqlite3")
  broker = object.__new__(LiveBroker)
  broker.agents = {}
  broker._trading_generation_lock = threading.Lock()
  broker._trading_connection_generation = 0
  broker._trading_mutation_generation = 0
  broker._trading_reconciled_generation = -1
  runtime = _bare_live_runtime(broker)
  runtime.journal = journal
  snapshot_commit_started = threading.Event()
  status_callback_started = threading.Event()
  callback_failures: list[BaseException] = []
  original_persist = runtime._persist_full_snapshot_report

  def persist_snapshot(message_id: str, serialized: str) -> None:
    snapshot_commit_started.set()
    assert status_callback_started.wait(timeout=1)
    assert broker.trading_mutation_generation() == 0
    original_persist(message_id, serialized)

  runtime._persist_full_snapshot_report = persist_snapshot
  sink = _LiveReportSink(
    "account-1",
    journal,
    on_callback_observed=broker._advance_trading_mutation,
  )

  def observe_status() -> None:
    try:
      assert snapshot_commit_started.wait(timeout=1)
      status_callback_started.set()
      sink.mark_status_observed()
    except BaseException as exc:
      callback_failures.append(exc)

  callback_thread = threading.Thread(target=observe_status)
  callback_thread.start()
  snapshot_message_id = "snapshot-before-status"
  runtime._persist_captured_full_snapshot(
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
    snapshot_message_id,
    0,
    0,
    0,
  )
  callback_thread.join(timeout=1)

  assert callback_thread.is_alive() is False
  assert callback_failures == []
  reports = [
    AgentEnvelope.model_validate_json(serialized)
    for serialized in journal.pending_reports()
  ]
  assert [report.message_type for report in reports] == [
    AgentMessageType.DELTA_REPORT,
  ]
  assert reports[0].message_id == snapshot_message_id
  assert broker.trading_mutation_generation() == 1


def test_snapshot_ack_cannot_clear_callback_gap_created_after_commit(
  tmp_path,
) -> None:
  journal = LocalJournal(tmp_path / "callback-gap-after-snapshot.sqlite3")
  manager = object.__new__(XTTradingManager)
  manager._callback_queue = queue.Queue(maxsize=1)
  manager._callback_state_lock = threading.Lock()
  manager._callback_pipeline_healthy = True
  manager._callback_pipeline_error = ""
  manager._callback_failure_generation = 0
  manager._callback_recovery_pending = False
  manager._callback_recovery_generation = None
  manager._callback_write_inflight = False
  manager._callback_accepting = True

  broker = object.__new__(LiveBroker)
  broker.agents = {
    "account-1": SimpleNamespace(trading_manager=manager),
  }
  broker._trading_generation_lock = threading.Lock()
  broker._trading_connection_generation = 0
  broker._trading_mutation_generation = 0
  broker._trading_reconciled_generation = -1
  manager.trading_service = _LiveReportSink(
    "account-1",
    journal,
    on_callback_observed=broker._advance_trading_mutation,
  )
  runtime = _bare_live_runtime(broker)
  runtime.journal = journal

  def snapshot(sequence: int) -> dict:
    return {
      "accounts": [],
      "positions_by_account": {},
      "orders": [],
      "trades": [],
      "sequence": sequence,
      "is_complete": True,
      "unavailable_accounts": [],
      "section_completeness_by_account": {},
      "mode": "live",
    }

  old_snapshot_id = "snapshot-before-callback-gap"
  runtime._persist_captured_full_snapshot(
    snapshot(1),
    old_snapshot_id,
    0,
    0,
    0,
  )
  runtime._trading_reconciliation_required = True
  runtime._trading_reconciliation_snapshot_id = old_snapshot_id
  runtime._trading_reconciliation_snapshot_generation = 0
  runtime._trading_reconciliation_snapshot_callback_failure_generation = 0

  assert manager.enqueue_durable_callback("unsupported", object()) is False
  assert manager.callback_failure_generation() == 1
  assert manager.callback_pipeline_healthy() is False
  assert runtime._acknowledge_trading_reconciliation_snapshot(
    old_snapshot_id
  ) is False
  assert runtime._requires_trading_reconciliation() is True
  assert broker._trading_reconciled_generation == -1
  assert manager.callback_pipeline_healthy() is False

  fresh_snapshot_id = "snapshot-after-callback-gap"
  runtime._persist_captured_full_snapshot(
    snapshot(2),
    fresh_snapshot_id,
    0,
    1,
    1,
  )
  runtime._trading_reconciliation_snapshot_id = fresh_snapshot_id
  runtime._trading_reconciliation_snapshot_generation = 0
  runtime._trading_reconciliation_snapshot_callback_failure_generation = 1

  assert runtime._acknowledge_trading_reconciliation_snapshot(fresh_snapshot_id)
  assert runtime._requires_trading_reconciliation() is False
  assert broker._trading_reconciled_generation == 0
  assert manager.callback_pipeline_healthy() is True


@pytest.mark.asyncio
async def test_emergency_stop_bypasses_market_and_reconciliation_gates(
  tmp_path,
) -> None:
  store = EmergencyStopStore(tmp_path / "emergency-stop.json")
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="emergency-during-reconciliation",
    ),
    device_secret="unused",
    mode="live",
    allowed_accounts={"account-1"},
    broker=SimpleNamespace(),
    journal=LocalJournal(tmp_path / "emergency-command.sqlite3"),
    emergency_stop=store,
    market_spool_base_directory=tmp_path,
  )
  runtime._trading_reconciliation_required = True
  runtime._set_market_stream_status("OFFLINE")

  class Socket:
    def __init__(self) -> None:
      self.sent: list[AgentEnvelope] = []

    async def send(self, serialized: str) -> None:
      self.sent.append(AgentEnvelope.model_validate_json(serialized))

  socket = Socket()
  envelope = AgentEnvelope(
    message_type=AgentMessageType.COMMAND,
    payload={
      "command_kind": "EMERGENCY_STOP",
      "client_order_id": "emergency-1",
      "account_id": "account-1",
      "reason": "operator emergency while reconciling",
      "expires_at": "2099-01-01T00:00:00+00:00",
    },
  )

  await runtime._handle_command(socket, envelope)

  assert store.status()["active"] is True
  ack = next(
    item
    for item in socket.sent
    if item.message_type is AgentMessageType.COMMAND_ACK
  )
  assert ack.payload["accepted"] is True
  assert ack.payload["reason"] == "local_emergency_stop_activated"
  runtime.stop()


@pytest.mark.asyncio
async def test_partitioned_snapshot_rejects_mixed_connection_generations() -> None:
  class Broker:
    generation = 1

    def capture_full_snapshot_partition(self, partition: str):
      if partition == "positions":
        self.generation += 1
      return {}, self.generation

    @staticmethod
    def assemble_full_snapshot_partitions(_partitions, _generation):
      raise AssertionError("mixed-generation sections must not be assembled")

    def trading_connection_generation(self) -> int:
      return self.generation

  runtime = _bare_live_runtime(Broker())

  with pytest.raises(RuntimeError, match="generation changed between"):
    await runtime._capture_full_snapshot(reconciliation=True)

  runtime._xttrading_worker.close()


@pytest.mark.asyncio
async def test_native_cancel_overtakes_queued_snapshot() -> None:
  runtime = _bare_live_runtime(SimpleNamespace())
  active_started = threading.Event()
  release_active = threading.Event()
  calls: list[str] = []

  def active() -> str:
    calls.append("active")
    active_started.set()
    release_active.wait(timeout=2)
    return "active"

  def snapshot() -> str:
    calls.append("snapshot")
    return "snapshot"

  def cancel() -> str:
    calls.append("cancel")
    return "cancel"

  active_task = asyncio.create_task(
    runtime._run_native_xttrading(
      "active-order",
      active,
      timeout=1,
      priority=runtime_module.XTTRADING_PRIORITY_ORDER,
    )
  )
  assert await asyncio.to_thread(active_started.wait, 1)
  snapshot_task = asyncio.create_task(
    runtime._run_native_xttrading(
      "snapshot",
      snapshot,
      timeout=1,
      priority=runtime_module.XTTRADING_PRIORITY_SNAPSHOT,
    )
  )
  await asyncio.sleep(0)
  cancel_task = asyncio.create_task(
    runtime._run_native_xttrading(
      "cancel",
      cancel,
      timeout=1,
      priority=runtime_module.XTTRADING_PRIORITY_CANCEL,
    )
  )

  release_active.set()
  assert await asyncio.gather(active_task, cancel_task, snapshot_task) == [
    "active",
    "cancel",
    "snapshot",
  ]
  assert calls == ["active", "cancel", "snapshot"]
  runtime._xttrading_worker.close()


@pytest.mark.asyncio
async def test_native_timeout_starts_after_priority_queue_wait() -> None:
  runtime = _bare_live_runtime(SimpleNamespace())
  active_started = threading.Event()
  release_active = threading.Event()

  def active() -> None:
    active_started.set()
    release_active.wait(timeout=2)

  active_task = asyncio.create_task(
    runtime._run_native_xttrading(
      "active",
      active,
      timeout=1,
    )
  )
  assert await asyncio.to_thread(active_started.wait, 1)
  queued = asyncio.create_task(
    runtime._run_native_xttrading(
      "queued",
      lambda: "done",
      timeout=0.02,
      priority=runtime_module.XTTRADING_PRIORITY_CANCEL,
    )
  )
  await asyncio.sleep(0.05)
  assert not queued.done()

  release_active.set()
  await active_task
  assert await queued == "done"
  runtime._xttrading_worker.close()


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_create_concurrent_native_call() -> None:
  runtime = _bare_live_runtime(SimpleNamespace())
  active_started = threading.Event()
  release_active = threading.Event()
  second_started = threading.Event()

  def active() -> None:
    active_started.set()
    release_active.wait(timeout=2)

  active_task = asyncio.create_task(
    runtime._run_native_xttrading("active", active, timeout=1)
  )
  assert await asyncio.to_thread(active_started.wait, 1)
  active_task.cancel()
  await asyncio.gather(active_task, return_exceptions=True)

  second_task = asyncio.create_task(
    runtime._run_native_xttrading(
      "second",
      lambda: second_started.set(),
      timeout=1,
      priority=runtime_module.XTTRADING_PRIORITY_CANCEL,
    )
  )
  await asyncio.sleep(0.05)
  assert second_started.is_set() is False

  release_active.set()
  await second_task
  assert second_started.is_set() is True
  runtime._xttrading_worker.close()


@pytest.mark.asyncio
async def test_cancelled_native_waiter_keeps_independent_watchdog_armed() -> None:
  runtime = _bare_live_runtime(SimpleNamespace())
  runtime._ensure_market_upload_state()
  runtime._runtime_loop = asyncio.get_running_loop()
  active_started = threading.Event()
  release_active = threading.Event()

  def active() -> None:
    active_started.set()
    release_active.wait(timeout=2)

  active_task = asyncio.create_task(
    runtime._run_native_xttrading("hung-after-disconnect", active, timeout=0.05)
  )
  assert await asyncio.to_thread(active_started.wait, 1)
  active_task.cancel()
  await asyncio.gather(active_task, return_exceptions=True)

  await asyncio.wait_for(runtime._fatal_trading_event.wait(), timeout=0.5)
  assert isinstance(
    runtime._fatal_trading_error,
    runtime_module._FatalTradingRecoveryError,
  )
  assert "hung-after-disconnect" in str(runtime._fatal_trading_error)

  release_active.set()
  runtime._xttrading_worker.close()


def test_stale_snapshot_ack_keeps_local_reconciliation_gate_closed() -> None:
  class Broker:
    generation = 2
    reconciled_generation = -1

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

  broker = Broker()
  runtime = _bare_live_runtime(broker)
  runtime._trading_reconciliation_required = True
  runtime._trading_reconciliation_snapshot_id = "snapshot-1"
  runtime._trading_reconciliation_snapshot_generation = 1
  runtime._trading_reconciliation_snapshot_callback_failure_generation = 0
  runtime._trading_recovery_started_monotonic = 10.0

  assert runtime._acknowledge_trading_reconciliation_snapshot("snapshot-1") is False
  assert runtime._requires_trading_reconciliation() is True
  assert runtime._trading_reconciliation_snapshot_id is None
  assert runtime._trading_reconciliation_snapshot_generation is None
  assert runtime._trading_recovery_started_monotonic == 10.0
  assert broker.reconciled_generation == -1


def test_snapshot_ack_cannot_open_gate_with_indeterminate_local_command(
  tmp_path,
) -> None:
  class Broker:
    reconciled = False

    def mark_trading_reconciled(
      self,
      _generation: int,
      _callback_failure_generation: int,
    ) -> bool:
      self.reconciled = True
      return True

  broker = Broker()
  journal = LocalJournal(tmp_path / "indeterminate.sqlite3")
  journal.begin_command(
    "cancel-crash",
    {
      "command_kind": "CANCEL_ORDER",
      "client_order_id": "client-1",
      "broker_order_id": "broker-1",
    },
  )
  runtime = _bare_live_runtime(broker)
  runtime.journal = journal
  runtime._trading_reconciliation_required = True
  runtime._trading_reconciliation_snapshot_id = "snapshot-1"
  runtime._trading_reconciliation_snapshot_generation = 1
  runtime._trading_reconciliation_snapshot_callback_failure_generation = 0
  runtime._trading_recovery_started_monotonic = 1.0

  assert runtime._acknowledge_trading_reconciliation_snapshot("snapshot-1") is False
  assert runtime._requires_trading_reconciliation()
  assert runtime._trading_recovery_reason == "journal_indeterminate_commands"
  assert runtime._trading_recovery_started_monotonic is None
  assert broker.reconciled is False
  runtime._raise_if_trading_recovery_expired()


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

    def mark_trading_reconciled(
      self,
      generation: int,
      _callback_failure_generation: int,
    ) -> bool:
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

    def mark_trading_reconciled(
      self,
      generation: int,
      _callback_failure_generation: int,
    ) -> bool:
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
        "stock_list": ["000001.SZ"],
        "periods": ["1d"],
        "start_time": "20250102",
        "end_time": "20250102",
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
async def test_heartbeat_never_captures_account_snapshot(
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

  assert snapshot_modes == []
  assert heartbeat_statuses == ["READY", "READY"]


@pytest.mark.asyncio
async def test_account_snapshot_loop_is_independent_from_heartbeat(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class StopSnapshots(BaseException):
    pass

  runtime = _bare_live_runtime(SimpleNamespace())
  runtime._ensure_market_upload_state()
  runtime._initial_reconciliation_complete.set()
  snapshot_modes: list[bool] = []

  async def no_wait(_seconds: float) -> None:
    return None

  async def queue_snapshot(*, reconciliation: bool = False) -> str:
    snapshot_modes.append(reconciliation)
    raise StopSnapshots

  monkeypatch.setattr(runtime_module.asyncio, "sleep", no_wait)
  monkeypatch.setattr(runtime, "_queue_full_snapshot", queue_snapshot)

  with pytest.raises(StopSnapshots):
    await runtime._account_snapshot_loop()

  assert snapshot_modes == [False]
