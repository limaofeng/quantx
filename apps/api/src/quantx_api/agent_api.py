"""Outbound-only QMT Agent WebSocket hub and durable report ingestion."""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import ipaddress
import json
import logging
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Optional

from fastapi import (
  APIRouter,
  WebSocket,
  WebSocketDisconnect,
)
from quantx_contracts import (
  PROTOCOL_VERSION,
  AgentEnvelope,
  AgentMessageType,
  CancelCommandPayload,
  CommandAckPayload,
  ExecutionEnvironment,
  ExecutionOwnerType,
  ReportAckPayload,
  TradeCommandPayload,
)
from quantx_infrastructure.auth.errors import AuthError
from quantx_infrastructure.auth.tokens import utcnow
from quantx_infrastructure.database.redis_pubsub import (
  AGENT_REPORT_WAKE_CHANNEL,
  redis_pubsub,
)
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  AgentReportInbox,
  MarketDataRequest,
  OrderCorrelation,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.account_execution_quarantine_service import (
  PHYSICAL_SEND_CANCELLED_REASON,
  AccountExecutionQuarantineService,
)
from quantx_infrastructure.services.account_execution_safety_service import (
  AccountExecutionSafetyService,
)
from quantx_infrastructure.services.agent_session_guard import (
  AGENT_SERVER_SESSION_PAYLOAD_KEY,
  QMT_ACCOUNT_MISMATCH,
  QMT_AGENT_NOT_RECONCILED,
  QMT_AGENT_OFFLINE,
  QMT_CONTROL_DEPENDENCY_UNAVAILABLE,
  QMT_CONTROL_SESSION_REPLACED,
  QMT_CONTROL_TRANSPORT_LOST,
  QMT_DEVICE_REVOKED,
  evaluate_agent_session,
  parse_utc_timestamp,
  to_naive_utc,
  utc_iso,
)
from quantx_infrastructure.services.exit_plan_zero_fill_safety import (
  invalidate_exit_plan_zero_fill_proof,
)
from quantx_infrastructure.services.market_stream_readiness import (
  authoritative_market_stream_tradable,
)
from quantx_infrastructure.services.trade_intent_processor import (
  LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE,
  LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE,
)
from redis.exceptions import RedisError
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from quantx_api.agent_hub import (
  MARKET_DEVICE_LEASE_REFRESH_SECONDS,
  AgentControlSession,
  agent_connection_hub,
)
from quantx_api.auth.agent_service import AgentAuthService
from quantx_api.monitoring.metrics import (
  AGENT_CONTROL_DATABASE_STATE,
  AGENT_CONTROL_EVENTS,
  AGENT_CONTROL_QUEUE_DEPTH,
  AGENT_CONTROL_QUEUE_OLDEST_AGE,
  AGENT_CONTROL_STAGE_DURATION,
)

logger = logging.getLogger(__name__)
agent_router = APIRouter(tags=["qmt-agent"])
REPORT_TYPES = {
  AgentMessageType.ORDER_REPORT,
  AgentMessageType.EXECUTION_REPORT,
  AgentMessageType.DELTA_REPORT,
}
TRADE_COMMAND_EXPIRY_SWEEP_INTERVAL_SECONDS = 1.0
TRADE_COMMAND_EXPIRY_SWEEP_BATCH_SIZE = 100
TRADE_COMMAND_REDELIVERY_SECONDS = 10.0
AGENT_CONTROL_INBOUND_QUEUE_CAPACITY = 32
AGENT_CONTROL_INBOUND_QUEUE_MAX_BYTES = 32 * 1024 * 1024
AGENT_CONTROL_OUTBOUND_QUEUE_CAPACITY = 64
AGENT_CONTROL_OUTBOUND_ACK_RESERVE = 16
AGENT_CONTROL_MAX_QUEUE_AGE_SECONDS = 5.0
AGENT_CONTROL_INBOUND_PROCESSING_TIMEOUT_SECONDS = 10.0
AGENT_CONTROL_DATABASE_POLL_TIMEOUT_SECONDS = 5.0
AGENT_CONTROL_TRADE_VALIDATION_TIMEOUT_SECONDS = 5.0
AGENT_CONTROL_TRADE_VALIDATION_HIGH_CAPACITY = 16
AGENT_CONTROL_TRADE_VALIDATION_NORMAL_CAPACITY = 32
AGENT_CONTROL_TRADE_VALIDATION_HIGH_WORKERS = 2
AGENT_CONTROL_TRADE_VALIDATION_NORMAL_WORKERS = 2
AGENT_CONTROL_DEPENDENCY_RETRY_SECONDS = 0.5
AGENT_CONTROL_SEND_TIMEOUT_SECONDS = 5.0
AGENT_CONTROL_POLL_INTERVAL_SECONDS = 1.0
AGENT_CONTROL_SLOW_STAGE_SECONDS = 1.0
AGENT_CONTROL_HEARTBEAT_STALE_SECONDS = 90.0
AGENT_CONTROL_CPU_OFFLOAD_CHARS = 64 * 1024
AGENT_CONTROL_CPU_OFFLOAD_BYTES = 256 * 1024
AGENT_CONTROL_CPU_WORKERS = 2

_MARKET_DATA_INFLIGHT_STATUSES = frozenset(
  {"DELIVERED", "RECEIVING", "UPLOADED", "PROCESSING"}
)
_MARKET_DATA_NATIVE_DISPATCH_STATUSES = frozenset({"DELIVERED", "RECEIVING"})
# UPLOADED is a frozen manifest: the Agent has completed native preparation.
# One next download may overlap ingestion. Two retained requests bound decoded
# data to 2 * 512 MiB and compressed data to 2 * 256 MiB, with existing global
# staging/free-disk and Agent spool quotas still enforced at every write.
MAX_MARKET_DATA_INFLIGHT_REQUESTS_PER_DEVICE = 2
MARKET_DATA_RECONNECT_STALE_SECONDS = 5 * 60
_agent_control_cpu_executor = ThreadPoolExecutor(
  max_workers=AGENT_CONTROL_CPU_WORKERS,
  thread_name_prefix="agent-control-cpu",
)


class _AgentControlPipelineError(RuntimeError):
  def __init__(
    self,
    reason: str,
    *,
    close_code: int = 1011,
    reason_code: str = QMT_CONTROL_TRANSPORT_LOST,
  ) -> None:
    super().__init__(reason)
    self.reason = reason
    self.close_code = close_code
    self.reason_code = reason_code


class _TradeCommandDeliveryDeferred(RuntimeError):
  """The command remains durable but its current delivery gate is closed."""

  def __init__(self, reason: str) -> None:
    super().__init__(reason)
    self.reason = reason


_TRANSIENT_DATABASE_ERRORS = (SQLAlchemyTimeoutError, DBAPIError)
_TRANSIENT_DEPENDENCY_ERRORS = _TRANSIENT_DATABASE_ERRORS + (
  ConnectionError,
  asyncio.TimeoutError,
  RedisError,
)


@dataclass
class _AgentDatabaseState:
  device_id: str
  control_session: AgentControlSession | None = None
  ready: asyncio.Event = field(default_factory=asyncio.Event)
  last_heartbeat_received_monotonic: float = field(default_factory=time.monotonic)
  consecutive_failures: int = 0

  def __post_init__(self) -> None:
    self.ready.set()
    self._update_metrics()

  def mark_success(self) -> None:
    self.ready.set()
    self.consecutive_failures = 0
    if self.control_session is not None:
      self.control_session.dependency_ready = True
      self.control_session.dependency_failure_count = 0
      self.control_session.dependency_reason = ""
    self._update_metrics()

  def mark_heartbeat_received(self) -> None:
    self.last_heartbeat_received_monotonic = time.monotonic()
    if self.control_session is not None:
      self.control_session.last_heartbeat_received_monotonic = (
        self.last_heartbeat_received_monotonic
      )
    self._update_metrics()

  def mark_failure(
    self,
    reason: str = QMT_CONTROL_DEPENDENCY_UNAVAILABLE,
  ) -> None:
    self.ready.clear()
    self.consecutive_failures += 1
    if self.control_session is not None:
      self.control_session.dependency_ready = False
      self.control_session.dependency_failure_count = self.consecutive_failures
      self.control_session.dependency_reason = str(
        reason or QMT_CONTROL_DEPENDENCY_UNAVAILABLE
      )
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


@dataclass(frozen=True)
class _PreparedReportPersistence:
  payload_hash: str
  business_idempotency_key: str


@dataclass(order=True)
class _AgentOutboundItem:
  priority: int
  sequence: int
  envelope: AgentEnvelope = field(compare=False)
  queued_monotonic: float = field(compare=False)
  protocol_reply: bool = field(compare=False)
  dedup_key: str = field(compare=False, default="")


@dataclass(frozen=True)
class _TradeCommandValidationItem:
  envelope: AgentEnvelope
  queued_monotonic: float


_TRADE_VALIDATION_HIGH_LANE = "high"
_TRADE_VALIDATION_NORMAL_LANE = "normal"


