"""Read-only, bounded export of the existing authoritative market stream."""

from __future__ import annotations

import asyncio
import hmac
import os
from contextlib import suppress
from datetime import date

from fastapi import (
  APIRouter,
  Depends,
  Header,
  HTTPException,
  WebSocket,
  WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from quantx_contracts import MarketBatchKind, MarketStreamBatch
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_infrastructure.core.data.market_stream_transport import market_stream_store
from quantx_infrastructure.services.data_exchange import (
  content_path,
  get_export,
  submit,
)

router = APIRouter(prefix="/market-data/v1")
_stream_connection = asyncio.Lock()


def require_data_access(authorization: str = Header(default="")) -> None:
  if not authorized(authorization):
    raise HTTPException(403, "Market data access denied")


@router.get("/reference/{instrument}", dependencies=[Depends(require_data_access)])
async def history_reference(instrument: str, as_of: date) -> dict:
  from quantx_infrastructure.services.data_exchange_reference import export_reference

  try:
    HistoryPartitionRequest(instrument=instrument, period="1d", trading_date=as_of)
    return await export_reference(instrument, as_of)
  except ValueError:
    raise HTTPException(422, "Reference data unavailable or invalid scope") from None


@router.get("/calendar/{year}", dependencies=[Depends(require_data_access)])
async def history_calendar(year: int) -> dict:
  from quantx_infrastructure.services.holiday_service import HolidayService

  if not 1990 <= year <= 2100:
    raise HTTPException(422, "Invalid calendar year")
  holidays = await HolidayService().get_holidays("SH", year)
  if not holidays:
    raise HTTPException(503, "Calendar unavailable")
  return {
    "year": year,
    "market": "SH",
    "holidays": [
      {"date": item.date.isoformat(), "description": item.description}
      for item in holidays
    ],
  }


@router.post("/history", dependencies=[Depends(require_data_access)], status_code=202)
async def request_history(request: HistoryPartitionRequest) -> dict:
  from datetime import datetime
  from zoneinfo import ZoneInfo

  current = datetime.now(ZoneInfo("Asia/Shanghai"))
  if request.trading_date > current.date() or (
    request.trading_date == current.date() and current.hour < 16
  ):
    raise HTTPException(422, "Only closed historical dates can be exported")
  try:
    return {"id": await submit(request)}
  except ValueError:
    raise HTTPException(429, "History queue capacity reached") from None


@router.get("/history/{identity}", dependencies=[Depends(require_data_access)])
async def history_status(identity: str) -> dict:
  result = await get_export(identity)
  if result is None:
    raise HTTPException(404, "Unknown export")
  return result


@router.post("/history/{identity}/retry", dependencies=[Depends(require_data_access)])
async def retry_history(identity: str) -> dict:
  from quantx_infrastructure.database.connection import AsyncSessionLocal
  from sqlalchemy import text

  async with AsyncSessionLocal() as db:
    result = await db.execute(
      text(
        "UPDATE development_data_export SET state='QUEUED',error=NULL WHERE id=:id AND state='INCOMPLETE'"
      ),
      {"id": identity},
    )
    await db.commit()
  return {"requeued": bool(result.rowcount)}


@router.get(
  "/history/{identity}/chunks/{digest}", dependencies=[Depends(require_data_access)]
)
async def history_chunk(identity: str, digest: str) -> FileResponse:
  from datetime import datetime, timezone

  result = await get_export(identity)
  if result is None:
    raise HTTPException(404, "Unknown export")
  if not result["expires_at"] or result["expires_at"] <= datetime.now(timezone.utc):
    raise HTTPException(410, "Export expired; submit again")
  if digest not in {
    item["checksum_sha256"] for item in (result["manifest"] or {}).get("chunks", [])
  }:
    raise HTTPException(404, "Unknown chunk")
  path = content_path(digest)
  if not path.is_file():
    raise HTTPException(410, "Export file unavailable; submit again")
  return FileResponse(
    path, media_type="application/gzip", headers={"Cache-Control": "private, no-store"}
  )


def authorized(authorization: str) -> bool:
  token = os.environ.get("QUANTX_MARKET_DATA_TOKEN", "")
  return (
    os.environ.get("ENV") == "production"
    and len(token) >= 32
    and "CHANGE_ME" not in token
    and hmac.compare_digest(authorization, f"Bearer {token}")
  )


@router.websocket("/stream")
async def stream(socket: WebSocket) -> None:
  if not authorized(socket.headers.get("authorization", "")):
    await socket.close(code=1008)
    return
  if _stream_connection.locked():
    await socket.close(code=1013)
    return
  await _stream_connection.acquire()
  subscription = None
  reader = None
  disconnected = None
  try:
    await socket.accept()
    async with asyncio.timeout(5):
      request = await socket.receive_json()
    codes = request.get("instruments") if isinstance(request, dict) else None
    if not isinstance(codes, list) or not codes or len(codes) > 500:
      raise ValueError("Invalid instrument selection")
    if any(not isinstance(code, str) for code in codes):
      raise ValueError("Invalid instrument selection")
    selected = frozenset(codes)
    # Subscribe before loading the consistent snapshot so no delta can be lost.
    subscription = await market_stream_store.open_subscription()
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=32)
    queued_bytes = 0

    async def collect() -> None:
      nonlocal queued_bytes
      async for payload in subscription.messages():
        queued_bytes += len(payload)
        if queued_bytes > 8 * 1024 * 1024:
          raise asyncio.QueueFull
        queue.put_nowait(payload)

    reader = asyncio.create_task(collect())
    snapshot = await market_stream_store.load_snapshot()
    if snapshot is None:
      raise ValueError("Source snapshot unavailable")
    state, ticks = snapshot
    if not selected.issubset(ticks) or state.captured_at is None:
      raise ValueError("Selection outside existing production supply")
    batch = MarketStreamBatch(
      stream_id=state.stream_id,
      sequence=state.sequence,
      captured_at=state.captured_at,
      kind=MarketBatchKind.SNAPSHOT,
      universe_codes=tuple(sorted(selected)),
      instrument_count=len(selected),
      data={code: ticks[code] for code in selected},
    )
    async with asyncio.timeout(5):
      await socket.send_bytes(batch.to_bytes())
    disconnected = asyncio.create_task(socket.receive())
    sequence = state.sequence
    while True:
      next_payload = asyncio.create_task(queue.get())
      try:
        done, _ = await asyncio.wait(
          {next_payload, reader, disconnected},
          timeout=10,
          return_when=asyncio.FIRST_COMPLETED,
        )
        if disconnected in done:
          raise WebSocketDisconnect()
        if reader in done:
          await reader
          raise ValueError("Source subscription ended")
        if next_payload not in done:
          raise TimeoutError()
        payload = next_payload.result()
      finally:
        next_payload.cancel()
        await asyncio.gather(next_payload, return_exceptions=True)
      queued_bytes -= len(payload)
      source = MarketStreamBatch.from_bytes(payload)
      if source.stream_id != state.stream_id:
        raise ValueError("Source stream changed")
      if source.sequence <= sequence:
        continue
      if source.sequence != sequence + 1 or source.kind != MarketBatchKind.DELTA:
        raise ValueError("Source sequence gap")
      sequence = source.sequence
      data = {code: tick for code, tick in source.data.items() if code in selected}
      filtered = source.model_copy(update={"data": data, "instrument_count": len(data)})
      async with asyncio.timeout(5):
        await socket.send_bytes(filtered.to_bytes())
  except (ValueError, TimeoutError, asyncio.QueueFull, WebSocketDisconnect):
    with suppress(RuntimeError, WebSocketDisconnect):
      await socket.close(
        code=1013, reason="Market stream unavailable; reconnect and resync"
      )
  finally:
    if disconnected:
      disconnected.cancel()
      await asyncio.gather(disconnected, return_exceptions=True)
    if reader:
      reader.cancel()
      await asyncio.gather(reader, return_exceptions=True)
    try:
      if subscription:
        await subscription.close()
    finally:
      _stream_connection.release()
