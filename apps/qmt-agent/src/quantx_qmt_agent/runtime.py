"""Authenticated outbound WebSocket runtime for the QMT Agent."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import math
import multiprocessing
import os
import queue
import random
import shutil
import stat
import tempfile
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, BinaryIO, Callable, Generator, Iterable, Iterator
from zoneinfo import ZoneInfo

import httpx
import orjson
import websockets
from pydantic import ValidationError
from quantx_contracts import (
  MARKET_STREAM_MARKETS,
  MARKET_STREAM_SUBPROTOCOL,
  MAX_MARKET_STREAM_FRAME_BYTES,
  PROTOCOL_VERSION,
  AgentEnvelope,
  AgentMessageType,
  CancelCommandPayload,
  HeartbeatPayload,
  MarketBatchKind,
  MarketControlType,
  MarketStreamBatch,
  MarketStreamControl,
  TradeCommandPayload,
  market_tick_source_time,
)

from .broker import (
  LIVE_FULL_SNAPSHOT_PARTITIONS,
  MAX_MARKET_DATA_RECORDS,
  HistoricalMarketDataFieldError,
  enrich_report_payload,
  validate_market_data_request,
)
from .credentials import DeviceConfiguration, state_directory
from .emergency import EmergencyStopStore
from .endpoints import configured_tls_context, httpx_verify, websocket_url
from .health import AGENT_VERSION, AgentHealthState
from .historical_worker import (
  HISTORICAL_CHECKPOINT,
  XTDATA_HISTORICAL_WORKER_KIND,
  run_historical_market_data_worker,
)
from .journal import LocalJournal
from .whole_market_capture import (
  MIN_CAPTURED_MARKET_EVENT_ESTIMATED_BYTES,
  CapturedMarketEvent,
  WholeMarketCapture,
)

logger = logging.getLogger(__name__)
MAX_MARKET_DATA_CHUNK_RECORDS = 5000
MAX_MARKET_DATA_CHUNK_UNCOMPRESSED_BYTES = 24 * 1024 * 1024
MAX_MARKET_DATA_RECORD_UNCOMPRESSED_BYTES = 1024 * 1024
MAX_MARKET_DATA_REQUEST_RECORDS = MAX_MARKET_DATA_RECORDS
MAX_MARKET_DATA_REQUEST_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES = 256 * 1024 * 1024
MAX_MARKET_DATA_UPLOAD_CACHE_BYTES = 512 * 1024 * 1024
MAX_CACHED_MARKET_DATA_REQUESTS = 4
MAX_QUEUED_MARKET_DATA_REQUESTS = 4
MAX_QUEUED_TRADE_COMMANDS = 256
MAX_ACTIVE_NORMAL_TRADE_COMMANDS = 32
MAX_ACTIVE_PRIORITY_TRADE_COMMANDS = 16
MAX_CONCURRENT_HISTORY_UPLOADS = 2
HISTORY_UPLOAD_BYTES_PER_SECOND = 8 * 1024 * 1024
HISTORY_UPLOAD_BURST_BYTES = 1024 * 1024
MAX_MARKET_DATA_TOMBSTONES = 1024
MARKET_DATA_UPLOAD_CACHE_TTL_SECONDS = 60 * 60
MARKET_DATA_UPLOAD_CACHE_SWEEP_SECONDS = 60
MARKET_DATA_PREPARATION_TIMEOUT_SECONDS = 15 * 60
HISTORICAL_WORK_UNIT_TIMEOUT_SECONDS = 30
HISTORY_QOS_CHECK_SECONDS = 1.0
HISTORY_QOS_HEALTHY_CYCLES = 2
HISTORY_QOS_MAX_SNAPSHOT_AGE_SECONDS = 30.0
HISTORY_QOS_MAX_HEARTBEAT_ACK_SECONDS = 5.0
XTDATA_CONTROL_TIMEOUT_SECONDS = 60
XTDATA_READINESS_RETRY_SECONDS = 5
XTTRADING_READINESS_RETRY_SECONDS = 5
XTTRADING_INITIALIZATION_TIMEOUT_SECONDS = 60
XTTRADING_RECONNECT_TIMEOUT_SECONDS = 30
XTTRADING_SNAPSHOT_TIMEOUT_SECONDS = 30
XTTRADING_SNAPSHOT_MUTATION_RETRIES = 3
XTTRADING_PRIORITY_CANCEL = 0
XTTRADING_PRIORITY_ORDER = 10
XTTRADING_PRIORITY_READINESS = 20
XTTRADING_PRIORITY_SNAPSHOT = 30
XTTRADING_PRIORITY_PERIODIC_SNAPSHOT = 40
JOURNAL_PRIORITY_CANCEL = 0
JOURNAL_PRIORITY_REPORT_ACK = 5
JOURNAL_PRIORITY_COMMAND = 10
JOURNAL_PRIORITY_REPORT_SCAN = 15
JOURNAL_PRIORITY_SNAPSHOT = 20
# A stale native session is unsafe to keep alive indefinitely.  The outer
# process supervisor owns the only safe reinitialization boundary once this
# bounded recovery window expires.
XTTRADING_RECOVERY_MAX_SECONDS = 90
WEBSOCKET_PING_INTERVAL_SECONDS = 20
WEBSOCKET_PING_TIMEOUT_SECONDS = 60
WEBSOCKET_SEND_TIMEOUT_SECONDS = 5
MARKET_EVENT_SEND_TIMEOUT_SECONDS = 0.25
CONTROL_HEARTBEAT_INTERVAL_SECONDS = 15.0
ACCOUNT_SNAPSHOT_INTERVAL_SECONDS = 30.0
REPORT_SEND_WINDOW = 16
REPORT_SEND_SCAN_LIMIT = REPORT_SEND_WINDOW * 2
REPORT_ACK_QUEUE_CAPACITY = REPORT_SEND_WINDOW * 4
REPORT_RETRY_BASE_SECONDS = 0.25
REPORT_RETRY_MAX_SECONDS = 5.0
MAX_QUEUED_MARKET_CONTROLS = 256
CONTROL_SEND_HIGH_CAPACITY = 256
CONTROL_SEND_REPORT_CAPACITY = 1024
CONTROL_SEND_LOW_CAPACITY = 256
# The initial snapshot is roughly 3 MiB and its Redis publish may cross a
# forwarded development Redis endpoint.  Keep this above the API's dedicated
# 60-second snapshot commit budget; normal deltas are still bounded by the
# server's short commit timeout.
MARKET_STREAM_ACK_TIMEOUT_SECONDS = 75.0
MARKET_STREAM_HANDSHAKE_TIMEOUT_SECONDS = 10
MARKET_STREAM_READY_INGRESS_BYTES = 64 * 1024 * 1024
# Every retained callback is charged at least this many estimated bytes.  Set
# the structural ceiling from the same budget so it cannot reject a valid burst
# before the 64 MiB retained-memory authority does.
MARKET_STREAM_READY_INGRESS_CALLBACKS = (
  MARKET_STREAM_READY_INGRESS_BYTES // MIN_CAPTURED_MARKET_EVENT_ESTIMATED_BYTES
)
# The callback path deliberately avoids JSON serialization.  Charge every tick
# a conservative retained-memory estimate; the outbound cap below uses exact
# encoded bytes.
MARKET_STREAM_READY_ESTIMATED_TICK_BYTES = 2048
MARKET_STREAM_OUTBOUND_BATCHES = 8
MARKET_STREAM_OUTBOUND_BYTES = 64 * 1024 * 1024
MARKET_STREAM_MAX_UNACKNOWLEDGED_BATCHES = 2
MARKET_STREAM_INITIAL_PUSH_WAIT_SECONDS = 5.0
MARKET_STREAM_MIN_INITIAL_COVERAGE = 0.99
MARKET_STREAM_REQUIRED_INDEX_CODES = frozenset({"000001.SH", "399001.SZ", "399006.SZ"})
MARKET_STREAM_MICROBATCH_SECONDS = 0.010
# A native whole-quote callback normally contains the entire Shanghai/Shenzhen
# universe.  Splitting that callback into fixed 512-instrument fragments can
# create more structural batches than both bounded queues can absorb under ACK
# backpressure.  Size one microbatch against the actual 64 MiB wire contract
# instead, using the same conservative retained-byte estimate as the callback
# ingress.  The encoder remains the final fail-closed authority when a
# pathological payload is larger than the wire limit.
MARKET_STREAM_MICROBATCH_ESTIMATED_BYTES = MAX_MARKET_STREAM_FRAME_BYTES
MARKET_STREAM_MICROBATCH_INSTRUMENTS = (
  MARKET_STREAM_MICROBATCH_ESTIMATED_BYTES // MARKET_STREAM_READY_ESTIMATED_TICK_BYTES
)
MARKET_STREAM_NATIVE_HEALTH_CHECK_SECONDS = 5.0
MARKET_STREAM_NATIVE_SILENCE_SECONDS = 10.0
MARKET_STREAM_NATIVE_SILENCE_CONFIRMATIONS = 2
MARKET_DATA_UPLOAD_READ_BYTES = 64 * 1024
MARKET_DATA_SPOOL_DIRECTORY_NAME = "market-data-spool"
MARKET_DATA_SPOOL_REQUEST_PREFIX = "request-"
MARKET_DATA_SPOOL_OWNER_MARKER = ".owner.json"
MARKET_DATA_SPOOL_MANIFEST = "manifest.json"
MARKET_DATA_SPOOL_TERMINAL_MARKER = "terminal.json"
MARKET_DATA_SPOOL_TERMINAL_MARKER_MAX_BYTES = 16 * 1024
MARKET_DATA_SPOOL_MANIFEST_VERSION = 1
LEGACY_MARKET_DATA_SPOOL_PREFIX = "quantx-market-data-spool-"
SHANGHAI_ZONE = ZoneInfo("Asia/Shanghai")
_MARKET_DATA_CHUNK_BOUNDARY = object()


@dataclass(frozen=True, slots=True)
class _MarketDataSpoolChunk:
  path: Path
  record_count: int
  digest: str
  compressed_bytes: int


@dataclass(frozen=True, slots=True)
class _PreparedMarketData:
  spool_directory: Path
  chunks: tuple[_MarketDataSpoolChunk, ...]
  compressed_bytes: int
  uncompressed_bytes: int
  record_count: int


@dataclass(slots=True)
class _MarketUploadCacheEntry:
  fingerprint: str
  created_at: float
  last_access_at: float
  task: asyncio.Task[_PreparedMarketData] | None = None
  compressed_bytes: int = 0


@dataclass(slots=True)
class _MarketUploadTombstone:
  fingerprint: str
  completed_at: float
  last_access_at: float


@dataclass(slots=True)
class _MarketUploadTaskEntry:
  fingerprint: str
  task: asyncio.Task[None]


class _MarketOutboundOverflow(RuntimeError):
  """Encoded batches exceeded the bounded outbound window."""


class _MarketStreamHandshakeError(RuntimeError):
  """Safe, structured market-lease rejection returned by the API."""

  def __init__(self, reason_code: str, message: str) -> None:
    self.reason_code = str(reason_code or "MARKET_AUTH_REJECTED")[:64]
    self.message = str(message or "market authentication rejected")[:256]
    super().__init__(self.message)


@dataclass(frozen=True, slots=True)
class _EncodedMarketBatch:
  batch: MarketStreamBatch
  payload: bytes


@dataclass(frozen=True, slots=True)
class _PendingMarketAck:
  encoded: _EncodedMarketBatch
  sent_monotonic: float


@dataclass(slots=True)
class _ControlSocketFrame:
  serialized: str
  completion: asyncio.Future[bool]


class _CommandDispatchQueue:
  """Bounded command lanes with capacity reserved for cancellations."""

  def __init__(self) -> None:
    self.high: asyncio.Queue[tuple[int, int, AgentEnvelope]] = asyncio.Queue(
      maxsize=MAX_ACTIVE_PRIORITY_TRADE_COMMANDS
    )
    self.normal: asyncio.Queue[tuple[int, int, AgentEnvelope]] = asyncio.Queue(
      maxsize=MAX_QUEUED_TRADE_COMMANDS - MAX_ACTIVE_PRIORITY_TRADE_COMMANDS
    )

  def put_nowait(self, item: tuple[int, int, AgentEnvelope]) -> None:
    priority, _, _ = item
    (self.high if priority == 0 else self.normal).put_nowait(item)

  async def get_high(self) -> tuple[int, int, AgentEnvelope]:
    return await self.high.get()

  async def get_normal(self) -> tuple[int, int, AgentEnvelope]:
    return await self.normal.get()

  def task_done(self, priority: int) -> None:
    (self.high if priority == 0 else self.normal).task_done()

  async def join(self) -> None:
    await asyncio.gather(self.high.join(), self.normal.join())

  def empty(self) -> bool:
    return self.high.empty() and self.normal.empty()

  def qsize(self) -> int:
    return self.high.qsize() + self.normal.qsize()


@dataclass(slots=True)
class _JournalCall:
  loop: asyncio.AbstractEventLoop
  function: Callable[..., Any]
  args: tuple[Any, ...]
  kwargs: dict[str, Any]
  outcome: asyncio.Future[Any]


class _JournalPriorityWorker:
  """Serialize SQLite work while reserving queue priority for cancellations."""

  def __init__(self) -> None:
    self._queue: queue.PriorityQueue[tuple[int, int, _JournalCall | None]] = (
      queue.PriorityQueue()
    )
    self._lock = threading.Lock()
    self._sequence = 0
    self._thread: threading.Thread | None = None
    self._closed = False

  async def execute(
    self,
    function: Callable[..., Any],
    *args: Any,
    priority: int,
    **kwargs: Any,
  ) -> Any:
    loop = asyncio.get_running_loop()
    outcome = loop.create_future()
    outcome.add_done_callback(self._consume_abandoned_outcome)
    call = _JournalCall(loop, function, args, kwargs, outcome)
    with self._lock:
      if self._closed:
        raise RuntimeError("journal worker is closed")
      self._sequence += 1
      self._queue.put_nowait((int(priority), self._sequence, call))
      self._ensure_thread_locked()
    return await asyncio.shield(outcome)

  def close(self) -> None:
    with self._lock:
      if self._closed:
        return
      self._closed = True
      self._sequence += 1
      self._queue.put_nowait((10_000, self._sequence, None))

  def _ensure_thread_locked(self) -> None:
    if self._thread is not None and self._thread.is_alive():
      return
    self._thread = threading.Thread(
      target=self._run,
      name="qmt-journal-priority",
      daemon=True,
    )
    self._thread.start()

  def _run(self) -> None:
    while True:
      _, _, call = self._queue.get()
      if call is None:
        return
      try:
        result = call.function(*call.args, **call.kwargs)
      except BaseException as exc:
        self._schedule(call, error=exc)
      else:
        self._schedule(call, result=result)

  @staticmethod
  def _schedule(
    call: _JournalCall,
    *,
    result: Any = None,
    error: BaseException | None = None,
  ) -> None:
    try:
      call.loop.call_soon_threadsafe(
        _JournalPriorityWorker._finish,
        call,
        result,
        error,
      )
    except RuntimeError:
      pass

  @staticmethod
  def _finish(
    call: _JournalCall,
    result: Any,
    error: BaseException | None,
  ) -> None:
    if call.outcome.done():
      return
    if error is None:
      call.outcome.set_result(result)
    else:
      call.outcome.set_exception(error)

  @staticmethod
  def _consume_abandoned_outcome(outcome: asyncio.Future[Any]) -> None:
    if not outcome.cancelled():
      outcome.exception()


class _PriorityControlSocketWriter:
  """Serialize physical sends while reserving capacity for control traffic."""

  def __init__(self, socket) -> None:
    self.socket = socket
    self.high: asyncio.Queue[_ControlSocketFrame] = asyncio.Queue(
      maxsize=CONTROL_SEND_HIGH_CAPACITY
    )
    self.reports: asyncio.Queue[_ControlSocketFrame] = asyncio.Queue(
      maxsize=CONTROL_SEND_REPORT_CAPACITY
    )
    self.low: asyncio.Queue[_ControlSocketFrame] = asyncio.Queue(
      maxsize=CONTROL_SEND_LOW_CAPACITY
    )
    self.wakeup = asyncio.Event()
    self.closed = False

  async def send(self, serialized: str, *, priority: int) -> bool:
    if self.closed:
      raise RuntimeError("control WebSocket writer is closed")
    loop = asyncio.get_running_loop()
    completion: asyncio.Future[bool] = loop.create_future()
    completion.add_done_callback(self._consume_abandoned_completion)
    frame = _ControlSocketFrame(serialized=serialized, completion=completion)
    target = self.high if priority == 0 else self.reports if priority == 1 else self.low
    try:
      target.put_nowait(frame)
    except asyncio.QueueFull:
      if priority >= 2:
        completion.cancel()
        logger.warning("Dropped low-priority control frame under backpressure")
        return False
      raise RuntimeError("priority control WebSocket queue is full")
    self.wakeup.set()
    if priority >= 2:
      # Single-instrument quotes are transient. Their producer must never wait
      # behind a physical WebSocket send and starve heartbeat/report handling.
      return True
    return await asyncio.shield(completion)

  async def run(self) -> None:
    try:
      while True:
        frame, source = self._take_next()
        if frame is None:
          self.wakeup.clear()
          frame, source = self._take_next()
          if frame is None:
            await self.wakeup.wait()
            continue
        try:
          await asyncio.wait_for(
            self.socket.send(frame.serialized),
            timeout=WEBSOCKET_SEND_TIMEOUT_SECONDS,
          )
        except BaseException as exc:
          if not frame.completion.done():
            frame.completion.set_exception(exc)
          raise
        else:
          if not frame.completion.done():
            frame.completion.set_result(True)
        finally:
          source.task_done()
    finally:
      self.closed = True
      self._fail_pending(RuntimeError("control WebSocket writer stopped"))

  def _take_next(
    self,
  ) -> tuple[_ControlSocketFrame | None, asyncio.Queue[_ControlSocketFrame] | None]:
    for source in (self.high, self.reports, self.low):
      try:
        return source.get_nowait(), source
      except asyncio.QueueEmpty:
        continue
    return None, None

  def _fail_pending(self, error: BaseException) -> None:
    for source in (self.high, self.reports, self.low):
      while True:
        try:
          frame = source.get_nowait()
        except asyncio.QueueEmpty:
          break
        if not frame.completion.done():
          frame.completion.set_exception(error)
        source.task_done()

  @staticmethod
  def _consume_abandoned_completion(future: asyncio.Future[bool]) -> None:
    if not future.cancelled():
      future.exception()


class _BoundedMarketBatchBuffer:
  """Bound queued and unacknowledged batches by their actual wire bytes."""

  def __init__(self, *, max_batches: int, max_bytes: int) -> None:
    self._queue: asyncio.Queue[_EncodedMarketBatch] = asyncio.Queue(maxsize=max_batches)
    self._max_batches = max_batches
    self._max_bytes = max_bytes
    self._reserved_batches = 0
    self._reserved_bytes = 0
    self._capacity_changed = asyncio.Condition()

  async def put(self, encoded: _EncodedMarketBatch) -> None:
    payload_bytes = len(encoded.payload)
    if payload_bytes > self._max_bytes:
      raise _MarketOutboundOverflow(
        "whole-market batch exceeds outbound byte budget: "
        f"batch_bytes={payload_bytes} max_bytes={self._max_bytes}"
      )
    async with self._capacity_changed:
      await self._capacity_changed.wait_for(
        lambda: (
          self._reserved_batches < self._max_batches
          and self._reserved_bytes + payload_bytes <= self._max_bytes
        )
      )
      self._queue.put_nowait(encoded)
      self._reserved_batches += 1
      self._reserved_bytes += payload_bytes

  async def get(self) -> _EncodedMarketBatch:
    return await self._queue.get()

  async def acknowledge(self, encoded: _EncodedMarketBatch) -> None:
    async with self._capacity_changed:
      self._reserved_batches = max(0, self._reserved_batches - 1)
      self._reserved_bytes = max(
        0,
        self._reserved_bytes - len(encoded.payload),
      )
      self._capacity_changed.notify_all()
    self._queue.task_done()

  async def join(self) -> None:
    await self._queue.join()

  @property
  def depth(self) -> int:
    return self._reserved_batches

  @property
  def bytes(self) -> int:
    return self._reserved_bytes


class _MarketDataRequestAlreadyCompleted(RuntimeError):
  """The server redelivered a request that this runtime fully uploaded."""


class _MarketDataSpoolCleanupPending(RuntimeError):
  """Historical spool quarantine is retryable and must not stop live trading."""


class _FatalMarketDataPreparationError(RuntimeError):
  """A hung native request requires the supervised Agent process to restart."""


class _FatalTradingRecoveryError(RuntimeError):
  """A stale XTTrading session did not recover within its bounded window."""


class _StaleTradingSnapshotError(RuntimeError):
  """Trading state changed while a partitioned snapshot was being captured."""


@dataclass(slots=True)
class _XTTradingWaiter:
  loop: asyncio.AbstractEventLoop
  started: asyncio.Future[None]
  outcome: asyncio.Future[Any]


@dataclass(slots=True)
class _XTTradingCall:
  operation: str
  function: Callable[..., Any]
  args: tuple[Any, ...]
  timeout: float
  coalesce_key: str | None
  waiters: list[_XTTradingWaiter]
  started: bool = False


@dataclass(frozen=True, slots=True)
class _XTTradingSubmission:
  call: _XTTradingCall
  waiter: _XTTradingWaiter


class _XTTradingPriorityWorker:
  """Own the process's one serialized XTTrading native execution lane."""

  def __init__(
    self,
    *,
    on_timeout: Callable[[BaseException, str], None] | None = None,
  ) -> None:
    self._queue: queue.PriorityQueue[tuple[int, int, _XTTradingCall | None]] = (
      queue.PriorityQueue()
    )
    self._lock = threading.Lock()
    self._pending: dict[str, _XTTradingCall] = {}
    self._calls: dict[int, _XTTradingCall] = {}
    self._sequence = 0
    self._thread: threading.Thread | None = None
    self._closed = False
    self._poisoned: BaseException | None = None
    self._on_timeout = on_timeout

  def submit(
    self,
    *,
    operation: str,
    function: Callable[..., Any],
    args: tuple[Any, ...],
    timeout: float,
    priority: int,
    coalesce_key: str | None,
  ) -> _XTTradingSubmission:
    loop = asyncio.get_running_loop()
    waiter = _XTTradingWaiter(
      loop=loop,
      started=loop.create_future(),
      outcome=loop.create_future(),
    )
    with self._lock:
      if self._closed:
        raise RuntimeError("XTTrading worker is closed")
      if self._poisoned is not None:
        raise self._poisoned
      call = self._pending.get(coalesce_key) if coalesce_key else None
      if call is not None:
        call.waiters.append(waiter)
        if call.started:
          waiter.started.set_result(None)
        return _XTTradingSubmission(call=call, waiter=waiter)
      call = _XTTradingCall(
        operation=operation,
        function=function,
        args=args,
        timeout=max(0.001, float(timeout)),
        coalesce_key=coalesce_key,
        waiters=[waiter],
      )
      if coalesce_key:
        self._pending[coalesce_key] = call
      self._calls[id(call)] = call
      self._sequence += 1
      self._queue.put_nowait((int(priority), self._sequence, call))
      self._ensure_thread_locked()
    return _XTTradingSubmission(call=call, waiter=waiter)

  @property
  def queued_calls(self) -> int:
    return self._queue.qsize()

  def abandon(self, submission: _XTTradingSubmission) -> None:
    with self._lock:
      try:
        submission.call.waiters.remove(submission.waiter)
      except ValueError:
        pass
    for future in (submission.waiter.started, submission.waiter.outcome):
      if not future.done():
        future.cancel()

  def poison(self, error: BaseException) -> None:
    with self._lock:
      if self._poisoned is None:
        self._poisoned = error
      calls = list(self._calls.values())
    for call in calls:
      self._deliver(call, error=error)

  def close(self) -> None:
    error = RuntimeError("XTTrading worker is closed")
    with self._lock:
      if self._closed:
        return
      self._closed = True
      self._sequence += 1
      self._queue.put_nowait((10_000, self._sequence, None))
      calls = list(self._calls.values())
    for call in calls:
      self._deliver(call, error=error)

  def _ensure_thread_locked(self) -> None:
    if self._thread is not None and self._thread.is_alive():
      return
    self._thread = threading.Thread(
      target=self._run,
      name="qmt-xttrading-priority",
      daemon=True,
    )
    self._thread.start()

  def _run(self) -> None:
    while True:
      _, _, call = self._queue.get()
      if call is None:
        return
      with self._lock:
        poisoned = self._poisoned
        closed = self._closed
        call.started = True
        waiters = tuple(call.waiters)
      if poisoned is not None or closed:
        self._deliver(
          call,
          error=poisoned or RuntimeError("XTTrading worker is closed"),
        )
        continue
      for waiter in waiters:
        self._schedule(waiter.loop, self._mark_started, waiter)
      watchdog = threading.Timer(
        call.timeout,
        self._execution_timed_out,
        args=(call,),
      )
      watchdog.name = f"qmt-xttrading-watchdog:{call.operation[:24]}"
      watchdog.daemon = True
      watchdog.start()
      try:
        result = call.function(*call.args)
      except BaseException as exc:
        watchdog.cancel()
        self._deliver(call, error=exc)
      else:
        watchdog.cancel()
        self._deliver(call, result=result)

  def _execution_timed_out(self, call: _XTTradingCall) -> None:
    with self._lock:
      if id(call) not in self._calls or self._poisoned is not None:
        return
    error = _FatalTradingRecoveryError(
      f"XTTrading {call.operation} timed out; Agent restart required"
    )
    self.poison(error)
    if self._on_timeout is not None:
      try:
        self._on_timeout(error, call.operation)
      except Exception:
        logger.exception("XTTrading timeout callback failed")

  def _deliver(
    self,
    call: _XTTradingCall,
    *,
    result: Any = None,
    error: BaseException | None = None,
  ) -> None:
    with self._lock:
      if call.coalesce_key and self._pending.get(call.coalesce_key) is call:
        self._pending.pop(call.coalesce_key, None)
      self._calls.pop(id(call), None)
      waiters = tuple(call.waiters)
      call.waiters.clear()
    for waiter in waiters:
      self._schedule(
        waiter.loop,
        self._finish_waiter,
        waiter,
        result,
        error,
      )

  @staticmethod
  def _schedule(loop: asyncio.AbstractEventLoop, callback, *args: Any) -> None:
    try:
      loop.call_soon_threadsafe(callback, *args)
    except RuntimeError:
      pass

  @staticmethod
  def _mark_started(waiter: _XTTradingWaiter) -> None:
    if not waiter.started.done():
      waiter.started.set_result(None)

  @staticmethod
  def _finish_waiter(
    waiter: _XTTradingWaiter,
    result: Any,
    error: BaseException | None,
  ) -> None:
    if not waiter.started.done():
      waiter.started.set_result(None)
    if waiter.outcome.done():
      return
    if error is not None:
      waiter.outcome.set_exception(error)
    else:
      waiter.outcome.set_result(result)


class _IsolatedMarketDataWorkerError(ValueError):
  """One isolated historical request failed without poisoning the Agent."""


_RETRYABLE_MARKET_DATA_HTTP_STATUSES = frozenset(
  {
    401,
    403,
    408,
    425,
    429,
  }
)


def _is_deterministic_market_data_request_error(error: Exception) -> bool:
  """Return whether retrying the same immutable request cannot help.

  Transport failures, authentication/session expiry, throttling, redirects,
  and server failures belong to the current connection rather than to the
  durable request. They must leave the request and its prepared spool intact
  so the API can redeliver it after the control socket reconnects.
  """
  if isinstance(error, httpx.HTTPStatusError):
    status = int(error.response.status_code)
    if status in _RETRYABLE_MARKET_DATA_HTTP_STATUSES or status >= 500:
      return False
    return 400 <= status < 500
  if isinstance(error, httpx.TransportError):
    return False
  return isinstance(
    error,
    (AttributeError, ValueError, TypeError, OverflowError, UnicodeError),
  )


def _market_data_failure_reason(error: Exception) -> str:
  """Return a bounded reason without exposing arbitrary native exception text."""

  if isinstance(error, HistoricalMarketDataFieldError):
    return f"ValueError: {error}"
  if isinstance(error, _IsolatedMarketDataWorkerError):
    return str(error)
  return error.__class__.__name__


def _market_data_spool_owner_key(device_id: str) -> str:
  return hashlib.sha256(device_id.encode("utf-8")).hexdigest()[:32]


def _cleanup_legacy_market_data_spools(temp_directory: Path) -> None:
  """Remove only old Agent-owned direct children of the system TEMP root."""
  resolved_temp = temp_directory.resolve()
  if not resolved_temp.is_dir():
    return
  reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
  for child in list(resolved_temp.iterdir()):
    if not child.name.startswith(LEGACY_MARKET_DATA_SPOOL_PREFIX):
      continue
    try:
      attributes = int(getattr(child.lstat(), "st_file_attributes", 0))
      if child.is_symlink() or attributes & reparse_flag:
        logger.warning("Skipped unsafe legacy market-data spool: %s", child)
        continue
      resolved_child = child.resolve()
      if resolved_child.parent != resolved_temp or not resolved_child.is_dir():
        logger.warning("Skipped unsafe legacy market-data spool: %s", child)
        continue
      shutil.rmtree(resolved_child)
    except FileNotFoundError:
      continue
    except OSError as exc:
      logger.warning(
        "Deferred legacy market-data spool cleanup: directory=%s error=%s",
        child.name,
        exc.__class__.__name__,
      )


