"""Outbound-only QMT Agent WebSocket hub and durable report ingestion."""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import hmac
import ipaddress
import json
import logging
import os
import shutil
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Optional

import aiofiles
from fastapi import (
  APIRouter,
  Header,
  HTTPException,
  Request,
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
  CommandAckPayload,
  MarketBatchKind,
  MarketControlType,
  MarketStreamBatch,
  MarketStreamControl,
  ReportAckPayload,
)
from quantx_infrastructure.core.data.market_stream_transport import (
  MarketStreamStore,
  market_stream_store,
)
from quantx_infrastructure.database.redis_pubsub import (
  AGENT_REPORT_WAKE_CHANNEL,
  redis_pubsub,
)
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  AgentReportInbox,
  MarketDataRequest,
  MarketDataTransfer,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyOrderCorrelation,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import market_data_staging as _market_data_staging
from quantx_infrastructure.services.account_execution_safety_service import (
  AccountExecutionSafetyService,
)
from quantx_infrastructure.services.agent_session_guard import (
  AGENT_SERVER_SESSION_PAYLOAD_KEY,
  API_HEARTBEAT_COMPONENT,
  REMOTE_AGENT_ACCOUNT_MISMATCH,
  REMOTE_AGENT_OFFLINE,
  api_instance_is_current,
  evaluate_agent_session,
  parse_utc_timestamp,
  to_naive_utc,
  utc_iso,
)
from redis.exceptions import RedisError
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.websockets import WebSocketState

from quantx_api.agent_hub import (
  MARKET_DEVICE_LEASE_REFRESH_SECONDS,
  AgentControlSession,
  MarketSessionLease,
  agent_connection_hub,
)
from quantx_api.auth.agent_service import AgentAuthService
from quantx_api.auth.errors import AuthError
from quantx_api.auth.tokens import utcnow
from quantx_api.monitoring.metrics import (
  AGENT_CONTROL_DATABASE_STATE,
  AGENT_CONTROL_EVENTS,
  AGENT_CONTROL_QUEUE_DEPTH,
  AGENT_CONTROL_QUEUE_OLDEST_AGE,
  AGENT_CONTROL_STAGE_DURATION,
  MARKET_STREAM_CONNECTIONS,
  MARKET_STREAM_FRAME_BYTES,
  MARKET_STREAM_FRAMES,
  MARKET_STREAM_INSTRUMENTS,
  MARKET_STREAM_PROCESSING,
  MARKET_STREAM_RESYNCS,
  MARKET_STREAM_SEQUENCE,
)

logger = logging.getLogger(__name__)
agent_router = APIRouter(tags=["qmt-agent"])
market_agent_router = APIRouter(tags=["qmt-market-agent"])
MARKET_DATA_ROOT = _market_data_staging.market_data_staging_root()
_is_reparse_point = _market_data_staging.is_reparse_point
_market_data_request_staging_usage_bytes = (
  _market_data_staging.market_data_request_staging_usage_bytes
)
_safe_market_data_request_directory = (
  _market_data_staging.safe_market_data_request_directory
)
_relative_market_data_storage_reference = (
  _market_data_staging.relative_market_data_storage_reference
)
REPORT_TYPES = {
  AgentMessageType.ORDER_REPORT,
  AgentMessageType.EXECUTION_REPORT,
  AgentMessageType.DELTA_REPORT,
}
MAX_MARKET_DATA_CHUNK_BYTES = 32 * 1024 * 1024
MAX_MARKET_DATA_CHUNK_RECORDS = 5000
# Agent bounds allow at most 99 record-bound emissions, 22 byte-bound
# emissions, and one final chunk; round the proven 122 ceiling up slightly.
MAX_MARKET_DATA_CHUNKS = 128
MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES = 256 * 1024 * 1024
MAX_MARKET_DATA_STAGING_BYTES = 1024 * 1024 * 1024
MIN_MARKET_DATA_STAGING_FREE_BYTES = 512 * 1024 * 1024
MARKET_DATA_STAGING_SWEEP_SECONDS = 5 * 60
MARKET_DATA_STAGING_TEMP_GRACE_SECONDS = 60 * 60
MARKET_DATA_STAGING_ORPHAN_GRACE_SECONDS = 60 * 60
MARKET_DATA_STAGING_FAILED_RETENTION_SECONDS = 24 * 60 * 60
MARKET_STREAM_COMMIT_QUEUE_CAPACITY = 2
MARKET_STREAM_COMMIT_QUEUE_MAX_BYTES = MAX_MARKET_STREAM_FRAME_BYTES
MARKET_STREAM_DECODE_OFFLOAD_BYTES = 256 * 1024
MARKET_STREAM_DEVICE_REVALIDATE_SECONDS = 5.0
MARKET_STREAM_REDIS_COMMIT_TIMEOUT_SECONDS = 5.0
MARKET_STREAM_REDIS_CLEANUP_TIMEOUT_SECONDS = 2.0
MARKET_STREAM_CONTROL_SEND_TIMEOUT_SECONDS = 2.0
MARKET_STREAM_CONTROL_REGISTRATION_WAIT_SECONDS = 2.0
MARKET_STREAM_CONTROL_REGISTRATION_POLL_SECONDS = 0.025
TRADE_COMMAND_EXPIRY_SWEEP_INTERVAL_SECONDS = 1.0
TRADE_COMMAND_EXPIRY_SWEEP_BATCH_SIZE = 100
AGENT_CONTROL_INBOUND_QUEUE_CAPACITY = 32
AGENT_CONTROL_INBOUND_QUEUE_MAX_BYTES = 32 * 1024 * 1024
AGENT_CONTROL_OUTBOUND_QUEUE_CAPACITY = 64
AGENT_CONTROL_OUTBOUND_ACK_RESERVE = 16
AGENT_CONTROL_MAX_QUEUE_AGE_SECONDS = 5.0
AGENT_CONTROL_INBOUND_PROCESSING_TIMEOUT_SECONDS = 10.0
AGENT_CONTROL_DATABASE_POLL_TIMEOUT_SECONDS = 5.0
AGENT_CONTROL_DEPENDENCY_RETRY_SECONDS = 0.5
AGENT_CONTROL_SEND_TIMEOUT_SECONDS = 5.0
AGENT_CONTROL_POLL_INTERVAL_SECONDS = 1.0
AGENT_CONTROL_SLOW_STAGE_SECONDS = 1.0
AGENT_CONTROL_HEARTBEAT_STALE_SECONDS = 90.0

_MARKET_DATA_MUTABLE_UPLOAD_STATUSES = frozenset({"QUEUED", "DELIVERED", "RECEIVING"})
_MARKET_DATA_FROZEN_MANIFEST_STATUSES = frozenset(
  {"UPLOADED", "PROCESSING", "COMPLETED"}
)
_MARKET_DATA_ACTIVE_DISPATCH_STATUSES = frozenset(
  {"DELIVERED", "RECEIVING", "UPLOADED", "PROCESSING"}
)
MARKET_DATA_RECONNECT_STALE_SECONDS = 5 * 60
_MARKET_DATA_AGENT_BUSY_REASON = "MARKET_DATA_AGENT_BUSY"
_market_data_staging_lock = asyncio.Lock()
_market_data_staging_sweep_lock = asyncio.Lock()


class _AgentControlPipelineError(RuntimeError):
  def __init__(self, reason: str, *, close_code: int = 1011) -> None:
    super().__init__(reason)
    self.reason = reason
    self.close_code = close_code


_TRANSIENT_DATABASE_ERRORS = (SQLAlchemyTimeoutError, DBAPIError)
_TRANSIENT_DEPENDENCY_ERRORS = _TRANSIENT_DATABASE_ERRORS + (
  ConnectionError,
  asyncio.TimeoutError,
  RedisError,
)


@dataclass
class _AgentDatabaseState:
  device_id: str
  ready: asyncio.Event = field(default_factory=asyncio.Event)
  last_heartbeat_received_monotonic: float = field(default_factory=time.monotonic)
  consecutive_failures: int = 0

  def __post_init__(self) -> None:
    self.ready.set()
    self._update_metrics()

  def mark_success(self) -> None:
    self.ready.set()
    self.consecutive_failures = 0
    self._update_metrics()

  def mark_heartbeat_received(self) -> None:
    self.last_heartbeat_received_monotonic = time.monotonic()
    self._update_metrics()

  def mark_failure(self) -> None:
    self.ready.clear()
    self.consecutive_failures += 1
    self._update_metrics()

  def heartbeat_age(self) -> float:
    return max(0.0, time.monotonic() - self.last_heartbeat_received_monotonic)

  def _update_metrics(self) -> None:
    AGENT_CONTROL_DATABASE_STATE.labels(
      device_id=self.device_id,
      measure="ready",
    ).set(1 if self.ready.is_set() else 0)
    AGENT_CONTROL_DATABASE_STATE.labels(
      device_id=self.device_id,
      measure="consecutive_failures",
    ).set(self.consecutive_failures)
    AGENT_CONTROL_DATABASE_STATE.labels(
      device_id=self.device_id,
      measure="heartbeat_age_seconds",
    ).set(self.heartbeat_age())


@dataclass(frozen=True)
class _AgentInboundItem:
  envelope: AgentEnvelope
  received_at: datetime
  received_monotonic: float
  frame_bytes: int
  dedup_key: str = ""


@dataclass(order=True)
class _AgentOutboundItem:
  priority: int
  sequence: int
  envelope: AgentEnvelope = field(compare=False)
  queued_monotonic: float = field(compare=False)
  protocol_reply: bool = field(compare=False)
  dedup_key: str = field(compare=False, default="")


class _AgentInboundBuffer:
  """Bound accepted control frames by count and retained encoded bytes."""

  def __init__(
    self,
    *,
    capacity: int = AGENT_CONTROL_INBOUND_QUEUE_CAPACITY,
    max_bytes: int = AGENT_CONTROL_INBOUND_QUEUE_MAX_BYTES,
  ) -> None:
    self._capacity = capacity
    self._max_bytes = max_bytes
    self._items: deque[_AgentInboundItem] = deque()
    self._retained_bytes = 0
    self._pending_keys: set[str] = set()
    self._condition = asyncio.Condition()

  async def put(self, item: _AgentInboundItem) -> bool:
    if item.frame_bytes > self._max_bytes:
      raise _AgentControlPipelineError("inbound_frame_too_large", close_code=1009)
    async with self._condition:
      if item.dedup_key and item.dedup_key in self._pending_keys:
        return False
      await self._condition.wait_for(
        lambda: (
          len(self._items) < self._capacity
          and self._retained_bytes + item.frame_bytes <= self._max_bytes
        )
      )
      self._items.append(item)
      self._retained_bytes += item.frame_bytes
      if item.dedup_key:
        self._pending_keys.add(item.dedup_key)
      self._condition.notify_all()
      return True

  async def get(self) -> _AgentInboundItem:
    async with self._condition:
      await self._condition.wait_for(lambda: bool(self._items))
      item = self._items.popleft()
      self._retained_bytes -= item.frame_bytes
      self._condition.notify_all()
      return item

  async def complete(self, item: _AgentInboundItem) -> None:
    if not item.dedup_key:
      return
    async with self._condition:
      self._pending_keys.discard(item.dedup_key)
      self._condition.notify_all()

  def qsize(self) -> int:
    return len(self._items)

  def oldest_age(self) -> float:
    if not self._items:
      return 0.0
    return max(0.0, time.monotonic() - self._items[0].received_monotonic)


