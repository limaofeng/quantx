"""Engine-local ordered fan-out for the authoritative SH/SZ quote stream."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable
from zoneinfo import ZoneInfo

from quantx_contracts import MarketBatchKind, MarketStreamBatch

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


class WholeQuoteHub:
  """Hydrates Redis state, validates ordering, and fans out locally."""

  def __init__(
    self,
    *,
    store: MarketStreamStore = market_stream_store,
    trading_time_service: TradingTimeService | None = None,
    stale_after_seconds: float = 10.0,
  ) -> None:
    self.store = store
    self.trading_time_service = trading_time_service or TradingTimeService()
    self.stale_after_seconds = max(1.0, float(stale_after_seconds))
    self.status = WholeQuoteStatus.OFFLINE
    self.stream_id = ""
    self.sequence = 0
    self.last_captured_at: datetime | None = None
    self._latest: dict[str, dict[str, Any]] = {}
    self._source_times: dict[str, float] = {}
    self._consumers: dict[str, _Consumer] = {}
    self._subscription: BinaryMarketSubscription | None = None
    self._consume_task: asyncio.Task[None] | None = None
    self._freshness_task: asyncio.Task[None] | None = None
    self._running = False
    self._last_received_monotonic = 0.0
    self.sequence_gaps = 0
    self.resyncs = 0
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
    self.status = WholeQuoteStatus.STARTING
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
      for task in (self._consume_task, self._freshness_task)
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
    for handle in list(self._consumers):
      await self.unsubscribe(handle)
    self.status = WholeQuoteStatus.OFFLINE
    await self._publish_watermark(reason="WholeQuoteHub stopped")

  async def _open_and_hydrate(self) -> None:
    self.status = WholeQuoteStatus.SYNCING
    self._subscription = await self.store.open_subscription()
    if not await self._hydrate_from_store():
      if self.status is WholeQuoteStatus.STALE:
        return
      self.status = WholeQuoteStatus.SYNCING
      await self._publish_watermark(reason="Redis market snapshot unavailable")
    while self._subscription is not None:
      payload = await self._subscription.wait_for_message(timeout=0.01)
      if payload is None:
        break
      await self._apply_payload(payload)

  async def _hydrate_from_store(self) -> bool:
    hydrated = await self.store.load_snapshot()
    if hydrated is None:
      return False
    state, latest = hydrated
    if await self._is_trading_session():
      freshness_time = state.updated_at or state.captured_at
      if freshness_time is None or (
        datetime.now(timezone.utc) - freshness_time.astimezone(timezone.utc)
      ).total_seconds() > self.stale_after_seconds:
        self.stream_id = state.stream_id
        self.sequence = 0
        self.last_captured_at = state.captured_at
        self._latest = {}
        self._source_times = {}
        self.status = WholeQuoteStatus.STALE
        await self._publish_watermark(
          reason="Redis market snapshot is stale for active session"
        )
        return False
    self.stream_id = state.stream_id
    self.sequence = state.sequence
    self.last_captured_at = state.captured_at
    self._latest = {}
    self._source_times = {}
    for code, tick in latest.items():
      self._apply_tick(code, tick, state.captured_at)
    self._last_received_monotonic = time.monotonic()
    self.status = WholeQuoteStatus.READY
    self.resyncs += 1
    await self._publish_watermark()
    await self._dispatch(latest)
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
        self.status = WholeQuoteStatus.OFFLINE
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
    apply_started = time.monotonic()
    try:
      decode_started = time.monotonic()
      batch = MarketStreamBatch.from_bytes(payload)
      self.last_decode_ms = (time.monotonic() - decode_started) * 1000
    except Exception as exc:
      self.status = WholeQuoteStatus.SYNCING
      logger.warning("WholeQuoteHub rejected invalid batch: %s", exc)
      await self._publish_watermark(reason="invalid binary market batch")
      await self._hydrate_from_store()
      return

    if batch.stream_id != self.stream_id:
      if batch.kind is not MarketBatchKind.SNAPSHOT or batch.sequence != 1:
        self.status = WholeQuoteStatus.SYNCING
        await self._publish_watermark(reason="new stream did not start with snapshot")
        await self._hydrate_from_store()
        return
      self.stream_id = batch.stream_id
      self.sequence = 0
      self._latest = {}
      self._source_times = {}

    if batch.sequence <= self.sequence:
      return
    if batch.sequence != self.sequence + 1:
      self.sequence_gaps += 1
      self.status = WholeQuoteStatus.SYNCING
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
      self._latest = {}
      self._source_times = {}
    accepted: dict[str, dict[str, Any]] = {}
    for code, tick in batch.data.items():
      if self._apply_tick(code, tick, batch.captured_at):
        accepted[code] = tick
    self.sequence = batch.sequence
    self.last_captured_at = batch.captured_at
    self._last_received_monotonic = time.monotonic()
    self.status = (
      WholeQuoteStatus.STALE
      if self._has_lagging_consumer()
      else WholeQuoteStatus.READY
    )
    await self._publish_watermark()
    self.last_apply_ms = (time.monotonic() - apply_started) * 1000
    if accepted:
      await self._dispatch(accepted)

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

  @staticmethod
  def _tick_source_time(
    tick: dict[str, Any],
    captured_at: datetime | None,
  ) -> float:
    raw_time = tick.get("time")
    try:
      value = float(raw_time)
      if value > 0:
        return value / 1000 if value > 10_000_000_000 else value
    except (TypeError, ValueError):
      pass
    timetag = tick.get("timetag")
    if isinstance(timetag, str):
      for fmt in ("%Y%m%d %H:%M:%S.%f", "%Y%m%d %H:%M:%S"):
        try:
          parsed = datetime.strptime(timetag, fmt).replace(
            tzinfo=ZoneInfo("Asia/Shanghai")
          )
          return parsed.timestamp()
        except ValueError:
          continue
    return (captured_at or datetime.now(timezone.utc)).timestamp()

  async def _dispatch(self, data: dict[str, dict[str, Any]]) -> None:
    started = time.monotonic()
    lagging_critical_consumer = False
    for consumer in tuple(self._consumers.values()):
      if consumer.status is not QuoteConsumerStatus.READY:
        continue
      if consumer.stock_code is None:
        payload = data
      else:
        tick = data.get(consumer.stock_code)
        if tick is None:
          continue
        payload = {consumer.stock_code: tick}
      if consumer.queue.full():
        if consumer.delivery is QuoteDeliveryMode.LATEST_ONLY:
          try:
            consumer.queue.get_nowait()
            consumer.queue.task_done()
          except asyncio.QueueEmpty:
            pass
          consumer.coalesced_batches += 1
        else:
          consumer.status = QuoteConsumerStatus.LAGGING
          lagging_critical_consumer = True
          logger.error(
            "WholeQuoteHub critical consumer lagging: handle=%s stock=%s",
            consumer.handle,
            consumer.stock_code or "*",
          )
          continue
      consumer.queue.put_nowait(payload)
    self.last_dispatch_ms = (time.monotonic() - started) * 1000
    if lagging_critical_consumer:
      self.status = WholeQuoteStatus.STALE
      await self._publish_watermark(reason="critical quote consumer is lagging")

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
    placeholder.cancel()
    await asyncio.gather(placeholder, return_exceptions=True)
    initial = self._latest if stock_code is None else {
      stock_code: self._latest[stock_code]
    } if stock_code in self._latest else {}
    if initial:
      consumer.queue.put_nowait(initial)
    return handle

  async def _consumer_loop(self, consumer: _Consumer) -> None:
    while True:
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
      finally:
        consumer.queue.task_done()

  async def unsubscribe(self, handle: str) -> bool:
    consumer = self._consumers.pop(handle, None)
    if consumer is None:
      return False
    consumer.status = QuoteConsumerStatus.STOPPED
    consumer.task.cancel()
    await asyncio.gather(consumer.task, return_exceptions=True)
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
      "lagging_consumers": sum(
        consumer.status is QuoteConsumerStatus.LAGGING
        for consumer in self._consumers.values()
      ),
      "coalesced_batches": sum(
        consumer.coalesced_batches for consumer in self._consumers.values()
      ),
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
      try:
        api_state = await self.store.state()
      except Exception as exc:
        if self.status is not WholeQuoteStatus.OFFLINE:
          logger.warning(
            "WholeQuoteHub cannot read API stream state: error=%s",
            exc.__class__.__name__,
          )
        self.status = WholeQuoteStatus.OFFLINE
        continue
      if api_state is None or api_state.status != "READY":
        desired = (
          WholeQuoteStatus.SYNCING
          if api_state is not None and api_state.status == "SYNCING"
          else WholeQuoteStatus.OFFLINE
        )
        if self.status is not desired:
          self.status = desired
          await self._publish_watermark(
            reason="API market stream is not ready"
          )
        continue
      if (
        api_state.stream_id != self.stream_id
        or api_state.sequence > self.sequence
      ):
        self.status = WholeQuoteStatus.SYNCING
        await self._publish_watermark(reason="Engine market watermark is behind")
        await self._hydrate_from_store()
        continue
      if self.status not in {WholeQuoteStatus.READY, WholeQuoteStatus.STALE}:
        continue
      if not await self._is_trading_session():
        if self.status is WholeQuoteStatus.STALE and not self._has_lagging_consumer():
          self.status = WholeQuoteStatus.READY
          await self._publish_watermark()
        continue
      if (
        self._last_received_monotonic <= 0
        or time.monotonic() - self._last_received_monotonic
        > self.stale_after_seconds
      ):
        if self.status is not WholeQuoteStatus.STALE:
          self.status = WholeQuoteStatus.STALE
          await self._publish_watermark(
            reason="no fresh market batch for 10 seconds"
          )

  def _has_lagging_consumer(self) -> bool:
    return any(
      consumer.status is QuoteConsumerStatus.LAGGING
      for consumer in self._consumers.values()
    )

  async def _publish_watermark(self, *, reason: str = "") -> None:
    await self.store.write_engine_state(
      status=self.status.value,
      stream_id=self.stream_id,
      sequence=self.sequence,
      captured_at=self.last_captured_at,
      instrument_count=len(self._latest),
      reason=reason,
    )

  async def _is_trading_session(self) -> bool:
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
