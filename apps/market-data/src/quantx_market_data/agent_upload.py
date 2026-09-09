"""Authenticated historical uploads, hosted only by the independent Data API."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import timezone

from fastapi import APIRouter, Header, HTTPException, Request
from quantx_contracts.history_upload import HistoryUploadChunk, HistoryUploadSnapshot
from quantx_infrastructure.auth.agent_access import authenticate_agent_session
from quantx_infrastructure.auth.errors import AuthError
from quantx_infrastructure.auth.tokens import utcnow
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  MarketDataRequest,
  MarketDataTransfer,
)
from quantx_infrastructure.services import market_data_staging as _market_data_staging
from quantx_infrastructure.services.market_data_capacity import (
  MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES,
  MAX_MARKET_DATA_STAGING_BYTES,
  MIN_MARKET_DATA_STAGING_FREE_BYTES,
)
from quantx_infrastructure.services.market_data_capacity import (
  staging_free_bytes as _market_data_staging_free_bytes,
)
from quantx_infrastructure.services.market_data_capacity import (
  staging_usage_bytes as _market_data_staging_usage_bytes,
)
from sqlalchemy import func, select, text

logger = logging.getLogger(__name__)
agent_router = APIRouter(tags=["qmt-history-upload"])
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
MAX_MARKET_DATA_CHUNK_BYTES = 32 * 1024 * 1024
MAX_MARKET_DATA_CHUNK_RECORDS = 5000
# Agent bounds allow at most 99 record-bound emissions, 22 byte-bound
# emissions, and one final chunk; round the proven 122 ceiling up slightly.
MAX_MARKET_DATA_CHUNKS = 128
_MARKET_DATA_MUTABLE_UPLOAD_STATUSES = frozenset({"QUEUED", "DELIVERED", "RECEIVING"})
_MARKET_DATA_FROZEN_MANIFEST_STATUSES = frozenset(
  {"UPLOADED", "PROCESSING", "BLOCKED", "COMPLETED"}
)
_MARKET_DATA_AGENT_BUSY_REASON = "MARKET_DATA_AGENT_BUSY"
_market_data_staging_lock = asyncio.Lock()


def _publish_upload_file(temporary, destination, raw):
  """Persist bytes and directory entries before any database receipt is committed."""
  with temporary.open("xb") as output:
    output.write(raw)
    output.flush()
    os.fsync(output.fileno())
  os.replace(temporary, destination)
  if os.name != "nt":
    # The request directory can have been created by this upload as well.
    for directory in (destination.parent, destination.parent.parent):
      descriptor = os.open(directory, os.O_RDONLY)
      try:
        os.fsync(descriptor)
      finally:
        os.close(descriptor)


async def _persist_upload_file(temporary, destination, raw):
  task = asyncio.create_task(
    asyncio.to_thread(_publish_upload_file, temporary, destination, raw)
  )
  try:
    await asyncio.shield(task)
  except asyncio.CancelledError:
    # Cleanup must not unlink a file while its writer is still publishing it.
    # A published orphan is retained; no database acknowledgement is fabricated.
    while not task.done():
      try:
        await asyncio.shield(task)
      except asyncio.CancelledError:
        continue
      except Exception:
        break
    if not task.cancelled():
      task.exception()
    raise


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


@agent_router.get(
  "/agent/market-data/{request_id}/upload", response_model=HistoryUploadSnapshot
)
async def get_market_data_upload(request_id: uuid.UUID, request: Request):
  """Read immutable receipt identities, never paths, files or collection state."""
  async with AsyncSessionLocal() as db:
    try:
      identity = await authenticate_agent_session(
        db, settings, token=_bearer(request), history=True
      )
    except AuthError as exc:
      raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    # The same row lock as PUT/complete gives one coherent request + chunk view.
    row = await db.scalar(
      select(MarketDataRequest)
      .where(
        MarketDataRequest.request_id == str(request_id),
        MarketDataRequest.device_id == identity.device.id,
      )
      .with_for_update(read=True)
    )
    if row is None:
      raise HTTPException(status_code=404, detail="行情数据请求不存在")
    transfers = (
      (
        await db.execute(
          select(MarketDataTransfer)
          .where(MarketDataTransfer.request_id == str(request_id))
          .order_by(MarketDataTransfer.chunk_index)
          .limit(129)
        )
      )
      .scalars()
      .all()
    )
    verified_at = None
    if (
      row.status == "COMPLETED"
      and row.completed_at is not None
      and isinstance(row.ingestion_progress, dict)
      and row.ingestion_progress.get("phase") == "VERIFIED"
      and isinstance(row.ingestion_result, dict)
    ):
      native_pending = await db.scalar(
        text("""
        SELECT EXISTS(SELECT 1 FROM market_data_collection_permit
          WHERE request_id=:request_id AND state IN ('ISSUED','STARTED'))
      """),
        {"request_id": str(request_id)},
      )
      if not native_pending:
        verified_at = row.completed_at
        if verified_at.tzinfo is None:
          verified_at = verified_at.replace(tzinfo=timezone.utc)
    return HistoryUploadSnapshot(
      verified_at=verified_at,
      request_id=request_id,
      status=row.status,
      total_chunks=row.expected_chunks,
      chunks=[
        HistoryUploadChunk(
          index=item.chunk_index,
          sha256=item.checksum_sha256,
          record_count=item.record_count,
          byte_count=item.compressed_bytes,
        )
        for item in transfers
      ],
    )


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
      session = await authenticate_agent_session(
        db, settings, token=_bearer(request), history=True
      )
      device = session.device
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
    or x_total_chunks < 0
    or x_total_chunks > MAX_MARKET_DATA_CHUNKS
    or (x_total_chunks > 0 and chunk_index >= x_total_chunks)
    or (x_total_chunks == 0 and chunk_index >= MAX_MARKET_DATA_CHUNKS)
  ):
    raise HTTPException(status_code=400, detail="行情批次序号无效")
  if x_record_count < 0 or x_record_count > MAX_MARKET_DATA_CHUNK_RECORDS:
    raise HTTPException(status_code=400, detail="行情批次记录数无效")
  if content_encoding.strip().lower() != "gzip":
    raise HTTPException(status_code=415, detail="行情批次必须使用 gzip 压缩")

  async with AsyncSessionLocal() as db:
    try:
      session = await authenticate_agent_session(
        db, settings, token=_bearer(request), history=True
      )
      device = session.device
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
    if market_request.expected_chunks is not None and x_total_chunks == 0:
      existing = (
        await db.execute(
          select(MarketDataTransfer).where(
            MarketDataTransfer.request_id == normalized_request_id,
            MarketDataTransfer.chunk_index == chunk_index,
          )
        )
      ).scalar_one_or_none()
      if (
        existing is not None
        and existing.checksum_sha256 == digest
        and int(existing.record_count) == x_record_count
      ):
        return {"accepted": True, "duplicate": True}
      raise HTTPException(status_code=409, detail="行情 manifest 已声明")
    if (
      market_request.expected_chunks is not None
      and x_total_chunks > 0
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
        await _persist_upload_file(temporary, destination, raw)
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
        if x_total_chunks > 0:
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
          if x_total_chunks > 0 and market_request.received_chunks == x_total_chunks
          else "RECEIVING"
        )
        market_request.updated_at = utcnow()
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


@agent_router.post(
  "/agent/market-data/{request_id}/complete",
  status_code=202,
)
async def complete_market_data_upload(
  request_id: str,
  request: Request,
  x_total_chunks: int = Header(alias="X-Total-Chunks"),
):
  """Freeze a provisionally uploaded manifest after every spool chunk exists."""
  try:
    normalized_request_id = str(uuid.UUID(request_id))
  except ValueError as exc:
    raise HTTPException(status_code=400, detail="request_id 无效") from exc
  if x_total_chunks <= 0 or x_total_chunks > MAX_MARKET_DATA_CHUNKS:
    raise HTTPException(status_code=400, detail="行情批次总数无效")

  async with AsyncSessionLocal() as db:
    try:
      session = await authenticate_agent_session(
        db, settings, token=_bearer(request), history=True
      )
      device = session.device
      authenticated_device_id = device.id
    except AuthError as exc:
      raise HTTPException(
        status_code=exc.status_code,
        detail=exc.message,
      ) from exc

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
        reason="market-data manifest total_chunks mismatch",
      )
      raise HTTPException(status_code=409, detail="行情 manifest 总数不一致")
    if status in _MARKET_DATA_FROZEN_MANIFEST_STATUSES:
      return {"accepted": True, "duplicate": True}
    if status not in _MARKET_DATA_MUTABLE_UPLOAD_STATUSES:
      raise HTTPException(status_code=409, detail="行情数据请求已经结束")

    chunk_indices = list(
      (
        await db.execute(
          select(MarketDataTransfer.chunk_index)
          .where(MarketDataTransfer.request_id == normalized_request_id)
          .order_by(MarketDataTransfer.chunk_index)
        )
      ).scalars()
    )
    if chunk_indices != list(range(x_total_chunks)):
      raise HTTPException(status_code=409, detail="行情 manifest 仍有缺失批次")

    market_request.expected_chunks = x_total_chunks
    market_request.received_chunks = len(chunk_indices)
    market_request.status = "UPLOADED"
    market_request.updated_at = utcnow()
    await db.commit()
  return {"accepted": True, "duplicate": False}