def _trade_validation_lane(envelope: AgentEnvelope) -> str:
  if envelope.message_type is AgentMessageType.CANCEL_COMMAND or (
    envelope.message_type is AgentMessageType.COMMAND
    and str(envelope.payload.get("command_kind") or "").upper()
    == "EMERGENCY_STOP"
  ):
    return _TRADE_VALIDATION_HIGH_LANE
  return _TRADE_VALIDATION_NORMAL_LANE


class _TradeCommandValidationBuffer:
  """Bound validation work while reserving workers and capacity for exits."""

  def __init__(
    self,
    *,
    high_capacity: int = AGENT_CONTROL_TRADE_VALIDATION_HIGH_CAPACITY,
    normal_capacity: int = AGENT_CONTROL_TRADE_VALIDATION_NORMAL_CAPACITY,
  ) -> None:
    if high_capacity <= 0 or normal_capacity <= 0:
      raise ValueError("trade validation capacities must be positive")
    self._capacity = {
      _TRADE_VALIDATION_HIGH_LANE: high_capacity,
      _TRADE_VALIDATION_NORMAL_LANE: normal_capacity,
    }
    self._items: dict[str, deque[_TradeCommandValidationItem]] = {
      _TRADE_VALIDATION_HIGH_LANE: deque(),
      _TRADE_VALIDATION_NORMAL_LANE: deque(),
    }
    self._pending_message_ids: set[str] = set()
    self._condition = asyncio.Condition()

  async def put(self, envelope: AgentEnvelope) -> bool:
    lane = _trade_validation_lane(envelope)
    async with self._condition:
      if envelope.message_id in self._pending_message_ids:
        return False
      if len(self._items[lane]) >= self._capacity[lane]:
        return False
      self._items[lane].append(
        _TradeCommandValidationItem(
          envelope=envelope,
          queued_monotonic=time.monotonic(),
        )
      )
      self._pending_message_ids.add(envelope.message_id)
      self._condition.notify_all()
      return True

  async def get(self, lane: str) -> _TradeCommandValidationItem:
    if lane not in self._items:
      raise ValueError("unknown trade validation lane")
    async with self._condition:
      await self._condition.wait_for(lambda: bool(self._items[lane]))
      return self._items[lane].popleft()

  async def complete(self, item: _TradeCommandValidationItem) -> None:
    async with self._condition:
      self._pending_message_ids.discard(item.envelope.message_id)
      self._condition.notify_all()

  def qsize(self, lane: str | None = None) -> int:
    if lane is not None:
      if lane not in self._items:
        raise ValueError("unknown trade validation lane")
      return len(self._items[lane])
    return sum(len(items) for items in self._items.values())


_INBOUND_HEARTBEAT_LANE = "heartbeat"
_INBOUND_COMMAND_ACK_LANE = "command-ack"
_INBOUND_DURABLE_LANE = "durable"


def _inbound_lane(envelope: AgentEnvelope) -> str:
  if envelope.message_type is AgentMessageType.HEARTBEAT:
    return _INBOUND_HEARTBEAT_LANE
  if envelope.message_type is AgentMessageType.COMMAND_ACK:
    return _INBOUND_COMMAND_ACK_LANE
  return _INBOUND_DURABLE_LANE


class _AgentInboundBuffer:
  """Bound each control lane independently so durable work cannot block liveness."""

  def __init__(
    self,
    *,
    capacity: int = AGENT_CONTROL_INBOUND_QUEUE_CAPACITY,
    max_bytes: int = AGENT_CONTROL_INBOUND_QUEUE_MAX_BYTES,
  ) -> None:
    if capacity <= 0 or max_bytes <= 0:
      raise ValueError("Agent inbound buffer limits must be positive")
    self._durable_capacity = capacity
    self._command_ack_capacity = max(16, capacity // 4)
    self._durable_max_bytes = max_bytes
    self._command_ack_max_bytes = max(1024, max_bytes // 8)
    self._items: dict[str, deque[_AgentInboundItem]] = {
      _INBOUND_HEARTBEAT_LANE: deque(),
      _INBOUND_COMMAND_ACK_LANE: deque(),
      _INBOUND_DURABLE_LANE: deque(),
    }
    self._retained_bytes = {
      _INBOUND_HEARTBEAT_LANE: 0,
      _INBOUND_COMMAND_ACK_LANE: 0,
      _INBOUND_DURABLE_LANE: 0,
    }
    self._pending_keys: set[str] = set()
    self._condition = asyncio.Condition()

  async def put(self, item: _AgentInboundItem) -> bool:
    if item.frame_bytes > self._durable_max_bytes:
      raise _AgentControlPipelineError("inbound_frame_too_large", close_code=1009)
    async with self._condition:
      lane = _inbound_lane(item.envelope)
      lane_items = self._items[lane]
      if lane == _INBOUND_HEARTBEAT_LANE:
        # Only the freshest queued heartbeat matters. Keep this one-slot lane
        # independent from durable reports, including while one heartbeat is
        # already being persisted by its processor.
        if lane_items:
          retired = lane_items.popleft()
          self._retained_bytes[lane] -= retired.frame_bytes
          if retired.dedup_key:
            self._pending_keys.discard(retired.dedup_key)
      elif item.dedup_key and item.dedup_key in self._pending_keys:
        return False
      elif lane == _INBOUND_COMMAND_ACK_LANE:
        if (
          len(lane_items) >= self._command_ack_capacity
          or self._retained_bytes[lane] + item.frame_bytes
          > self._command_ack_max_bytes
        ):
          return False
      elif lane == _INBOUND_DURABLE_LANE:
        if (
          len(lane_items) >= self._durable_capacity
          or self._retained_bytes[lane] + item.frame_bytes
          > self._durable_max_bytes
        ):
          return False
      self._items[lane].append(item)
      self._retained_bytes[lane] += item.frame_bytes
      if item.dedup_key:
        self._pending_keys.add(item.dedup_key)
      self._condition.notify_all()
      return True

  async def get(self, lane: str = _INBOUND_DURABLE_LANE) -> _AgentInboundItem:
    if lane not in self._items:
      raise ValueError("unknown Agent inbound lane")
    async with self._condition:
      await self._condition.wait_for(lambda: bool(self._items[lane]))
      item = self._items[lane].popleft()
      self._retained_bytes[lane] -= item.frame_bytes
      self._condition.notify_all()
      return item

  async def complete(self, item: _AgentInboundItem) -> None:
    if not item.dedup_key:
      return
    async with self._condition:
      self._pending_keys.discard(item.dedup_key)
      self._condition.notify_all()

  def has_pending(self, dedup_key: str) -> bool:
    return bool(dedup_key and dedup_key in self._pending_keys)

  def qsize(self, lane: str | None = None) -> int:
    if lane is not None:
      if lane not in self._items:
        raise ValueError("unknown Agent inbound lane")
      return len(self._items[lane])
    return sum(len(items) for items in self._items.values())

  def oldest_age(self) -> float:
    heads = [items[0] for items in self._items.values() if items]
    if not heads:
      return 0.0
    oldest = min(item.received_monotonic for item in heads)
    return max(0.0, time.monotonic() - oldest)


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
  if envelope.message_type is AgentMessageType.CANCEL_COMMAND or (
    envelope.message_type is AgentMessageType.COMMAND
    and str(envelope.payload.get("command_kind") or "").upper()
    == "EMERGENCY_STOP"
  ):
    return 1, False
  if envelope.message_type is AgentMessageType.COMMAND:
    return 2, False
  if envelope.message_type is AgentMessageType.MARKET_DATA_REQUEST:
    return 3, False
  return 4, False


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
  heartbeat_protocol = str(payload.get("protocol_version") or PROTOCOL_VERSION)
  if heartbeat_protocol != PROTOCOL_VERSION:
    raise ValueError(f"Agent heartbeat requires protocol {PROTOCOL_VERSION}")
  now = utcnow()
  normalized_sent_at = to_naive_utc(sent_at)
  heartbeat_delay_seconds = (
    round(max(0.0, (now - normalized_sent_at).total_seconds()), 3)
    if normalized_sent_at is not None
    else None
  )
  if await agent_connection_hub.current_session(session.device_id) is not session:
    raise AuthError(
      QMT_CONTROL_SESSION_REPLACED,
      "Agent 控制会话已被替换",
    )
  revoked_ids: list[str] = []
  async with AsyncSessionLocal() as db:
    agent = await db.get(AgentDevice, session.device_id)
    if agent is None or agent.revoked_at is not None:
      raise AuthError(QMT_DEVICE_REVOKED, "Agent 设备已撤销")
    agent.last_seen_at = now
    heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{session.device_id}",
      with_for_update=True,
    )
    if await agent_connection_hub.current_session(session.device_id) is not session:
      raise AuthError(
        QMT_CONTROL_SESSION_REPLACED,
        "Agent 控制会话已被替换",
      )
    previous_details = dict(heartbeat.details or {}) if heartbeat is not None else {}
    previous_connected_at = parse_utc_timestamp(
      previous_details.get("serverConnectedAt")
    )
    session_connected_at = to_naive_utc(session.server_connected_at)
    if (
      previous_connected_at is not None
      and session_connected_at is not None
      and previous_connected_at > session_connected_at
    ):
      raise AuthError(
        QMT_CONTROL_SESSION_REPLACED,
        "Agent 控制会话已被更新连接替换",
      )
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
        QMT_ACCOUNT_MISMATCH,
      }
      and requested_status == "READY"
    )
    if preserve_server_status:
      # Only Engine may promote a reconnecting Agent after the durable full
      # snapshot has been applied. A heartbeat is not reconciliation proof.
      status = str(heartbeat.status).upper()
    details = previous_details
    details.pop("remoteAddressSummary", None)
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
        "historyWorkload": str(payload.get("history_workload") or "idle")[:16],
        "historyProgress": payload.get("history_progress") or [],
        "historyWorkloadReason": str(
          payload.get("history_workload_reason") or ""
        )[:64],
        "apiInstanceId": session.api_instance_id,
        "agentSessionId": session.agent_session_id,
        "serverConnectedAt": utc_iso(session.server_connected_at),
        "serverReceivedAt": utc_iso(now),
        "agentSentAt": utc_iso(sent_at) if sent_at is not None else None,
        "heartbeatDelaySeconds": heartbeat_delay_seconds,
        "heartbeatDelayWarning": bool(
          heartbeat_delay_seconds is not None and heartbeat_delay_seconds > 5.0
        ),
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
async def _mark_session_offline(
  session: AgentControlSession,
  *,
  reason_code: str = QMT_AGENT_OFFLINE,
) -> None:
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
        "reasonCode": str(reason_code or QMT_AGENT_OFFLINE)[:64],
        "lastDisconnectReasonCode": str(reason_code or QMT_AGENT_OFFLINE)[:64],
        "lastDisconnectedAt": utc_iso(now),
      }
    )
    heartbeat.status = "OFFLINE"
    heartbeat.details = details
    heartbeat.updated_at = now
    await db.commit()


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
    PHYSICAL_SEND_CANCELLED_REASON,
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


