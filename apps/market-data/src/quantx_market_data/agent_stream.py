"""Dedicated whole-market receive/commit pipeline with shared device authentication."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import (
  APIRouter,
  WebSocket,
  WebSocketDisconnect,
)
from quantx_contracts import (
  MARKET_STREAM_MARKETS,
  MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS,
  MARKET_STREAM_SUBPROTOCOL,
  MAX_MARKET_STREAM_FRAME_BYTES,
  PROTOCOL_VERSION,
  AgentEnvelope,
  AgentMessageType,
  MarketBatchKind,
  MarketControlType,
  MarketStreamBatch,
  MarketStreamControl,
)
from quantx_infrastructure.auth.agent_access import authenticate_agent_session
from quantx_infrastructure.auth.errors import AuthError
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.core.data.market_stream_transport import (
  MarketStreamStore,
  market_stream_store,
)
from quantx_infrastructure.database.redis_pubsub import (
  redis_pubsub,
)
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
)
from quantx_infrastructure.services.agent_session_guard import (
  QMT_DEVICE_REVOKED,
)
from redis.exceptions import RedisError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.websockets import WebSocketState

from .metrics import (
  MARKET_STREAM_CONNECTIONS,
  MARKET_STREAM_EVENTS,
  MARKET_STREAM_FRAME_BYTES,
  MARKET_STREAM_FRAMES,
  MARKET_STREAM_INSTRUMENTS,
  MARKET_STREAM_PROCESSING,
  MARKET_STREAM_RESYNCS,
  MARKET_STREAM_SEQUENCE,
)

logger = logging.getLogger(__name__)

_TRANSIENT_DATABASE_ERRORS = (SQLAlchemyTimeoutError, DBAPIError)


_TRANSIENT_DEPENDENCY_ERRORS = _TRANSIENT_DATABASE_ERRORS + (
  ConnectionError,
  asyncio.TimeoutError,
  RedisError,
)


def _auth_result(
  *,
  accepted: bool,
  reason: str = "",
  reason_code: str = "",
  protocol_version: str = PROTOCOL_VERSION,
  agent_session_id: str = "",
) -> AgentEnvelope:
  payload = {"accepted": accepted, "reason": reason}
  if reason_code:
    payload["reason_code"] = reason_code
  if agent_session_id:
    payload["agent_session_id"] = agent_session_id
  return AgentEnvelope(
    protocol_version=protocol_version,
    message_type=AgentMessageType.AUTH_RESULT,
    payload=payload,
  )


def _normalized_agent_capabilities(values: Any) -> set[str]:
  capabilities = {
    str(value).strip().lower() for value in values or [] if str(value).strip()
  }
  execution_modes = capabilities & {"live", "paper", "data-only"}
  if len(execution_modes) != 1:
    raise AuthError("FORBIDDEN", "Agent 必须声明唯一运行模式")
  return capabilities


async def _authenticate(envelope: AgentEnvelope):
  if envelope.message_type is not AgentMessageType.AUTH:
    raise AuthError("UNAUTHENTICATED", "首条 Agent 消息必须是 auth")
  token = str(envelope.payload.get("access_token", ""))
  device_id = str(envelope.payload.get("device_id", ""))
  if not token or not device_id:
    raise AuthError("UNAUTHENTICATED", "Agent auth 缺少设备或令牌")
  async with AsyncSessionLocal() as db:
    return await authenticate_agent_session(
      db,
      settings,
      token=token,
      expected_device_id=device_id,
    )


market_agent_router = APIRouter(tags=["qmt-market-agent"])


MARKET_STREAM_COMMIT_QUEUE_CAPACITY = 2


MARKET_STREAM_COMMIT_QUEUE_MAX_BYTES = MAX_MARKET_STREAM_FRAME_BYTES


MARKET_STREAM_DECODE_OFFLOAD_BYTES = 256 * 1024


MARKET_STREAM_DEVICE_REVALIDATE_SECONDS = 5.0


MARKET_STREAM_REDIS_COMMIT_TIMEOUT_SECONDS = 5.0


MARKET_STREAM_REDIS_CLEANUP_TIMEOUT_SECONDS = 2.0


MARKET_STREAM_CONTROL_SEND_TIMEOUT_SECONDS = 2.0


MARKET_STREAM_EVENT_QUEUE_CAPACITY = 512


MARKET_STREAM_EVENT_QUEUE_MAX_BYTES = 8 * 1024 * 1024


MARKET_STREAM_EVENT_MAX_FRAME_BYTES = 64 * 1024


MARKET_STREAM_EVENT_PROCESSING_TIMEOUT_SECONDS = 2.0


class _MarketConnectionRegistry:
  """Process-local single connection gate for the personal market Agent."""

  def __init__(self) -> None:
    self._lock = asyncio.Lock()
    self._connection_id = ""

  @property
  def active_stream_id(self) -> str:
    return self._connection_id

  async def register(self) -> str | None:
    async with self._lock:
      if self._connection_id:
        return None
      self._connection_id = str(uuid.uuid4())
      return self._connection_id

  async def unregister(self, connection_id: str) -> None:
    async with self._lock:
      if self._connection_id == connection_id:
        self._connection_id = ""


_market_connections = _MarketConnectionRegistry()


def active_market_stream_id() -> str:
  """Only the owning gateway process can attest to a live market connection."""
  return _market_connections.active_stream_id


@dataclass(frozen=True)
class _MarketCommitItem:
  batch: MarketStreamBatch
  payload: bytes
  received_at: datetime
  received_monotonic: float


@dataclass(frozen=True)
class _MarketCommitQueueClosed:
  disconnect: WebSocketDisconnect


@dataclass
class _MarketCommitState:
  last_sequence: int = 0


@dataclass(frozen=True)
class _MarketEventIngressItem:
  envelope: AgentEnvelope
  frame_bytes: int


class _MarketEventIngressBuffer:
  """Bound replaceable single-instrument events outside the binary ACK lane."""

  def __init__(
    self,
    *,
    capacity: int = MARKET_STREAM_EVENT_QUEUE_CAPACITY,
    max_bytes: int = MARKET_STREAM_EVENT_QUEUE_MAX_BYTES,
  ) -> None:
    if capacity <= 0 or max_bytes <= 0:
      raise ValueError("market event ingress limits must be positive")
    self._capacity = capacity
    self._max_bytes = max_bytes
    self._retained_bytes = 0
    self._queue: asyncio.Queue[_MarketEventIngressItem] = asyncio.Queue(
      maxsize=capacity
    )

  def put_latest(self, item: _MarketEventIngressItem) -> bool:
    if (
      item.frame_bytes <= 0
      or item.frame_bytes > MARKET_STREAM_EVENT_MAX_FRAME_BYTES
      or item.frame_bytes > self._max_bytes
    ):
      return False
    # Quotes are current state, not a durable event log. Retire stale queued
    # observations until the newest one fits both hard bounds.
    while (
      self._queue.full() or self._retained_bytes + item.frame_bytes > self._max_bytes
    ):
      try:
        retired = self._queue.get_nowait()
      except asyncio.QueueEmpty:
        return False
      self._retained_bytes -= retired.frame_bytes
      self._queue.task_done()
    self._queue.put_nowait(item)
    self._retained_bytes += item.frame_bytes
    return True

  async def get(self) -> _MarketEventIngressItem:
    item = await self._queue.get()
    self._retained_bytes -= item.frame_bytes
    return item

  def complete(self) -> None:
    self._queue.task_done()

  @property
  def qsize(self) -> int:
    return self._queue.qsize()

  @property
  def retained_bytes(self) -> int:
    return self._retained_bytes


class _MarketCommitBuffer:
  """Bound Redis work without silently dropping an accepted market frame."""

  def __init__(
    self,
    *,
    capacity: int = MARKET_STREAM_COMMIT_QUEUE_CAPACITY,
    max_bytes: int = MARKET_STREAM_COMMIT_QUEUE_MAX_BYTES,
  ) -> None:
    if capacity <= 0 or max_bytes <= 0:
      raise ValueError("market commit buffer limits must be positive")
    self._capacity = capacity
    self._max_bytes = max_bytes
    self._buffered_batches = 0
    self._buffered_bytes = 0
    self._condition = asyncio.Condition()
    self._queue: asyncio.Queue[_MarketCommitItem | _MarketCommitQueueClosed] = (
      asyncio.Queue(maxsize=capacity)
    )

  @property
  def buffered_batches(self) -> int:
    return self._buffered_batches

  @property
  def buffered_bytes(self) -> int:
    return self._buffered_bytes

  async def reserve(self) -> None:
    async with self._condition:
      await self._condition.wait_for(lambda: self._buffered_batches < self._capacity)
      self._buffered_batches += 1

  async def put_reserved(self, item: _MarketCommitItem) -> None:
    payload_bytes = len(item.payload)
    await self.reserve_payload(payload_bytes)
    try:
      self._queue.put_nowait(item)
    except BaseException:
      await self.release_payload(payload_bytes)
      raise

  async def put_pre_reserved(self, item: _MarketCommitItem) -> None:
    """Queue an item whose payload bytes were reserved before decoding."""

    self._queue.put_nowait(item)

  async def reserve_payload(self, payload_bytes: int) -> None:
    if payload_bytes < 0:
      raise ValueError("market frame byte size must be non-negative")
    async with self._condition:
      if self._buffered_bytes + payload_bytes > self._max_bytes:
        raise ValueError("market frame exceeds API commit buffer byte limit")
      self._buffered_bytes += payload_bytes

  async def release_payload(self, payload_bytes: int) -> None:
    async with self._condition:
      self._buffered_bytes -= payload_bytes
      self._condition.notify_all()

  async def cancel_reservation(self, *, payload_bytes: int = 0) -> None:
    async with self._condition:
      self._buffered_batches -= 1
      self._buffered_bytes -= payload_bytes
      self._condition.notify_all()

  async def put(self, item: _MarketCommitItem) -> None:
    await self.reserve()
    try:
      await self.put_reserved(item)
    except BaseException:
      await self.cancel_reservation()
      raise

  async def close(self, disconnect: WebSocketDisconnect) -> None:
    await self._queue.put(_MarketCommitQueueClosed(disconnect))

  async def get(self) -> _MarketCommitItem | _MarketCommitQueueClosed:
    return await self._queue.get()

  async def join(self) -> None:
    await self._queue.join()

  def complete_closed(self) -> None:
    self._queue.task_done()

  async def complete(self, item: _MarketCommitItem) -> None:
    async with self._condition:
      self._buffered_batches -= 1
      self._buffered_bytes -= len(item.payload)
      self._condition.notify_all()
    self._queue.task_done()


@dataclass(frozen=True)
class MarketStreamSession:
  device_id: str
  user_id: str
  stream_id: str


async def _publish_market_event(
  session: MarketStreamSession, payload: dict[str, Any]
) -> None:
  await _ensure_device_active(session.device_id, session=session)
  kind = str(payload.get("kind") or "")
  stock_code = str(payload.get("stock_code") or "")
  period = str(payload.get("period") or "tick")
  if kind == "quote" and stock_code:
    channel = f"market-data:{stock_code}:{period}"
  else:
    raise ValueError("market_event 只允许单标的 K 线行情")
  data = payload.get("data")
  if not isinstance(data, dict):
    data = {stock_code or "data": data}
  await redis_pubsub.publish(channel, data)


async def _ensure_device_active(
  device_id: str,
  *,
  session: MarketStreamSession | None = None,
) -> None:
  """Revalidate the owning Gateway connection and current durable identity."""
  if (
    session is None
    or session.device_id != device_id
    or not session.stream_id
    or _market_connections.active_stream_id != session.stream_id
  ):
    raise AuthError("MARKET_SESSION_REPLACED", "Agent 行情连接已失效或被替换")
  async with AsyncSessionLocal() as db:
    device = await db.get(AgentDevice, device_id)
  if (
    device is None or device.revoked_at is not None or device.user_id != session.user_id
  ):
    raise AuthError(QMT_DEVICE_REVOKED, "Agent 设备已撤销或身份已改变")


async def _send_market_text(websocket: WebSocket, payload: str) -> None:
  def disconnected() -> bool:
    return (
      getattr(websocket, "client_state", None) is WebSocketState.DISCONNECTED
      or getattr(websocket, "application_state", None) is WebSocketState.DISCONNECTED
    )

  if disconnected():
    # The receiver and Redis committer deliberately run concurrently.  The
    # receiver may observe the peer disconnect while the committer is
    # finishing an already accepted batch.  Treat that race as the original
    # disconnect instead of attempting an ASGI send on a completed response
    # and misclassifying it as a stream fault that requires RESYNC.
    raise WebSocketDisconnect(code=1006)
  try:
    await asyncio.wait_for(
      websocket.send_text(payload),
      timeout=MARKET_STREAM_CONTROL_SEND_TIMEOUT_SECONDS,
    )
  except RuntimeError as exc:
    # The peer may disconnect after the preflight check but before Starlette
    # reaches the ASGI send.  Preserve the disconnect classification in that
    # race as well; unrelated RuntimeErrors still propagate as stream faults.
    if disconnected():
      raise WebSocketDisconnect(code=1006) from exc
    raise


async def _request_market_resync(
  websocket: WebSocket,
  *,
  stream_id: str,
  sequence: int,
  reason: str,
) -> None:
  try:
    await _send_market_text(
      websocket,
      MarketStreamControl(
        type=MarketControlType.RESYNC,
        stream_id=stream_id,
        sequence=sequence,
        reason=reason[:256],
      ).model_dump_json(),
    )
  except Exception:
    pass


async def _receive_market_batches(
  websocket: WebSocket,
  *,
  stream_id: str,
  device_id: str,
  buffer: _MarketCommitBuffer,
  market_events: _MarketEventIngressBuffer | None = None,
  protocol_version: str = PROTOCOL_VERSION,
  validate_device: Callable[[str], Awaitable[None]] | None = None,
) -> None:
  device_validator = validate_device or _ensure_device_active
  expected_sequence = 1
  next_device_check = 0.0
  while True:
    now = time.monotonic()
    if now >= next_device_check:
      try:
        await device_validator(device_id)
      except _TRANSIENT_DEPENDENCY_ERRORS:
        MARKET_STREAM_EVENTS.labels(
          event="dependency",
          reason="market_session_revalidation_deferred",
        ).inc()
        logger.warning(
          "Market session revalidation deferred without disconnect: device_id=%s",
          device_id,
        )
      next_device_check = now + MARKET_STREAM_DEVICE_REVALIDATE_SECONDS

    await buffer.reserve()
    reserved = True
    reserved_payload_bytes = 0
    try:
      message = await websocket.receive()
      if message["type"] == "websocket.disconnect":
        await buffer.cancel_reservation()
        reserved = False
        await buffer.close(WebSocketDisconnect(message.get("code", 1000)))
        return

      # Revalidate after an idle receive so a revoked device cannot commit its
      # first post-revocation frame. A transient dependency failure retains the
      # established transport; the Redis freshness lease still expires and
      # keeps trading fail-closed until authority can be proved again.
      now = time.monotonic()
      if now >= next_device_check:
        try:
          await device_validator(device_id)
        except _TRANSIENT_DEPENDENCY_ERRORS:
          MARKET_STREAM_EVENTS.labels(
            event="dependency",
            reason="market_session_revalidation_deferred",
          ).inc()
          logger.warning(
            "Market session revalidation deferred without disconnect: device_id=%s",
            device_id,
          )
        next_device_check = time.monotonic() + MARKET_STREAM_DEVICE_REVALIDATE_SECONDS

      text_payload = message.get("text")
      if isinstance(text_payload, str):
        await buffer.cancel_reservation()
        reserved = False
        if market_events is None:
          raise ValueError("market text event processor is not configured")
        frame_bytes = len(text_payload.encode("utf-8"))
        if frame_bytes > MARKET_STREAM_EVENT_MAX_FRAME_BYTES:
          MARKET_STREAM_EVENTS.labels(
            event="backpressure",
            reason="market_stream_event_frame_too_large",
          ).inc()
          continue
        envelope = AgentEnvelope.model_validate_json(text_payload)
        if envelope.protocol_version != protocol_version:
          raise ValueError("market connection changed protocol version")
        if envelope.message_type is not AgentMessageType.MARKET_EVENT:
          raise ValueError("market stream text frame must be MARKET_EVENT")
        accepted = market_events.put_latest(
          _MarketEventIngressItem(
            envelope=envelope,
            frame_bytes=frame_bytes,
          )
        )
        if not accepted:
          MARKET_STREAM_EVENTS.labels(
            event="backpressure",
            reason="market_stream_event_dropped",
          ).inc()
        continue

      payload = message.get("bytes")
      if not isinstance(payload, bytes):
        raise ValueError("market stream frame must be binary or MARKET_EVENT text")
      payload_bytes = len(payload)
      if payload_bytes > MAX_MARKET_STREAM_FRAME_BYTES:
        raise ValueError("market stream frame exceeds 64 MiB")
      # Reserve the aggregate raw-byte budget before allocating the decoded
      # object graph. This mirrors the Agent's 64 MiB ACK-held outbound budget
      # and keeps the two-frame pipeline from decoding 128 MiB of raw frames.
      await buffer.reserve_payload(payload_bytes)
      reserved_payload_bytes = payload_bytes

      received_monotonic = time.monotonic()
      # Capture an aware server-side wall clock before decode/queueing. Database
      # helpers intentionally use naive UTC in parts of the API, but ingress
      # freshness validation requires an unambiguous instant.
      received_at = datetime.now(timezone.utc)
      if payload_bytes >= MARKET_STREAM_DECODE_OFFLOAD_BYTES:
        # asyncio.to_thread forwards the existing immutable bytes reference;
        # it does not make another 64 MiB payload copy. Only the unavoidable
        # decoded object graph is allocated off the event-loop thread.
        batch = await asyncio.to_thread(MarketStreamBatch.from_bytes, payload)
      else:
        batch = MarketStreamBatch.from_bytes(payload)
      if batch.stream_id != stream_id:
        raise ValueError("market stream id mismatch")
      if batch.sequence != expected_sequence:
        raise ValueError(
          "market stream sequence gap: "
          f"expected={expected_sequence} actual={batch.sequence}"
        )
      if expected_sequence == 1 and batch.kind is not MarketBatchKind.SNAPSHOT:
        raise ValueError("first market stream batch must be SNAPSHOT")
      if expected_sequence > 1 and batch.kind is MarketBatchKind.SNAPSHOT:
        raise ValueError("market stream SNAPSHOT is only valid as the first batch")
      await buffer.put_pre_reserved(
        _MarketCommitItem(
          batch=batch,
          payload=payload,
          received_at=received_at,
          received_monotonic=received_monotonic,
        )
      )
      reserved = False
      reserved_payload_bytes = 0
      expected_sequence += 1
    except BaseException:
      if reserved:
        await buffer.cancel_reservation(payload_bytes=reserved_payload_bytes)
      raise


async def _commit_market_batches(
  websocket: WebSocket,
  *,
  stream_id: str,
  buffer: _MarketCommitBuffer,
  commit_state: _MarketCommitState,
  store: MarketStreamStore | None = None,
) -> None:
  active_store = store or market_stream_store
  while True:
    queued = await buffer.get()
    if isinstance(queued, _MarketCommitQueueClosed):
      buffer.complete_closed()
      raise queued.disconnect

    try:
      retry_delay = 0.05
      allow_uncertain_retry = False
      while True:
        capture_age = max(
          0.0,
          (
            datetime.now(timezone.utc)
            - queued.batch.captured_at.astimezone(timezone.utc)
          ).total_seconds(),
        )
        remaining_freshness = MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS - capture_age
        if remaining_freshness <= 0:
          raise TimeoutError(
            "market stream capture expired while waiting for Redis: "
            f"age={capture_age:.3f}s"
          )
        try:
          write_options: dict[str, Any] = {
            "received_at": queued.received_at,
          }
          if allow_uncertain_retry:
            write_options["allow_uncertain_retry"] = True
          state = await asyncio.wait_for(
            active_store.write_batch(
              queued.batch,
              queued.payload,
              **write_options,
            ),
            timeout=min(
              MARKET_STREAM_REDIS_COMMIT_TIMEOUT_SECONDS,
              remaining_freshness,
            ),
          )
          break
        except (ConnectionError, asyncio.TimeoutError, RedisError) as exc:
          allow_uncertain_retry = True
          MARKET_STREAM_EVENTS.labels(
            event="dependency",
            reason="market_redis_commit_retry",
          ).inc()
          capture_age = max(
            0.0,
            (
              datetime.now(timezone.utc)
              - queued.batch.captured_at.astimezone(timezone.utc)
            ).total_seconds(),
          )
          remaining_freshness = MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS - capture_age
          if remaining_freshness <= retry_delay:
            raise TimeoutError(
              "market stream Redis commit did not recover before capture expiry"
            ) from exc
          await asyncio.sleep(min(retry_delay, remaining_freshness))
          retry_delay = min(retry_delay * 2, 0.5)
      MARKET_STREAM_PROCESSING.observe(time.monotonic() - queued.received_monotonic)
      MARKET_STREAM_FRAMES.labels(kind=queued.batch.kind.value).inc()
      MARKET_STREAM_FRAME_BYTES.set(len(queued.payload))
      MARKET_STREAM_INSTRUMENTS.set(queued.batch.instrument_count)
      MARKET_STREAM_SEQUENCE.set(queued.batch.sequence)
      commit_state.last_sequence = state.sequence
    finally:
      # Free one receive/commit slot only after Redis has either committed or
      # rejected the frame. A successful commit may open the next Agent ACK
      # window before the small control frame is written to the socket.
      await buffer.complete(queued)

    await _send_market_text(
      websocket,
      MarketStreamControl(
        type=MarketControlType.ACK,
        stream_id=stream_id,
        sequence=commit_state.last_sequence,
      ).model_dump_json(),
    )


async def _process_market_stream_events(
  market_session: MarketStreamSession,
  market_events: _MarketEventIngressBuffer,
) -> None:
  """Publish lossy single-symbol events without delaying binary receive/ACK."""

  while True:
    item = await market_events.get()
    try:
      try:
        await asyncio.wait_for(
          _publish_market_event(market_session, item.envelope.payload),
          timeout=MARKET_STREAM_EVENT_PROCESSING_TIMEOUT_SECONDS,
        )
      except _TRANSIENT_DEPENDENCY_ERRORS as exc:
        # The whole-market stream remains authoritative for current quotes.
        # This auxiliary event is replaceable, so retire it instead of holding
        # the binary ACK path or reconnecting the market transport.
        MARKET_STREAM_EVENTS.labels(
          event="dependency",
          reason="market_stream_event_dropped",
        ).inc()
        logger.warning(
          "Single-instrument market event dropped without stream reconnect: "
          "device_id=%s error=%s",
          market_session.device_id,
          exc.__class__.__name__,
        )
    finally:
      market_events.complete()


async def _run_market_commit_pipeline(
  websocket: WebSocket,
  *,
  stream_id: str,
  device_id: str,
  commit_state: _MarketCommitState,
  store: MarketStreamStore | None = None,
  market_session: MarketStreamSession | None = None,
  protocol_version: str = PROTOCOL_VERSION,
  validate_device: Callable[[str], Awaitable[None]] | None = None,
) -> None:
  buffer = _MarketCommitBuffer()
  market_events = _MarketEventIngressBuffer() if market_session is not None else None
  receiver = asyncio.create_task(
    _receive_market_batches(
      websocket,
      stream_id=stream_id,
      device_id=device_id,
      buffer=buffer,
      market_events=market_events,
      protocol_version=protocol_version,
      validate_device=validate_device,
    ),
    name=f"market-receiver:{stream_id}",
  )
  committer = asyncio.create_task(
    _commit_market_batches(
      websocket,
      stream_id=stream_id,
      buffer=buffer,
      commit_state=commit_state,
      store=store,
    ),
    name=f"market-redis-committer:{stream_id}",
  )
  event_processor = (
    asyncio.create_task(
      _process_market_stream_events(
        market_session,
        market_events,
      ),
      name=f"market-event-processor:{stream_id}",
    )
    if market_session is not None and market_events is not None
    else None
  )
  tasks = [receiver, committer]
  if event_processor is not None:
    tasks.append(event_processor)
  try:
    await asyncio.gather(*tasks)
  finally:
    for task in tasks:
      if not task.done():
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@market_agent_router.websocket("/ws/agent/market")
async def agent_market_websocket(websocket: WebSocket) -> None:
  """Receive the only SH/SZ whole-quote stream and converge it in Redis."""
  offered = set(websocket.scope.get("subprotocols") or [])
  if MARKET_STREAM_SUBPROTOCOL not in offered:
    await websocket.close(code=4406, reason="market subprotocol required")
    return
  await websocket.accept(subprotocol=MARKET_STREAM_SUBPROTOCOL)
  connection_id = ""
  stream_id = ""
  commit_state = _MarketCommitState()
  disconnect_reason = "market websocket disconnected"
  try:
    first = AgentEnvelope.model_validate_json(await websocket.receive_text())
    session = await _authenticate(first)
    device = session.device
    capabilities = _normalized_agent_capabilities(first.payload.get("capabilities", []))
    if "market-data" not in capabilities:
      raise AuthError("FORBIDDEN", "Agent 未声明 market-data 能力")
    if "agent_session_id" in first.payload:
      raise AuthError("UNAUTHENTICATED", "行情认证不接受控制会话字段")
    connection_id = await _market_connections.register() or ""
    if not connection_id:
      raise AuthError("CONFLICT", "已存在活动行情连接")
    market_session = MarketStreamSession(device.id, device.user_id, connection_id)
    await _ensure_device_active(device.id, session=market_session)
    MARKET_STREAM_CONNECTIONS.set(1)

    await _send_market_text(
      websocket,
      _auth_result(accepted=True).model_dump_json(),
    )
    await asyncio.wait_for(
      market_stream_store.cleanup_legacy_whole_controls(),
      timeout=MARKET_STREAM_REDIS_COMMIT_TIMEOUT_SECONDS,
    )
    stream_generation = await asyncio.wait_for(
      market_stream_store.allocate_generation(),
      timeout=MARKET_STREAM_REDIS_COMMIT_TIMEOUT_SECONDS,
    )
    stream_id = connection_id
    await asyncio.wait_for(
      market_stream_store.mark_syncing(
        stream_id,
        generation=stream_generation,
        reason="market websocket connected",
      ),
      timeout=MARKET_STREAM_REDIS_COMMIT_TIMEOUT_SECONDS,
    )
    await _send_market_text(
      websocket,
      MarketStreamControl(
        type=MarketControlType.START,
        stream_id=stream_id,
        markets=MARKET_STREAM_MARKETS,
      ).model_dump_json(),
    )

    await _run_market_commit_pipeline(
      websocket,
      stream_id=stream_id,
      device_id=device.id,
      commit_state=commit_state,
      market_session=market_session,
      protocol_version=first.protocol_version,
      validate_device=lambda checked_device_id: _ensure_device_active(
        checked_device_id,
        session=market_session,
      ),
    )
  except WebSocketDisconnect:
    disconnect_reason = "market websocket disconnected"
  except AuthError as exc:
    disconnect_reason = exc.message
    if not stream_id:
      try:
        await _send_market_text(
          websocket,
          _auth_result(
            accepted=False,
            reason=exc.message,
            reason_code=exc.code,
          ).model_dump_json(),
        )
      except Exception:
        pass
    else:
      await _request_market_resync(
        websocket,
        stream_id=stream_id,
        sequence=commit_state.last_sequence,
        reason=exc.message,
      )
    try:
      await websocket.close(code=4401, reason=exc.message[:120])
    except Exception:
      pass
  except Exception as exc:
    disconnect_reason = f"{exc.__class__.__name__}: {exc}"
    logger.warning(
      "Agent market WebSocket resync: stream_id=%s sequence=%s error=%s",
      stream_id,
      commit_state.last_sequence,
      disconnect_reason,
    )
    MARKET_STREAM_RESYNCS.labels(reason=exc.__class__.__name__).inc()
    if stream_id:
      await _request_market_resync(
        websocket,
        stream_id=stream_id,
        sequence=commit_state.last_sequence,
        reason=disconnect_reason,
      )
    try:
      await websocket.close(code=1011, reason="market stream resync required")
    except Exception:
      pass
  finally:
    # Release the single-connection lease before best-effort Redis cleanup.
    # A black-holed Redis connection must never strand this registry and make
    # every healthy Agent reconnect fail with CONFLICT.
    if connection_id:
      await _market_connections.unregister(connection_id)
      MARKET_STREAM_CONNECTIONS.set(0)
    if stream_id:
      try:
        await asyncio.wait_for(
          market_stream_store.mark_offline(
            stream_id,
            reason=disconnect_reason,
          ),
          timeout=MARKET_STREAM_REDIS_CLEANUP_TIMEOUT_SECONDS,
        )
      except Exception as exc:
        logger.warning(
          "Could not mark market stream offline: stream_id=%s error=%s",
          stream_id,
          exc.__class__.__name__,
        )
