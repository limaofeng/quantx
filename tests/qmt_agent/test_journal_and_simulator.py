import asyncio
import threading
from types import SimpleNamespace

import pytest
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_qmt_agent import broker as broker_module
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
      return {
        "沪深A股": ["600000.SH", "300001.SZ"],
        "沪深指数": ["000001.SH", "399001.SZ"],
      }[sector]

    def get_instrument_detail_list(self, codes, iscomplete=False):
      detail_batches.append(list(codes))
      assert iscomplete is True
      return {
        "600000.SH": {"UpStopPrice": 11.0, "PriceTick": 0.01},
        "300001.SZ": {"UpStopPrice": 24.0, "PriceTick": 0.01},
      }

    def subscribe_whole_quote(self, codes, callback):
      assert codes == [
        "000001.SH",
        "300001.SZ",
        "399001.SZ",
        "600000.SH",
      ]
      callbacks.append(callback)
      return 202

    def get_full_tick(self, codes):
      assert tuple(codes) == (
        "000001.SH",
        "300001.SZ",
        "399001.SZ",
        "600000.SH",
      )
      return {
        "000001.SH": {"lastPrice": 3500.0},
        "600000.SH": {"lastPrice": 10.8, "unused": "drop-me"},
        "300001.SZ": {"lastPrice": 23.5},
        "399001.SZ": {"lastPrice": 11000.0},
        "510300.SH": {"lastPrice": 4.5},
      }

    def unsubscribe_quote(self, subscription_id):
      del subscription_id

  events = []
  streamer = _LocalMarketStreamer(FakeDataManager())
  assert streamer.subscribe_whole_market(events.append)
  assert streamer.subscribe_whole_market(events.append)
  assert detail_batches == [["000001.SH", "300001.SZ", "399001.SZ", "600000.SH"]]
  raw_ticks = {
    "000001.SH": {"lastPrice": 3501.0},
    "600000.SH": {"lastPrice": 10.8},
    "300001.SZ": {
      "lastPrice": 23.5,
      "upperLimit": 24.0,
      "priceTick": 0.02,
    },
    "399001.SZ": {"lastPrice": 11001.0},
    "510300.SH": {"lastPrice": 4.5},
  }
  callbacks[0](raw_ticks)

  assert set(events[0]) == {
    "000001.SH",
    "300001.SZ",
    "399001.SZ",
    "600000.SH",
  }
  assert "510300.SH" not in events[0]
  ticks = streamer.prepare_whole_market_data(events[0])
  assert ticks["600000.SH"]["upperLimit"] == 11.0
  assert ticks["600000.SH"]["priceTick"] == 0.01
  assert ticks["300001.SZ"]["upperLimit"] == 24.0
  assert ticks["300001.SZ"]["priceTick"] == 0.02
  assert "510300.SH" not in streamer.prepare_whole_market_data(raw_ticks)

  snapshot = streamer.whole_market_snapshot()
  assert snapshot["600000.SH"]["upperLimit"] == 11.0
  assert "510300.SH" not in snapshot
  assert "unused" not in streamer.prepare_whole_market_data(snapshot)["600000.SH"]
  assert detail_batches == [["000001.SH", "300001.SZ", "399001.SZ", "600000.SH"]]


