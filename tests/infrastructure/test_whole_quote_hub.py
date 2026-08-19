import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from quantx_contracts import MarketBatchKind, MarketStreamBatch
from quantx_infrastructure.core.data.market_stream_transport import (
  MarketStreamFreshnessLease,
  MarketStreamState,
)
from quantx_infrastructure.core.data.whole_quote_hub import (
  QuoteConsumerStatus,
  QuoteDeliveryMode,
  WholeQuoteHub,
  WholeQuoteStatus,
)


class FakeSubscription:
  def __init__(self) -> None:
    self.queue: asyncio.Queue[bytes] = asyncio.Queue()
    self.closed = False

  async def wait_for_message(self, timeout=0.0):
    del timeout
    try:
      return self.queue.get_nowait()
    except asyncio.QueueEmpty:
      return None

  async def messages(self):
    while True:
      yield await self.queue.get()

  async def close(self):
    self.closed = True


class FakeStore:
  def __init__(self) -> None:
    self.calls = []
    self.subscription = FakeSubscription()
    self.snapshot = (
      MarketStreamState(
        status="READY",
        stream_id="stream-1",
        sequence=1,
        captured_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        instrument_count=1,
      ),
      {"600000.SH": {"lastPrice": 10.0, "time": 2_000}},
    )
    self.freshness = MarketStreamFreshnessLease(
      stream_id="stream-1",
      sequence=1,
    )
    self.watermarks = []

  async def open_subscription(self):
    self.calls.append("subscribe")
    return self.subscription

  async def load_snapshot(self):
    self.calls.append("snapshot")
    return self.snapshot

  async def state(self):
    return self.snapshot[0]

  async def state_with_freshness(self):
    return self.snapshot[0], self.freshness

  async def write_engine_state(self, **state):
    self.watermarks.append(state)


class AlwaysClosed:
  async def is_trading_hours(self, *_args):
    return False


class AlwaysOpen:
  async def is_trading_hours(self, *_args):
    return True


def batch(
  sequence,
  data,
  *,
  kind=MarketBatchKind.DELTA,
  captured_at=None,
):
  return MarketStreamBatch(
    stream_id="stream-1",
    sequence=sequence,
    kind=kind,
    captured_at=captured_at or datetime.now(timezone.utc),
    instrument_count=len(data),
    data=data,
  )


def set_authority(
  store: FakeStore,
  *,
  sequence: int,
  status: str = "READY",
  stream_id: str = "stream-1",
  captured_at: datetime | None = None,
  latest: dict | None = None,
  fresh: bool = True,
) -> None:
  _state, current_latest = store.snapshot
  authoritative_latest = current_latest if latest is None else latest
  observed_at = captured_at or datetime.now(timezone.utc)
  store.snapshot = (
    MarketStreamState(
      status=status,
      stream_id=stream_id,
      sequence=sequence,
      captured_at=observed_at,
      updated_at=datetime.now(timezone.utc),
      instrument_count=len(authoritative_latest),
    ),
    authoritative_latest,
  )
  store.freshness = (
    MarketStreamFreshnessLease(stream_id=stream_id, sequence=sequence)
    if fresh
    else None
  )


@pytest.mark.asyncio
async def test_hub_subscribes_before_hydrating_and_applies_delta() -> None:
  store = FakeStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysClosed())
  await hub.start()
  try:
    assert store.calls[:2] == ["subscribe", "snapshot"]
    assert hub.status is WholeQuoteStatus.READY
    assert hub.latest("600000.SH")["lastPrice"] == 10.0

    delta = batch(
      2,
      {"600000.SH": {"lastPrice": 10.2, "time": 3_000}},
    )
    set_authority(store, sequence=2, captured_at=delta.captured_at)
    await hub._apply_payload(delta.to_bytes())
    assert hub.sequence == 2
    assert hub.latest("600000.SH")["lastPrice"] == 10.2
  finally:
    await hub.stop()


