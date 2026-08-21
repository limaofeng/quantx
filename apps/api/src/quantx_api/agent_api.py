"""Outbound-only QMT Agent WebSocket hub and durable report ingestion."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from starlette.websockets import WebSocketState

from quantx_api.agent_hub import (
  MARKET_DEVICE_LEASE_REFRESH_SECONDS,
  agent_connection_hub,
)
from quantx_api.auth.agent_service import AgentAuthService
from quantx_api.auth.errors import AuthError
from quantx_api.auth.tokens import utcnow
from quantx_api.monitoring.metrics import (
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
MARKET_STREAM_MAX_QUEUE_AGE_SECONDS = 2.0
MARKET_STREAM_REDIS_CLEANUP_TIMEOUT_SECONDS = 2.0
MARKET_STREAM_CONTROL_SEND_TIMEOUT_SECONDS = 2.0
MARKET_STREAM_CONTROL_REGISTRATION_WAIT_SECONDS = 2.0
MARKET_STREAM_CONTROL_REGISTRATION_POLL_SECONDS = 0.025
TRADE_COMMAND_EXPIRY_SWEEP_INTERVAL_SECONDS = 1.0
TRADE_COMMAND_EXPIRY_SWEEP_BATCH_SIZE = 100

_MARKET_DATA_MUTABLE_UPLOAD_STATUSES = frozenset(
  {"QUEUED", "DELIVERED", "RECEIVING"}
)
_MARKET_DATA_FROZEN_MANIFEST_STATUSES = frozenset(
  {"UPLOADED", "PROCESSING", "COMPLETED"}
)
_market_data_staging_lock = asyncio.Lock()
_market_data_staging_sweep_lock = asyncio.Lock()


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
    self._queue: asyncio.Queue[
      _MarketCommitItem | _MarketCommitQueueClosed
    ] = asyncio.Queue(maxsize=capacity)

  @property
  def buffered_batches(self) -> int:
    return self._buffered_batches

  @property
  def buffered_bytes(self) -> int:
    return self._buffered_bytes

  async def reserve(self) -> None:
    async with self._condition:
      await self._condition.wait_for(
        lambda: self._buffered_batches < self._capacity
      )
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
  device_id: str,
  payload: dict[str, Any],
) -> None:
  if not await agent_connection_hub.is_market_device(device_id):
    raise AuthError("FORBIDDEN", "当前设备不是活动行情 Agent")
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
) -> AgentEnvelope:
  return AgentEnvelope(
    protocol_version=protocol_version,
    message_type=AgentMessageType.AUTH_RESULT,
    payload={"accepted": accepted, "reason": reason},
  )


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


async def _record_heartbeat(device_id: str, payload: dict[str, Any]) -> None:
  now = utcnow()
  async with AsyncSessionLocal() as db:
    agent = await db.get(AgentDevice, device_id)
    if agent is None or agent.revoked_at is not None:
      raise AuthError("UNAUTHENTICATED", "Agent 设备已撤销")
    agent.last_seen_at = now
    agent.capabilities = list(payload.get("capabilities") or [])
    heartbeat = await db.get(RuntimeComponentHeartbeat, f"qmt-agent:{device_id}")
    requested_status = str(payload.get("status", "READY"))[:32].upper()
    status = requested_status
    if (
      heartbeat is not None
      and str(heartbeat.status or "").upper()
      in {"RECONCILING", "RECONCILE_REQUIRED"}
      and requested_status == "READY"
    ):
      # Only Engine may promote a reconnecting Agent after the durable full
      # snapshot has been applied. A heartbeat is not reconciliation proof.
      status = str(heartbeat.status).upper()
    details = dict(heartbeat.details or {}) if heartbeat is not None else {}
    details.update(
      {
        "agentVersion": str(payload.get("agent_version", ""))[:64],
        "protocolVersion": str(payload.get("protocol_version", ""))[:16],
        "capabilities": agent.capabilities,
        "journalIntegrity": str(
          payload.get("journal_integrity", "")
        )[:32],
        "journalSizeBytes": int(payload.get("journal_size_bytes") or 0),
        "journalPendingReports": int(
          payload.get("journal_pending_reports") or 0
        ),
        "journalProcessingCommands": int(
          payload.get("journal_processing_commands") or 0
        ),
        "marketStreamStatus": str(
          payload.get("market_stream_status") or "OFFLINE"
        )[:32],
        "marketStreamSequence": int(
          payload.get("market_stream_sequence") or 0
        ),
        "marketStreamQueueDepth": int(
          payload.get("market_stream_queue_depth") or 0
        ),
        "marketStreamResyncs": int(
          payload.get("market_stream_resyncs") or 0
        ),
        "marketStreamAckLatencyMs": float(
          payload.get("market_stream_ack_latency_ms") or 0.0
        ),
      }
    )
    if heartbeat is None:
      db.add(
        RuntimeComponentHeartbeat(
          component=f"qmt-agent:{device_id}",
          instance_id=device_id,
          status=status,
          details=details,
          updated_at=now,
        )
      )
    else:
      heartbeat.status = status
      heartbeat.details = details
      heartbeat.updated_at = now
    await db.commit()


async def _ensure_device_active(device_id: str) -> None:
  async with AsyncSessionLocal() as db:
    device = await db.get(AgentDevice, device_id)
    if device is None or device.revoked_at is not None:
      raise AuthError("UNAUTHENTICATED", "Agent 设备已撤销")


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
      .where(
        StrategyOrderCorrelation.client_order_id == command.client_order_id
      )
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
      and (
        not pending.intent_id
        or (intent is not None and intent_zero_execution)
      )
    )
  )
  if normalized_status in {"EXPIRED", "REJECTED"} and not safe_pre_execution_state:
    normalized_status = "RECONCILE_REQUIRED"
    reason = f"{reason}:durable_pre_execution_proof_missing"[:256]

  request_metadata = {
    **(dict(pending.request_metadata or {}) if pending is not None else {}),
    **(
      dict(correlation.request_metadata or {})
      if correlation is not None
      else {}
    ),
  }
  intent_metadata = dict(intent.intent_metadata or {}) if intent is not None else {}
  entry_plan_id = str(
    request_metadata.get("entry_plan_id")
    or intent_metadata.get("entry_plan_id")
    or ""
  ).strip()
  managed_entry_zero_fill = bool(
    normalized_status == "EXPIRED"
    and safe_pre_execution_state
    and entry_plan_id
    and pending is not None
    and correlation is not None
    and intent is not None
    and str(pending.side or "").upper() == "BUY"
    and str(pending.strategy_run_id or "") == entry_plan_id
    and str(correlation.strategy_run_id or "") == entry_plan_id
    and str(intent.strategy_run_id or "") == entry_plan_id
    and str(intent.direction or "").upper() == "BUY"
    and str(intent_metadata.get("entry_plan_id") or "") == entry_plan_id
    and intent_zero_execution
  )
  strategy_status = (
    "RECONCILED_ZERO_FILL"
    if managed_entry_zero_fill
    else normalized_status
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
    await db.execute(
      query.order_by(TradeCommandOutbox.expires_at, TradeCommandOutbox.created_at)
      .limit(max(1, int(batch_size)))
      .with_for_update(skip_locked=True)
    )
  ).scalars().all()

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
        command.last_error = (
          reason or "indeterminate_cancel_command_rejection"
        )[:256]
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
    elif (
      previous_status in {"REJECTED", "EXPIRED"}
      and (
        (previous_status == "EXPIRED" and reason == "command_expired")
        or (
          previous_status == "REJECTED"
          and reason in _PRE_EXECUTION_REJECTION_REASONS
          and reason != "command_expired"
        )
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
  device_id: str,
  envelope: AgentEnvelope,
) -> ReportAckPayload:
  payload = envelope.payload
  if envelope.protocol_version == PROTOCOL_VERSION:
    envelope.validate_payload()
  payload_hash = hashlib.sha256(
    json.dumps(
      {
        "message_type": envelope.message_type.value,
        "payload": payload,
      },
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  body = _body_for_report_idempotency(envelope)
  business_idempotency_key = hashlib.sha256(
    (
      f"{device_id}:{envelope.message_type.value}:"
      f"{json.dumps(body, sort_keys=True, separators=(',', ':'), default=str)}"
    ).encode("utf-8")
  ).hexdigest()
  report = AgentReportInbox(
    message_id=envelope.message_id,
    device_id=device_id,
    message_type=envelope.message_type.value,
    protocol_version=envelope.protocol_version,
    client_order_id=str(payload.get("client_order_id", "")) or None,
    raw_payload_hash=payload_hash,
    business_idempotency_key=business_idempotency_key,
    payload=payload,
    received_at=utcnow(),
    processing_status="PENDING",
  )
  ack: ReportAckPayload
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
            (
              AgentReportInbox.message_id == envelope.message_id
            )
            | (
              AgentReportInbox.business_idempotency_key
              == business_idempotency_key
            )
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
  device_id: str,
  *,
  protocol_version: str = PROTOCOL_VERSION,
) -> Optional[AgentEnvelope]:
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
    heartbeat_stale = bool(
      heartbeat
      and heartbeat.updated_at < now - timedelta(seconds=90)
    )
    if (
      heartbeat is None
      or heartbeat_stale
      or str(heartbeat.status or "").upper() not in acceptable_statuses
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
  device_id: str,
  *,
  protocol_version: str = PROTOCOL_VERSION,
) -> Optional[AgentEnvelope]:
  async with AsyncSessionLocal() as db:
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


async def _requeue_incomplete_market_requests(device_id: str) -> None:
  """Resume interrupted transfers once, immediately after Agent reconnect."""
  async with AsyncSessionLocal() as db:
    await db.execute(
      update(MarketDataRequest)
      .where(
        MarketDataRequest.device_id == device_id,
        MarketDataRequest.status.in_(("DELIVERED", "RECEIVING")),
      )
      .values(status="QUEUED")
    )
    await db.commit()


async def _process_message(
  websocket: WebSocket,
  device_id: str,
  envelope: AgentEnvelope,
  *,
  protocol_version: str = PROTOCOL_VERSION,
) -> None:
  if envelope.message_type is AgentMessageType.HEARTBEAT:
    if str(envelope.payload.get("device_id", "")) != device_id:
      raise AuthError("UNAUTHENTICATED", "heartbeat 设备不匹配")
    await _record_heartbeat(device_id, envelope.payload)
    reply = AgentEnvelope(
      protocol_version=protocol_version,
      message_type=AgentMessageType.HEARTBEAT_ACK,
      payload={"heartbeat_message_id": envelope.message_id},
    )
    await websocket.send_text(reply.model_dump_json())
    return
  if envelope.message_type is AgentMessageType.COMMAND_ACK:
    await _record_command_ack(device_id, envelope.payload)
    return
  if envelope.message_type in REPORT_TYPES:
    ack = await _record_report(device_id, envelope)
    await websocket.send_text(
      AgentEnvelope(
        protocol_version=protocol_version,
        message_type=AgentMessageType.REPORT_ACK,
        payload=ack.model_dump(mode="json"),
      ).model_dump_json()
    )
    return
  if envelope.message_type is AgentMessageType.MARKET_EVENT:
    await _publish_market_event(device_id, envelope.payload)
    return
  raise ValueError(f"不支持的 Agent 消息类型: {envelope.message_type.value}")


async def _send_market_text(websocket: WebSocket, payload: str) -> None:
  def disconnected() -> bool:
    return (
      getattr(websocket, "client_state", None) is WebSocketState.DISCONNECTED
      or getattr(websocket, "application_state", None)
      is WebSocketState.DISCONNECTED
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


async def _wait_for_active_market_device(device_id: str) -> None:
  """Bridge the short race between control and market WebSocket startup."""
  deadline = (
    time.monotonic() + MARKET_STREAM_CONTROL_REGISTRATION_WAIT_SECONDS
  )
  while True:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      raise AuthError("FORBIDDEN", "当前设备不是活动行情 Agent")
    try:
      is_active = await asyncio.wait_for(
        agent_connection_hub.is_market_device(device_id),
        timeout=remaining,
      )
    except asyncio.TimeoutError as exc:
      raise AuthError(
        "FORBIDDEN",
        "当前设备不是活动行情 Agent",
      ) from exc
    if is_active:
      return
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      raise AuthError("FORBIDDEN", "当前设备不是活动行情 Agent")
    await asyncio.sleep(
      min(MARKET_STREAM_CONTROL_REGISTRATION_POLL_SECONDS, remaining)
    )


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
  session_expires_at: datetime,
  buffer: _MarketCommitBuffer,
  validate_device: Callable[[str], Awaitable[None]] | None = None,
) -> None:
  device_validator = validate_device or _ensure_device_active
  expected_sequence = 1
  next_device_check = 0.0
  while True:
    if utcnow() >= session_expires_at:
      raise AuthError("UNAUTHENTICATED", "Agent 访问令牌已过期")
    now = time.monotonic()
    if now >= next_device_check:
      await device_validator(device_id)
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

      # A quiet socket can remain blocked across token expiry or device
      # revocation. Revalidate before parsing or exposing its first frame to
      # the Redis committer so that frame can never be persisted or ACKed.
      if utcnow() >= session_expires_at:
        raise AuthError("UNAUTHENTICATED", "Agent 访问令牌已过期")
      now = time.monotonic()
      if now >= next_device_check:
        await device_validator(device_id)
        next_device_check = (
          time.monotonic() + MARKET_STREAM_DEVICE_REVALIDATE_SECONDS
        )

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
      if (
        expected_sequence == 1
        and batch.kind is not MarketBatchKind.SNAPSHOT
      ):
        raise ValueError("first market stream batch must be SNAPSHOT")
      if expected_sequence > 1 and batch.kind is MarketBatchKind.SNAPSHOT:
        raise ValueError(
          "market stream SNAPSHOT is only valid as the first batch"
        )
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
        await buffer.cancel_reservation(
          payload_bytes=reserved_payload_bytes
        )
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
      queue_age = time.monotonic() - queued.received_monotonic
      if queue_age > MARKET_STREAM_MAX_QUEUE_AGE_SECONDS:
        raise TimeoutError(
          "market stream commit queue exceeded maximum age: "
          f"age={queue_age:.3f}s"
        )
      state = await asyncio.wait_for(
        active_store.write_batch(
          queued.batch,
          queued.payload,
          received_at=queued.received_at,
        ),
        timeout=MARKET_STREAM_REDIS_COMMIT_TIMEOUT_SECONDS,
      )
      MARKET_STREAM_PROCESSING.observe(
        time.monotonic() - queued.received_monotonic
      )
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
  session_expires_at: datetime,
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
      session_expires_at=session_expires_at,
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
    capabilities = {
      str(value).lower() for value in first.payload.get("capabilities", [])
    }
    if "market-data" not in capabilities:
      raise AuthError("FORBIDDEN", "Agent 未声明 market-data 能力")
    await _wait_for_active_market_device(device.id)
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
      session_expires_at=session.expires_at,
      commit_state=commit_state,
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


@agent_router.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket) -> None:
  await websocket.accept()
  try:
    first = AgentEnvelope.model_validate_json(await websocket.receive_text())
    session = await _authenticate(first)
    device = session.device
    capabilities = {
      str(value).lower() for value in first.payload.get("capabilities", [])
    }
    if "live" in capabilities and first.protocol_version != PROTOCOL_VERSION:
      raise AuthError(
        "PROTOCOL_UPGRADE_REQUIRED",
        f"真实交易 Agent 必须使用协议 {PROTOCOL_VERSION}",
      )
    connection_protocol = first.protocol_version
    await websocket.send_text(
      _auth_result(
        accepted=True,
        protocol_version=connection_protocol,
      ).model_dump_json()
    )
    await _record_heartbeat(
      device.id,
      {
        "agent_version": first.payload.get("agent_version", ""),
        "protocol_version": connection_protocol,
        "capabilities": first.payload.get("capabilities", []),
        "status": "RECONCILING",
      },
    )
    await _requeue_incomplete_market_requests(device.id)
    control_queue = await agent_connection_hub.register(
      device.id,
      set(first.payload.get("capabilities") or []),
    )
    next_market_lease_refresh = time.monotonic()
    while True:
      if utcnow() >= session.expires_at:
        raise AuthError("UNAUTHENTICATED", "Agent 访问令牌已过期")
      await _ensure_device_active(device.id)
      if time.monotonic() >= next_market_lease_refresh:
        await agent_connection_hub.refresh_market_device(
          device.id,
          control_queue,
        )
        next_market_lease_refresh = (
          time.monotonic() + MARKET_DEVICE_LEASE_REFRESH_SECONDS
        )
      command = await _next_command(
        device.id,
        protocol_version=connection_protocol,
      )
      if command is not None:
        await websocket.send_text(command.model_dump_json())
      market_request = await _next_market_data_request(
        device.id,
        protocol_version=connection_protocol,
      )
      if market_request is not None:
        await websocket.send_text(market_request.model_dump_json())
      while True:
        try:
          control = control_queue.get_nowait()
        except asyncio.QueueEmpty:
          break
        await websocket.send_text(
          control.model_copy(
            update={"protocol_version": connection_protocol}
          ).model_dump_json()
        )
      try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
      except asyncio.TimeoutError:
        continue
      envelope = AgentEnvelope.model_validate_json(raw)
      if envelope.protocol_version != connection_protocol:
        raise ValueError("Agent connection changed protocol version")
      await _process_message(
        websocket,
        device.id,
        envelope,
        protocol_version=connection_protocol,
      )
  except WebSocketDisconnect:
    return
  except AuthError as exc:
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
  except Exception as exc:
    logger.warning("Agent WebSocket closed: %s", exc.__class__.__name__)
    await websocket.close(code=4400)
  finally:
    if "control_queue" in locals() and "device" in locals():
      await agent_connection_hub.unregister(device.id, control_queue)


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
    requests = {
      str(row.request_id): row
      for row in rows
    }
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
                terminal_at = _as_aware_utc(
                  locked.completed_at or locked.updated_at
                )
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


@agent_router.post(
  "/agent/market-data/{request_id}/fail",
  status_code=202,
)
async def fail_market_data_request(
  request_id: str,
  request: Request,
):
  """Let an authenticated Agent terminate a request it cannot execute."""
  try:
    normalized_request_id = str(uuid.UUID(request_id))
  except ValueError as exc:
    raise HTTPException(status_code=400, detail="request_id 无效") from exc

  async with AsyncSessionLocal() as db:
    try:
      device = await AgentAuthService(db).authenticate_agent(
        token=_bearer(request)
      )
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
      device = await AgentAuthService(db).authenticate_agent(
        token=_bearer(request)
      )
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
    if (
      market_request is None
      or market_request.device_id != authenticated_device_id
    ):
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
          select(func.coalesce(func.sum(MarketDataTransfer.compressed_bytes), 0))
          .where(MarketDataTransfer.request_id == normalized_request_id)
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
            storage_reference=str(destination),
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