def test_whole_market_universe_change_rebinds_without_subscription_overlap() -> None:
  sectors = {
    "沪深A股": ["600000.SH"],
    "沪深指数": ["000001.SH"],
  }
  callbacks = []
  operations = []
  active_native_subscriptions = 0

  class FakeDataManager:
    @staticmethod
    def get_stock_list_in_sector(sector):
      return list(sectors[sector])

    @staticmethod
    def get_instrument_detail_list(codes, iscomplete=False):
      assert iscomplete is True
      return {code: {} for code in codes}

    @staticmethod
    def get_full_tick(codes):
      return {code: {"lastPrice": 10.0} for code in codes}

    @staticmethod
    def subscribe_whole_quote(codes, callback):
      nonlocal active_native_subscriptions
      assert active_native_subscriptions == 0
      active_native_subscriptions += 1
      subscription_id = len(callbacks) + 1
      callbacks.append(callback)
      operations.append(("subscribe", subscription_id, list(codes)))
      return subscription_id

    @staticmethod
    def unsubscribe_quote(subscription_id):
      nonlocal active_native_subscriptions
      assert active_native_subscriptions == 1
      active_native_subscriptions -= 1
      operations.append(("unsubscribe", subscription_id))

  events = []
  streamer = _LocalMarketStreamer(FakeDataManager())
  assert streamer.subscribe_whole_market(events.append)
  initial_generation = streamer.whole_market_universe_generation()
  assert streamer.whole_market_codes() == ("000001.SH", "600000.SH")

  sectors["沪深A股"] = ["600000.SH", "600001.SH"]
  assert streamer._refresh_whole_quote_metadata(["SH", "SZ"])
  assert streamer.whole_market_universe_generation() == initial_generation + 1
  # The active universe must remain identical to the still-live native source.
  assert streamer.whole_market_codes() == ("000001.SH", "600000.SH")

  callbacks[0](
    {
      "600000.SH": {"lastPrice": 10.0},
      "600001.SH": {"lastPrice": 11.0},
    }
  )
  assert set(events[-1]) == {"600000.SH"}

  streamer.unsubscribe_whole_market()
  event_count = len(events)
  callbacks[0]({"600000.SH": {"lastPrice": 10.1}})
  assert len(events) == event_count

  assert streamer.subscribe_whole_market(events.append)
  assert streamer.whole_market_codes() == (
    "000001.SH",
    "600000.SH",
    "600001.SH",
  )
  assert set(streamer.whole_market_snapshot()) == {
    "000001.SH",
    "600000.SH",
    "600001.SH",
  }
  callbacks[1]({"600001.SH": {"lastPrice": 11.1}})
  assert set(events[-1]) == {"600001.SH"}
  assert operations == [
    ("subscribe", 1, ["000001.SH", "600000.SH"]),
    ("unsubscribe", 1),
    ("subscribe", 2, ["000001.SH", "600000.SH", "600001.SH"]),
  ]

  stable_generation = streamer.whole_market_universe_generation()
  assert streamer._refresh_whole_quote_metadata(["SH", "SZ"])
  assert streamer.whole_market_universe_generation() == stable_generation


def test_whole_market_refresh_waits_for_native_subscription_binding() -> None:
  sectors = {
    "沪深A股": ["600000.SH"],
    "沪深指数": ["000001.SH"],
  }
  second_subscribe_started = threading.Event()
  release_second_subscribe = threading.Event()
  refresh_finished = threading.Event()
  subscribe_calls = 0

  class FakeDataManager:
    @staticmethod
    def get_stock_list_in_sector(sector):
      return list(sectors[sector])

    @staticmethod
    def get_instrument_detail_list(codes, iscomplete=False):
      assert iscomplete is True
      return {code: {} for code in codes}

    @staticmethod
    def subscribe_whole_quote(_codes, callback):
      del callback
      nonlocal subscribe_calls
      subscribe_calls += 1
      if subscribe_calls == 2:
        second_subscribe_started.set()
        assert release_second_subscribe.wait(timeout=2)
      return subscribe_calls

    @staticmethod
    def unsubscribe_quote(_subscription_id):
      return None

  streamer = _LocalMarketStreamer(FakeDataManager())
  assert streamer.subscribe_whole_market(lambda _data: None)
  streamer.unsubscribe_whole_market()
  bound_generation = streamer.whole_market_universe_generation()

  subscribe_thread = threading.Thread(
    target=lambda: streamer.subscribe_whole_market(lambda _data: None),
  )
  subscribe_thread.start()
  assert second_subscribe_started.wait(timeout=2)

  sectors["沪深A股"] = ["600000.SH", "600001.SH"]

  def refresh() -> None:
    streamer._refresh_whole_quote_metadata(["SH", "SZ"])
    refresh_finished.set()

  refresh_thread = threading.Thread(target=refresh)
  refresh_thread.start()
  assert not refresh_finished.wait(timeout=0.05)
  assert streamer.whole_market_codes() == ("000001.SH", "600000.SH")

  release_second_subscribe.set()
  subscribe_thread.join(timeout=2)
  refresh_thread.join(timeout=2)
  assert not subscribe_thread.is_alive()
  assert not refresh_thread.is_alive()
  assert refresh_finished.is_set()

  assert streamer.whole_market_codes() == ("000001.SH", "600000.SH")
  assert streamer.whole_market_bound_universe_generation() == bound_generation
  desired_generation = streamer.whole_market_universe_generation()
  assert desired_generation == bound_generation + 1
  assert broker_module._combine_market_data_source_generation(
    1,
    streamer.whole_market_bound_universe_generation(),
  ) != broker_module._combine_market_data_source_generation(
    1,
    desired_generation,
  )