@pytest.mark.asyncio
async def test_hub_drains_batches_published_during_snapshot_hydration() -> None:
  class RacingStore(FakeStore):
    async def load_snapshot(self):
      self.calls.append("snapshot")
      loaded = self.snapshot
      delta = batch(
        2,
        {"600000.SH": {"lastPrice": 10.2, "time": 3_000}},
      )
      await self.subscription.queue.put(delta.to_bytes())
      set_authority(
        self,
        sequence=2,
        captured_at=delta.captured_at,
        latest=delta.data,
      )
      return loaded

  store = RacingStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysClosed())
  await hub.start()
  try:
    assert store.calls[:2] == ["subscribe", "snapshot"]
    assert hub.sequence == 2
    assert hub.latest("600000.SH")["lastPrice"] == 10.2
  finally:
    await hub.stop()


@pytest.mark.asyncio
async def test_empty_ready_barrier_dispatches_buffered_snapshot() -> None:
  store = FakeStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysClosed())
  batch_updates: list[dict[str, dict]] = []
  tick_updates: list[dict[str, dict]] = []
  batch_handle = await hub.subscribe_batches(batch_updates.append)
  tick_handle = await hub.subscribe_tick("600000.SH", tick_updates.append)
  snapshot_data = {
    "600000.SH": {"lastPrice": 10.0, "time": 2_000},
    "000001.SZ": {"lastPrice": 12.0, "time": 2_000},
  }
  snapshot = batch(
    1,
    snapshot_data,
    kind=MarketBatchKind.SNAPSHOT,
  )
  barrier = batch(2, {})
  try:
    set_authority(
      store,
      sequence=1,
      status="SYNCING",
      captured_at=snapshot.captured_at,
      latest=snapshot_data,
    )
    await hub._apply_payload(snapshot.to_bytes())
    await asyncio.sleep(0)

    assert hub.status is WholeQuoteStatus.SYNCING
    assert batch_updates == []
    assert tick_updates == []

    set_authority(
      store,
      sequence=2,
      captured_at=barrier.captured_at,
      latest=snapshot_data,
    )
    await hub._apply_payload(barrier.to_bytes())
    for _ in range(10):
      if batch_updates and tick_updates:
        break
      await asyncio.sleep(0)

    assert hub.status is WholeQuoteStatus.READY
    assert batch_updates == [snapshot_data]
    assert tick_updates == [
      {"600000.SH": {"lastPrice": 10.0, "time": 2_000}}
    ]
  finally:
    await hub.unsubscribe(batch_handle)
    await hub.unsubscribe(tick_handle)


@pytest.mark.asyncio
async def test_hub_rejects_source_time_regression_and_recovers_gap() -> None:
  store = FakeStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysClosed())
  await hub.start()
  try:
    delta = batch(2, {"600000.SH": {"lastPrice": 10.2, "time": 3_000}})
    set_authority(store, sequence=2, captured_at=delta.captured_at)
    await hub._apply_payload(delta.to_bytes())
    regression = batch(
      3,
      {"600000.SH": {"lastPrice": 9.9, "time": 2_500}},
    )
    set_authority(store, sequence=3, captured_at=regression.captured_at)
    await hub._apply_payload(regression.to_bytes())
    assert hub.latest("600000.SH")["lastPrice"] == 10.2

    store.snapshot = (
      MarketStreamState(
        status="READY",
        stream_id="stream-1",
        sequence=5,
        captured_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        instrument_count=1,
      ),
      {"600000.SH": {"lastPrice": 10.8, "time": 5_000}},
    )
    store.freshness = MarketStreamFreshnessLease(
      stream_id="stream-1",
      sequence=5,
    )
    await hub._apply_payload(
      batch(5, {"600000.SH": {"lastPrice": 10.8, "time": 5_000}}).to_bytes()
    )
    assert hub.sequence_gaps == 1
    assert hub.sequence == 5
    assert hub.latest("600000.SH")["lastPrice"] == 10.8
  finally:
    await hub.stop()


