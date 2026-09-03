from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace

import pytest
from quantx_contracts import AgentEnvelope
from quantx_qmt_agent.broker import LiveBroker, _LiveReportSink
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.miniqmt.trading import trading_manager as manager_module
from quantx_qmt_agent.miniqmt.trading.trading_manager import XTTradingManager


def _manager(*, capacity: int = 8, start_writer: bool = True):
  manager = object.__new__(XTTradingManager)
  manager.trading_service = None
  manager._callback_queue = queue.Queue(maxsize=capacity)
  manager._callback_state_lock = threading.Lock()
  manager._callback_pipeline_healthy = True
  manager._callback_pipeline_error = ""
  manager._callback_failure_generation = 0
  manager._callback_recovery_pending = False
  manager._callback_recovery_generation = None
  manager._callback_write_inflight = False
  manager._callback_accepting = True
  manager._callback_close_lock = threading.Lock()
  manager._callback_writer_sentinel_enqueued = False
  manager._callback_writer_stopped = threading.Event()
  manager._callback_writer_thread = None
  manager.xttrader = None
  manager._native_started = False
  manager.is_connected = True
  manager.session_id = 1
  manager.event_loop = None
  manager.event_loop_thread = None
  if start_writer:
    manager._callback_writer_thread = threading.Thread(
      target=manager._durable_callback_writer,
      daemon=True,
    )
    manager._callback_writer_thread.start()
  return manager


def _stop_manager(manager) -> None:
  assert manager._stop_durable_callback_writer(timeout=2)


def test_callback_is_normalized_before_native_object_can_change(tmp_path) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  manager = _manager()
  manager.trading_service = _LiveReportSink("account-1", journal)
  order = SimpleNamespace(
    account_id="account-1",
    order_id=123,
    stock_code="600000.SH",
    order_status=48,
    order_volume=100,
    traded_volume=0,
    order_remark="qx:client",
  )

  assert manager.enqueue_durable_callback("order", order)
  order.order_id = 999
  manager._callback_queue.join()

  envelope = AgentEnvelope.model_validate_json(journal.pending_reports()[0])
  assert envelope.payload["order"]["order_id"] == 123
  _stop_manager(manager)


def test_callback_enqueue_fences_snapshot_before_durable_writer_runs(tmp_path) -> None:
  journal = LocalJournal(tmp_path / "callback-fence.sqlite3")
  broker = object.__new__(LiveBroker)
  broker._trading_generation_lock = threading.Lock()
  broker._trading_mutation_generation = 0
  manager = _manager(start_writer=False)
  manager.trading_service = _LiveReportSink(
    "account-1",
    journal,
    on_callback_observed=broker._advance_trading_mutation,
  )

  assert manager.enqueue_durable_callback(
    "order",
    SimpleNamespace(
      account_id="account-1",
      order_id=123,
      stock_code="600000.SH",
      order_status=48,
      order_volume=100,
      traded_volume=0,
      order_remark="qx:client",
    ),
  )

  assert broker.trading_mutation_generation() == 1
  assert journal.pending_reports() == []

  manager._callback_writer_thread = threading.Thread(
    target=manager._durable_callback_writer,
    daemon=True,
  )
  manager._callback_writer_thread.start()
  manager._callback_queue.join()
  assert len(journal.pending_reports()) == 1
  assert broker.trading_mutation_generation() == 1
  _stop_manager(manager)


def test_callback_observation_fences_snapshot_when_normalization_fails(
  tmp_path,
) -> None:
  broker = object.__new__(LiveBroker)
  broker._trading_generation_lock = threading.Lock()
  broker._trading_mutation_generation = 0
  manager = _manager(start_writer=False)
  manager.trading_service = _LiveReportSink(
    "account-1",
    LocalJournal(tmp_path / "callback-normalization-fence.sqlite3"),
    on_callback_observed=broker._advance_trading_mutation,
  )

  assert manager.enqueue_durable_callback("unsupported", object()) is False

  assert broker.trading_mutation_generation() == 1
  assert manager.callback_pipeline_healthy() is False
  assert manager.callback_pipeline_error() == "REPORT_NORMALIZATION_FAILED"