def _durable_owner_triple(value: object) -> tuple[str, str, str] | None:
  """Return the canonical owner/environment columns from one durable row.

  Owner identity is deliberately read only from typed durable columns.  The
  metadata JSON carried by old rows is audit context and is never a source of
  routing authority.
  """

  owner_type = getattr(value, "owner_type", None)
  owner_id = getattr(value, "owner_id", None)
  environment = getattr(value, "environment", None)
  try:
    owner_type_value = ExecutionOwnerType(
      str(getattr(owner_type, "value", owner_type) or "").strip().upper()
    ).value
    environment_value = ExecutionEnvironment(
      str(getattr(environment, "value", environment) or "").strip().upper()
    ).value
  except (TypeError, ValueError):
    return None
  if not isinstance(owner_id, str) or not owner_id or owner_id != owner_id.strip():
    return None
  strategy_run_id = str(getattr(value, "strategy_run_id", "") or "").strip()
  if strategy_run_id and (
    owner_type_value != ExecutionOwnerType.STRATEGY_RUN.value
    or strategy_run_id != owner_id
  ):
    return None
  return owner_type_value, owner_id, environment_value


def _durable_owner_chain_matches(
  *values: object,
) -> bool:
  """Require every available durable execution row to share one owner triple."""

  triples: list[tuple[str, str, str]] = []
  for value in values:
    if value is None:
      continue
    triple = _durable_owner_triple(value)
    if triple is None:
      return False
    triples.append(triple)
  return bool(triples) and all(triple == triples[0] for triple in triples[1:])


def _wire_execution_mode(environment: object) -> str:
  """Project the canonical uppercase durable environment to the wire mode."""

  try:
    return ExecutionEnvironment(
      str(getattr(environment, "value", environment) or "").strip().upper()
    ).value.lower()
  except (TypeError, ValueError):
    return ""


def _is_place_order(command: TradeCommandOutbox) -> bool:
  return str((command.payload or {}).get("command_kind") or "").upper() == "PLACE_ORDER"


def _validated_command_payload(command: TradeCommandOutbox) -> dict[str, Any] | None:
  """Validate and project one durable command to the exact 1.2 wire shape.

  The owner triple belongs to the durable outbox row and is intentionally not
  projected to QMT.  A malformed/legacy row is not repaired by copying owner
  metadata into a new payload; callers leave it reconcile-only.
  """

  raw_payload = command.payload
  if not isinstance(raw_payload, dict):
    return None
  command_kind = str(raw_payload.get("command_kind") or "").strip().upper()
  payload_model: Any
  if command_kind == "PLACE_ORDER":
    payload_model = TradeCommandPayload
  elif command_kind == "CANCEL_ORDER":
    payload_model = CancelCommandPayload
  elif command_kind == "EMERGENCY_STOP":
    # Emergency stop is a control escape hatch, not an order payload.  Keep
    # its historical minimal fields while refusing owner/legacy projections.
    if set(raw_payload) - {
      "command_kind",
      "client_order_id",
      "account_id",
      "reason",
      "expires_at",
    }:
      return None
    if any(
      not str(raw_payload.get(key) or "").strip()
      for key in ("client_order_id", "account_id", "expires_at")
    ):
      return None
    projected = {
      "command_kind": "EMERGENCY_STOP",
      "client_order_id": str(raw_payload["client_order_id"]),
      "account_id": str(raw_payload["account_id"]),
      "reason": str(raw_payload.get("reason") or ""),
      "expires_at": raw_payload["expires_at"],
    }
    payload_model = None
  else:
    return None
  if payload_model is None:
    canonical = projected
  else:
    try:
      canonical = payload_model.model_validate(raw_payload).model_dump(mode="json")
    except (TypeError, ValueError):
      return None
  owner_triple = _durable_owner_triple(command)
  if owner_triple is None:
    return None
  _owner_type, _owner_id, environment = owner_triple
  if str(canonical.get("account_id") or "") != str(command.account_id or ""):
    return None
  if command_kind != "EMERGENCY_STOP" and (
    str(canonical.get("execution_mode") or "").upper() != environment
  ):
    return None
  return canonical