def _safe_market_data_spool_request(
  root: Path,
  candidate: Path,
) -> Path:
  resolved_root = root.resolve()
  resolved = candidate.resolve()
  if (
    resolved.parent != resolved_root
    or not resolved.name.startswith(MARKET_DATA_SPOOL_REQUEST_PREFIX)
    or candidate.is_symlink()
  ):
    raise RuntimeError("unsafe market-data spool path")
  return resolved


def _market_data_spool_request_directory(root: Path, request_id: str) -> Path:
  normalized_request_id = str(request_id or "").strip()
  if not normalized_request_id:
    raise ValueError("market-data request_id is required")
  digest = hashlib.sha256(normalized_request_id.encode("utf-8")).hexdigest()[:32]
  return _safe_market_data_spool_request(
    root,
    root / f"{MARKET_DATA_SPOOL_REQUEST_PREFIX}{digest}",
  )


def _market_data_file_digest(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as source:
    while block := source.read(MARKET_DATA_UPLOAD_READ_BYTES):
      digest.update(block)
  return digest.hexdigest()


def _reset_market_data_spool_directory(root: Path, request_id: str) -> Path:
  spool_directory = _market_data_spool_request_directory(root, request_id)
  if spool_directory.exists():
    if spool_directory.is_symlink() or not spool_directory.is_dir():
      raise RuntimeError("unsafe market-data spool request path")
    shutil.rmtree(spool_directory)
  spool_directory.mkdir(parents=False, exist_ok=False)
  return spool_directory


def _write_market_data_spool_manifest(
  prepared: _PreparedMarketData,
  *,
  request_id: str,
  fingerprint: str,
) -> None:
  resolved_spool = prepared.spool_directory.resolve()
  expected_spool = _market_data_spool_request_directory(
    resolved_spool.parent,
    request_id,
  )
  if resolved_spool != expected_spool or prepared.spool_directory.is_symlink():
    raise RuntimeError("unsafe market-data spool manifest target")
  chunks: list[dict[str, Any]] = []
  for chunk in prepared.chunks:
    resolved_chunk = chunk.path.resolve()
    if (
      resolved_chunk.parent != resolved_spool
      or chunk.path.is_symlink()
      or not chunk.path.is_file()
    ):
      raise RuntimeError("unsafe market-data spool chunk")
    chunks.append(
      {
        "name": chunk.path.name,
        "record_count": chunk.record_count,
        "digest": chunk.digest,
        "compressed_bytes": chunk.compressed_bytes,
      }
    )
  payload = {
    "version": MARKET_DATA_SPOOL_MANIFEST_VERSION,
    "request_id": request_id,
    "fingerprint": fingerprint,
    "created_at": time.time(),
    "chunks": chunks,
    "compressed_bytes": prepared.compressed_bytes,
    "uncompressed_bytes": prepared.uncompressed_bytes,
    "record_count": prepared.record_count,
  }
  manifest = resolved_spool / MARKET_DATA_SPOOL_MANIFEST
  temporary = resolved_spool / f"{MARKET_DATA_SPOOL_MANIFEST}.tmp"
  with temporary.open("w", encoding="utf-8", newline="\n") as output:
    json.dump(payload, output, sort_keys=True, separators=(",", ":"))
    output.flush()
    os.fsync(output.fileno())
  temporary.replace(manifest)


def _write_market_data_spool_terminal_marker(
  prepared: _PreparedMarketData,
  *,
  request_id: str,
  fingerprint: str,
  terminal_status: str,
) -> None:
  if terminal_status not in {"COMPLETED", "FAILED"}:
    raise ValueError("invalid market-data terminal status")
  if (
    len(fingerprint) != 64
    or any(character not in "0123456789abcdef" for character in fingerprint)
  ):
    raise ValueError("invalid market-data terminal fingerprint")
  resolved_spool = prepared.spool_directory.resolve()
  expected_spool = _market_data_spool_request_directory(
    resolved_spool.parent,
    request_id,
  )
  if resolved_spool != expected_spool or prepared.spool_directory.is_symlink():
    raise RuntimeError("unsafe market-data terminal marker target")
  payload = {
    "request_id": request_id,
    "fingerprint": fingerprint,
    "terminal_status": terminal_status,
    "recorded_at": time.time(),
  }
  marker = resolved_spool / MARKET_DATA_SPOOL_TERMINAL_MARKER
  temporary = resolved_spool / f"{MARKET_DATA_SPOOL_TERMINAL_MARKER}.tmp"
  with temporary.open("w", encoding="utf-8", newline="\n") as output:
    json.dump(payload, output, sort_keys=True, separators=(",", ":"))
    output.flush()
    os.fsync(output.fileno())
  temporary.replace(marker)


def _read_market_data_spool_manifest(
  spool_directory: Path,
  *,
  expected_request_id: str | None = None,
  expected_fingerprint: str | None = None,
  verify_digests: bool = True,
) -> tuple[_PreparedMarketData, str, str, float]:
  if spool_directory.is_symlink() or not spool_directory.is_dir():
    raise RuntimeError("unsafe market-data spool directory")
  manifest_path = spool_directory / MARKET_DATA_SPOOL_MANIFEST
  if manifest_path.is_symlink() or not manifest_path.is_file():
    raise FileNotFoundError("market-data spool manifest is missing")
  if manifest_path.stat().st_size > 1024 * 1024:
    raise RuntimeError("market-data spool manifest is too large")
  try:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
  except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
    raise RuntimeError("invalid market-data spool manifest") from exc
  if not isinstance(payload, dict) or int(payload.get("version") or 0) != (
    MARKET_DATA_SPOOL_MANIFEST_VERSION
  ):
    raise RuntimeError("unsupported market-data spool manifest")
  request_id = str(payload.get("request_id") or "")
  fingerprint = str(payload.get("fingerprint") or "")
  created_at = float(payload.get("created_at") or 0.0)
  if (
    not request_id
    or len(fingerprint) != 64
    or any(character not in "0123456789abcdef" for character in fingerprint)
    or not math.isfinite(created_at)
    or created_at <= 0
    or created_at > time.time() + 300.0
  ):
    raise RuntimeError("invalid market-data spool identity")
  if expected_request_id is not None and request_id != expected_request_id:
    raise RuntimeError("market-data spool request identity mismatch")
  if expected_fingerprint is not None and fingerprint != expected_fingerprint:
    raise RuntimeError("同一 market-data request_id 的重投参数不一致")
  expected_directory = _market_data_spool_request_directory(
    spool_directory.resolve().parent,
    request_id,
  )
  if expected_directory != spool_directory.resolve():
    raise RuntimeError("market-data spool directory identity mismatch")
  raw_chunks = payload.get("chunks")
  if not isinstance(raw_chunks, list) or not raw_chunks:
    raise RuntimeError("market-data spool manifest has no chunks")
  chunks: list[_MarketDataSpoolChunk] = []
  seen: set[str] = set()
  for raw_chunk in raw_chunks:
    if not isinstance(raw_chunk, dict):
      raise RuntimeError("invalid market-data spool chunk manifest")
    name = str(raw_chunk.get("name") or "")
    if not name or name in seen or Path(name).name != name:
      raise RuntimeError("invalid market-data spool chunk name")
    seen.add(name)
    chunk_path = spool_directory / name
    compressed_bytes = int(raw_chunk.get("compressed_bytes") or 0)
    record_count = int(raw_chunk.get("record_count") or 0)
    digest = str(raw_chunk.get("digest") or "")
    if (
      chunk_path.is_symlink()
      or not chunk_path.is_file()
      or chunk_path.resolve().parent != spool_directory.resolve()
      or compressed_bytes <= 0
      or chunk_path.stat().st_size != compressed_bytes
      or record_count < 0
      or len(digest) != 64
      or any(character not in "0123456789abcdef" for character in digest)
      or (verify_digests and _market_data_file_digest(chunk_path) != digest)
    ):
      raise RuntimeError("market-data spool chunk validation failed")
    chunks.append(
      _MarketDataSpoolChunk(
        path=chunk_path,
        record_count=record_count,
        digest=digest,
        compressed_bytes=compressed_bytes,
      )
    )
  prepared = _PreparedMarketData(
    spool_directory=spool_directory,
    chunks=tuple(chunks),
    compressed_bytes=int(payload.get("compressed_bytes") or 0),
    uncompressed_bytes=int(payload.get("uncompressed_bytes") or 0),
    record_count=int(payload.get("record_count") or 0),
  )
  if (
    prepared.compressed_bytes < 0
    or prepared.uncompressed_bytes < 0
    or prepared.record_count < 0
    or prepared.compressed_bytes != sum(chunk.compressed_bytes for chunk in chunks)
    or prepared.record_count != sum(chunk.record_count for chunk in chunks)
    or prepared.compressed_bytes > MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES
    or prepared.uncompressed_bytes > MAX_MARKET_DATA_REQUEST_UNCOMPRESSED_BYTES
    or prepared.record_count > MAX_MARKET_DATA_REQUEST_RECORDS
  ):
    raise RuntimeError("market-data spool totals are invalid")
  return (
    prepared,
    request_id,
    fingerprint,
    created_at,
  )


def _read_market_data_spool_terminal_marker(
  spool_directory: Path,
  *,
  expected_request_id: str,
  expected_fingerprint: str,
) -> dict[str, Any]:
  marker = spool_directory / MARKET_DATA_SPOOL_TERMINAL_MARKER
  if marker.is_symlink() or not marker.is_file():
    raise FileNotFoundError("market-data terminal marker is missing")
  if marker.stat().st_size > MARKET_DATA_SPOOL_TERMINAL_MARKER_MAX_BYTES:
    raise RuntimeError("market-data terminal marker is too large")
  try:
    payload = json.loads(marker.read_text(encoding="utf-8"))
  except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
    raise RuntimeError("invalid market-data terminal marker") from exc
  if not isinstance(payload, dict) or set(payload) != {
    "request_id",
    "fingerprint",
    "terminal_status",
    "recorded_at",
  }:
    raise RuntimeError("invalid market-data terminal marker")
  recorded_at = payload.get("recorded_at")
  if (
    str(payload.get("request_id") or "") != expected_request_id
    or str(payload.get("fingerprint") or "") != expected_fingerprint
    or str(payload.get("terminal_status") or "") not in {"COMPLETED", "FAILED"}
    or isinstance(recorded_at, bool)
    or not isinstance(recorded_at, (int, float))
    or not math.isfinite(float(recorded_at))
    or float(recorded_at) <= 0
    or float(recorded_at) > time.time() + 300.0
  ):
    raise RuntimeError("invalid market-data terminal marker")
  return payload


def _remove_market_data_spool_best_effort(spool_directory: Path) -> bool:
  try:
    shutil.rmtree(spool_directory)
  except FileNotFoundError:
    return True
  except OSError as exc:
    logger.warning(
      "Deferred market-data spool cleanup: directory=%s error=%s",
      spool_directory.name,
      exc.__class__.__name__,
    )
    return False
  return True


def _sweep_market_data_spool_cleanup(
  root: Path,
  *,
  protected_directory_names: frozenset[str] = frozenset(),
) -> bool:
  """Best-effort history-only cleanup; return whether dispatch must pause."""

  cleanup_pending = False
  try:
    children = list(root.iterdir())
  except OSError as exc:
    logger.warning(
      "Market-data spool scan deferred without stopping Agent: error=%s",
      exc.__class__.__name__,
    )
    return True
  for child in children:
    if not child.name.startswith(MARKET_DATA_SPOOL_REQUEST_PREFIX):
      continue
    if child.name in protected_directory_names:
      continue
    try:
      safe_child = _safe_market_data_spool_request(root, child)
    except (OSError, RuntimeError) as exc:
      cleanup_pending = True
      logger.warning(
        "Quarantined unsafe market-data spool: directory=%s error=%s",
        child.name,
        exc.__class__.__name__,
      )
      continue
    terminal_marker = safe_child / MARKET_DATA_SPOOL_TERMINAL_MARKER
    try:
      _, request_id, fingerprint, _ = _read_market_data_spool_manifest(
        safe_child,
        verify_digests=False,
      )
    except Exception as exc:
      try:
        terminal_marker_present = terminal_marker.exists()
      except OSError:
        terminal_marker_present = True
      if terminal_marker_present:
        cleanup_pending = True
        logger.warning(
          "Quarantined market-data spool with unverifiable terminal state: "
          "directory=%s error=%s",
          child.name,
          exc.__class__.__name__,
        )
        continue
      if not safe_child.is_dir() or safe_child.is_symlink():
        cleanup_pending = True
        continue
      if not _remove_market_data_spool_best_effort(safe_child):
        cleanup_pending = True
      continue
    try:
      terminal_marker_present = terminal_marker.exists()
    except OSError as exc:
      cleanup_pending = True
      logger.warning(
        "Quarantined unreadable market-data terminal marker: "
        "directory=%s error=%s",
        child.name,
        exc.__class__.__name__,
      )
      continue
    if not terminal_marker_present:
      continue
    try:
      _read_market_data_spool_terminal_marker(
        safe_child,
        expected_request_id=request_id,
        expected_fingerprint=fingerprint,
      )
    except Exception as exc:
      cleanup_pending = True
      logger.warning(
        "Quarantined invalid market-data terminal marker: "
        "directory=%s error=%s",
        child.name,
        exc.__class__.__name__,
      )
      continue
    if not _remove_market_data_spool_best_effort(safe_child):
      cleanup_pending = True
  return cleanup_pending


def _initialize_market_data_spool_root(
  base_directory: Path,
  device_id: str,
) -> Path:
  owner_key = _market_data_spool_owner_key(device_id)
  managed_root = base_directory.resolve() / MARKET_DATA_SPOOL_DIRECTORY_NAME
  managed_root.mkdir(parents=True, exist_ok=True)
  owner_root = managed_root / owner_key
  owner_root.mkdir(parents=False, exist_ok=True)
  if owner_root.is_symlink() or owner_root.resolve().parent != managed_root.resolve():
    raise RuntimeError("unsafe market-data spool owner root")
  marker = owner_root / MARKET_DATA_SPOOL_OWNER_MARKER
  if marker.exists():
    try:
      marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
      raise RuntimeError("invalid market-data spool owner marker") from exc
    if marker_payload != {"owner_key": owner_key}:
      raise RuntimeError("market-data spool owner marker mismatch")
  else:
    existing = list(owner_root.iterdir())
    if existing:
      raise RuntimeError("unowned market-data spool directory is not empty")
    temporary = owner_root / f"{MARKET_DATA_SPOOL_OWNER_MARKER}.tmp"
    temporary.write_text(
      json.dumps({"owner_key": owner_key}, sort_keys=True),
      encoding="utf-8",
    )
    temporary.replace(marker)

  if _sweep_market_data_spool_cleanup(owner_root):
    logger.warning(
      "Historical market-data dispatch paused for spool quarantine cleanup"
    )
  return owner_root


def _managed_market_data_spool_bytes(root: Path) -> int:
  total = 0
  for child in root.iterdir():
    if not child.name.startswith(MARKET_DATA_SPOOL_REQUEST_PREFIX):
      continue
    safe_child = _safe_market_data_spool_request(root, child)
    if not safe_child.is_dir():
      continue
    for path in safe_child.rglob("*"):
      if path.is_symlink():
        raise RuntimeError("market-data spool contains a symbolic link")
      if path.is_file():
        total += path.stat().st_size
        if total > MAX_MARKET_DATA_UPLOAD_CACHE_BYTES:
          raise RuntimeError("market-data upload cache byte limit exceeded")
  return total


class _LimitedHashingWriter:
  def __init__(
    self,
    raw: BinaryIO,
    *,
    max_bytes: int,
    reserve_bytes: Callable[[int], None] | None = None,
  ) -> None:
    self.raw = raw
    self.max_bytes = max_bytes
    self.reserve_bytes = reserve_bytes
    self.bytes_written = 0
    self.digest = hashlib.sha256()

  def write(self, data: bytes) -> int:
    next_size = self.bytes_written + len(data)
    if next_size > self.max_bytes:
      raise ValueError("market data request exceeds compressed byte limit")
    if self.reserve_bytes is not None:
      self.reserve_bytes(len(data))
    written = self.raw.write(data)
    if written != len(data):
      raise OSError("short write while spooling market data")
    self.digest.update(data)
    self.bytes_written = next_size
    return written

  def flush(self) -> None:
    self.raw.flush()

  def tell(self) -> int:
    return self.bytes_written


def _iter_encoded_market_data_chunks(
  records: Iterable[Any],
  *,
  max_records: int = MAX_MARKET_DATA_CHUNK_RECORDS,
  max_uncompressed_bytes: int = MAX_MARKET_DATA_CHUNK_UNCOMPRESSED_BYTES,
  max_total_records: int = MAX_MARKET_DATA_REQUEST_RECORDS,
  max_record_uncompressed_bytes: int = (MAX_MARKET_DATA_RECORD_UNCOMPRESSED_BYTES),
  max_total_uncompressed_bytes: int = MAX_MARKET_DATA_REQUEST_UNCOMPRESSED_BYTES,
) -> Iterator[tuple[bytearray | None, int]]:
  """Yield one bounded raw JSON chunk at a time without materializing input."""
  if (
    max_records <= 0
    or max_uncompressed_bytes < 2
    or max_total_records <= 0
    or max_record_uncompressed_bytes <= 0
    or max_total_uncompressed_bytes < 2
  ):
    raise ValueError("invalid market data chunk limits")

  current = bytearray(b"[")
  current_records = 0
  total_size = 0
  total_records = 0

  for record in records:
    if record is HISTORICAL_CHECKPOINT:
      yield None, 0
      continue
    if record is _MARKET_DATA_CHUNK_BOUNDARY:
      if current_records:
        current.extend(b"]")
        total_size += len(current)
        if total_size > max_total_uncompressed_bytes:
          raise ValueError("market data request exceeds uncompressed byte limit")
        yield current, current_records
        current = bytearray(b"[")
        current_records = 0
      continue
    total_records += 1
    if total_records > max_total_records:
      raise ValueError("market data request exceeds record count limit")
    try:
      encoded = json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
        allow_nan=False,
      ).encode("utf-8")
    except ValueError as exc:
      raise ValueError("market data record contains a non-finite JSON number") from exc
    if len(encoded) > max_record_uncompressed_bytes:
      raise ValueError("single market data record exceeds record byte limit")
    if len(encoded) + 2 > max_uncompressed_bytes:
      raise ValueError("single market data record exceeds chunk size limit")
    separator_size = 1 if current_records > 0 else 0
    if current_records > 0 and (
      current_records >= max_records
      or len(current) + separator_size + len(encoded) + 1 > max_uncompressed_bytes
    ):
      current.extend(b"]")
      total_size += len(current)
      if total_size > max_total_uncompressed_bytes:
        raise ValueError("market data request exceeds uncompressed byte limit")
      yield current, current_records
      current = bytearray(b"[")
      current_records = 0
      separator_size = 0
    if separator_size:
      current.extend(b",")
    current.extend(encoded)
    current_records += 1
    if total_size + len(current) + 1 > max_total_uncompressed_bytes:
      raise ValueError("market data request exceeds uncompressed byte limit")

  current.extend(b"]")
  total_size += len(current)
  if total_size > max_total_uncompressed_bytes:
    raise ValueError("market data request exceeds uncompressed byte limit")
  yield current, current_records


def _prepare_market_data_spool_sync(
  broker: Any,
  payload: dict[str, Any],
  spool_directory: Path,
  *,
  max_total_uncompressed_bytes: int,
  max_total_compressed_bytes: int,
) -> _PreparedMarketData:
  """Stream a broker request into deterministic, bounded gzip files."""
  iterator_factory = getattr(broker, "iter_market_data", None)
  records = (
    iterator_factory(payload)
    if callable(iterator_factory)
    else iter(broker.market_data(payload))
  )
  return _prepare_market_data_records_spool_sync(
    records,
    spool_directory,
    max_total_uncompressed_bytes=max_total_uncompressed_bytes,
    max_total_compressed_bytes=max_total_compressed_bytes,
  )


def _prepare_market_data_records_spool_sync(records, spool_directory, **kwargs):
  steps = _prepare_market_data_records_spool_steps(records, spool_directory, **kwargs)
  while True:
    try:
      next(steps)
    except StopIteration as done:
      return done.value


def _prepare_market_data_records_spool_steps(
  records: Iterable[Any],
  spool_directory: Path,
  *,
  max_total_uncompressed_bytes: int,
  max_total_compressed_bytes: int,
  on_chunk: Callable[[int, _MarketDataSpoolChunk], None] | None = None,
  reserve_compressed_bytes: Callable[[int], None] | None = None,
) -> Generator[None, None, _PreparedMarketData]:
  """Stream normalized records into atomically published gzip spool files."""

  chunks: list[_MarketDataSpoolChunk] = []
  uncompressed_bytes = 0
  compressed_bytes = 0
  record_count_total = 0
  try:
    for raw, record_count in _iter_encoded_market_data_chunks(
      records, max_total_uncompressed_bytes=max_total_uncompressed_bytes,
    ):
      if raw is None:
        yield None
        continue
      chunk_index = len(chunks)
      path = spool_directory / f"chunk-{chunk_index:06d}.json.gz"
      temporary = path.with_suffix(f"{path.suffix}.tmp")
      remaining = max_total_compressed_bytes - compressed_bytes
      if remaining <= 0:
        raise ValueError("market data request exceeds compressed byte limit")
      with temporary.open("xb") as file_handle:
        writer = _LimitedHashingWriter(
          file_handle,
          max_bytes=remaining,
          reserve_bytes=reserve_compressed_bytes,
        )
        with gzip.GzipFile(
          filename="",
          mode="wb",
          fileobj=writer,
          mtime=0,
        ) as compressor:
          compressor.write(raw)
        file_handle.flush()
        os.fsync(file_handle.fileno())
      temporary.replace(path)
      chunk = _MarketDataSpoolChunk(
        path=path,
        record_count=record_count,
        digest=writer.digest.hexdigest(),
        compressed_bytes=writer.bytes_written,
      )
      chunks.append(chunk)
      if on_chunk is not None:
        on_chunk(chunk_index, chunk)
      uncompressed_bytes += len(raw)
      compressed_bytes += writer.bytes_written
      record_count_total += record_count
    return _PreparedMarketData(
      spool_directory=spool_directory,
      chunks=tuple(chunks),
      compressed_bytes=compressed_bytes,
      uncompressed_bytes=uncompressed_bytes,
      record_count=record_count_total,
    )
  except BaseException:
    shutil.rmtree(spool_directory, ignore_errors=True)
    raise


def _terminate_market_data_process(process: Any) -> None:
  """Bounded cleanup for one spawned historical-data process."""

  if process.is_alive():
    process.terminate()
  process.join(5.0)
  if process.is_alive():
    kill = getattr(process, "kill", None)
    if callable(kill):
      kill()
    process.join(5.0)


def _decode_isolated_market_data_result(
  message: Any,
  *,
  request_id: str,
  spool_directory: Path,
) -> _PreparedMarketData:
  """Validate the small trusted-child result before using spool paths."""

  if (
    not isinstance(message, dict)
    or str(message.get("request_id") or "") != request_id
  ):
    raise _IsolatedMarketDataWorkerError("MARKET_DATA_PREPARATION_PROTOCOL_ERROR")
  status = message.get("type")
  if status == "error":
    payload = message.get("error")
    if not isinstance(payload, dict):
      raise _IsolatedMarketDataWorkerError(
        "MARKET_DATA_PREPARATION_PROTOCOL_ERROR"
      )
    kind = payload.get("kind")
    if kind == "historical_field":
      raise HistoricalMarketDataFieldError(
        code=str(payload.get("code") or ""),
        period=str(payload.get("period") or ""),
        source_time_ms=int(payload.get("source_time_ms") or 0),
        field=str(payload.get("field") or ""),
      )
    if kind == "deterministic":
      message_text = str(payload.get("message") or "")[:1024]
      error_type = str(payload.get("error_type") or "")
      error_class = {
        "AttributeError": AttributeError,
        "OverflowError": OverflowError,
        "TypeError": TypeError,
        "ValueError": ValueError,
      }.get(error_type, ValueError)
      raise error_class(message_text)
    reason = str(payload.get("reason") or "")
    if reason not in {
      "MARKET_DATA_PREPARATION_FAILED",
      "XTDATA_UNAVAILABLE",
    }:
      reason = "MARKET_DATA_PREPARATION_PROTOCOL_ERROR"
    raise _IsolatedMarketDataWorkerError(reason)
  manifest = message.get("manifest")
  if status != "ok" or not isinstance(manifest, dict):
    raise _IsolatedMarketDataWorkerError("MARKET_DATA_PREPARATION_PROTOCOL_ERROR")

  resolved_spool = spool_directory.resolve()
  manifest_spool = Path(str(manifest.get("spool_directory") or ""))
  if manifest_spool.resolve() != resolved_spool:
    raise _IsolatedMarketDataWorkerError("MARKET_DATA_PREPARATION_PROTOCOL_ERROR")
  if spool_directory.is_symlink() or not spool_directory.is_dir():
    raise _IsolatedMarketDataWorkerError("MARKET_DATA_PREPARATION_PROTOCOL_ERROR")
  raw_chunks = manifest.get("chunks")
  if not isinstance(raw_chunks, list) or not raw_chunks:
    raise _IsolatedMarketDataWorkerError("MARKET_DATA_PREPARATION_PROTOCOL_ERROR")
  chunks: list[_MarketDataSpoolChunk] = []
  try:
    for raw_chunk in raw_chunks:
      if not isinstance(raw_chunk, dict):
        raise ValueError
      chunks.append(
        _MarketDataSpoolChunk(
          path=Path(str(raw_chunk["path"])),
          record_count=int(raw_chunk["record_count"]),
          digest=str(raw_chunk["digest"]),
          compressed_bytes=int(raw_chunk["compressed_bytes"]),
        )
      )
    payload = _PreparedMarketData(
      spool_directory=manifest_spool,
      chunks=tuple(chunks),
      compressed_bytes=int(manifest["compressed_bytes"]),
      uncompressed_bytes=int(manifest["uncompressed_bytes"]),
      record_count=int(manifest["record_count"]),
    )
  except (KeyError, TypeError, ValueError) as exc:
    raise _IsolatedMarketDataWorkerError(
      "MARKET_DATA_PREPARATION_PROTOCOL_ERROR"
    ) from exc
  if (
    payload.compressed_bytes < 0
    or payload.uncompressed_bytes < 0
    or payload.record_count < 0
    or payload.compressed_bytes
    != sum(chunk.compressed_bytes for chunk in payload.chunks)
    or payload.record_count != sum(chunk.record_count for chunk in payload.chunks)
  ):
    raise _IsolatedMarketDataWorkerError("MARKET_DATA_PREPARATION_PROTOCOL_ERROR")
  seen_paths: set[Path] = set()
  for chunk in payload.chunks:
    resolved_chunk = chunk.path.resolve()
    if (
      resolved_chunk.parent != resolved_spool
      or resolved_chunk in seen_paths
      or chunk.record_count < 0
      or chunk.compressed_bytes <= 0
      or chunk.path.is_symlink()
      or not chunk.path.is_file()
      or chunk.path.stat().st_size != chunk.compressed_bytes
      or len(chunk.digest) != 64
      or any(character not in "0123456789abcdef" for character in chunk.digest)
    ):
      raise _IsolatedMarketDataWorkerError(
        "MARKET_DATA_PREPARATION_PROTOCOL_ERROR"
      )
    seen_paths.add(resolved_chunk)
  return payload


def _decode_isolated_spool_chunk(
  message: dict[str, Any],
  *,
  request_id: str,
  spool_directory: Path,
) -> tuple[int, _MarketDataSpoolChunk]:
  if (
    message.get("type") != "chunk"
    or str(message.get("request_id") or "") != request_id
    or not isinstance(message.get("chunk"), dict)
  ):
    raise _IsolatedMarketDataWorkerError("MARKET_DATA_PREPARATION_PROTOCOL_ERROR")
  try:
    index = int(message["chunk_index"])
    raw_chunk = message["chunk"]
    chunk = _MarketDataSpoolChunk(
      path=Path(str(raw_chunk["path"])),
      record_count=int(raw_chunk["record_count"]),
      digest=str(raw_chunk["digest"]),
      compressed_bytes=int(raw_chunk["compressed_bytes"]),
    )
  except (KeyError, TypeError, ValueError) as exc:
    raise _IsolatedMarketDataWorkerError(
      "MARKET_DATA_PREPARATION_PROTOCOL_ERROR"
    ) from exc
  resolved_spool = spool_directory.resolve()
  resolved_chunk = chunk.path.resolve()
  if (
    index < 0
    or resolved_chunk.parent != resolved_spool
    or chunk.record_count < 0
    or chunk.compressed_bytes <= 0
    or chunk.path.is_symlink()
    or not chunk.path.is_file()
    or chunk.path.stat().st_size != chunk.compressed_bytes
    or len(chunk.digest) != 64
    or any(character not in "0123456789abcdef" for character in chunk.digest)
  ):
    raise _IsolatedMarketDataWorkerError("MARKET_DATA_PREPARATION_PROTOCOL_ERROR")
  return index, chunk