class _AgentOutboundBuffer:
  """Prioritize protocol acknowledgements while reserving bounded capacity."""

  def __init__(
    self,
    *,
    capacity: int = AGENT_CONTROL_OUTBOUND_QUEUE_CAPACITY,
    ack_reserve: int = AGENT_CONTROL_OUTBOUND_ACK_RESERVE,
  ) -> None:
    self._capacity = capacity
    self._work_capacity = capacity - ack_reserve
    self._items: list[_AgentOutboundItem] = []
    self._work_items = 0
    self._sequence = 0
    self._dedup_keys: set[str] = set()
    self._condition = asyncio.Condition()

  async def put(
    self,
    envelope: AgentEnvelope,
    *,
    priority: int,
    protocol_reply: bool = False,
    dedup_key: str = "",
  ) -> bool:
    async with self._condition:
      if dedup_key and dedup_key in self._dedup_keys:
        return False
      await self._condition.wait_for(
        lambda: (
          len(self._items) < self._capacity
          and (protocol_reply or self._work_items < self._work_capacity)
        )
      )
      if dedup_key and dedup_key in self._dedup_keys:
        return False
      self._sequence += 1
      heapq.heappush(
        self._items,
        _AgentOutboundItem(
          priority=priority,
          sequence=self._sequence,
          envelope=envelope,
          queued_monotonic=time.monotonic(),
          protocol_reply=protocol_reply,
          dedup_key=dedup_key,
        ),
      )
      if not protocol_reply:
        self._work_items += 1
      if dedup_key:
        self._dedup_keys.add(dedup_key)
      self._condition.notify_all()
      return True

  async def get(self) -> _AgentOutboundItem:
    async with self._condition:
      await self._condition.wait_for(lambda: bool(self._items))
      item = heapq.heappop(self._items)
      if not item.protocol_reply:
        self._work_items -= 1
      self._condition.notify_all()
      return item

  async def complete(self, item: _AgentOutboundItem) -> None:
    if not item.dedup_key:
      return
    async with self._condition:
      self._dedup_keys.discard(item.dedup_key)
      self._condition.notify_all()

  def qsize(self) -> int:
    return len(self._items)

  def oldest_age(self) -> float:
    if not self._items:
      return 0.0
    oldest = min(item.queued_monotonic for item in self._items)
    return max(0.0, time.monotonic() - oldest)


def _set_agent_control_queue_metrics(
  device_id: str,
  direction: str,
  buffer: _AgentInboundBuffer | _AgentOutboundBuffer,
) -> None:
  AGENT_CONTROL_QUEUE_DEPTH.labels(
    device_id=device_id,
    direction=direction,
  ).set(buffer.qsize())
  AGENT_CONTROL_QUEUE_OLDEST_AGE.labels(
    device_id=device_id,
    direction=direction,
  ).set(buffer.oldest_age())


def _observe_agent_control_stage(
  *,
  stage: str,
  envelope: AgentEnvelope,
  duration: float,
  device_id: str,
) -> None:
  safe_duration = max(0.0, duration)
  AGENT_CONTROL_STAGE_DURATION.labels(
    stage=stage,
    message_type=envelope.message_type.value,
  ).observe(safe_duration)
  if safe_duration >= AGENT_CONTROL_SLOW_STAGE_SECONDS:
    logger.warning(
      "Slow Agent control stage: device_id=%s message_type=%s message_id=%s "
      "stage=%s duration=%.3fs",
      device_id,
      envelope.message_type.value,
      envelope.message_id,
      stage,
      safe_duration,
    )


def _outbound_priority(envelope: AgentEnvelope) -> tuple[int, bool]:
  if envelope.message_type in {
    AgentMessageType.REPORT_ACK,
    AgentMessageType.HEARTBEAT_ACK,
  }:
    return 0, True
  if envelope.message_type is AgentMessageType.CANCEL_COMMAND:
    return 1, False
  if envelope.message_type is AgentMessageType.COMMAND:
    return 2, False
  if envelope.message_type is AgentMessageType.MARKET_DATA_REQUEST:
    return 3, False
  return 4, False


class _MarketConnectionRegistry:
  """Process-local single connection gate for the personal market Agent."""

  def __init__(self) -> None:
    self._lock = asyncio.Lock()
    self._connection_id = ""

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


async def _publish_market_event(
  control_session: AgentControlSession,
  payload: dict[str, Any],
) -> None:
  lease = MarketSessionLease(
    device_id=control_session.device_id,
    api_instance_id=control_session.api_instance_id,
    agent_session_id=control_session.agent_session_id,
  )
  if not await agent_connection_hub.is_market_session(lease):
    raise AuthError("FORBIDDEN", "当前设备不是活动行情 Agent")
  await _ensure_device_active(control_session.device_id, lease=lease)
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


def _auth_result(
  *,
  accepted: bool,
  reason: str = "",
  protocol_version: str = PROTOCOL_VERSION,
  agent_session_id: str = "",
) -> AgentEnvelope:
  payload = {"accepted": accepted, "reason": reason}
  if agent_session_id:
    payload["agent_session_id"] = agent_session_id
  return AgentEnvelope(
    protocol_version=protocol_version,
    message_type=AgentMessageType.AUTH_RESULT,
    payload=payload,
  )


def _remote_address_summary(websocket: WebSocket) -> str:
  host = str(websocket.client.host if websocket.client else "").strip()
  if not host:
    return "unknown"
  try:
    address = ipaddress.ip_address(host.split("%", 1)[0])
  except ValueError:
    return "unknown"
  mapped = getattr(address, "ipv4_mapped", None)
  if mapped is not None:
    address = mapped
  if isinstance(address, ipaddress.IPv4Address):
    octets = str(address).split(".")
    return ".".join((*octets[:3], "*"))
  groups = address.exploded.split(":")
  return ":".join((*groups[:3], "*"))


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
    return await AgentAuthService(db).authenticate_agent_session(
      token=token,
      expected_device_id=device_id,
    )


async def _record_heartbeat(
  session: AgentControlSession,
  payload: dict[str, Any],
  *,
  sent_at: datetime | None = None,
  establish: bool = False,
) -> None:
  now = utcnow()
  if (
    establish
    and await agent_connection_hub.current_session(session.device_id) is not session
  ):
    raise AuthError("UNAUTHENTICATED", "Agent 控制会话已被替换")
  revoked_ids: list[str] = []
  async with AsyncSessionLocal() as db:
    agent = await db.get(AgentDevice, session.device_id)
    if agent is None or agent.revoked_at is not None:
      raise AuthError("UNAUTHENTICATED", "Agent 设备已撤销")
    agent.last_seen_at = now
    heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{session.device_id}",
      with_for_update=True,
    )
    previous_details = dict(heartbeat.details or {}) if heartbeat is not None else {}
    previous_connected_at = parse_utc_timestamp(
      previous_details.get("serverConnectedAt")
    )
    session_connected_at = to_naive_utc(session.server_connected_at)
    if (
      establish
      and str(previous_details.get("apiInstanceId") or "") == session.api_instance_id
      and previous_connected_at is not None
      and session_connected_at is not None
      and previous_connected_at > session_connected_at
    ):
      raise AuthError("UNAUTHENTICATED", "Agent 控制会话已被更新连接替换")
    if not establish and (
      str(previous_details.get("apiInstanceId") or "") != session.api_instance_id
      or str(previous_details.get("agentSessionId") or "") != session.agent_session_id
    ):
      raise AuthError("UNAUTHENTICATED", "Agent 控制会话已被替换")
    frozen_capabilities = sorted(session.capabilities)
    agent.capabilities = frozen_capabilities
    requested_status = str(payload.get("status", "READY"))[:32].upper()
    status = "RECONCILING" if establish else requested_status
    preserve_server_status = bool(
      heartbeat is not None
      and str(heartbeat.status or "").upper()
      in {
        "RECONCILING",
        "RECONCILE_REQUIRED",
        REMOTE_AGENT_ACCOUNT_MISMATCH,
      }
      and requested_status == "READY"
    )
    if preserve_server_status:
      # Only Engine may promote a reconnecting Agent after the durable full
      # snapshot has been applied. A heartbeat is not reconciliation proof.
      status = str(heartbeat.status).upper()
    details = previous_details
    if establish:
      for key in (
        "readyAccounts",
        "blockedAccounts",
        "accountReconciliation",
        "snapshotId",
        "snapshotHash",
        "snapshotAt",
      ):
        details.pop(key, None)
    details.update(
      {
        "agentVersion": str(payload.get("agent_version", ""))[:64],
        "protocolVersion": str(payload.get("protocol_version", ""))[:16],
        "capabilities": frozen_capabilities,
        "xtdataStatus": str(payload.get("xtdata_status") or "UNKNOWN")[:32],
        "xtdataReason": str(payload.get("xtdata_reason") or "")[:64],
        "xttradingStatus": str(payload.get("xttrading_status") or "UNKNOWN")[:32],
        "xttradingReason": str(payload.get("xttrading_reason") or "")[:64],
        "journalIntegrity": str(payload.get("journal_integrity", ""))[:32],
        "journalSizeBytes": int(payload.get("journal_size_bytes") or 0),
        "journalPendingReports": int(payload.get("journal_pending_reports") or 0),
        "journalProcessingCommands": int(
          payload.get("journal_processing_commands") or 0
        ),
        "marketStreamStatus": str(payload.get("market_stream_status") or "OFFLINE")[
          :32
        ],
        "marketStreamSequence": int(payload.get("market_stream_sequence") or 0),
        "marketStreamQueueDepth": int(payload.get("market_stream_queue_depth") or 0),
        "marketStreamResyncs": int(payload.get("market_stream_resyncs") or 0),
        "marketStreamAckLatencyMs": float(
          payload.get("market_stream_ack_latency_ms") or 0.0
        ),
        "apiInstanceId": session.api_instance_id,
        "agentSessionId": session.agent_session_id,
        "serverConnectedAt": utc_iso(session.server_connected_at),
        "serverReceivedAt": utc_iso(now),
        "agentSentAt": utc_iso(sent_at) if sent_at is not None else None,
        "remoteAddressSummary": session.remote_address_summary,
        "sessionActive": True,
        "reasonCode": (
          str(previous_details.get("reasonCode") or "")[:64]
          if preserve_server_status
          else ""
        ),
      }
    )
    if heartbeat is None:
      heartbeat = RuntimeComponentHeartbeat(
        component=f"qmt-agent:{session.device_id}",
        instance_id=session.device_id,
        status=status,
        details=details,
        updated_at=now,
      )
      db.add(heartbeat)
    else:
      heartbeat.status = status
      heartbeat.details = details
      heartbeat.updated_at = now
    if status == "READY":
      revoked_ids = await AgentAuthService(db).converge_ready_device(
        device=agent,
        observed_at=now,
      )
      if revoked_ids:
        details = {
          **details,
          "completedHandoverDeviceIds": revoked_ids,
          "completedHandoverAt": now.isoformat(),
        }
        heartbeat.details = details
    await db.commit()
  for revoked_device_id in revoked_ids:
    await agent_connection_hub.revoke(revoked_device_id)
  if status == "READY" and "market-data" in session.capabilities:
    try:
      await agent_connection_hub.authorize_market_after_reconciliation(session)
    except Exception as exc:
      # Heartbeat durability is already committed. The independent lease
      # refresher retries Redis publication without rebuilding the control
      # session. The short freshness lease keeps market trading fail-closed.
      logger.warning(
        "无法在账户对账后启用 Agent 行情租约: device=%s error=%s",
        session.device_id,
        exc.__class__.__name__,
      )


async def _mark_session_offline(session: AgentControlSession) -> None:
  """Persist disconnect only when this is still the authoritative generation."""
  now = utcnow()
  async with AsyncSessionLocal() as db:
    heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{session.device_id}",
      with_for_update=True,
    )
    if heartbeat is None:
      return
    details = dict(heartbeat.details or {})
    if (
      str(details.get("apiInstanceId") or "") != session.api_instance_id
      or str(details.get("agentSessionId") or "") != session.agent_session_id
    ):
      return
    details.update(
      {
        "sessionActive": False,
        "serverReceivedAt": utc_iso(now),
        "reasonCode": REMOTE_AGENT_OFFLINE,
      }
    )
    heartbeat.status = "OFFLINE"
    heartbeat.details = details
    heartbeat.updated_at = now
    await db.commit()


async def _ensure_device_active(
  device_id: str,
  *,
  lease: MarketSessionLease | None = None,
) -> None:
  """Validate a market lease against Redis and the current API generation."""

  active_lease = lease or await agent_connection_hub.market_lease(device_id)
  active = bool(
    active_lease is not None
    and await agent_connection_hub.is_market_session(active_lease)
  )
  if not active or active_lease is None:
    raise AuthError("UNAUTHENTICATED", "Agent 设备已撤销或控制会话已断开")
  now = utcnow()
  async with AsyncSessionLocal() as db:
    device = await db.get(AgentDevice, device_id)
    api_heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      API_HEARTBEAT_COMPONENT,
    )
  if (
    device is None
    or device.revoked_at is not None
    or active_lease.api_instance_id
    != str(getattr(api_heartbeat, "instance_id", "") or "")
    or not api_instance_is_current(api_heartbeat, now=now)
  ):
    raise AuthError("UNAUTHENTICATED", "Agent 设备已撤销或控制会话已断开")


