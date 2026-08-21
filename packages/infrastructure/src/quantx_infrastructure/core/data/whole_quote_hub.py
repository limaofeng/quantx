"""Engine-local ordered fan-out for the authoritative SH/SZ quote stream."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable
from zoneinfo import ZoneInfo

from quantx_contracts import (
  MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS,
  MarketBatchKind,
  MarketStreamBatch,
  market_tick_source_time,
  validate_market_stream_capture_time,
)

from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.core.data.market_stream_transport import (
  BinaryMarketSubscription,
  MarketStreamStore,
  market_stream_store,
)
from quantx_infrastructure.services.trading_time_service import TradingTimeService

logger = logging.getLogger(__name__)


class WholeQuoteStatus(str, Enum):
  STARTING = "STARTING"
  SYNCING = "SYNCING"
  READY = "READY"
  STALE = "STALE"
  OFFLINE = "OFFLINE"


class QuoteDeliveryMode(str, Enum):
  CRITICAL = "CRITICAL"
  LATEST_ONLY = "LATEST_ONLY"


class QuoteConsumerStatus(str, Enum):
  READY = "READY"
  LAGGING = "LAGGING"
  STOPPED = "STOPPED"


@dataclass
class _Consumer:
  handle: str
  callback: Callable[[dict[str, dict[str, Any]]], Any]
  stock_code: str | None
  delivery: QuoteDeliveryMode
  queue: asyncio.Queue[dict[str, dict[str, Any]]]
  task: asyncio.Task[None]
  status: QuoteConsumerStatus = QuoteConsumerStatus.READY
  coalesced_batches: int = 0
  coalesced_updates: int = 0
  lag_events: int = 0
  invalidated_batches: int = 0
  lag_reason: str = ""


class WholeQuoteHub:
  """Hydrates Redis state, validates ordering, and fans out locally."""

  _DECODE_OFFLOAD_BYTES = 256 * 1024
  _SNAPSHOT_OFFLOAD_INSTRUMENTS = 1_000
  _CONSUMER_CANCEL_TIMEOUT_SECONDS = 1.0
  _AUTHORITY_AHEAD_GRACE_SECONDS = 1.0

  def __init__(
    self,
    *,
    store: MarketStreamStore = market_stream_store,
    trading_time_service: TradingTimeService | None = None,
    stale_after_seconds: float = MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS,
  ) -> None:
    self.store = store
    self.trading_time_service = trading_time_service or TradingTimeService()
    self.stale_after_seconds = max(1.0, float(stale_after_seconds))
    self.status = WholeQuoteStatus.OFFLINE
    self.stream_id = ""
    self.sequence = 0
    self.universe_count = 0
    self.universe_hash = ""
    self.last_captured_at: datetime | None = None
    self._latest: dict[str, dict[str, Any]] = {}
    self._source_times: dict[str, float] = {}
    self._consumers: dict[str, _Consumer] = {}
    self._batch_consumers: dict[str, _Consumer] = {}
    self._tick_consumers_by_code: dict[str, dict[str, _Consumer]] = {}
    self._subscription: BinaryMarketSubscription | None = None
    self._consume_task: asyncio.Task[None] | None = None
    self._freshness_task: asyncio.Task[None] | None = None
    self._consumer_recovery_lock = asyncio.Lock()
    self._running = False
    self._payloads_in_flight = 0
    self._last_received_monotonic = 0.0
    self._last_sequence_progress_monotonic = 0.0
    self._authority_ahead_since_monotonic: float | None = None
    self.sequence_gaps = 0
    self.resyncs = 0
    self.invalidated_batches = 0
    self.authority_rejections = 0
    self.last_batch_age_seconds = 0.0
    self.last_processing_age_ms = 0.0
    self.last_decode_ms = 0.0
    self.last_apply_ms = 0.0
    self.last_dispatch_ms = 0.0

  @property
  def is_ready(self) -> bool:
    return self.status is WholeQuoteStatus.READY

  async def start(self) -> None:
    if self._running:
      return
    self._running = True
    self._set_status(WholeQuoteStatus.STARTING)
    await self._open_and_hydrate()
    self._consume_task = asyncio.create_task(
      self._consume_forever(),
      name="whole-quote-hub-consumer",
    )
    self._freshness_task = asyncio.create_task(
      self._freshness_loop(),
      name="whole-quote-hub-freshness",
    )

  async def stop(self) -> None:
    self._running = False
    tasks = [
      task
      for task in (
        self._consume_task,
        self._freshness_task,
      )
      if task is not None
    ]
    for task in tasks:
      task.cancel()
    if tasks:
      await asyncio.gather(*tasks, return_exceptions=True)
    self._consume_task = None
    self._freshness_task = None
    if self._subscription is not None:
      await self._subscription.close()
      self._subscription = None
    self._set_status(WholeQuoteStatus.OFFLINE)
    for handle in list(self._consumers):
      await self.unsubscribe(handle)
    await self._publish_watermark(reason="WholeQuoteHub stopped")

  async def _open_and_hydrate(self) -> None:
    self._set_status(WholeQuoteStatus.SYNCING)
    self._subscription = await self.store.open_subscription()
    if not await self._hydrate_from_store():
      if self.status is WholeQuoteStatus.STALE:
        return
      self._set_status(WholeQuoteStatus.SYNCING)
      await self._publish_watermark(reason="Redis market snapshot unavailable")
    while self._subscription is not None:
      payload = await self._subscription.wait_for_message(timeout=0.01)
      if payload is None:
        break
      await self._apply_payload(payload)

  async def _hydrate_from_store(self) -> bool:
    received_monotonic = time.monotonic()
    hydrated = await self.store.load_snapshot()
    if hydrated is None:
      return False
    state, latest = hydrated
    self.stream_id = state.stream_id
    self.sequence = state.sequence
    self.universe_count = state.universe_count
    self.universe_hash = state.universe_hash
    self._last_sequence_progress_monotonic = time.monotonic()
    self.last_captured_at = state.captured_at
    self._latest, self._source_times = await self._prepare_snapshot(
      latest,
      state.captured_at,
    )
    if not await self._validate_authoritative_ready(
      stream_id=state.stream_id,
      sequence=state.sequence,
      captured_at=state.captured_at,
      received_monotonic=received_monotonic,
      allow_authority_ahead=False,
    ):
      if self.status is WholeQuoteStatus.STALE:
        self._latest = {}
        self._source_times = {}
      return False
    self._last_received_monotonic = received_monotonic
    if self._has_lagging_consumer():
      self._set_status(WholeQuoteStatus.STALE)
      await self._publish_watermark(reason="critical quote consumer is lagging")
      return False
    self.status = WholeQuoteStatus.READY
    self.resyncs += 1
    await self._publish_watermark()
    await self._dispatch(self._latest)
    return True

  async def _consume_forever(self) -> None:
    while self._running:
      try:
        if self._subscription is None:
          await self._open_and_hydrate()
        assert self._subscription is not None
        async for payload in self._subscription.messages():
          await self._apply_payload(payload)
      except asyncio.CancelledError:
        raise
      except Exception as exc:
        self._set_status(WholeQuoteStatus.OFFLINE)
        logger.warning(
          "WholeQuoteHub Redis stream failed: error=%s: %s",
          exc.__class__.__name__,
          exc,
        )
        if self._subscription is not None:
          try:
            await self._subscription.close()
          except Exception:
            pass
          self._subscription = None
        await asyncio.sleep(1)

  async def _apply_payload(self, payload: bytes) -> None:
    self._payloads_in_flight += 1
    try:
      await self._apply_payload_inner(payload)
    finally:
      self._payloads_in_flight -= 1

  async def _apply_payload_inner(self, payload: bytes) -> None:
    received_monotonic = time.monotonic()
    apply_started = time.monotonic()
    previous_status = self.status
    previous_stream_id = self.stream_id
    try:
      decode_started = time.monotonic()
      if len(payload) >= self._DECODE_OFFLOAD_BYTES:
        batch = await asyncio.to_thread(MarketStreamBatch.from_bytes, payload)
      else:
        batch = MarketStreamBatch.from_bytes(payload)
      self.last_decode_ms = (time.monotonic() - decode_started) * 1000
    except Exception as exc:
      self._set_status(WholeQuoteStatus.SYNCING)
      logger.warning("WholeQuoteHub rejected invalid batch: %s", exc)
      await self._publish_watermark(reason="invalid binary market batch")
      await self._hydrate_from_store()
      return

    if batch.stream_id != self.stream_id:
      if batch.kind is not MarketBatchKind.SNAPSHOT or batch.sequence != 1:
        self._set_status(WholeQuoteStatus.SYNCING)
        await self._publish_watermark(reason="new stream did not start with snapshot")
        await self._hydrate_from_store()
        return
      self.stream_id = batch.stream_id
      self.sequence = 0
      self.universe_count = 0
      self.universe_hash = ""
      self._latest = {}
      self._source_times = {}

    if batch.sequence <= self.sequence:
      return
    if batch.sequence != self.sequence + 1:
      self.sequence_gaps += 1
      self._set_status(WholeQuoteStatus.SYNCING)
      logger.warning(
        "WholeQuoteHub sequence gap: stream_id=%s expected=%s actual=%s",
        batch.stream_id,
        self.sequence + 1,
        batch.sequence,
      )
      await self._publish_watermark(reason="market sequence gap")
      if not await self._hydrate_from_store():
        return
      if batch.stream_id != self.stream_id or batch.sequence <= self.sequence:
        return
      if batch.sequence != self.sequence + 1:
        return

    if batch.kind is MarketBatchKind.SNAPSHOT:
      self.universe_count = len(batch.universe_codes)
      self.universe_hash = hashlib.sha256(
        "\n".join(batch.universe_codes).encode("utf-8")
      ).hexdigest()
      self._latest, self._source_times = await self._prepare_snapshot(
        batch.data,
        batch.captured_at,
      )
      accepted = self._latest
    else:
      accepted: dict[str, dict[str, Any]] = {}
      for code, tick in batch.data.items():
        if self._apply_tick(code, tick, batch.captured_at):
          accepted[code] = tick
    self.sequence = batch.sequence
    self._last_sequence_progress_monotonic = time.monotonic()
    self.last_captured_at = batch.captured_at
    if not await self._validate_authoritative_ready(
      stream_id=batch.stream_id,
      sequence=batch.sequence,
      captured_at=batch.captured_at,
      received_monotonic=received_monotonic,
      allow_authority_ahead=(
        previous_status is WholeQuoteStatus.READY
        and self.status is WholeQuoteStatus.READY
        and previous_stream_id == batch.stream_id
      ),
    ):
      self.last_apply_ms = (time.monotonic() - apply_started) * 1000
      return
    self._last_received_monotonic = received_monotonic
    if self._has_lagging_consumer():
      self._set_status(WholeQuoteStatus.STALE)
      await self._publish_watermark(reason="critical quote consumer is lagging")
    else:
      self.status = WholeQuoteStatus.READY
      await self._publish_watermark()
    self.last_apply_ms = (time.monotonic() - apply_started) * 1000
    dispatch_data = (
      accepted
      if previous_status is WholeQuoteStatus.READY
      else self._latest
    )
    if dispatch_data and self.is_ready:
      await self._dispatch(dispatch_data)

  def _apply_tick(
    self,
    code: str,
    tick: dict[str, Any],
    captured_at: datetime | None,
  ) -> bool:
    source_time = self._tick_source_time(tick, captured_at)
    previous = self._source_times.get(code)
    if previous is not None and source_time < previous:
      return False
    self._source_times[code] = source_time
    self._latest[code] = tick
    return True

  async def _prepare_snapshot(
    self,
    data: dict[str, dict[str, Any]],
    captured_at: datetime | None,
  ) -> tuple[dict[str, dict[str, Any]], dict[str, float]]:
    if len(data) >= self._SNAPSHOT_OFFLOAD_INSTRUMENTS:
      return await asyncio.to_thread(
        self._build_snapshot_state,
        data,
        captured_at,
      )
    return self._build_snapshot_state(data, captured_at)

  @classmethod
  def _build_snapshot_state(
    cls,
    data: dict[str, dict[str, Any]],
    captured_at: datetime | None,
  ) -> tuple[dict[str, dict[str, Any]], dict[str, float]]:
    latest: dict[str, dict[str, Any]] = {}
    source_times: dict[str, float] = {}
    for code, tick in data.items():
      latest[code] = tick
      source_times[code] = cls._tick_source_time(tick, captured_at)
    return latest, source_times

  @staticmethod
  def _tick_source_time(
    tick: dict[str, Any],
    captured_at: datetime | None,
  ) -> float:
    del captured_at
    return market_tick_source_time(
      tick,
      reference_at=datetime.now(timezone.utc),
    )

  async def _dispatch(self, data: dict[str, dict[str, Any]]) -> None:
    started = time.monotonic()
    lagging_critical_consumer = False
    # Full-market consumers receive the original batch object. Per-symbol payloads
    # are allocated once per affected symbol and shared by all of its consumers.
    for consumer in tuple(self._batch_consumers.values()):
      lagging_critical_consumer |= not self._enqueue_consumer(consumer, data)
    affected_codes = self._tick_consumers_by_code.keys() & data.keys()
    for code in affected_codes:
      tick = data[code]
      indexed = self._tick_consumers_by_code[code]
      payload = {code: tick}
      for consumer in tuple(indexed.values()):
        lagging_critical_consumer |= not self._enqueue_consumer(
          consumer,
          payload,
        )
    self.last_dispatch_ms = (time.monotonic() - started) * 1000
    if lagging_critical_consumer:
      self._set_status(WholeQuoteStatus.SYNCING)
      await self._publish_watermark(reason="critical quote consumer is lagging")
      await self._recover_lagging_consumers()

  @staticmethod
  def _enqueue_consumer(
    consumer: _Consumer,
    payload: dict[str, dict[str, Any]],
  ) -> bool:
    if consumer.status is not QuoteConsumerStatus.READY:
      return True
    queued_payload = payload
    if consumer.queue.full():
      if consumer.delivery is QuoteDeliveryMode.CRITICAL:
        consumer.status = QuoteConsumerStatus.LAGGING
        consumer.lag_events += 1
        consumer.lag_reason = "queue_overflow"
        logger.error(
          "WholeQuoteHub critical consumer lagging: handle=%s stock=%s",
          consumer.handle,
          consumer.stock_code or "*",
        )
        return False
      try:
        previous = consumer.queue.get_nowait()
        consumer.queue.task_done()
      except asyncio.QueueEmpty:
        previous = {}
      # A batch latest-only consumer must retain symbols that have not changed in
      # the incoming batch. Overlapping symbols converge to the newest tick.
      queued_payload = previous | payload
      consumer.coalesced_batches += 1
      consumer.coalesced_updates += len(previous)
    consumer.queue.put_nowait(queued_payload)
    return True

  async def subscribe_tick(
    self,
    stock_code: str,
    callback: Callable[[dict[str, dict[str, Any]]], Any],
    *,
    delivery: QuoteDeliveryMode = QuoteDeliveryMode.CRITICAL,
  ) -> str:
    return await self._subscribe(stock_code, callback, delivery)

  async def subscribe_batches(
    self,
    callback: Callable[[dict[str, dict[str, Any]]], Any],
    *,
    delivery: QuoteDeliveryMode = QuoteDeliveryMode.CRITICAL,
  ) -> str:
    return await self._subscribe(None, callback, delivery)

  async def _subscribe(
    self,
    stock_code: str | None,
    callback: Callable[[dict[str, dict[str, Any]]], Any],
    delivery: QuoteDeliveryMode,
  ) -> str:
    if callback is None:
      raise ValueError("whole-quote callback is required")
    handle = str(uuid.uuid4())
    queue_size = 1 if delivery is QuoteDeliveryMode.LATEST_ONLY else 8
    queue: asyncio.Queue[dict[str, dict[str, Any]]] = asyncio.Queue(
      maxsize=queue_size
    )
    placeholder = asyncio.create_task(asyncio.sleep(0))
    consumer = _Consumer(
      handle=handle,
      callback=callback,
      stock_code=stock_code,
      delivery=delivery,
      queue=queue,
      task=placeholder,
    )
    consumer.task = asyncio.create_task(
      self._consumer_loop(consumer),
      name=f"whole-quote-consumer:{handle}",
    )
    self._consumers[handle] = consumer
    if stock_code is None:
      self._batch_consumers[handle] = consumer
    else:
      self._tick_consumers_by_code.setdefault(stock_code, {})[handle] = consumer
    placeholder.cancel()
    await asyncio.gather(placeholder, return_exceptions=True)
    initial = self._latest if stock_code is None else {
      stock_code: self._latest[stock_code]
    } if stock_code in self._latest else {}
    if initial and self.is_ready:
      consumer.queue.put_nowait(initial)
    return handle

  async def _consumer_loop(self, consumer: _Consumer) -> None:
    while consumer.status is not QuoteConsumerStatus.STOPPED:
      payload = await consumer.queue.get()
      try:
        result = consumer.callback(payload)
        if inspect.isawaitable(result):
          await result
      except asyncio.CancelledError:
        raise
      except Exception:
        logger.exception(
          "WholeQuoteHub consumer callback failed: handle=%s",
          consumer.handle,
        )
        if consumer.delivery is QuoteDeliveryMode.CRITICAL:
          consumer.status = QuoteConsumerStatus.LAGGING
          consumer.lag_events += 1
          consumer.lag_reason = "callback_failed"
          self._set_status(WholeQuoteStatus.STALE)
          await self._publish_watermark(
            reason="critical quote consumer callback failed"
          )
          return
      finally:
        consumer.queue.task_done()

  async def unsubscribe(self, handle: str) -> bool:
    consumer = self._consumers.get(handle)
    if consumer is None:
      return True
    consumer.status = QuoteConsumerStatus.STOPPED
    if not consumer.task.done():
      consumer.task.cancel()
    _done, pending = await asyncio.wait(
      [consumer.task],
      timeout=self._CONSUMER_CANCEL_TIMEOUT_SECONDS,
    )
    if pending:
      logger.error(
        "WholeQuoteHub consumer stop timed out: handle=%s",
        consumer.handle,
      )
      return False

    self._consumers.pop(handle, None)
    if consumer.stock_code is None:
      self._batch_consumers.pop(handle, None)
    else:
      indexed = self._tick_consumers_by_code.get(consumer.stock_code)
      if indexed is not None:
        indexed.pop(handle, None)
        if not indexed:
          self._tick_consumers_by_code.pop(consumer.stock_code, None)
    return True

  def latest(self, stock_code: str) -> dict[str, Any] | None:
    return self._latest.get(stock_code)

  def snapshot(self) -> dict[str, dict[str, Any]]:
    return dict(self._latest)

  def consumer_status(self, handle: str) -> QuoteConsumerStatus | None:
    consumer = self._consumers.get(handle)
    return consumer.status if consumer is not None else None

  def status_snapshot(self) -> dict[str, Any]:
    return {
      "status": self.status.value,
      "stream_id": self.stream_id,
      "sequence": self.sequence,
      "instrument_count": len(self._latest),
      "captured_at": self.last_captured_at,
      "sequence_gaps": self.sequence_gaps,
      "resyncs": self.resyncs,
      "consumers": len(self._consumers),
      "batch_consumers": len(self._batch_consumers),
      "tick_consumers": sum(
        len(consumers) for consumers in self._tick_consumers_by_code.values()
      ),
      "indexed_tick_symbols": len(self._tick_consumers_by_code),
      "lagging_consumers": sum(
        consumer.status is QuoteConsumerStatus.LAGGING
        for consumer in self._consumers.values()
      ),
      "coalesced_batches": sum(
        consumer.coalesced_batches for consumer in self._consumers.values()
      ),
      "coalesced_updates": sum(
        consumer.coalesced_updates for consumer in self._consumers.values()
      ),
      "consumer_lag_events": sum(
        consumer.lag_events for consumer in self._consumers.values()
      ),
      "invalidated_batches": self.invalidated_batches,
      "authority_rejections": self.authority_rejections,
      "batch_age_seconds": round(self.last_batch_age_seconds, 3),
      "processing_age_ms": round(self.last_processing_age_ms, 3),
      "queue_depth": sum(
        consumer.queue.qsize() for consumer in self._consumers.values()
      ),
      "decode_ms": round(self.last_decode_ms, 3),
      "apply_ms": round(self.last_apply_ms, 3),
      "dispatch_ms": round(self.last_dispatch_ms, 3),
    }

  async def _freshness_loop(self) -> None:
    while True:
      await asyncio.sleep(1)
      await self._check_freshness_once()

  async def _check_freshness_once(self) -> None:
    try:
      api_state, freshness = await self.store.state_with_freshness()
    except Exception as exc:
      if self.status is not WholeQuoteStatus.OFFLINE:
        logger.warning(
          "WholeQuoteHub cannot read API stream state: error=%s",
          exc.__class__.__name__,
        )
      self._set_status(WholeQuoteStatus.OFFLINE)
      return
    if api_state is None or api_state.status != "READY":
      desired = (
        WholeQuoteStatus.SYNCING
        if api_state is not None and api_state.status == "SYNCING"
        else WholeQuoteStatus.STALE
        if api_state is not None and api_state.status == "STALE"
        else WholeQuoteStatus.OFFLINE
      )
      if self.status is not desired:
        self._set_status(desired)
        await self._publish_watermark(reason="API market stream is not ready")
      return

    trading_session = await self.is_trading_session()
    if trading_session and not self._freshness_matches_state(
      api_state,
      freshness,
    ):
      if self.status is not WholeQuoteStatus.STALE:
        self._set_status(WholeQuoteStatus.STALE)
        await self._publish_watermark(
          reason="API market freshness lease expired or mismatched"
        )
      return

    try:
      self.last_batch_age_seconds = validate_market_stream_capture_time(
        api_state.captured_at,
        received_at=datetime.now(timezone.utc),
        max_age_seconds=(
          self.stale_after_seconds if trading_session else None
        ),
      )
    except ValueError as exc:
      self._set_status(WholeQuoteStatus.STALE)
      await self._publish_watermark(reason=str(exc))
      return

    if api_state.stream_id != self.stream_id:
      self._authority_ahead_since_monotonic = None
      self._set_status(WholeQuoteStatus.SYNCING)
      await self._publish_watermark(reason="API market stream id changed")
      if self._payloads_in_flight == 0:
        await self._hydrate_from_store()
      return

    now_monotonic = time.monotonic()
    if api_state.sequence > self.sequence:
      receipt_stale = bool(
        trading_session
        and (
          self._last_received_monotonic <= 0
          or now_monotonic - self._last_received_monotonic
          > self.stale_after_seconds
        )
      )
      if receipt_stale:
        desired = (
          WholeQuoteStatus.STALE
          if self._payloads_in_flight
          else WholeQuoteStatus.SYNCING
        )
        self._set_status(desired)
        await self._publish_watermark(
          reason="no fresh Engine market batch for 10 seconds"
        )
        if self._payloads_in_flight == 0:
          await self._hydrate_from_store()
        return
      if self.status is not WholeQuoteStatus.READY:
        if self._payloads_in_flight == 0:
          await self._hydrate_from_store()
        return
      if self._authority_ahead_since_monotonic is None:
        self._authority_ahead_since_monotonic = now_monotonic
        return
      engine_is_progressing = bool(
        self._payloads_in_flight
        or now_monotonic - self._last_sequence_progress_monotonic
        <= self._AUTHORITY_AHEAD_GRACE_SECONDS
      )
      within_grace = (
        now_monotonic - self._authority_ahead_since_monotonic
        <= self._AUTHORITY_AHEAD_GRACE_SECONDS
      )
      if engine_is_progressing or within_grace:
        return
      self._set_status(WholeQuoteStatus.SYNCING)
      await self._publish_watermark(
        reason="Engine market watermark remained behind"
      )
      await self._hydrate_from_store()
      return
    if api_state.sequence < self.sequence:
      self._authority_ahead_since_monotonic = None
      self._set_status(WholeQuoteStatus.SYNCING)
      await self._publish_watermark(
        reason="API market watermark is behind Engine"
      )
      return

    self._authority_ahead_since_monotonic = None
    if self._has_recoverable_lag():
      await self._recover_lagging_consumers()
      return
    if self.status not in {WholeQuoteStatus.READY, WholeQuoteStatus.STALE}:
      if self._payloads_in_flight == 0:
        await self._hydrate_from_store()
      return
    if not trading_session:
      if self.status is WholeQuoteStatus.STALE and not self._has_lagging_consumer():
        self.status = WholeQuoteStatus.READY
        await self._publish_watermark()
      return
    if (
      self._last_received_monotonic <= 0
      or now_monotonic - self._last_received_monotonic
      > self.stale_after_seconds
    ):
      if self.status is not WholeQuoteStatus.STALE:
        self._set_status(WholeQuoteStatus.STALE)
        await self._publish_watermark(
          reason="no fresh market batch for 10 seconds"
        )

  def _has_lagging_consumer(self) -> bool:
    return any(
      consumer.status is QuoteConsumerStatus.LAGGING
      for consumer in self._consumers.values()
    )

  def _has_recoverable_lag(self) -> bool:
    return any(
      consumer.status is QuoteConsumerStatus.LAGGING
      and consumer.lag_reason == "queue_overflow"
      for consumer in self._consumers.values()
    )

  def _set_status(self, status: WholeQuoteStatus) -> None:
    self.status = status
    if status is not WholeQuoteStatus.READY:
      self._invalidate_pending_batches()

  def _invalidate_pending_batches(self) -> int:
    invalidated = 0
    for consumer in tuple(self._consumers.values()):
      consumer_invalidated = 0
      while True:
        try:
          consumer.queue.get_nowait()
          consumer.queue.task_done()
          consumer_invalidated += 1
        except asyncio.QueueEmpty:
          break
      consumer.invalidated_batches += consumer_invalidated
      invalidated += consumer_invalidated
    self.invalidated_batches += invalidated
    return invalidated

  async def _validate_authoritative_ready(
    self,
    *,
    stream_id: str,
    sequence: int,
    captured_at: datetime | None,
    received_monotonic: float,
    allow_authority_ahead: bool,
  ) -> bool:
    self.last_processing_age_ms = max(
      0.0,
      (time.monotonic() - received_monotonic) * 1000,
    )
    try:
      api_state, freshness = await self.store.state_with_freshness()
    except Exception as exc:
      self.authority_rejections += 1
      self._set_status(WholeQuoteStatus.OFFLINE)
      logger.warning(
        "WholeQuoteHub authority check failed: error=%s",
        exc.__class__.__name__,
      )
      return False
    if api_state is None or api_state.status != "READY":
      self.authority_rejections += 1
      desired = (
        WholeQuoteStatus.SYNCING
        if api_state is not None and api_state.status == "SYNCING"
        else WholeQuoteStatus.STALE
        if api_state is not None and api_state.status == "STALE"
        else WholeQuoteStatus.OFFLINE
      )
      self._set_status(desired)
      await self._publish_watermark(reason="API market stream is not ready")
      return False
    if api_state.stream_id != stream_id:
      self.authority_rejections += 1
      self._set_status(WholeQuoteStatus.SYNCING)
      await self._publish_watermark(reason="API market stream id changed")
      return False
    trading_session = await self.is_trading_session()
    if trading_session and not self._freshness_matches_state(
      api_state,
      freshness,
    ):
      self.authority_rejections += 1
      self._set_status(WholeQuoteStatus.STALE)
      await self._publish_watermark(
        reason="API market freshness lease expired or mismatched"
      )
      return False
    if api_state.sequence < sequence:
      self.authority_rejections += 1
      self._authority_ahead_since_monotonic = None
      self._set_status(WholeQuoteStatus.SYNCING)
      await self._publish_watermark(
        reason="API market watermark is behind local batch"
      )
      return False
    if api_state.sequence > sequence:
      if not allow_authority_ahead:
        self.authority_rejections += 1
        self._set_status(WholeQuoteStatus.SYNCING)
        await self._publish_watermark(
          reason="API market watermark is ahead of recovering Engine"
        )
        return False
      if self._authority_ahead_since_monotonic is None:
        self._authority_ahead_since_monotonic = time.monotonic()
    else:
      self._authority_ahead_since_monotonic = None

    try:
      captured_age = validate_market_stream_capture_time(
        captured_at,
        received_at=datetime.now(timezone.utc),
        max_age_seconds=(
          self.stale_after_seconds if trading_session else None
        ),
      )
    except ValueError as exc:
      self.authority_rejections += 1
      self.last_batch_age_seconds = self._captured_age_seconds(captured_at)
      self._set_status(WholeQuoteStatus.STALE)
      await self._publish_watermark(reason=str(exc))
      return False
    self.last_batch_age_seconds = captured_age
    processing_stale = (
      self.last_processing_age_ms / 1000 > self.stale_after_seconds
    )
    if processing_stale and trading_session:
      self.authority_rejections += 1
      self._set_status(WholeQuoteStatus.STALE)
      await self._publish_watermark(
        reason="market batch processing exceeded freshness window"
      )
      return False
    return True

  @staticmethod
  def _freshness_matches_state(api_state: Any, freshness: Any) -> bool:
    return bool(
      freshness is not None
      and freshness.stream_id == api_state.stream_id
      and freshness.sequence == api_state.sequence
    )

  @staticmethod
  def _captured_age_seconds(captured_at: datetime | None) -> float:
    if captured_at is None:
      return float("inf")
    normalized = (
      captured_at.replace(tzinfo=timezone.utc)
      if captured_at.tzinfo is None
      else captured_at.astimezone(timezone.utc)
    )
    return max(0.0, (datetime.now(timezone.utc) - normalized).total_seconds())

  async def _recover_lagging_consumers(self) -> bool:
    async with self._consumer_recovery_lock:
      lagging = [
        consumer
        for consumer in self._consumers.values()
        if (
          consumer.status is QuoteConsumerStatus.LAGGING
          and consumer.lag_reason == "queue_overflow"
        )
      ]
      if not lagging:
        return self.is_ready
      self._set_status(WholeQuoteStatus.SYNCING)
      for consumer in lagging:
        consumer.task.cancel()
      _done, pending = await asyncio.wait(
        [consumer.task for consumer in lagging],
        timeout=self._CONSUMER_CANCEL_TIMEOUT_SECONDS,
      )
      if pending:
        logger.error(
          "WholeQuoteHub critical consumer cancellation timed out: handles=%s",
          ",".join(
            consumer.handle
            for consumer in lagging
            if consumer.task in pending
          ),
        )
        self._set_status(WholeQuoteStatus.SYNCING)
        await self._publish_watermark(
          reason="critical consumer cancellation timed out"
        )
        return False
      for consumer in lagging:
        if consumer.handle not in self._consumers:
          continue
        consumer.status = QuoteConsumerStatus.READY
        consumer.lag_reason = ""
        consumer.task = asyncio.create_task(
          self._consumer_loop(consumer),
          name=f"whole-quote-consumer:{consumer.handle}",
        )
      try:
        hydrated = await self._hydrate_from_store()
      except Exception as exc:
        logger.warning(
          "WholeQuoteHub consumer recovery failed: error=%s: %s",
          exc.__class__.__name__,
          exc,
        )
        self._set_status(WholeQuoteStatus.SYNCING)
        return False
      if not hydrated:
        self._set_status(WholeQuoteStatus.SYNCING)
        await self._publish_watermark(
          reason="critical consumer recovery snapshot unavailable"
        )
        return False
      return self.is_ready

  async def _publish_watermark(self, *, reason: str = "") -> None:
    await self.store.write_engine_state(
      status=self.status.value,
      stream_id=self.stream_id,
      sequence=self.sequence,
      captured_at=self.last_captured_at,
      instrument_count=len(self._latest),
      universe_count=self.universe_count,
      universe_hash=self.universe_hash,
      reason=reason,
    )

  async def is_trading_session(self) -> bool:
    """Return whether realtime freshness is required for the current session."""
    now = datetime.now(ZoneInfo(settings.trading_timezone))
    try:
      return await self.trading_time_service.is_trading_hours("SH", now)
    except Exception:
      if now.weekday() >= 5:
        return False
      current = now.strftime("%H:%M")
      return any(
        len(bounds) == 2 and bounds[0] <= current <= bounds[1]
        for bounds in settings.trading_sessions.values()
      )


whole_quote_hub = WholeQuoteHub()


__all__ = [
  "QuoteConsumerStatus",
  "QuoteDeliveryMode",
  "WholeQuoteHub",
  "WholeQuoteStatus",
  "whole_quote_hub",
]
