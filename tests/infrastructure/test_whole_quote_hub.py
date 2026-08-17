import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from quantx_contracts import MarketBatchKind, MarketStreamBatch
from quantx_infrastructure.core.data.market_stream_transport import (
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
    self.watermarks = []

  async def open_subscription(self):
    self.calls.append("subscribe")
    return self.subscription

  async def load_snapshot(self):
    self.calls.append("snapshot")
    return self.snapshot

  async def state(self):
    return self.snapshot[0]

  async def write_engine_state(self, **state):
    self.watermarks.append(state)


class AlwaysClosed:
  async def is_trading_hours(self, *_args):
    return False


class AlwaysOpen:
  async def is_trading_hours(self, *_args):
    return True


def batch(sequence, data, *, kind=MarketBatchKind.DELTA):
  return MarketStreamBatch(
    stream_id="stream-1",
    sequence=sequence,
    kind=kind,
    captured_at=datetime.now(timezone.utc),
    instrument_count=len(data),
    data=data,
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

    await hub._apply_payload(
      batch(
        2,
        {"600000.SH": {"lastPrice": 10.2, "time": 3_000}},
      ).to_bytes()
    )
    assert hub.sequence == 2
    assert hub.latest("600000.SH")["lastPrice"] == 10.2
  finally:
    await hub.stop()


@pytest.mark.asyncio
async def test_hub_rejects_source_time_regression_and_recovers_gap() -> None:
  store = FakeStore()
  hub = WholeQuoteHub(store=store, trading_time_service=AlwaysClosed())
  await hub.start()
  try:
    await hub._apply_payload(
      batch(2, {"600000.SH": {"lastPrice": 10.2, "time": 3_000}}).to_bytes()
    )
    await hub._apply_payload(
      batch(3, {"600000.SH": {"lastPrice": 9.9, "time": 2_500}}).to_bytes()
    )
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
    await hub._apply_payload(
      batch(5, {"600000.SH": {"lastPrice": 10.8, "time": 5_000}}).to_bytes()
    )
    assert hub.sequence_gaps == 1
    assert hub.sequence == 5
    assert hub.latest("600000.SH")["lastPrice"] == 10.8
  finally:
    await hub.stop()


@pytest.mark.asyncio
async def test_critical_consumer_lag_is_explicit_and_ui_coalesces() -> None:
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
    assert hub.consumer_status(critical) is QuoteConsumerStatus.LAGGING
    assert hub.consumer_status(latest) is QuoteConsumerStatus.READY
    assert hub.status is WholeQuoteStatus.STALE
    assert hub._consumers[latest].coalesced_batches > 0
  finally:
    blocker.set()
    await hub.unsubscribe(critical)
    await hub.unsubscribe(latest)


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

    await hub._apply_payload(
      batch(2, {"600000.SH": {"lastPrice": 10.2, "time": 3_000}}).to_bytes()
    )
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
    await asyncio.sleep(1.1)
    assert hub.status is WholeQuoteStatus.OFFLINE
  finally:
    await hub.stop()


@pytest.mark.asyncio
async def test_hub_does_not_dispatch_stale_snapshot_during_active_session() -> None:
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
    assert hub.status is WholeQuoteStatus.STALE
    assert hub.snapshot() == {}
  finally:
    await hub.stop()
