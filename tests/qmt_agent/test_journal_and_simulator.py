import asyncio
from types import SimpleNamespace

import pytest
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_qmt_agent.broker import (
  QmtDataBroker,
  SimulatorBroker,
  _LiveReportSink,
  _LocalMarketStreamer,
)
from quantx_qmt_agent.credentials import DeviceConfiguration
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.runtime import AgentRuntime


def test_journal_rejects_message_id_payload_mismatch(tmp_path) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  assert journal.begin_command("message-1", {"volume": 100}) == ("NEW", None)
  state, _ = journal.begin_command("message-1", {"volume": 200})
  assert state == "MISMATCH"


def test_journal_returns_completed_result_for_duplicate(tmp_path) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  journal.begin_command("message-1", {"volume": 100})
  journal.complete_command(
    "message-1",
    {"accepted": True, "reason": "", "reports": []},
  )
  state, result = journal.begin_command("message-1", {"volume": 100})
  assert state == "DUPLICATE"
  assert result and result["accepted"] is True


def test_journal_persists_client_to_broker_order_correlation(tmp_path) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  journal.begin_command(
    "message-1",
    {"client_order_id": "client-1", "volume": 100},
  )
  journal.complete_command(
    "message-1",
    {"accepted": True, "broker_order_id": 123456, "reports": []},
  )

  assert journal.broker_order_client_ids() == {"123456": "client-1"}


def test_journal_resolves_early_callback_from_local_order_remark(tmp_path) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  client_order_id = "client-order-1234567890-unique"
  journal.begin_command(
    "message-1",
    {"client_order_id": client_order_id, "volume": 100},
  )

  assert journal.client_order_id_for_report(
    broker_order_id=123456,
    order_remark=f"qx:{client_order_id[:20]}",
  ) == client_order_id


def test_snapshot_reconciles_interrupted_order_without_resubmission(
  tmp_path,
) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  client_order_id = "client-order-1234567890-unique"
  payload = {"client_order_id": client_order_id, "volume": 100}
  journal.begin_command("message-1", payload)

  assert journal.reconcile_processing_order(
    client_order_id=client_order_id,
    broker_order_id=123456,
  )
  state, result = journal.begin_command("message-1", payload)

  assert state == "DUPLICATE"
  assert result is not None
  assert result["accepted"] is True
  assert result["broker_order_id"] == 123456


@pytest.mark.asyncio
async def test_identical_full_snapshots_have_distinct_reconciliation_ids(
  tmp_path,
) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  broker = SimulatorBroker(set(), data_only=True)
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="device-1",
    ),
    device_secret="unused",
    mode="data-only",
    allowed_accounts=set(),
    broker=broker,
    journal=journal,
    market_spool_base_directory=tmp_path,
  )

  await runtime._queue_full_snapshot()
  first = AgentEnvelope.model_validate_json(journal.pending_reports()[0])
  await runtime._queue_full_snapshot()

  reports = [
    AgentEnvelope.model_validate_json(value)
    for value in journal.pending_reports()
  ]
  assert len(reports) == 1
  assert first.payload["report_id"] == first.message_id
  assert reports[0].payload["report_id"] == reports[0].message_id
  assert first.payload["report_id"] != reports[0].payload["report_id"]


def test_retiring_full_snapshots_preserves_incremental_reports(tmp_path) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  complete = AgentEnvelope(
    message_type=AgentMessageType.DELTA_REPORT,
    payload={"is_complete": True, "positions_by_account": {}},
  )
  incremental = AgentEnvelope(
    message_type=AgentMessageType.DELTA_REPORT,
    payload={"is_complete": False, "position_deltas": []},
  )
  journal.add_report(complete.message_id, complete.model_dump_json())
  journal.add_report(incremental.message_id, incremental.model_dump_json())

  assert journal.retire_pending_full_snapshots() == 1
  pending = [
    AgentEnvelope.model_validate_json(value)
    for value in journal.pending_reports()
  ]
  assert [item.message_id for item in pending] == [incremental.message_id]