def test_whole_market_universe_refresh_failure_is_rate_limited(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  refresh_attempts = 0
  monotonic_now = 100.0

  class FakeDataManager:
    @staticmethod
    def get_stock_list_in_sector(_sector):
      nonlocal refresh_attempts
      refresh_attempts += 1
      raise RuntimeError("sector table unavailable")

  class ImmediateThread:
    def __init__(self, *, target, args, name, daemon):
      del name, daemon
      self._target = target
      self._args = args

    def start(self):
      self._target(*self._args)

  monkeypatch.setattr(broker_module.threading, "Thread", ImmediateThread)
  monkeypatch.setattr(broker_module.time, "monotonic", lambda: monotonic_now)
  streamer = _LocalMarketStreamer(FakeDataManager())

  streamer._ensure_whole_quote_metadata_current(["SH", "SZ"])
  streamer._ensure_whole_quote_metadata_current(["SH", "SZ"])
  assert refresh_attempts == 1
  assert streamer._whole_quote_active_universe is None

  monotonic_now += broker_module.WHOLE_QUOTE_METADATA_REFRESH_RETRY_SECONDS + 1
  streamer._ensure_whole_quote_metadata_current(["SH", "SZ"])
  assert refresh_attempts == 2


def test_whole_market_subscription_metadata_failure_honors_retry_interval(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  refresh_attempts = 0
  monotonic_now = 100.0

  class FakeDataManager:
    @staticmethod
    def get_stock_list_in_sector(_sector):
      nonlocal refresh_attempts
      refresh_attempts += 1
      raise RuntimeError("sector table unavailable")

  monkeypatch.setattr(broker_module.time, "monotonic", lambda: monotonic_now)
  streamer = _LocalMarketStreamer(FakeDataManager())

  assert not streamer.subscribe_whole_market(lambda _data: None)
  assert not streamer.subscribe_whole_market(lambda _data: None)
  assert refresh_attempts == 1

  monotonic_now += broker_module.WHOLE_QUOTE_METADATA_REFRESH_RETRY_SECONDS + 1
  assert not streamer.subscribe_whole_market(lambda _data: None)
  assert refresh_attempts == 2


def test_failed_whole_market_subscription_drains_inflight_callback() -> None:
  callback_started = threading.Event()
  release_callback = threading.Event()
  callback_finished = threading.Event()
  native_returned = threading.Event()
  subscription_result = []

  class FakeDataManager:
    @staticmethod
    def get_stock_list_in_sector(sector):
      return {
        "沪深A股": ["600000.SH"],
        "沪深指数": ["000001.SH"],
      }[sector]

    @staticmethod
    def get_instrument_detail_list(codes, iscomplete=False):
      assert iscomplete is True
      return {code: {} for code in codes}

    @staticmethod
    def subscribe_whole_quote(_codes, callback):
      callback_thread = threading.Thread(
        target=lambda: callback({"600000.SH": {"lastPrice": 10.0}}),
      )
      callback_thread.start()
      assert callback_started.wait(timeout=2)
      native_returned.set()
      return -1

  def consume(_data) -> None:
    callback_started.set()
    assert release_callback.wait(timeout=2)
    callback_finished.set()

  streamer = _LocalMarketStreamer(FakeDataManager())
  subscribe_thread = threading.Thread(
    target=lambda: subscription_result.append(
      streamer.subscribe_whole_market(consume)
    ),
  )
  subscribe_thread.start()
  assert native_returned.wait(timeout=2)
  assert subscribe_thread.is_alive()

  release_callback.set()
  subscribe_thread.join(timeout=2)
  assert not subscribe_thread.is_alive()
  assert callback_finished.is_set()
  assert subscription_result == [False]


def test_whole_market_snapshot_uses_bounded_native_fragments_and_merges(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  codes = (
    "000001.SH",
    "000002.SZ",
    "300001.SZ",
    "399001.SZ",
    "600000.SH",
  )
  native_calls: list[list[str]] = []

  class FakeDataManager:
    @staticmethod
    def get_full_tick(batch):
      native_calls.append(list(batch))
      return {
        code: {"lastPrice": float(index + 1), "unused": "drop-me"}
        for index, code in enumerate(batch)
        if code != "600000.SH"
      }

  monkeypatch.setattr(
    broker_module,
    "WHOLE_QUOTE_SNAPSHOT_BATCH_SIZE",
    2,
  )
  streamer = _LocalMarketStreamer(FakeDataManager())
  streamer._whole_quote_active_universe = streamer._build_whole_quote_universe(
    trading_date=broker_module.datetime.now(
      broker_module.SHANGHAI_TIMEZONE
    ).date(),
    codes=codes,
    metadata={},
  )

  snapshot = streamer.whole_market_snapshot()

  assert native_calls == [
    ["000001.SH", "000002.SZ"],
    ["300001.SZ", "399001.SZ"],
    ["600000.SH"],
  ]
  # A successful native query may legitimately omit an instrument for which
  # QMT has no current tick. Do not synthesize an empty quote.
  assert set(snapshot) == set(codes) - {"600000.SH"}
  assert all("unused" not in tick for tick in snapshot.values())


def test_whole_market_snapshot_fragment_failure_never_returns_partial_data(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  codes = ("000001.SH", "000002.SZ", "300001.SZ")
  native_calls: list[list[str]] = []
  preparation_calls = 0

  class FakeDataManager:
    @staticmethod
    def get_full_tick(batch):
      native_calls.append(list(batch))
      if len(native_calls) == 2:
        raise RuntimeError("native fragment failed")
      return {code: {"lastPrice": 1.0} for code in batch}

  monkeypatch.setattr(
    broker_module,
    "WHOLE_QUOTE_SNAPSHOT_BATCH_SIZE",
    2,
  )
  streamer = _LocalMarketStreamer(FakeDataManager())
  streamer._whole_quote_active_universe = streamer._build_whole_quote_universe(
    trading_date=broker_module.datetime.now(
      broker_module.SHANGHAI_TIMEZONE
    ).date(),
    codes=codes,
    metadata={},
  )
  original_prepare = streamer.prepare_whole_market_data

  def record_prepare(data):
    nonlocal preparation_calls
    preparation_calls += 1
    return original_prepare(data)

  monkeypatch.setattr(streamer, "prepare_whole_market_data", record_prepare)

  with pytest.raises(RuntimeError, match="native fragment failed"):
    streamer.whole_market_snapshot()

  assert native_calls == [
    ["000001.SH", "000002.SZ"],
    ["300001.SZ"],
  ]
  assert preparation_calls == 0


def test_whole_market_unsubscribe_does_not_hide_native_failure() -> None:
  class FakeDataManager:
    @staticmethod
    def unsubscribe_quote(subscription_id):
      raise RuntimeError(f"cannot cancel {subscription_id}")

  streamer = _LocalMarketStreamer(FakeDataManager())
  streamer._whole_quote_subscription = 202

  with pytest.raises(RuntimeError, match="failed to cancel"):
    streamer.unsubscribe_whole_market()


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
