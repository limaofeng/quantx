"""Use an actual local pipe with a fake native broker, never a QMT connection."""

import multiprocessing
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from quantx_contracts.collection_permit import CollectionPermit, CollectionUnit
from quantx_qmt_agent import historical_worker
from quantx_qmt_agent.historical_worker import (
  historical_work_units,
  run_historical_market_data_worker,
)
from quantx_qmt_agent.native_unit_ipc import iter_native_unit, serve_native_unit

PAYLOAD = {
  "operation": "bars",
  "stock_list": ["000001.SZ"],
  "periods": ["1d"],
  "start_time": "20250102",
  "end_time": "20250102",
}


def permit():
  now = datetime.now(timezone.utc)
  return CollectionPermit(
    permit_id=uuid4(),
    device_id=uuid4(),
    owner_epoch=1,
    unit=CollectionUnit.from_payload(
      str(uuid4()), 0, historical_work_units(PAYLOAD)[0]
    ),
    issued_at=now,
    expires_at=now + timedelta(seconds=15),
  )


def test_full_child_loop_streams_bounded_batches_and_reuses_one_broker(monkeypatch):
  parent, child = multiprocessing.Pipe()
  calls, closed = [], []

  class Broker:
    data_manager = SimpleNamespace(close_connection=lambda: closed.append(True))

    def iter_market_data(self, payload):
      calls.append(payload)
      for index in range(300):
        yield {"value": index}

  monkeypatch.setattr(historical_worker, "_create_historical_broker", Broker)
  thread = threading.Thread(
    target=run_historical_market_data_worker, args=(child, "xtdata")
  )
  thread.start()
  try:
    for _ in range(2):
      assert list(
        iter_native_unit(
          parent, permit(), PAYLOAD, timeout=3, abort=lambda: parent.close()
        )
      ) == [{"value": index} for index in range(300)]
  finally:
    parent.send({"type": "shutdown"})
    thread.join(3)
    parent.close()
  assert not thread.is_alive()
  assert calls == [historical_work_units(PAYLOAD)[0]] * 2
  assert closed == [True]


def test_child_rejects_changed_plan_before_native_entry():
  from unittest.mock import Mock

  grant = permit()
  broker = Mock()
  with pytest.raises(ValueError, match="plan mismatch"):
    serve_native_unit(
      Mock(),
      broker,
      {
        "request_id": str(grant.unit.request_id),
        "permit": grant.model_dump(mode="json"),
        "payload": {**PAYLOAD, "stock_list": ["600000.SH"]},
      },
    )
  broker.iter_market_data.assert_not_called()


def test_parent_rejects_false_completion_count():
  from unittest.mock import Mock

  grant = permit()
  connection = Mock()
  connection.poll.return_value = True
  connection.recv.return_value = {
    "type": "unit_complete",
    "request_id": str(grant.unit.request_id),
    "permit_id": str(grant.permit_id),
    "sequence": 0,
    "record_count": 1,
  }
  with pytest.raises(ValueError, match="completion count"):
    list(iter_native_unit(connection, grant, PAYLOAD, timeout=1, abort=lambda: None))


def test_parent_deadline_is_for_whole_unit_not_each_batch():
  from unittest.mock import Mock

  connection = Mock()
  connection.poll.return_value = False
  with pytest.raises(TimeoutError):
    list(
      iter_native_unit(connection, permit(), PAYLOAD, timeout=0.01, abort=lambda: None)
    )
  assert 0 < connection.poll.call_args.args[0] <= 0.01


def test_blocked_request_send_aborts_peer_and_joins_sender():
  stopped = threading.Event()
  joined = threading.Event()

  class Connection:
    def send(self, _):
      stopped.wait(2)
      joined.set()

  try:
    with pytest.raises(TimeoutError, match="deadline"):
      list(
        iter_native_unit(
          Connection(), permit(), PAYLOAD, timeout=0.01, abort=stopped.set
        )
      )
    assert joined.is_set()
  finally:
    stopped.set()


async def test_runtime_closing_partial_iterator_terminates_before_unlock(monkeypatch):
  import asyncio
  from unittest.mock import Mock

  from quantx_qmt_agent import runtime as module

  runtime = module.AgentRuntime.__new__(module.AgentRuntime)
  runtime.broker = SimpleNamespace(historical_market_data_worker_kind=lambda: "xtdata")
  runtime._historical_worker_lock = asyncio.Lock()
  runtime._historical_worker_connection = object()
  runtime._ensure_historical_worker_sync = Mock()
  runtime._shutdown_historical_worker_sync = Mock(
    side_effect=lambda **_: (
      runtime._historical_worker_lock.locked() or pytest.fail("unlocked before stop")
    )
  )
  monkeypatch.setattr(
    module,
    "iter_native_unit",
    lambda *args, **kwargs: iter([{"value": 1}, {"value": 2}]),
  )
  async with runtime._historical_worker_lock:
    records = runtime._collect_history_unit_sync(permit(), PAYLOAD)
    assert next(records) == {"value": 1}
    records.close()
    runtime._shutdown_historical_worker_sync.assert_called_once_with(graceful=False)


def test_child_rechecks_expiry_after_startup_before_native_entry():
  from unittest.mock import Mock

  grant = permit()
  old = datetime.now(timezone.utc) - timedelta(minutes=1)
  grant = grant.model_copy(
    update={"issued_at": old, "expires_at": old + timedelta(seconds=15)}
  )
  broker = Mock()
  with pytest.raises(ValueError, match="expired before child entry"):
    serve_native_unit(
      Mock(),
      broker,
      {
        "request_id": str(grant.unit.request_id),
        "permit": grant.model_dump(mode="json"),
        "payload": PAYLOAD,
      },
    )
  broker.iter_market_data.assert_not_called()


async def test_uncertain_child_startup_cannot_become_abort():
  import asyncio
  from unittest.mock import Mock

  from quantx_qmt_agent import runtime as module
  from quantx_qmt_agent.history_pipeline import HistoryPipeline

  runtime = module.AgentRuntime.__new__(module.AgentRuntime)
  runtime.broker = SimpleNamespace(historical_market_data_worker_kind=lambda: "xtdata")
  runtime._historical_worker_lock = asyncio.Lock()
  runtime._ensure_historical_worker_sync = Mock(
    side_effect=OSError("spawn interrupted")
  )
  runtime._shutdown_historical_worker_sync = Mock()
  pipeline = HistoryPipeline.__new__(HistoryPipeline)
  pipeline.runtime = runtime
  async with runtime._historical_worker_lock:
    with pytest.raises(module._FatalMarketDataPreparationError) as caught:
      list(runtime._collect_history_unit_sync(permit(), PAYLOAD))
    with pytest.raises(module._FatalMarketDataPreparationError):
      pipeline._stop_failed_native(caught.value)
  runtime._shutdown_historical_worker_sync.assert_not_called()
