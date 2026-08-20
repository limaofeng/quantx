"""Authenticated outbound WebSocket runtime for the QMT Agent."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
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
from typing import Any, AsyncIterator, BinaryIO, Iterable, Iterator
from zoneinfo import ZoneInfo

import httpx
import websockets
from pydantic import ValidationError
from quantx_contracts import (
  MARKET_STREAM_MARKETS,
  MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS,
  MARKET_STREAM_SUBPROTOCOL,
  MAX_MARKET_STREAM_FRAME_BYTES,
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
  MAX_MARKET_DATA_RECORDS,
  WHOLE_QUOTE_SNAPSHOT_BATCH_SIZE,
  enrich_report_payload,
)
from .credentials import DeviceConfiguration, state_directory
from .emergency import EmergencyStopStore
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
MAX_MARKET_DATA_TOMBSTONES = 1024
MARKET_DATA_UPLOAD_CACHE_TTL_SECONDS = 60 * 60
MARKET_DATA_UPLOAD_CACHE_SWEEP_SECONDS = 60
MARKET_DATA_PREPARATION_TIMEOUT_SECONDS = 15 * 60
XTDATA_CONTROL_TIMEOUT_SECONDS = 60
XTDATA_READINESS_RETRY_SECONDS = 5
XTTRADING_READINESS_RETRY_SECONDS = 5
XTTRADING_RECONNECT_TIMEOUT_SECONDS = 30
WEBSOCKET_PING_INTERVAL_SECONDS = 20
WEBSOCKET_PING_TIMEOUT_SECONDS = 16 * 60
WEBSOCKET_SEND_TIMEOUT_SECONDS = 30
MARKET_STREAM_ACK_TIMEOUT_SECONDS = MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS
MARKET_STREAM_HANDSHAKE_TIMEOUT_SECONDS = 10
MARKET_STREAM_READY_INGRESS_BYTES = 64 * 1024 * 1024
# Every retained callback is charged at least this many estimated bytes.  Set
# the structural ceiling from the same budget so it cannot reject a valid burst
# before the 64 MiB retained-memory authority does.
MARKET_STREAM_READY_INGRESS_CALLBACKS = (
  MARKET_STREAM_READY_INGRESS_BYTES
  // MIN_CAPTURED_MARKET_EVENT_ESTIMATED_BYTES
)
# The callback path deliberately avoids JSON serialization.  Charge every tick
# a conservative retained-memory estimate; the outbound cap below uses exact
# encoded bytes.
MARKET_STREAM_READY_ESTIMATED_TICK_BYTES = 2048
MARKET_STREAM_OUTBOUND_BATCHES = 8
MARKET_STREAM_OUTBOUND_BYTES = 64 * 1024 * 1024
MARKET_STREAM_MAX_UNACKNOWLEDGED_BATCHES = 2
MARKET_STREAM_INITIAL_PUSH_WAIT_SECONDS = 5.0
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
  MARKET_STREAM_MICROBATCH_ESTIMATED_BYTES
  // MARKET_STREAM_READY_ESTIMATED_TICK_BYTES
)
MARKET_STREAM_STABLE_READY_SECONDS = 30.0
MARKET_STREAM_NATIVE_HEALTH_CHECK_SECONDS = 5.0
MARKET_STREAM_NATIVE_SILENCE_SECONDS = 10.0
MARKET_STREAM_NATIVE_SILENCE_CONFIRMATIONS = 2
MARKET_DATA_UPLOAD_READ_BYTES = 256 * 1024
MARKET_DATA_SPOOL_DIRECTORY_NAME = "market-data-spool"
MARKET_DATA_SPOOL_REQUEST_PREFIX = "request-"
MARKET_DATA_SPOOL_OWNER_MARKER = ".owner.json"
LEGACY_MARKET_DATA_SPOOL_PREFIX = "quantx-market-data-spool-"
SHANGHAI_ZONE = ZoneInfo("Asia/Shanghai")


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


@dataclass(frozen=True, slots=True)
class _EncodedMarketBatch:
  batch: MarketStreamBatch
  payload: bytes


@dataclass(frozen=True, slots=True)
class _PendingMarketAck:
  encoded: _EncodedMarketBatch
  sent_monotonic: float


class _BoundedMarketBatchBuffer:
  """Bound queued and unacknowledged batches by their actual wire bytes."""

  def __init__(self, *, max_batches: int, max_bytes: int) -> None:
    self._queue: asyncio.Queue[_EncodedMarketBatch] = asyncio.Queue(
      maxsize=max_batches
    )
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


class _FatalMarketDataPreparationError(RuntimeError):
  """A hung native request requires the supervised Agent process to restart."""


class _FatalMarketDataUploadConflict(_FatalMarketDataPreparationError):
  """A server-side chunk identity conflict forbids further mixed uploads."""


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
      attributes = int(
        getattr(child.lstat(), "st_file_attributes", 0)
      )
      if child.is_symlink() or attributes & reparse_flag:
        logger.warning("Skipped unsafe legacy market-data spool: %s", child)
        continue
      resolved_child = child.resolve()
      if (
        resolved_child.parent != resolved_temp
        or not resolved_child.is_dir()
      ):
        logger.warning("Skipped unsafe legacy market-data spool: %s", child)
        continue
      shutil.rmtree(resolved_child)
    except FileNotFoundError:
      continue


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


def _initialize_market_data_spool_root(
  base_directory: Path,
  device_id: str,
) -> Path:
  owner_key = _market_data_spool_owner_key(device_id)
  managed_root = (
    base_directory.resolve() / MARKET_DATA_SPOOL_DIRECTORY_NAME
  )
  managed_root.mkdir(parents=True, exist_ok=True)
  owner_root = managed_root / owner_key
  owner_root.mkdir(parents=False, exist_ok=True)
  if (
    owner_root.is_symlink()
    or owner_root.resolve().parent != managed_root.resolve()
  ):
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

  for child in list(owner_root.iterdir()):
    if child.name == MARKET_DATA_SPOOL_OWNER_MARKER:
      continue
    if not child.name.startswith(MARKET_DATA_SPOOL_REQUEST_PREFIX):
      continue
    safe_child = _safe_market_data_spool_request(owner_root, child)
    if safe_child.is_dir():
      shutil.rmtree(safe_child)
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
          raise RuntimeError("market-data spool byte limit exceeded")
  return total


class _LimitedHashingWriter:
  def __init__(self, raw: BinaryIO, *, max_bytes: int) -> None:
    self.raw = raw
    self.max_bytes = max_bytes
    self.bytes_written = 0
    self.digest = hashlib.sha256()

  def write(self, data: bytes) -> int:
    next_size = self.bytes_written + len(data)
    if next_size > self.max_bytes:
      raise ValueError("market data request exceeds compressed byte limit")
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
  records: Iterable[dict[str, Any]],
  *,
  max_records: int = MAX_MARKET_DATA_CHUNK_RECORDS,
  max_uncompressed_bytes: int = MAX_MARKET_DATA_CHUNK_UNCOMPRESSED_BYTES,
  max_total_records: int = MAX_MARKET_DATA_REQUEST_RECORDS,
  max_record_uncompressed_bytes: int = (
    MAX_MARKET_DATA_RECORD_UNCOMPRESSED_BYTES
  ),
  max_total_uncompressed_bytes: int = MAX_MARKET_DATA_REQUEST_UNCOMPRESSED_BYTES,
) -> Iterator[tuple[bytearray, int]]:
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
    total_records += 1
    if total_records > max_total_records:
      raise ValueError("market data request exceeds record count limit")
    encoded = json.dumps(
      record,
      ensure_ascii=False,
      separators=(",", ":"),
      sort_keys=True,
      default=str,
    ).encode("utf-8")
    if len(encoded) > max_record_uncompressed_bytes:
      raise ValueError("single market data record exceeds record byte limit")
    if len(encoded) + 2 > max_uncompressed_bytes:
      raise ValueError("single market data record exceeds chunk size limit")
    separator_size = 1 if current_records > 0 else 0
    if current_records > 0 and (
      current_records >= max_records
      or len(current) + separator_size + len(encoded) + 1
      > max_uncompressed_bytes
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
  chunks: list[_MarketDataSpoolChunk] = []
  uncompressed_bytes = 0
  compressed_bytes = 0
  record_count_total = 0
  try:
    for chunk_index, (raw, record_count) in enumerate(
      _iter_encoded_market_data_chunks(
        records,
        max_total_uncompressed_bytes=max_total_uncompressed_bytes,
      )
    ):
      path = spool_directory / f"chunk-{chunk_index:06d}.json.gz"
      remaining = max_total_compressed_bytes - compressed_bytes
      if remaining <= 0:
        raise ValueError("market data request exceeds compressed byte limit")
      with path.open("xb") as file_handle:
        writer = _LimitedHashingWriter(file_handle, max_bytes=remaining)
        with gzip.GzipFile(
          filename="",
          mode="wb",
          fileobj=writer,
          mtime=0,
        ) as compressor:
          compressor.write(raw)
      chunks.append(
        _MarketDataSpoolChunk(
          path=path,
          record_count=record_count,
          digest=writer.digest.hexdigest(),
          compressed_bytes=writer.bytes_written,
        )
      )
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


async def _stream_spool_chunk(path: Path) -> AsyncIterator[bytes]:
  file_handle = await asyncio.to_thread(path.open, "rb")
  try:
    while True:
      block = await asyncio.to_thread(
        file_handle.read,
        MARKET_DATA_UPLOAD_READ_BYTES,
      )
      if not block:
        break
      yield block
  finally:
    await asyncio.to_thread(file_handle.close)


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
  if api_url.startswith("https://"):
    return f"wss://{api_url[8:].rstrip('/')}{path}"
  if api_url.startswith("http://"):
    return f"ws://{api_url[7:].rstrip('/')}{path}"
  raise ValueError("api_url 必须以 http:// 或 https:// 开头")


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
    broker,
    journal: LocalJournal,
    emergency_stop: EmergencyStopStore | None = None,
    market_spool_base_directory: Path | None = None,
  ) -> None:
    self.configuration = configuration
    self.device_secret = device_secret
    self.mode = mode
    self.allowed_accounts = allowed_accounts
    self.broker = broker
    self.journal = journal
    self.emergency_stop = emergency_stop
    self._stopped = asyncio.Event()
    self._access_token = ""
    self._access_token_expires_at = datetime.now(timezone.utc)
    self._access_token_ready = asyncio.Event()
    # Sticky process-lifetime gate. The market socket must not race ahead of
    # the first successful control-hub registration, but later control socket
    # reconnects must never tear down an already READY market stream.
    self._control_hub_registered_once = asyncio.Event()
    self._session_loop: asyncio.AbstractEventLoop | None = None
    self._market_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
      maxsize=10_000
    )
    self._market_event_overflow = asyncio.Event()
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
    self._market_stream_ready_since_monotonic = 0.0
    self._market_stream_outbound_depth = 0
    self._market_stream_outbound_bytes = 0
    self._market_requests: asyncio.Queue[AgentEnvelope] = asyncio.Queue(
      maxsize=MAX_QUEUED_MARKET_DATA_REQUESTS
    )
    # A token refresh intentionally reconnects the WebSocket. Keep the exact
    # compressed bytes for in-flight requests so a redelivery reuses the same
    # chunk checksums instead of re-querying a still-changing XTData cache.
    self._market_upload_cache: dict[str, _MarketUploadCacheEntry] = {}
    self._market_upload_tombstones: dict[
      str, _MarketUploadTombstone
    ] = {}
    self._market_upload_tasks: dict[str, _MarketUploadTaskEntry] = {}
    self._market_upload_cache_bytes = 0
    self._xtdata_access_lock = asyncio.Lock()
    self._websocket_send_lock = asyncio.Lock()
    self._heartbeat_checkpoint_lock = asyncio.Lock()
    self._market_upload_clock = time.monotonic
    self._fatal_market_data_error: (
      _FatalMarketDataPreparationError | None
    ) = None
    self._fatal_market_data_event = asyncio.Event()
    _cleanup_legacy_market_data_spools(Path(tempfile.gettempdir()))
    self._market_spool_root = _initialize_market_data_spool_root(
      market_spool_base_directory or state_directory(),
      configuration.device_id,
    )
    self._market_spool_ephemeral_base: Path | None = None

  async def run_forever(self) -> None:
    self._ensure_market_upload_state()
    self._ensure_whole_market_state()
    self._whole_market_capture.bind_loop(asyncio.get_running_loop())
    cache_sweeper = asyncio.create_task(
      self._market_upload_cache_sweeper(),
      name="market-data-cache-sweeper",
    )
    capture_supervisor = asyncio.create_task(
      self._whole_market_capture_supervisor(),
      name="whole-market-capture-supervisor",
    )
    market_stream_supervisor = asyncio.create_task(
      self._whole_market_stream_supervisor(),
      name="whole-market-stream-supervisor",
    )
    try:
      delay = 1
      while not self._stopped.is_set():
        try:
          if not await self._run_session_until_fatal():
            break
          delay = 1
        except asyncio.CancelledError:
          raise
        except Exception as exc:
          logger.warning(
            "QMT Agent disconnected: %s: %s",
            exc.__class__.__name__,
            exc,
          )
          try:
            await asyncio.wait_for(self._stopped.wait(), timeout=delay)
          except asyncio.TimeoutError:
            pass
          delay = min(delay * 2, 300)
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
      self._clear_market_upload_state()
    if self._fatal_market_data_error is not None:
      raise self._fatal_market_data_error

  async def _run_session_until_fatal(self) -> bool:
    session = asyncio.create_task(
      self._run_session(),
      name="qmt-agent-session",
    )
    fatal = asyncio.create_task(
      self._fatal_market_data_event.wait(),
      name="qmt-agent-fatal-wait",
    )
    try:
      done, _ = await asyncio.wait(
        {session, fatal},
        return_when=asyncio.FIRST_COMPLETED,
      )
      if fatal in done and self._fatal_market_data_event.is_set():
        session.cancel()
        await asyncio.gather(session, return_exceptions=True)
        return False
      fatal.cancel()
      await asyncio.gather(fatal, return_exceptions=True)
      await session
      return True
    finally:
      for task in (session, fatal):
        if not task.done():
          task.cancel()
      await asyncio.gather(session, fatal, return_exceptions=True)

  async def _market_upload_cache_sweeper(self) -> None:
    while not self._stopped.is_set():
      try:
        await asyncio.wait_for(
          self._stopped.wait(),
          timeout=MARKET_DATA_UPLOAD_CACHE_SWEEP_SECONDS,
        )
      except asyncio.TimeoutError:
        self._cleanup_expired_market_uploads()

  async def _issue_token(self) -> tuple[str, datetime]:
    async with httpx.AsyncClient(timeout=10.0) as client:
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

  async def _run_session(self) -> None:
    access_token, expires_at = await self._issue_token()
    self._access_token = access_token
    self._access_token_expires_at = expires_at
    self._access_token_ready.set()
    async with websockets.connect(
      _websocket_url(self.configuration.api_url),
      max_size=8 * 1024 * 1024,
      ping_interval=WEBSOCKET_PING_INTERVAL_SECONDS,
      # XTData is guarded by a 15-minute fail-stop watchdog, but a native call
      # can hold the GIL until that watchdog fires. Keep the transport alive
      # slightly longer; application heartbeats still expose stale Agents.
      ping_timeout=WEBSOCKET_PING_TIMEOUT_SECONDS,
    ) as socket:
      auth = AgentEnvelope(
        message_type=AgentMessageType.AUTH,
        payload={
          "device_id": self.configuration.device_id,
          "access_token": self._access_token,
          "agent_version": "0.1.0",
          "capabilities": ["market-data", "divid-factors", self.mode],
        },
      )
      await self._send_socket_text(socket, auth.model_dump_json())
      auth_result = AgentEnvelope.model_validate_json(await socket.recv())
      if (
        auth_result.message_type is not AgentMessageType.AUTH_RESULT
        or not auth_result.payload.get("accepted")
      ):
        raise RuntimeError("QMT Agent authentication rejected")
      self._control_hub_registered_once.set()

      self._session_loop = asyncio.get_running_loop()
      self._market_requests = asyncio.Queue(
        maxsize=MAX_QUEUED_MARKET_DATA_REQUESTS
      )
      self._market_event_overflow = asyncio.Event()
      await self._ensure_trading_ready()
      await self._queue_full_snapshot()
      await self._heartbeat_checkpoint(socket, status="RECONCILING")
      session_tasks = {
        "receiver": asyncio.create_task(
          self._receive_session_messages(socket),
          name="qmt-agent-receiver",
        ),
        "heartbeat": asyncio.create_task(
          self._heartbeat_loop(socket),
          name="qmt-agent-heartbeat",
        ),
        "renewal": asyncio.create_task(
          self._close_before_token_expiry(socket),
          name="qmt-agent-token-renewal",
        ),
        "report-sender": asyncio.create_task(
          self._broker_report_loop(socket),
          name="qmt-agent-report-sender",
        ),
        "market-sender": asyncio.create_task(
          self._market_event_loop(socket),
          name="qmt-agent-market-sender",
        ),
        "market-request": asyncio.create_task(
          self._market_request_loop(socket),
          name="qmt-agent-market-request",
        ),
        "market-readiness": asyncio.create_task(
          self._market_data_readiness_loop(),
          name="qmt-agent-market-readiness",
        ),
        "trading-readiness": asyncio.create_task(
          self._trading_readiness_loop(),
          name="qmt-agent-trading-readiness",
        ),
      }
      try:
        await self._supervise_session_tasks(socket, session_tasks)
      finally:
        for task in session_tasks.values():
          if not task.done():
            task.cancel()
        await asyncio.gather(*session_tasks.values(), return_exceptions=True)
        self._session_loop = None

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
      for role, task in tasks.items():
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
        if role not in {"renewal", "market-request"}:
          error = RuntimeError(
            f"QMT Agent session task stopped unexpectedly: {role}"
          )
          logger.error("%s", error)
          await socket.close(
            code=1011,
            reason=f"session task stopped: {role}",
          )
          raise error
        await receiver
        return

  async def _queue_full_snapshot(self) -> None:
    snapshot = await asyncio.to_thread(self.broker.full_snapshot)
    snapshot_message_id = str(uuid.uuid4())
    correlations = self.journal.broker_order_client_ids()
    for collection_name in ("orders", "trades"):
      for item in snapshot.get(collection_name) or []:
        if not isinstance(item, dict):
          continue
        broker_order_id = item.get("order_id") or item.get("broker_order_id")
        client_order_id = correlations.get(str(broker_order_id))
        if not client_order_id:
          client_order_id = self.journal.client_order_id_for_report(
            broker_order_id=broker_order_id,
            order_remark=str(item.get("order_remark") or ""),
          )
        if client_order_id:
          item["client_order_id"] = client_order_id
          self.journal.reconcile_processing_order(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
          )
    snapshot["snapshot_id"] = snapshot_message_id
    snapshot["report_id"] = snapshot_message_id
    snapshot = enrich_report_payload(AgentMessageType.DELTA_REPORT, snapshot)
    envelope = AgentEnvelope(
      message_id=snapshot_message_id,
      message_type=AgentMessageType.DELTA_REPORT,
      payload=snapshot,
    )
    # An identical snapshot still proves reconciliation for this connection.
    # Give every generated full snapshot its own durable business identity while
    # keeping retries of the same journaled envelope idempotent.
    # A newer complete snapshot supersedes older unacknowledged complete
    # snapshots. Incremental order, execution, and position reports remain
    # pending and retain their original delivery order.
    self.journal.retire_pending_full_snapshots()
    self.journal.add_report(envelope.message_id, envelope.model_dump_json())

  async def _flush_reports(self, socket) -> None:
    for serialized in self.journal.pending_reports():
      await self._send_socket_text(socket, serialized)

  async def _heartbeat_loop(self, socket) -> None:
    cycles = 0
    while True:
      await asyncio.sleep(30)
      cycles += 1
      heartbeat_status = "READY"
      if cycles % 2 == 0:
        await self._queue_full_snapshot()
        heartbeat_status = "RECONCILING"
      await self._heartbeat_checkpoint(socket, status=heartbeat_status)

  def _is_market_data_ready(self) -> bool:
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
        "QMT broker readiness check failed: %s: %s",
        exc.__class__.__name__,
        exc,
      )
      return False

  async def _market_data_readiness_loop(self) -> None:
    ensure_ready = getattr(self.broker, "ensure_market_data_ready", None)
    if not callable(ensure_ready):
      while True:
        await asyncio.sleep(XTDATA_READINESS_RETRY_SECONDS)
    previous = self._is_market_data_ready()
    while True:
      try:
        await self._run_xtdata_control(
          "market-data-readiness",
          ensure_ready,
        )
      except _FatalMarketDataPreparationError:
        raise
      except Exception as exc:
        logger.warning(
          "XTData readiness retry failed: %s: %s",
          exc.__class__.__name__,
          exc,
        )
      current = self._is_market_data_ready()
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
    readiness = getattr(self.broker, "is_trading_ready", None)
    if not callable(readiness):
      return False
    try:
      return bool(readiness())
    except Exception as exc:
      logger.warning(
        "QMT trading readiness check failed: %s: %s",
        exc.__class__.__name__,
        exc,
      )
      return False

  async def _ensure_trading_ready(self) -> bool:
    if self.mode != "live":
      return True
    ensure_ready = getattr(self.broker, "ensure_trading_ready", None)
    if not callable(ensure_ready):
      return False
    try:
      return bool(
        await asyncio.wait_for(
          asyncio.to_thread(ensure_ready),
          timeout=XTTRADING_RECONNECT_TIMEOUT_SECONDS,
        )
      )
    except asyncio.TimeoutError:
      logger.warning("XTTrading reconnect timed out")
    except Exception as exc:
      logger.warning(
        "XTTrading readiness retry failed: %s: %s",
        exc.__class__.__name__,
        exc,
      )
    return False

  async def _trading_readiness_loop(self) -> None:
    previous = self._is_trading_ready()
    while True:
      await self._ensure_trading_ready()
      current = self._is_trading_ready()
      if current != previous:
        logger.info("XTTrading readiness changed: ready=%s", current)
      previous = current
      await asyncio.sleep(XTTRADING_READINESS_RETRY_SECONDS)

  async def _broker_report_loop(self, socket) -> None:
    """Flush callbacks already persisted by LiveBroker without snapshot delay."""
    while True:
      await asyncio.sleep(1)
      await self._flush_reports(socket)

  def _enqueue_market_event(self, payload: dict[str, Any]) -> None:
    loop = self._session_loop
    if loop is None or loop.is_closed():
      return

    def enqueue() -> None:
      if self._market_events.full():
        logger.error("QMT single-quote event queue overflow")
        self._market_event_overflow.set()
        return
      self._market_events.put_nowait(payload)

    loop.call_soon_threadsafe(enqueue)

  async def _market_event_loop(self, socket) -> None:
    while True:
      queued = asyncio.create_task(self._market_events.get())
      overflow = asyncio.create_task(self._market_event_overflow.wait())
      try:
        done, _ = await asyncio.wait(
          {queued, overflow},
          return_when=asyncio.FIRST_COMPLETED,
        )
        if overflow in done and self._market_event_overflow.is_set():
          raise RuntimeError("single-quote event queue overflow")
        payload = queued.result()
        await self._send_socket_text(
          socket,
          AgentEnvelope(
            message_type=AgentMessageType.MARKET_EVENT,
            payload=payload,
          ).model_dump_json(),
        )
        self._market_events.task_done()
      finally:
        for task in (queued, overflow):
          if not task.done():
            task.cancel()
        await asyncio.gather(queued, overflow, return_exceptions=True)

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
          if (
            subscribed_generation > 0
            and current_generation != subscribed_generation
          ):
            reset_reason = (
              "XTData source generation changed: "
              f"{subscribed_generation}->{current_generation}"
            )
            break

          readiness = getattr(self.broker, "is_market_data_ready", None)
          connected = bool(readiness()) if callable(readiness) else True
          if not connected:
            try:
              connected = bool(
                await self._run_xtdata_control(
                  "whole-market-readiness",
                  ensure_ready,
                )
              ) if callable(ensure_ready) else False
            except Exception as exc:
              logger.warning(
                "QMT whole-market readiness probe failed: error=%s",
                exc.__class__.__name__,
              )
              continue
            if connected:
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
          if (
            silence_confirmations
            >= MARKET_STREAM_NATIVE_SILENCE_CONFIRMATIONS
          ):
            reset_reason = (
              "XTData callback silence confirmed during trading session: "
              f"seconds={silence_seconds:.3f}"
            )
            break

        if not reset_reason:
          return
        logger.error("QMT whole-market native subscription reset: %s", reset_reason)
        self._market_stream_status = "STALE"
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
          self._whole_market_subscription_ready.clear()
          self._whole_market_capture.reset_source(
            "whole-market native subscription attempt failed: "
            f"{exc.__class__.__name__}"
          )
        logger.warning(
          "QMT whole-market subscription retry: error=%s: %s",
          exc.__class__.__name__,
          exc,
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
    if ready_seconds >= MARKET_STREAM_STABLE_READY_SECONDS:
      return 1.0, 1.0
    return current_delay, min(current_delay * 2, 30.0)

  async def _wait_for_fresh_access_token(self) -> None:
    while True:
      if (
        self._access_token
        and self._access_token_expires_at
        > datetime.now(timezone.utc) + timedelta(seconds=5)
      ):
        return
      self._access_token_ready.clear()
      if (
        self._access_token
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
        self._market_stream_status = "OFFLINE"
        raise
      except Exception as exc:
        ready_since = self._market_stream_ready_since_monotonic
        ready_seconds = (
          max(0.0, time.monotonic() - ready_since)
          if ready_since > 0
          else 0.0
        )
        sleep_delay, delay = self._market_stream_retry_delay(
          delay,
          ready_seconds=ready_seconds,
        )
        self._market_stream_resyncs += 1
        self._market_stream_status = "SYNCING"
        self._market_stream_ready_since_monotonic = 0.0
        logger.warning(
          "QMT whole-market stream reconnecting: resyncs=%s "
          "ready_seconds=%.3f error=%s: %s",
          self._market_stream_resyncs,
          ready_seconds,
          exc.__class__.__name__,
          exc,
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
        "agent_version": "0.1.0",
        "capabilities": ["market-data", self.mode],
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
      raise RuntimeError(
        str(auth_result.payload.get("reason") or "market authentication rejected")
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
    market_token_expires_at = self._access_token_expires_at
    self._market_stream_status = "SYNCING"
    self._market_stream_sequence = 0
    self._market_stream_ready_since_monotonic = 0.0
    self._market_stream_outbound_depth = 0
    self._market_stream_outbound_bytes = 0
    await self._whole_market_subscription_ready.wait()
    self._whole_market_native_reset.clear()
    self._whole_market_capture.begin_syncing()
    async with websockets.connect(
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
      snapshot_raw, snapshot_watermark = (
        await self._build_whole_market_snapshot(stream_trading_date)
      )
      self._require_native_whole_market_sync("snapshot-build")
      snapshot = await self._prepare_encoded_market_batch(
        stream_id=start.stream_id,
        sequence=1,
        kind=MarketBatchKind.SNAPSHOT,
        captured_at=datetime.now(timezone.utc),
        raw_data=snapshot_raw,
      )
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
        barrier_tasks.extend(
          [native_reset, capture_invalidated, barrier_ack]
        )
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
        renewal = asyncio.create_task(
          self._close_before_token_expiry(
            socket,
            expires_at=market_token_expires_at,
          ),
          name="whole-market-token-renewal",
        )
        native_reset = asyncio.create_task(
          self._whole_market_native_reset.wait(),
          name="whole-market-native-reset",
        )
        capture_invalidated = asyncio.create_task(
          self._whole_market_capture.wait_until_invalidated(),
          name="whole-market-capture-invalidated",
        )
        pipeline_tasks.extend(
          [producer, transport, renewal, native_reset, capture_invalidated]
        )
        done, _ = await asyncio.wait(
          {
            producer,
            transport,
            renewal,
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
        await renewal
        raise RuntimeError("whole-market access token renewal closed the stream")
      finally:
        for task in pipeline_tasks:
          if not task.done():
            task.cancel()
        if pipeline_tasks:
          await asyncio.gather(*pipeline_tasks, return_exceptions=True)
        self._whole_market_capture.begin_syncing()
        self._market_stream_status = "SYNCING"
        self._market_stream_ready_since_monotonic = 0.0

  def _require_native_whole_market_sync(self, stage: str) -> None:
    if (
      self._whole_market_native_reset.is_set()
      or not self._whole_market_subscription_ready.is_set()
    ):
      capture = getattr(self, "_whole_market_capture", None)
      reason = (
        capture.invalidation_reason if capture is not None else ""
      )
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

  @classmethod
  def _merge_whole_market_snapshot(
    cls,
    fallback: dict[str, Any],
    callback_latest: dict[str, Any],
    *,
    callback_capture_sequences: dict[str, int] | None = None,
    callback_captured_monotonic: dict[str, float] | None = None,
    fallback_completion_sequences: dict[str, int] | None = None,
    fallback_completed_monotonic: dict[str, float] | None = None,
  ) -> dict[str, Any]:
    callback_sequences = callback_capture_sequences or {}
    callback_monotonic = callback_captured_monotonic or {}
    fallback_sequences = fallback_completion_sequences or {}
    fallback_monotonic = fallback_completed_monotonic or {}
    merged = dict(fallback)
    for code, latest_tick in callback_latest.items():
      fallback_tick = merged.get(code)
      if fallback_tick is None:
        merged[code] = latest_tick
        continue
      latest_time = cls._whole_market_tick_source_time(latest_tick)
      fallback_time = cls._whole_market_tick_source_time(fallback_tick)
      if latest_time > 0 and (
        fallback_time <= 0 or latest_time >= fallback_time
      ):
        merged[code] = latest_tick
        continue
      if latest_time > 0 or fallback_time > 0:
        continue
      callback_sequence = callback_sequences.get(code, 0)
      fallback_sequence = fallback_sequences.get(code, 0)
      callback_completed_at = callback_monotonic.get(code, 0.0)
      fallback_completed_at = fallback_monotonic.get(code, 0.0)
      if (
        callback_sequence > fallback_sequence > 0
        or (
          fallback_sequence <= 0
          and callback_completed_at > fallback_completed_at > 0
        )
      ):
        # Neither side exposes a source timestamp. A callback captured only
        # after this code's native fragment completed is strictly newer than
        # the fallback and must be included before the global watermark cut.
        merged[code] = latest_tick
    return merged

  async def _build_whole_market_snapshot(
    self,
    trading_date: date,
  ) -> tuple[dict[str, Any], int]:
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
    deadline = time.monotonic() + MARKET_STREAM_INITIAL_PUSH_WAIT_SECONDS
    latest = self._whole_market_capture.latest_snapshot(
      trading_date=trading_date
    )
    while expected_codes and not expected_codes.issubset(latest.data):
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
      latest = self._whole_market_capture.latest_snapshot(
        trading_date=trading_date
      )
    if expected_codes and expected_codes.issubset(latest.data):
      logger.info(
        "QMT initial whole-quote push covers the full universe: instruments=%s",
        len(latest.data),
      )
      return latest.data, latest.capture_watermark

    chunk_reader = getattr(
      self.broker,
      "whole_market_snapshot_chunk",
      None,
    )
    fallback_completion_sequences: dict[str, int] = {}
    fallback_completed_monotonic: dict[str, float] = {}
    if callable(chunk_reader) and expected_codes:
      fallback: dict[str, Any] = {}
      ordered_codes = tuple(
        code for code in expected_values if code in expected_codes
      )
      for start in range(
        0,
        len(ordered_codes),
        WHOLE_QUOTE_SNAPSHOT_BATCH_SIZE,
      ):
        batch = list(
          ordered_codes[start : start + WHOLE_QUOTE_SNAPSHOT_BATCH_SIZE]
        )

        def read_fragment():
          fragment = chunk_reader(batch)
          return (
            fragment,
            self._whole_market_capture.capture_sequence,
            time.monotonic(),
          )

        fragment, completion_sequence, completed_monotonic = (
          await self._run_xtdata_control(
            "whole-market-snapshot:"
            f"{start // WHOLE_QUOTE_SNAPSHOT_BATCH_SIZE}",
            read_fragment,
          )
        )
        if not isinstance(fragment, dict):
          raise RuntimeError("XTData returned an invalid snapshot fragment")
        fallback.update(fragment)
        for code in batch:
          fallback_completion_sequences[code] = completion_sequence
          fallback_completed_monotonic[code] = completed_monotonic
        # Each fragment has its own native timeout. Yield explicitly between
        # calls so heartbeats and WebSocket control tasks remain schedulable.
        await asyncio.sleep(0)
    else:
      def read_snapshot():
        snapshot = self.broker.whole_market_snapshot()
        return (
          snapshot,
          self._whole_market_capture.capture_sequence,
          time.monotonic(),
        )

      fallback, completion_sequence, completed_monotonic = (
        await self._run_xtdata_control(
          "whole-market-snapshot",
          read_snapshot,
        )
      )
      for code in expected_codes:
        fallback_completion_sequences[code] = completion_sequence
        fallback_completed_monotonic[code] = completed_monotonic
    if not isinstance(fallback, dict) or not fallback:
      raise RuntimeError("XTData returned an empty SH/SZ snapshot")
    latest = self._whole_market_capture.latest_snapshot(
      trading_date=trading_date
    )
    merged = self._merge_whole_market_snapshot(
      fallback,
      latest.data,
      callback_capture_sequences=latest.capture_sequences,
      callback_captured_monotonic=latest.captured_monotonic,
      fallback_completion_sequences=fallback_completion_sequences,
      fallback_completed_monotonic=fallback_completed_monotonic,
    )
    missing_codes = expected_codes.difference(merged)
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
      "QMT whole-market snapshot used get_full_tick fallback: "
      "expected=%s callback_coverage=%s merged=%s",
      len(expected_codes),
      len(latest.data),
      len(merged),
    )
    return merged, latest.capture_watermark

  async def _prepare_encoded_market_batch(
    self,
    *,
    stream_id: str,
    sequence: int,
    kind: MarketBatchKind,
    captured_at: datetime,
    raw_data: dict[str, Any],
  ) -> _EncodedMarketBatch:
    def prepare() -> _EncodedMarketBatch:
      validation_reference_at = datetime.now(timezone.utc)
      data = self.broker.prepare_whole_market_data(raw_data)
      if not isinstance(data, dict) or (
        kind is MarketBatchKind.SNAPSHOT and not data
      ):
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
      batch = MarketStreamBatch(
        stream_id=stream_id,
        sequence=sequence,
        kind=kind,
        captured_at=captured_at,
        instrument_count=len(data),
        data=data,
      )
      try:
        payload = batch.to_bytes()
      except ValueError as exc:
        raise RuntimeError(
          "whole-market batch encoding failed: "
          f"stream_id={stream_id} sequence={sequence} "
          f"kind={kind.value} instruments={len(data)} error={exc}"
        ) from exc
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
          values = dict(
            items[offset : offset + MARKET_STREAM_MICROBATCH_INSTRUMENTS]
          )
          fragment_estimated_bytes = max(
            1024,
            (
              event.estimated_bytes * len(values)
              + max(1, len(items))
              - 1
            )
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
        if (
          candidate.captured_at.astimezone(SHANGHAI_ZONE).date()
          != trading_date
        ):
          raise RuntimeError("trading day changed; full snapshot required")
        if (
          codes.intersection(candidate.data)
          or len(raw_data) + len(candidate.data)
          > MARKET_STREAM_MICROBATCH_INSTRUMENTS
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
    pending: deque[_PendingMarketAck] = deque()
    receive_task = asyncio.create_task(socket.recv())
    get_task: asyncio.Task[_EncodedMarketBatch] | None = None
    try:
      while True:
        if (
          len(pending) < MARKET_STREAM_MAX_UNACKNOWLEDGED_BATCHES
          and get_task is None
        ):
          get_task = asyncio.create_task(outbound.get())
        waiters: set[asyncio.Task[Any]] = {receive_task}
        if get_task is not None:
          waiters.add(get_task)
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
          expected_sequence = (
            pending[0].encoded.batch.sequence if pending else 0
          )
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
          latency_ms = (
            time.monotonic() - expected.sent_monotonic
          ) * 1000
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
            self._market_stream_status = "READY"
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
    finally:
      tasks = [receive_task]
      if get_task is not None:
        tasks.append(get_task)
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
    self._market_stream_ack_latency_ms = (
      time.monotonic() - started
    ) * 1000

  async def _close_before_token_expiry(
    self,
    socket,
    *,
    expires_at: datetime | None = None,
  ) -> None:
    renew_at = (
      expires_at or self._access_token_expires_at
    ) - timedelta(minutes=2)
    delay = max(
      1.0,
      (renew_at - datetime.now(timezone.utc)).total_seconds(),
    )
    await asyncio.sleep(delay)
    await socket.close(code=4001, reason="refreshing Agent access token")

  async def _send_heartbeat(self, socket, *, status: str) -> None:
    self._ensure_market_upload_state()
    if not self._is_market_data_ready():
      status = "XTDATA_UNAVAILABLE"
    if not self._is_trading_ready():
      status = "TRADING_UNAVAILABLE"
    if self.emergency_stop and self.emergency_stop.status()["active"]:
      status = "EMERGENCY_STOP"
    journal_stats = self.journal.stats()
    payload = HeartbeatPayload(
      device_id=self.configuration.device_id,
      agent_version="0.1.0",
      capabilities=[
        "market-data",
        "divid-factors",
        "financial-data-v1",
        self.mode,
      ],
      status=status,
      journal_integrity=str(journal_stats["integrity"]),
      journal_size_bytes=int(journal_stats["size_bytes"]),
      journal_pending_reports=int(journal_stats["pending_reports"]),
      journal_processing_commands=int(
        journal_stats["processing_commands"]
      ),
      market_stream_status=self._market_stream_status,
      market_stream_sequence=self._market_stream_sequence,
      market_stream_queue_depth=(
        self._whole_market_capture.queue_depth
        + self._market_stream_outbound_depth
      ),
      market_stream_resyncs=self._market_stream_resyncs,
      market_stream_ack_latency_ms=self._market_stream_ack_latency_ms,
    )
    await self._send_socket_text(
      socket,
      AgentEnvelope(
        message_type=AgentMessageType.HEARTBEAT,
        payload=payload.model_dump(mode="json"),
      ).model_dump_json(),
    )

  async def _heartbeat_checkpoint(self, socket, *, status: str) -> None:
    async with self._heartbeat_checkpoint_lock:
      await self._send_heartbeat(socket, status=status)
      await self._flush_reports(socket)

  async def _send_socket_text(self, socket, serialized: str) -> None:
    async with self._websocket_send_lock:
      await asyncio.wait_for(
        socket.send(serialized),
        timeout=WEBSOCKET_SEND_TIMEOUT_SECONDS,
      )

  async def _handle_message(self, socket, raw_message: str) -> None:
    envelope = AgentEnvelope.model_validate_json(raw_message)
    if envelope.message_type in {
      AgentMessageType.HEARTBEAT_ACK,
      AgentMessageType.AUTH_RESULT,
    }:
      return
    if envelope.message_type is AgentMessageType.REPORT_ACK:
      if envelope.payload.get("accepted"):
        self.journal.acknowledge_report(
          str(envelope.payload.get("report_message_id", ""))
        )
      return
    if envelope.message_type in {
      AgentMessageType.COMMAND,
      AgentMessageType.CANCEL_COMMAND,
    }:
      await self._handle_command(socket, envelope)
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
      try:
        self._market_requests.put_nowait(envelope)
      except asyncio.QueueFull:
        logger.warning("QMT market-data request queue is full")
        if socket is not None:
          await socket.close(
            code=1013,
            reason="market-data request queue full",
          )
      return
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
          "subscription_id=%s error=%s: %s",
          envelope.payload.get("subscription_id"),
          exc.__class__.__name__,
          exc,
        )
        return
      if not accepted:
        logger.warning(
          "XTData subscription rejected: subscription_id=%s",
          envelope.payload.get("subscription_id"),
        )
      return
    if envelope.message_type is AgentMessageType.MARKET_UNSUBSCRIBE:
      await self._run_xtdata_control(
        "unsubscribe-market",
        self.broker.unsubscribe_market,
        str(envelope.payload.get("subscription_id") or ""),
      )
      return
    logger.warning("Unsupported Agent message: %s", envelope.message_type.value)

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

  async def _handle_command(self, socket, envelope: AgentEnvelope) -> None:
    payload = envelope.payload
    state, previous = self.journal.begin_command(
      envelope.message_id,
      payload,
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
        reason="local_reconciliation_required",
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
        envelope.message_type is not AgentMessageType.CANCEL_COMMAND
        and self.emergency_stop
        and self.emergency_stop.status()["active"]
      ):
        rejection = "local_emergency_stop"
    except ValidationError:
      rejection = "invalid_command_payload"
    except (TypeError, ValueError):
      rejection = "invalid_command_expiry"

    if rejection:
      error_key = (
        "cancel_errors"
        if envelope.message_type is AgentMessageType.CANCEL_COMMAND
        else "order_errors"
      )
      result = {
        "accepted": False,
        "reason": rejection,
        "reports": [
          (
            AgentMessageType.DELTA_REPORT.value,
            {
              error_key: [
                {
                  "client_order_id": payload.get("client_order_id"),
                  "account_id": account_id,
                  "reason": rejection,
                  "error_msg": rejection,
                }
              ],
              "sequence": int(
                datetime.now(timezone.utc).timestamp() * 1_000_000
              ),
              "is_complete": False,
            },
          )
        ],
      }
    elif emergency_command:
      if self.emergency_stop is None:
        result = {
          "accepted": False,
          "reason": "emergency_store_unavailable",
          "reports": [],
        }
      else:
        self.emergency_stop.activate(str(payload.get("reason") or ""))
        result = {
          "accepted": True,
          "reason": "local_emergency_stop_activated",
          "reports": [],
        }
    else:
      result = (
        await self._run_xtdata_control(
          "live-command-market-preflight",
          self.broker.execute,
          payload,
        )
        if self.mode == "live"
        else await asyncio.to_thread(self.broker.execute, payload)
      )
    self.journal.complete_command(envelope.message_id, result)

    for message_type, report_payload in result.get("reports") or []:
      report = AgentEnvelope(
        message_type=AgentMessageType(message_type),
        payload=enrich_report_payload(
          AgentMessageType(message_type),
          report_payload,
        ),
      )
      self.journal.add_report(report.message_id, report.model_dump_json())
    await self._command_ack(
      socket,
      envelope,
      accepted=bool(result.get("accepted")),
      reason=str(result.get("reason", "")),
    )
    await self._flush_reports(socket)

  async def _handle_market_data_request(
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
    async with httpx.AsyncClient(timeout=60.0) as client:
      for index, chunk in enumerate(chunks):
        self._touch_market_upload(request_id)
        response = await client.put(
          (
            f"{self.configuration.api_url}/agent/market-data/"
            f"{request_id}/chunks/{index}"
          ),
          content=_stream_spool_chunk(chunk.path),
          headers={
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "Content-Length": str(chunk.compressed_bytes),
            "X-Content-SHA256": chunk.digest,
            "X-Record-Count": str(chunk.record_count),
            "X-Total-Chunks": str(len(chunks)),
          },
        )
        try:
          response.raise_for_status()
        except httpx.HTTPStatusError as exc:
          if exc.response.status_code == 409:
            fatal = _FatalMarketDataUploadConflict(
              "market-data chunk identity conflict; Agent restart required"
            )
            self._trip_market_data_fatal(fatal)
            raise fatal from exc
          raise
        self._touch_market_upload(request_id)
    await self._complete_market_upload(request_id)

  async def _prepared_market_data_chunks(
    self,
    request_id: str,
    payload: dict[str, Any],
  ) -> tuple[_MarketDataSpoolChunk, ...]:
    self._ensure_market_upload_state()
    if self._fatal_market_data_error is not None:
      raise self._fatal_market_data_error
    payload_fingerprint = _market_data_payload_fingerprint(payload)
    now = self._market_upload_clock()
    self._cleanup_expired_market_uploads(now)

    tombstone = self._market_upload_tombstones.get(request_id)
    if tombstone is not None:
      tombstone.last_access_at = now
      if tombstone.fingerprint != payload_fingerprint:
        raise RuntimeError(
          "同一 market-data request_id 的重投参数不一致"
        )
      raise _MarketDataRequestAlreadyCompleted(
        "market-data request was already uploaded"
      )

    cached = self._market_upload_cache.get(request_id)
    if cached is not None:
      cached.last_access_at = now
      if cached.fingerprint != payload_fingerprint:
        raise RuntimeError(
          "同一 market-data request_id 的重投参数不一致"
        )
      task = cached.task
      if task is None:
        raise RuntimeError("market-data preparation state is incomplete")
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

  async def _prepare_and_cache_market_data(
    self,
    request_id: str,
    entry: _MarketUploadCacheEntry,
    payload: dict[str, Any],
  ) -> _PreparedMarketData:
    # A session cancellation must never cancel this task. The lock also keeps
    # a lingering XTData call from overlapping a different request after a
    # reconnect because XTData's cache mutation is not thread-safe.
    try:
      async with self._xtdata_access_lock:
        managed_spool_bytes = await asyncio.to_thread(
          _managed_market_data_spool_bytes,
          self._market_spool_root,
        )
        remaining_cache_bytes = (
          MAX_MARKET_DATA_UPLOAD_CACHE_BYTES
          - managed_spool_bytes
        )
        if remaining_cache_bytes <= 0:
          raise RuntimeError(
            "market-data upload cache byte limit exceeded"
          )
        compressed_budget = min(
          MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES,
          remaining_cache_bytes,
        )
        try:
          prepared = await self._run_market_data_preparation_daemon(
            request_id,
            payload,
            max_total_uncompressed_bytes=(
              MAX_MARKET_DATA_REQUEST_UNCOMPRESSED_BYTES
            ),
            max_total_compressed_bytes=compressed_budget,
          )
        except ValueError as exc:
          if (
            compressed_budget == remaining_cache_bytes
            and remaining_cache_bytes
            < MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES
            and "compressed byte limit" in str(exc)
          ):
            raise RuntimeError(
              "market-data upload cache byte limit exceeded"
            ) from exc
          raise

      cached = self._market_upload_cache.get(request_id)
      if cached is not entry:
        self._remove_prepared_market_data(prepared)
        raise RuntimeError("market-data preparation was retired")
      next_cache_bytes = (
        self._market_upload_cache_bytes + prepared.compressed_bytes
      )
      if next_cache_bytes > MAX_MARKET_DATA_UPLOAD_CACHE_BYTES:
        self._remove_prepared_market_data(prepared)
        raise RuntimeError("market-data upload cache byte limit exceeded")
      entry.compressed_bytes = prepared.compressed_bytes
      entry.last_access_at = self._market_upload_clock()
      self._market_upload_cache_bytes = next_cache_bytes
      return prepared
    except BaseException:
      if self._market_upload_cache.get(request_id) is entry:
        self._drop_market_upload_cache_entry(request_id)
      raise

  async def _run_market_data_preparation_daemon(
    self,
    request_id: str,
    payload: dict[str, Any],
    *,
    max_total_uncompressed_bytes: int,
    max_total_compressed_bytes: int,
  ) -> _PreparedMarketData:
    """Run native XTData + bounded spooling in a fail-stop daemon thread."""
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
      spool_directory = Path(
        tempfile.mkdtemp(
          prefix=MARKET_DATA_SPOOL_REQUEST_PREFIX,
          dir=self._market_spool_root,
        )
      )
      try:
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
    """Initialize upload state for old harnesses that construct via __new__."""
    if not hasattr(self, "_stopped"):
      self._stopped = asyncio.Event()
    if not hasattr(self, "_fatal_market_data_error"):
      self._fatal_market_data_error = None
    if not hasattr(self, "_fatal_market_data_event"):
      self._fatal_market_data_event = asyncio.Event()
    if not hasattr(self, "_market_spool_root"):
      ephemeral_base = Path(
        tempfile.mkdtemp(prefix="quantx-qmt-agent-test-")
      )
      self._market_spool_root = _initialize_market_data_spool_root(
        ephemeral_base,
        f"test-{uuid.uuid4()}",
      )
      self._market_spool_ephemeral_base = ephemeral_base
    elif not hasattr(self, "_market_spool_ephemeral_base"):
      self._market_spool_ephemeral_base = None
    if not hasattr(self, "_market_upload_cache"):
      self._market_upload_cache = {}
    if not hasattr(self, "_market_upload_tombstones"):
      self._market_upload_tombstones = {}
    if not hasattr(self, "_market_upload_tasks"):
      self._market_upload_tasks = {}
    if not hasattr(self, "_market_upload_cache_bytes"):
      self._market_upload_cache_bytes = 0
    if not hasattr(self, "_xtdata_access_lock"):
      self._xtdata_access_lock = asyncio.Lock()
    if not hasattr(self, "_websocket_send_lock"):
      self._websocket_send_lock = asyncio.Lock()
    if not hasattr(self, "_heartbeat_checkpoint_lock"):
      self._heartbeat_checkpoint_lock = asyncio.Lock()
    if not hasattr(self, "_market_upload_clock"):
      self._market_upload_clock = time.monotonic
    if not hasattr(self, "_session_loop"):
      self._session_loop = None
    if not hasattr(self, "_market_event_overflow"):
      self._market_event_overflow = asyncio.Event()
    self._ensure_whole_market_state()
    if not hasattr(self, "_market_stream_resyncs"):
      self._market_stream_resyncs = 0
    if not hasattr(self, "_market_stream_status"):
      self._market_stream_status = "OFFLINE"
    if not hasattr(self, "_market_stream_sequence"):
      self._market_stream_sequence = 0
    if not hasattr(self, "_market_stream_ack_latency_ms"):
      self._market_stream_ack_latency_ms = 0.0

  def _ensure_whole_market_state(self) -> None:
    """Initialize capture state for focused harnesses using ``__new__``."""
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

  def _cleanup_expired_market_uploads(self, now: float | None = None) -> None:
    self._ensure_market_upload_state()
    current = self._market_upload_clock() if now is None else now
    expired_request_ids = [
      request_id
      for request_id, entry in self._market_upload_cache.items()
      if (
        entry.task is not None
        and entry.task.done()
        and current - entry.last_access_at
        >= MARKET_DATA_UPLOAD_CACHE_TTL_SECONDS
      )
    ]
    for request_id in expired_request_ids:
      self._drop_market_upload_cache_entry(request_id)

    expired_tombstones = [
      request_id
      for request_id, tombstone in self._market_upload_tombstones.items()
      if (
        current - tombstone.last_access_at
        >= MARKET_DATA_UPLOAD_CACHE_TTL_SECONDS
      )
    ]
    for request_id in expired_tombstones:
      self._market_upload_tombstones.pop(request_id, None)

  def _drop_market_upload_cache_entry(
    self,
    request_id: str,
    *,
    cancel: bool = False,
    remove_prepared: bool = True,
  ) -> _MarketUploadCacheEntry | None:
    entry = self._market_upload_cache.pop(request_id, None)
    if entry is None:
      return None
    self._market_upload_cache_bytes = max(
      0,
      self._market_upload_cache_bytes - entry.compressed_bytes,
    )
    if cancel and entry.task is not None and not entry.task.done():
      entry.task.cancel()
    elif (
      remove_prepared
      and entry.task is not None
      and entry.task.done()
    ):
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

  async def _complete_market_upload(self, request_id: str) -> None:
    self._ensure_market_upload_state()
    now = self._market_upload_clock()
    entry = self._drop_market_upload_cache_entry(
      request_id,
      remove_prepared=False,
    )
    if entry is None:
      return
    self._market_upload_tombstones[request_id] = _MarketUploadTombstone(
      fingerprint=entry.fingerprint,
      completed_at=now,
      last_access_at=now,
    )
    while len(self._market_upload_tombstones) > MAX_MARKET_DATA_TOMBSTONES:
      oldest_request_id = next(iter(self._market_upload_tombstones))
      self._market_upload_tombstones.pop(oldest_request_id, None)
    if entry.task is not None and entry.task.done():
      try:
        prepared = entry.task.result()
      except BaseException:
        return
      await asyncio.to_thread(self._remove_prepared_market_data, prepared)

  def _clear_market_upload_state(self) -> None:
    self._ensure_market_upload_state()
    active_uploads = [
      upload.task
      for upload in self._market_upload_tasks.values()
      if not upload.task.done()
    ]
    if active_uploads:
      raise RuntimeError(
        "market-data uploads must stop before clearing their spool"
      )
    self._market_upload_tasks.clear()
    for request_id in list(self._market_upload_cache):
      self._drop_market_upload_cache_entry(request_id, cancel=True)
    self._market_upload_cache_bytes = 0
    self._market_upload_tombstones.clear()
    if self._market_spool_ephemeral_base is not None:
      shutil.rmtree(self._market_spool_ephemeral_base, ignore_errors=True)
      self._market_spool_ephemeral_base = None

  async def _cancel_market_upload_tasks(self) -> None:
    self._ensure_market_upload_state()
    tasks = [
      upload.task for upload in self._market_upload_tasks.values()
    ]
    for task in tasks:
      if not task.done():
        task.cancel()
    if tasks:
      await asyncio.gather(*tasks, return_exceptions=True)
    self._market_upload_tasks.clear()

  async def _market_request_loop(self, socket) -> None:
    while True:
      envelope = await self._market_requests.get()
      request_id = str(envelope.payload.get("request_id") or "")
      upload_task: asyncio.Task[None] | None = None
      try:
        upload_task = self._market_upload_task(envelope)
        await asyncio.shield(upload_task)
        # The 90-second server freshness window may expire while XTData holds
        # the GIL. Refresh it before this serial worker starts another request.
        await self._heartbeat_checkpoint(socket, status="READY")
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
        logger.warning(
          "QMT market data request failed: request_id=%s error=%s: %s",
          request_id,
          exc.__class__.__name__,
          exc,
        )
        try:
          await self._report_market_data_failure(request_id, exc)
        except Exception as report_exc:
          logger.warning(
            "Could not report QMT market data failure: request_id=%s "
            "error=%s: %s",
            request_id,
            report_exc.__class__.__name__,
            report_exc,
          )
      finally:
        self._market_requests.task_done()

  async def _report_market_data_failure(
    self,
    request_id: str,
    error: Exception,
  ) -> None:
    reason = f"{error.__class__.__name__}: {error}"[:900]
    async with httpx.AsyncClient(timeout=10.0) as client:
      response = await client.post(
        (
          f"{self.configuration.api_url}/agent/market-data/"
          f"{request_id}/fail"
        ),
        headers={"Authorization": f"Bearer {self._access_token}"},
        json={"reason": reason},
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
        raise RuntimeError(
          "同一 market-data request_id 的重投参数不一致"
        )
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
    for upload in self._market_upload_tasks.values():
      if not upload.task.done():
        upload.task.cancel()
    if not any(
      not upload.task.done()
      for upload in self._market_upload_tasks.values()
    ):
      self._clear_market_upload_state()