async def _stage_command_runtime_event(
  db,
  *,
  command: TradeCommandOutbox,
  pending: PendingTradeOrder,
  correlation: OrderCorrelation | None,
  status: str,
  reason: str,
  now: datetime,
  zero_fill_invalidation: dict[str, Any] | None = None,
) -> bool:
  if correlation is None:
    return False
  intent = None
  if pending.intent_id:
    intent = await db.get(TradeIntentRecord, pending.intent_id)
  if correlation.intent_id and intent is None:
    return False
  if (
    not _durable_owner_chain_matches(command, pending, correlation, intent)
    or (
      not correlation.intent_id
      and str(getattr(correlation, "owner_type", "")).upper()
      != ExecutionOwnerType.MANUAL_COMMAND.value
    )
  ):
    return False
  owner_type, owner_id, environment = _durable_owner_triple(correlation) or (
    "",
    "",
    "",
  )
  strategy_run_id = (
    owner_id if owner_type == ExecutionOwnerType.STRATEGY_RUN.value else None
  )
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
  # The durable event columns are the sole owner/environment authority.  Old
  # correlation rows may still carry identity projections in their JSON
  # metadata; keep that metadata only as non-identity audit context so a new
  # runtime event cannot publish two competing facts.
  identity_metadata_keys = {
    "owner_type",
    "owner_id",
    "environment",
    "execution_mode",
    "strategy_run_id",
  }
  metadata = {
    key: value
    for key, value in dict(correlation.request_metadata or {}).items()
    if key not in identity_metadata_keys
  }
  metadata.update({
    "intent_id": getattr(correlation, "intent_id", None) or "",
    "instrument_code": pending.instrument_code,
    "t_batch_id": correlation.batch_id or "",
    "bucket": correlation.bucket,
    "t_trade_role": str(correlation.t_trade_role or "").lower(),
    "risk_decision_id": correlation.risk_decision_id or "",
    "trace_id": correlation.trace_id,
    "substitution_plan": correlation.substitution_plan,
    "approval_reason": reason,
    "runtime_event_key": business_key,
    "command_message_id": command.message_id,
    "command_lifecycle_status": str(pending.status or "").upper(),
  })
  if (
    str(getattr(correlation, "owner_type", "") or "").strip().upper()
    == ExecutionOwnerType.STRATEGY_RUN.value
  ):
    metadata["strategy_order_id"] = (
      getattr(correlation, "strategy_order_id", None) or ""
    )
  if zero_fill_invalidation:
    metadata["zero_fill_proof_invalidation"] = dict(zero_fill_invalidation)
  event = StrategyRuntimeEvent(
    event_id=str(uuid.uuid4()),
    business_key=business_key,
    owner_type=owner_type,
    owner_id=owner_id,
    environment=environment,
    strategy_run_id=strategy_run_id,
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
      select(OrderCorrelation)
      .where(OrderCorrelation.client_order_id == command.client_order_id)
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
  owner_chain_valid = bool(
    pending is not None
    and correlation is not None
    and _durable_owner_chain_matches(command, pending, correlation, intent)
    and (
      bool(correlation.intent_id)
      or str(getattr(correlation, "owner_type", "")).upper()
      == ExecutionOwnerType.MANUAL_COMMAND.value
    )
  )
  if not owner_chain_valid:
    # A lifecycle outcome with contradictory or incomplete durable ownership
    # cannot safely be attributed.  Preserve the command for reconciliation;
    # never recover an owner from metadata, a run id, or the wire payload.
    normalized_status = "RECONCILE_REQUIRED"
    reason = "OWNER_BINDING_CONFLICT"
  previous_command_status = str(command.delivery_status or "").upper()
  from quantx_infrastructure.services.t_order_lifecycle_state import (
    t_order_lifecycle_pending,
  )

  t_lifecycle_open = t_order_lifecycle_pending(pending)
  if (
    getattr(pending, "t_order_original_created_at", None) is not None
    and not t_lifecycle_open
    and normalized_status in {"EXPIRED", "REJECTED"}
    and normalized_status == str(pending.status or "").upper()
    and reason == str(pending.status_reason or "")
  ):
    return False
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
          and reason == PHYSICAL_SEND_CANCELLED_REASON
          and str(pending.status or "").upper() == "CANCEL_REQUESTED"
        )
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
  if t_lifecycle_open:
    # Prior attempts may already have real fills in the same intent/batch.
    # Only this command's pre-execution proof decides this attempt's zero.
    safe_pre_execution_state = bool(
      owner_chain_valid
      and pre_execution_proven
      and not pending.broker_order_id
      and not correlation.broker_order_id
      and (
        str(pending.status or "").upper() in {"QUEUED", "CANCEL_REQUESTED"}
        or (
          str(pending.status or "").upper() in {"EXPIRED", "REJECTED"}
          and str(pending.status_reason or "") == reason
          and dict(pending.request_metadata or {}).get("execution_terminal_source")
          in {LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE, LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE}
        )
      )
    )
  if normalized_status in {"EXPIRED", "REJECTED"} and not safe_pre_execution_state:
    normalized_status = "RECONCILE_REQUIRED"
    reason = f"{reason}:durable_pre_execution_proof_missing"[:256]

  pending_request_metadata = (
    dict(pending.request_metadata or {}) if pending is not None else {}
  )
  correlation_request_metadata = (
    dict(correlation.request_metadata or {}) if correlation is not None else {}
  )
  request_metadata = {
    **pending_request_metadata,
    **correlation_request_metadata,
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
  pending_owner = _durable_owner_triple(pending) if pending is not None else None
  intent_owner = _durable_owner_triple(intent) if intent is not None else None
  correlation_owner = (
    _durable_owner_triple(correlation) if correlation is not None else None
  )
  request_exit_plan_id = (
    pending_owner[1]
    if pending_owner is not None
    and pending_owner[0] == ExecutionOwnerType.EXIT_PLAN.value
    else ""
  )
  intent_exit_plan_id = (
    intent_owner[1]
    if intent_owner is not None
    and intent_owner[0] == ExecutionOwnerType.EXIT_PLAN.value
    else ""
  )
  exact_exit_plan_binding = bool(
    request_exit_plan_id
    and request_exit_plan_id == intent_exit_plan_id
    and pending_owner is not None
    and intent_owner == pending_owner
    and pending_owner[0] == ExecutionOwnerType.EXIT_PLAN.value
    and (
      correlation_owner is None or correlation_owner == pending_owner
    )
    and pending is not None
    and intent is not None
    and str(pending.side or "").upper() == "SELL"
    and str(intent.direction or "").upper() == "SELL"
    and str(intent.owner_type or "").upper() == "EXIT_PLAN"
    and str(intent.owner_id or "") == request_exit_plan_id
    and str(intent.account_id or "") == str(pending.account_id or "")
    and str(intent.instrument_code or "").upper()
    == str(pending.instrument_code or "").upper()
    and str(intent.strategy_run_id or "") == str(pending.strategy_run_id or "")
    and (
      correlation is None
      or (
        str(correlation.intent_id or "") == str(intent.id or "")
        and str(correlation.account_id or "") == str(pending.account_id or "")
        and str(correlation.strategy_run_id or "")
        == str(pending.strategy_run_id or "")
      )
    )
    and intent_zero_execution
  )
  first_exit_plan_outbox_expiry = bool(
    normalized_status == "EXPIRED"
    and reason == "command_expired_before_delivery"
    and previous_command_status == "QUEUED"
    and command.delivered_at is None
    and safe_pre_execution_state
    and exact_exit_plan_binding
  )
  replayed_exit_plan_outbox_expiry = bool(
    normalized_status != "RECONCILE_REQUIRED"
    and previous_command_status == "EXPIRED"
    and command.delivered_at is None
    and pending is not None
    and str(pending.status or "").upper() == "EXPIRED"
    and intent is not None
    and str(intent.status or "").upper() == "RECONCILED_ZERO_FILL"
    and str(intent_metadata.get("execution_terminal_source") or "").upper()
    == LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE
    and safe_pre_execution_state
    and exact_exit_plan_binding
  )
  first_exit_plan_agent_rejection = bool(
    exact_exit_plan_binding
    and normalized_status in {"REJECTED", "EXPIRED"}
    and reason in _PRE_EXECUTION_REJECTION_REASONS
    and safe_pre_execution_state
  )
  replayed_exit_plan_agent_rejection = bool(
    exact_exit_plan_binding
    and normalized_status != "RECONCILE_REQUIRED"
    and previous_command_status in {"REJECTED", "EXPIRED"}
    and pending is not None
    and str(pending.status or "").upper() in {"REJECTED", "EXPIRED"}
    and intent is not None
    and str(intent.status or "").upper() == "RECONCILED_ZERO_FILL"
    and str(intent_metadata.get("execution_terminal_source") or "").upper()
    == LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE
    and safe_pre_execution_state
  )
  replayed_exit_plan_zero_fill = bool(
    replayed_exit_plan_outbox_expiry or replayed_exit_plan_agent_rejection
  )
  exit_plan_zero_fill = bool(
    first_exit_plan_outbox_expiry
    or replayed_exit_plan_outbox_expiry
    or first_exit_plan_agent_rejection
    or replayed_exit_plan_agent_rejection
  )
  strategy_status = (
    "RECONCILED_ZERO_FILL"
    if managed_entry_zero_fill or exit_plan_zero_fill
    else normalized_status
  )
  if (
    exact_exit_plan_binding
    and normalized_status == "RECONCILE_REQUIRED"
    and reason == "accepted_ack_conflicts_with_terminal_command_state"
  ):
    await invalidate_exit_plan_zero_fill_proof(
      db,
      client_order_id=str(pending.client_order_id or ""),
      evidence_kind="COMMAND_ACK",
      evidence_status="ACCEPTED",
      evidence_key=f"command-ack:{command.message_id}:ACCEPTED",
    )
    intent_metadata = dict(intent.intent_metadata or {}) if intent is not None else {}

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
  if t_lifecycle_open and normalized_status in {"EXPIRED", "REJECTED"} and safe_pre_execution_state:
    pending.request_metadata = {
      **dict(pending.request_metadata or {}),
      "execution_terminal_source": (
        LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE
        if command.delivered_at is None and reason == "command_expired_before_delivery"
        else LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE
      ),
      "execution_terminal_reason": reason,
      "execution_terminal_at": now.isoformat(),
      "command_lifecycle_message_id": str(command.message_id),
    }
  if intent is not None:
    if t_lifecycle_open and normalized_status != "RECONCILE_REQUIRED":
      if str(intent.status or "") not in {"PENDING", "APPROVED", "EXECUTION_READY", "EXECUTION_PENDING"}:
        intent.status = "PARTIAL_FILLED" if intent_executed_volume > 0 else "QUEUED"
    else:
      intent.status = strategy_status
    intent.notes = reason[:2000] or intent.notes
    if normalized_status == "RECONCILE_REQUIRED" and str(
      intent_metadata.get("execution_terminal_source") or ""
    ).upper() in {
      LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE,
      LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE,
    }:
      intent_metadata = {
        key: value
        for key, value in intent_metadata.items()
        if key
        not in {
          "execution_terminal_source",
          "execution_terminal_reason",
          "execution_terminal_at",
          "command_lifecycle_status",
          "command_lifecycle_previous_status",
          "command_lifecycle_message_id",
        }
      }
      intent.intent_metadata = intent_metadata
    if (managed_entry_zero_fill or exit_plan_zero_fill) and not t_lifecycle_open:
      if not replayed_exit_plan_zero_fill:
        intent.intent_metadata = {
          **intent_metadata,
          "execution_terminal_source": (
            LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE
            if first_exit_plan_outbox_expiry
            else LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE
            if first_exit_plan_agent_rejection
            else "AGENT_COMMAND_LIFECYCLE"
          ),
          "execution_terminal_reason": reason,
          "command_lifecycle_status": normalized_status,
          "command_lifecycle_previous_status": previous_command_status,
          "command_lifecycle_message_id": str(command.message_id or ""),
          "execution_terminal_at": now.isoformat(),
        }
  if not t_lifecycle_open or normalized_status == "RECONCILE_REQUIRED":
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
    zero_fill_invalidation=(
      dict(intent_metadata.get("zero_fill_proof_invalidation") or {})
      if isinstance(intent_metadata.get("zero_fill_proof_invalidation"), dict)
      else None
    ),
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

  query = select(
    TradeCommandOutbox.message_id,
    TradeCommandOutbox.payload,
  ).where(
    TradeCommandOutbox.delivery_status.in_(("QUEUED", "DELIVERED")),
    TradeCommandOutbox.expires_at <= now,
  )
  if device_id:
    query = query.where(TradeCommandOutbox.device_id == device_id)
  candidates = list(
    (
      await db.execute(
        query.order_by(TradeCommandOutbox.expires_at, TradeCommandOutbox.created_at)
        .limit(max(1, int(batch_size)))
      )
    ).all()
  )

  staged_runtime_event = False
  expired_count = 0
  for message_id, candidate_payload in candidates:
    candidate_payload = dict(candidate_payload or {})
    live_place = bool(
      str(candidate_payload.get("command_kind") or "").upper() == "PLACE_ORDER"
      and str(candidate_payload.get("execution_mode") or "").lower() == "live"
    )
    if live_place:
      lifecycle_lock = await AccountExecutionQuarantineService(
        db
      ).lock_command_for_lifecycle(
        message_id=str(message_id),
        device_id=str(device_id or ""),
      )
      expired = lifecycle_lock.command
    else:
      expired = await db.get(
        TradeCommandOutbox,
        str(message_id),
        with_for_update=True,
        populate_existing=True,
      )
    if (
      expired is None
      or str(expired.delivery_status or "").upper() not in {"QUEUED", "DELIVERED"}
      or expired.expires_at > now
      or (device_id and str(expired.device_id or "") != str(device_id))
    ):
      continue
    expired_count += 1
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
  return expired_count, staged_runtime_event


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
    lifecycle_lock = await AccountExecutionQuarantineService(
      db
    ).lock_command_for_lifecycle(
      message_id=message_id,
      device_id=device_id,
    )
    command = lifecycle_lock.command
    if command is None:
      return
    if command.client_order_id != client_order_id:
      raise ValueError("command_ack 命令与 client_order_id 不匹配")
    now = utcnow()
    previous_status = str(command.delivery_status or "").upper()
    if reason == "command_processing":
      # A redelivery raced the Agent's original native call. This is neither
      # rejection nor broker acceptance; postpone another delivery and wait
      # for the original durable result/report.  A late processing ACK must
      # never revive a command already quarantined or otherwise terminalized.
      if previous_status in {"QUEUED", "DELIVERED"}:
        command.delivery_status = "DELIVERED"
        command.delivered_at = now
        command.acknowledged_at = None
        command.last_error = reason
      await db.commit()
      return
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
    elif accepted and previous_status == "RECONCILE_REQUIRED":
      # ACK proves only Agent-local receipt.  It cannot resolve broker
      # uncertainty or erase the durable reason that quarantined this command.
      pass
    elif accepted and previous_status in {"QUEUED", "DELIVERED"}:
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


def _prepare_report_persistence(
  device_id: str,
  envelope: AgentEnvelope,
) -> _PreparedReportPersistence:
  if envelope.protocol_version != PROTOCOL_VERSION:
    raise ValueError(f"Agent report requires protocol {PROTOCOL_VERSION}")
  wire_payload = envelope.payload
  envelope.validate_payload()
  canonical_payload = json.dumps(
    wire_payload,
    sort_keys=True,
    separators=(",", ":"),
    default=str,
  )
  canonical_message_type = json.dumps(
    envelope.message_type.value,
    separators=(",", ":"),
  )
  payload_hash = hashlib.sha256(
    (
      '{"message_type":'
      f"{canonical_message_type},"
      f'"payload":{canonical_payload}'
      "}"
    ).encode("utf-8")
  ).hexdigest()
  body = _body_for_report_idempotency(
    envelope,
    canonical_payload=canonical_payload,
  )
  business_idempotency_key = hashlib.sha256(
    (
      f"{device_id}:{envelope.message_type.value}:"
      f"{json.dumps(body, sort_keys=True, separators=(',', ':'), default=str)}"
    ).encode("utf-8")
  ).hexdigest()
  return _PreparedReportPersistence(
    payload_hash=payload_hash,
    business_idempotency_key=business_idempotency_key,
  )


async def _prepare_report_persistence_async(
  device_id: str,
  envelope: AgentEnvelope,
  *,
  frame_bytes: int,
) -> _PreparedReportPersistence:
  if frame_bytes < AGENT_CONTROL_CPU_OFFLOAD_BYTES:
    return _prepare_report_persistence(device_id, envelope)
  return await asyncio.get_running_loop().run_in_executor(
    _agent_control_cpu_executor,
    _prepare_report_persistence,
    device_id,
    envelope,
  )


async def _record_report(
  session: AgentControlSession,
  envelope: AgentEnvelope,
  *,
  received_at: datetime,
  frame_bytes: int = 0,
) -> ReportAckPayload:
  if envelope.protocol_version != PROTOCOL_VERSION:
    raise ValueError(f"Agent report requires protocol {PROTOCOL_VERSION}")
  wire_payload = envelope.payload
  if AGENT_SERVER_SESSION_PAYLOAD_KEY in wire_payload:
    raise ValueError("Agent report contains a reserved server field")
  prepared = await _prepare_report_persistence_async(
    session.device_id,
    envelope,
    frame_bytes=frame_bytes,
  )
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
    client_order_id=_report_client_order_id(wire_payload, envelope.message_type),
    raw_payload_hash=prepared.payload_hash,
    business_idempotency_key=prepared.business_idempotency_key,
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
            | (
              AgentReportInbox.business_idempotency_key
              == prepared.business_idempotency_key
            )
          )
        )
      ).scalar_one_or_none()
      same = (
        existing is not None
        and existing.raw_payload_hash == prepared.payload_hash
      )
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