@pytest.mark.asyncio
async def test_sequence_gap_invalidates_queued_delta_before_snapshot_recovery() -> None:
  store = FakeStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysClosed())
  entered = asyncio.Event()
  release = asyncio.Event()
  prices = []

  async def blocked(data):
    prices.append(data["600000.SH"]["lastPrice"])
    if len(prices) == 1:
      entered.set()
      await release.wait()

  await hub.start()
  handle = await hub.subscribe_tick("600000.SH", blocked)
  try:
    await entered.wait()
    delta = batch(2, {"600000.SH": {"lastPrice": 10.2, "time": 3_000}})
    set_authority(store, sequence=2, captured_at=delta.captured_at)
    await hub._apply_payload(delta.to_bytes())
    assert hub._consumers[handle].queue.qsize() == 1

    store.snapshot = (
      MarketStreamState(
        status="READY",
        stream_id="stream-1",
        sequence=4,
        captured_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        instrument_count=1,
      ),
      {"600000.SH": {"lastPrice": 10.8, "time": 5_000}},
    )
    store.freshness = MarketStreamFreshnessLease(
      stream_id="stream-1",
      sequence=4,
    )
    await hub._apply_payload(
      batch(4, {"600000.SH": {"lastPrice": 10.8, "time": 5_000}}).to_bytes()
    )
    release.set()
    for _ in range(10):
      if len(prices) >= 2:
        break
      await asyncio.sleep(0)

    assert prices == [10.0, 10.8]
    assert hub.invalidated_batches >= 1
    assert hub.status_snapshot()["invalidated_batches"] >= 1
  finally:
    release.set()
    await hub.unsubscribe(handle)
    await hub.stop()


@pytest.mark.asyncio
async def test_critical_consumer_overflow_recovers_from_latest_snapshot() -> None:
  store = FakeStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysClosed())
  blocker = asyncio.Event()

  async def blocked(_data):
    await blocker.wait()

  critical = await hub.subscribe_batches(blocked)
  latest = await hub.subscribe_batches(
    blocked,
    delivery=QuoteDeliveryMode.LATEST_ONLY,
  )
  try:
    for value in range(12):
      await hub._dispatch({"600000.SH": {"lastPrice": value}})
    assert hub.consumer_status(critical) is QuoteConsumerStatus.READY
    assert hub.consumer_status(latest) is QuoteConsumerStatus.READY
    assert hub.status is WholeQuoteStatus.READY
    assert hub._consumers[latest].coalesced_batches > 0
    assert hub._consumers[critical].lag_events == 1
    assert hub.invalidated_batches >= 8
    assert any(
      watermark["status"] == WholeQuoteStatus.SYNCING.value
      for watermark in store.watermarks
    )
  finally:
    blocker.set()
    await hub.unsubscribe(critical)
    await hub.unsubscribe(latest)


@pytest.mark.asyncio
async def test_overflow_cancels_hung_callback_before_worker_restart() -> None:
  store = FakeStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysClosed())
  entered = asyncio.Event()
  cancelled = asyncio.Event()
  recovered = asyncio.Event()
  invocations = 0

  async def hangs_once(_data):
    nonlocal invocations
    invocations += 1
    if invocations == 1:
      entered.set()
      try:
        await asyncio.Event().wait()
      except asyncio.CancelledError:
        cancelled.set()
        raise
    recovered.set()

  await hub.start()
  handle = await hub.subscribe_batches(hangs_once)
  try:
    await entered.wait()
    for value in range(9):
      await hub._dispatch({"600000.SH": {"lastPrice": value}})
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    await asyncio.wait_for(recovered.wait(), timeout=1.0)

    assert hub.consumer_status(handle) is QuoteConsumerStatus.READY
    assert hub.status is WholeQuoteStatus.READY
    assert hub._consumers[handle].lag_events == 1
    assert hub.invalidated_batches >= 8
  finally:
    await hub.unsubscribe(handle)
    await hub.stop()


@pytest.mark.asyncio
async def test_tick_fanout_is_indexed_and_reuses_per_symbol_payload() -> None:
  hub = WholeQuoteHub(store=FakeStore(), trading_time_service=AlwaysClosed())
  first_received = []
  second_received = []
  unrelated_received = []
  batch_received = []

  handles = [
    await hub.subscribe_tick("600000.SH", first_received.append),
    await hub.subscribe_tick("600000.SH", second_received.append),
    await hub.subscribe_tick("300001.SZ", unrelated_received.append),
    await hub.subscribe_batches(batch_received.append),
  ]
  payload = {"600000.SH": {"lastPrice": 10.2}}
  try:
    await hub._dispatch(payload)
    for _ in range(10):
      if first_received and second_received and batch_received:
        break
      await asyncio.sleep(0)

    assert first_received[0] is second_received[0]
    assert first_received[0] == payload
    assert batch_received[0] is payload
    assert unrelated_received == []
    assert set(hub._tick_consumers_by_code) == {"600000.SH", "300001.SZ"}
    assert hub.status_snapshot()["tick_consumers"] == 3
  finally:
    for handle in handles:
      await hub.unsubscribe(handle)

  assert hub._tick_consumers_by_code == {}
  assert hub._batch_consumers == {}