@pytest.mark.asyncio
async def test_market_request_does_not_block_report_ack_processing(
  monkeypatch,
  tmp_path,
) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  runtime = AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="device-1",
    ),
    device_secret="unused",
    mode="data-only",
    allowed_accounts=set(),
    broker=SimulatorBroker(set(), data_only=True),
    journal=journal,
    market_spool_base_directory=tmp_path,
  )
  report = AgentEnvelope(
    message_type=AgentMessageType.DELTA_REPORT,
    payload={"is_complete": True},
  )
  journal.add_report(report.message_id, report.model_dump_json())
  request_started = asyncio.Event()
  release_request = asyncio.Event()

  async def slow_market_request(envelope) -> None:
    del envelope
    request_started.set()
    await release_request.wait()

  monkeypatch.setattr(
    runtime,
    "_handle_market_data_request",
    slow_market_request,
  )

  class Socket:
    def __init__(self) -> None:
      self.sent: list[str] = []
      self.closed: list[tuple[int, str]] = []

    async def send(self, serialized: str) -> None:
      self.sent.append(serialized)

    async def close(self, *, code: int, reason: str) -> None:
      self.closed.append((code, reason))

  socket = Socket()
  worker = asyncio.create_task(runtime._market_request_loop(socket))
  market_request = AgentEnvelope(
    message_type=AgentMessageType.MARKET_DATA_REQUEST,
    payload={"request_id": "request-1"},
  )
  await runtime._handle_message(None, market_request.model_dump_json())
  await asyncio.wait_for(request_started.wait(), timeout=1)

  report_ack = AgentEnvelope(
    message_type=AgentMessageType.REPORT_ACK,
    payload={
      "report_message_id": report.message_id,
      "accepted": True,
    },
  )
  await asyncio.wait_for(
    runtime._handle_message(None, report_ack.model_dump_json()),
    timeout=1,
  )

  assert journal.pending_reports() == []
  release_request.set()
  await asyncio.wait_for(runtime._market_requests.join(), timeout=1)
  sent_envelopes = [
    AgentEnvelope.model_validate_json(serialized)
    for serialized in socket.sent
  ]
  assert any(
    envelope.message_type is AgentMessageType.HEARTBEAT
    and envelope.payload["status"] == "READY"
    for envelope in sent_envelopes
  )
  assert socket.closed == []
  worker.cancel()
  await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_live_callback_sink_persists_reports_before_websocket_send(
  tmp_path,
) -> None:
  journal = LocalJournal(tmp_path / "journal.sqlite3")
  client_order_id = "client-order-1234567890-unique"
  journal.begin_command(
    "message-1",
    {"client_order_id": client_order_id, "volume": 100},
  )
  callbacks = []
  sink = _LiveReportSink(
    "account-1",
    journal,
    on_report=lambda: callbacks.append("persisted"),
  )

  await sink.handle_order_callback(
    SimpleNamespace(
      order_id=123456,
      stock_code="600000.SH",
      order_type=23,
      order_volume=100,
      price_type=50,
      price=10.5,
      traded_volume=0,
      traded_price=0,
      order_status=50,
      status_msg="accepted",
      order_remark=f"qx:{client_order_id[:20]}",
    )
  )
  await sink.handle_trade_callback(
    SimpleNamespace(
      order_id=123456,
      traded_id="trade-1",
      stock_code="600000.SH",
      order_type=23,
      traded_time=123,
      traded_price=10.5,
      traded_volume=100,
      traded_amount=1050,
      order_remark=f"qx:{client_order_id[:20]}",
    )
  )
  await sink.handle_position_update(
    SimpleNamespace(
      stock_code="600000.SH",
      volume=100,
      can_use_volume=0,
      avg_price=10.5,
      market_value=1050,
    )
  )

  reports = [
    AgentEnvelope.model_validate_json(value)
    for value in journal.pending_reports()
  ]
  assert [item.message_type for item in reports] == [
    AgentMessageType.ORDER_REPORT,
    AgentMessageType.EXECUTION_REPORT,
    AgentMessageType.DELTA_REPORT,
  ]
  assert reports[0].payload["client_order_id"] == client_order_id
  assert reports[1].payload["client_order_id"] == client_order_id
  assert reports[2].payload["is_complete"] is False
  assert reports[2].payload["position_deltas"][0]["account_id"] == "account-1"
  assert callbacks == ["persisted", "persisted", "persisted"]