def _normalized_report_identity(value: Any) -> str | None:
  """Normalize one current 1.2 report identity value for stable hashing."""

  normalized = str(value).strip() if value is not None else ""
  return normalized or None


def _first_report_identity(*values: Any) -> str | None:
  for value in values:
    normalized = _normalized_report_identity(value)
    if normalized is not None:
      return normalized
  return None


def _report_broker_order_id(
  payload: dict[str, Any],
  message_type: AgentMessageType,
) -> str | None:
  """Read the broker id from the typed nested 1.2 order/execution body."""

  nested_key = (
    "order"
    if message_type is AgentMessageType.ORDER_REPORT
    else "execution"
    if message_type is AgentMessageType.EXECUTION_REPORT
    else ""
  )
  nested = payload.get(nested_key) if nested_key else None
  if not isinstance(nested, dict):
    nested = payload
  return _first_report_identity(
    nested.get("order_id"),
    nested.get("broker_order_id"),
  )


def _body_for_report_idempotency(
  envelope: AgentEnvelope,
  *,
  canonical_payload: str | None = None,
) -> dict[str, Any]:
  payload = envelope.payload
  if envelope.message_type is AgentMessageType.EXECUTION_REPORT:
    execution = payload.get("execution")
    body = execution if isinstance(execution, dict) else payload
    identity = {
      "account_id": body.get("account_id"),
      # Protocol 1.2 carries the service-owned client id at the payload level;
      # retain the nested spelling accepted by the typed execution body too.
      "client_order_id": _report_client_order_id(
        payload,
        AgentMessageType.EXECUTION_REPORT,
      ),
      "broker_order_id": _report_broker_order_id(
        payload,
        AgentMessageType.EXECUTION_REPORT,
      ),
      "execution_id": _first_report_identity(
        body.get("execution_id"),
        body.get("traded_id"),
      ),
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
      (
        canonical_payload
        if canonical_payload is not None
        else json.dumps(
          payload,
          sort_keys=True,
          separators=(",", ":"),
          default=str,
        )
      ).encode("utf-8")
    ).hexdigest(),
  }


def _report_client_order_id(
  payload: dict[str, Any],
  message_type: AgentMessageType,
) -> str | None:
  nested_key = (
    "order"
    if message_type is AgentMessageType.ORDER_REPORT
    else "execution"
    if message_type is AgentMessageType.EXECUTION_REPORT
    else ""
  )
  nested = payload.get(nested_key) if nested_key else None
  nested_value = nested.get("client_order_id") if isinstance(nested, dict) else None
  return _first_report_identity(payload.get("client_order_id"), nested_value)


