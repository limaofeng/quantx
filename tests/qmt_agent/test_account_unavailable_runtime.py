from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from quantx_contracts import AgentEnvelope
from quantx_qmt_agent import runtime as runtime_module
from quantx_qmt_agent.broker import LiveBroker
from quantx_qmt_agent.credentials import DeviceConfiguration
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.miniqmt.local_agent import MiniQmtLocalAgent
from quantx_qmt_agent.miniqmt.manager_registry import XTTradingManagerRegistry
from quantx_qmt_agent.miniqmt.trading.trading_manager import XTTradingManager
from quantx_qmt_agent.runtime import AgentRuntime, _FatalTradingRecoveryError


class NativeTrader:
  status: int | None = 3
  rpc_failed = False
  connect_calls = 0

  def query_account_status(self):
    if self.rpc_failed:
      raise ConnectionError("native RPC unavailable")
    return [
      SimpleNamespace(account_id="account-1", account_type="STOCK", status=self.status)
    ]

  def connect(self):
    self.connect_calls += 1
    return 0


class Manager:
  # Exercise the real native status/probe/reconnect implementation without
  # constructing a trader, callback threads, or any external connection.
  _query_account_status = XTTradingManager._query_account_status
  query_account_status = XTTradingManager.query_account_status
  is_connection_healthy = XTTradingManager.is_connection_healthy
  is_account_status_ready = XTTradingManager.is_account_status_ready
  _account_status_ready = staticmethod(XTTradingManager._account_status_ready)
  reconnect = XTTradingManager.reconnect

  def __init__(self):
    self.account_id = "account-1"
    self.acc = SimpleNamespace(account_id="account-1", account_type="STOCK")
    self.xttrader = NativeTrader()
    self.is_connected = True
    self.account_status_rpc_succeeded = False
    self._native_started = True
    self.fact_queries = 0

  def get_account_info(self):
    self.fact_queries += 1
    return {"cash": 100_000, "total_asset": 100_000}

  def get_positions(self):
    self.fact_queries += 1
    return []

  def get_orders(self, cancelable_only=False):
    self.fact_queries += 1
    return []

  def get_trades(self):
    self.fact_queries += 1
    return []


@pytest.fixture
def account_runtime(tmp_path):
  manager = Manager()
  registry = object.__new__(XTTradingManagerRegistry)
  registry._managers = {"account-1": manager}
  registry._last_reconnect_attempts = {}
  registry._connection_generations = {}
  registry._reconnect_interval = 0.0
  journal = LocalJournal(tmp_path / "account-unavailable.sqlite3")
  broker = object.__new__(LiveBroker)
  broker.agents = {"account-1": MiniQmtLocalAgent(manager)}
  broker._trading_registry = registry
  broker._trading_journal = journal
  broker._trading_access_lock = threading.RLock()
  broker._trading_generation_lock = threading.Lock()
  broker._trading_connection_generation = 0
  broker._trading_mutation_generation = 0
  broker._trading_reconciled_generation = -1
  broker._registry_trading_generations = {"account-1": 0}
  broker.is_market_data_ready = lambda: True
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080", device_id="account-unavailable"
    ),
    device_secret="unused",
    mode="live",
    allowed_accounts={"account-1"},
    broker=broker,
    journal=journal,
    market_spool_base_directory=tmp_path,
  )
  runtime.health_state.set_control_connected(True)
  runtime._set_market_stream_status("READY")
  yield runtime, manager
  runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [3, 1, None])
async def test_responsive_rpc_waits_for_account_without_restart(
  account_runtime, status
):
  runtime, manager = account_runtime
  manager.xttrader.status = status
  await runtime._initialize_authenticated_session()

  assert runtime._is_trading_ready() is False
  assert runtime._requires_trading_reconciliation() is True
  assert runtime._trading_recovery_started_monotonic is None
  assert manager.fact_queries == 0
  for _ in range(3):
    assert await runtime._ensure_trading_ready() is False
    # A previously armed deadline must not expire while a responsive account
    # remains unavailable, even across repeated incomplete snapshots.
    runtime._trading_recovery_started_monotonic = time.monotonic() - 3600
    runtime._raise_if_trading_recovery_expired()
    await runtime._queue_full_snapshot(reconciliation=True)
    assert runtime._trading_recovery_started_monotonic is None

  health = runtime.health_state.snapshot()
  assert health.status.value == "degraded"
  assert health.xtdata_status.value == "connected"
  assert health.market_stream_status.value == "ready"
  assert manager.xttrader.connect_calls == 0
  assert runtime.broker.trading_connection_generation() == 0
  assert runtime.broker.trading_requires_reconciliation() is True
  for serialized in runtime.journal.pending_reports():
    payload = AgentEnvelope.model_validate_json(serialized).payload
    assert payload["is_complete"] is False
    assert payload["accounts"] == []
    assert payload["positions_by_account"] == {}