def test_native_callback_correlation_never_waits_for_sqlite_writer_lock(
  tmp_path,
) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  client_order_id = "client-order-1234567890-extra"
  journal.begin_command(
    "command-1",
    {
      "command_kind": "PLACE_ORDER",
      "client_order_id": client_order_id,
    },
  )
  sink = _LiveReportSink("account-1", journal)
  callback_done = threading.Event()
  failures: list[BaseException] = []

  def callback() -> None:
    try:
      sink.prepare_callback(
        "order",
        SimpleNamespace(
          account_id="account-1",
          order_id=123,
          stock_code="600000.SH",
          order_status=48,
          order_volume=100,
          traded_volume=0,
          order_remark=f"qx:{client_order_id[:20]}",
        ),
      )
    except BaseException as exc:
      failures.append(exc)
    finally:
      callback_done.set()

  journal.lock.acquire()
  try:
    thread = threading.Thread(target=callback)
    thread.start()
    assert callback_done.wait(timeout=0.2)
  finally:
    journal.lock.release()
  thread.join(timeout=1)

  assert failures == []


def test_persistence_failure_retries_queue_head_without_reordering() -> None:
  persisted: list[int] = []
  attempts = {1: 0}

  class Service:
    @staticmethod
    def prepare_callback(_kind, value):
      return int(value)

    @staticmethod
    def persist_prepared_callback(value):
      if value == 1 and attempts[1] == 0:
        attempts[1] += 1
        raise OSError("injected journal outage")
      persisted.append(value)

  manager = _manager()
  manager.trading_service = Service()
  assert manager.enqueue_durable_callback("order", 1)
  assert manager.enqueue_durable_callback("order", 2)
  manager._callback_queue.join()

  assert persisted == [1, 2]
  assert manager.callback_pipeline_healthy() is False
  assert manager.callback_pipeline_error() == "REPORT_PERSISTENCE_FAILED"
  assert manager.mark_callback_pipeline_reconciled(
    manager.callback_failure_generation()
  ) is True
  assert manager.callback_pipeline_healthy() is True
  _stop_manager(manager)


def test_callback_overflow_latches_reconciliation_gate() -> None:
  class Service:
    @staticmethod
    def prepare_callback(_kind, value):
      return value

  manager = _manager(capacity=1, start_writer=False)
  manager.trading_service = Service()

  assert manager.enqueue_durable_callback("order", 1)
  assert manager.enqueue_durable_callback("order", 2) is False
  assert manager.callback_pipeline_healthy() is False
  assert manager.callback_pipeline_error() == "REPORT_QUEUE_OVERFLOW"


def test_close_drains_accepted_callback_backlog_before_returning() -> None:
  first_started = threading.Event()
  release_first = threading.Event()
  native_stopped = threading.Event()
  persisted: list[int] = []

  class Service:
    @staticmethod
    def prepare_callback(_kind, value):
      return int(value)

    @staticmethod
    def persist_prepared_callback(value):
      if value == 1:
        first_started.set()
        assert release_first.wait(timeout=2)
      persisted.append(value)

  manager = _manager()
  manager.trading_service = Service()

  class Trader:
    @staticmethod
    def stop() -> None:
      assert manager._callback_accepting is True
      assert manager.enqueue_durable_callback("order", 3)
      native_stopped.set()

  manager.xttrader = Trader()
  manager._native_started = True
  assert manager.enqueue_durable_callback("order", 1)
  assert first_started.wait(timeout=1)
  assert manager.enqueue_durable_callback("trade", 2)

  closer = threading.Thread(target=manager.close_connection)
  closer.start()
  time.sleep(0.02)

  assert closer.is_alive()
  assert native_stopped.is_set()
  assert manager._callback_accepting is False
  assert persisted == []

  release_first.set()
  closer.join(timeout=1)

  assert closer.is_alive() is False
  assert persisted == [1, 2, 3]
  assert manager._callback_queue.unfinished_tasks == 0
  assert manager._callback_writer_stopped.is_set()
  assert manager.callback_pipeline_healthy() is True
  assert manager.enqueue_durable_callback("order", 4) is False
  assert manager.callback_pipeline_healthy() is False
  assert manager.callback_pipeline_error() == "REPORT_CALLBACK_AFTER_CLOSE"


