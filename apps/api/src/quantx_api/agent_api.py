"""Outbound-only QMT Agent WebSocket hub and durable report ingestion."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import timedelta, timezone
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
  PROTOCOL_VERSION,
  AgentEnvelope,
  AgentMessageType,
  ReportAckPayload,
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
  RuntimeComponentHeartbeat,
  TradeCommandOutbox,
)
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from quantx_api.agent_hub import agent_connection_hub
from quantx_api.auth.agent_service import AgentAuthService
from quantx_api.auth.errors import AuthError
from quantx_api.auth.tokens import utcnow

logger = logging.getLogger(__name__)
agent_router = APIRouter(tags=["qmt-agent"])
MARKET_DATA_ROOT = (
  (
    Path(os.environ["QUANTX_RUNTIME_DIR"]).expanduser().resolve()
    if os.environ.get("QUANTX_RUNTIME_DIR")
    else (
      (
        Path(os.environ["QUANTX_ROOT"]).expanduser().resolve()
        if os.environ.get("QUANTX_ROOT")
        else Path(__file__).resolve().parents[4]
      )
      / ".runtime"
    )
  )
  / "market-data"
)
REPORT_TYPES = {
  AgentMessageType.ORDER_REPORT,
  AgentMessageType.EXECUTION_REPORT,
  AgentMessageType.DELTA_REPORT,
}
MAX_MARKET_DATA_CHUNK_BYTES = 32 * 1024 * 1024
MAX_MARKET_DATA_CHUNK_RECORDS = 5000
MAX_MARKET_DATA_CHUNKS = 100_000


async def _publish_market_event(
  device_id: str,
  payload: dict[str, Any],
) -> None:
  if not await agent_connection_hub.is_market_device(device_id):
    raise AuthError("FORBIDDEN", "当前设备不是活动行情 Agent")
  kind = str(payload.get("kind") or "")
  stock_code = str(payload.get("stock_code") or "")
  period = str(payload.get("period") or "tick")
  if kind == "whole":
    channel = "market-data:whole"
  elif kind == "quote" and stock_code:
    channel = f"market-data:{stock_code}:{period}"
  else:
    raise ValueError("market_event 缺少有效行情路由")
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


async def _record_command_ack(device_id: str, payload: dict[str, Any]) -> None:
  message_id = str(payload.get("command_message_id", ""))
  client_order_id = str(payload.get("client_order_id", ""))
  if not message_id and not client_order_id:
    raise ValueError("command_ack 缺少命令标识")
  async with AsyncSessionLocal() as db:
    query = select(TradeCommandOutbox).where(
      TradeCommandOutbox.device_id == device_id
    )
    if message_id:
      query = query.where(TradeCommandOutbox.message_id == message_id)
    else:
      query = query.where(TradeCommandOutbox.client_order_id == client_order_id)
    command = (await db.execute(query.with_for_update())).scalar_one_or_none()
    if command is None:
      return
    command.delivery_status = (
      "ACKNOWLEDGED" if bool(payload.get("accepted")) else "REJECTED"
    )
    command.acknowledged_at = utcnow()
    command.last_error = str(payload.get("reason", ""))[:256] or None
    await db.commit()


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
    await db.execute(
      update(TradeCommandOutbox)
      .where(
        TradeCommandOutbox.device_id == device_id,
        TradeCommandOutbox.delivery_status.in_(("QUEUED", "DELIVERED")),
        TradeCommandOutbox.expires_at <= now,
      )
      .values(
        delivery_status="EXPIRED",
        last_error="command_expired",
      )
    )
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
    while True:
      if utcnow() >= session.expires_at:
        raise AuthError("UNAUTHENTICATED", "Agent 访问令牌已过期")
      await _ensure_device_active(device.id)
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
    if market_request is None or market_request.status in {"COMPLETED", "FAILED"}:
      return
    market_request.status = "FAILED"
    market_request.processing_error = reason[:1000]
    market_request.completed_at = utcnow()
    await db.commit()


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
    if market_request.status == "FAILED":
      raise HTTPException(status_code=409, detail="行情数据请求已经结束")
    if (
      market_request.expected_chunks is not None
      and int(market_request.expected_chunks) != x_total_chunks
    ):
      if market_request.status != "COMPLETED":
        market_request.status = "FAILED"
        market_request.processing_error = (
          f"chunk {chunk_index} total_chunks mismatch"
        )
        market_request.completed_at = utcnow()
        await db.commit()
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
        if market_request.status != "COMPLETED":
          market_request.status = "FAILED"
          market_request.processing_error = (
            f"chunk {chunk_index} checksum mismatch"
          )
          market_request.completed_at = utcnow()
          await db.commit()
        raise HTTPException(status_code=409, detail="重复批次内容不一致")
      if int(existing.record_count) != x_record_count:
        if market_request.status != "COMPLETED":
          market_request.status = "FAILED"
          market_request.processing_error = (
            f"chunk {chunk_index} record_count mismatch"
          )
          market_request.completed_at = utcnow()
          await db.commit()
        raise HTTPException(status_code=409, detail="重复批次记录数不一致")
      return {"accepted": True, "duplicate": True}
    if market_request.status == "COMPLETED":
      raise HTTPException(status_code=409, detail="行情数据请求已经结束")

    destination_directory = MARKET_DATA_ROOT / normalized_request_id
    destination_directory.mkdir(parents=True, exist_ok=True)
    destination = destination_directory / f"{chunk_index:08d}.json.gz"
    temporary = destination.with_suffix(
      f"{destination.suffix}.{uuid.uuid4().hex}.tmp"
    )
    try:
      async with aiofiles.open(temporary, "wb") as output:
        await output.write(raw)
      os.replace(temporary, destination)
    finally:
      temporary.unlink(missing_ok=True)
    db.add(
      MarketDataTransfer(
        transfer_id=str(uuid.uuid4()),
        request_id=normalized_request_id,
        chunk_index=chunk_index,
        checksum_sha256=digest,
        record_count=x_record_count,
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
    await db.commit()
  return {"accepted": True, "duplicate": False}
