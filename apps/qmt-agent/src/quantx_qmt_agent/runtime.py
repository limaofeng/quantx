"""Authenticated outbound WebSocket runtime for the QMT Agent."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import shutil
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, BinaryIO, Iterable, Iterator

import httpx
import websockets
from pydantic import ValidationError
from quantx_contracts import (
  AgentEnvelope,
  AgentMessageType,
  CancelCommandPayload,
  HeartbeatPayload,
  TradeCommandPayload,
)

from .broker import MAX_MARKET_DATA_RECORDS, enrich_report_payload
from .credentials import DeviceConfiguration, state_directory
from .emergency import EmergencyStopStore
from .journal import LocalJournal

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
MARKET_DATA_UPLOAD_READ_BYTES = 256 * 1024
MARKET_DATA_SPOOL_DIRECTORY_NAME = "market-data-spool"
MARKET_DATA_SPOOL_REQUEST_PREFIX = "request-"
MARKET_DATA_SPOOL_OWNER_MARKER = ".owner.json"
LEGACY_MARKET_DATA_SPOOL_PREFIX = "quantx-market-data-spool-"


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


def _websocket_url(api_url: str) -> str:
  if api_url.startswith("https://"):
    return f"wss://{api_url[8:].rstrip('/')}/ws/agent"
  if api_url.startswith("http://"):
    return f"ws://{api_url[7:].rstrip('/')}/ws/agent"
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
    self._session_loop: asyncio.AbstractEventLoop | None = None
    self._market_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
      maxsize=10_000
    )
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
    cache_sweeper = asyncio.create_task(
      self._market_upload_cache_sweeper(),
      name="market-data-cache-sweeper",
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
      cache_sweeper.cancel()
      await asyncio.gather(cache_sweeper, return_exceptions=True)
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
    self._access_token, self._access_token_expires_at = await self._issue_token()
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

      self._session_loop = asyncio.get_running_loop()
      self._market_requests = asyncio.Queue(
        maxsize=MAX_QUEUED_MARKET_DATA_REQUESTS
      )
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
        try:
          self._market_events.get_nowait()
        except asyncio.QueueEmpty:
          pass
      self._market_events.put_nowait(payload)

    loop.call_soon_threadsafe(enqueue)

  async def _market_event_loop(self, socket) -> None:
    while True:
      payload = await self._market_events.get()
      await self._send_socket_text(
        socket,
        AgentEnvelope(
          message_type=AgentMessageType.MARKET_EVENT,
          payload=payload,
        ).model_dump_json(),
      )

  async def _close_before_token_expiry(self, socket) -> None:
    renew_at = self._access_token_expires_at - timedelta(minutes=2)
    delay = max(
      1.0,
      (renew_at - datetime.now(timezone.utc)).total_seconds(),
    )
    await asyncio.sleep(delay)
    await socket.close(code=4001, reason="refreshing Agent access token")

  async def _send_heartbeat(self, socket, *, status: str) -> None:
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
      capabilities=["market-data", "divid-factors", self.mode],
      status=status,
      journal_integrity=str(journal_stats["integrity"]),
      journal_size_bytes=int(journal_stats["size_bytes"]),
      journal_pending_reports=int(journal_stats["pending_reports"]),
      journal_processing_commands=int(
        journal_stats["processing_commands"]
      ),
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