_PRE_EXECUTION_REJECTION_REASONS = frozenset(
  {
    # Runtime validation and capability gates run before Broker.execute().
    "command_expired",
    "account_not_whitelisted",
    "data_only_agent",
    "execution_mode_mismatch",
    "local_emergency_stop",
    "invalid_command_payload",
    "invalid_command_expiry",
    # MiniQMTBroker rejects these before calling XTTrader.order_stock().
    "miniQMT trading connection unavailable",
    "miniQMT disconnected",
    "broker report stale",
    "invalid order command",
    "invalid A-share code",
    "invalid order side",
    "buy volume must be a board lot",
    "invalid protected limit price",
    "outside trading session",
    "insufficient cash",
    "insufficient available volume",
    "live market metadata unavailable",
    "live quote or instrument metadata unavailable",
    "market_stream_not_ready",
    "instrument suspended",
    "quote timestamp unavailable",
    "stale live quote",
    "incomplete live trading limits",
    "invalid price tick",
    "price outside daily limits",
    "limit-up buy blocked",
    "limit-down sell blocked",
  }
)


def _is_place_order(command: TradeCommandOutbox) -> bool:
  return str((command.payload or {}).get("command_kind") or "").upper() == "PLACE_ORDER"


async def _stage_command_runtime_event(
  db,
  *,
  command: TradeCommandOutbox,
  pending: PendingTradeOrder,
  correlation: StrategyOrderCorrelation | None,
  status: str,
  reason: str,
  now: datetime,
) -> bool:
  if correlation is None:
    return False
  normalized_status = str(status or "").upper()
  business_key = (
    f"command:{pending.client_order_id}:RECONCILE_REQUIRED"
    if normalized_status == "RECONCILE_REQUIRED"
    else f"order:{pending.client_order_id}::{normalized_status}:0"
  )[:192]
  existing = await db.scalar(
    select(StrategyRuntimeEvent.event_id).where(
      StrategyRuntimeEvent.business_key == business_key
    )
  )
  if existing is not None:
    return False
  metadata = {
    **dict(correlation.request_metadata or {}),
    "strategy_run_id": correlation.strategy_run_id,
    "strategy_order_id": correlation.strategy_order_id,
    "intent_id": correlation.intent_id,
    "instrument_code": pending.instrument_code,
    "t_batch_id": correlation.batch_id or "",
    "bucket": correlation.bucket,
    "t_trade_role": str(correlation.t_trade_role or "").lower(),
    "risk_decision_id": correlation.risk_decision_id or "",
    "trace_id": correlation.trace_id,
    "substitution_plan": correlation.substitution_plan,
    "execution_mode": correlation.execution_mode,
    "approval_reason": reason,
    "runtime_event_key": business_key,
    "command_message_id": command.message_id,
    "command_lifecycle_status": str(pending.status or "").upper(),
  }
  event = StrategyRuntimeEvent(
    event_id=str(uuid.uuid4()),
    business_key=business_key,
    strategy_run_id=correlation.strategy_run_id,
    client_order_id=correlation.client_order_id,
    broker_order_id=correlation.broker_order_id,
    event_type="ORDER",
    payload={
      "report": {
        "client_order_id": pending.client_order_id,
        "account_id": pending.account_id,
        "stock_code": pending.instrument_code,
        "side": pending.side,
        "order_volume": int(pending.volume or 0),
        "price": float(pending.limit_price or 0),
        "traded_volume": 0,
        "status": normalized_status,
        "order_status": normalized_status,
        "status_msg": reason,
        "command_lifecycle_status": str(pending.status or "").upper(),
      },
      "metadata": metadata,
    },
    application_status="PENDING",
    application_attempts=0,
    created_at=now,
  )
  try:
    async with db.begin_nested():
      db.add(event)
      await db.flush()
  except IntegrityError:
    # A report consumer or duplicate ACK may stage the same semantic outcome
    # concurrently.  The unique business key is the final idempotency gate;
    # keep the surrounding lifecycle transaction intact.
    return False
  return True


async def _project_command_batch_status(
  db,
  *,
  pending: PendingTradeOrder,
  status: str,
  reason: str,
) -> None:
  if not pending.batch_id:
    return
  batch = await db.get(TTradeBatch, pending.batch_id, with_for_update=True)
  if batch is None:
    return
  normalized_status = str(status or "").upper()
  role = str(pending.t_trade_role or "").upper()
  if normalized_status == "RECONCILE_REQUIRED":
    batch.status = "RECONCILE_REQUIRED"
    batch.exception_reason = reason
  elif role == "ENTRY":
    batch.status = (
      "ENTRY_EXPIRED" if normalized_status == "EXPIRED" else "ENTRY_REJECTED"
    )
    batch.exception_reason = reason
  elif role == "EXIT":
    batch.status = "EXIT_REJECTED"
    batch.exception_reason = reason


async def _transition_place_order_command(
  db,
  *,
  command: TradeCommandOutbox,
  requested_status: str,
  reason: str,
  now: datetime,
  pre_execution_proven: bool,
) -> bool:
  """Atomically converge a non-broker command outcome into durable truth.

  A terminal status is legal only while every durable row still proves that
  no broker-side effect exists.  Any contradictory evidence is fail-closed as
  RECONCILE_REQUIRED; broker reports remain the only fill/acceptance truth.
  """

  pending = await db.get(
    PendingTradeOrder,
    command.client_order_id,
    with_for_update=True,
  )
  correlation = (
    await db.execute(
      select(StrategyOrderCorrelation)
      .where(StrategyOrderCorrelation.client_order_id == command.client_order_id)
      .with_for_update()
    )
  ).scalar_one_or_none()
  intent = None
  if pending is not None and pending.intent_id:
    intent = await db.get(
      TradeIntentRecord,
      pending.intent_id,
      with_for_update=True,
    )
  batch = None
  if pending is not None and pending.batch_id:
    batch = await db.get(TTradeBatch, pending.batch_id, with_for_update=True)

  normalized_status = str(requested_status or "").upper()
  role = str(pending.t_trade_role or "").upper() if pending is not None else ""
  batch_fill_volume = 0
  if role == "ENTRY" and batch is not None:
    batch_fill_volume = int(batch.entry_filled_volume or 0)
  elif role == "EXIT" and batch is not None:
    batch_fill_volume = int(batch.exit_filled_volume or 0)
  batch_pre_execution_state = bool(
    batch is None
    or (role == "ENTRY" and str(batch.status or "").upper() == "ENTRY_QUEUED")
    or (role == "EXIT" and str(batch.status or "").upper() == "EXIT_TRIGGERED")
  )
  try:
    intent_executed_volume = int(intent.executed_volume or 0) if intent else 0
    intent_executed_price = float(intent.executed_price or 0.0) if intent else 0.0
    intent_zero_execution = bool(
      intent is None
      or (
        intent_executed_volume == 0
        and isfinite(intent_executed_price)
        and intent_executed_price <= 0
        and intent.executed_time is None
      )
    )
  except (TypeError, ValueError, OverflowError):
    intent_executed_volume = -1
    intent_zero_execution = False
  already_same_pre_execution_outcome = bool(
    pending is not None
    and normalized_status in {"EXPIRED", "REJECTED"}
    and str(pending.status or "").upper() == normalized_status
    and str(pending.status_reason or "") == reason
    and not pending.broker_order_id
    and (correlation is None or not correlation.broker_order_id)
    and (not pending.strategy_run_id or correlation is not None)
    and (not pending.intent_id or intent is not None)
    and (not pending.batch_id or batch is not None)
    and batch_fill_volume == 0
    and intent_zero_execution
  )
  safe_pre_execution_state = bool(
    already_same_pre_execution_outcome
    or (
      pre_execution_proven
      and pending is not None
      and (
        str(pending.status or "").upper() == "QUEUED"
        or (
          normalized_status == "EXPIRED"
          and reason == "command_expired"
          and str(pending.status or "").upper() == "RECONCILE_REQUIRED"
        )
      )
      and not pending.broker_order_id
      and (correlation is None or not correlation.broker_order_id)
      and (not pending.strategy_run_id or correlation is not None)
      and (not pending.batch_id or batch is not None)
      and batch_fill_volume == 0
      and batch_pre_execution_state
      and (not pending.intent_id or (intent is not None and intent_zero_execution))
    )
  )
  if normalized_status in {"EXPIRED", "REJECTED"} and not safe_pre_execution_state:
    normalized_status = "RECONCILE_REQUIRED"
    reason = f"{reason}:durable_pre_execution_proof_missing"[:256]

  request_metadata = {
    **(dict(pending.request_metadata or {}) if pending is not None else {}),
    **(dict(correlation.request_metadata or {}) if correlation is not None else {}),
  }
  intent_metadata = dict(intent.intent_metadata or {}) if intent is not None else {}
  entry_plan_id = str(
    request_metadata.get("entry_plan_id") or intent_metadata.get("entry_plan_id") or ""
  ).strip()
  managed_entry_zero_fill = bool(
    normalized_status == "EXPIRED"
    and safe_pre_execution_state
    and entry_plan_id
    and pending is not None
    and correlation is not None
    and intent is not None
    and str(pending.side or "").upper() == "BUY"
    and bool(str(pending.strategy_run_id or ""))
    and str(correlation.strategy_run_id or "") == str(pending.strategy_run_id or "")
    and str(intent.strategy_run_id or "") == str(pending.strategy_run_id or "")
    and str(intent.direction or "").upper() == "BUY"
    and str(intent_metadata.get("entry_plan_id") or "") == entry_plan_id
    and intent_zero_execution
  )
  strategy_status = (
    "RECONCILED_ZERO_FILL" if managed_entry_zero_fill else normalized_status
  )

  if normalized_status == "RECONCILE_REQUIRED":
    command.delivery_status = "RECONCILE_REQUIRED"
  else:
    command.delivery_status = normalized_status
  command.last_error = reason[:256] or None

  if pending is None:
    logger.error(
      "Trade command lifecycle lost pending row; reconciliation required: message=%s client=%s",
      command.message_id,
      command.client_order_id,
    )
    return False

  pending.status = normalized_status
  pending.status_reason = reason[:256] or None
  if intent is not None:
    intent.status = strategy_status
    intent.notes = reason[:2000] or intent.notes
    if managed_entry_zero_fill:
      intent.intent_metadata = {
        **intent_metadata,
        "execution_terminal_source": "AGENT_COMMAND_LIFECYCLE",
        "execution_terminal_reason": reason,
        "command_lifecycle_status": normalized_status,
        "execution_terminal_at": now.isoformat(),
      }
  await _project_command_batch_status(
    db,
    pending=pending,
    status=normalized_status,
    reason=reason,
  )
  return await _stage_command_runtime_event(
    db,
    command=command,
    pending=pending,
    correlation=correlation,
    status=strategy_status,
    reason=reason,
    now=now,
  )


async def _wake_runtime_event_consumer() -> None:
  try:
    await asyncio.wait_for(
      redis_pubsub.publish(
        AGENT_REPORT_WAKE_CHANNEL,
        {"source": "trade_command_lifecycle"},
      ),
      timeout=0.5,
    )
  except Exception as exc:
    logger.debug(
      "Command lifecycle Redis wake-up failed; database polling remains active: %s",
      exc.__class__.__name__,
    )


async def _expire_trade_commands_in_session(
  db,
  *,
  now: datetime,
  device_id: str | None = None,
  batch_size: int = TRADE_COMMAND_EXPIRY_SWEEP_BATCH_SIZE,
) -> tuple[int, bool]:
  """Lock and converge one bounded batch of expired command rows.

  The cross-device API-owned sweeper and the connected-Agent delivery path use
  this same transition. PostgreSQL ``SKIP LOCKED`` lets multiple API processes
  run it safely without serializing unrelated devices or applying one command
  outcome twice.
  """

  query = select(TradeCommandOutbox).where(
    TradeCommandOutbox.delivery_status.in_(("QUEUED", "DELIVERED")),
    TradeCommandOutbox.expires_at <= now,
  )
  if device_id:
    query = query.where(TradeCommandOutbox.device_id == device_id)
  expired_commands = (
    (
      await db.execute(
        query.order_by(TradeCommandOutbox.expires_at, TradeCommandOutbox.created_at)
        .limit(max(1, int(batch_size)))
        .with_for_update(skip_locked=True)
      )
    )
    .scalars()
    .all()
  )

  staged_runtime_event = False
  for expired in expired_commands:
    previous_status = str(expired.delivery_status or "").upper()
    if _is_place_order(expired):
      if previous_status == "QUEUED":
        staged_runtime_event = (
          await _transition_place_order_command(
            db,
            command=expired,
            requested_status="EXPIRED",
            reason="command_expired_before_delivery",
            now=now,
            pre_execution_proven=True,
          )
          or staged_runtime_event
        )
      else:
        staged_runtime_event = (
          await _transition_place_order_command(
            db,
            command=expired,
            requested_status="RECONCILE_REQUIRED",
            reason="delivered_command_expired_without_ack",
            now=now,
            pre_execution_proven=False,
          )
          or staged_runtime_event
        )
    else:
      expired.delivery_status = (
        "EXPIRED" if previous_status == "QUEUED" else "RECONCILE_REQUIRED"
      )
      expired.last_error = (
        "command_expired_before_delivery"
        if previous_status == "QUEUED"
        else "delivered_command_expired_without_ack"
      )
  return len(expired_commands), staged_runtime_event


