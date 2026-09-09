from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import (
  APIRouter,
  WebSocket,
  WebSocketDisconnect,
)
from quantx_contracts import MarketBatchKind, MarketStreamBatch
from quantx_infrastructure.core.data.market_stream_transport import market_stream_store

from .development_access import authorized

router = APIRouter(prefix="/market-data/v1")
_stream_connection = asyncio.Lock()


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
    snapshot = await market_stream_store.load_selected_snapshot(selected)
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