@pytest.mark.asyncio
async def test_latest_only_batch_coalescing_preserves_unchanged_symbols() -> None:
  hub = WholeQuoteHub(store=FakeStore(), trading_time_service=AlwaysClosed())
  handle = await hub.subscribe_batches(
    lambda _data: None,
    delivery=QuoteDeliveryMode.LATEST_ONLY,
  )
  consumer = hub._consumers[handle]
  try:
    await hub._dispatch({"600000.SH": {"lastPrice": 10.0}})
    await hub._dispatch({"000001.SZ": {"lastPrice": 12.0}})
    await hub._dispatch({"600000.SH": {"lastPrice": 10.2}})

    merged = consumer.queue.get_nowait()
    consumer.queue.task_done()
    assert merged == {
      "600000.SH": {"lastPrice": 10.2},
      "000001.SZ": {"lastPrice": 12.0},
    }
    assert consumer.coalesced_batches == 2
    assert hub.status_snapshot()["coalesced_batches"] == 2
  finally:
    await hub.unsubscribe(handle)


@pytest.mark.asyncio
async def test_critical_callback_failure_closes_realtime_gate() -> None:
  store = FakeStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysClosed())

  def fail(_data):
    raise RuntimeError("consumer failed")

  handle = await hub.subscribe_batches(fail)
  try:
    await hub._dispatch({"600000.SH": {"lastPrice": 10.0}})
    for _ in range(10):
      if hub.consumer_status(handle) is QuoteConsumerStatus.LAGGING:
        break
      await asyncio.sleep(0)

    assert hub.consumer_status(handle) is QuoteConsumerStatus.LAGGING
    assert hub.status is WholeQuoteStatus.STALE
    assert store.watermarks[-1]["reason"] == (
      "critical quote consumer callback failed"
    )
    assert hub.status_snapshot()["consumer_lag_events"] == 1
  finally:
    await hub.unsubscribe(handle)


@pytest.mark.asyncio
async def test_large_snapshot_preparation_is_offloaded(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  store = FakeStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysClosed())
  hub._SNAPSHOT_OFFLOAD_INSTRUMENTS = 1
  offloaded = []

  async def run_in_thread(function, *args):
    offloaded.append(function.__name__)
    return function(*args)

  monkeypatch.setattr(asyncio, "to_thread", run_in_thread)
  await hub._apply_payload(
    batch(
      1,
      {"600000.SH": {"lastPrice": 10.0, "time": 2_000}},
      kind=MarketBatchKind.SNAPSHOT,
    ).to_bytes()
  )

  assert "_build_snapshot_state" in offloaded
  assert hub.latest("600000.SH")["lastPrice"] == 10.0


@pytest.mark.asyncio
async def test_hub_marks_active_session_stale_and_recovers_on_next_batch() -> None:
  store = FakeStore()
  hub = WholeQuoteHub(
    store=store,
    trading_time_service=AlwaysOpen(),
    stale_after_seconds=1,
  )
  await hub.start()
  try:
    hub._last_received_monotonic -= 2
    await asyncio.sleep(1.1)
    assert hub.status is WholeQuoteStatus.STALE

    delta = batch(2, {"600000.SH": {"lastPrice": 10.2, "time": 3_000}})
    set_authority(store, sequence=2, captured_at=delta.captured_at)
    await hub._apply_payload(delta.to_bytes())
    assert hub.status is WholeQuoteStatus.READY

    ready_state, latest = store.snapshot
    store.snapshot = (
      MarketStreamState(
        status="OFFLINE",
        stream_id=ready_state.stream_id,
        sequence=hub.sequence,
        captured_at=hub.last_captured_at,
        updated_at=datetime.now(timezone.utc),
        instrument_count=len(latest),
      ),
      latest,
    )
    store.freshness = None
    await asyncio.sleep(1.1)
    assert hub.status is WholeQuoteStatus.OFFLINE
  finally:
    await hub.stop()