async def _next_command(
  control_session: AgentControlSession,
  *,
  protocol_version: str = PROTOCOL_VERSION,
) -> Optional[AgentEnvelope]:
  if protocol_version != PROTOCOL_VERSION:
    raise ValueError(f"Agent command delivery requires protocol {PROTOCOL_VERSION}")
  device_id = control_session.device_id
  now = utcnow()
  redelivery_before = now - timedelta(seconds=TRADE_COMMAND_REDELIVERY_SECONDS)
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
    command_kind_expression = func.upper(
      TradeCommandOutbox.payload.op("->>")("command_kind")
    )
    high_priority_kinds = ("CANCEL_ORDER", "EMERGENCY_STOP")
    eligible_delivery = or_(
      TradeCommandOutbox.delivery_status == "QUEUED",
      and_(
        TradeCommandOutbox.delivery_status == "DELIVERED",
        or_(
          TradeCommandOutbox.delivered_at.is_(None),
          TradeCommandOutbox.delivered_at <= redelivery_before,
        ),
      ),
    )
    command = (
      await db.execute(
        select(TradeCommandOutbox)
        .where(
          TradeCommandOutbox.device_id == device_id,
          eligible_delivery,
          TradeCommandOutbox.expires_at > now,
          command_kind_expression.in_(high_priority_kinds),
        )
        .order_by(TradeCommandOutbox.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
      )
    ).scalar_one_or_none()
    if command is None:
      fresh_delivery = await db.scalar(
        select(TradeCommandOutbox.message_id)
        .where(
          TradeCommandOutbox.device_id == device_id,
          TradeCommandOutbox.delivery_status == "DELIVERED",
          TradeCommandOutbox.delivered_at > redelivery_before,
          TradeCommandOutbox.expires_at > now,
        )
        .limit(1)
      )
      if fresh_delivery is not None:
        await db.commit()
        return None
      # Discover without a durable row lock, then claim through the shared
      # account -> outbox boundary.  Exact release-proof invalidation takes the
      # same account lock before it quarantines a PLACE_ORDER, so a concurrent
      # LIVE SELL is either already DELIVERED evidence or cannot leave here.
      candidate_message_id = await db.scalar(
        select(TradeCommandOutbox.message_id)
        .where(
          TradeCommandOutbox.device_id == device_id,
          eligible_delivery,
          TradeCommandOutbox.expires_at > now,
          command_kind_expression.not_in(high_priority_kinds),
        )
        .order_by(TradeCommandOutbox.created_at)
        .limit(1)
      )
      if candidate_message_id is not None:
        delivery_lock = await AccountExecutionQuarantineService(
          db
        ).lock_command_for_delivery(
          message_id=str(candidate_message_id),
          now=now,
          redelivery_before=redelivery_before,
        )
        command = delivery_lock.command
    if command is None:
      await db.commit()
      return None
    projected_payload = _validated_command_payload(command)
    if projected_payload is None:
      # Never rewrite an already delivered command into a new protocol shape:
      # its broker-side outcome remains a reconciliation fact.  A queued
      # malformed row is quarantined locally and will not be retried.
      if str(command.delivery_status or "").upper() == "QUEUED":
        command.delivery_status = "RECONCILE_REQUIRED"
        command.last_error = "INVALID_PROTOCOL_1_2_COMMAND_PAYLOAD"
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
    high_priority_command = command_kind in {"CANCEL_ORDER", "EMERGENCY_STOP"}
    acceptable_statuses = (
      {"READY", "RECONCILING", "EMERGENCY_STOP", "RECONCILE_REQUIRED"}
      if high_priority_command
      else {"READY"}
    )
    session_state = evaluate_agent_session(
      heartbeat,
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
        if command_kind == "CANCEL_ORDER"
        else AgentMessageType.COMMAND
      ),
      sent_at=sent_at,
      payload=projected_payload,
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
    session_state = evaluate_agent_session(
      heartbeat,
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
    inflight_statuses = list(
      (await db.scalars(
        select(MarketDataRequest.status)
        .where(
          or_(
            and_(MarketDataRequest.device_id == device_id,
                 MarketDataRequest.status.in_(_MARKET_DATA_INFLIGHT_STATUSES)),
            and_(MarketDataRequest.status == "BLOCKED",
                 MarketDataRequest.ingestion_progress["reason_code"].astext.in_((
                   "DEPENDENCY_QUERY_CAPACITY_BLOCKED", "DEPENDENCY_AUTH_BLOCKED",
                   "DEPENDENCY_WRITE_CAPACITY_BLOCKED",
                   "DEPENDENCY_READBACK_UNAVAILABLE", "DEPENDENCY_WRITE_UNAVAILABLE",
                 ))),
          ),
        )
        .limit(MAX_MARKET_DATA_INFLIGHT_REQUESTS_PER_DEVICE)
      )).all()
    )
    if (
      any(status in _MARKET_DATA_NATIVE_DISPATCH_STATUSES for status in inflight_statuses)
      or "BLOCKED" in inflight_statuses
      or len(inflight_statuses) >= MAX_MARKET_DATA_INFLIGHT_REQUESTS_PER_DEVICE
    ):
      return None
    from quantx_infrastructure.services.development_history_window import (
      history_window_open,
    )

    development_allowed = (
      session_state.current
      and heartbeat.status == "READY"
      and await history_window_open()
    )
    result = await db.execute(
      select(MarketDataRequest)
      .where(
        MarketDataRequest.device_id == device_id,
        MarketDataRequest.status == "QUEUED",
        or_(MarketDataRequest.development_only.is_(False), development_allowed),
      )
      .order_by(MarketDataRequest.development_only, MarketDataRequest.created_at)
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
  future_after = reference_time + timedelta(seconds=MARKET_DATA_RECONNECT_STALE_SECONDS)
  async with AsyncSessionLocal() as db:
    await db.execute(
      update(MarketDataRequest)
      .where(
        MarketDataRequest.device_id == device_id,
        MarketDataRequest.status.in_(("DELIVERED", "RECEIVING")),
        or_(
          MarketDataRequest.updated_at < stale_before,
          MarketDataRequest.updated_at > future_after,
        ),
      )
      .values(status="QUEUED", updated_at=reference_time)
    )
    await db.commit()


async def _process_message(
  session: AgentControlSession,
  envelope: AgentEnvelope,
  *,
  received_at: datetime,
  frame_bytes: int = 0,
  protocol_version: str = PROTOCOL_VERSION,
) -> AgentEnvelope | None:
  device_id = session.device_id
  if not await agent_connection_hub.is_connected(
    device_id,
    agent_session_id=session.agent_session_id,
  ):
    raise AuthError(
      QMT_CONTROL_SESSION_REPLACED,
      "Agent 控制会话已被替换",
    )
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
      frame_bytes=frame_bytes,
    )
    return AgentEnvelope(
      protocol_version=protocol_version,
      message_type=AgentMessageType.REPORT_ACK,
      payload=ack.model_dump(mode="json"),
    )
  raise ValueError(f"不支持的 Agent 消息类型: {envelope.message_type.value}")


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


def _parse_agent_control_frame(raw: str) -> tuple[AgentEnvelope, int]:
  frame_bytes = len(raw.encode("utf-8"))
  return AgentEnvelope.model_validate_json(raw), frame_bytes


async def _parse_agent_control_frame_async(
  raw: str,
) -> tuple[AgentEnvelope, int]:
  if len(raw) < AGENT_CONTROL_CPU_OFFLOAD_CHARS:
    return _parse_agent_control_frame(raw)
  return await asyncio.get_running_loop().run_in_executor(
    _agent_control_cpu_executor,
    _parse_agent_control_frame,
    raw,
  )


async def _receive_agent_control_messages(
  websocket: WebSocket,
  *,
  device_id: str,
  protocol_version: str,
  inbound: _AgentInboundBuffer,
  outbound: _AgentOutboundBuffer,
  database_state: _AgentDatabaseState,
) -> None:
  while True:
    raw = await websocket.receive_text()
    received_monotonic = time.monotonic()
    received_at = utcnow()
    envelope, frame_bytes = await _parse_agent_control_frame_async(raw)
    if envelope.protocol_version != protocol_version:
      raise ValueError("Agent connection changed protocol version")
    if envelope.message_type is AgentMessageType.MARKET_EVENT:
      raise ValueError("MARKET_EVENT must use /ws/agent/market")
    if envelope.message_type is AgentMessageType.HEARTBEAT:
      # Liveness is a transport fact. Database persistence can lag without
      # turning a healthy socket into a reconnect/reconciliation storm.
      database_state.mark_heartbeat_received()
    _observe_source_to_receive(device_id, envelope, received_at)
    item = _AgentInboundItem(
      envelope=envelope,
      received_at=received_at,
      received_monotonic=received_monotonic,
      frame_bytes=frame_bytes,
      dedup_key=(
        envelope.message_id
        if envelope.message_type in REPORT_TYPES
        else "command-ack:"
        + str(envelope.payload.get("command_message_id") or "")
        if envelope.message_type is AgentMessageType.COMMAND_ACK
        else "latest-heartbeat"
        if envelope.message_type is AgentMessageType.HEARTBEAT
        else ""
      ),
    )
    already_pending = inbound.has_pending(item.dedup_key)
    queued = await inbound.put(item)
    if not queued:
      durable_backpressure = not already_pending
      AGENT_CONTROL_EVENTS.labels(
        event="backpressure" if durable_backpressure else "deduplicate",
        reason=(
          "inbound_durable_retry_requested"
          if durable_backpressure
          else "inbound_message_pending"
        ),
      ).inc()
      if durable_backpressure and envelope.message_type in REPORT_TYPES:
        # The Agent retains the report in its local journal. An explicit NACK
        # retires the in-flight slot immediately so it can retry without a
        # control-socket reconnect or a permanently lost send window.
        await _enqueue_agent_outbound(
          device_id,
          outbound,
          AgentEnvelope(
            message_type=AgentMessageType.REPORT_ACK,
            payload={
              "report_message_id": envelope.message_id,
              "accepted": False,
              "duplicate": False,
              "reason": "inbound_backpressure_retry",
            },
          ),
        )
    _set_agent_control_queue_metrics(device_id, "inbound", inbound)


async def _process_agent_control_messages(
  *,
  control_session: AgentControlSession,
  protocol_version: str,
  inbound: _AgentInboundBuffer,
  outbound: _AgentOutboundBuffer,
  database_state: _AgentDatabaseState,
  lane: str = _INBOUND_DURABLE_LANE,
) -> None:
  device_id = control_session.device_id
  while True:
    item = await inbound.get(lane)
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
            frame_bytes=item.frame_bytes,
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
        item.envelope.message_type is AgentMessageType.HEARTBEAT
        or inbound.qsize(lane) == 0
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
      raise AuthError(
        QMT_CONTROL_SESSION_REPLACED,
        "Agent 控制会话已被替换",
      )
    serialized = item.envelope.model_dump_json()
    live_place_order = bool(
      item.envelope.message_type is AgentMessageType.COMMAND
      and str(item.envelope.payload.get("command_kind") or "").upper()
      == "PLACE_ORDER"
      and str(item.envelope.payload.get("execution_mode") or "").lower() == "live"
    )
    if item.envelope.message_type in {
      AgentMessageType.COMMAND,
      AgentMessageType.CANCEL_COMMAND,
    }:
      try:
        await _assert_trade_delivery_session(control_session, item.envelope)
      except _TradeCommandDeliveryDeferred as exc:
        await outbound.complete(item)
        AGENT_CONTROL_EVENTS.labels(
          event="delivery",
          reason=f"physical_send_deferred_{exc.reason}",
        ).inc()
        logger.info(
          "Agent physical trade-command send deferred without disconnect: "
          "device_id=%s message_id=%s reason=%s",
          device_id,
          item.envelope.message_id,
          exc.reason,
        )
        continue
    try:
      if live_place_order:
        async with AsyncSessionLocal() as db:
          send_lock = await AccountExecutionQuarantineService(
            db
          ).lock_command_for_physical_send(
            message_id=item.envelope.message_id,
            expected_payload=item.envelope.payload,
          )
          if send_lock.pre_execution_terminal_command is not None:
            staged_runtime_event = await _transition_place_order_command(
              db,
              command=send_lock.pre_execution_terminal_command,
              requested_status=send_lock.pre_execution_terminal_status,
              reason=send_lock.pre_execution_terminal_reason,
              now=utcnow(),
              pre_execution_proven=True,
            )
            await db.commit()
            if staged_runtime_event:
              await _wake_runtime_event_consumer()
            await outbound.complete(item)
            AGENT_CONTROL_EVENTS.labels(
              event="delivery",
              reason="live_place_physical_send_cancelled",
            ).inc()
            continue
          if send_lock.command is None:
            if send_lock.commit_required:
              # The physical EXIT_PLAN final gate sealed a claimed DELIVERED
              # row and opened an explicit reconciliation barrier.
              await db.commit()
            else:
              await db.rollback()
            await outbound.complete(item)
            AGENT_CONTROL_EVENTS.labels(
              event="delivery",
              reason=(
                "live_place_physical_send_"
                + str(send_lock.blocked_reason or "blocked").lower()
              ),
            ).inc()
            continue
          # Keep Account -> PLACE outbox locked only for this bounded physical
          # write.  This is the linearization point with exact quarantine.
          await asyncio.wait_for(
            websocket.send_text(serialized),
            timeout=AGENT_CONTROL_SEND_TIMEOUT_SECONDS,
          )
          await db.commit()
      else:
        await asyncio.wait_for(
          websocket.send_text(serialized),
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

  if envelope.protocol_version != PROTOCOL_VERSION:
    raise _TradeCommandDeliveryDeferred("protocol_version_mismatch")
  command_kind = str(envelope.payload.get("command_kind") or "").strip().upper()
  try:
    if envelope.message_type is AgentMessageType.COMMAND and command_kind == "PLACE_ORDER":
      TradeCommandPayload.model_validate(envelope.payload)
    elif envelope.message_type is AgentMessageType.CANCEL_COMMAND and command_kind == "CANCEL_ORDER":
      CancelCommandPayload.model_validate(envelope.payload)
    elif envelope.message_type is AgentMessageType.COMMAND and command_kind == "EMERGENCY_STOP":
      if set(envelope.payload) - {
        "command_kind",
        "client_order_id",
        "account_id",
        "reason",
        "expires_at",
      }:
        raise ValueError("unexpected emergency-stop payload field")
    else:
      raise ValueError("unsupported protocol-1.2 command shape")
  except (TypeError, ValueError) as exc:
    raise _TradeCommandDeliveryDeferred("invalid_command_payload") from exc

  if not await agent_connection_hub.is_connected(
    control_session.device_id,
    agent_session_id=control_session.agent_session_id,
  ):
    raise AuthError(
      QMT_CONTROL_SESSION_REPLACED,
      "Agent 控制会话已被替换",
    )
  now = utcnow()
  try:
    async with AsyncSessionLocal() as db:
      device = await db.get(AgentDevice, control_session.device_id)
      heartbeat = await db.get(
        RuntimeComponentHeartbeat,
        f"qmt-agent:{control_session.device_id}",
      )
  except _TRANSIENT_DEPENDENCY_ERRORS as exc:
    raise _TradeCommandDeliveryDeferred(
      "delivery_authority_dependency_unavailable"
    ) from exc

  command_kind = str(envelope.payload.get("command_kind") or "").upper()
  acceptable_statuses = (
    {"READY", "RECONCILING", "EMERGENCY_STOP", "RECONCILE_REQUIRED"}
    if envelope.message_type is AgentMessageType.CANCEL_COMMAND
    or command_kind == "EMERGENCY_STOP"
    else {"READY"}
  )
  session_state = evaluate_agent_session(
    heartbeat,
    now=now,
    acceptable_statuses=acceptable_statuses,
  )
  account_id = str(envelope.payload.get("account_id") or "").strip()
  execution_mode = str(envelope.payload.get("execution_mode") or "").strip().lower()
  risk_increasing = bool(
    envelope.message_type is AgentMessageType.COMMAND
    and str(envelope.payload.get("command_kind") or "").upper() == "PLACE_ORDER"
    and str(envelope.payload.get("side") or "").upper() != "SELL"
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
  if device is None or device.revoked_at is not None:
    raise AuthError(QMT_DEVICE_REVOKED, "Agent 设备已撤销")
  if (
    account_id not in control_session.authorized_account_ids
    or (execution_mode and execution_mode not in capabilities)
  ):
    raise _TradeCommandDeliveryDeferred("command_authority_mismatch")
  escape_command = bool(
    envelope.message_type is AgentMessageType.CANCEL_COMMAND
    or command_kind == "EMERGENCY_STOP"
  )
  projection_identity_matches = bool(
    session_state.api_instance_id == control_session.api_instance_id
    and session_state.agent_session_id == control_session.agent_session_id
  )
  if (
    not escape_command
    and projection_identity_matches
    and session_state.reason_code == QMT_AGENT_NOT_RECONCILED
  ):
    raise _TradeCommandDeliveryDeferred("agent_not_ready_for_command")
  if not escape_command and (
    not session_state.current or not projection_identity_matches
  ):
    raise _TradeCommandDeliveryDeferred("session_projection_unavailable")
  if live_risk_increase and not market_stream_ready:
    raise _TradeCommandDeliveryDeferred("market_stream_not_ready")
  if live_risk_increase:
    try:
      safety_status, market_stream_tradable = await asyncio.gather(
        AccountExecutionSafetyService().status(account_id),
        authoritative_market_stream_tradable(),
      )
    except _TRANSIENT_DEPENDENCY_ERRORS as exc:
      raise _TradeCommandDeliveryDeferred(
        "risk_gate_dependency_unavailable"
      ) from exc
    if not bool(safety_status.get("can_increase_risk")) or not market_stream_tradable:
      raise _TradeCommandDeliveryDeferred("risk_increase_gate_closed")


async def _enqueue_validated_trade_command(
  *,
  control_session: AgentControlSession,
  outbound: _AgentOutboundBuffer,
  envelope: AgentEnvelope,
) -> None:
  """Run slow delivery authority checks outside the physical WS writer."""
  await asyncio.wait_for(
    _assert_trade_delivery_session(control_session, envelope),
    timeout=AGENT_CONTROL_TRADE_VALIDATION_TIMEOUT_SECONDS,
  )
  await _enqueue_agent_outbound(
    control_session.device_id,
    outbound,
    envelope,
    deduplicate=True,
  )


async def _process_trade_command_validations(
  *,
  control_session: AgentControlSession,
  outbound: _AgentOutboundBuffer,
  validations: _TradeCommandValidationBuffer,
  database_state: _AgentDatabaseState,
  lane: str,
) -> None:
  device_id = control_session.device_id
  while True:
    item = await validations.get(lane)
    _observe_agent_control_stage(
      stage=f"trade_validation_{lane}_queue_wait",
      envelope=item.envelope,
      duration=time.monotonic() - item.queued_monotonic,
      device_id=device_id,
    )
    try:
      await _enqueue_validated_trade_command(
        control_session=control_session,
        outbound=outbound,
        envelope=item.envelope,
      )
    except _TradeCommandDeliveryDeferred as exc:
      if exc.reason in {
        "delivery_authority_dependency_unavailable",
        "risk_gate_dependency_unavailable",
        "session_projection_unavailable",
      }:
        database_state.mark_failure(exc.reason)
      AGENT_CONTROL_EVENTS.labels(
        event="delivery",
        reason=f"trade_command_deferred_{exc.reason}",
      ).inc()
      logger.info(
        "Agent %s-priority trade-command delivery deferred: "
        "device_id=%s message_id=%s reason=%s redelivery_seconds=%.1f",
        lane,
        device_id,
        item.envelope.message_id,
        exc.reason,
        TRADE_COMMAND_REDELIVERY_SECONDS,
      )
    except asyncio.TimeoutError:
      database_state.mark_failure()
      AGENT_CONTROL_EVENTS.labels(
        event="timeout",
        reason=f"trade_command_validation_{lane}",
      ).inc()
      logger.warning(
        "Agent %s-priority trade-command validation timed out: device_id=%s",
        lane,
        device_id,
      )
    finally:
      await validations.complete(item)


async def _poll_agent_trade_commands(
  *,
  control_session: AgentControlSession,
  protocol_version: str,
  validations: _TradeCommandValidationBuffer,
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
        queued = await validations.put(command)
        if not queued:
          lane = _trade_validation_lane(command)
          AGENT_CONTROL_EVENTS.labels(
            event="backpressure",
            reason=f"trade_command_validation_{lane}_full_or_duplicate",
          ).inc()
          logger.warning(
            "Agent %s-priority trade-command validation deferred: "
            "device_id=%s message_id=%s",
            lane,
            device_id,
            command.message_id,
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
    heartbeat_age = database_state.heartbeat_age()
    database_state._update_metrics()
    if (
      heartbeat_age >= AGENT_CONTROL_HEARTBEAT_STALE_SECONDS
      and inbound.qsize() == 0
    ):
      AGENT_CONTROL_EVENTS.labels(
        event="timeout",
        reason="heartbeat_transport_stale",
      ).inc()
      raise _AgentControlPipelineError(
        "heartbeat_transport_stale",
        reason_code=QMT_CONTROL_TRANSPORT_LOST,
      )
    heartbeat_remaining = AGENT_CONTROL_HEARTBEAT_STALE_SECONDS - heartbeat_age
    timeout_seconds = 5.0 if heartbeat_remaining <= 0 else min(5.0, heartbeat_remaining)
    revocation_reason = await agent_connection_hub.wait_until_revoked(
      control_session,
      timeout_seconds=timeout_seconds,
    )
    if revocation_reason:
      raise AuthError(
        revocation_reason,
        (
          "Agent 控制会话已被替换"
          if revocation_reason == QMT_CONTROL_SESSION_REPLACED
          else "Agent 设备已撤销"
        ),
      )

    async def load_authority():
      async with AsyncSessionLocal() as db:
        return await db.get(AgentDevice, device_id)

    try:
      device = await asyncio.wait_for(
        load_authority(),
        timeout=AGENT_CONTROL_DATABASE_POLL_TIMEOUT_SECONDS,
      )
    except _TRANSIENT_DEPENDENCY_ERRORS as exc:
      database_state.mark_failure(QMT_CONTROL_DEPENDENCY_UNAVAILABLE)
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
    if device is None or device.revoked_at is not None:
      raise AuthError(QMT_DEVICE_REVOKED, "Agent 设备已撤销")
    database_state.mark_success()


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
  validations = _TradeCommandValidationBuffer()
  database_state = _AgentDatabaseState(
    device_id=device_id,
    control_session=control_session,
  )
  tasks = {
    asyncio.create_task(
      _receive_agent_control_messages(
        websocket,
        device_id=device_id,
        protocol_version=protocol_version,
        inbound=inbound,
        outbound=outbound,
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
        lane=_INBOUND_DURABLE_LANE,
      ),
      name=f"agent-control-durable-processor:{device_id}",
    ),
    asyncio.create_task(
      _process_agent_control_messages(
        control_session=control_session,
        protocol_version=protocol_version,
        inbound=inbound,
        outbound=outbound,
        database_state=database_state,
        lane=_INBOUND_COMMAND_ACK_LANE,
      ),
      name=f"agent-control-command-ack-processor:{device_id}",
    ),
    asyncio.create_task(
      _process_agent_control_messages(
        control_session=control_session,
        protocol_version=protocol_version,
        inbound=inbound,
        outbound=outbound,
        database_state=database_state,
        lane=_INBOUND_HEARTBEAT_LANE,
      ),
      name=f"agent-control-heartbeat-processor:{device_id}",
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
        validations=validations,
        database_state=database_state,
      ),
      name=f"agent-command-poller:{device_id}",
    ),
    *(
      asyncio.create_task(
        _process_trade_command_validations(
          control_session=control_session,
          outbound=outbound,
          validations=validations,
          database_state=database_state,
          lane=_TRADE_VALIDATION_HIGH_LANE,
        ),
        name=f"agent-command-high-validator-{index}:{device_id}",
      )
      for index in range(AGENT_CONTROL_TRADE_VALIDATION_HIGH_WORKERS)
    ),
    *(
      asyncio.create_task(
        _process_trade_command_validations(
          control_session=control_session,
          outbound=outbound,
          validations=validations,
          database_state=database_state,
          lane=_TRADE_VALIDATION_NORMAL_LANE,
        ),
        name=f"agent-command-normal-validator-{index}:{device_id}",
      )
      for index in range(AGENT_CONTROL_TRADE_VALIDATION_NORMAL_WORKERS)
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
  disconnect_reason_code = QMT_CONTROL_TRANSPORT_LOST
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
  except WebSocketDisconnect as exc:
    disconnect_reason_code = QMT_CONTROL_TRANSPORT_LOST
    AGENT_CONTROL_EVENTS.labels(
      event="close",
      reason=QMT_CONTROL_TRANSPORT_LOST,
    ).inc()
    logger.info(
      "Agent WebSocket transport closed: code=%s reason_code=%s",
      getattr(exc, "code", None),
      disconnect_reason_code,
    )
    return
  except _AgentControlPipelineError as exc:
    disconnect_reason_code = exc.reason_code
    AGENT_CONTROL_EVENTS.labels(
      event="close",
      reason=disconnect_reason_code,
    ).inc()
    logger.warning(
      "Agent WebSocket closed: reason=%s reason_code=%s",
      exc.reason,
      disconnect_reason_code,
    )
    try:
      await websocket.close(
        code=exc.close_code,
        reason=disconnect_reason_code[:120],
      )
    except Exception:
      pass
  except AuthError as exc:
    disconnect_reason_code = str(exc.code or "UNAUTHENTICATED")[:64]
    AGENT_CONTROL_EVENTS.labels(
      event="close",
      reason=disconnect_reason_code,
    ).inc()
    close_code = (
      4409
      if disconnect_reason_code == QMT_CONTROL_SESSION_REPLACED
      else 4401
    )
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
      await websocket.close(
        code=close_code,
        reason=disconnect_reason_code[:120],
      )
    except Exception:
      pass
  except Exception as exc:
    disconnect_reason_code = QMT_CONTROL_TRANSPORT_LOST
    AGENT_CONTROL_EVENTS.labels(
      event="close",
      reason=disconnect_reason_code,
    ).inc()
    logger.warning(
      "Agent WebSocket closed: error=%s reason_code=%s",
      exc.__class__.__name__,
      disconnect_reason_code,
    )
    try:
      await websocket.close(code=1011, reason=disconnect_reason_code)
    except Exception:
      pass
  finally:
    if "control_session" in locals():
      if await agent_connection_hub.unregister(control_session):
        try:
          await _mark_session_offline(
            control_session,
            reason_code=disconnect_reason_code,
          )
        except Exception as exc:
          logger.warning(
            "无法持久化 Agent 离线状态: device=%s error=%s",
            control_session.device_id,
            exc.__class__.__name__,
          )
