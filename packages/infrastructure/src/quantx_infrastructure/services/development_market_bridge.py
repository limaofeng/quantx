"""Import read-only remote quotes into an isolated development market store."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from quantx_contracts import MarketBatchKind, MarketStreamBatch
from websockets.asyncio.client import connect

from quantx_infrastructure.core.data.market_stream_transport import market_stream_store

logger = logging.getLogger(__name__)


async def run_bridge() -> None:
  if os.environ.get("ENV") != "development":
    raise ValueError("Remote market import is development-only")
  endpoint = urlsplit(os.environ["QUANTX_MARKET_DATA_URL"])
  if endpoint.scheme not in {"http", "https"} or endpoint.username or endpoint.password:
    raise ValueError("Invalid remote market URL")
  url = urlunsplit(
    (
      "wss" if endpoint.scheme == "https" else "ws",
      endpoint.netloc,
      "/market-data/v1/stream",
      "",
      "",
    )
  )
  codes = [
    code.strip()
    for code in os.environ["QUANTX_MARKET_DATA_INSTRUMENTS"].split(",")
    if code.strip()
  ]
  while True:
    local_id = f"development-{uuid4()}"
    try:
      await market_stream_store.mark_syncing(local_id)
      async with connect(
        url,
        additional_headers={
          "Authorization": f"Bearer {os.environ['QUANTX_MARKET_DATA_TOKEN']}"
        },
        max_size=64 * 1024 * 1024,
        max_queue=16,
        proxy=None,
      ) as socket:
        await socket.send(json.dumps({"instruments": codes}))
        source_id, source_sequence, local_sequence = "", 0, 0
        while True:
          async with asyncio.timeout(15):
            payload = await socket.recv()
          batch = MarketStreamBatch.from_bytes(payload)
          if local_sequence == 0:
            if batch.kind != MarketBatchKind.SNAPSHOT or set(
              batch.universe_codes
            ) != set(codes):
              raise ValueError("Expected selected snapshot")
            source_id = batch.stream_id
          elif (
            batch.stream_id != source_id
            or batch.sequence != source_sequence + 1
            or batch.kind != MarketBatchKind.DELTA
          ):
            raise ValueError("Remote sequence gap")
          source_sequence = batch.sequence
          local_sequence += 1
          # Preserve capture and individual tick times. Import never renews source age.
          local = batch.model_copy(
            update={"stream_id": local_id, "sequence": local_sequence}
          )
          await market_stream_store.write_batch(local, local.to_bytes())
    except asyncio.CancelledError:
      raise
    except Exception as exc:
      logger.warning("Development market bridge disconnected: %s", type(exc).__name__)
    finally:
      await market_stream_store.mark_offline(local_id, reason="REMOTE_DISCONNECTED")
    await asyncio.sleep(3)