@pytest.mark.asyncio
async def test_agent_clock_30_seconds_slow_with_valid_lease_stays_ready() -> None:
  store = FakeStore()
  hub = WholeQuoteHub(
    store=store,
    trading_time_service=AlwaysOpen(),
    stale_after_seconds=10,
  )
  prices: list[float] = []
  await hub.start()
  handle = await hub.subscribe_tick(
    "600000.SH",
    lambda data: prices.append(data["600000.SH"]["lastPrice"]),
  )
  try:
    await asyncio.sleep(0)
    old_capture = datetime.now(timezone.utc) - timedelta(seconds=30)
    for sequence, price in ((2, 10.2), (3, 10.3)):
      delta = batch(
        sequence,
        {"600000.SH": {"lastPrice": price, "time": sequence * 1_000}},
        captured_at=old_capture,
      )
      set_authority(
        store,
        sequence=sequence,
        captured_at=old_capture,
      )
      await hub._apply_payload(delta.to_bytes())

      assert hub.status is WholeQuoteStatus.READY

    for _ in range(10):
      if len(prices) == 3:
        break
      await asyncio.sleep(0)
    assert prices == [10.0, 10.2, 10.3]
    assert hub.last_batch_age_seconds >= 25
    assert hub.authority_rejections == 0
  finally:
    await hub.unsubscribe(handle)
    await hub.stop()


@pytest.mark.asyncio
async def test_api_offline_during_decode_cannot_reopen_gate(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  store = FakeStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysOpen())
  await hub.start()
  try:
    delta = batch(2, {"600000.SH": {"lastPrice": 10.2, "time": 3_000}})
    set_authority(store, sequence=2, captured_at=delta.captured_at)
    hub._DECODE_OFFLOAD_BYTES = 1

    async def decode_then_disconnect(function, *args):
      decoded = function(*args)
      set_authority(
        store,
        sequence=2,
        status="OFFLINE",
        captured_at=delta.captured_at,
        fresh=False,
      )
      return decoded

    monkeypatch.setattr(asyncio, "to_thread", decode_then_disconnect)
    await hub._apply_payload(delta.to_bytes())

    assert hub.sequence == 2
    assert hub.status is WholeQuoteStatus.OFFLINE
    assert store.watermarks[-1]["status"] == WholeQuoteStatus.OFFLINE.value
  finally:
    await hub.stop()


@pytest.mark.asyncio
async def test_ready_hub_dispatches_ordered_batches_while_api_is_ahead() -> None:
  store = FakeStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysOpen())
  received: list[dict[str, dict]] = []
  await hub.start()
  handle = await hub.subscribe_batches(received.append)
  try:
    await asyncio.sleep(0)
    second = batch(
      2,
      {"600000.SH": {"lastPrice": 10.2, "time": 3_000}},
    )
    third = batch(
      3,
      {"000001.SZ": {"lastPrice": 12.0, "time": 4_000}},
    )
    fourth = batch(
      4,
      {"600000.SH": {"lastPrice": 10.4, "time": 5_000}},
    )
    set_authority(store, sequence=4, captured_at=fourth.captured_at)

    await hub._apply_payload(second.to_bytes())
    assert hub.sequence == 2
    assert hub.status is WholeQuoteStatus.READY

    await hub._apply_payload(third.to_bytes())
    assert hub.sequence == 3
    assert hub.status is WholeQuoteStatus.READY

    await hub._apply_payload(fourth.to_bytes())
    for _ in range(10):
      if len(received) == 4:
        break
      await asyncio.sleep(0)

    assert hub.sequence == 4
    assert hub.status is WholeQuoteStatus.READY
    assert received[1:] == [
      {"600000.SH": {"lastPrice": 10.2, "time": 3_000}},
      {"000001.SZ": {"lastPrice": 12.0, "time": 4_000}},
      {"600000.SH": {"lastPrice": 10.4, "time": 5_000}},
    ]
  finally:
    await hub.unsubscribe(handle)
    await hub.stop()