async def _run_readiness_cycles(runtime, monkeypatch):
  cycles = 0
  original_sleep = asyncio.sleep

  async def cycle_sleep(seconds):
    nonlocal cycles
    if seconds == runtime_module.XTTRADING_READINESS_RETRY_SECONDS:
      cycles += 1
      if cycles == 3:
        raise asyncio.CancelledError
    await original_sleep(0)

  async def checkpoint(*_args, **_kwargs):
    pass

  with monkeypatch.context() as patch:
    patch.setattr(runtime_module.asyncio, "sleep", cycle_sleep)
    patch.setattr(runtime, "_heartbeat_checkpoint", checkpoint)
    with pytest.raises(asyncio.CancelledError):
      await runtime._trading_readiness_loop(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered_status", [0, 6])
async def test_account_recovery_requires_one_fresh_reconciliation(
  account_runtime, monkeypatch, recovered_status
):
  runtime, manager = account_runtime
  await runtime._initialize_authenticated_session()
  assert runtime._trading_recovery_started_monotonic is None
  manager.xttrader.status = recovered_status
  await _run_readiness_cycles(runtime, monkeypatch)

  reports = [
    AgentEnvelope.model_validate_json(value)
    for value in runtime.journal.pending_reports()
  ]
  assert len(reports) == 2
  assert sum(report.payload["is_complete"] is True for report in reports) == 1
  snapshot_id = runtime._trading_reconciliation_snapshot_id
  assert snapshot_id is not None
  assert runtime._is_trading_ready() is True
  assert runtime._requires_trading_reconciliation() is True
  assert runtime.broker.trading_requires_reconciliation() is True
  assert runtime._trading_recovery_started_monotonic is not None
  assert manager.xttrader.connect_calls == 0
  assert runtime.broker.trading_connection_generation() == 0

  # Waiting for the valid snapshot ACK is still bounded after account recovery.
  runtime._trading_recovery_started_monotonic = time.monotonic() - 91
  with pytest.raises(_FatalTradingRecoveryError):
    runtime._raise_if_trading_recovery_expired()
  assert runtime._acknowledge_trading_reconciliation_snapshot(snapshot_id) is True
  assert runtime._requires_trading_reconciliation() is False
  assert runtime.broker.trading_requires_reconciliation() is False
  assert runtime._trading_recovery_started_monotonic is None


@pytest.mark.asyncio
async def test_ready_account_failure_closes_trading_without_interrupting_market(
  account_runtime, monkeypatch
):
  runtime, manager = account_runtime
  manager.xttrader.status = 0
  await runtime._initialize_authenticated_session()
  assert runtime._acknowledge_trading_reconciliation_snapshot(
    runtime._trading_reconciliation_snapshot_id
  )
  fact_queries = manager.fact_queries
  manager.xttrader.status = 3
  await _run_readiness_cycles(runtime, monkeypatch)

  assert runtime._is_trading_ready() is False
  assert runtime._requires_trading_reconciliation() is True
  assert runtime.broker.trading_requires_reconciliation() is True
  assert runtime._trading_recovery_started_monotonic is None
  assert runtime.health_state.snapshot().market_stream_status.value == "ready"
  assert runtime.health_state.snapshot().xtdata_status.value == "connected"
  assert manager.fact_queries == fact_queries
  assert manager.xttrader.connect_calls == 0
  assert runtime.broker.trading_connection_generation() == 0


@pytest.mark.asyncio
async def test_rpc_failure_after_account_wait_still_has_bounded_recovery(
  account_runtime,
):
  runtime, manager = account_runtime
  await runtime._initialize_authenticated_session()
  assert runtime._trading_recovery_started_monotonic is None
  assert manager.account_status_rpc_succeeded is True
  manager.xttrader.rpc_failed = True

  assert await runtime._ensure_trading_ready() is False
  assert manager.account_status_rpc_succeeded is False
  assert manager.xttrader.connect_calls == 1
  started = runtime._trading_recovery_started_monotonic
  assert started is not None
  runtime._raise_if_trading_recovery_expired()

  assert await runtime._ensure_trading_ready() is False
  assert runtime._trading_recovery_started_monotonic == started
  runtime._trading_recovery_started_monotonic = time.monotonic() - 91
  with pytest.raises(_FatalTradingRecoveryError):
    runtime._raise_if_trading_recovery_expired()
  assert runtime._is_trading_ready() is False
  assert runtime._requires_trading_reconciliation() is True


async def _prepare_history_on_waiting_account(runtime):
  await runtime._initialize_authenticated_session()
  runtime._control_session_authenticated = True
  runtime._whole_market_subscription_ready.set()
  runtime._whole_market_subscription_active = True
  for report in runtime.journal.pending_reports():
    runtime.journal.acknowledge_report(
      AgentEnvelope.model_validate_json(report).message_id
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [3, 1, None])
async def test_history_remains_available_without_authoritative_account_snapshot(
  account_runtime, status
):
  runtime, manager = account_runtime
  manager.xttrader.status = status
  await _prepare_history_on_waiting_account(runtime)

  for snapshot_at in (0.0, time.monotonic() - 3600):
    runtime._last_complete_account_snapshot_monotonic = snapshot_at
    await asyncio.wait_for(runtime._wait_for_history_dispatch(), timeout=1)
    assert runtime._history_workload == "running"
    assert runtime._history_workload_reason == ""
    assert runtime._is_trading_ready() is False
    assert runtime._requires_trading_reconciliation() is True
  assert manager.fact_queries == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("blocked_by", "reason"),
  [
    ("control", "CONTROL_CONNECTION_UNHEALTHY"),
    ("market", "MARKET_STREAM_NOT_READY"),
    ("xtdata", "XTDATA_UNSTABLE"),
    ("snapshot", "ACCOUNT_SNAPSHOT_RUNNING"),
    ("command", "TRADE_COMMAND_PENDING"),
    ("report", "BROKER_REPORT_PENDING"),
    ("heartbeat", "CONTROL_HEARTBEAT_DELAYED"),
    ("market_ack", "MARKET_STREAM_DELAYED"),
  ],
)
async def test_account_wait_keeps_history_transport_and_workload_guards(
  account_runtime, blocked_by, reason
):
  runtime, _ = account_runtime
  await _prepare_history_on_waiting_account(runtime)
  assert runtime._history_qos_block_reason() == ""

  if blocked_by == "control":
    runtime._control_session_authenticated = False
  elif blocked_by == "market":
    runtime._set_market_stream_status("OFFLINE")
  elif blocked_by == "xtdata":
    runtime._set_market_data_ready(False)
  elif blocked_by == "snapshot":
    await runtime._full_snapshot_lock.acquire()
  elif blocked_by == "command":
    runtime._active_command_count = 1
  elif blocked_by == "report":
    await runtime._queue_full_snapshot(reconciliation=True)
  elif blocked_by == "heartbeat":
    runtime._heartbeat_sent_monotonic = {"unacked": time.monotonic() - 6}
  elif blocked_by == "market_ack":
    runtime._market_stream_pending_ack_monotonic = time.monotonic() - 6

  try:
    assert runtime._history_qos_block_reason() == reason
  finally:
    if blocked_by == "snapshot":
      runtime._full_snapshot_lock.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("rpc_failed", [False, True])
async def test_account_wait_exit_restores_history_reconciliation_priority(
  account_runtime, rpc_failed
):
  runtime, manager = account_runtime
  await _prepare_history_on_waiting_account(runtime)
  assert runtime._history_qos_block_reason() == ""

  manager.xttrader.rpc_failed = rpc_failed
  manager.xttrader.status = 0
  await runtime._ensure_trading_ready()

  assert runtime._trading_account_waiting is False
  assert runtime._history_qos_block_reason() == "TRADING_RECONCILING"
  assert runtime._is_trading_ready() is not rpc_failed
  assert runtime._requires_trading_reconciliation() is True
