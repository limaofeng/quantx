from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from quantx_contracts import AgentEnvelope
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

  assert runtime._acknowledge_trading_reconciliation_snapshot(
    "snapshot-1"
  ) is False
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
async def test_periodic_live_snapshot_enters_reconciliation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class StopHeartbeat(Exception):
    pass

  runtime = _bare_live_runtime(SimpleNamespace())
  snapshot_modes: list[bool] = []
  heartbeat_statuses: list[str] = []

  async def no_wait(_seconds: float) -> None:
    return None

  async def queue_snapshot(*, reconciliation: bool = False) -> str:
    snapshot_modes.append(reconciliation)
    runtime._trading_reconciliation_required = reconciliation
    return "snapshot-1"

  async def checkpoint(_socket, *, status: str) -> None:
    heartbeat_statuses.append(status)
    if len(heartbeat_statuses) == 2:
      raise StopHeartbeat

  monkeypatch.setattr(runtime_module.asyncio, "sleep", no_wait)
  monkeypatch.setattr(runtime, "_queue_full_snapshot", queue_snapshot)
  monkeypatch.setattr(runtime, "_heartbeat_checkpoint", checkpoint)

  with pytest.raises(StopHeartbeat):
    await runtime._heartbeat_loop(SimpleNamespace())

  assert snapshot_modes == [True]
  assert heartbeat_statuses == ["READY", "RECONCILING"]