def test_close_timeout_keeps_blocked_callback_unfinished_and_latches_gap(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  persistence_started = threading.Event()
  release_persistence = threading.Event()
  persisted: list[int] = []

  class Service:
    @staticmethod
    def prepare_callback(_kind, value):
      return int(value)

    @staticmethod
    def persist_prepared_callback(value):
      persistence_started.set()
      assert release_persistence.wait(timeout=2)
      persisted.append(value)

  monkeypatch.setattr(manager_module, "CALLBACK_DRAIN_TIMEOUT_SECONDS", 0.05)
  manager = _manager()
  manager.trading_service = Service()
  assert manager.enqueue_durable_callback("order", 1)
  assert persistence_started.wait(timeout=1)

  started = time.monotonic()
  manager.close_connection()
  elapsed = time.monotonic() - started

  assert elapsed < 0.5
  assert manager._callback_writer_thread.is_alive()
  assert manager._callback_queue.unfinished_tasks == 2
  assert manager.callback_pipeline_healthy() is False
  assert manager.callback_pipeline_error() == "REPORT_DRAIN_TIMEOUT"
  assert manager.mark_callback_pipeline_reconciled(
    manager.callback_failure_generation()
  ) is False

  release_persistence.set()
  manager._callback_writer_thread.join(timeout=1)

  assert manager._callback_writer_thread.is_alive() is False
  assert manager._callback_queue.unfinished_tasks == 0
  assert persisted == [1]
  assert manager.callback_pipeline_healthy() is True


def test_close_during_persistence_failure_retains_head_until_success(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  failure_observed = threading.Event()
  allow_persistence = threading.Event()
  persisted: list[int] = []

  class Service:
    @staticmethod
    def prepare_callback(_kind, value):
      return int(value)

    @staticmethod
    def persist_prepared_callback(value):
      if not allow_persistence.is_set():
        failure_observed.set()
        raise OSError("injected durable store outage")
      persisted.append(value)

  monkeypatch.setattr(manager_module, "CALLBACK_DRAIN_TIMEOUT_SECONDS", 0.02)
  manager = _manager()
  manager.trading_service = Service()
  assert manager.enqueue_durable_callback("trade", 9)
  assert failure_observed.wait(timeout=1)

  manager.close_connection()

  assert manager._callback_writer_thread.is_alive()
  assert manager._callback_queue.unfinished_tasks == 2
  assert manager.callback_pipeline_healthy() is False
  assert manager.callback_pipeline_error() == "REPORT_DRAIN_TIMEOUT"
  assert manager.mark_callback_pipeline_reconciled(
    manager.callback_failure_generation()
  ) is False

  allow_persistence.set()
  manager._callback_writer_thread.join(timeout=1)

  assert manager._callback_writer_thread.is_alive() is False
  assert manager._callback_queue.unfinished_tasks == 0
  assert persisted == [9]
  assert manager.callback_pipeline_healthy() is True


def test_callback_gap_blocks_new_order_but_not_cancel() -> None:
  placed: list[dict] = []
  cancelled: list[int] = []
  manager = SimpleNamespace(
    is_connected=True,
    callback_pipeline_healthy=lambda: False,
  )
  agent = SimpleNamespace(
    trading_manager=manager,
    cancel_order=lambda order_id: (
      cancelled.append(order_id) or {"success": True, "message": ""}
    ),
    place_order=lambda payload: (
      placed.append(payload) or {"success": True, "order_id": 1}
    ),
  )
  broker = object.__new__(LiveBroker)
  broker.agents = {"account-1": agent}
  broker._trading_access_lock = threading.RLock()
  broker._trading_generation_lock = threading.Lock()
  broker._trading_connection_generation = 1
  broker._trading_reconciled_generation = 1
  broker.ensure_trading_ready = lambda: True

  cancel = broker.execute(
    {
      "account_id": "account-1",
      "command_kind": "CANCEL_ORDER",
      "broker_order_id": 42,
    }
  )
  order = broker.execute(
    {
      "account_id": "account-1",
      "command_kind": "PLACE_ORDER",
      "client_order_id": "client-1",
      "side": "BUY",
      "price_type": "FIX_PRICE",
      "limit_price": 10,
    }
  )

  assert cancel["accepted"] is True
  assert cancelled == [42]
  assert order["accepted"] is False
  assert order["reason"] == "local_reconciliation_required"
  assert placed == []