class _HistoryUploadBandwidthLimiter:
  """Process-wide token bucket for low-priority historical HTTP traffic."""

  def __init__(
    self,
    *,
    bytes_per_second: int = HISTORY_UPLOAD_BYTES_PER_SECOND,
    burst_bytes: int = HISTORY_UPLOAD_BURST_BYTES,
  ) -> None:
    if bytes_per_second <= 0 or burst_bytes <= 0:
      raise ValueError("history upload bandwidth limits must be positive")
    self._bytes_per_second = float(bytes_per_second)
    self._capacity = float(burst_bytes)
    self._tokens = float(burst_bytes)
    self._updated_at = time.monotonic()
    self._lock = asyncio.Lock()

  async def consume(self, byte_count: int) -> None:
    if byte_count <= 0:
      return
    if byte_count > self._capacity:
      raise ValueError("history upload block exceeds token bucket capacity")
    while True:
      async with self._lock:
        now = time.monotonic()
        elapsed = max(0.0, now - self._updated_at)
        self._tokens = min(
          self._capacity,
          self._tokens + elapsed * self._bytes_per_second,
        )
        self._updated_at = now
        if self._tokens >= byte_count:
          self._tokens -= byte_count
          return
        delay = (byte_count - self._tokens) / self._bytes_per_second
      await asyncio.sleep(delay)


async def _stream_spool_chunk(
  path: Path,
  *,
  limiter: _HistoryUploadBandwidthLimiter | None = None,
  executor: ThreadPoolExecutor | None = None,
) -> AsyncIterator[bytes]:
  loop = asyncio.get_running_loop()
  file_handle = await loop.run_in_executor(executor, path.open, "rb")
  try:
    while True:
      block = await loop.run_in_executor(
        executor,
        file_handle.read,
        MARKET_DATA_UPLOAD_READ_BYTES,
      )
      if not block:
        break
      if limiter is not None:
        await limiter.consume(len(block))
      yield block
  finally:
    await loop.run_in_executor(executor, file_handle.close)


def _set_low_thread_priority() -> None:
  """Keep historical disk/IPC helpers below live trading threads on Windows."""
  if os.name != "nt":
    return
  import ctypes
  from ctypes import wintypes

  thread_priority_below_normal = -1
  kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
  kernel32.GetCurrentThread.argtypes = []
  kernel32.GetCurrentThread.restype = wintypes.HANDLE
  kernel32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
  kernel32.SetThreadPriority.restype = wintypes.BOOL
  if not kernel32.SetThreadPriority(
    kernel32.GetCurrentThread(),
    thread_priority_below_normal,
  ):
    logger.warning("Could not lower historical helper thread priority")


def _market_data_payload_fingerprint(payload: dict[str, Any]) -> str:
  try:
    serialized = json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      allow_nan=False,
    ).encode("utf-8")
  except (TypeError, ValueError) as exc:
    raise ValueError("market-data payload is not canonical JSON") from exc
  return hashlib.sha256(serialized).hexdigest()


def _websocket_url(api_url: str, path: str = "/ws/agent") -> str:
  return websocket_url(api_url, path)


def _connect_websocket(uri: str, **kwargs):
  if uri.startswith("wss://") and "ssl" not in kwargs:
    tls_context = configured_tls_context()
    if tls_context is not None:
      kwargs["ssl"] = tls_context
  connection = websockets.connect(uri, proxy=None, **kwargs)
  # The configured API root is an enrolled trust boundary. Following a server
  # redirect could move the subsequent AUTH frame to another authority.
  connection.process_redirect = lambda exc: exc
  return connection


def _websocket_close_code(exc: BaseException) -> int | None:
  for direction in ("rcvd", "sent"):
    close_frame = getattr(exc, direction, None)
    code = getattr(close_frame, "code", None)
    if isinstance(code, int):
      return code
  code = getattr(exc, "code", None)
  return code if isinstance(code, int) else None


def _websocket_close_reason(exc: BaseException) -> str:
  for direction in ("rcvd", "sent"):
    close_frame = getattr(exc, direction, None)
    reason = str(getattr(close_frame, "reason", "") or "").strip()
    if reason:
      return reason[:120]
  reason = str(getattr(exc, "reason", "") or "").strip()
  return reason[:120]


def _parse_expiry(value: Any) -> datetime:
  if not isinstance(value, str):
    raise ValueError("命令缺少 expires_at")
  parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
  if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone.utc)
  return parsed