def test_local_market_streamer_is_idempotent_and_resets() -> None:
  callbacks = []
  subscriptions = []
  unsubscribed = []

  class FakeDataManager:
    def subscribe_quote(self, stock_code, **kwargs):
      assert stock_code == "600000.SH"
      subscriptions.append(kwargs)
      callbacks.append(kwargs["callback"])
      return 101

    def unsubscribe_quote(self, subscription_id):
      unsubscribed.append(subscription_id)

  events = []
  streamer = _LocalMarketStreamer(FakeDataManager())
  payload = {
    "subscription_id": "remote-1",
    "kind": "quote",
    "stock_code": "600000.SH",
    "period": "1m",
    "start_time": "20260813000000",
    "end_time": "20260813235959",
    "count": -1,
  }

  assert streamer.subscribe(payload, events.append)
  assert streamer.subscribe(payload, events.append)
  assert len(callbacks) == 1
  assert subscriptions[0]["period"] == "1m"
  assert subscriptions[0]["start_time"] == "20260813000000"
  assert subscriptions[0]["end_time"] == "20260813235959"
  assert subscriptions[0]["count"] == -1
  callbacks[0]({"600000.SH": [{"time": 1, "close": 10.5}]})
  assert events == [
    {
      "subscription_id": "remote-1",
      "kind": "quote",
      "stock_code": "600000.SH",
      "period": "1m",
      "data": {"600000.SH": [{"time": 1, "close": 10.5}]},
    }
  ]

  streamer.reset()
  assert unsubscribed == [101]


def test_whole_market_streamer_enriches_ticks_with_qmt_limit_metadata() -> None:
  callbacks = []
  detail_batches = []

  class FakeDataManager:
    def get_stock_list_in_sector(self, sector):
      assert sector == "沪深A股"
      return ["600000.SH", "300001.SZ"]

    def get_instrument_detail_list(self, codes, iscomplete=False):
      detail_batches.append(list(codes))
      assert iscomplete is True
      return {
        "600000.SH": {"UpStopPrice": 11.0, "PriceTick": 0.01},
        "300001.SZ": {"UpStopPrice": 24.0, "PriceTick": 0.01},
      }

    def subscribe_whole_quote(self, markets, callback):
      assert markets == ["SH", "SZ"]
      callbacks.append(callback)
      return 202

    def get_full_tick(self, codes):
      assert tuple(codes) == ("300001.SZ", "600000.SH")
      return {
        "600000.SH": {"lastPrice": 10.8},
        "300001.SZ": {"lastPrice": 23.5},
      }

    def unsubscribe_quote(self, subscription_id):
      del subscription_id

  events = []
  streamer = _LocalMarketStreamer(FakeDataManager())
  assert streamer.subscribe_whole_market(events.append)
  assert streamer.subscribe_whole_market(events.append)
  assert detail_batches == [["300001.SZ", "600000.SH"]]
  raw_ticks = {
    "600000.SH": {"lastPrice": 10.8},
    "300001.SZ": {
      "lastPrice": 23.5,
      "upperLimit": 24.0,
      "priceTick": 0.02,
    },
  }
  callbacks[0](raw_ticks)

  assert events[0] is raw_ticks
  ticks = streamer.prepare_whole_market_data(events[0])
  assert ticks["600000.SH"]["upperLimit"] == 11.0
  assert ticks["600000.SH"]["priceTick"] == 0.01
  assert ticks["300001.SZ"]["upperLimit"] == 24.0
  assert ticks["300001.SZ"]["priceTick"] == 0.02

  snapshot = streamer.whole_market_snapshot()
  assert snapshot["600000.SH"]["upperLimit"] == 11.0
  assert detail_batches == [["300001.SZ", "600000.SH"]]