@pytest.mark.asyncio
async def test_slow_agent_clock_does_not_reject_hydrated_snapshot() -> None:
  store = FakeStore()
  stale_state, latest = store.snapshot
  store.snapshot = (
    MarketStreamState(
      status="READY",
      stream_id=stale_state.stream_id,
      sequence=stale_state.sequence,
      captured_at=datetime.now(timezone.utc) - timedelta(seconds=20),
      updated_at=datetime.now(timezone.utc) - timedelta(seconds=20),
      instrument_count=stale_state.instrument_count,
    ),
    latest,
  )
  hub = WholeQuoteHub(
    store=store,
    trading_time_service=AlwaysOpen(),
    stale_after_seconds=10,
  )

  await hub.start()
  try:
    assert hub.status is WholeQuoteStatus.READY
    assert hub.snapshot() == latest
    assert hub.last_batch_age_seconds >= 15
  finally:
    await hub.stop()


@pytest.mark.asyncio
async def test_hydration_freshness_uses_local_monotonic_receipt_time() -> None:
  store = FakeStore()
  state, latest = store.snapshot
  slow_clock = datetime.now(timezone.utc) - timedelta(seconds=30)
  store.snapshot = (
    MarketStreamState(
      status="READY",
      stream_id=state.stream_id,
      sequence=state.sequence,
      captured_at=slow_clock,
      updated_at=slow_clock,
      instrument_count=state.instrument_count,
    ),
    latest,
  )
  hub = WholeQuoteHub(
    store=store,
    trading_time_service=AlwaysOpen(),
    stale_after_seconds=2,
  )

  assert await hub._hydrate_from_store()
  assert hub.status is WholeQuoteStatus.READY
  assert time.monotonic() - hub._last_received_monotonic < 0.5

  hub._last_received_monotonic -= 3
  await hub._check_freshness_once()

  assert hub.status is WholeQuoteStatus.STALE


@pytest.mark.asyncio
async def test_persistent_api_lead_recovers_from_authoritative_snapshot() -> None:
  store = FakeStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysClosed())
  assert await hub._hydrate_from_store()
  prices: list[float] = []
  handle = await hub.subscribe_tick(
    "600000.SH",
    lambda data: prices.append(data["600000.SH"]["lastPrice"]),
  )
  try:
    await asyncio.sleep(0)
    set_authority(
      store,
      sequence=3,
      latest={"600000.SH": {"lastPrice": 10.3, "time": 4_000}},
    )

    await hub._check_freshness_once()
    assert hub.sequence == 1
    assert hub.status is WholeQuoteStatus.READY

    assert hub._authority_ahead_since_monotonic is not None
    hub._authority_ahead_since_monotonic -= 2
    hub._last_sequence_progress_monotonic -= 2
    await hub._check_freshness_once()
    for _ in range(10):
      if prices[-1:] == [10.3]:
        break
      await asyncio.sleep(0)

    assert hub.sequence == 3
    assert hub.status is WholeQuoteStatus.READY
    assert prices == [10.0, 10.3]
  finally:
    await hub.unsubscribe(handle)


@pytest.mark.asyncio
async def test_missing_freshness_lease_keeps_active_session_stale() -> None:
  store = FakeStore()
  store.freshness = None
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysOpen())

  assert not await hub._hydrate_from_store()
  assert hub.status is WholeQuoteStatus.STALE
  assert hub.snapshot() == {}


@pytest.mark.asyncio
async def test_processing_over_freshness_window_keeps_gate_stale() -> None:
  store = FakeStore()
  hub = WholeQuoteHub(
    store=store,
    trading_time_service=AlwaysOpen(),
    stale_after_seconds=10,
  )

  accepted = await hub._validate_authoritative_ready(
    stream_id="stream-1",
    sequence=1,
    captured_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    received_monotonic=time.monotonic() - 11,
    allow_authority_ahead=False,
  )

  assert not accepted
  assert hub.status is WholeQuoteStatus.STALE
  assert hub.last_processing_age_ms >= 10_000