async def sweep_expired_trade_commands(
  *,
  now: datetime | None = None,
  batch_size: int = TRADE_COMMAND_EXPIRY_SWEEP_BATCH_SIZE,
) -> int:
  """Converge expired commands even when their QMT Agent is disconnected."""

  effective_batch_size = max(1, int(batch_size))
  total = 0
  staged_runtime_event = False
  while True:
    async with AsyncSessionLocal() as db:
      count, staged = await _expire_trade_commands_in_session(
        db,
        now=now or utcnow(),
        batch_size=effective_batch_size,
      )
      await db.commit()
    total += count
    staged_runtime_event = staged_runtime_event or staged
    if count < effective_batch_size:
      break
  if staged_runtime_event:
    await _wake_runtime_event_consumer()
  return total


async def run_trade_command_expiry_sweeper(
  stopped: asyncio.Event,
  *,
  interval_seconds: float = TRADE_COMMAND_EXPIRY_SWEEP_INTERVAL_SECONDS,
) -> None:
  """Run an immediate startup sweep followed by bounded periodic recovery."""

  interval = max(0.1, float(interval_seconds))
  while not stopped.is_set():
    try:
      await sweep_expired_trade_commands()
    except asyncio.CancelledError:
      raise
    except Exception:
      logger.exception("Trade command expiry sweep failed")
    try:
      await asyncio.wait_for(stopped.wait(), timeout=interval)
    except asyncio.TimeoutError:
      pass


async def _record_command_ack(device_id: str, payload: dict[str, Any]) -> None:
  ack = CommandAckPayload.model_validate(payload)
  message_id = ack.command_message_id
  client_order_id = ack.client_order_id
  accepted = ack.accepted
  reason = ack.reason.strip()
  staged_runtime_event = False
  async with AsyncSessionLocal() as db:
    query = select(TradeCommandOutbox).where(
      TradeCommandOutbox.device_id == device_id,
      TradeCommandOutbox.message_id == message_id,
    )
    command = (await db.execute(query.with_for_update())).scalar_one_or_none()
    if command is None:
      return
    if command.client_order_id != client_order_id:
      raise ValueError("command_ack 命令与 client_order_id 不匹配")
    now = utcnow()
    previous_status = str(command.delivery_status or "").upper()
    command.acknowledged_at = now
    if not _is_place_order(command):
      if accepted:
        command.delivery_status = "ACKNOWLEDGED"
        command.last_error = reason[:256] or None
      elif reason in _PRE_EXECUTION_REJECTION_REASONS:
        command.delivery_status = (
          "EXPIRED" if reason == "command_expired" else "REJECTED"
        )
        command.last_error = reason[:256] or None
      else:
        command.delivery_status = "RECONCILE_REQUIRED"
        command.last_error = (reason or "indeterminate_cancel_command_rejection")[:256]
    elif accepted and previous_status == "ACKNOWLEDGED":
      # Idempotent replay of the Agent journal result.
      command.last_error = reason[:256] or None
    elif accepted and previous_status in {"QUEUED", "DELIVERED", "RECONCILE_REQUIRED"}:
      # ACK is delivery/local-processing evidence only.  Pending order truth
      # still waits for a durable broker report.
      command.delivery_status = "ACKNOWLEDGED"
      command.last_error = reason[:256] or None
    elif accepted:
      # An accepted ACK contradicting a server-side terminal outcome is not
      # proof of broker acceptance or non-acceptance.  Preserve every link and
      # force reconciliation.
      staged_runtime_event = await _transition_place_order_command(
        db,
        command=command,
        requested_status="RECONCILE_REQUIRED",
        reason="accepted_ack_conflicts_with_terminal_command_state",
        now=now,
        pre_execution_proven=False,
      )
    elif previous_status == "ACKNOWLEDGED":
      staged_runtime_event = await _transition_place_order_command(
        db,
        command=command,
        requested_status="RECONCILE_REQUIRED",
        reason=(reason or "rejected_ack_conflicts_with_acknowledged_command"),
        now=now,
        pre_execution_proven=False,
      )
    elif previous_status in {"REJECTED", "EXPIRED"} and (
      (previous_status == "EXPIRED" and reason == "command_expired")
      or (
        previous_status == "REJECTED"
        and reason in _PRE_EXECUTION_REJECTION_REASONS
        and reason != "command_expired"
      )
    ):
      # The same deterministic rejection may be replayed after reconnect.
      command.last_error = reason[:256] or command.last_error
    elif reason in _PRE_EXECUTION_REJECTION_REASONS:
      terminal_status = "EXPIRED" if reason == "command_expired" else "REJECTED"
      staged_runtime_event = await _transition_place_order_command(
        db,
        command=command,
        requested_status=terminal_status,
        reason=reason,
        now=now,
        pre_execution_proven=True,
      )
    else:
      staged_runtime_event = await _transition_place_order_command(
        db,
        command=command,
        requested_status="RECONCILE_REQUIRED",
        reason=reason or "indeterminate_command_rejection",
        now=now,
        pre_execution_proven=False,
      )
    await db.commit()
  if staged_runtime_event:
    await _wake_runtime_event_consumer()


async def _record_report(
  session: AgentControlSession,
  envelope: AgentEnvelope,
  *,
  received_at: datetime,
) -> ReportAckPayload:
  wire_payload = envelope.payload
  if AGENT_SERVER_SESSION_PAYLOAD_KEY in wire_payload:
    raise ValueError("Agent report contains a reserved server field")
  if envelope.protocol_version == PROTOCOL_VERSION:
    envelope.validate_payload()
  payload_hash = hashlib.sha256(
    json.dumps(
      {
        "message_type": envelope.message_type.value,
        "payload": wire_payload,
      },
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  body = _body_for_report_idempotency(envelope)
  business_idempotency_key = hashlib.sha256(
    (
      f"{session.device_id}:{envelope.message_type.value}:"
      f"{json.dumps(body, sort_keys=True, separators=(',', ':'), default=str)}"
    ).encode("utf-8")
  ).hexdigest()
  payload = {
    **wire_payload,
    AGENT_SERVER_SESSION_PAYLOAD_KEY: {
      "apiInstanceId": session.api_instance_id,
      "agentSessionId": session.agent_session_id,
      "serverConnectedAt": utc_iso(session.server_connected_at),
      "serverReceivedAt": utc_iso(received_at),
      "authorizedAccountIds": sorted(session.authorized_account_ids),
    },
  }
  report = AgentReportInbox(
    message_id=envelope.message_id,
    device_id=session.device_id,
    message_type=envelope.message_type.value,
    protocol_version=envelope.protocol_version,
    client_order_id=str(wire_payload.get("client_order_id", "")) or None,
    raw_payload_hash=payload_hash,
    business_idempotency_key=business_idempotency_key,
    payload=payload,
    received_at=received_at,
    processing_status="PENDING",
  )
  ack: ReportAckPayload
  persist_started = time.monotonic()
  async with AsyncSessionLocal() as db:
    db.add(report)
    try:
      await db.commit()
      ack = ReportAckPayload(
        report_message_id=envelope.message_id,
        accepted=True,
      )
    except IntegrityError:
      await db.rollback()
      existing = (
        await db.execute(
          select(AgentReportInbox).where(
            (AgentReportInbox.message_id == envelope.message_id)
            | (AgentReportInbox.business_idempotency_key == business_idempotency_key)
          )
        )
      ).scalar_one_or_none()
      same = existing is not None and existing.raw_payload_hash == payload_hash
      ack = ReportAckPayload(
        report_message_id=envelope.message_id,
        accepted=same,
        duplicate=same,
        reason="" if same else "message_id_payload_mismatch",
      )
  _observe_agent_control_stage(
    stage="report_persist",
    envelope=envelope,
    duration=time.monotonic() - persist_started,
    device_id=session.device_id,
  )
  if ack.accepted:
    try:
      await asyncio.wait_for(
        redis_pubsub.publish(
          AGENT_REPORT_WAKE_CHANNEL,
          {"message_id": envelope.message_id},
        ),
        timeout=0.5,
      )
    except Exception as exc:
      logger.debug(
        "Agent report Redis wake-up failed; database polling remains active: %s",
        exc.__class__.__name__,
      )
  return ack


def _body_for_report_idempotency(
  envelope: AgentEnvelope,
) -> dict[str, Any]:
  payload = envelope.payload
  if envelope.message_type is AgentMessageType.EXECUTION_REPORT:
    execution = payload.get("execution")
    body = execution if isinstance(execution, dict) else payload
    identity = {
      "account_id": body.get("account_id"),
      "execution_id": body.get("execution_id") or body.get("traded_id"),
    }
    if not identity["execution_id"]:
      identity["payload_hash"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode("utf-8")
      ).hexdigest()
    return identity
  if envelope.message_type is AgentMessageType.ORDER_REPORT:
    order = payload.get("order")
    body = order if isinstance(order, dict) else payload
    identity = {
      "account_id": body.get("account_id"),
      "order_id": body.get("order_id") or body.get("broker_order_id"),
      "order_status": body.get("order_status") or body.get("status"),
      "traded_volume": body.get("traded_volume"),
      "traded_price": body.get("traded_price"),
    }
    if not identity["order_id"]:
      identity["payload_hash"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode("utf-8")
      ).hexdigest()
    return identity
  return {
    "report_id": payload.get("report_id"),
    "sequence": payload.get("sequence"),
    "snapshot_hash": hashlib.sha256(
      json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
      ).encode("utf-8")
    ).hexdigest(),
  }


async def _next_command(
  control_session: AgentControlSession,
  *,
  protocol_version: str = PROTOCOL_VERSION,
) -> Optional[AgentEnvelope]:
  device_id = control_session.device_id
  now = utcnow()
  async with AsyncSessionLocal() as db:
    expired_count, staged_runtime_event = await _expire_trade_commands_in_session(
      db,
      now=now,
      device_id=device_id,
    )
    if expired_count:
      # Expiry convergence is an independent lifecycle transaction. Commit and
      # wake Engine before readiness checks can return early for this device.
      await db.commit()
      if staged_runtime_event:
        await _wake_runtime_event_consumer()
    result = await db.execute(
      select(TradeCommandOutbox)
      .where(
        TradeCommandOutbox.device_id == device_id,
        TradeCommandOutbox.delivery_status.in_(("QUEUED", "DELIVERED")),
        TradeCommandOutbox.expires_at > now,
      )
      .order_by(TradeCommandOutbox.created_at)
      .limit(1)
      .with_for_update(skip_locked=True)
    )
    command = result.scalar_one_or_none()
    if command is None:
      await db.commit()
      return None
    device = await db.get(AgentDevice, device_id)
    if device is None or device.revoked_at is not None:
      await db.commit()
      return None
    heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{device_id}",
    )
    command_kind = str(command.payload.get("command_kind") or "").upper()
    acceptable_statuses = (
      {"READY", "EMERGENCY_STOP", "RECONCILE_REQUIRED"}
      if command_kind == "CANCEL_ORDER"
      else {"READY"}
    )
    api_heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      API_HEARTBEAT_COMPONENT,
    )
    session_state = evaluate_agent_session(
      heartbeat,
      api_heartbeat,
      now=now,
      acceptable_statuses=acceptable_statuses,
    )
    if (
      not session_state.current
      or session_state.api_instance_id != control_session.api_instance_id
      or session_state.agent_session_id != control_session.agent_session_id
    ):
      await db.commit()
      return None
    command.delivery_status = "DELIVERED"
    command.delivered_at = now
    command.attempts = (command.attempts or 0) + 1
    await db.commit()
    sent_at = command.created_at
    if sent_at.tzinfo is None:
      sent_at = sent_at.replace(tzinfo=timezone.utc)
    return AgentEnvelope(
      protocol_version=protocol_version,
      message_id=command.message_id,
      message_type=(
        AgentMessageType.CANCEL_COMMAND
        if command.payload.get("command_kind") == "CANCEL_ORDER"
        else AgentMessageType.COMMAND
      ),
      sent_at=sent_at,
      payload=command.payload,
    )