class AgentRuntime:
  def __init__(
    self,
    *,
    configuration: DeviceConfiguration,
    device_secret: str,
    mode: str,
    allowed_accounts: set[str],
    broker=None,
    broker_factory=None,
    journal: LocalJournal,
    emergency_stop: EmergencyStopStore | None = None,
    market_spool_base_directory: Path | None = None,
    health_state: AgentHealthState | None = None,
  ) -> None:
    self.configuration = configuration
    self.device_secret = device_secret
    self.mode = mode
    self.allowed_accounts = allowed_accounts
    self.broker = broker
    self._broker_factory = broker_factory
    if self.broker is None and self._broker_factory is None:
      raise ValueError("AgentRuntime requires broker or broker_factory")
    self._broker_ready = asyncio.Event()
    if self.broker is not None:
      self._broker_ready.set()
    self.journal = journal
    self.emergency_stop = emergency_stop
    self.health_state = health_state or AgentHealthState(mode)
    self._stopped = asyncio.Event()
    self._access_token = ""
    self._access_token_expires_at = datetime.now(timezone.utc)
    self._access_token_ready = asyncio.Event()
    self._control_agent_session_id = ""
    # Sticky process-lifetime gate. The market socket must not race ahead of
    # the first successful control-hub registration, but later control socket
    # reconnects must never tear down an already READY market stream.
    self._control_hub_registered_once = asyncio.Event()
    self._session_loop: asyncio.AbstractEventLoop | None = None
    self._market_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10_000)
    self._market_event_drops = 0
    self._whole_market_capture = WholeMarketCapture(
      max_ready_callbacks=MARKET_STREAM_READY_INGRESS_CALLBACKS,
      max_ready_estimated_bytes=MARKET_STREAM_READY_INGRESS_BYTES,
      estimated_tick_bytes=MARKET_STREAM_READY_ESTIMATED_TICK_BYTES,
    )
    self._whole_market_subscription_ready = asyncio.Event()
    self._whole_market_subscription_active = False
    self._whole_market_native_reset = asyncio.Event()
    self._whole_market_encode_executor = ThreadPoolExecutor(
      max_workers=1,
      thread_name_prefix="qmt-whole-market-encode",
    )
    self._market_stream_resyncs = 0
    self._market_stream_status = "OFFLINE"
    self._market_stream_sequence = 0
    self._market_stream_ack_latency_ms = 0.0
    self._market_stream_pending_ack_monotonic = 0.0
    self._market_stream_ready_since_monotonic = 0.0
    self._market_stream_outbound_depth = 0
    self._market_stream_outbound_bytes = 0
    self._market_requests: asyncio.Queue[AgentEnvelope] = asyncio.Queue(
      maxsize=MAX_QUEUED_MARKET_DATA_REQUESTS
    )
    self._command_requests = _CommandDispatchQueue()
    self._command_request_sequence = 0
    self._active_command_count = 0
    self._market_control_requests: asyncio.Queue[AgentEnvelope] = asyncio.Queue(
      maxsize=MAX_QUEUED_MARKET_CONTROLS
    )
    self._queued_market_data_requests: dict[str, str] = {}
    # Keep the exact compressed bytes for in-flight requests so any transport
    # redelivery reuses the same checksums instead of re-querying a changing
    # XTData cache.
    self._market_upload_cache: dict[str, _MarketUploadCacheEntry] = {}
    self._market_upload_tombstones: dict[str, _MarketUploadTombstone] = {}
    self._market_upload_tasks: dict[str, _MarketUploadTaskEntry] = {}
    self._market_data_http_client: httpx.AsyncClient | None = None
    self._history_upload_slots = asyncio.Semaphore(MAX_CONCURRENT_HISTORY_UPLOADS)
    self._history_upload_limiter = _HistoryUploadBandwidthLimiter()
    self._history_upload_io_executor = ThreadPoolExecutor(
      max_workers=1,
      thread_name_prefix="qmt-history-upload-io",
      initializer=_set_low_thread_priority,
    )
    self._historical_ipc_executor = ThreadPoolExecutor(
      max_workers=1,
      thread_name_prefix="qmt-history-ipc",
      initializer=_set_low_thread_priority,
    )
    self._streamed_market_uploads: set[str] = set()
    self._provisional_market_uploads: set[str] = set()
    self._market_upload_cache_bytes = 0
    self._xtdata_access_lock = asyncio.Lock()
    self._historical_worker_lock = asyncio.Lock()
    self._websocket_send_lock = asyncio.Lock()
    self._control_socket_writer: _PriorityControlSocketWriter | None = None
    self._heartbeat_checkpoint_lock = asyncio.Lock()
    self._report_flush_lock = asyncio.Lock()
    self._report_wakeup = asyncio.Event()
    self._reports_inflight: set[str] = set()
    self._report_retry_attempts: dict[str, int] = {}
    self._report_retry_not_before: dict[str, float] = {}
    self._report_ack_requests: asyncio.Queue[str] = asyncio.Queue(
      maxsize=REPORT_ACK_QUEUE_CAPACITY
    )
    self._report_ack_pending: set[str] = set()
    self._full_snapshot_lock = asyncio.Lock()
    self._initial_reconciliation_complete = asyncio.Event()
    self._heartbeat_wakeup = asyncio.Event()
    self._heartbeat_sent_monotonic: dict[str, float] = {}
    self._control_heartbeat_ack_latency_seconds = 0.0
    self._last_complete_account_snapshot_monotonic = 0.0
    self._history_workload = "idle"
    self._history_workload_reason = ""
    self._emergency_stop_status_cache = (
      emergency_stop.status()
      if emergency_stop is not None
      else {"active": False, "reason": "", "activated_at": None}
    )
    self._historical_worker_process: Any | None = None
    self._historical_worker_connection: Any | None = None
    self._historical_worker_kind = ""
    # A live control connection starts in reconciliation and cannot advertise
    # READY again until this connection's fresh, complete snapshot was durably
    # accepted by the API.  The Engine retains the final authority to promote
    # that RECONCILING heartbeat after it applies the snapshot.
    self._trading_reconciliation_required = False
    self._trading_reconciliation_snapshot_id: str | None = None
    self._trading_reconciliation_snapshot_generation: int | None = None
    self._trading_reconciliation_snapshot_callback_failure_generation: (
      int | None
    ) = None
    self._trading_recovery_started_monotonic: float | None = None
    self._trading_recovery_reason = ""
    self._trading_account_waiting = False
    self._trading_readiness_failed = False
    self._market_data_ready_cache = False
    if broker is not None and not callable(
      getattr(broker, "ensure_market_data_ready", None)
    ):
      readiness = getattr(broker, "is_market_data_ready", None)
      try:
        self._market_data_ready_cache = (
          bool(readiness()) if callable(readiness) else True
        )
      except Exception:
        self._market_data_ready_cache = False
    self._trading_ready_cache = mode != "live"
    self._trading_connection_generation_cache = 0
    self._journal_worker = _JournalPriorityWorker()
    self._xttrading_worker = (
      _XTTradingPriorityWorker(on_timeout=self._on_xttrading_timeout)
      if mode == "live"
      else None
    )
    self._runtime_loop: asyncio.AbstractEventLoop | None = None
    self._fatal_trading_error: _FatalTradingRecoveryError | None = None
    self._fatal_trading_event = asyncio.Event()
    self._control_session_authenticated = False
    self._market_upload_clock = time.monotonic
    self._fatal_market_data_error: _FatalMarketDataPreparationError | None = None
    self._fatal_market_data_event = asyncio.Event()
    _cleanup_legacy_market_data_spools(Path(tempfile.gettempdir()))
    self._market_spool_root = _initialize_market_data_spool_root(
      market_spool_base_directory or state_directory(),
      configuration.device_id,
    )
    self._market_spool_cleanup_pending = _sweep_market_data_spool_cleanup(
      self._market_spool_root
    )
    self._market_spool_cleanup_lock = asyncio.Lock()
    self._market_spool_ephemeral_base: Path | None = None

  async def _run_journal_call(
    self,
    function: Callable[..., Any],
    *args: Any,
    priority: int,
    **kwargs: Any,
  ) -> Any:
    """Run all runtime-owned SQLite work on its one priority-aware lane."""
    worker = getattr(self, "_journal_worker", None)
    if worker is None:
      # A few focused test doubles construct AgentRuntime with ``__new__``.
      # Production always creates this lane in ``__init__``.
      worker = _JournalPriorityWorker()
      self._journal_worker = worker
    return await worker.execute(
      function,
      *args,
      priority=priority,
      **kwargs,
    )

  async def run_forever(self) -> None:
    self._runtime_loop = asyncio.get_running_loop()
    self._ensure_market_upload_state()
    self._ensure_whole_market_state()
    self._whole_market_capture.bind_loop(asyncio.get_running_loop())
    cache_sweeper = asyncio.create_task(
      self._market_upload_cache_sweeper(),
      name="market-data-cache-sweeper",
    )
    capture_supervisor = asyncio.create_task(
      self._run_after_broker_ready(self._whole_market_capture_supervisor),
      name="whole-market-capture-supervisor",
    )
    market_stream_supervisor = asyncio.create_task(
      self._run_after_broker_ready(self._whole_market_stream_supervisor),
      name="whole-market-stream-supervisor",
    )
    try:
      delay = 1
      while not self._stopped.is_set():
        try:
          if not await self._run_session_until_fatal():
            break
          delay = 1
        except _FatalTradingRecoveryError:
          raise
        except asyncio.CancelledError:
          raise
        except Exception as exc:
          session_was_authenticated = getattr(
            self,
            "_control_session_authenticated",
            False,
          )
          sleep_delay, delay = self._control_reconnect_delay(
            delay,
            authenticated=session_was_authenticated,
          )
          # A disconnected control socket immediately pauses new historical
          # native units. Preserve the prior value only long enough to choose
          # the reconnect backoff for the session that just ended.
          self._control_session_authenticated = False
          logger.warning(
            "QMT Agent disconnected: error=%s close_code=%s close_reason=%s",
            exc.__class__.__name__,
            _websocket_close_code(exc),
            _websocket_close_reason(exc) or "QMT_CONTROL_TRANSPORT_LOST",
          )
          try:
            await asyncio.wait_for(self._stopped.wait(), timeout=sleep_delay)
          except asyncio.TimeoutError:
            pass
    finally:
      for task in (
        cache_sweeper,
        capture_supervisor,
        market_stream_supervisor,
      ):
        task.cancel()
      await asyncio.gather(
        cache_sweeper,
        capture_supervisor,
        market_stream_supervisor,
        return_exceptions=True,
      )
      await self._shutdown_whole_market_capture()
      self._whole_market_capture.unbind_loop()
      self._whole_market_encode_executor.shutdown(
        wait=False,
        cancel_futures=True,
      )
      await self._cancel_market_upload_tasks()
      await self._shutdown_historical_worker()
      await self._close_market_data_upload_client()
      historical_ipc_executor = getattr(
        self,
        "_historical_ipc_executor",
        None,
      )
      if historical_ipc_executor is not None:
        historical_ipc_executor.shutdown(
          wait=False,
          cancel_futures=True,
        )
      history_upload_io_executor = getattr(
        self,
        "_history_upload_io_executor",
        None,
      )
      if history_upload_io_executor is not None:
        history_upload_io_executor.shutdown(
          wait=False,
          cancel_futures=True,
        )
      xttrading_worker = getattr(self, "_xttrading_worker", None)
      if xttrading_worker is not None:
        xttrading_worker.close()
      journal_worker = getattr(self, "_journal_worker", None)
      if journal_worker is not None:
        journal_worker.close()
      self._clear_market_upload_state()
      self._runtime_loop = None
    if self._fatal_market_data_error is not None:
      raise self._fatal_market_data_error

  @staticmethod
  def _control_reconnect_delay(
    current_delay: float, *, authenticated: bool
  ) -> tuple[float, float]:
    if authenticated:
      current_delay = 1.0
    return current_delay, min(current_delay * 2, 300.0)

  async def _run_session_until_fatal(self) -> bool:
    session = asyncio.create_task(
      self._run_session(),
      name="qmt-agent-session",
    )
    market_fatal = asyncio.create_task(
      self._fatal_market_data_event.wait(),
      name="qmt-agent-market-fatal-wait",
    )
    trading_fatal = asyncio.create_task(
      self._fatal_trading_event.wait(),
      name="qmt-agent-trading-fatal-wait",
    )
    try:
      done, _ = await asyncio.wait(
        {session, market_fatal, trading_fatal},
        return_when=asyncio.FIRST_COMPLETED,
      )
      if trading_fatal in done and self._fatal_trading_event.is_set():
        session.cancel()
        await asyncio.gather(session, return_exceptions=True)
        raise self._fatal_trading_error or _FatalTradingRecoveryError(
          "XTTrading worker failed; Agent restart required"
        )
      if market_fatal in done and self._fatal_market_data_event.is_set():
        session.cancel()
        await asyncio.gather(session, return_exceptions=True)
        return False
      for task in (market_fatal, trading_fatal):
        task.cancel()
      await asyncio.gather(market_fatal, trading_fatal, return_exceptions=True)
      await session
      return True
    finally:
      self._health_state().set_control_connected(False)
      for task in (session, market_fatal, trading_fatal):
        if not task.done():
          task.cancel()
      await asyncio.gather(
        session,
        market_fatal,
        trading_fatal,
        return_exceptions=True,
      )

  async def _market_upload_cache_sweeper(self) -> None:
    while not self._stopped.is_set():
      try:
        await asyncio.wait_for(
          self._stopped.wait(),
          timeout=MARKET_DATA_UPLOAD_CACHE_SWEEP_SECONDS,
        )
      except asyncio.TimeoutError:
        expired = self._cleanup_expired_market_uploads(remove_prepared=False)
        if expired:
          await asyncio.gather(
            *(
              asyncio.to_thread(self._remove_prepared_market_data, prepared)
              for prepared in expired
            )
          )

  async def _issue_token(self) -> tuple[str, datetime]:
    async with httpx.AsyncClient(
      timeout=10.0,
      follow_redirects=False,
      trust_env=False,
      verify=httpx_verify(self.configuration.api_url),
    ) as client:
      response = await client.post(
        f"{self.configuration.api_url}/auth/agent/token",
        json={
          "deviceId": self.configuration.device_id,
          "deviceSecret": self.device_secret,
        },
      )
    response.raise_for_status()
    payload = response.json()
    token = str(payload.get("accessToken") or payload.get("access_token") or "")
    if not token:
      raise RuntimeError("Agent token response 缺少 access token")
    expires_value = (
      payload.get("accessTokenExpiresAt")
      or payload.get("access_token_expires_at")
      or payload.get("expiresAt")
      or payload.get("expires_at")
    )
    expires_at = _parse_expiry(expires_value)
    return token, expires_at

  def _install_access_token(self, token: str, expires_at: datetime) -> None:
    self._access_token = token
    self._access_token_expires_at = expires_at
    self._access_token_ready.set()

  async def _refresh_access_token_loop(self) -> None:
    """Refresh future handshake credentials without rebuilding live sockets."""

    retry_delay = 1.0
    while True:
      renew_at = self._access_token_expires_at - timedelta(minutes=2)
      delay = max(
        1.0,
        (renew_at - datetime.now(timezone.utc)).total_seconds(),
      )
      await asyncio.sleep(delay)
      try:
        token, expires_at = await self._issue_token()
      except asyncio.CancelledError:
        raise
      except Exception as exc:
        logger.warning(
          "QMT Agent access token refresh deferred without reconnect: "
          "error=%s retry_seconds=%.0f",
          exc.__class__.__name__,
          retry_delay,
        )
        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 30.0)
        continue
      self._install_access_token(token, expires_at)
      retry_delay = 1.0
      logger.info("QMT Agent access token refreshed without reconnect")

  async def _run_after_broker_ready(self, operation) -> None:
    await self._broker_ready.wait()
    await operation()

  async def _ensure_broker_initialized(self) -> None:
    if self.broker is not None:
      self._broker_ready.set()
      self._set_market_data_ready(
        await asyncio.to_thread(self._read_broker_market_data_ready)
      )
      self._health_state().set_xttrading_connected(self._is_trading_ready())
      return
    factory = self._broker_factory
    if factory is None:  # pragma: no cover - constructor enforces this
      raise RuntimeError("QMT broker factory is unavailable")
    self.broker = (
      await self._run_native_xttrading(
        "broker-initialize",
        factory,
        timeout=XTTRADING_INITIALIZATION_TIMEOUT_SECONDS,
        priority=XTTRADING_PRIORITY_READINESS,
        coalesce_key="broker-initialize",
      )
      if self.mode == "live"
      else await asyncio.to_thread(factory)
    )
    self._broker_ready.set()
    self._set_market_data_ready(
      await asyncio.to_thread(self._read_broker_market_data_ready)
    )
    self._health_state().set_xttrading_connected(self._is_trading_ready())

  def _health_state(self) -> AgentHealthState:
    state = getattr(self, "health_state", None)
    if state is None:
      state = AgentHealthState(getattr(self, "mode", "data-only"))
      self.health_state = state
    return state

  def _set_market_stream_status(self, status: str) -> None:
    self._market_stream_status = status
    self._health_state().set_market_stream_status(status)

  def _set_market_data_ready(self, ready: bool) -> None:
    self._market_data_ready_cache = bool(ready)
    self._health_state().set_xtdata_connected(bool(ready))

  def _set_trading_ready(self, ready: bool) -> None:
    self._trading_ready_cache = bool(ready)
    self._health_state().set_xttrading_connected(bool(ready))

  def _set_trading_account_waiting(self, waiting: bool) -> None:
    was_waiting = self._trading_account_waiting
    self._trading_account_waiting = waiting
    if waiting:
      # Account login/availability is external to the Agent. A responsive RPC
      # does not need process recovery, however long the account takes to return.
      self._trading_recovery_started_monotonic = None
    elif (
      was_waiting
      and self._requires_trading_reconciliation()
      and self._trading_recovery_reason != "journal_indeterminate_commands"
    ):
      # A real transport failure or a newly eligible account gets its own full
      # recovery window; time spent waiting for the account cannot consume it.
      self._trading_recovery_started_monotonic = time.monotonic()
    if waiting != was_waiting:
      logger.info("XTTrading account availability wait changed: waiting=%s", waiting)

  def _requires_trading_reconciliation(self) -> bool:
    return bool(
      self.mode == "live" and getattr(self, "_trading_reconciliation_required", False)
    )

  def _advertised_capabilities(self) -> list[str]:
    """Return the immutable capability set for every socket and heartbeat.

    The API freezes capabilities from the authenticated control frame.  Using
    one source here prevents a later heartbeat from appearing to add a feature
    that the durable request router can never select for this session.
    """

    return [
      "market-data",
      "divid-factors",
      "financial-data-v1",
      self.mode,
    ]

  def _begin_trading_reconciliation(
    self,
    reason: str,
  ) -> None:
    """Fail closed until a newly generated complete snapshot is acknowledged."""
    if self.mode != "live":
      return
    already_reconciling = self._requires_trading_reconciliation()
    self._trading_reconciliation_required = True
    self._health_state().set_reconciliation_ready(False)
    self._trading_reconciliation_snapshot_id = None
    self._trading_reconciliation_snapshot_generation = None
    self._trading_reconciliation_snapshot_callback_failure_generation = None
    self._trading_recovery_reason = reason[:128]
    if self._trading_account_waiting:
      self._trading_recovery_started_monotonic = None
    elif not already_reconciling or self._trading_recovery_started_monotonic is None:
      self._trading_recovery_started_monotonic = time.monotonic()
    require_reconciliation = getattr(
      getattr(self, "broker", None),
      "require_trading_reconciliation",
      None,
    )
    if callable(require_reconciliation):
      try:
        require_reconciliation()
      except Exception as exc:
        logger.warning(
          "Could not close the local XTTrading order gate: error=%s",
          exc.__class__.__name__,
        )

  def _acknowledge_trading_reconciliation_snapshot(
    self,
    report_message_id: str,
  ) -> bool:
    if (
      self.mode != "live"
      or not self._requires_trading_reconciliation()
      or report_message_id != self._trading_reconciliation_snapshot_id
    ):
      return False
    snapshot_generation = self._trading_reconciliation_snapshot_generation
    if snapshot_generation is None:
      self._begin_trading_reconciliation("snapshot_generation_missing")
      return False
    callback_failure_generation = (
      self._trading_reconciliation_snapshot_callback_failure_generation
    )
    if callback_failure_generation is None:
      self._begin_trading_reconciliation(
        "snapshot_callback_failure_generation_missing"
      )
      return False
    journal = getattr(self, "journal", None)
    processing_commands = (
      int(journal.stats()["processing_commands"]) if journal is not None else 0
    )
    if processing_commands > 0:
      # A complete broker snapshot can prove a PLACE_ORDER or a terminal
      # CANCEL, but it cannot make an unknown native outcome disappear. Keep
      # new orders fail-closed while still allowing later cancellations.
      self._trading_recovery_reason = "journal_indeterminate_commands"
      self._trading_recovery_started_monotonic = None
      logger.error(
        "XTTrading reconciliation retained local order gate: "
        "processing_commands=%s",
        processing_commands,
      )
      return False
    mark_reconciled = getattr(self.broker, "mark_trading_reconciled", None)
    try:
      generation_is_current = (
        bool(
          mark_reconciled(
            snapshot_generation,
            callback_failure_generation,
          )
        )
        if callable(mark_reconciled)
        else (
          snapshot_generation == self._trading_connection_generation()
          and callback_failure_generation
          == self._read_broker_callback_failure_generation()
        )
      )
    except Exception as exc:
      logger.warning(
        "Could not open the local XTTrading order gate: error=%s",
        exc.__class__.__name__,
      )
      generation_is_current = False
    if not generation_is_current:
      self._begin_trading_reconciliation("snapshot_generation_stale")
      return False
    self._trading_reconciliation_required = False
    self._health_state().set_reconciliation_ready(True)
    self._trading_reconciliation_snapshot_id = None
    self._trading_reconciliation_snapshot_generation = None
    self._trading_reconciliation_snapshot_callback_failure_generation = None
    self._trading_recovery_started_monotonic = None
    self._trading_recovery_reason = ""
    self._trading_readiness_failed = False
    logger.info(
      "XTTrading reconciliation snapshot acknowledged; awaiting Engine promotion"
    )
    return True

  def _heartbeat_status(self) -> str:
    return "RECONCILING" if self._requires_trading_reconciliation() else "READY"

  def _raise_if_trading_recovery_expired(self) -> None:
    if not self._requires_trading_reconciliation():
      return
    if self._trading_account_waiting:
      return
    if self._trading_recovery_reason == "journal_indeterminate_commands":
      return
    started = self._trading_recovery_started_monotonic
    if started is None:
      return
    elapsed = time.monotonic() - started
    if elapsed < XTTRADING_RECOVERY_MAX_SECONDS:
      return
    raise _FatalTradingRecoveryError(
      "XTTrading recovery did not obtain an acknowledged complete snapshot "
      f"within {XTTRADING_RECOVERY_MAX_SECONDS}s"
    )

  async def _run_session(self) -> None:
    self._control_session_authenticated = False
    access_token, expires_at = await self._issue_token()
    self._install_access_token(access_token, expires_at)
    async with _connect_websocket(
      _websocket_url(self.configuration.api_url),
      max_size=8 * 1024 * 1024,
      ping_interval=WEBSOCKET_PING_INTERVAL_SECONDS,
      # Historical XTData work is process-isolated.  Keep a normal bounded
      # transport timeout so a genuinely stale control connection is rebuilt.
      ping_timeout=WEBSOCKET_PING_TIMEOUT_SECONDS,
    ) as socket:
      auth = AgentEnvelope(
        message_type=AgentMessageType.AUTH,
        payload={
          "device_id": self.configuration.device_id,
          "access_token": self._access_token,
          "agent_version": AGENT_VERSION,
          "capabilities": self._advertised_capabilities(),
        },
      )
      await self._send_socket_text(socket, auth.model_dump_json())
      auth_result = AgentEnvelope.model_validate_json(await socket.recv())
      if (
        auth_result.message_type is not AgentMessageType.AUTH_RESULT
        or not auth_result.payload.get("accepted")
      ):
        raise RuntimeError("QMT Agent authentication rejected")
      control_agent_session_id = str(
        auth_result.payload.get("agent_session_id") or ""
      ).strip()
      if not control_agent_session_id:
        raise RuntimeError("QMT Agent authentication missing control session id")
      self._control_agent_session_id = control_agent_session_id
      self._control_socket_writer = _PriorityControlSocketWriter(socket)
      self._control_session_authenticated = True
      self._health_state().set_control_connected(True)
      self._control_hub_registered_once.set()

      self._session_loop = asyncio.get_running_loop()
      self._market_requests = asyncio.Queue(maxsize=MAX_QUEUED_MARKET_DATA_REQUESTS)
      self._queued_market_data_requests = {}
      self._command_requests = _CommandDispatchQueue()
      self._market_control_requests = asyncio.Queue(
        maxsize=MAX_QUEUED_MARKET_CONTROLS
      )
      self._command_request_sequence = 0
      self._active_command_count = 0
      self._reports_inflight = set()
      self._report_retry_attempts = {}
      self._report_retry_not_before = {}
      self._report_ack_requests = asyncio.Queue(
        maxsize=REPORT_ACK_QUEUE_CAPACITY
      )
      self._report_ack_pending = set()
      self._report_wakeup = asyncio.Event()
      self._initial_reconciliation_complete = asyncio.Event()
      self._heartbeat_sent_monotonic.clear()
      self._control_heartbeat_ack_latency_seconds = 0.0
      self._market_event_drops = 0
      self._begin_trading_reconciliation(
        "control_session_connected",
      )
      session_tasks = {
        "socket-writer": asyncio.create_task(
          self._control_socket_writer.run(),
          name="qmt-agent-control-writer",
        ),
        "receiver": asyncio.create_task(
          self._receive_session_messages(socket),
          name="qmt-agent-receiver",
        ),
        "heartbeat": asyncio.create_task(
          self._heartbeat_loop(socket),
          name="qmt-agent-heartbeat",
        ),
        "initialization": asyncio.create_task(
          self._initialize_authenticated_session(),
          name="qmt-agent-initial-reconciliation",
        ),
        "token-refresh": asyncio.create_task(
          self._refresh_access_token_loop(),
          name="qmt-agent-token-refresh",
        ),
        "report-sender": asyncio.create_task(
          self._broker_report_loop(socket),
          name="qmt-agent-report-sender",
        ),
        "report-ack-writer": asyncio.create_task(
          self._report_ack_loop(socket),
          name="qmt-agent-report-ack-writer",
        ),
        "emergency-refresh": asyncio.create_task(
          self._emergency_stop_refresh_loop(),
          name="qmt-agent-emergency-refresh",
        ),
        "market-request": asyncio.create_task(
          self._market_request_loop(socket),
          name="qmt-agent-market-request",
        ),
        **{
          f"market-request-{index}": asyncio.create_task(
            self._market_request_loop(socket), name=f"qmt-agent-market-request-{index}"
          ) for index in range(1, MAX_CACHED_MARKET_DATA_REQUESTS)
        },
        "market-control": asyncio.create_task(
          self._market_control_loop(),
          name="qmt-agent-market-control",
        ),
        "command-worker": asyncio.create_task(
          self._command_request_loop(socket),
          name="qmt-agent-command-worker",
        ),
        "market-readiness": asyncio.create_task(
          self._market_data_readiness_loop(),
          name="qmt-agent-market-readiness",
        ),
        "trading-readiness": asyncio.create_task(
          self._trading_readiness_loop(socket),
          name="qmt-agent-trading-readiness",
        ),
        "account-snapshot": asyncio.create_task(
          self._account_snapshot_loop(),
          name="qmt-agent-account-snapshot",
        ),
      }
      self._heartbeat_wakeup.set()
      try:
        await self._supervise_session_tasks(
          socket,
          session_tasks,
        )
      finally:
        for task in session_tasks.values():
          if not task.done():
            task.cancel()
        await asyncio.gather(*session_tasks.values(), return_exceptions=True)
        self._control_socket_writer = None
        self._session_loop = None

  async def _initialize_authenticated_session(self) -> None:
    await self._ensure_broker_initialized()
    if self.mode == "live":
      await self._ensure_trading_ready()
    await self._queue_full_snapshot(reconciliation=self.mode == "live")
    self._initial_reconciliation_complete.set()
    self._report_wakeup.set()
    self._heartbeat_wakeup.set()

  async def _receive_session_messages(self, socket) -> None:
    async for raw_message in socket:
      await self._handle_message(socket, raw_message)

  async def _supervise_session_tasks(
    self,
    socket,
    tasks: dict[str, asyncio.Task[None]],
  ) -> None:
    receiver = tasks["receiver"]
    while True:
      done, _ = await asyncio.wait(
        set(tasks.values()),
        return_when=asyncio.FIRST_COMPLETED,
      )
      if receiver in done:
        await receiver
        return
      for role, task in list(tasks.items()):
        if role == "receiver" or task not in done:
          continue
        try:
          await task
        except asyncio.CancelledError:
          raise
        except Exception as exc:
          logger.error(
            "QMT Agent session task failed: role=%s error=%s",
            role,
            exc.__class__.__name__,
          )
          await socket.close(
            code=1011,
            reason=f"session task failed: {role}",
          )
          raise
        if role == "initialization":
          tasks.pop(role, None)
          continue
        if role != "market-request":
          error = RuntimeError(f"QMT Agent session task stopped unexpectedly: {role}")
          logger.error("%s", error)
          await socket.close(
            code=1011,
            reason=f"session task stopped: {role}",
          )
          raise error
        await receiver
        return

  async def _run_native_xttrading(
    self,
    operation: str,
    function,
    *args,
    timeout: float,
    priority: int = XTTRADING_PRIORITY_ORDER,
    coalesce_key: str | None = None,
  ) -> Any:
    """Run one prioritized native call; timeout starts only after execution."""
    worker = self._ensure_xttrading_worker()
    submission = worker.submit(
      operation=operation,
      function=function,
      args=args,
      timeout=timeout,
      priority=priority,
      coalesce_key=coalesce_key,
    )
    try:
      # A queued cancel may wait for the one already-running native call, but a
      # low-priority snapshot ahead of it cannot consume the cancel's native
      # execution budget.  The dedicated worker also prevents session
      # cancellation from accidentally starting a second native call.
      await asyncio.shield(submission.waiter.started)
      return await asyncio.shield(submission.waiter.outcome)
    except _FatalTradingRecoveryError as exc:
      self._trip_trading_fatal(exc, operation)
      raise
    except asyncio.CancelledError:
      worker.abandon(submission)
      raise

  def _ensure_xttrading_worker(self) -> _XTTradingPriorityWorker:
    worker = getattr(self, "_xttrading_worker", None)
    if worker is None:
      worker = _XTTradingPriorityWorker(on_timeout=self._on_xttrading_timeout)
      self._xttrading_worker = worker
    return worker

  def _on_xttrading_timeout(self, error: BaseException, operation: str) -> None:
    fatal = (
      error
      if isinstance(error, _FatalTradingRecoveryError)
      else _FatalTradingRecoveryError(
        f"XTTrading {operation} failed; Agent restart required"
      )
    )
    loop = getattr(self, "_runtime_loop", None) or getattr(
      self,
      "_session_loop",
      None,
    )
    if loop is None or loop.is_closed():
      return
    try:
      loop.call_soon_threadsafe(self._trip_trading_fatal, fatal, operation)
    except RuntimeError:
      pass

  def _trip_trading_fatal(
    self,
    error: _FatalTradingRecoveryError,
    operation: str,
  ) -> None:
    if getattr(self, "_fatal_trading_error", None) is None:
      self._fatal_trading_error = error
    self._set_trading_ready(False)
    self._trading_readiness_failed = True
    self._begin_trading_reconciliation(f"{operation}_timed_out")
    fatal_event = getattr(self, "_fatal_trading_event", None)
    if fatal_event is not None:
      fatal_event.set()

  def _read_broker_trading_generation(self) -> int:
    generation_reader = getattr(
      self.broker,
      "trading_connection_generation",
      None,
    )
    if not callable(generation_reader):
      return 0
    try:
      return max(0, int(generation_reader()))
    except Exception as exc:
      logger.warning(
        "XTTrading connection generation check failed: error=%s",
        exc.__class__.__name__,
      )
      return 0

  def _read_broker_trading_mutation_generation(self) -> int | None:
    generation_reader = getattr(
      self.broker,
      "trading_mutation_generation",
      None,
    )
    if not callable(generation_reader):
      return None
    try:
      return max(0, int(generation_reader()))
    except Exception as exc:
      logger.warning(
        "XTTrading mutation generation check failed: error=%s",
        exc.__class__.__name__,
      )
      raise RuntimeError("XTTrading mutation generation is unavailable") from exc

  def _read_broker_callback_failure_generation(self) -> int:
    generation_reader = getattr(
      self.broker,
      "trading_callback_failure_generation",
      None,
    )
    if not callable(generation_reader):
      return 0
    try:
      return max(0, int(generation_reader()))
    except Exception as exc:
      logger.warning(
        "XTTrading callback failure generation check failed: error=%s",
        exc.__class__.__name__,
      )
      raise RuntimeError(
        "XTTrading callback failure generation is unavailable"
      ) from exc

  def _assert_trading_mutation_generation(
    self,
    expected_generation: int | None,
  ) -> None:
    if expected_generation is None:
      return
    if self._read_broker_trading_mutation_generation() != expected_generation:
      raise _StaleTradingSnapshotError(
        "XTTrading state changed during full snapshot"
      )

  def _assert_callback_failure_generation(
    self,
    expected_generation: int,
  ) -> None:
    if self._read_broker_callback_failure_generation() != expected_generation:
      raise _StaleTradingSnapshotError(
        "XTTrading callback gap changed during full snapshot"
      )

  async def _capture_full_snapshot(
    self,
    *,
    reconciliation: bool,
  ) -> tuple[dict[str, Any], int | None, int | None, int | None]:
    if self.mode != "live":
      return await asyncio.to_thread(self.broker.full_snapshot), None, None, None

    priority = (
      XTTRADING_PRIORITY_SNAPSHOT
      if reconciliation
      else XTTRADING_PRIORITY_PERIODIC_SNAPSHOT
    )
    snapshot_kind = "reconciliation" if reconciliation else "periodic"
    capture_partition = getattr(
      self.broker,
      "capture_full_snapshot_partition",
      None,
    )
    assemble_partitions = getattr(
      self.broker,
      "assemble_full_snapshot_partitions",
      None,
    )
    if callable(capture_partition) and callable(assemble_partitions):
      partitions: dict[str, dict[str, dict[str, Any]]] = {}
      snapshot_generation: int | None = None
      mutation_generation = self._read_broker_trading_mutation_generation()
      callback_failure_generation = (
        self._read_broker_callback_failure_generation()
      )
      for partition in LIVE_FULL_SNAPSHOT_PARTITIONS:
        captured_partition = await self._run_native_xttrading(
          f"full-snapshot-{partition.replace('_', '-')}",
          capture_partition,
          partition,
          timeout=XTTRADING_SNAPSHOT_TIMEOUT_SECONDS,
          priority=priority,
          coalesce_key=f"{snapshot_kind}-snapshot-{partition}",
        )
        if (
          not isinstance(captured_partition, tuple)
          or len(captured_partition) != 2
        ):
          raise RuntimeError(
            "XTTrading snapshot partition did not return its generation"
          )
        section, generation = captured_partition
        if not isinstance(section, dict):
          raise RuntimeError("XTTrading snapshot partition must be an object")
        normalized_generation = max(0, int(generation))
        if snapshot_generation is None:
          snapshot_generation = normalized_generation
        elif snapshot_generation != normalized_generation:
          raise RuntimeError(
            "XTTrading connection generation changed between snapshot partitions"
          )
        self._assert_trading_mutation_generation(mutation_generation)
        self._assert_callback_failure_generation(callback_failure_generation)
        partitions[partition] = section
      captured = await self._run_native_xttrading(
        "full-snapshot-assemble",
        assemble_partitions,
        partitions,
        snapshot_generation if snapshot_generation is not None else 0,
        timeout=XTTRADING_SNAPSHOT_TIMEOUT_SECONDS,
        priority=priority,
        coalesce_key=f"{snapshot_kind}-snapshot-assemble",
      )
      if (
        snapshot_generation is not None
        and self._read_broker_trading_generation() != snapshot_generation
      ):
        raise RuntimeError(
          "XTTrading connection generation changed after full snapshot"
        )
      self._assert_trading_mutation_generation(mutation_generation)
      self._assert_callback_failure_generation(callback_failure_generation)
    else:
      mutation_generation = self._read_broker_trading_mutation_generation()
      callback_failure_generation = (
        self._read_broker_callback_failure_generation()
      )
      capture = getattr(self.broker, "capture_full_snapshot", None)
      if callable(capture):
        captured = await self._run_native_xttrading(
          "full-snapshot",
          capture,
          timeout=XTTRADING_SNAPSHOT_TIMEOUT_SECONDS,
          priority=priority,
          coalesce_key=f"{snapshot_kind}-snapshot",
        )
      else:

        def capture_with_generation() -> tuple[dict[str, Any], int]:
          return self.broker.full_snapshot(), self._read_broker_trading_generation()

        captured = await self._run_native_xttrading(
          "full-snapshot",
          capture_with_generation,
          timeout=XTTRADING_SNAPSHOT_TIMEOUT_SECONDS,
          priority=priority,
          coalesce_key=f"{snapshot_kind}-snapshot",
        )
      self._assert_trading_mutation_generation(mutation_generation)
      self._assert_callback_failure_generation(callback_failure_generation)
    if not isinstance(captured, tuple) or len(captured) != 2:
      raise RuntimeError("XTTrading full snapshot did not return its generation")
    snapshot, generation = captured
    if not isinstance(snapshot, dict):
      raise RuntimeError("XTTrading full snapshot payload must be an object")
    normalized_generation = max(0, int(generation))
    self._trading_connection_generation_cache = normalized_generation
    return (
      snapshot,
      normalized_generation,
      mutation_generation,
      callback_failure_generation,
    )

  async def _queue_full_snapshot(
    self,
    *,
    reconciliation: bool = False,
  ) -> str:
    """Journal one broker snapshot and bind recovery only to a complete one."""
    async with self._full_snapshot_lock:
      if reconciliation:
        self._begin_trading_reconciliation("fresh_snapshot_requested")
      for attempt in range(XTTRADING_SNAPSHOT_MUTATION_RETRIES):
        try:
          (
            snapshot,
            snapshot_generation,
            mutation_generation,
            callback_failure_generation,
          ) = (
            await self._capture_full_snapshot(
              reconciliation=reconciliation,
            )
          )
          snapshot_message_id = str(uuid.uuid4())
          snapshot = await self._run_journal_call(
            self._persist_captured_full_snapshot,
            snapshot,
            snapshot_message_id,
            snapshot_generation,
            mutation_generation,
            callback_failure_generation,
            priority=JOURNAL_PRIORITY_SNAPSHOT,
          )
        except _StaleTradingSnapshotError:
          if attempt + 1 < XTTRADING_SNAPSHOT_MUTATION_RETRIES:
            logger.info(
              "XTTrading state changed during snapshot; retrying: attempt=%s",
              attempt + 1,
            )
            await asyncio.sleep(0)
            continue
          if reconciliation or self._requires_trading_reconciliation():
            self._begin_trading_reconciliation("snapshot_state_unstable")
          raise
        except _FatalTradingRecoveryError:
          raise
        except Exception:
          self._begin_trading_reconciliation("snapshot_query_failed")
          if self.mode == "live":
            self._set_trading_ready(False)
            self._trading_readiness_failed = True
          raise

        is_complete = snapshot.get("is_complete") is True
        if is_complete:
          self._last_complete_account_snapshot_monotonic = time.monotonic()
        if self.mode == "live":
          if not is_complete:
            # LiveBroker marks the native session unhealthy as well.  Never let
            # an incomplete report serve as recovery evidence.
            self._begin_trading_reconciliation("snapshot_incomplete")
            self._set_trading_ready(False)
            self._trading_readiness_failed = True
          elif reconciliation or self._requires_trading_reconciliation():
            self._trading_reconciliation_snapshot_id = snapshot_message_id
            self._trading_reconciliation_snapshot_generation = snapshot_generation
            self._trading_reconciliation_snapshot_callback_failure_generation = (
              callback_failure_generation
            )
        return snapshot_message_id
      raise AssertionError("snapshot retry loop exited unexpectedly")

  def _persist_captured_full_snapshot(
    self,
    snapshot: dict[str, Any],
    message_id: str,
    connection_generation: int | None,
    mutation_generation: int | None,
    callback_failure_generation: int | None,
  ) -> dict[str, Any]:
    """Correlate and journal a snapshot under the broker's mutation fence."""

    def persist() -> dict[str, Any]:
      self._reconcile_snapshot_correlations(snapshot)
      snapshot["snapshot_id"] = message_id
      snapshot["report_id"] = message_id
      enriched = enrich_report_payload(AgentMessageType.DELTA_REPORT, snapshot)
      envelope = AgentEnvelope(
        message_id=message_id,
        message_type=AgentMessageType.DELTA_REPORT,
        payload=enriched,
      )
      # A newer full snapshot only supersedes older full snapshots. Incremental
      # callbacks retain their journal order around this guarded commit.
      self._persist_full_snapshot_report(
        envelope.message_id,
        envelope.model_dump_json(),
      )
      return enriched

    if (
      self.mode != "live"
      or mutation_generation is None
      or callback_failure_generation is None
    ):
      return persist()
    state_is_current = getattr(self.broker, "trading_state_is_current", None)
    if not callable(state_is_current):
      raise RuntimeError("Live broker cannot fence a full snapshot commit")
    # Durable callback writes take this same RLock. A callback already fenced
    # at enqueue invalidates the snapshot here; one arriving after this check
    # is journaled after the snapshot and therefore cannot be overwritten by it.
    with self.journal.lock:
      if not state_is_current(
        connection_generation if connection_generation is not None else 0,
        mutation_generation,
        callback_failure_generation,
      ):
        raise _StaleTradingSnapshotError(
          "XTTrading state changed before full snapshot persistence"
        )
      persisted = persist()
    if not isinstance(persisted, dict):
      raise RuntimeError("Full snapshot persistence returned no payload")
    return persisted

  def _reconcile_snapshot_correlations(self, snapshot: dict[str, Any]) -> None:
    correlations = self.journal.broker_order_client_ids()
    for collection_name in ("orders", "trades"):
      for item in snapshot.get(collection_name) or []:
        if not isinstance(item, dict):
          continue
        broker_order_id = item.get("order_id") or item.get("broker_order_id")
        self.journal.reconcile_processing_cancel(
          broker_order_id=broker_order_id,
          order_status=str(
            item.get("effective_order_status")
            or item.get("order_status")
            or item.get("status")
            or ""
          ),
        )
        client_order_id = correlations.get(str(broker_order_id))
        if client_order_id:
          item["client_order_id"] = client_order_id
          self.journal.reconcile_processing_order(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
          )

  def _persist_full_snapshot_report(
    self,
    message_id: str,
    serialized: str,
  ) -> None:
    self.journal.retire_pending_full_snapshots()
    self.journal.add_report(message_id, serialized)

  async def _flush_reports(self, socket) -> None:
    async with self._report_flush_lock:
      available = REPORT_SEND_WINDOW - len(self._reports_inflight)
      if available <= 0:
        return
      serialized_reports = await self._run_journal_call(
        self.journal.pending_reports,
        priority=JOURNAL_PRIORITY_REPORT_SCAN,
        limit=REPORT_SEND_SCAN_LIMIT,
      )
      now = time.monotonic()
      for serialized in serialized_reports:
        try:
          report_envelope = AgentEnvelope.model_validate_json(serialized)
        except (TypeError, ValueError, ValidationError):
          # A pre-upgrade journal row is retained for reconciliation/audit and
          # must never be rewritten or retransmitted as the current protocol.
          logger.warning("Skipping incompatible historical journal report")
          continue
        if report_envelope.protocol_version != PROTOCOL_VERSION:
          logger.warning(
            "Skipping historical journal report with protocol=%s",
            report_envelope.protocol_version,
          )
          continue
        message_id = report_envelope.message_id
        if (
          not message_id
          or message_id in self._reports_inflight
          or self._report_retry_not_before.get(message_id, 0.0) > now
        ):
          continue
        await self._send_socket_text(socket, serialized)
        self._reports_inflight.add(message_id)
        available -= 1
        if available <= 0:
          break

  async def _heartbeat_loop(self, socket) -> None:
    self._ensure_market_upload_state()
    while True:
      try:
        await asyncio.wait_for(
          self._heartbeat_wakeup.wait(),
          timeout=CONTROL_HEARTBEAT_INTERVAL_SECONDS,
        )
      except asyncio.TimeoutError:
        pass
      self._heartbeat_wakeup.clear()
      self._raise_if_trading_recovery_expired()
      heartbeat_status = (
        self._heartbeat_status() if self.mode == "live" else "READY"
      )
      await self._heartbeat_checkpoint(socket, status=heartbeat_status)

  async def _account_snapshot_loop(self) -> None:
    await self._initial_reconciliation_complete.wait()
    while True:
      await asyncio.sleep(ACCOUNT_SNAPSHOT_INTERVAL_SECONDS)
      try:
        await self._queue_full_snapshot(reconciliation=False)
      except _FatalTradingRecoveryError:
        raise
      except Exception as exc:
        logger.warning(
          "Periodic account snapshot failed: error=%s",
          exc.__class__.__name__,
        )
      self._report_wakeup.set()
      self._heartbeat_wakeup.set()

  def _is_market_data_ready(self) -> bool:
    broker = getattr(self, "broker", None)
    if broker is None:
      return bool(getattr(self, "_market_data_ready_cache", False))
    if hasattr(self, "_market_data_ready_cache"):
      return bool(self._market_data_ready_cache)
    # Focused Runtime doubles created without __init__ retain a direct probe.
    # Production heartbeats and QoS always consume the event-loop-safe cache.
    return self._read_broker_market_data_ready()

  def _read_broker_market_data_ready(self) -> bool:
    broker = getattr(self, "broker", None)
    if broker is None:
      return True
    readiness = getattr(broker, "is_market_data_ready", None)
    if not callable(readiness):
      return True
    try:
      return bool(readiness())
    except Exception as exc:
      logger.warning(
        "QMT broker readiness check failed: error=%s",
        exc.__class__.__name__,
      )
      return False

  async def _market_data_readiness_loop(self) -> None:
    await self._broker_ready.wait()
    ensure_ready = getattr(self.broker, "ensure_market_data_ready", None)
    if not callable(ensure_ready):
      ensure_ready = getattr(self.broker, "is_market_data_ready", None)
    if not callable(ensure_ready):
      self._set_market_data_ready(True)
      while True:
        await asyncio.sleep(XTDATA_READINESS_RETRY_SECONDS)
    previous = self._is_market_data_ready()
    self._set_market_data_ready(previous)
    while True:
      try:
        current = bool(
          await self._run_xtdata_control(
            "market-data-readiness",
            ensure_ready,
          )
        )
      except _FatalMarketDataPreparationError:
        raise
      except Exception as exc:
        logger.warning(
          "XTData readiness retry failed: error=%s",
          exc.__class__.__name__,
        )
        current = False
      self._set_market_data_ready(current)
      if current != previous:
        logger.info(
          "XTData readiness changed: ready=%s",
          current,
        )
      previous = current
      await asyncio.sleep(XTDATA_READINESS_RETRY_SECONDS)

  def _is_trading_ready(self) -> bool:
    if self.mode != "live":
      return True
    if hasattr(self, "_trading_ready_cache"):
      return bool(self._trading_ready_cache)
    # Unit-level Runtime doubles created without __init__ retain the legacy
    # direct probe.  Production instances always use the event-loop-safe cache.
    readiness = getattr(self.broker, "is_trading_ready", None)
    if not callable(readiness):
      return False
    try:
      return bool(readiness())
    except Exception as exc:
      logger.warning(
        "QMT trading readiness check failed: error=%s",
        exc.__class__.__name__,
      )
      return False

  def _trading_connection_generation(self) -> int:
    if hasattr(self, "_trading_connection_generation_cache"):
      return max(0, int(self._trading_connection_generation_cache))
    return self._read_broker_trading_generation()

  async def _ensure_trading_ready(self) -> bool:
    if self.mode != "live":
      return True
    ensure_ready = getattr(self.broker, "ensure_trading_ready", None)
    if not callable(ensure_ready):
      self._set_trading_account_waiting(False)
      self._set_trading_ready(False)
      self._trading_readiness_failed = True
      return False

    def ensure_with_generation() -> tuple[bool, int, bool]:
      ready = bool(ensure_ready())
      transport_healthy = self.broker.is_trading_transport_healthy()
      return ready, self._read_broker_trading_generation(), transport_healthy

    try:
      ready, generation, transport_healthy = await self._run_native_xttrading(
        "readiness",
        ensure_with_generation,
        timeout=XTTRADING_RECONNECT_TIMEOUT_SECONDS,
        priority=XTTRADING_PRIORITY_READINESS,
        coalesce_key="readiness",
      )
      self._trading_connection_generation_cache = max(0, int(generation))
      self._set_trading_account_waiting(transport_healthy and not ready)
      self._set_trading_ready(bool(ready))
      self._trading_readiness_failed = not ready
      return bool(ready)
    except _FatalTradingRecoveryError:
      raise
    except Exception as exc:
      logger.warning(
        "XTTrading readiness retry failed: error=%s",
        exc.__class__.__name__,
      )
    self._set_trading_account_waiting(False)
    self._set_trading_ready(False)
    self._trading_readiness_failed = True
    return False

  async def _trading_readiness_loop(self, socket) -> None:
    await self._broker_ready.wait()
    await self._initial_reconciliation_complete.wait()
    previous = self._is_trading_ready()
    previous_generation = self._trading_connection_generation()
    while True:
      ensured = await self._ensure_trading_ready()
      current = bool(ensured)
      current_generation = self._trading_connection_generation()
      reconnect_observed = current_generation != previous_generation
      self._refresh_journal_reconciliation_gate()
      broker_reconciliation = getattr(
        self.broker,
        "trading_requires_reconciliation",
        None,
      )
      if (
        current
        and callable(broker_reconciliation)
        and bool(broker_reconciliation())
        and not self._requires_trading_reconciliation()
      ):
        self._begin_trading_reconciliation("report_pipeline_unhealthy")
      if current != previous or reconnect_observed:
        logger.info("XTTrading readiness changed: ready=%s", current)
        if not current:
          self._begin_trading_reconciliation(
            "xttrading_unavailable",
          )
        else:
          self._begin_trading_reconciliation(
            "xttrading_reconnected",
          )
          await self._queue_full_snapshot(reconciliation=True)
        await self._heartbeat_checkpoint(
          socket,
          status=self._heartbeat_status(),
        )
      elif (
        current
        and self._requires_trading_reconciliation()
        and self._trading_reconciliation_snapshot_id is None
      ):
        # A failed/incomplete snapshot invalidates the cached native session.
        # Once the registry reconnects it, obtain a new authoritative snapshot
        # rather than waiting for the periodic heartbeat cadence.
        await self._queue_full_snapshot(reconciliation=True)
        await self._heartbeat_checkpoint(
          socket,
          status=self._heartbeat_status(),
        )
      previous = current
      previous_generation = current_generation
      self._raise_if_trading_recovery_expired()
      await asyncio.sleep(XTTRADING_READINESS_RETRY_SECONDS)

  def _refresh_journal_reconciliation_gate(self) -> None:
    journal = getattr(self, "journal", None)
    if (
      self._trading_recovery_reason != "journal_indeterminate_commands"
      or journal is None
      or int(journal.stats()["processing_commands"]) > 0
    ):
      return
    # The previously ACKed snapshot stayed bound to an unresolved command.
    # Once that command gains durable evidence, require one new generation-
    # current snapshot rather than opening the gate from stale evidence.
    self._trading_reconciliation_snapshot_id = None
    self._trading_reconciliation_snapshot_generation = None
    self._trading_reconciliation_snapshot_callback_failure_generation = None
    self._trading_recovery_reason = "journal_commands_resolved"
    self._trading_recovery_started_monotonic = time.monotonic()

  async def _broker_report_loop(self, socket) -> None:
    """Flush callbacks already persisted by LiveBroker without snapshot delay."""
    while True:
      try:
        await asyncio.wait_for(self._report_wakeup.wait(), timeout=1.0)
      except asyncio.TimeoutError:
        pass
      self._report_wakeup.clear()
      await self._flush_reports(socket)

  async def _report_ack_loop(self, socket) -> None:
    """Persist report ACKs in bounded batches away from the socket receiver."""
    while True:
      first = await self._report_ack_requests.get()
      message_ids = [first]
      while len(message_ids) < REPORT_SEND_SCAN_LIMIT:
        try:
          message_ids.append(self._report_ack_requests.get_nowait())
        except asyncio.QueueEmpty:
          break
      try:
        await self._run_journal_call(
          self.journal.acknowledge_reports,
          message_ids,
          priority=JOURNAL_PRIORITY_REPORT_ACK,
        )
      except BaseException:
        for message_id in message_ids:
          self._report_ack_pending.discard(message_id)
        raise
      else:
        wake_heartbeat = False
        for message_id in message_ids:
          self._report_ack_pending.discard(message_id)
          self._reports_inflight.discard(message_id)
          self._report_retry_attempts.pop(message_id, None)
          self._report_retry_not_before.pop(message_id, None)
          wake_heartbeat = (
            self._acknowledge_trading_reconciliation_snapshot(message_id)
            or wake_heartbeat
          )
        if wake_heartbeat and socket is not None:
          self._heartbeat_wakeup.set()
        self._wake_report_sender()
      finally:
        for _ in message_ids:
          self._report_ack_requests.task_done()

  async def _emergency_stop_refresh_loop(self) -> None:
    """Refresh external emergency-stop changes without touching heartbeat IO."""
    while True:
      store = getattr(self, "emergency_stop", None)
      if store is not None:
        self._emergency_stop_status_cache = await asyncio.to_thread(store.status)
      await asyncio.sleep(1.0)

  def _emergency_stop_active(self) -> bool:
    if getattr(self, "emergency_stop", None) is None:
      return False
    status = getattr(self, "_emergency_stop_status_cache", None)
    # A runtime constructed without the normal initializer cannot prove the
    # local safety file is clear, so retain the fail-closed behavior.
    return True if status is None else bool(status.get("active"))

  def _enqueue_market_event(self, payload: dict[str, Any]) -> None:
    # Single-instrument quotes now belong to the process-lifetime market
    # transport. Do not couple their callback bridge to a transient control
    # WebSocket session.
    loop = getattr(self, "_runtime_loop", None) or self._session_loop
    if loop is None or loop.is_closed():
      return

    def enqueue() -> None:
      if self._market_events.full():
        # Quotes are transient state. Keep the newest observation and retire
        # the oldest queued observation without failing the control session.
        try:
          self._market_events.get_nowait()
        except asyncio.QueueEmpty:
          pass
        else:
          self._market_events.task_done()
          self._market_event_drops += 1
          if self._market_event_drops == 1 or self._market_event_drops % 1024 == 0:
            logger.warning(
              "Dropped stale single-quote events under backpressure: count=%s",
              self._market_event_drops,
            )
      self._market_events.put_nowait(payload)

    loop.call_soon_threadsafe(enqueue)

  def _enqueue_whole_market_event(self, data: Any) -> None:
    self._ensure_whole_market_state()
    self._whole_market_capture.capture(data)

  async def _whole_market_capture_supervisor(self) -> None:
    """Own the one native subscription for the lifetime of the Agent process."""
    delay = 1.0
    while not self._stopped.is_set():
      try:
        ensure_ready = getattr(self.broker, "ensure_market_data_ready", None)
        if callable(ensure_ready):
          await self._run_xtdata_control(
            "whole-market-readiness",
            ensure_ready,
          )
        accepted = bool(
          await self._run_xtdata_control(
            "subscribe-whole-market",
            self.broker.subscribe_whole_market,
            self._enqueue_whole_market_event,
          )
        )
        if not accepted:
          raise RuntimeError("XTData rejected whole-market subscription")
        self._whole_market_subscription_active = True
        self._whole_market_subscription_ready.set()
        self._health_state().set_xtdata_connected(True)
        generation_reader = getattr(
          self.broker,
          "market_data_connection_generation",
          None,
        )
        subscription_generation_reader = getattr(
          self.broker,
          "market_data_subscription_generation",
          generation_reader,
        )
        subscribed_generation = int(
          subscription_generation_reader()
          if callable(subscription_generation_reader)
          else 0
        )
        subscription_started = time.monotonic()
        silence_confirmations = 0
        logger.info(
          "QMT process-wide whole-market subscription is active: generation=%s",
          subscribed_generation,
        )
        reset_reason = ""
        while not self._stopped.is_set():
          try:
            await asyncio.wait_for(
              self._stopped.wait(),
              timeout=MARKET_STREAM_NATIVE_HEALTH_CHECK_SECONDS,
            )
            return
          except asyncio.TimeoutError:
            pass

          current_generation = int(
            generation_reader() if callable(generation_reader) else 0
          )
          if subscribed_generation > 0 and current_generation != subscribed_generation:
            reset_reason = (
              "XTData source generation changed: "
              f"{subscribed_generation}->{current_generation}"
            )
            break

          readiness = getattr(self.broker, "is_market_data_ready", None)
          connected = bool(readiness()) if callable(readiness) else True
          if not connected:
            self._health_state().set_xtdata_connected(False)
            try:
              connected = (
                bool(
                  await self._run_xtdata_control(
                    "whole-market-readiness",
                    ensure_ready,
                  )
                )
                if callable(ensure_ready)
                else False
              )
            except Exception as exc:
              logger.warning(
                "QMT whole-market readiness probe failed: error=%s",
                exc.__class__.__name__,
              )
              continue
            if connected:
              self._health_state().set_xtdata_connected(True)
              reset_reason = "XTData connection recovered after disconnect"
              break

          session_reader = getattr(
            self.broker,
            "is_whole_market_trading_session",
            None,
          )
          in_trading_session = False
          if callable(session_reader):
            try:
              in_trading_session = bool(
                await self._run_xtdata_control(
                  "whole-market-trading-session",
                  session_reader,
                )
              )
            except Exception as exc:
              logger.warning(
                "QMT trading-session probe failed: error=%s",
                exc.__class__.__name__,
              )
          if not in_trading_session:
            silence_confirmations = 0
            continue
          stats = self._whole_market_capture.stats()
          last_callback = float(stats["last_callback_monotonic"])
          silence_seconds = time.monotonic() - max(
            subscription_started,
            last_callback,
          )
          if silence_seconds >= MARKET_STREAM_NATIVE_SILENCE_SECONDS:
            silence_confirmations += 1
          else:
            silence_confirmations = 0
          if silence_confirmations >= MARKET_STREAM_NATIVE_SILENCE_CONFIRMATIONS:
            reset_reason = (
              "XTData callback silence confirmed during trading session: "
              f"seconds={silence_seconds:.3f}"
            )
            break

        if not reset_reason:
          return
        logger.error("QMT whole-market native subscription reset: %s", reset_reason)
        self._set_market_stream_status("STALE")
        # Publish the exact continuity-loss reason before waking the stream
        # task.  Otherwise the event can win the scheduling race and the
        # stream supervisor can only report a generic native-reset error.
        self._whole_market_capture.force_resync(reset_reason)
        self._whole_market_native_reset.set()
        self._whole_market_subscription_ready.clear()
        self._whole_market_subscription_active = False
        try:
          await self._run_xtdata_control(
            "unsubscribe-whole-market",
            self.broker.unsubscribe_whole_market,
          )
        except Exception as exc:
          fatal = _FatalMarketDataPreparationError(
            "could not cancel invalid native whole-market subscription; "
            "Agent restart required"
          )
          self._trip_market_data_fatal(fatal)
          raise fatal from exc
        self._whole_market_capture.reset_source(reset_reason)
        delay = 1.0
      except asyncio.CancelledError:
        raise
      except _FatalMarketDataPreparationError:
        raise
      except Exception as exc:
        if not self._whole_market_subscription_active:
          self._health_state().set_xtdata_connected(False)
          self._whole_market_subscription_ready.clear()
          self._whole_market_capture.reset_source(
            f"whole-market native subscription attempt failed: {exc.__class__.__name__}"
          )
        logger.warning(
          "QMT whole-market subscription retry: error=%s",
          exc.__class__.__name__,
        )
        try:
          await asyncio.wait_for(self._stopped.wait(), timeout=delay)
        except asyncio.TimeoutError:
          pass
        delay = min(delay * 2, 30.0)

  async def _shutdown_whole_market_capture(self) -> None:
    if not self._whole_market_subscription_active:
      return
    self._whole_market_subscription_active = False
    self._whole_market_subscription_ready.clear()
    try:
      await self._run_xtdata_control(
        "unsubscribe-whole-market",
        self.broker.unsubscribe_whole_market,
      )
    except Exception as exc:
      logger.warning(
        "Could not remove process-wide whole-market subscription: error=%s",
        exc.__class__.__name__,
      )

  @staticmethod
  def _market_stream_retry_delay(
    current_delay: float,
    *,
    ready_seconds: float,
  ) -> tuple[float, float]:
    if ready_seconds > 0:
      return 1.0, 1.0
    return current_delay, min(current_delay * 2, 30.0)

  async def _wait_for_fresh_access_token(
    self, *, previous_token: str | None = None
  ) -> None:
    while True:
      if (
        self._access_token
        and self._access_token != previous_token
        and self._access_token_expires_at
        > datetime.now(timezone.utc) + timedelta(seconds=5)
      ):
        return
      self._access_token_ready.clear()
      if (
        self._access_token
        and self._access_token != previous_token
        and self._access_token_expires_at
        > datetime.now(timezone.utc) + timedelta(seconds=5)
      ):
        continue
      await self._access_token_ready.wait()

  async def _wait_for_initial_control_hub_registration(self) -> None:
    self._ensure_whole_market_state()
    await self._control_hub_registered_once.wait()

  async def _whole_market_stream_supervisor(self) -> None:
    delay = 1.0
    while True:
      try:
        await self._run_whole_market_stream()
        raise RuntimeError("whole-market stream stopped unexpectedly")
      except asyncio.CancelledError:
        self._whole_market_capture.begin_syncing()
        self._set_market_stream_status("OFFLINE")
        self._market_stream_ready_since_monotonic = 0.0
        raise
      except Exception as exc:
        ready_since = self._market_stream_ready_since_monotonic
        ready_seconds = (
          max(0.0, time.monotonic() - ready_since) if ready_since > 0 else 0.0
        )
        sleep_delay, delay = self._market_stream_retry_delay(
          delay,
          ready_seconds=ready_seconds,
        )
        self._market_stream_resyncs += 1
        self._set_market_stream_status("SYNCING")
        self._market_stream_ready_since_monotonic = 0.0
        reason_code = str(
          getattr(exc, "reason_code", None) or exc.__class__.__name__
        )[:64]
        close_code = getattr(exc, "code", None)
        close_reason = str(getattr(exc, "reason", None) or "")[:256]
        error_detail = str(exc)[:256]
        logger.warning(
          "QMT whole-market stream reconnecting: resyncs=%s "
          "ready_seconds=%.3f reason_code=%s close_code=%s "
          "close_reason=%s error=%s",
          self._market_stream_resyncs,
          ready_seconds,
          reason_code,
          close_code,
          close_reason,
          error_detail,
        )
        await asyncio.sleep(
          sleep_delay + random.uniform(0.0, min(1.0, sleep_delay * 0.2))
        )

  async def _perform_market_stream_handshake(
    self,
    socket,
    *,
    access_token: str,
  ) -> MarketStreamControl:
    auth = AgentEnvelope(
      message_type=AgentMessageType.AUTH,
      payload={
        "device_id": self.configuration.device_id,
        "access_token": access_token,
        "agent_version": AGENT_VERSION,
        "capabilities": self._advertised_capabilities(),
        "agent_session_id": getattr(self, "_control_agent_session_id", ""),
      },
    )
    await asyncio.wait_for(
      socket.send(auth.model_dump_json()),
      timeout=MARKET_STREAM_HANDSHAKE_TIMEOUT_SECONDS,
    )
    raw_auth_result = await asyncio.wait_for(
      socket.recv(),
      timeout=MARKET_STREAM_HANDSHAKE_TIMEOUT_SECONDS,
    )
    auth_result = AgentEnvelope.model_validate_json(raw_auth_result)
    if (
      auth_result.message_type is not AgentMessageType.AUTH_RESULT
      or not auth_result.payload.get("accepted")
    ):
      raise _MarketStreamHandshakeError(
        str(auth_result.payload.get("reason_code") or "MARKET_AUTH_REJECTED"),
        str(auth_result.payload.get("reason") or "market authentication rejected"),
      )
    raw_start = await asyncio.wait_for(
      socket.recv(),
      timeout=MARKET_STREAM_HANDSHAKE_TIMEOUT_SECONDS,
    )
    start = MarketStreamControl.model_validate_json(raw_start)
    if (
      start.type is not MarketControlType.START
      or start.markets != MARKET_STREAM_MARKETS
    ):
      raise RuntimeError("invalid market stream START frame")
    return start

  async def _run_whole_market_stream(self) -> None:
    self._ensure_whole_market_state()
    await self._wait_for_fresh_access_token()
    await self._wait_for_initial_control_hub_registration()
    market_access_token = self._access_token
    self._set_market_stream_status("SYNCING")
    self._market_stream_sequence = 0
    self._market_stream_ready_since_monotonic = 0.0
    self._market_stream_outbound_depth = 0
    self._market_stream_outbound_bytes = 0
    await self._whole_market_subscription_ready.wait()
    self._whole_market_native_reset.clear()
    self._whole_market_capture.begin_syncing()
    async with _connect_websocket(
      _websocket_url(self.configuration.api_url, "/ws/agent/market"),
      subprotocols=[MARKET_STREAM_SUBPROTOCOL],
      max_size=MAX_MARKET_STREAM_FRAME_BYTES,
      ping_interval=WEBSOCKET_PING_INTERVAL_SECONDS,
      ping_timeout=WEBSOCKET_PING_TIMEOUT_SECONDS,
      open_timeout=MARKET_STREAM_HANDSHAKE_TIMEOUT_SECONDS,
    ) as socket:
      start = await self._perform_market_stream_handshake(
        socket,
        access_token=market_access_token,
      )
      stream_trading_date = datetime.now(SHANGHAI_ZONE).date()
      (
        snapshot_raw,
        snapshot_watermark,
        universe_codes,
      ) = await self._build_whole_market_snapshot(stream_trading_date)
      self._require_native_whole_market_sync("snapshot-build")
      snapshot = await self._prepare_encoded_market_batch(
        stream_id=start.stream_id,
        sequence=1,
        kind=MarketBatchKind.SNAPSHOT,
        captured_at=datetime.now(timezone.utc),
        raw_data=snapshot_raw,
        universe_codes=universe_codes,
      )
      sent_tick_fingerprints = {
        code: orjson.dumps(tick) for code, tick in snapshot.batch.data.items()
      }
      self._require_native_whole_market_sync("snapshot-encode")
      self._require_native_whole_market_sync("snapshot-send")
      await self._send_encoded_market_batch_and_wait_ack(socket, snapshot)
      self._require_native_whole_market_sync("snapshot-ack")
      logger.info(
        "QMT whole-market snapshot acknowledged: stream_id=%s "
        "instruments=%s watermark=%s bytes=%s",
        start.stream_id,
        snapshot.batch.instrument_count,
        snapshot_watermark,
        len(snapshot.payload),
      )
      ready_barrier_delta = self._whole_market_capture.converged_event(
        after_sequence=snapshot_watermark,
        trading_date=stream_trading_date,
      )
      ready_barrier_watermark = ready_barrier_delta.capture_sequence
      ready_barrier = await self._prepare_encoded_market_batch(
        stream_id=start.stream_id,
        sequence=2,
        kind=MarketBatchKind.DELTA,
        captured_at=ready_barrier_delta.captured_at,
        raw_data=ready_barrier_delta.data,
        dedupe_fingerprints=sent_tick_fingerprints,
      )
      self._require_native_whole_market_sync("ready-barrier-encode")
      self._whole_market_capture.raise_if_invalidated()

      # Keep the capture in latest-state convergence while sequence 2 waits
      # for its ACK.  A slow downstream cannot force us to retain every native
      # callback before the stream is READY; the post-ACK atomic cut below
      # turns all updates after this watermark into one sequence 3 event.
      barrier_tasks: list[asyncio.Task[Any]] = []
      try:
        native_reset = asyncio.create_task(
          self._whole_market_native_reset.wait(),
          name="whole-market-ready-barrier-native-reset",
        )
        capture_invalidated = asyncio.create_task(
          self._whole_market_capture.wait_until_invalidated(),
          name="whole-market-ready-barrier-capture-invalidated",
        )
        barrier_ack = asyncio.create_task(
          self._send_encoded_market_batch_and_wait_ack(socket, ready_barrier),
          name="whole-market-ready-barrier-ack",
        )
        barrier_tasks.extend([native_reset, capture_invalidated, barrier_ack])
        done, _ = await asyncio.wait(
          {native_reset, capture_invalidated, barrier_ack},
          return_when=asyncio.FIRST_COMPLETED,
        )
        if capture_invalidated in done:
          self._whole_market_capture.raise_if_invalidated()
          raise RuntimeError(
            "whole-market capture invalidated without a recorded reason"
          )
        if native_reset in done and self._whole_market_native_reset.is_set():
          self._whole_market_capture.raise_if_invalidated()
          raise RuntimeError(
            "native whole-market subscription reset without a recorded reason"
          )
        await barrier_ack
      finally:
        for task in barrier_tasks:
          if not task.done():
            task.cancel()
        if barrier_tasks:
          await asyncio.gather(*barrier_tasks, return_exceptions=True)

      self._require_native_whole_market_sync("ready-barrier-ack")
      self._whole_market_capture.raise_if_invalidated()
      ready_confirmation_event = self._whole_market_capture.activate_ready(
        after_sequence=ready_barrier_watermark,
        trading_date=stream_trading_date,
      )
      self._require_native_whole_market_sync("ready-cut")
      self._whole_market_capture.raise_if_invalidated()
      ready_confirmation = await self._prepare_encoded_market_batch(
        stream_id=start.stream_id,
        sequence=3,
        kind=MarketBatchKind.DELTA,
        captured_at=ready_confirmation_event.captured_at,
        raw_data=ready_confirmation_event.data,
        dedupe_fingerprints=sent_tick_fingerprints,
      )
      self._require_native_whole_market_sync("ready-confirmation-encode")
      self._whole_market_capture.raise_if_invalidated()

      outbound = _BoundedMarketBatchBuffer(
        max_batches=MARKET_STREAM_OUTBOUND_BATCHES,
        max_bytes=MARKET_STREAM_OUTBOUND_BYTES,
      )
      # Sequence 3 is mandatory, even when no instrument changed while the
      # sequence-2 ACK was in flight.  It proves to the API that the Agent has
      # received that ACK and atomically switched to ordered READY capture.
      # Sending it through the normal bounded transport lets sequence 4+
      # callbacks apply the same two-unacknowledged-batch backpressure rather
      # than reopening a special unbounded ACK window.
      await outbound.put(ready_confirmation)
      self._market_stream_outbound_depth = outbound.depth
      self._market_stream_outbound_bytes = outbound.bytes
      logger.info(
        "QMT whole-market ordered capture activated; awaiting readiness ACK: "
        "stream_id=%s barrier_sequence=2 confirmation_sequence=3 "
        "barrier_instruments=%s watermark=%s "
        "confirmation_instruments=%s",
        start.stream_id,
        ready_barrier.batch.instrument_count,
        ready_barrier_watermark,
        ready_confirmation.batch.instrument_count,
      )
      pipeline_tasks: list[asyncio.Task[Any]] = []
      try:
        producer = asyncio.create_task(
          self._whole_market_batch_producer(
            outbound,
            stream_id=start.stream_id,
            starting_sequence=3,
            trading_date=stream_trading_date,
            first_event=None,
            dedupe_fingerprints=sent_tick_fingerprints,
          ),
          name="whole-market-batch-producer",
        )
        transport = asyncio.create_task(
          self._transmit_market_batches(
            socket,
            outbound,
            stream_id=start.stream_id,
          ),
          name="whole-market-batch-transport",
        )
        native_reset = asyncio.create_task(
          self._whole_market_native_reset.wait(),
          name="whole-market-native-reset",
        )
        capture_invalidated = asyncio.create_task(
          self._whole_market_capture.wait_until_invalidated(),
          name="whole-market-capture-invalidated",
        )
        pipeline_tasks.extend([producer, transport, native_reset, capture_invalidated])
        done, _ = await asyncio.wait(
          {
            producer,
            transport,
            native_reset,
            capture_invalidated,
          },
          return_when=asyncio.FIRST_COMPLETED,
        )
        if capture_invalidated in done:
          self._whole_market_capture.raise_if_invalidated()
          raise RuntimeError(
            "whole-market capture invalidated without a recorded reason"
          )
        if native_reset in done and self._whole_market_native_reset.is_set():
          self._whole_market_capture.raise_if_invalidated()
          raise RuntimeError(
            "native whole-market subscription reset without a recorded reason"
          )
        if producer in done:
          await producer
          raise RuntimeError("whole-market batch producer stopped unexpectedly")
        if transport in done:
          await transport
          raise RuntimeError("whole-market batch transport stopped unexpectedly")
        raise RuntimeError("whole-market stream stopped without an owner")
      finally:
        for task in pipeline_tasks:
          if not task.done():
            task.cancel()
        if pipeline_tasks:
          await asyncio.gather(*pipeline_tasks, return_exceptions=True)
        self._whole_market_capture.begin_syncing()
        self._set_market_stream_status("SYNCING")

  def _require_native_whole_market_sync(self, stage: str) -> None:
    if (
      self._whole_market_native_reset.is_set()
      or not self._whole_market_subscription_ready.is_set()
    ):
      capture = getattr(self, "_whole_market_capture", None)
      reason = capture.invalidation_reason if capture is not None else ""
      reason_suffix = f" reason={reason}" if reason else ""
      raise RuntimeError(
        "native whole-market subscription changed during sync: "
        f"stage={stage}{reason_suffix}"
      )

  @staticmethod
  def _whole_market_tick_source_time(
    tick: Any,
    *,
    reference_at: datetime | None = None,
  ) -> float:
    try:
      return market_tick_source_time(tick, reference_at=reference_at)
    except ValueError:
      return 0.0

  async def _build_whole_market_snapshot(
    self,
    trading_date: date,
  ) -> tuple[dict[str, Any], int, tuple[str, ...]]:
    codes_reader = getattr(self.broker, "whole_market_codes", None)
    expected_values = (
      await self._run_xtdata_control(
        "whole-market-codes",
        codes_reader,
      )
      if callable(codes_reader)
      else ()
    )
    expected_codes = frozenset(expected_values or ())
    if not expected_codes:
      raise RuntimeError("XTData returned an empty SH/SZ universe")

    def snapshot_ready(data: dict[str, Any]) -> bool:
      coverage = len(expected_codes.intersection(data)) / len(expected_codes)
      required = MARKET_STREAM_REQUIRED_INDEX_CODES.intersection(expected_codes)
      return coverage >= MARKET_STREAM_MIN_INITIAL_COVERAGE and required <= data.keys()

    deadline = time.monotonic() + MARKET_STREAM_INITIAL_PUSH_WAIT_SECONDS
    latest = self._whole_market_capture.latest_snapshot(trading_date=trading_date)
    while not snapshot_ready(latest.data):
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        break
      try:
        await self._whole_market_capture.wait_for_change(
          after_sequence=latest.capture_watermark,
          timeout=remaining,
        )
      except asyncio.TimeoutError:
        break
      latest = self._whole_market_capture.latest_snapshot(trading_date=trading_date)
    if not snapshot_ready(latest.data):
      available = len(expected_codes.intersection(latest.data))
      coverage = available / len(expected_codes)
      missing_required = sorted(
        MARKET_STREAM_REQUIRED_INDEX_CODES.intersection(expected_codes).difference(
          latest.data
        )
      )
      raise RuntimeError(
        "QMT initial whole-quote callback coverage is insufficient: "
        f"available={available} expected={len(expected_codes)} "
        f"coverage={coverage:.4f} missing_required={missing_required}"
      )

    snapshot = {
      code: tick for code, tick in latest.data.items() if code in expected_codes
    }
    missing_codes = expected_codes.difference(snapshot)
    if missing_codes:
      missing_samples = sorted(missing_codes)[:5]
      logger.warning(
        "QMT whole-market snapshot omits instruments without an available "
        "tick: missing=%s expected=%s samples=%s",
        len(missing_codes),
        len(expected_codes),
        missing_samples,
      )
    logger.info(
      "QMT whole-market snapshot built from native callback state: "
      "expected=%s available=%s missing=%s",
      len(expected_codes),
      len(snapshot),
      len(missing_codes),
    )
    return snapshot, latest.capture_watermark, tuple(sorted(expected_codes))

  async def _prepare_encoded_market_batch(
    self,
    *,
    stream_id: str,
    sequence: int,
    kind: MarketBatchKind,
    captured_at: datetime,
    raw_data: dict[str, Any],
    universe_codes: tuple[str, ...] = (),
    dedupe_fingerprints: dict[str, bytes] | None = None,
  ) -> _EncodedMarketBatch:
    def prepare() -> _EncodedMarketBatch:
      validation_reference_at = datetime.now(timezone.utc)
      data = self.broker.prepare_whole_market_data(raw_data)
      if not isinstance(data, dict) or (kind is MarketBatchKind.SNAPSHOT and not data):
        raise RuntimeError("XTData returned an empty whole-market batch")
      missing_source_time = [
        code
        for code, tick in data.items()
        if self._whole_market_tick_source_time(
          tick,
          reference_at=validation_reference_at,
        )
        <= 0
      ]
      if missing_source_time:
        samples = ",".join(sorted(missing_source_time)[:5])
        raise RuntimeError(
          "whole-market batch contains tick without a valid source time: "
          f"stream_id={stream_id} sequence={sequence} "
          f"kind={kind.value} invalid={len(missing_source_time)} "
          f"samples={samples}"
        )
      if dedupe_fingerprints is not None:
        fingerprints = {code: orjson.dumps(tick) for code, tick in data.items()}
        data = {
          code: tick
          for code, tick in data.items()
          if dedupe_fingerprints.get(code) != fingerprints[code]
        }
        dedupe_fingerprints.update(fingerprints)
      batch = MarketStreamBatch(
        stream_id=stream_id,
        sequence=sequence,
        kind=kind,
        captured_at=captured_at,
        instrument_count=len(data),
        universe_codes=universe_codes,
        data=data,
      )
      try:
        payload = batch.to_bytes()
      except ValueError as exc:
        # Preserve the stable protocol-limit diagnostic without relaying an
        # arbitrary serialization error that could contain native payload data.
        error_detail = (
          "payload exceeds 64 MiB"
          if "exceeds 64 MiB" in str(exc)
          else f"error={exc.__class__.__name__}"
        )
        raise RuntimeError(
          "whole-market batch encoding failed: "
          f"stream_id={stream_id} sequence={sequence} "
          f"kind={kind.value} instruments={len(data)} "
          f"{error_detail}"
        ) from None
      return _EncodedMarketBatch(batch=batch, payload=payload)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
      self._whole_market_encode_executor,
      prepare,
    )

  async def _whole_market_batch_producer(
    self,
    outbound: _BoundedMarketBatchBuffer,
    *,
    stream_id: str,
    starting_sequence: int,
    trading_date: date,
    first_event: CapturedMarketEvent | None,
    dedupe_fingerprints: dict[str, bytes],
  ) -> None:
    source_events: deque[CapturedMarketEvent] = deque()
    fragments: deque[CapturedMarketEvent] = deque()
    if first_event is not None:
      source_events.append(first_event)
    pending_fragment: CapturedMarketEvent | None = None
    last_capture_sequence = 0
    sequence = starting_sequence

    async def next_fragment() -> CapturedMarketEvent:
      nonlocal last_capture_sequence
      if not fragments:
        event = (
          source_events.popleft()
          if source_events
          else await self._whole_market_capture.next_ready_event()
        )
        if event.capture_sequence <= last_capture_sequence:
          raise RuntimeError("whole-market capture sequence is not increasing")
        last_capture_sequence = event.capture_sequence
        items = list(event.data.items())
        for offset in range(
          0,
          len(items),
          MARKET_STREAM_MICROBATCH_INSTRUMENTS,
        ):
          values = dict(items[offset : offset + MARKET_STREAM_MICROBATCH_INSTRUMENTS])
          fragment_estimated_bytes = max(
            1024,
            (event.estimated_bytes * len(values) + max(1, len(items)) - 1)
            // max(1, len(items)),
          )
          fragments.append(
            CapturedMarketEvent(
              capture_sequence=event.capture_sequence,
              captured_at=event.captured_at,
              captured_monotonic=event.captured_monotonic,
              data=values,
              estimated_bytes=fragment_estimated_bytes,
            )
          )
      return fragments.popleft()

    while True:
      fragment = pending_fragment or await next_fragment()
      pending_fragment = None
      if fragment.captured_at.astimezone(SHANGHAI_ZONE).date() != trading_date:
        raise RuntimeError("trading day changed; full snapshot required")
      raw_data = dict(fragment.data)
      codes = set(raw_data)
      estimated_bytes = fragment.estimated_bytes
      captured_at = fragment.captured_at
      deadline = time.monotonic() + MARKET_STREAM_MICROBATCH_SECONDS
      while (
        len(raw_data) < MARKET_STREAM_MICROBATCH_INSTRUMENTS
        and estimated_bytes < MARKET_STREAM_MICROBATCH_ESTIMATED_BYTES
      ):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          break
        try:
          candidate = await asyncio.wait_for(
            next_fragment(),
            timeout=remaining,
          )
        except asyncio.TimeoutError:
          break
        if candidate.captured_at.astimezone(SHANGHAI_ZONE).date() != trading_date:
          raise RuntimeError("trading day changed; full snapshot required")
        if (
          codes.intersection(candidate.data)
          or len(raw_data) + len(candidate.data) > MARKET_STREAM_MICROBATCH_INSTRUMENTS
          or estimated_bytes + candidate.estimated_bytes
          > MARKET_STREAM_MICROBATCH_ESTIMATED_BYTES
        ):
          pending_fragment = candidate
          break
        raw_data.update(candidate.data)
        codes.update(candidate.data)
        estimated_bytes += candidate.estimated_bytes
        captured_at = min(captured_at, candidate.captured_at)

      sequence += 1
      serialize_started = time.monotonic()
      encoded = await self._prepare_encoded_market_batch(
        stream_id=stream_id,
        sequence=sequence,
        kind=MarketBatchKind.DELTA,
        captured_at=captured_at,
        raw_data=raw_data,
        dedupe_fingerprints=dedupe_fingerprints,
      )
      await outbound.put(encoded)
      self._market_stream_outbound_depth = outbound.depth
      self._market_stream_outbound_bytes = outbound.bytes
      logger.debug(
        "QMT whole-market batch prepared: sequence=%s instruments=%s "
        "serialize_ms=%.3f bytes=%s capture_queue=%s outbound=%s",
        sequence,
        encoded.batch.instrument_count,
        (time.monotonic() - serialize_started) * 1000,
        len(encoded.payload),
        self._whole_market_capture.queue_depth,
        outbound.depth,
      )

  @staticmethod
  def _parse_market_control_frame(raw_control: Any) -> MarketStreamControl:
    if not isinstance(raw_control, str):
      raise RuntimeError("market stream control must be a text frame")
    control = MarketStreamControl.model_validate_json(raw_control)
    if control.type is MarketControlType.RESYNC:
      raise RuntimeError(control.reason or "API requested market resync")
    return control

  async def _transmit_market_batches(
    self,
    socket,
    outbound: _BoundedMarketBatchBuffer,
    *,
    stream_id: str,
  ) -> None:
    self._ensure_whole_market_state()
    pending: deque[_PendingMarketAck] = deque()
    receive_task = asyncio.create_task(socket.recv())
    get_task: asyncio.Task[_EncodedMarketBatch] | None = None
    market_event_task: asyncio.Task[dict[str, Any]] | None = None
    try:
      while True:
        if len(pending) < MARKET_STREAM_MAX_UNACKNOWLEDGED_BATCHES and get_task is None:
          get_task = asyncio.create_task(outbound.get())
        if market_event_task is None:
          market_event_task = asyncio.create_task(self._market_events.get())
        waiters: set[asyncio.Task[Any]] = {receive_task}
        if get_task is not None:
          waiters.add(get_task)
        # Never start a replaceable text send while a binary ACK is pending.
        # This keeps the ACK deadline and whole-market send window authoritative.
        if not pending:
          waiters.add(market_event_task)
        timeout = None
        if pending:
          timeout = max(
            0.0,
            MARKET_STREAM_ACK_TIMEOUT_SECONDS
            - (time.monotonic() - pending[0].sent_monotonic),
          )
        done, _ = await asyncio.wait(
          waiters,
          timeout=timeout,
          return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
          expected_sequence = pending[0].encoded.batch.sequence if pending else 0
          raise asyncio.TimeoutError(
            "market stream ACK timed out: "
            f"stream_id={stream_id} sequence={expected_sequence} "
            f"unacknowledged={len(pending)} outbound={outbound.depth}"
          )

        if receive_task in done:
          control = self._parse_market_control_frame(receive_task.result())
          if not pending:
            raise RuntimeError(
              "market stream ACK arrived with no pending batch: "
              f"stream_id={control.stream_id} sequence={control.sequence}"
            )
          expected = pending.popleft()
          self._market_stream_pending_ack_monotonic = (
            pending[0].sent_monotonic if pending else 0.0
          )
          if (
            control.type is not MarketControlType.ACK
            or control.stream_id != stream_id
            or control.sequence != expected.encoded.batch.sequence
          ):
            raise RuntimeError(
              "market stream ACK does not match sent batch: "
              f"expected_stream_id={stream_id} "
              f"expected_sequence={expected.encoded.batch.sequence} "
              f"actual_type={control.type.value} "
              f"actual_stream_id={control.stream_id} "
              f"actual_sequence={control.sequence}"
            )
          latency_ms = (time.monotonic() - expected.sent_monotonic) * 1000
          await outbound.acknowledge(expected.encoded)
          self._market_stream_sequence = expected.encoded.batch.sequence
          self._market_stream_ack_latency_ms = latency_ms
          self._market_stream_outbound_depth = outbound.depth
          self._market_stream_outbound_bytes = outbound.bytes
          if expected.encoded.batch.sequence == 3:
            # Sequence 3 is the readiness commit: the API has durably applied
            # the post-barrier convergence batch and acknowledged it.  The
            # capture is already ordered at this point, but the Agent's public
            # stream status must remain fail-closed until this ACK arrives.
            self._set_market_stream_status("READY")
            self._market_stream_ready_since_monotonic = time.monotonic()
            logger.info(
              "QMT whole-market stream ready: stream_id=%s sequence=3",
              stream_id,
            )
          logger.debug(
            "QMT market batch ACK: sequence=%s bytes=%s latency_ms=%.3f "
            "unacknowledged=%s outbound=%s",
            expected.encoded.batch.sequence,
            len(expected.encoded.payload),
            latency_ms,
            len(pending),
            outbound.depth,
          )
          receive_task = asyncio.create_task(socket.recv())
          # Processing an ACK may have opened a binary send slot. Re-enter
          # selection so a queued batch is considered before any text event.
          continue

        if get_task is not None and get_task in done:
          encoded = get_task.result()
          get_task = None
          if encoded.batch.stream_id != stream_id:
            raise RuntimeError("market batch belongs to another stream")
          started = time.monotonic()
          await asyncio.wait_for(
            socket.send(encoded.payload),
            timeout=WEBSOCKET_SEND_TIMEOUT_SECONDS,
          )
          pending.append(
            _PendingMarketAck(
              encoded=encoded,
              sent_monotonic=started,
            )
          )
          if len(pending) == 1:
            self._market_stream_pending_ack_monotonic = started
          # A ready binary frame always wins over a queued single-instrument
          # event. Re-enter the scheduler before considering the low lane so
          # a burst of whole-market frames cannot be interleaved behind text.
          continue

        if market_event_task in done:
          payload = market_event_task.result()
          market_event_task = None
          try:
            # The task may have been held while binary ACKs were outstanding.
            # Coalesce that replaceable observation to the newest queued state
            # before using the market socket's one low-priority send turn.
            while True:
              try:
                newer_payload = self._market_events.get_nowait()
              except asyncio.QueueEmpty:
                break
              self._market_events.task_done()
              self._market_event_drops += 1
              payload = newer_payload
            serialized = AgentEnvelope(
              message_type=AgentMessageType.MARKET_EVENT,
              payload=payload,
            ).model_dump_json()
            try:
              await asyncio.wait_for(
                socket.send(serialized),
                timeout=MARKET_EVENT_SEND_TIMEOUT_SECONDS,
              )
            except asyncio.TimeoutError as exc:
              # wait_for() cancels the in-flight websocket send. Reusing that
              # websocket after a cancelled send is not a supported transport
              # state, so fail this market-only session and let its supervisor
              # reconnect. The independent control connection remains intact.
              self._market_event_drops += 1
              if self._market_event_drops == 1 or self._market_event_drops % 1024 == 0:
                logger.warning(
                  "Resetting market socket after single-quote send timeout: "
                  "count=%s",
                  self._market_event_drops,
                )
              raise RuntimeError(
                "market socket send timed out; reconnect required"
              ) from exc
          finally:
            self._market_events.task_done()
    finally:
      self._market_stream_pending_ack_monotonic = 0.0
      if market_event_task is not None and market_event_task.done():
        if not market_event_task.cancelled():
          market_event_task.result()
          self._market_events.task_done()
        market_event_task = None
      tasks = [receive_task]
      if get_task is not None:
        tasks.append(get_task)
      if market_event_task is not None:
        tasks.append(market_event_task)
      for task in tasks:
        if not task.done():
          task.cancel()
      await asyncio.gather(*tasks, return_exceptions=True)

  async def _send_market_batch_and_wait_ack(
    self,
    socket,
    batch: MarketStreamBatch,
  ) -> None:
    await self._send_encoded_market_batch_and_wait_ack(
      socket,
      _EncodedMarketBatch(batch=batch, payload=batch.to_bytes()),
    )

  async def _send_encoded_market_batch_and_wait_ack(
    self,
    socket,
    encoded: _EncodedMarketBatch,
  ) -> None:
    started = time.monotonic()
    self._market_stream_pending_ack_monotonic = started
    try:
      await asyncio.wait_for(
        socket.send(encoded.payload),
        timeout=WEBSOCKET_SEND_TIMEOUT_SECONDS,
      )
      try:
        raw_ack = await asyncio.wait_for(
          socket.recv(),
          timeout=MARKET_STREAM_ACK_TIMEOUT_SECONDS,
        )
      except asyncio.TimeoutError as exc:
        raise asyncio.TimeoutError(
          "market stream ACK timed out: "
          f"stream_id={encoded.batch.stream_id} "
          f"sequence={encoded.batch.sequence}"
        ) from exc
      ack = self._parse_market_control_frame(raw_ack)
      if (
        ack.type is not MarketControlType.ACK
        or ack.stream_id != encoded.batch.stream_id
        or ack.sequence != encoded.batch.sequence
      ):
        raise RuntimeError(
          "market stream ACK does not match sent batch: "
          f"expected_stream_id={encoded.batch.stream_id} "
          f"expected_sequence={encoded.batch.sequence} "
          f"actual_type={ack.type.value} "
          f"actual_stream_id={ack.stream_id} "
          f"actual_sequence={ack.sequence}"
        )
      logger.debug(
        "QMT market batch ACK: sequence=%s bytes=%s latency_ms=%.3f",
        encoded.batch.sequence,
        len(encoded.payload),
        (time.monotonic() - started) * 1000,
      )
      self._market_stream_sequence = encoded.batch.sequence
      self._market_stream_ack_latency_ms = (time.monotonic() - started) * 1000
    finally:
      self._market_stream_pending_ack_monotonic = 0.0

  async def _send_heartbeat(self, socket, *, status: str) -> None:
    self._ensure_market_upload_state()
    market_data_ready = self._is_market_data_ready()
    trading_ready = bool(
      self._is_trading_ready() and not getattr(self, "_trading_readiness_failed", False)
    )
    self._health_state().set_xtdata_connected(market_data_ready)
    self._health_state().set_xttrading_connected(trading_ready)
    if self._requires_trading_reconciliation():
      status = "RECONCILING"
    if not market_data_ready:
      status = "XTDATA_UNAVAILABLE"
    if not trading_ready:
      status = "TRADING_UNAVAILABLE"
    if self._emergency_stop_active():
      status = "EMERGENCY_STOP"
    journal_stats = self.journal.stats()
    payload = HeartbeatPayload(
      device_id=self.configuration.device_id,
      agent_version=AGENT_VERSION,
      capabilities=self._advertised_capabilities(),
      status=status,
      xtdata_status="CONNECTED" if market_data_ready else "DISCONNECTED",
      xtdata_reason="" if market_data_ready else "XTDATA_UNAVAILABLE",
      xttrading_status=(
        "CONNECTED"
        if self.mode == "live" and trading_ready
        else "DISCONNECTED"
        if self.mode == "live"
        else "DISABLED"
      ),
      xttrading_reason=(
        ""
        if self.mode == "live" and trading_ready
        else "XTTRADING_UNAVAILABLE"
        if self.mode == "live"
        else "TRADING_DISABLED_BY_MODE"
      ),
      journal_integrity=str(journal_stats["integrity"]),
      journal_size_bytes=int(journal_stats["size_bytes"]),
      journal_pending_reports=int(journal_stats["pending_reports"]),
      journal_processing_commands=int(journal_stats["processing_commands"]),
      market_stream_status=self._market_stream_status,
      market_stream_sequence=self._market_stream_sequence,
      market_stream_queue_depth=(
        self._whole_market_capture.queue_depth + self._market_stream_outbound_depth
      ),
      market_stream_resyncs=self._market_stream_resyncs,
      market_stream_ack_latency_ms=self._market_stream_ack_latency_ms,
      history_workload=self._history_workload,
      history_workload_reason=self._history_workload_reason,
      history_progress=list(getattr(self, "_history_progress", {}).values()),
    )
    envelope = AgentEnvelope(
      message_type=AgentMessageType.HEARTBEAT,
      payload=payload.model_dump(mode="json"),
    )
    self._heartbeat_sent_monotonic[envelope.message_id] = time.monotonic()
    try:
      await self._send_socket_text(socket, envelope.model_dump_json())
    except BaseException:
      self._heartbeat_sent_monotonic.pop(envelope.message_id, None)
      raise

  async def _heartbeat_checkpoint(self, socket, *, status: str) -> None:
    async with self._heartbeat_checkpoint_lock:
      await self._send_heartbeat(socket, status=status)
    self._wake_report_sender()

  def _wake_report_sender(self) -> None:
    wakeup = getattr(self, "_report_wakeup", None)
    if wakeup is None:
      wakeup = asyncio.Event()
      self._report_wakeup = wakeup
    wakeup.set()

  async def _send_socket_text(self, socket, serialized: str) -> None:
    writer = getattr(self, "_control_socket_writer", None)
    if writer is not None and writer.socket is socket:
      await writer.send(
        serialized,
        priority=self._control_frame_priority(serialized),
      )
      return
    async with self._websocket_send_lock:
      await asyncio.wait_for(
        socket.send(serialized),
        timeout=WEBSOCKET_SEND_TIMEOUT_SECONDS,
      )

  @staticmethod
  def _control_frame_priority(serialized: str) -> int:
    try:
      message_type = str(orjson.loads(serialized).get("message_type") or "")
    except (TypeError, ValueError, orjson.JSONDecodeError):
      return 1
    if message_type in {
      AgentMessageType.AUTH.value,
      AgentMessageType.HEARTBEAT.value,
      AgentMessageType.COMMAND_ACK.value,
    }:
      return 0
    return 1

  async def _handle_message(self, socket, raw_message: str) -> None:
    self._ensure_market_upload_state()
    envelope = AgentEnvelope.model_validate_json(raw_message)
    if envelope.message_type is AgentMessageType.HEARTBEAT_ACK:
      heartbeat_message_id = str(
        envelope.payload.get("heartbeat_message_id") or ""
      )
      sent = self._heartbeat_sent_monotonic.pop(heartbeat_message_id, None)
      if sent is not None:
        # A later acknowledged heartbeat proves the path is current. One lost
        # older ACK must not leave low-priority history paused forever.
        for pending_id, pending_sent in tuple(
          self._heartbeat_sent_monotonic.items()
        ):
          if pending_sent <= sent:
            self._heartbeat_sent_monotonic.pop(pending_id, None)
        self._control_heartbeat_ack_latency_seconds = max(
          0.0,
          time.monotonic() - sent,
        )
      return
    if envelope.message_type is AgentMessageType.AUTH_RESULT:
      return
    if envelope.message_type is AgentMessageType.REPORT_ACK:
      report_message_id = str(envelope.payload.get("report_message_id", ""))
      accepted = bool(envelope.payload.get("accepted"))
      if accepted:
        if report_message_id and report_message_id not in self._report_ack_pending:
          self._report_ack_pending.add(report_message_id)
          try:
            self._report_ack_requests.put_nowait(report_message_id)
          except asyncio.QueueFull as exc:
            self._report_ack_pending.discard(report_message_id)
            raise RuntimeError("report ACK journal queue is full") from exc
      else:
        self._reports_inflight.discard(report_message_id)
        attempts = self._report_retry_attempts.get(report_message_id, 0) + 1
        self._report_retry_attempts[report_message_id] = attempts
        retry_delay = min(
          REPORT_RETRY_MAX_SECONDS,
          REPORT_RETRY_BASE_SECONDS * (2 ** min(attempts - 1, 8)),
        )
        self._report_retry_not_before[report_message_id] = (
          time.monotonic() + retry_delay
        )
        self._wake_report_sender()
      return
    if envelope.message_type in {
      AgentMessageType.COMMAND,
      AgentMessageType.CANCEL_COMMAND,
    }:
      priority = (
        0
        if envelope.message_type is AgentMessageType.CANCEL_COMMAND
        or str(envelope.payload.get("command_kind") or "").upper()
        == "EMERGENCY_STOP"
        else 1
      )
      self._command_request_sequence += 1
      try:
        self._command_requests.put_nowait(
          (priority, self._command_request_sequence, envelope)
        )
      except asyncio.QueueFull as exc:
        if socket is not None:
          await socket.close(code=1013, reason="trade command queue full")
        raise RuntimeError("trade command queue is full") from exc
      return
    if envelope.message_type is AgentMessageType.MARKET_DATA_REQUEST:
      self._ensure_market_upload_state()
      if self._fatal_market_data_error is not None:
        if socket is not None:
          await socket.close(
            code=1011,
            reason="Agent requires restart",
          )
        raise self._fatal_market_data_error
      # XTData requests may take tens of seconds. Keep the WebSocket receive
      # loop draining report acknowledgements and protocol pongs while one
      # dedicated worker performs requests serially.
      request_id = str(envelope.payload.get("request_id") or "")
      fingerprint = _market_data_payload_fingerprint(envelope.payload)
      active_upload = self._market_upload_tasks.get(request_id)
      existing_fingerprint = (
        active_upload.fingerprint
        if active_upload is not None
        else self._queued_market_data_requests.get(request_id)
      )
      if existing_fingerprint is not None:
        if existing_fingerprint != fingerprint:
          error = RuntimeError("同一 market-data request_id 的重投参数不一致")
          logger.warning(
            "Rejected conflicting QMT market-data redelivery: request_id=%s",
            request_id,
          )
          try:
            await self._report_market_data_failure(request_id, error)
          except Exception as report_exc:
            logger.warning(
              "Could not report conflicting QMT market-data redelivery: "
              "request_id=%s error=%s",
              request_id,
              report_exc.__class__.__name__,
            )
          return
        logger.info(
          "QMT market-data redelivery joined queued or active upload: request_id=%s",
          request_id,
        )
        return
      try:
        if len(self._market_upload_tasks) + len(self._queued_market_data_requests) >= (
          MAX_CACHED_MARKET_DATA_REQUESTS + MAX_QUEUED_MARKET_DATA_REQUESTS
        ):
          raise asyncio.QueueFull
        self._market_requests.put_nowait(envelope)
        self._queued_market_data_requests[request_id] = fingerprint
      except asyncio.QueueFull:
        logger.warning(
          "QMT market-data request queue is full: request_id=%s",
          request_id,
        )
        try:
          await self._report_market_data_busy(request_id)
        except Exception as report_exc:
          logger.warning(
            "Could not report QMT market-data backpressure: request_id=%s error=%s",
            request_id,
            report_exc.__class__.__name__,
          )
      return
    if envelope.message_type in {
      AgentMessageType.MARKET_RESET,
      AgentMessageType.MARKET_SUBSCRIBE,
      AgentMessageType.MARKET_UNSUBSCRIBE,
    }:
      if envelope.message_type is AgentMessageType.MARKET_RESET:
        while True:
          try:
            self._market_control_requests.get_nowait()
          except asyncio.QueueEmpty:
            break
          else:
            self._market_control_requests.task_done()
      try:
        self._market_control_requests.put_nowait(envelope)
      except asyncio.QueueFull:
        logger.warning(
          "Dropped low-priority market control under backpressure: type=%s",
          envelope.message_type.value,
        )
      return
    logger.warning("Unsupported Agent message: %s", envelope.message_type.value)

  async def _market_control_loop(self) -> None:
    if getattr(self, "broker", None) is None and getattr(
      self,
      "_broker_factory",
      None,
    ) is not None:
      await self._broker_ready.wait()
    while True:
      envelope = await self._market_control_requests.get()
      try:
        await self._handle_market_control(envelope)
      finally:
        self._market_control_requests.task_done()

  async def _handle_market_control(self, envelope: AgentEnvelope) -> None:
    if envelope.message_type is AgentMessageType.MARKET_RESET:
      await self._run_xtdata_control(
        "reset-market-subscriptions",
        self.broker.reset_market_subscriptions,
      )
      return
    if envelope.message_type is AgentMessageType.MARKET_SUBSCRIBE:
      if str(envelope.payload.get("kind") or "quote") != "quote":
        logger.warning(
          "Rejected obsolete non-quote market control: subscription_id=%s",
          envelope.payload.get("subscription_id"),
        )
        return
      try:
        accepted = await self._run_xtdata_control(
          "subscribe-market",
          self.broker.subscribe_market,
          envelope.payload,
          self._enqueue_market_event,
        )
      except _FatalMarketDataPreparationError:
        raise
      except Exception as exc:
        logger.warning(
          "XTData subscription failed without closing Agent session: "
          "subscription_id=%s error=%s",
          envelope.payload.get("subscription_id"),
          exc.__class__.__name__,
        )
        return
      if not accepted:
        logger.warning(
          "XTData subscription rejected: subscription_id=%s",
          envelope.payload.get("subscription_id"),
        )
      return
    await self._run_xtdata_control(
      "unsubscribe-market",
      self.broker.unsubscribe_market,
      str(envelope.payload.get("subscription_id") or ""),
    )

  async def _run_xtdata_control(
    self,
    operation: str,
    function,
    *args,
  ) -> Any:
    self._ensure_market_upload_state()
    if self._fatal_market_data_error is not None:
      raise self._fatal_market_data_error
    task = asyncio.create_task(
      self._run_serialized_xtdata_control(
        operation,
        function,
        args,
      ),
      name=f"xtdata-control:{operation}",
    )
    task.add_done_callback(self._consume_xtdata_control_result)
    return await asyncio.shield(task)

  async def _run_serialized_xtdata_control(
    self,
    operation: str,
    function,
    args: tuple[Any, ...],
  ) -> Any:
    async with self._xtdata_access_lock:
      if self._fatal_market_data_error is not None:
        raise self._fatal_market_data_error
      return await self._run_xtdata_control_daemon(
        operation,
        function,
        args,
      )

  async def _run_xtdata_control_daemon(
    self,
    operation: str,
    function,
    args: tuple[Any, ...],
  ) -> Any:
    loop = asyncio.get_running_loop()
    outcome = loop.create_future()
    abandoned = threading.Event()

    def deliver_result(result: Any) -> None:
      if not outcome.done() and not abandoned.is_set():
        outcome.set_result(result)

    def deliver_error(exc: BaseException) -> None:
      if not outcome.done():
        outcome.set_exception(exc)

    def worker() -> None:
      try:
        result = function(*args)
      except BaseException as exc:
        try:
          loop.call_soon_threadsafe(deliver_error, exc)
        except RuntimeError:
          pass
        return
      if abandoned.is_set():
        return
      try:
        loop.call_soon_threadsafe(deliver_result, result)
      except RuntimeError:
        pass

    threading.Thread(
      target=worker,
      name=f"qmt-xtdata:{operation[:32]}",
      daemon=True,
    ).start()
    try:
      return await asyncio.wait_for(
        asyncio.shield(outcome),
        timeout=XTDATA_CONTROL_TIMEOUT_SECONDS,
      )
    except asyncio.TimeoutError as exc:
      abandoned.set()
      outcome.cancel()
      fatal = _FatalMarketDataPreparationError(
        f"XTData {operation} timed out; Agent restart required"
      )
      self._trip_market_data_fatal(fatal)
      raise fatal from exc

  @staticmethod
  def _consume_xtdata_control_result(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
      task.exception()

  async def _command_request_loop(self, socket) -> None:
    """Submit bounded command lanes to the sole native priority worker."""

    if getattr(self, "broker", None) is None and getattr(
      self,
      "_broker_factory",
      None,
    ) is not None:
      await self._broker_ready.wait()
    active: dict[asyncio.Task[None], int] = {}
    getters: dict[asyncio.Task[tuple[int, int, AgentEnvelope]], int] = {}
    try:
      while True:
        active_high = sum(priority == 0 for priority in active.values())
        active_normal = len(active) - active_high
        if (
          active_high < MAX_ACTIVE_PRIORITY_TRADE_COMMANDS
          and 0 not in getters.values()
        ):
          task = asyncio.create_task(
            self._command_requests.get_high(),
            name="qmt-command-priority-dequeue",
          )
          getters[task] = 0
        if (
          active_normal < MAX_ACTIVE_NORMAL_TRADE_COMMANDS
          and 1 not in getters.values()
        ):
          task = asyncio.create_task(
            self._command_requests.get_normal(),
            name="qmt-command-normal-dequeue",
          )
          getters[task] = 1

        done, _ = await asyncio.wait(
          {*active, *getters},
          return_when=asyncio.FIRST_COMPLETED,
        )
        completed_commands = [task for task in done if task in active]
        for task in completed_commands:
          active.pop(task)
          await task

        for getter in [task for task in done if task in getters]:
          lane_priority = getters.pop(getter)
          priority, _, envelope = getter.result()
          command = asyncio.create_task(
            self._dispatch_command(socket, envelope, priority=priority),
            name=(
              "qmt-command-priority"
              if lane_priority == 0
              else "qmt-command-normal"
            ),
          )
          active[command] = priority
    finally:
      for task in (*getters, *active):
        if not task.done():
          task.cancel()
      await asyncio.gather(*getters, *active, return_exceptions=True)

  async def _dispatch_command(
    self,
    socket,
    envelope: AgentEnvelope,
    *,
    priority: int,
  ) -> None:
    self._active_command_count += 1
    try:
      await self._handle_command(socket, envelope)
    finally:
      self._active_command_count = max(0, self._active_command_count - 1)
      self._command_requests.task_done(priority)

  async def _command_ack(
    self,
    socket,
    envelope: AgentEnvelope,
    *,
    accepted: bool,
    reason: str,
  ) -> None:
    await self._send_socket_text(
      socket,
      AgentEnvelope(
        message_type=AgentMessageType.COMMAND_ACK,
        payload={
          "command_message_id": envelope.message_id,
          "client_order_id": envelope.payload.get("client_order_id"),
          "accepted": accepted,
          "reason": reason,
        },
      ).model_dump_json(),
    )

  @staticmethod
  def _command_rejection_result(
    payload: dict[str, Any],
    *,
    reason: str,
    cancel: bool,
  ) -> dict[str, Any]:
    error_key = "cancel_errors" if cancel else "order_errors"
    return {
      "accepted": False,
      "reason": reason,
      "reports": [
        (
          AgentMessageType.DELTA_REPORT.value,
          {
            error_key: [
              {
                "client_order_id": payload.get("client_order_id"),
                "account_id": str(payload.get("account_id") or ""),
                "reason": reason,
                "error_msg": reason,
              }
            ],
            "sequence": int(datetime.now(timezone.utc).timestamp() * 1_000_000),
            "is_complete": False,
          },
        )
      ],
    }

  async def _handle_command(self, socket, envelope: AgentEnvelope) -> None:
    payload = envelope.payload
    emergency_command = (
      str(payload.get("command_kind") or "").upper() == "EMERGENCY_STOP"
    )
    command_priority = (
      JOURNAL_PRIORITY_CANCEL
      if envelope.message_type is AgentMessageType.CANCEL_COMMAND
      or emergency_command
      else JOURNAL_PRIORITY_COMMAND
    )
    state, previous = await self._run_journal_call(
      self.journal.begin_command,
      envelope.message_id,
      payload,
      priority=command_priority,
    )
    if state == "MISMATCH":
      await self._command_ack(
        socket,
        envelope,
        accepted=False,
        reason="message_id_payload_mismatch",
      )
      return
    if state == "INDETERMINATE":
      await self._command_ack(
        socket,
        envelope,
        accepted=False,
        reason="command_processing",
      )
      return
    if state == "DUPLICATE":
      await self._command_ack(
        socket,
        envelope,
        accepted=bool((previous or {}).get("accepted")),
        reason=str((previous or {}).get("reason", "")),
      )
      await self._flush_reports(socket)
      return

    account_id = str(payload.get("account_id", ""))
    command_kind = str(payload.get("command_kind") or "").upper()
    emergency_command = command_kind == "EMERGENCY_STOP"
    rejection = ""
    try:
      if emergency_command:
        if (
          not payload.get("client_order_id")
          or not payload.get("expires_at")
          or not str(payload.get("reason") or "").strip()
        ):
          raise ValueError("invalid emergency command")
      elif envelope.message_type is AgentMessageType.CANCEL_COMMAND:
        CancelCommandPayload.model_validate(payload)
      else:
        TradeCommandPayload.model_validate(payload)
      if _parse_expiry(payload.get("expires_at")) <= datetime.now(timezone.utc):
        rejection = "command_expired"
      elif account_id not in self.allowed_accounts:
        rejection = "account_not_whitelisted"
      elif self.mode == "data-only":
        rejection = "data_only_agent"
      elif (
        not emergency_command
        and str(payload.get("execution_mode") or "").lower() != self.mode
      ):
        rejection = "execution_mode_mismatch"
      elif (
        not emergency_command
        and envelope.message_type is not AgentMessageType.CANCEL_COMMAND
        and self._requires_trading_reconciliation()
      ):
        rejection = "local_reconciliation_required"
      elif (
        self.mode == "live"
        and not emergency_command
        and envelope.message_type is not AgentMessageType.CANCEL_COMMAND
        and str(payload.get("side") or "").upper() != "SELL"
        and self._market_stream_status != "READY"
      ):
        rejection = "market_stream_not_ready"
      elif (
        envelope.message_type is not AgentMessageType.CANCEL_COMMAND
        and self._emergency_stop_active()
      ):
        rejection = "local_emergency_stop"
    except ValidationError:
      rejection = "invalid_command_payload"
    except (TypeError, ValueError):
      rejection = "invalid_command_expiry"

    if rejection:
      result = self._command_rejection_result(
        payload,
        reason=rejection,
        cancel=envelope.message_type is AgentMessageType.CANCEL_COMMAND,
      )
    elif emergency_command:
      if self.emergency_stop is None:
        result = {
          "accepted": False,
          "reason": "emergency_store_unavailable",
          "reports": [],
        }
      else:
        self._emergency_stop_status_cache = await self._run_journal_call(
          self.emergency_stop.activate,
          str(payload.get("reason") or ""),
          priority=JOURNAL_PRIORITY_CANCEL,
        )
        result = {
          "accepted": True,
          "reason": "local_emergency_stop_activated",
          "reports": [],
        }
    else:
      def execute_if_current() -> dict[str, Any]:
        if _parse_expiry(payload.get("expires_at")) <= datetime.now(timezone.utc):
          return self._command_rejection_result(
            payload,
            reason="command_expired",
            cancel=envelope.message_type is AgentMessageType.CANCEL_COMMAND,
          )
        return self.broker.execute(payload)

      result = (
        await self._run_native_xttrading(
          "execute-command",
          execute_if_current,
          timeout=XTTRADING_RECONNECT_TIMEOUT_SECONDS,
          priority=(
            XTTRADING_PRIORITY_CANCEL
            if envelope.message_type is AgentMessageType.CANCEL_COMMAND
            else XTTRADING_PRIORITY_ORDER
          ),
        )
        if self.mode == "live"
        else await asyncio.to_thread(self.broker.execute, payload)
      )
      if (
        self.mode == "live"
        and str(result.get("reason") or "") == "local_reconciliation_required"
      ):
        self._trading_connection_generation_cache = (
          self._read_broker_trading_generation()
        )
        self._begin_trading_reconciliation("broker_rejected_unreconciled_generation")
    reports: list[tuple[str, str]] = []
    for message_type, report_payload in result.get("reports") or []:
      report = AgentEnvelope(
        message_type=AgentMessageType(message_type),
        payload=enrich_report_payload(
          AgentMessageType(message_type),
          report_payload,
        ),
      )
      reports.append((report.message_id, report.model_dump_json()))
    await self._run_journal_call(
      self._persist_command_outcome,
      envelope.message_id,
      result,
      reports,
      priority=command_priority,
    )
    if self.mode == "live":
      self._refresh_journal_reconciliation_gate()
    await self._command_ack(
      socket,
      envelope,
      accepted=bool(result.get("accepted")),
      reason=str(result.get("reason", "")),
    )
    await self._flush_reports(socket)

  def _persist_command_outcome(
    self,
    message_id: str,
    result: dict[str, Any],
    reports: list[tuple[str, str]],
  ) -> None:
    self.journal.complete_command(message_id, result)
    for report_message_id, serialized in reports:
      self.journal.add_report(report_message_id, serialized)

  async def _handle_market_data_request(self, envelope: AgentEnvelope) -> None:
    self._ensure_market_upload_state()
    async with self._history_request_slots:
      await self._handle_market_data_request_in_slot(envelope)

  async def _handle_market_data_request_in_slot(
    self,
    envelope: AgentEnvelope,
  ) -> None:
    request_id = str(envelope.payload["request_id"])
    try:
      chunks = await self._prepared_market_data_chunks(
        request_id,
        envelope.payload,
      )
    except _MarketDataRequestAlreadyCompleted:
      # The server derives completion from its durable chunks. A duplicate
      # delivery after every PUT succeeded needs neither XTData nor another PUT.
      return
    if request_id in self._streamed_market_uploads:
      self._streamed_market_uploads.discard(request_id)
      self._provisional_market_uploads.discard(request_id)
      await self._complete_market_upload(request_id)
      return
    client = self._market_data_upload_client()
    for index, chunk in enumerate(chunks):
      self._touch_market_upload(request_id)
      await self._put_market_data_chunk(
        client,
        request_id=request_id,
        chunk_index=index,
        chunk=chunk,
        total_chunks=len(chunks),
      )
      self._touch_market_upload(request_id)
    if request_id in self._provisional_market_uploads:
      await self._finalize_market_data_upload(
        request_id,
        len(chunks),
        client=client,
      )
      self._provisional_market_uploads.discard(request_id)
    await self._complete_market_upload(request_id)

  def _market_data_upload_client(self) -> httpx.AsyncClient:
    self._ensure_market_upload_state()
    client = self._market_data_http_client
    if client is None:
      client = httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=False,
        trust_env=False,
        verify=httpx_verify(self.configuration.api_url),
        limits=httpx.Limits(
          max_connections=MAX_CONCURRENT_HISTORY_UPLOADS,
          max_keepalive_connections=MAX_CONCURRENT_HISTORY_UPLOADS,
        ),
      )
      self._market_data_http_client = client
    return client

  async def _close_market_data_upload_client(self) -> None:
    client = getattr(self, "_market_data_http_client", None)
    self._market_data_http_client = None
    if client is None:
      return
    close = getattr(client, "aclose", None)
    if close is not None:
      await close()

  async def _put_market_data_chunk(
    self,
    client: httpx.AsyncClient,
    *,
    request_id: str,
    chunk_index: int,
    chunk: _MarketDataSpoolChunk,
    total_chunks: int,
  ) -> None:
    self._ensure_market_upload_state()
    async with self._history_upload_slots:
      response = await client.put(
        (
          f"{self.configuration.api_url}/agent/market-data/"
          f"{request_id}/chunks/{chunk_index}"
        ),
        content=_stream_spool_chunk(
          chunk.path,
          limiter=self._history_upload_limiter,
          executor=self._history_upload_io_executor,
        ),
        headers={
          "Authorization": f"Bearer {self._access_token}",
          "Content-Type": "application/json",
          "Content-Encoding": "gzip",
          "Content-Length": str(chunk.compressed_bytes),
          "X-Content-SHA256": chunk.digest,
          "X-Record-Count": str(chunk.record_count),
          "X-Total-Chunks": str(total_chunks),
        },
      )
    try:
      response.raise_for_status()
    except httpx.HTTPStatusError as exc:
      if exc.response.status_code == 409:
        raise _IsolatedMarketDataWorkerError(
          "MARKET_DATA_UPLOAD_CONFLICT"
        ) from exc
      raise

    if not hasattr(self, "_history_uploaded_chunks"):
      self._history_uploaded_chunks = {}
    uploaded = self._history_uploaded_chunks.setdefault(request_id, {})
    uploaded[chunk_index] = chunk.compressed_bytes
    self._set_history_progress(request_id, uploaded_bytes=sum(uploaded.values()))

  async def _upload_provisional_market_data_chunk(
    self,
    client: httpx.AsyncClient,
    request_id: str,
    chunk_index: int,
    chunk: _MarketDataSpoolChunk,
  ) -> None:
    await self._put_market_data_chunk(
      client,
      request_id=request_id,
      chunk_index=chunk_index,
      chunk=chunk,
      total_chunks=0,
    )

  async def _finalize_market_data_upload(
    self,
    request_id: str,
    total_chunks: int,
    *,
    client: httpx.AsyncClient | None = None,
  ) -> None:
    if client is None:
      await self._finalize_market_data_upload(
        request_id,
        total_chunks,
        client=self._market_data_upload_client(),
      )
      return
    self._ensure_market_upload_state()
    async with self._history_upload_slots:
      response = await client.post(
        f"{self.configuration.api_url}/agent/market-data/{request_id}/complete",
        headers={
          "Authorization": f"Bearer {self._access_token}",
          "X-Total-Chunks": str(total_chunks),
        },
        timeout=10.0,
      )
    try:
      response.raise_for_status()
    except httpx.HTTPStatusError as exc:
      if exc.response.status_code == 409:
        raise _IsolatedMarketDataWorkerError(
          "MARKET_DATA_UPLOAD_CONFLICT"
        ) from exc
      raise

    getattr(self, "_history_progress", {}).pop(request_id, None)
    getattr(self, "_history_uploaded_chunks", {}).pop(request_id, None)

  async def _prepared_market_data_chunks(
    self,
    request_id: str,
    payload: dict[str, Any],
  ) -> tuple[_MarketDataSpoolChunk, ...]:
    self._ensure_market_upload_state()
    if self._fatal_market_data_error is not None:
      raise self._fatal_market_data_error
    if self._market_spool_cleanup_pending:
      async with self._market_spool_cleanup_lock:
        if self._market_spool_cleanup_pending:
          protected_directory_names = frozenset(
            _market_data_spool_request_directory(
              self._market_spool_root,
              cached_request_id,
            ).name
            for cached_request_id in self._market_upload_cache
          )
          self._market_spool_cleanup_pending = await asyncio.to_thread(
            _sweep_market_data_spool_cleanup,
            self._market_spool_root,
            protected_directory_names=protected_directory_names,
          )
      if self._market_spool_cleanup_pending:
        raise _MarketDataSpoolCleanupPending(
          "market-data spool cleanup is pending"
        )
    payload_fingerprint = _market_data_payload_fingerprint(payload)
    now = self._market_upload_clock()
    expired = self._cleanup_expired_market_uploads(
      now,
      remove_prepared=False,
    )
    if expired:
      await asyncio.gather(
        *(
          asyncio.to_thread(self._remove_prepared_market_data, prepared)
          for prepared in expired
        )
      )

    tombstone = self._market_upload_tombstones.get(request_id)
    if tombstone is not None:
      tombstone.last_access_at = now
      if tombstone.fingerprint != payload_fingerprint:
        raise RuntimeError("同一 market-data request_id 的重投参数不一致")
      raise _MarketDataRequestAlreadyCompleted(
        "market-data request was already uploaded"
      )

    cached = self._market_upload_cache.get(request_id)
    if cached is not None:
      cached.last_access_at = now
      if cached.fingerprint != payload_fingerprint:
        raise RuntimeError("同一 market-data request_id 的重投参数不一致")
      task = cached.task
      if task is None:
        raise RuntimeError("market-data preparation state is incomplete")
      return (await asyncio.shield(task)).chunks

    spool_directory = _market_data_spool_request_directory(
      self._market_spool_root,
      request_id,
    )
    if spool_directory.exists():
      try:
        recovered, _, _, _ = await asyncio.to_thread(
          _read_market_data_spool_manifest,
          spool_directory,
          expected_request_id=request_id,
          expected_fingerprint=payload_fingerprint,
        )
      except RuntimeError as exc:
        if "重投参数不一致" in str(exc):
          raise
        logger.warning(
          "Discarded unusable market-data recovery spool: request_id=%s error=%s",
          request_id,
          exc.__class__.__name__,
        )
        await asyncio.to_thread(shutil.rmtree, spool_directory, True)
      else:
        next_cache_bytes = self._market_upload_cache_bytes + (
          recovered.compressed_bytes
        )
        if next_cache_bytes > MAX_MARKET_DATA_UPLOAD_CACHE_BYTES:
          raise RuntimeError("market-data upload cache byte limit exceeded")
        entry = _MarketUploadCacheEntry(
          payload_fingerprint,
          created_at=now,
          last_access_at=now,
          compressed_bytes=recovered.compressed_bytes,
        )

        async def recovered_result() -> _PreparedMarketData:
          return recovered

        task = asyncio.create_task(
          recovered_result(),
          name=f"market-data-recovered:{request_id}",
        )
        entry.task = task
        self._market_upload_cache[request_id] = entry
        self._market_upload_cache_bytes = next_cache_bytes
        task.add_done_callback(self._consume_market_preparation_result)
        logger.info(
          "Recovered durable market-data spool: request_id=%s chunks=%s",
          request_id,
          len(recovered.chunks),
        )
        return (await asyncio.shield(task)).chunks

    if len(self._market_upload_cache) >= MAX_CACHED_MARKET_DATA_REQUESTS:
      raise RuntimeError("market-data upload cache request limit exceeded")

    entry = _MarketUploadCacheEntry(
      payload_fingerprint,
      created_at=now,
      last_access_at=now,
    )
    self._market_upload_cache[request_id] = entry
    task = asyncio.create_task(
      self._prepare_and_cache_market_data(
        request_id,
        entry,
        dict(payload),
      ),
      name=f"market-data-prepare:{request_id}",
    )
    entry.task = task
    task.add_done_callback(self._consume_market_preparation_result)
    return (await asyncio.shield(task)).chunks

  def _set_history_progress(self, request_id: str, **changes: Any) -> None:
    if not hasattr(self, "_history_progress"):
      self._history_progress = {}
    previous = self._history_progress.get(request_id, {
      "request_id": request_id, "operation": "unknown", "stage": "queued", "completed_units": 0,
      "total_units": 0, "uploaded_bytes": 0,
    })
    self._history_progress[request_id] = {**previous, **changes}

  async def _prepare_and_cache_market_data(
    self,
    request_id: str,
    entry: _MarketUploadCacheEntry,
    payload: dict[str, Any],
  ) -> _PreparedMarketData:
    # A session cancellation must never cancel this task. Historical work owns
    # a separate spawned XTData client, so this lock serializes only that child
    # and never blocks the parent process's real-time XTData control path.
    prepared: _PreparedMarketData | None = None
    self._set_history_progress(request_id, stage="queued", operation=str(payload.get("operation") or "bars")[:32])
    try:
      managed_spool_bytes = await asyncio.to_thread(
        _managed_market_data_spool_bytes,
        self._market_spool_root,
      )
      remaining_cache_bytes = MAX_MARKET_DATA_UPLOAD_CACHE_BYTES - managed_spool_bytes
      if remaining_cache_bytes <= 0:
        raise RuntimeError("market-data upload cache byte limit exceeded")
      compressed_budget = min(
        MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES,
        remaining_cache_bytes,
      )
      try:
        prepared = await self._run_market_data_preparation_daemon(
          request_id,
          payload,
          max_total_uncompressed_bytes=(MAX_MARKET_DATA_REQUEST_UNCOMPRESSED_BYTES),
          max_total_compressed_bytes=compressed_budget,
          max_spool_bytes=remaining_cache_bytes,
        )
      except ValueError as exc:
        if (
          "spool disk byte limit" in str(exc)
          or "spool byte limit" in str(exc)
        ):
          raise RuntimeError(
            "market-data upload cache byte limit exceeded"
          ) from exc
        if (
          compressed_budget == remaining_cache_bytes
          and remaining_cache_bytes < MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES
          and "compressed byte limit" in str(exc)
        ):
          raise RuntimeError("market-data upload cache byte limit exceeded") from exc
        raise
      await asyncio.to_thread(
        _write_market_data_spool_manifest,
        prepared,
        request_id=request_id,
        fingerprint=entry.fingerprint,
      )

      cached = self._market_upload_cache.get(request_id)
      if cached is not entry:
        await asyncio.to_thread(self._remove_prepared_market_data, prepared)
        raise RuntimeError("market-data preparation was retired")
      next_cache_bytes = self._market_upload_cache_bytes + prepared.compressed_bytes
      if next_cache_bytes > MAX_MARKET_DATA_UPLOAD_CACHE_BYTES:
        await asyncio.to_thread(self._remove_prepared_market_data, prepared)
        raise RuntimeError("market-data upload cache byte limit exceeded")
      entry.compressed_bytes = prepared.compressed_bytes
      entry.last_access_at = self._market_upload_clock()
      self._market_upload_cache_bytes = next_cache_bytes
      return prepared
    except BaseException:
      self._history_progress.pop(request_id, None)
      if self._market_upload_cache.get(request_id) is entry:
        self._drop_market_upload_cache_entry(request_id)
      if prepared is not None and prepared.spool_directory.exists():
        await asyncio.to_thread(self._remove_prepared_market_data, prepared)
      raise

  def _market_data_preparation_lock(self) -> asyncio.Lock:
    worker_kind_reader = getattr(
      self.broker,
      "historical_market_data_worker_kind",
      None,
    )
    worker_kind = worker_kind_reader() if callable(worker_kind_reader) else None
    return (
      self._historical_worker_lock
      if worker_kind is not None
      else self._xtdata_access_lock
    )

  async def _run_market_data_preparation_daemon(
    self,
    request_id: str,
    payload: dict[str, Any],
    *,
    max_total_uncompressed_bytes: int,
    max_total_compressed_bytes: int,
    max_spool_bytes: int = MAX_MARKET_DATA_UPLOAD_CACHE_BYTES,
  ) -> _PreparedMarketData:
    """Run historical preparation outside the control Agent when supported."""

    worker_kind_reader = getattr(
      self.broker,
      "historical_market_data_worker_kind",
      None,
    )
    worker_kind = worker_kind_reader() if callable(worker_kind_reader) else None
    if worker_kind is not None:
      # The limit belongs to the immutable server request, not to each native
      # unit produced by the isolated worker. Keep this pure validation in the
      # parent so an oversized request cannot start XTData or upload a partial
      # transfer before being rejected. Injected fallback brokers retain their
      # deliberately narrow test schemas and never split the request.
      validate_market_data_request(payload)
      return await self._run_isolated_market_data_preparation(
        request_id,
        payload,
        worker_kind=str(worker_kind),
        max_total_uncompressed_bytes=max_total_uncompressed_bytes,
        max_total_compressed_bytes=max_total_compressed_bytes,
        max_spool_bytes=max_spool_bytes,
      )
    async with self._market_data_preparation_lock():
      return await self._run_market_data_preparation_thread(
        request_id,
        payload,
        max_total_uncompressed_bytes=max_total_uncompressed_bytes,
        max_total_compressed_bytes=max_total_compressed_bytes,
      )

  async def _run_isolated_market_data_preparation(self, request_id, payload, **kwargs):
    async with self._market_data_preparation_lock():
      used = await asyncio.to_thread(_managed_market_data_spool_bytes, self._market_spool_root)
      available = MAX_MARKET_DATA_UPLOAD_CACHE_BYTES - used
      if available <= 0:
        raise RuntimeError("market-data upload cache byte limit exceeded")
      kwargs["max_spool_bytes"] = min(kwargs.get("max_spool_bytes", available), available)
      kwargs["max_total_compressed_bytes"] = min(kwargs["max_total_compressed_bytes"], available)
      return await self._run_isolated_market_data_preparation_locked(request_id, payload, **kwargs)

  async def _run_isolated_market_data_preparation_locked(
    self,
    request_id: str,
    payload: dict[str, Any],
    *,
    worker_kind: str,
    max_total_uncompressed_bytes: int,
    max_total_compressed_bytes: int,
    max_spool_bytes: int = MAX_MARKET_DATA_UPLOAD_CACHE_BYTES,
  ) -> _PreparedMarketData:
    if worker_kind != XTDATA_HISTORICAL_WORKER_KIND:
      raise _IsolatedMarketDataWorkerError(
        "MARKET_DATA_PREPARATION_WORKER_UNSUPPORTED"
      )
    await self._wait_for_history_dispatch()
    self._set_history_progress(request_id, stage="downloading")
    spool_directory = await asyncio.to_thread(
      _reset_market_data_spool_directory,
      self._market_spool_root,
      request_id,
    )
    self._history_workload = "running"
    self._history_workload_reason = ""
    provisional_uploads_enabled = bool(getattr(self, "_access_token", ""))
    upload_client: httpx.AsyncClient | None = None
    upload_tasks: set[asyncio.Task[None]] = set()
    uploaded_chunk_indices: set[int] = set()
    provisional_upload_failed = False

    async def observe_uploads(
      tasks: set[asyncio.Task[None]],
    ) -> bool:
      failed = False
      for task in tasks:
        try:
          await task
        except _FatalMarketDataPreparationError:
          raise
        except Exception as exc:
          failed = True
          logger.warning(
            "Provisional market-data upload will fall back after preparation: "
            "request_id=%s error=%s",
            request_id,
            exc.__class__.__name__,
          )
      return failed

    try:
      await asyncio.to_thread(
        self._ensure_historical_worker_sync,
        worker_kind,
      )
      connection = self._historical_worker_connection
      process = self._historical_worker_process
      if connection is None or process is None:
        raise _IsolatedMarketDataWorkerError(
          "MARKET_DATA_PREPARATION_START_FAILED"
        )
      await asyncio.to_thread(
        connection.send,
        {
          "type": "prepare",
          "request_id": request_id,
          "payload": payload,
          "spool_directory": str(spool_directory),
          "max_total_uncompressed_bytes": max_total_uncompressed_bytes,
          "max_total_compressed_bytes": max_total_compressed_bytes,
          "max_spool_bytes": max_spool_bytes,
        },
      )
      if provisional_uploads_enabled:
        upload_client = self._market_data_upload_client()
    except Exception as exc:
      await asyncio.to_thread(shutil.rmtree, spool_directory, True)
      self._history_workload = "idle"
      self._history_workload_reason = ""
      if isinstance(exc, _FatalMarketDataPreparationError):
        self._trip_market_data_fatal(exc)
        raise
      await self._shutdown_historical_worker(graceful=False)
      if isinstance(exc, _IsolatedMarketDataWorkerError):
        raise
      raise _IsolatedMarketDataWorkerError(
        "MARKET_DATA_PREPARATION_START_FAILED"
      ) from exc
    message_type = None
    try:
      while True:
        message = await self._receive_historical_worker_message(
          connection,
          process,
          request_id=request_id,
        )
        message_type = message.get("type") if isinstance(message, dict) else None
        message_request_id = (
          str(message.get("request_id") or "")
          if isinstance(message, dict)
          else ""
        )
        if message_request_id != request_id:
          raise _IsolatedMarketDataWorkerError(
            "MARKET_DATA_PREPARATION_PROTOCOL_ERROR"
          )
        if message_type == "chunk":
          chunk_index, chunk = _decode_isolated_spool_chunk(
            message,
            request_id=request_id,
            spool_directory=spool_directory,
          )
          if chunk_index in uploaded_chunk_indices:
            raise _IsolatedMarketDataWorkerError(
              "MARKET_DATA_PREPARATION_PROTOCOL_ERROR"
            )
          uploaded_chunk_indices.add(chunk_index)
          self._set_history_progress(request_id, stage="encoding")
          if provisional_uploads_enabled:
            if upload_client is None:
              raise _IsolatedMarketDataWorkerError(
                "MARKET_DATA_PREPARATION_PROTOCOL_ERROR"
              )
            self._provisional_market_uploads.add(request_id)
            while len(upload_tasks) >= MAX_CONCURRENT_HISTORY_UPLOADS:
              done, upload_tasks = await asyncio.wait(
                upload_tasks,
                return_when=asyncio.FIRST_COMPLETED,
              )
              provisional_upload_failed = (
                await observe_uploads(done) or provisional_upload_failed
              )
            upload_tasks.add(
              asyncio.create_task(
                self._upload_provisional_market_data_chunk(
                  upload_client,
                  request_id,
                  chunk_index,
                  chunk,
                ),
                name=f"market-data-provisional-upload:{request_id}:{chunk_index}",
              )
            )
          continue
        if message_type == "started":
          total_units = int(message.get("total_units") or 0)
          if total_units <= 0:
            raise _IsolatedMarketDataWorkerError(
              "MARKET_DATA_PREPARATION_PROTOCOL_ERROR"
            )
          self._set_history_progress(request_id, stage="downloading", total_units=total_units)
          continue
        if message_type == "checkpoint":
          completed_units = int(message.get("completed_units") or 0)
          total_units = int(message.get("total_units") or 0)
          if completed_units <= 0 or completed_units >= total_units:
            raise _IsolatedMarketDataWorkerError(
              "MARKET_DATA_PREPARATION_PROTOCOL_ERROR"
            )
          self._set_history_progress(request_id, stage="queued", completed_units=completed_units)
          lock = self._market_data_preparation_lock()
          lock.release()
          try:
            await lock.acquire()
          except BaseException:
            # The enclosing async-with must still own its lock on exit.
            await asyncio.shield(lock.acquire())
            raise
          if self._historical_worker_process is not process or not process.is_alive():
            raise _IsolatedMarketDataWorkerError("MARKET_DATA_PREPARATION_CRASH")
          await self._wait_for_history_dispatch()
          self._set_history_progress(request_id, stage="downloading")
          used = await asyncio.to_thread(_managed_market_data_spool_bytes, self._market_spool_root)
          await asyncio.to_thread(
            connection.send,
            {"type": "continue", "request_id": request_id,
             "max_spool_bytes": max(0, MAX_MARKET_DATA_UPLOAD_CACHE_BYTES - used)},
          )
          continue
        prepared = _decode_isolated_market_data_result(
          message,
          request_id=request_id,
          spool_directory=spool_directory,
        )
        progress = self._history_progress.get(request_id, {})
        self._set_history_progress(request_id, stage="uploading", completed_units=progress.get("total_units", 0))
        if upload_tasks:
          provisional_upload_failed = (
            await observe_uploads(upload_tasks) or provisional_upload_failed
          )
          upload_tasks.clear()
        if uploaded_chunk_indices and uploaded_chunk_indices != set(
          range(len(prepared.chunks))
        ):
          raise _IsolatedMarketDataWorkerError(
            "MARKET_DATA_PREPARATION_PROTOCOL_ERROR"
          )
        if (
          provisional_uploads_enabled
          and uploaded_chunk_indices
          and not provisional_upload_failed
        ):
          try:
            await self._finalize_market_data_upload(
              request_id,
              len(prepared.chunks),
              client=upload_client,
            )
          except _FatalMarketDataPreparationError:
            raise
          except Exception as exc:
            logger.warning(
              "Provisional market-data manifest finalize will fall back: "
              "request_id=%s error=%s",
              request_id,
              exc.__class__.__name__,
            )
          else:
            self._streamed_market_uploads.add(request_id)
        self._history_workload = "idle"
        self._history_workload_reason = ""
        return prepared
    except asyncio.CancelledError:
      self._streamed_market_uploads.discard(request_id)
      self._provisional_market_uploads.discard(request_id)
      for task in upload_tasks:
        task.cancel()
      if upload_tasks:
        await asyncio.gather(*upload_tasks, return_exceptions=True)
      await asyncio.shield(self._shutdown_historical_worker(graceful=False))
      await asyncio.to_thread(shutil.rmtree, spool_directory, True)
      raise
    except _FatalMarketDataPreparationError:
      self._streamed_market_uploads.discard(request_id)
      self._provisional_market_uploads.discard(request_id)
      for task in upload_tasks:
        task.cancel()
      if upload_tasks:
        await asyncio.gather(*upload_tasks, return_exceptions=True)
      await asyncio.to_thread(shutil.rmtree, spool_directory, True)
      raise
    except Exception:
      self._streamed_market_uploads.discard(request_id)
      self._provisional_market_uploads.discard(request_id)
      for task in upload_tasks:
        task.cancel()
      if upload_tasks:
        await asyncio.gather(*upload_tasks, return_exceptions=True)
      if message_type not in {"ok", "error"} and self._historical_worker_process is process:
        await self._shutdown_historical_worker(graceful=False)
      await asyncio.to_thread(shutil.rmtree, spool_directory, True)
      raise
    finally:
      if self._history_workload != "idle":
        self._history_workload = "idle"
        self._history_workload_reason = ""

  def _ensure_historical_worker_sync(self, worker_kind: str) -> None:
    process = self._historical_worker_process
    connection = self._historical_worker_connection
    if (
      process is not None
      and connection is not None
      and process.is_alive()
      and self._historical_worker_kind == worker_kind
    ):
      return
    self._shutdown_historical_worker_sync(graceful=False)
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=True)
    process = context.Process(
      target=run_historical_market_data_worker,
      args=(child_connection, worker_kind),
      name="qmt-xtdata-history",
      daemon=True,
    )
    try:
      process.start()
    except Exception:
      parent_connection.close()
      child_connection.close()
      raise
    child_connection.close()
    self._historical_worker_process = process
    self._historical_worker_connection = parent_connection
    self._historical_worker_kind = worker_kind

  async def _receive_historical_worker_message(
    self,
    connection: Any,
    process: Any,
    *,
    request_id: str,
  ) -> Any:
    del request_id

    def poll_and_receive() -> Any:
      poll = getattr(connection, "poll", None)
      if callable(poll) and not poll(HISTORICAL_WORK_UNIT_TIMEOUT_SECONDS):
        raise TimeoutError("historical worker response timed out")
      return connection.recv()

    try:
      return await asyncio.get_running_loop().run_in_executor(
        self._historical_ipc_executor,
        poll_and_receive,
      )
    except TimeoutError as exc:
      await self._shutdown_historical_worker(graceful=False)
      raise _IsolatedMarketDataWorkerError(
        "MARKET_DATA_PREPARATION_TIMEOUT"
      ) from exc
    except (EOFError, OSError) as exc:
      await self._shutdown_historical_worker(graceful=False)
      raise _IsolatedMarketDataWorkerError(
        "MARKET_DATA_PREPARATION_CRASH"
      ) from exc

  async def _shutdown_historical_worker(self, *, graceful: bool = True) -> None:
    try:
      await asyncio.to_thread(
        self._shutdown_historical_worker_sync,
        graceful=graceful,
      )
    except _FatalMarketDataPreparationError as error:
      self._trip_market_data_fatal(error)
      raise

  def _shutdown_historical_worker_sync(self, *, graceful: bool = True) -> None:
    process = getattr(self, "_historical_worker_process", None)
    connection = getattr(self, "_historical_worker_connection", None)
    if connection is not None:
      if graceful and process is not None and process.is_alive():
        try:
          connection.send({"type": "shutdown"})
        except (BrokenPipeError, EOFError, OSError):
          pass
      try:
        connection.close()
      except OSError:
        pass
    if process is None:
      self._historical_worker_process = None
      self._historical_worker_connection = None
      self._historical_worker_kind = ""
      return
    if graceful:
      process.join(5.0)
    if process.is_alive():
      _terminate_market_data_process(process)
    else:
      process.join()
    if process.is_alive():
      # Never clear this reference or spawn a second native XTData caller while
      # the old process may still own MiniQMT resources.
      raise _FatalMarketDataPreparationError(
        "historical XTData worker could not be terminated; Agent restart required"
      )
    self._historical_worker_process = None
    self._historical_worker_connection = None
    self._historical_worker_kind = ""
    close_process = getattr(process, "close", None)
    if callable(close_process):
      close_process()

  def _history_qos_block_reason(self) -> str:
    if not getattr(self, "_control_session_authenticated", False):
      return "CONTROL_CONNECTION_UNHEALTHY"
    if str(getattr(self, "_market_stream_status", "OFFLINE")).upper() != "READY":
      return "MARKET_STREAM_NOT_READY"
    native_reset = getattr(self, "_whole_market_native_reset", None)
    if native_reset is not None and native_reset.is_set():
      return "MARKET_STREAM_NATIVE_RESET"
    subscription_ready = getattr(self, "_whole_market_subscription_ready", None)
    if subscription_ready is not None and not subscription_ready.is_set():
      return "MARKET_STREAM_SUBSCRIPTION_PENDING"
    if (
      hasattr(self, "_whole_market_subscription_active")
      and not self._whole_market_subscription_active
    ):
      return "MARKET_STREAM_SUBSCRIPTION_PENDING"
    xtdata_control_lock = getattr(self, "_xtdata_access_lock", None)
    if xtdata_control_lock is not None and xtdata_control_lock.locked():
      return "XTDATA_CONTROL_PENDING"
    if getattr(self, "mode", "data-only") == "live":
      # A responsive native RPC with an unavailable broker account must not
      # suspend XTData history while waiting for the broker to recover. Actual
      # native work and report/command backpressure still take priority below.
      if not self._trading_account_waiting:
        if self._requires_trading_reconciliation():
          return "TRADING_RECONCILING"
        if (
          not self._is_trading_ready()
          or getattr(self, "_trading_readiness_failed", False)
        ):
          return "XTTRADING_UNSTABLE"
      if getattr(self, "_full_snapshot_lock", None) is not None and (
        self._full_snapshot_lock.locked()
      ):
        return "ACCOUNT_SNAPSHOT_RUNNING"
      if not self._trading_account_waiting:
        snapshot_at = getattr(
          self,
          "_last_complete_account_snapshot_monotonic",
          0.0,
        )
        if snapshot_at <= 0 or time.monotonic() - snapshot_at > (
          HISTORY_QOS_MAX_SNAPSHOT_AGE_SECONDS
        ):
          return "ACCOUNT_SNAPSHOT_STALE"
    if not self._is_market_data_ready():
      return "XTDATA_UNSTABLE"
    command_queue = getattr(self, "_command_requests", None)
    if (
      getattr(self, "_active_command_count", 0) > 0
      or (command_queue is not None and not command_queue.empty())
    ):
      return "TRADE_COMMAND_PENDING"
    journal = getattr(self, "journal", None)
    if journal is None:
      journal_stats = {}
    else:
      try:
        journal_stats = journal.stats()
      except Exception:
        return "JOURNAL_HEALTH_UNKNOWN"
    if int(journal_stats.get("processing_commands") or 0) > 0:
      return "TRADE_COMMAND_PENDING"
    if int(journal_stats.get("pending_reports") or 0) > 0:
      return "BROKER_REPORT_PENDING"
    now = time.monotonic()
    heartbeat_sent = getattr(self, "_heartbeat_sent_monotonic", {})
    if heartbeat_sent and now - min(heartbeat_sent.values()) > (
      HISTORY_QOS_MAX_HEARTBEAT_ACK_SECONDS
    ):
      return "CONTROL_HEARTBEAT_DELAYED"
    if getattr(self, "_control_heartbeat_ack_latency_seconds", 0.0) > (
      HISTORY_QOS_MAX_HEARTBEAT_ACK_SECONDS
    ):
      return "CONTROL_HEARTBEAT_DELAYED"
    pending_market_ack = getattr(
      self,
      "_market_stream_pending_ack_monotonic",
      0.0,
    )
    if pending_market_ack > 0 and now - pending_market_ack > (
      HISTORY_QOS_MAX_HEARTBEAT_ACK_SECONDS
    ):
      return "MARKET_STREAM_DELAYED"
    if getattr(self, "_market_stream_ack_latency_ms", 0.0) > (
      HISTORY_QOS_MAX_HEARTBEAT_ACK_SECONDS * 1000
    ):
      return "MARKET_STREAM_DELAYED"
    return ""

  async def _wait_for_history_dispatch(self) -> None:
    reason = self._history_qos_block_reason()
    if not reason:
      self._history_workload = "running"
      self._history_workload_reason = ""
      return
    self._history_workload = "paused"
    self._history_workload_reason = reason
    healthy_cycles = 0
    while healthy_cycles < HISTORY_QOS_HEALTHY_CYCLES:
      await asyncio.sleep(HISTORY_QOS_CHECK_SECONDS)
      reason = self._history_qos_block_reason()
      if reason:
        healthy_cycles = 0
        self._history_workload_reason = reason
        continue
      healthy_cycles += 1
    self._history_workload = "running"
    self._history_workload_reason = ""

  async def _run_market_data_preparation_thread(
    self,
    request_id: str,
    payload: dict[str, Any],
    *,
    max_total_uncompressed_bytes: int,
    max_total_compressed_bytes: int,
  ) -> _PreparedMarketData:
    """Fallback for injected test brokers without a child-process backend."""

    loop = asyncio.get_running_loop()
    outcome: asyncio.Future[_PreparedMarketData] = loop.create_future()
    abandoned = threading.Event()

    def discard(prepared: _PreparedMarketData) -> None:
      shutil.rmtree(prepared.spool_directory, ignore_errors=True)

    def deliver_result(prepared: _PreparedMarketData) -> None:
      if outcome.done() or abandoned.is_set():
        discard(prepared)
        return
      outcome.set_result(prepared)

    def deliver_error(exc: BaseException) -> None:
      if not outcome.done():
        outcome.set_exception(exc)

    def worker() -> None:
      try:
        spool_directory = _reset_market_data_spool_directory(
          self._market_spool_root,
          request_id,
        )
        prepared = _prepare_market_data_spool_sync(
          self.broker,
          payload,
          spool_directory,
          max_total_uncompressed_bytes=max_total_uncompressed_bytes,
          max_total_compressed_bytes=max_total_compressed_bytes,
        )
      except BaseException as exc:
        try:
          loop.call_soon_threadsafe(deliver_error, exc)
        except RuntimeError:
          pass
        return
      if abandoned.is_set():
        discard(prepared)
        return
      try:
        loop.call_soon_threadsafe(deliver_result, prepared)
      except RuntimeError:
        discard(prepared)

    threading.Thread(
      target=worker,
      name=f"qmt-market-data:{request_id[:32]}",
      daemon=True,
    ).start()
    try:
      return await asyncio.wait_for(
        asyncio.shield(outcome),
        timeout=MARKET_DATA_PREPARATION_TIMEOUT_SECONDS,
      )
    except asyncio.TimeoutError as exc:
      abandoned.set()
      outcome.cancel()
      fatal = _FatalMarketDataPreparationError(
        "market-data native preparation timed out; Agent restart required"
      )
      self._trip_market_data_fatal(fatal)
      raise fatal from exc
    except asyncio.CancelledError:
      abandoned.set()
      outcome.cancel()
      raise

  def _trip_market_data_fatal(
    self,
    error: _FatalMarketDataPreparationError,
  ) -> None:
    if self._fatal_market_data_error is None:
      self._fatal_market_data_error = error
    self._set_market_data_ready(False)
    self._set_market_stream_status("OFFLINE")
    self._fatal_market_data_event.set()
    self._stopped.set()

  @staticmethod
  def _consume_market_preparation_result(
    task: asyncio.Task[_PreparedMarketData],
  ) -> None:
    # A WebSocket session can disappear while the shared preparation continues.
    # Observe terminal exceptions here so an abandoned task never emits
    # "exception was never retrieved"; a redelivery can still await the task.
    if task.cancelled():
      return
    task.exception()

  def _ensure_market_upload_state(self) -> None:
    """Initialize upload state for focused harnesses that construct via __new__."""
    if not hasattr(self, "_history_request_slots"):
      self._history_request_slots = asyncio.Semaphore(MAX_CACHED_MARKET_DATA_REQUESTS)
    if not hasattr(self, "_stopped"):
      self._stopped = asyncio.Event()
    if not hasattr(self, "_fatal_market_data_error"):
      self._fatal_market_data_error = None
    if not hasattr(self, "_fatal_market_data_event"):
      self._fatal_market_data_event = asyncio.Event()
    if not hasattr(self, "_fatal_trading_error"):
      self._fatal_trading_error = None
    if not hasattr(self, "_fatal_trading_event"):
      self._fatal_trading_event = asyncio.Event()
    if not hasattr(self, "_runtime_loop"):
      self._runtime_loop = None
    if not hasattr(self, "_broker_ready"):
      self._broker_ready = asyncio.Event()
      if getattr(self, "broker", None) is not None:
        self._broker_ready.set()
    if not hasattr(self, "_market_spool_root"):
      ephemeral_base = Path(tempfile.mkdtemp(prefix="quantx-qmt-agent-test-"))
      self._market_spool_root = _initialize_market_data_spool_root(
        ephemeral_base,
        f"test-{uuid.uuid4()}",
      )
      self._market_spool_ephemeral_base = ephemeral_base
    elif not hasattr(self, "_market_spool_ephemeral_base"):
      self._market_spool_ephemeral_base = None
    if not hasattr(self, "_market_spool_cleanup_pending"):
      self._market_spool_cleanup_pending = _sweep_market_data_spool_cleanup(
        self._market_spool_root
      )
    if not hasattr(self, "_market_spool_cleanup_lock"):
      self._market_spool_cleanup_lock = asyncio.Lock()
    if not hasattr(self, "_market_upload_cache"):
      self._market_upload_cache = {}
    if not hasattr(self, "_market_upload_tombstones"):
      self._market_upload_tombstones = {}
    if not hasattr(self, "_market_upload_tasks"):
      self._market_upload_tasks = {}
    if not hasattr(self, "_market_data_http_client"):
      self._market_data_http_client = None
    if not hasattr(self, "_history_upload_slots"):
      self._history_upload_slots = asyncio.Semaphore(MAX_CONCURRENT_HISTORY_UPLOADS)
    if not hasattr(self, "_history_upload_limiter"):
      self._history_upload_limiter = _HistoryUploadBandwidthLimiter()
    if not hasattr(self, "_history_upload_io_executor"):
      self._history_upload_io_executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="qmt-history-upload-io",
        initializer=_set_low_thread_priority,
      )
    if not hasattr(self, "_historical_ipc_executor"):
      self._historical_ipc_executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="qmt-history-ipc",
        initializer=_set_low_thread_priority,
      )
    if not hasattr(self, "_streamed_market_uploads"):
      self._streamed_market_uploads = set()
    if not hasattr(self, "_provisional_market_uploads"):
      self._provisional_market_uploads = set()
    if not hasattr(self, "_queued_market_data_requests"):
      self._queued_market_data_requests = {}
    if not hasattr(self, "_market_upload_cache_bytes"):
      self._market_upload_cache_bytes = 0
    if not hasattr(self, "_xtdata_access_lock"):
      self._xtdata_access_lock = asyncio.Lock()
    if not hasattr(self, "_historical_worker_lock"):
      self._historical_worker_lock = asyncio.Lock()
    if not hasattr(self, "_websocket_send_lock"):
      self._websocket_send_lock = asyncio.Lock()
    if not hasattr(self, "_control_socket_writer"):
      self._control_socket_writer = None
    if not hasattr(self, "_heartbeat_checkpoint_lock"):
      self._heartbeat_checkpoint_lock = asyncio.Lock()
    if not hasattr(self, "_report_flush_lock"):
      self._report_flush_lock = asyncio.Lock()
    if not hasattr(self, "_report_wakeup"):
      self._report_wakeup = asyncio.Event()
    if not hasattr(self, "_reports_inflight"):
      self._reports_inflight = set()
    if not hasattr(self, "_report_retry_attempts"):
      self._report_retry_attempts = {}
    if not hasattr(self, "_report_retry_not_before"):
      self._report_retry_not_before = {}
    if not hasattr(self, "_report_ack_requests"):
      self._report_ack_requests = asyncio.Queue(
        maxsize=REPORT_ACK_QUEUE_CAPACITY
      )
    if not hasattr(self, "_report_ack_pending"):
      self._report_ack_pending = set()
    if not hasattr(self, "_full_snapshot_lock"):
      self._full_snapshot_lock = asyncio.Lock()
    if not hasattr(self, "_initial_reconciliation_complete"):
      self._initial_reconciliation_complete = asyncio.Event()
      if getattr(self, "broker", None) is not None:
        self._initial_reconciliation_complete.set()
    if not hasattr(self, "_heartbeat_wakeup"):
      self._heartbeat_wakeup = asyncio.Event()
    if not hasattr(self, "_heartbeat_sent_monotonic"):
      self._heartbeat_sent_monotonic = {}
    if not hasattr(self, "_control_heartbeat_ack_latency_seconds"):
      self._control_heartbeat_ack_latency_seconds = 0.0
    if not hasattr(self, "_last_complete_account_snapshot_monotonic"):
      self._last_complete_account_snapshot_monotonic = 0.0
    if not hasattr(self, "_history_workload"):
      self._history_workload = "idle"
    if not hasattr(self, "_history_workload_reason"):
      self._history_workload_reason = ""
    if not hasattr(self, "_emergency_stop_status_cache"):
      self._emergency_stop_status_cache = None
    if not hasattr(self, "_historical_worker_process"):
      self._historical_worker_process = None
    if not hasattr(self, "_historical_worker_connection"):
      self._historical_worker_connection = None
    if not hasattr(self, "_historical_worker_kind"):
      self._historical_worker_kind = ""
    if not hasattr(self, "_command_requests"):
      self._command_requests = _CommandDispatchQueue()
    if not hasattr(self, "_command_request_sequence"):
      self._command_request_sequence = 0
    if not hasattr(self, "_active_command_count"):
      self._active_command_count = 0
    if not hasattr(self, "_market_control_requests"):
      self._market_control_requests = asyncio.Queue(
        maxsize=MAX_QUEUED_MARKET_CONTROLS
      )
    if not hasattr(self, "_xttrading_worker"):
      self._xttrading_worker = None
    if not hasattr(self, "_journal_worker"):
      self._journal_worker = _JournalPriorityWorker()
    if not hasattr(self, "_market_upload_clock"):
      self._market_upload_clock = time.monotonic
    if not hasattr(self, "_session_loop"):
      self._session_loop = None
    if not hasattr(self, "_market_event_drops"):
      self._market_event_drops = 0
    self._ensure_whole_market_state()
    if not hasattr(self, "_market_stream_resyncs"):
      self._market_stream_resyncs = 0
    if not hasattr(self, "_market_stream_status"):
      self._set_market_stream_status("OFFLINE")
    if not hasattr(self, "_market_stream_sequence"):
      self._market_stream_sequence = 0
    if not hasattr(self, "_market_stream_ack_latency_ms"):
      self._market_stream_ack_latency_ms = 0.0
    if not hasattr(self, "_market_stream_pending_ack_monotonic"):
      self._market_stream_pending_ack_monotonic = 0.0

  def _ensure_whole_market_state(self) -> None:
    """Initialize capture state for focused harnesses using ``__new__``."""
    if not hasattr(self, "_market_events"):
      self._market_events = asyncio.Queue(maxsize=10_000)
    if not hasattr(self, "_market_event_drops"):
      self._market_event_drops = 0
    if not hasattr(self, "_whole_market_capture"):
      self._whole_market_capture = WholeMarketCapture(
        max_ready_callbacks=MARKET_STREAM_READY_INGRESS_CALLBACKS,
        max_ready_estimated_bytes=MARKET_STREAM_READY_INGRESS_BYTES,
        estimated_tick_bytes=MARKET_STREAM_READY_ESTIMATED_TICK_BYTES,
      )
    if not hasattr(self, "_whole_market_subscription_ready"):
      self._whole_market_subscription_ready = asyncio.Event()
    if not hasattr(self, "_whole_market_subscription_active"):
      self._whole_market_subscription_active = False
    if not hasattr(self, "_whole_market_native_reset"):
      self._whole_market_native_reset = asyncio.Event()
    if not hasattr(self, "_access_token_ready"):
      self._access_token_ready = asyncio.Event()
    if not hasattr(self, "_control_hub_registered_once"):
      self._control_hub_registered_once = asyncio.Event()
    if not hasattr(self, "_access_token"):
      self._access_token = ""
    if not hasattr(self, "_access_token_expires_at"):
      self._access_token_expires_at = datetime.now(timezone.utc)
    if not hasattr(self, "_control_agent_session_id"):
      self._control_agent_session_id = ""
    if not hasattr(self, "_whole_market_encode_executor"):
      self._whole_market_encode_executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="qmt-whole-market-encode",
      )
    if not hasattr(self, "_market_stream_ready_since_monotonic"):
      self._market_stream_ready_since_monotonic = 0.0
    if not hasattr(self, "_market_stream_outbound_depth"):
      self._market_stream_outbound_depth = 0
    if not hasattr(self, "_market_stream_outbound_bytes"):
      self._market_stream_outbound_bytes = 0

  def _cleanup_expired_market_uploads(
    self,
    now: float | None = None,
    *,
    remove_prepared: bool = True,
  ) -> list[_PreparedMarketData]:
    self._ensure_market_upload_state()
    _ = remove_prepared  # Retained for compatibility with focused harnesses.
    current = self._market_upload_clock() if now is None else now
    retired: list[_PreparedMarketData] = []
    expired_request_ids = [
      request_id
      for request_id, entry in self._market_upload_cache.items()
      if (
        entry.task is not None
        and entry.task.done()
        and current - entry.last_access_at >= MARKET_DATA_UPLOAD_CACHE_TTL_SECONDS
      )
    ]
    for request_id in expired_request_ids:
      # Retire only the in-memory projection. A completed spool is the durable
      # retry authority and remains until the server confirms terminal upload.
      self._drop_market_upload_cache_entry(
        request_id,
        remove_prepared=False,
      )
    expired_tombstones = [
      request_id
      for request_id, tombstone in self._market_upload_tombstones.items()
      if (current - tombstone.last_access_at >= MARKET_DATA_UPLOAD_CACHE_TTL_SECONDS)
    ]
    for request_id in expired_tombstones:
      self._market_upload_tombstones.pop(request_id, None)
    return retired

  def _drop_market_upload_cache_entry(
    self,
    request_id: str,
    *,
    cancel: bool = False,
    remove_prepared: bool = True,
  ) -> _MarketUploadCacheEntry | None:
    getattr(self, "_history_progress", {}).pop(request_id, None)
    getattr(self, "_history_uploaded_chunks", {}).pop(request_id, None)
    entry = self._market_upload_cache.pop(request_id, None)
    if entry is None:
      return None
    self._market_upload_cache_bytes = max(
      0,
      self._market_upload_cache_bytes - entry.compressed_bytes,
    )
    if cancel and entry.task is not None and not entry.task.done():
      entry.task.cancel()
    elif remove_prepared and entry.task is not None and entry.task.done():
      try:
        prepared = entry.task.result()
      except BaseException:
        pass
      else:
        self._remove_prepared_market_data(prepared)
    return entry

  @staticmethod
  def _remove_prepared_market_data(prepared: _PreparedMarketData) -> None:
    shutil.rmtree(prepared.spool_directory, ignore_errors=True)

  def _touch_market_upload(self, request_id: str) -> None:
    entry = self._market_upload_cache.get(request_id)
    if entry is not None:
      entry.last_access_at = self._market_upload_clock()

  def _record_market_upload_tombstone(
    self,
    request_id: str,
    fingerprint: str,
  ) -> None:
    now = self._market_upload_clock()
    self._market_upload_tombstones[request_id] = _MarketUploadTombstone(
      fingerprint=fingerprint,
      completed_at=now,
      last_access_at=now,
    )
    while len(self._market_upload_tombstones) > MAX_MARKET_DATA_TOMBSTONES:
      oldest_request_id = next(iter(self._market_upload_tombstones))
      self._market_upload_tombstones.pop(oldest_request_id, None)

  async def _retire_terminal_market_upload(
    self,
    request_id: str,
    *,
    fingerprint: str = "",
    terminal_status: str,
  ) -> None:
    self._ensure_market_upload_state()
    getattr(self, "_history_progress", {}).pop(request_id, None)
    getattr(self, "_history_uploaded_chunks", {}).pop(request_id, None)
    self._streamed_market_uploads.discard(request_id)
    self._provisional_market_uploads.discard(request_id)
    entry = self._market_upload_cache.get(request_id)
    authoritative_fingerprint = entry.fingerprint if entry is not None else fingerprint
    prepared: _PreparedMarketData | None = None
    if entry is not None and entry.task is not None and entry.task.done():
      try:
        prepared = entry.task.result()
      except BaseException:
        prepared = None
    if prepared is not None:
      try:
        await asyncio.to_thread(
          _write_market_data_spool_terminal_marker,
          prepared,
          request_id=request_id,
          fingerprint=authoritative_fingerprint,
          terminal_status=terminal_status,
        )
      except Exception as exc:
        # Removal still proceeds. If Windows has a transient file handle, a
        # successfully written marker lets startup finish cleanup safely.
        logger.warning(
          "Could not persist market-data terminal spool marker: "
          "request_id=%s error=%s",
          request_id,
          exc.__class__.__name__,
        )
    self._drop_market_upload_cache_entry(
      request_id,
      remove_prepared=False,
    )
    if authoritative_fingerprint:
      self._record_market_upload_tombstone(
        request_id,
        authoritative_fingerprint,
      )
    if prepared is not None:
      await asyncio.to_thread(self._remove_prepared_market_data, prepared)
      try:
        cleanup_pending = prepared.spool_directory.exists()
      except OSError:
        cleanup_pending = True
      if cleanup_pending:
        self._market_spool_cleanup_pending = True

  async def _complete_market_upload(self, request_id: str) -> None:
    await self._retire_terminal_market_upload(
      request_id,
      terminal_status="COMPLETED",
    )

  def _clear_market_upload_state(self) -> None:
    self._ensure_market_upload_state()
    active_uploads = [
      upload.task
      for upload in self._market_upload_tasks.values()
      if not upload.task.done()
    ]
    if active_uploads:
      raise RuntimeError("market-data uploads must stop before clearing their spool")
    self._market_upload_tasks.clear()
    self._streamed_market_uploads.clear()
    self._provisional_market_uploads.clear()
    self._queued_market_data_requests.clear()
    remove_persisted_spool = self._market_spool_ephemeral_base is not None
    for request_id in list(self._market_upload_cache):
      self._drop_market_upload_cache_entry(
        request_id,
        cancel=True,
        remove_prepared=remove_persisted_spool,
      )
    self._market_upload_cache_bytes = 0
    self._market_upload_tombstones.clear()
    if self._market_spool_ephemeral_base is not None:
      shutil.rmtree(self._market_spool_ephemeral_base, ignore_errors=True)
      self._market_spool_ephemeral_base = None

  async def _cancel_market_upload_tasks(self) -> None:
    self._ensure_market_upload_state()
    tasks = [upload.task for upload in self._market_upload_tasks.values()]
    for task in tasks:
      if not task.done():
        task.cancel()
    if tasks:
      await asyncio.gather(*tasks, return_exceptions=True)
    self._market_upload_tasks.clear()

  async def _market_request_loop(self, socket) -> None:
    if getattr(self, "broker", None) is None and getattr(
      self,
      "_broker_factory",
      None,
    ) is not None:
      await self._broker_ready.wait()
    while True:
      envelope = await self._market_requests.get()
      request_id = str(envelope.payload.get("request_id") or "")
      self._queued_market_data_requests.pop(request_id, None)
      upload_task: asyncio.Task[None] | None = None
      try:
        try:
          while True:
            upload_task = self._market_upload_task(envelope)
            try:
              await asyncio.shield(upload_task)
            except _MarketDataSpoolCleanupPending:
              self._history_workload = "paused"
              self._history_workload_reason = "SPOOL_CLEANUP_PENDING"
              logger.warning(
                "Historical market-data request paused for spool cleanup: "
                "request_id=%s",
                request_id,
              )
              await asyncio.sleep(HISTORY_QOS_CHECK_SECONDS)
              continue
            break
        except asyncio.CancelledError:
          if upload_task is not None and not upload_task.done():
            logger.info(
              "QMT market-data session detached; upload continues: request_id=%s",
              request_id,
            )
          raise
        except _FatalMarketDataPreparationError:
          await socket.close(code=1011, reason="market data request failed")
          return
        except Exception as exc:
          if not _is_deterministic_market_data_request_error(exc):
            logger.warning(
              "QMT market-data upload will resume after session reconnect: "
              "request_id=%s error=%s",
              request_id,
              exc.__class__.__name__,
            )
            await socket.close(
              code=1012,
              reason="market data upload retry",
            )
            return
          logger.warning(
            "QMT market data request rejected: request_id=%s error=%s",
            request_id,
            _market_data_failure_reason(exc),
          )
          try:
            await self._report_market_data_failure(request_id, exc)
          except Exception as report_exc:
            logger.warning(
              "Could not report QMT market data failure: request_id=%s error=%s",
              request_id,
              report_exc.__class__.__name__,
            )
          else:
            await self._retire_terminal_market_upload(
              request_id,
              fingerprint=_market_data_payload_fingerprint(envelope.payload),
              terminal_status="FAILED",
            )
          continue

        # The 90-second server freshness window may expire while XTData holds
        # the GIL. Refresh it before this serial worker starts another request.
        # Keep the checkpoint outside upload failure classification: a socket
        # send failure must never terminally fail an already uploaded request.
        await self._heartbeat_checkpoint(socket, status="READY")
      finally:
        self._market_requests.task_done()

  async def _report_market_data_failure(
    self,
    request_id: str,
    error: Exception,
  ) -> None:
    reason = _market_data_failure_reason(error)
    async with httpx.AsyncClient(
      timeout=10.0,
      follow_redirects=False,
      trust_env=False,
      verify=httpx_verify(self.configuration.api_url),
    ) as client:
      response = await client.post(
        (f"{self.configuration.api_url}/agent/market-data/{request_id}/fail"),
        headers={"Authorization": f"Bearer {self._access_token}"},
        json={"reason": reason},
      )
      response.raise_for_status()

  async def _report_market_data_busy(self, request_id: str) -> None:
    """Use the existing per-request failure endpoint for retryable backpressure."""

    async with httpx.AsyncClient(
      timeout=10.0,
      follow_redirects=False,
      trust_env=False,
      verify=httpx_verify(self.configuration.api_url),
    ) as client:
      response = await client.post(
        (f"{self.configuration.api_url}/agent/market-data/{request_id}/fail"),
        headers={"Authorization": f"Bearer {self._access_token}"},
        json={"reason": "MARKET_DATA_AGENT_BUSY"},
      )
      response.raise_for_status()

  def _market_upload_task(
    self,
    envelope: AgentEnvelope,
  ) -> asyncio.Task[None]:
    self._ensure_market_upload_state()
    request_id = str(envelope.payload["request_id"])
    fingerprint = _market_data_payload_fingerprint(envelope.payload)
    existing = self._market_upload_tasks.get(request_id)
    if existing is not None:
      if existing.fingerprint != fingerprint:
        raise RuntimeError("同一 market-data request_id 的重投参数不一致")
      logger.info(
        "QMT market-data redelivery joined active upload: request_id=%s",
        request_id,
      )
      return existing.task

    task = asyncio.create_task(
      self._handle_market_data_request(envelope),
      name=f"market-data-upload:{request_id}",
    )
    entry = _MarketUploadTaskEntry(
      fingerprint=fingerprint,
      task=task,
    )
    self._market_upload_tasks[request_id] = entry
    task.add_done_callback(
      lambda completed: self._retire_market_upload_task(
        request_id,
        entry,
        completed,
      )
    )
    return task

  def _retire_market_upload_task(
    self,
    request_id: str,
    entry: _MarketUploadTaskEntry,
    task: asyncio.Task[None],
  ) -> None:
    if self._market_upload_tasks.get(request_id) is entry:
      self._market_upload_tasks.pop(request_id, None)
    if not task.cancelled():
      task.exception()

  def stop(self) -> None:
    self._ensure_market_upload_state()
    self._stopped.set()
    worker = getattr(self, "_xttrading_worker", None)
    if worker is not None:
      worker.close()
    journal_worker = getattr(self, "_journal_worker", None)
    if journal_worker is not None:
      journal_worker.close()
    for upload in self._market_upload_tasks.values():
      if not upload.task.done():
        upload.task.cancel()
    if not any(not upload.task.done() for upload in self._market_upload_tasks.values()):
      self._clear_market_upload_state()