def test_data_only_simulator_rejects_trade_commands() -> None:
  broker = SimulatorBroker({"account-1"}, data_only=True)
  result = broker.execute(
    {
      "client_order_id": "client-1",
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "volume": 100,
    }
  )
  assert result == {"accepted": False, "reason": "data_only_agent"}


def test_qmt_data_broker_uses_local_data_capability(monkeypatch) -> None:
  pytest.importorskip(
    "xtquant",
    reason="miniQMT SDK is only available on the QMT host",
  )
  from quantx_qmt_agent.miniqmt import manager_registry

  class FakeDataManager:
    def get_market_data(self, **kwargs):
      import pandas as pd

      assert kwargs["stock_list"] == ["600000.SH"]
      return {
        "600000.SH": pd.DataFrame(
          [
            {
              "time": 1_735_776_000_000,
              "open": 10,
              "high": 11,
              "low": 9,
              "close": 10.5,
            }
          ]
        )
      }

  monkeypatch.setattr(
    manager_registry.XTDataManagerRegistry,
    "get_manager",
    lambda self: FakeDataManager(),
  )
  broker = QmtDataBroker(set(), data_only=True)

  records = broker.market_data(
    {
      "operation": "bars",
      "stock_list": ["600000.SH"],
      "periods": ["1d"],
      "start_time": "20250102",
      "end_time": "20250102",
    }
  )

  assert records[0]["code"] == "600000.SH"
  assert records[0]["period"] == "1d"
  assert records[0]["close"] == 10.5


def test_qmt_data_broker_fails_closed_when_xtdata_is_disconnected(
  monkeypatch,
) -> None:
  pytest.importorskip(
    "xtquant",
    reason="miniQMT SDK is only available on the QMT host",
  )
  from quantx_qmt_agent.miniqmt import manager_registry
  from quantx_qmt_agent.miniqmt.data.data_manager import (
    XTDataUnavailableError,
  )

  class DisconnectedDataManager:
    is_connected = False
    last_connection_error = "verified endpoint unavailable"

    @staticmethod
    def ensure_connected() -> bool:
      return False

    @staticmethod
    def get_market_data(**_kwargs):
      raise AssertionError("query must not run while XTData is disconnected")

  monkeypatch.setattr(
    manager_registry.XTDataManagerRegistry,
    "get_manager",
    lambda self: DisconnectedDataManager(),
  )
  broker = QmtDataBroker(set(), data_only=True)

  assert broker.is_market_data_ready() is False
  with pytest.raises(XTDataUnavailableError, match="verified endpoint unavailable"):
    broker.market_data(
      {
        "operation": "bars",
        "stock_list": ["600000.SH"],
        "periods": ["1d"],
        "start_time": "20250102",
        "end_time": "20250102",
      }
    )


def test_qmt_data_broker_downloads_requested_history_before_read(
  monkeypatch,
) -> None:
  pytest.importorskip(
    "xtquant",
    reason="miniQMT SDK is only available on the QMT host",
  )
  from quantx_qmt_agent.miniqmt import manager_registry

  calls: list[tuple[str, dict]] = []

  class FakeDataManager:
    def download_market_data(self, **kwargs):
      calls.append(("download", kwargs))
      return {}

    def get_market_data(self, **kwargs):
      import pandas as pd

      calls.append(("read", kwargs))
      return {
        "600000.SH": pd.DataFrame(
          [
            {
              "time": 20260723,
              "open": 10,
              "high": 11,
              "low": 9,
              "close": 10.5,
            }
          ]
        )
      }

  monkeypatch.setattr(
    manager_registry.XTDataManagerRegistry,
    "get_manager",
    lambda self: FakeDataManager(),
  )
  broker = QmtDataBroker(set(), data_only=True)

  broker.market_data(
    {
      "operation": "bars",
      "download": True,
      "stock_list": ["600000.SH"],
      "periods": ["1d"],
      "start_time": "20260723",
      "end_time": "20260729",
    }
  )

  assert [name for name, _ in calls] == ["download", "read"]
  assert calls[0][1] == {
    "stock_list": ["600000.SH"],
    "period": "1d",
    "start_time": "20260723",
    "end_time": "20260729",
    "incrementally": False,
  }