async def _next_market_data_request(
  control_session: AgentControlSession,
  *,
  protocol_version: str = PROTOCOL_VERSION,
) -> Optional[AgentEnvelope]:
  device_id = control_session.device_id
  if "market-data" not in {
    str(capability).strip().lower() for capability in control_session.capabilities
  }:
    return None
  async with AsyncSessionLocal() as db:
    # Lock the device row before inspecting or dispatching requests. Locking only
    # a QUEUED row lets two concurrent websocket loops each observe no active
    # delivery and dispatch separate rows for the same serial QMT worker.
    device = await db.scalar(
      select(AgentDevice.id)
      .where(
        AgentDevice.id == device_id,
        AgentDevice.revoked_at.is_(None),
      )
      .with_for_update()
    )
    if device is None:
      return None
    heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{device_id}",
    )
    api_heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      API_HEARTBEAT_COMPONENT,
    )
    session_state = evaluate_agent_session(
      heartbeat,
      api_heartbeat,
      now=utcnow(),
      acceptable_statuses={
        "READY",
        "RECONCILING",
        "RECONCILE_REQUIRED",
        "TRADING_UNAVAILABLE",
        "EMERGENCY_STOP",
      },
    )
    if (
      not session_state.current
      or session_state.api_instance_id != control_session.api_instance_id
      or session_state.agent_session_id != control_session.agent_session_id
    ):
      return None
    active_request_id = await db.scalar(
      select(MarketDataRequest.request_id)
      .where(
        MarketDataRequest.device_id == device_id,
        MarketDataRequest.status.in_(_MARKET_DATA_ACTIVE_DISPATCH_STATUSES),
      )
      .limit(1)
    )
    if active_request_id is not None:
      return None
    result = await db.execute(
      select(MarketDataRequest)
      .where(
        MarketDataRequest.device_id == device_id,
        MarketDataRequest.status == "QUEUED",
      )
      .order_by(MarketDataRequest.created_at)
      .limit(1)
      .with_for_update(skip_locked=True)
    )
    request = result.scalar_one_or_none()
    if request is None:
      return None
    request.status = "DELIVERED"
    request.updated_at = utcnow()
    await db.commit()
    return AgentEnvelope(
      protocol_version=protocol_version,
      message_id=request.request_id,
      message_type=AgentMessageType.MARKET_DATA_REQUEST,
      payload={
        **request.request_payload,
        "request_id": request.request_id,
        "upload_path": f"/agent/market-data/{request.request_id}/chunks",
      },
    )


async def _requeue_incomplete_market_requests(
  device_id: str,
  *,
  now: datetime | None = None,
) -> None:
  """Recover only expired delivery leases after an Agent reconnect.

  ``updated_at`` advances on dispatch and every accepted upload chunk. A fresh
  DELIVERED/RECEIVING request is therefore an active upload lease, not reconnect
  debris. UPLOADED and PROCESSING belong to durable ingestion and are never
  eligible for websocket redispatch.
  """

  reference_time = now or utcnow()
  stale_before = reference_time - timedelta(seconds=MARKET_DATA_RECONNECT_STALE_SECONDS)
  async with AsyncSessionLocal() as db:
    await db.execute(
      update(MarketDataRequest)
      .where(
        MarketDataRequest.device_id == device_id,
        MarketDataRequest.status.in_(("DELIVERED", "RECEIVING")),
        MarketDataRequest.updated_at < stale_before,
      )
      .values(status="QUEUED", updated_at=reference_time)
    )
    await db.commit()


async def _process_message(
  session: AgentControlSession,
  envelope: AgentEnvelope,
  *,
  received_at: datetime,
  protocol_version: str = PROTOCOL_VERSION,
) -> AgentEnvelope | None:
  device_id = session.device_id
  if not await agent_connection_hub.is_connected(
    device_id,
    agent_session_id=session.agent_session_id,
  ):
    raise AuthError("UNAUTHENTICATED", "Agent 控制会话已被替换")
  if envelope.message_type is AgentMessageType.HEARTBEAT:
    if str(envelope.payload.get("device_id", "")) != device_id:
      raise AuthError("UNAUTHENTICATED", "heartbeat 设备不匹配")
    await _record_heartbeat(
      session,
      envelope.payload,
      sent_at=envelope.sent_at,
    )
    return AgentEnvelope(
      protocol_version=protocol_version,
      message_type=AgentMessageType.HEARTBEAT_ACK,
      payload={"heartbeat_message_id": envelope.message_id},
    )
  if envelope.message_type is AgentMessageType.COMMAND_ACK:
    await _record_command_ack(device_id, envelope.payload)
    return None
  if envelope.message_type in REPORT_TYPES:
    ack = await _record_report(
      session,
      envelope,
      received_at=received_at,
    )
    return AgentEnvelope(
      protocol_version=protocol_version,
      message_type=AgentMessageType.REPORT_ACK,
      payload=ack.model_dump(mode="json"),
    )
  if envelope.message_type is AgentMessageType.MARKET_EVENT:
    await _publish_market_event(session, envelope.payload)
    return None
  raise ValueError(f"不支持的 Agent 消息类型: {envelope.message_type.value}")


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


async def _wait_for_active_market_device(
  device_id: str,
) -> MarketSessionLease:
  """Bridge the short race between control and market WebSocket startup."""
  deadline = time.monotonic() + MARKET_STREAM_CONTROL_REGISTRATION_WAIT_SECONDS
  while True:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      raise AuthError("FORBIDDEN", "当前设备不是活动行情 Agent")
    try:
      lease = await asyncio.wait_for(
        agent_connection_hub.market_lease(device_id),
        timeout=remaining,
      )
    except asyncio.TimeoutError as exc:
      raise AuthError(
        "FORBIDDEN",
        "当前设备不是活动行情 Agent",
      ) from exc
    if lease is not None:
      return lease
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      raise AuthError("FORBIDDEN", "当前设备不是活动行情 Agent")
    await asyncio.sleep(min(MARKET_STREAM_CONTROL_REGISTRATION_POLL_SECONDS, remaining))


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
        AGENT_CONTROL_EVENTS.labels(
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
          AGENT_CONTROL_EVENTS.labels(
            event="dependency",
            reason="market_session_revalidation_deferred",
          ).inc()
          logger.warning(
            "Market session revalidation deferred without disconnect: device_id=%s",
            device_id,
          )
        next_device_check = time.monotonic() + MARKET_STREAM_DEVICE_REVALIDATE_SECONDS

      payload = message.get("bytes")
      if not isinstance(payload, bytes):
        raise ValueError("market stream only accepts binary data frames")
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
          AGENT_CONTROL_EVENTS.labels(
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


async def _run_market_commit_pipeline(
  websocket: WebSocket,
  *,
  stream_id: str,
  device_id: str,
  commit_state: _MarketCommitState,
  store: MarketStreamStore | None = None,
  validate_device: Callable[[str], Awaitable[None]] | None = None,
) -> None:
  buffer = _MarketCommitBuffer()
  receiver = asyncio.create_task(
    _receive_market_batches(
      websocket,
      stream_id=stream_id,
      device_id=device_id,
      buffer=buffer,
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
  try:
    await asyncio.gather(receiver, committer)
  finally:
    for task in (receiver, committer):
      if not task.done():
        task.cancel()
    await asyncio.gather(receiver, committer, return_exceptions=True)


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
    market_lease = await _wait_for_active_market_device(device.id)
    requested_control_session_id = str(
      first.payload.get("agent_session_id") or ""
    ).strip()
    if (
      not requested_control_session_id
      or requested_control_session_id != market_lease.agent_session_id
    ):
      raise AuthError("UNAUTHENTICATED", "行情连接与当前控制会话不匹配")
    await _ensure_device_active(device.id, lease=market_lease)
    connection_id = await _market_connections.register() or ""
    if not connection_id:
      raise AuthError("CONFLICT", "已存在活动行情连接")
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
    stream_id = str(uuid.uuid4())
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
      validate_device=lambda checked_device_id: _ensure_device_active(
        checked_device_id,
        lease=market_lease,
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
          _auth_result(accepted=False, reason=exc.message).model_dump_json(),
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


def _observe_source_to_receive(
  device_id: str,
  envelope: AgentEnvelope,
  received_at: datetime,
) -> None:
  if envelope.message_type is not AgentMessageType.DELTA_REPORT:
    return
  raw_source = envelope.payload.get("source_event_at")
  if not raw_source:
    return
  try:
    source_at = datetime.fromisoformat(str(raw_source).replace("Z", "+00:00"))
    if source_at.tzinfo is None:
      source_at = source_at.replace(tzinfo=timezone.utc)
    received_utc = (
      received_at.replace(tzinfo=timezone.utc)
      if received_at.tzinfo is None
      else received_at.astimezone(timezone.utc)
    )
    duration = (received_utc - source_at.astimezone(timezone.utc)).total_seconds()
  except (TypeError, ValueError):
    return
  if duration < 0:
    AGENT_CONTROL_EVENTS.labels(
      event="timestamp",
      reason="future_source_event",
    ).inc()
    return
  _observe_agent_control_stage(
    stage="source_to_socket_receive",
    envelope=envelope,
    duration=duration,
    device_id=device_id,
  )


async def _enqueue_agent_outbound(
  device_id: str,
  buffer: _AgentOutboundBuffer,
  envelope: AgentEnvelope,
  *,
  deduplicate: bool = False,
) -> bool:
  priority, protocol_reply = _outbound_priority(envelope)
  dedup_key = envelope.message_id if deduplicate else ""
  queued = await buffer.put(
    envelope,
    priority=priority,
    protocol_reply=protocol_reply,
    dedup_key=dedup_key,
  )
  _set_agent_control_queue_metrics(device_id, "outbound", buffer)
  return queued


async def _receive_agent_control_messages(
  websocket: WebSocket,
  *,
  device_id: str,
  protocol_version: str,
  inbound: _AgentInboundBuffer,
  database_state: _AgentDatabaseState,
) -> None:
  while True:
    raw = await websocket.receive_text()
    received_monotonic = time.monotonic()
    received_at = utcnow()
    envelope = AgentEnvelope.model_validate_json(raw)
    if envelope.protocol_version != protocol_version:
      raise ValueError("Agent connection changed protocol version")
    if envelope.message_type is AgentMessageType.HEARTBEAT:
      # Liveness is a transport fact. Database persistence can lag without
      # turning a healthy socket into a reconnect/reconciliation storm.
      database_state.mark_heartbeat_received()
    _observe_source_to_receive(device_id, envelope, received_at)
    item = _AgentInboundItem(
      envelope=envelope,
      received_at=received_at,
      received_monotonic=received_monotonic,
      frame_bytes=len(raw.encode("utf-8")),
      dedup_key=(envelope.message_id if envelope.message_type in REPORT_TYPES else ""),
    )
    queued = await inbound.put(item)
    if not queued:
      AGENT_CONTROL_EVENTS.labels(
        event="deduplicate",
        reason="inbound_report_pending",
      ).inc()
    _set_agent_control_queue_metrics(device_id, "inbound", inbound)


async def _process_agent_control_messages(
  *,
  control_session: AgentControlSession,
  protocol_version: str,
  inbound: _AgentInboundBuffer,
  outbound: _AgentOutboundBuffer,
  database_state: _AgentDatabaseState,
) -> None:
  device_id = control_session.device_id
  while True:
    item = await inbound.get()
    _set_agent_control_queue_metrics(device_id, "inbound", inbound)
    queue_age = max(0.0, time.monotonic() - item.received_monotonic)
    _observe_agent_control_stage(
      stage="inbound_queue_wait",
      envelope=item.envelope,
      duration=queue_age,
      device_id=device_id,
    )
    if queue_age > AGENT_CONTROL_MAX_QUEUE_AGE_SECONDS:
      AGENT_CONTROL_EVENTS.labels(
        event="backpressure",
        reason="inbound_queue_stale",
      ).inc()
      logger.warning(
        "Agent inbound message waited %.3fs; processing retained durable message: device_id=%s message_type=%s",
        queue_age,
        device_id,
        item.envelope.message_type.value,
      )
    processing_started = time.monotonic()
    retry_delay = AGENT_CONTROL_DEPENDENCY_RETRY_SECONDS
    while True:
      try:
        reply = await asyncio.wait_for(
          _process_message(
            control_session,
            item.envelope,
            received_at=item.received_at,
            protocol_version=protocol_version,
          ),
          timeout=AGENT_CONTROL_INBOUND_PROCESSING_TIMEOUT_SECONDS,
        )
      except _TRANSIENT_DEPENDENCY_ERRORS as exc:
        database_state.mark_failure()
        AGENT_CONTROL_EVENTS.labels(
          event="dependency",
          reason="inbound_processing_retry",
        ).inc()
        logger.warning(
          "Agent message persistence deferred without disconnect: "
          "device_id=%s message_type=%s error=%s retry_seconds=%.1f",
          device_id,
          item.envelope.message_type.value,
          exc.__class__.__name__,
          retry_delay,
        )
        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 5.0)
        continue
      if (
        item.envelope.message_type is AgentMessageType.HEARTBEAT or inbound.qsize() == 0
      ):
        database_state.mark_success()
      break
    _observe_agent_control_stage(
      stage="inbound_processing",
      envelope=item.envelope,
      duration=time.monotonic() - processing_started,
      device_id=device_id,
    )
    if reply is not None:
      await _enqueue_agent_outbound(device_id, outbound, reply)
    await inbound.complete(item)


async def _send_agent_control_messages(
  websocket: WebSocket,
  *,
  control_session: AgentControlSession,
  outbound: _AgentOutboundBuffer,
) -> None:
  device_id = control_session.device_id
  while True:
    item = await outbound.get()
    _set_agent_control_queue_metrics(device_id, "outbound", outbound)
    queue_age = max(0.0, time.monotonic() - item.queued_monotonic)
    _observe_agent_control_stage(
      stage="outbound_queue_wait",
      envelope=item.envelope,
      duration=queue_age,
      device_id=device_id,
    )
    send_started = time.monotonic()
    if not await agent_connection_hub.is_connected(
      device_id,
      agent_session_id=control_session.agent_session_id,
    ):
      raise AuthError("UNAUTHENTICATED", "Agent 控制会话已被替换")
    if item.envelope.message_type in {
      AgentMessageType.COMMAND,
      AgentMessageType.CANCEL_COMMAND,
    }:
      await _assert_trade_delivery_session(control_session, item.envelope)
    try:
      await asyncio.wait_for(
        websocket.send_text(item.envelope.model_dump_json()),
        timeout=AGENT_CONTROL_SEND_TIMEOUT_SECONDS,
      )
    except asyncio.TimeoutError as exc:
      AGENT_CONTROL_EVENTS.labels(
        event="timeout",
        reason="socket_send",
      ).inc()
      raise _AgentControlPipelineError("socket_send_timeout") from exc
    await outbound.complete(item)
    _observe_agent_control_stage(
      stage="socket_send",
      envelope=item.envelope,
      duration=time.monotonic() - send_started,
      device_id=device_id,
    )


async def _assert_trade_delivery_session(
  control_session: AgentControlSession,
  envelope: AgentEnvelope,
) -> None:
  """Revalidate durable authority immediately before a trade frame is sent."""

  now = utcnow()
  async with AsyncSessionLocal() as db:
    device = await db.get(AgentDevice, control_session.device_id)
    heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{control_session.device_id}",
    )
    api_heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      API_HEARTBEAT_COMPONENT,
    )

  acceptable_statuses = (
    {"READY", "EMERGENCY_STOP", "RECONCILE_REQUIRED"}
    if envelope.message_type is AgentMessageType.CANCEL_COMMAND
    else {"READY"}
  )
  session_state = evaluate_agent_session(
    heartbeat,
    api_heartbeat,
    now=now,
    acceptable_statuses=acceptable_statuses,
  )
  account_id = str(envelope.payload.get("account_id") or "").strip()
  execution_mode = str(envelope.payload.get("execution_mode") or "").strip().lower()
  risk_increasing = bool(
    envelope.message_type is AgentMessageType.COMMAND
    and str(envelope.payload.get("command_kind") or "").upper() == "PLACE_ORDER"
    and str(envelope.payload.get("side") or "").upper() != "SELL"
    and str(envelope.payload.get("t_trade_role") or "").upper() != "EXIT"
  )
  market_stream_ready = bool(
    str(dict(getattr(heartbeat, "details", None) or {}).get("marketStreamStatus") or "")
    .strip()
    .upper()
    == "READY"
  )
  live_risk_increase = risk_increasing and execution_mode == "live"
  capabilities = {
    str(value).strip().lower()
    for value in control_session.capabilities
    if str(value).strip()
  }
  if (
    device is None
    or device.revoked_at is not None
    or not session_state.current
    or session_state.api_instance_id != control_session.api_instance_id
    or session_state.agent_session_id != control_session.agent_session_id
    or account_id not in control_session.authorized_account_ids
    or (execution_mode and execution_mode not in capabilities)
    or (live_risk_increase and not market_stream_ready)
  ):
    raise AuthError("UNAUTHENTICATED", "Agent 交易投递会话已失效")
  if live_risk_increase:
    try:
      safety_status = await AccountExecutionSafetyService().status(account_id)
    except Exception as exc:
      raise AuthError("UNAUTHENTICATED", "Agent 交易投递会话已失效") from exc
    if not bool(safety_status.get("can_increase_risk")):
      raise AuthError("UNAUTHENTICATED", "Agent 交易投递会话已失效")


async def _poll_agent_trade_commands(
  *,
  control_session: AgentControlSession,
  protocol_version: str,
  outbound: _AgentOutboundBuffer,
  database_state: _AgentDatabaseState,
) -> None:
  device_id = control_session.device_id
  while True:
    if not database_state.ready.is_set():
      await asyncio.sleep(AGENT_CONTROL_POLL_INTERVAL_SECONDS)
      continue
    try:
      command = await asyncio.wait_for(
        _next_command(
          control_session,
          protocol_version=protocol_version,
        ),
        timeout=AGENT_CONTROL_DATABASE_POLL_TIMEOUT_SECONDS,
      )
    except _TRANSIENT_DEPENDENCY_ERRORS:
      database_state.mark_failure()
      AGENT_CONTROL_EVENTS.labels(
        event="timeout",
        reason="trade_command_poll",
      ).inc()
      logger.warning("Agent trade-command poll timed out: device_id=%s", device_id)
    else:
      if command is not None:
        await _enqueue_agent_outbound(
          device_id,
          outbound,
          command,
          deduplicate=True,
        )
    await asyncio.sleep(AGENT_CONTROL_POLL_INTERVAL_SECONDS)


async def _poll_agent_market_requests(
  *,
  control_session: AgentControlSession,
  protocol_version: str,
  outbound: _AgentOutboundBuffer,
  database_state: _AgentDatabaseState,
) -> None:
  device_id = control_session.device_id
  while True:
    if not database_state.ready.is_set():
      await asyncio.sleep(AGENT_CONTROL_POLL_INTERVAL_SECONDS)
      continue
    try:
      request = await asyncio.wait_for(
        _next_market_data_request(
          control_session,
          protocol_version=protocol_version,
        ),
        timeout=AGENT_CONTROL_DATABASE_POLL_TIMEOUT_SECONDS,
      )
    except _TRANSIENT_DEPENDENCY_ERRORS:
      database_state.mark_failure()
      AGENT_CONTROL_EVENTS.labels(
        event="timeout",
        reason="market_request_poll",
      ).inc()
      logger.warning("Agent market-request poll timed out: device_id=%s", device_id)
    else:
      if request is not None:
        await _enqueue_agent_outbound(
          device_id,
          outbound,
          request,
          deduplicate=True,
        )
    await asyncio.sleep(AGENT_CONTROL_POLL_INTERVAL_SECONDS)


async def _relay_agent_hub_controls(
  *,
  control_session: AgentControlSession,
  protocol_version: str,
  outbound: _AgentOutboundBuffer,
) -> None:
  device_id = control_session.device_id
  while True:
    control = await control_session.queue.get()
    await _enqueue_agent_outbound(
      device_id,
      outbound,
      control.model_copy(update={"protocol_version": protocol_version}),
    )


async def _guard_agent_control_session(
  *,
  control_session: AgentControlSession,
  inbound: _AgentInboundBuffer,
  database_state: _AgentDatabaseState,
) -> None:
  device_id = control_session.device_id
  while True:
    now = utcnow()
    heartbeat_age = database_state.heartbeat_age()
    database_state._update_metrics()
    if (
      heartbeat_age >= AGENT_CONTROL_HEARTBEAT_STALE_SECONDS
      and database_state.ready.is_set()
      and inbound.qsize() == 0
    ):
      AGENT_CONTROL_EVENTS.labels(
        event="timeout",
        reason="heartbeat_transport_stale",
      ).inc()
      raise _AgentControlPipelineError("heartbeat_transport_stale")
    heartbeat_remaining = AGENT_CONTROL_HEARTBEAT_STALE_SECONDS - heartbeat_age
    timeout_seconds = 5.0 if heartbeat_remaining <= 0 else min(5.0, heartbeat_remaining)
    revoked = await agent_connection_hub.wait_until_revoked(
      control_session,
      timeout_seconds=timeout_seconds,
    )
    if revoked:
      raise AuthError("UNAUTHENTICATED", "Agent 设备已撤销")

    async def load_authority():
      async with AsyncSessionLocal() as db:
        return (
          await db.get(AgentDevice, device_id),
          await db.get(
            RuntimeComponentHeartbeat,
            f"qmt-agent:{device_id}",
          ),
          await db.get(
            RuntimeComponentHeartbeat,
            API_HEARTBEAT_COMPONENT,
          ),
        )

    try:
      device, heartbeat, api_heartbeat = await asyncio.wait_for(
        load_authority(),
        timeout=AGENT_CONTROL_DATABASE_POLL_TIMEOUT_SECONDS,
      )
    except _TRANSIENT_DEPENDENCY_ERRORS as exc:
      database_state.mark_failure()
      AGENT_CONTROL_EVENTS.labels(
        event="dependency",
        reason="session_authority_revalidation_deferred",
      ).inc()
      logger.warning(
        "Agent session authority revalidation deferred without disconnect: "
        "device_id=%s error=%s",
        device_id,
        exc.__class__.__name__,
      )
      continue
    details = dict(heartbeat.details or {}) if heartbeat is not None else {}
    if (
      device is None
      or device.revoked_at is not None
      or str(getattr(api_heartbeat, "instance_id", "") or "")
      != control_session.api_instance_id
      or not api_instance_is_current(api_heartbeat, now=now)
      or str(details.get("apiInstanceId") or "") != control_session.api_instance_id
      or str(details.get("agentSessionId") or "") != control_session.agent_session_id
    ):
      raise AuthError("UNAUTHENTICATED", "Agent 设备已撤销或控制会话已被替换")


async def _refresh_agent_market_lease(
  *,
  control_session: AgentControlSession,
) -> None:
  while True:
    try:
      await asyncio.wait_for(
        agent_connection_hub.refresh_market_device(control_session),
        timeout=AGENT_CONTROL_DATABASE_POLL_TIMEOUT_SECONDS,
      )
    except asyncio.CancelledError:
      raise
    except _TRANSIENT_DEPENDENCY_ERRORS as exc:
      AGENT_CONTROL_EVENTS.labels(
        event="dependency",
        reason="market_lease_refresh_deferred",
      ).inc()
      logger.warning(
        "Agent market lease refresh deferred without closing control session: "
        "device_id=%s error=%s",
        control_session.device_id,
        exc.__class__.__name__,
      )
      await asyncio.sleep(AGENT_CONTROL_DEPENDENCY_RETRY_SECONDS)
      continue
    await asyncio.sleep(MARKET_DEVICE_LEASE_REFRESH_SECONDS)


async def _run_agent_control_pipeline(
  websocket: WebSocket,
  *,
  control_session: AgentControlSession,
  protocol_version: str,
) -> None:
  device_id = control_session.device_id
  inbound = _AgentInboundBuffer()
  outbound = _AgentOutboundBuffer()
  database_state = _AgentDatabaseState(device_id=device_id)
  tasks = {
    asyncio.create_task(
      _receive_agent_control_messages(
        websocket,
        device_id=device_id,
        protocol_version=protocol_version,
        inbound=inbound,
        database_state=database_state,
      ),
      name=f"agent-control-receiver:{device_id}",
    ),
    asyncio.create_task(
      _process_agent_control_messages(
        control_session=control_session,
        protocol_version=protocol_version,
        inbound=inbound,
        outbound=outbound,
        database_state=database_state,
      ),
      name=f"agent-control-processor:{device_id}",
    ),
    asyncio.create_task(
      _send_agent_control_messages(
        websocket,
        control_session=control_session,
        outbound=outbound,
      ),
      name=f"agent-control-writer:{device_id}",
    ),
    asyncio.create_task(
      _poll_agent_trade_commands(
        control_session=control_session,
        protocol_version=protocol_version,
        outbound=outbound,
        database_state=database_state,
      ),
      name=f"agent-command-poller:{device_id}",
    ),
    asyncio.create_task(
      _poll_agent_market_requests(
        control_session=control_session,
        protocol_version=protocol_version,
        outbound=outbound,
        database_state=database_state,
      ),
      name=f"agent-market-request-poller:{device_id}",
    ),
    asyncio.create_task(
      _relay_agent_hub_controls(
        control_session=control_session,
        protocol_version=protocol_version,
        outbound=outbound,
      ),
      name=f"agent-control-relay:{device_id}",
    ),
    asyncio.create_task(
      _guard_agent_control_session(
        control_session=control_session,
        inbound=inbound,
        database_state=database_state,
      ),
      name=f"agent-control-guard:{device_id}",
    ),
    asyncio.create_task(
      _refresh_agent_market_lease(
        control_session=control_session,
      ),
      name=f"agent-market-lease:{device_id}",
    ),
  }
  try:
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for task in done:
      error = task.exception()
      if error is not None:
        raise error
    raise WebSocketDisconnect(code=1000)
  finally:
    for task in tasks:
      if not task.done():
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    AGENT_CONTROL_QUEUE_DEPTH.labels(
      device_id=device_id,
      direction="inbound",
    ).set(0)
    AGENT_CONTROL_QUEUE_DEPTH.labels(
      device_id=device_id,
      direction="outbound",
    ).set(0)
    AGENT_CONTROL_QUEUE_OLDEST_AGE.labels(
      device_id=device_id,
      direction="inbound",
    ).set(0)
    AGENT_CONTROL_QUEUE_OLDEST_AGE.labels(
      device_id=device_id,
      direction="outbound",
    ).set(0)


@agent_router.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket) -> None:
  await websocket.accept()
  try:
    first = AgentEnvelope.model_validate_json(await websocket.receive_text())
    session = await _authenticate(first)
    device = session.device
    capabilities = _normalized_agent_capabilities(first.payload.get("capabilities", []))
    if "live" in capabilities and first.protocol_version != PROTOCOL_VERSION:
      raise AuthError(
        "PROTOCOL_UPGRADE_REQUIRED",
        f"真实交易 Agent 必须使用协议 {PROTOCOL_VERSION}",
      )
    connection_protocol = first.protocol_version
    control_session = await agent_connection_hub.register(
      device.id,
      capabilities,
      authorized_account_ids={
        str(value).strip()
        for value in list(device.authorized_account_ids or [])
        if str(value).strip()
      },
      connected_at=utcnow(),
      remote_address_summary=_remote_address_summary(websocket),
    )
    await _record_heartbeat(
      control_session,
      {
        "agent_version": first.payload.get("agent_version", ""),
        "protocol_version": connection_protocol,
        "capabilities": first.payload.get("capabilities", []),
        "status": "RECONCILING",
      },
      sent_at=first.sent_at,
      establish=True,
    )
    await _requeue_incomplete_market_requests(device.id)
    await websocket.send_text(
      _auth_result(
        accepted=True,
        protocol_version=connection_protocol,
        agent_session_id=control_session.agent_session_id,
      ).model_dump_json()
    )
    await _run_agent_control_pipeline(
      websocket,
      control_session=control_session,
      protocol_version=connection_protocol,
    )
  except WebSocketDisconnect:
    AGENT_CONTROL_EVENTS.labels(event="close", reason="disconnect").inc()
    return
  except _AgentControlPipelineError as exc:
    AGENT_CONTROL_EVENTS.labels(event="close", reason=exc.reason).inc()
    logger.warning("Agent WebSocket closed: reason=%s", exc.reason)
    try:
      await websocket.close(code=exc.close_code, reason=exc.reason[:120])
    except Exception:
      pass
  except AuthError as exc:
    AGENT_CONTROL_EVENTS.labels(event="close", reason="auth_error").inc()
    try:
      await websocket.send_text(
        _auth_result(
          accepted=False,
          reason=exc.message,
          protocol_version=(
            first.protocol_version if "first" in locals() else PROTOCOL_VERSION
          ),
        ).model_dump_json()
      )
      await websocket.close(code=4401)
    except Exception:
      pass
  except Exception as exc:
    AGENT_CONTROL_EVENTS.labels(
      event="close",
      reason=exc.__class__.__name__,
    ).inc()
    logger.warning("Agent WebSocket closed: %s", exc.__class__.__name__)
    try:
      await websocket.close(code=4400)
    except Exception:
      pass
  finally:
    if "control_session" in locals():
      if await agent_connection_hub.unregister(control_session):
        try:
          await _mark_session_offline(control_session)
        except Exception as exc:
          logger.warning(
            "无法持久化 Agent 离线状态: device=%s error=%s",
            control_session.device_id,
            exc.__class__.__name__,
          )


def _bearer(request: Request) -> str:
  scheme, separator, token = request.headers.get("authorization", "").partition(" ")
  if not separator or scheme.lower() != "bearer" or not token.strip():
    raise HTTPException(status_code=401, detail="缺少 Agent Bearer Token")
  return token.strip()


async def _read_limited_body(
  request: Request,
  *,
  limit: int = MAX_MARKET_DATA_CHUNK_BYTES,
) -> bytes:
  content_length = request.headers.get("content-length", "").strip()
  if content_length:
    try:
      declared_length = int(content_length)
    except ValueError as exc:
      raise HTTPException(status_code=400, detail="Content-Length 无效") from exc
    if declared_length < 0:
      raise HTTPException(status_code=400, detail="Content-Length 无效")
    if declared_length > limit:
      raise HTTPException(status_code=413, detail="行情批次超过大小限制")

  body = bytearray()
  async for chunk in request.stream():
    body.extend(chunk)
    if len(body) > limit:
      raise HTTPException(status_code=413, detail="行情批次超过大小限制")
  return bytes(body)


def _market_data_staging_usage_bytes(root: Path) -> int:
  """Return actual retained bytes, failing closed on linked staging content."""
  if not root.exists():
    return 0
  if root.is_symlink() or _is_reparse_point(root):
    raise RuntimeError("unsafe market-data staging root")
  resolved_root = root.resolve()
  total = 0
  for path in root.rglob("*"):
    if path.is_symlink() or _is_reparse_point(path):
      raise RuntimeError("market-data staging contains a reparse point")
    if not path.is_file():
      continue
    resolved = path.resolve()
    if resolved_root not in resolved.parents:
      raise RuntimeError("market-data staging file escaped its root")
    total += path.stat().st_size
  return total


def _market_data_staging_free_bytes(root: Path) -> int:
  return int(shutil.disk_usage(root).free)


async def _market_data_manifest_is_complete(db, market_request) -> bool:
  expected = int(market_request.expected_chunks or 0)
  if expected <= 0:
    return False
  persisted = int(
    await db.scalar(
      select(func.count())
      .select_from(MarketDataTransfer)
      .where(MarketDataTransfer.request_id == market_request.request_id)
    )
    or 0
  )
  return persisted == expected


async def _fail_mutable_market_data_request(
  db,
  market_request,
  *,
  reason: str,
) -> bool:
  status = str(market_request.status or "").upper()
  if status not in _MARKET_DATA_MUTABLE_UPLOAD_STATUSES:
    return False
  if await _market_data_manifest_is_complete(db, market_request):
    return False
  market_request.status = "FAILED"
  market_request.processing_error = reason[:1000]
  market_request.completed_at = utcnow()
  await db.commit()
  return True


def _as_aware_utc(value: datetime | None) -> datetime | None:
  if value is None:
    return None
  if value.tzinfo is None:
    return value.replace(tzinfo=timezone.utc)
  return value.astimezone(timezone.utc)


def _remove_safe_market_data_request_directory(root: Path, candidate: Path) -> None:
  try:
    resolved = _safe_market_data_request_directory(root, candidate)
  except FileNotFoundError:
    return
  for descendant in resolved.rglob("*"):
    if descendant.is_symlink() or _is_reparse_point(descendant):
      raise RuntimeError("refusing to remove linked market-data staging content")
    resolved_descendant = descendant.resolve()
    if resolved not in resolved_descendant.parents:
      raise RuntimeError("market-data staging content escaped request directory")
  shutil.rmtree(resolved)


async def sweep_market_data_staging_once(
  *,
  now: datetime | None = None,
) -> dict[str, int]:
  """Remove stale temporary, terminal, and orphan Agent upload staging safely."""
  current = _as_aware_utc(now if now is not None else utcnow())
  if current is None:  # pragma: no cover - the expression above is never None
    raise RuntimeError("market-data staging sweep requires a clock value")
  removed_directories = 0
  removed_temporary_files = 0
  root = MARKET_DATA_ROOT
  if not root.exists():
    return {"directories": 0, "temporary_files": 0}

  # Uploads acquire a request row lock before the staging lock. The sweeper uses
  # a distinct process lock and relies on the same request row lock, avoiding a
  # staging-lock/row-lock inversion while still serializing sweep runs.
  async with _market_data_staging_sweep_lock:
    if root.is_symlink() or _is_reparse_point(root):
      raise RuntimeError("unsafe market-data staging root")
    candidates: dict[str, Path] = {}
    for child in list(root.iterdir()):
      if not child.is_dir():
        continue
      try:
        resolved = _safe_market_data_request_directory(root, child)
      except RuntimeError:
        logger.warning("Skipped unsafe market-data staging entry: %s", child)
        continue
      candidates[child.name] = resolved
      temporary_cutoff = current - timedelta(
        seconds=MARKET_DATA_STAGING_TEMP_GRACE_SECONDS
      )
      for temporary in resolved.glob("*.tmp"):
        if temporary.is_symlink() or _is_reparse_point(temporary):
          logger.warning("Skipped unsafe market-data staging temp: %s", temporary)
          continue
        modified = datetime.fromtimestamp(
          temporary.stat().st_mtime,
          tz=timezone.utc,
        )
        if modified <= temporary_cutoff:
          temporary.unlink(missing_ok=True)
          removed_temporary_files += 1

    if not candidates:
      return {
        "directories": removed_directories,
        "temporary_files": removed_temporary_files,
      }

    async with AsyncSessionLocal() as db:
      rows = (
        await db.execute(
          select(
            MarketDataRequest.request_id,
            MarketDataRequest.status,
            MarketDataRequest.completed_at,
            MarketDataRequest.updated_at,
          ).where(MarketDataRequest.request_id.in_(tuple(candidates)))
        )
      ).all()
    requests = {str(row.request_id): row for row in rows}
    orphan_cutoff = current - timedelta(
      seconds=MARKET_DATA_STAGING_ORPHAN_GRACE_SECONDS
    )
    failed_cutoff = current - timedelta(
      seconds=MARKET_DATA_STAGING_FAILED_RETENTION_SECONDS
    )
    for request_id, directory in candidates.items():
      request_row = requests.get(request_id)
      remove = False
      if request_row is None:
        modified = datetime.fromtimestamp(
          directory.stat().st_mtime,
          tz=timezone.utc,
        )
        remove = modified <= orphan_cutoff
      else:
        status = str(request_row.status or "").upper()
        if status in {"COMPLETED", "FAILED"}:
          # Recheck under the request row lock. A FAILED request may be reopened
          # for ingestion by another process between the initial scan and delete.
          async with AsyncSessionLocal() as db:
            locked = await db.scalar(
              select(MarketDataRequest)
              .where(MarketDataRequest.request_id == request_id)
              .with_for_update()
            )
            retired_failed_manifest = False
            if locked is not None:
              locked_status = str(locked.status or "").upper()
              if locked_status == "COMPLETED":
                remove = True
              elif locked_status == "FAILED":
                terminal_at = _as_aware_utc(locked.completed_at or locked.updated_at)
                remove = terminal_at is not None and terminal_at <= failed_cutoff
                if remove:
                  # Retiring the durable manifest before deleting its files
                  # prevents a later FAILED recovery from reopening paths that
                  # no longer exist. It will derive a fresh request instead.
                  await db.execute(
                    delete(MarketDataTransfer).where(
                      MarketDataTransfer.request_id == request_id
                    )
                  )
                  locked.expected_chunks = None
                  locked.received_chunks = 0
                  await db.commit()
                  retired_failed_manifest = True
              if remove:
                await asyncio.to_thread(
                  _remove_safe_market_data_request_directory,
                  root,
                  directory,
                )
                removed_directories += 1
            if not retired_failed_manifest:
              await db.rollback()
          continue
      if not remove:
        continue
      await asyncio.to_thread(
        _remove_safe_market_data_request_directory,
        root,
        directory,
      )
      removed_directories += 1

  return {
    "directories": removed_directories,
    "temporary_files": removed_temporary_files,
  }


async def run_market_data_staging_sweeper(stopped: asyncio.Event) -> None:
  while not stopped.is_set():
    try:
      removed = await sweep_market_data_staging_once()
      if removed["directories"] or removed["temporary_files"]:
        logger.info("Cleaned market-data staging: %s", removed)
    except asyncio.CancelledError:
      raise
    except Exception:
      logger.exception("Could not sweep market-data staging")
    try:
      await asyncio.wait_for(
        stopped.wait(),
        timeout=MARKET_DATA_STAGING_SWEEP_SECONDS,
      )
    except asyncio.TimeoutError:
      pass


async def _fail_market_data_upload(
  *,
  request_id: str,
  device_id: str,
  reason: str,
) -> None:
  """Terminate a poisoned transfer so reconnecting Agents do not retry forever."""
  async with AsyncSessionLocal() as db:
    market_request = await db.scalar(
      select(MarketDataRequest)
      .where(
        MarketDataRequest.request_id == request_id,
        MarketDataRequest.device_id == device_id,
      )
      .with_for_update()
    )
    if market_request is None:
      return
    status = str(market_request.status or "").upper()
    if status in _MARKET_DATA_FROZEN_MANIFEST_STATUSES or status == "FAILED":
      return
    if status not in _MARKET_DATA_MUTABLE_UPLOAD_STATUSES:
      return
    await _fail_mutable_market_data_request(
      db,
      market_request,
      reason=reason,
    )


async def _requeue_busy_market_data_request(
  *,
  request_id: str,
  device_id: str,
) -> bool:
  """Return an undispatched request to the durable queue after Agent backpressure."""

  async with AsyncSessionLocal() as db:
    market_request = await db.scalar(
      select(MarketDataRequest)
      .where(
        MarketDataRequest.request_id == request_id,
        MarketDataRequest.device_id == device_id,
      )
      .with_for_update()
    )
    if (
      market_request is None or str(market_request.status or "").upper() != "DELIVERED"
    ):
      return False
    market_request.status = "QUEUED"
    market_request.processing_error = "Agent busy: market-data request queue full"
    market_request.updated_at = utcnow()
    await db.commit()
    return True


@agent_router.post(
  "/agent/market-data/{request_id}/fail",
  status_code=202,
)
async def fail_market_data_request(
  request_id: str,
  request: Request,
):
  """Accept an Agent terminal rejection or a retryable queue-busy outcome."""
  try:
    normalized_request_id = str(uuid.UUID(request_id))
  except ValueError as exc:
    raise HTTPException(status_code=400, detail="request_id 无效") from exc

  async with AsyncSessionLocal() as db:
    try:
      device = await AgentAuthService(db).authenticate_agent(token=_bearer(request))
      authenticated_device_id = device.id
    except AuthError as exc:
      raise HTTPException(
        status_code=exc.status_code,
        detail=exc.message,
      ) from exc

  raw = await _read_limited_body(request, limit=4096)
  try:
    payload = json.loads(raw.decode("utf-8"))
  except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise HTTPException(status_code=400, detail="失败原因格式无效") from exc
  reason = str(payload.get("reason") or "").strip()
  if not reason:
    raise HTTPException(status_code=400, detail="失败原因不能为空")
  if reason == _MARKET_DATA_AGENT_BUSY_REASON:
    retryable = await _requeue_busy_market_data_request(
      request_id=normalized_request_id,
      device_id=authenticated_device_id,
    )
    return {"accepted": True, "retryable": retryable}
  await _fail_market_data_upload(
    request_id=normalized_request_id,
    device_id=authenticated_device_id,
    reason=f"Agent rejected request: {reason}",
  )
  return {"accepted": True}


def _matches_sha256_digest(digest: str, expected: str) -> bool:
  if not expected:
    return False
  return hmac.compare_digest(digest, expected.lower())


@agent_router.put(
  "/agent/market-data/{request_id}/chunks/{chunk_index}",
  status_code=202,
)
async def upload_market_data_chunk(
  request_id: str,
  chunk_index: int,
  request: Request,
  x_content_sha256: str = Header(alias="X-Content-SHA256"),
  x_record_count: int = Header(alias="X-Record-Count"),
  x_total_chunks: int = Header(alias="X-Total-Chunks"),
  content_encoding: str = Header(alias="Content-Encoding"),
):
  try:
    normalized_request_id = str(uuid.UUID(request_id))
  except ValueError as exc:
    raise HTTPException(status_code=400, detail="request_id 无效") from exc
  if (
    chunk_index < 0
    or x_total_chunks <= 0
    or x_total_chunks > MAX_MARKET_DATA_CHUNKS
    or chunk_index >= x_total_chunks
  ):
    raise HTTPException(status_code=400, detail="行情批次序号无效")
  if x_record_count < 0 or x_record_count > MAX_MARKET_DATA_CHUNK_RECORDS:
    raise HTTPException(status_code=400, detail="行情批次记录数无效")
  if content_encoding.strip().lower() != "gzip":
    raise HTTPException(status_code=415, detail="行情批次必须使用 gzip 压缩")

  async with AsyncSessionLocal() as db:
    try:
      device = await AgentAuthService(db).authenticate_agent(token=_bearer(request))
      authenticated_device_id = device.id
    except AuthError as exc:
      raise HTTPException(
        status_code=exc.status_code,
        detail=exc.message,
      ) from exc

  try:
    raw = await _read_limited_body(request)
  except HTTPException as exc:
    await _fail_market_data_upload(
      request_id=normalized_request_id,
      device_id=authenticated_device_id,
      reason=f"chunk {chunk_index} rejected: {exc.detail}",
    )
    raise
  digest = hashlib.sha256(raw).hexdigest()
  if not _matches_sha256_digest(digest, x_content_sha256):
    await _fail_market_data_upload(
      request_id=normalized_request_id,
      device_id=authenticated_device_id,
      reason=f"chunk {chunk_index} SHA256 verification failed",
    )
    raise HTTPException(status_code=422, detail="行情批次 SHA256 校验失败")

  async with AsyncSessionLocal() as db:
    market_request = await db.scalar(
      select(MarketDataRequest)
      .where(MarketDataRequest.request_id == normalized_request_id)
      .with_for_update()
    )
    if market_request is None or market_request.device_id != authenticated_device_id:
      raise HTTPException(status_code=404, detail="行情数据请求不存在")
    status = str(market_request.status or "").upper()
    if status == "FAILED":
      raise HTTPException(status_code=409, detail="行情数据请求已经结束")
    if (
      market_request.expected_chunks is not None
      and int(market_request.expected_chunks) != x_total_chunks
    ):
      await _fail_mutable_market_data_request(
        db,
        market_request,
        reason=f"chunk {chunk_index} total_chunks mismatch",
      )
      raise HTTPException(status_code=409, detail="行情批次总数与首次上传不一致")
    existing = (
      await db.execute(
        select(MarketDataTransfer).where(
          MarketDataTransfer.request_id == normalized_request_id,
          MarketDataTransfer.chunk_index == chunk_index,
        )
      )
    ).scalar_one_or_none()
    if existing is not None:
      if existing.checksum_sha256 != digest:
        await _fail_mutable_market_data_request(
          db,
          market_request,
          reason=f"chunk {chunk_index} checksum mismatch",
        )
        raise HTTPException(status_code=409, detail="重复批次内容不一致")
      if int(existing.record_count) != x_record_count:
        await _fail_mutable_market_data_request(
          db,
          market_request,
          reason=f"chunk {chunk_index} record_count mismatch",
        )
        raise HTTPException(status_code=409, detail="重复批次记录数不一致")
      return {"accepted": True, "duplicate": True}
    if status in _MARKET_DATA_FROZEN_MANIFEST_STATUSES:
      raise HTTPException(status_code=409, detail="行情数据 manifest 已冻结")
    if status not in _MARKET_DATA_MUTABLE_UPLOAD_STATUSES:
      raise HTTPException(status_code=409, detail="行情数据请求已经结束")

    async with _market_data_staging_lock:
      MARKET_DATA_ROOT.mkdir(parents=True, exist_ok=True)
      if MARKET_DATA_ROOT.is_symlink() or _is_reparse_point(MARKET_DATA_ROOT):
        raise HTTPException(status_code=507, detail="行情 staging 根目录不安全")
      destination_directory = MARKET_DATA_ROOT / normalized_request_id
      destination_directory.mkdir(parents=False, exist_ok=True)
      try:
        destination_directory = _safe_market_data_request_directory(
          MARKET_DATA_ROOT,
          destination_directory,
        )
      except RuntimeError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
      destination = destination_directory / f"{chunk_index:08d}.json.gz"
      if destination.is_symlink() or _is_reparse_point(destination):
        raise HTTPException(status_code=507, detail="行情 staging 文件不安全")

      request_compressed_bytes = int(
        await db.scalar(
          select(func.coalesce(func.sum(MarketDataTransfer.compressed_bytes), 0)).where(
            MarketDataTransfer.request_id == normalized_request_id
          )
        )
        or 0
      )
      try:
        actual_request_bytes = await asyncio.to_thread(
          _market_data_request_staging_usage_bytes,
          root=MARKET_DATA_ROOT,
          request_id=normalized_request_id,
        )
        existing_file_bytes = (
          destination.stat().st_size
          if destination.exists() and destination.is_file()
          else 0
        )
      except (OSError, RuntimeError) as exc:
        raise HTTPException(
          status_code=507,
          detail="无法验证行情 request staging 容量",
        ) from exc
      additional_bytes = max(0, len(raw) - existing_file_bytes)
      if (
        max(request_compressed_bytes, actual_request_bytes) + additional_bytes
        > MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES
      ):
        await _fail_mutable_market_data_request(
          db,
          market_request,
          reason="market-data request exceeds compressed byte limit",
        )
        raise HTTPException(status_code=413, detail="行情请求压缩数据超过大小限制")

      try:
        retained_bytes = await asyncio.to_thread(
          _market_data_staging_usage_bytes,
          MARKET_DATA_ROOT,
        )
        free_bytes = await asyncio.to_thread(
          _market_data_staging_free_bytes,
          MARKET_DATA_ROOT,
        )
      except (OSError, RuntimeError) as exc:
        raise HTTPException(
          status_code=507,
          detail="无法验证行情 staging 容量",
        ) from exc
      if retained_bytes + additional_bytes > MAX_MARKET_DATA_STAGING_BYTES:
        raise HTTPException(status_code=507, detail="行情 staging 总容量不足")
      if free_bytes - additional_bytes < MIN_MARKET_DATA_STAGING_FREE_BYTES:
        raise HTTPException(status_code=507, detail="行情 staging 磁盘余量不足")

      temporary = destination.with_suffix(
        f"{destination.suffix}.{uuid.uuid4().hex}.tmp"
      )
      destination_written = False
      commit_started = False
      committed = False
      try:
        async with aiofiles.open(temporary, "wb") as output:
          await output.write(raw)
        os.replace(temporary, destination)
        destination_written = True
        db.add(
          MarketDataTransfer(
            transfer_id=str(uuid.uuid4()),
            request_id=normalized_request_id,
            chunk_index=chunk_index,
            checksum_sha256=digest,
            record_count=x_record_count,
            compressed_bytes=len(raw),
            compressed=True,
            storage_reference=_relative_market_data_storage_reference(
              root=MARKET_DATA_ROOT,
              candidate=destination,
            ),
            received_at=utcnow(),
          )
        )
        market_request.expected_chunks = x_total_chunks
        await db.flush()
        market_request.received_chunks = int(
          await db.scalar(
            select(func.count())
            .select_from(MarketDataTransfer)
            .where(MarketDataTransfer.request_id == normalized_request_id)
          )
          or 0
        )
        market_request.status = (
          "UPLOADED"
          if market_request.received_chunks == x_total_chunks
          else "RECEIVING"
        )
        commit_started = True
        await db.commit()
        committed = True
      finally:
        temporary.unlink(missing_ok=True)
        # Once COMMIT starts its outcome may be unknown after cancellation or a
        # connection loss. Retain the immutable file for retry/reconciliation;
        # the orphan sweeper removes it only when no durable request references it.
        if destination_written and not committed and not commit_started:
          destination.unlink(missing_ok=True)
  return {"accepted": True, "duplicate": False}
