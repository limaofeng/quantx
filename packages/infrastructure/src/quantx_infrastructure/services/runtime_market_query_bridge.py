"""Transient API-to-Engine queries for Engine-owned intraday observations."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from quantx_infrastructure.database.redis_pubsub import redis_pubsub

RUNTIME_MARKET_QUERY_CONTROL = "runtime-market-query:control"


class RuntimeMarketQueryBridge:
  async def query(
    self,
    operation: str,
    payload: dict[str, Any],
    *,
    timeout: float = 2.0,
  ) -> dict[str, Any] | None:
    request_id = str(uuid.uuid4())
    channel = f"runtime-market-query:result:{request_id}"
    subscription = await redis_pubsub.open_subscription(channel)
    try:
      await redis_pubsub.publish(
        RUNTIME_MARKET_QUERY_CONTROL,
        {
          "request_id": request_id,
          "response_channel": channel,
          "operation": operation,
          "payload": payload,
        },
      )
      iterator = subscription.messages()
      response = await asyncio.wait_for(iterator.__anext__(), timeout=timeout)
      if response.get("request_id") != request_id:
        return None
      if not response.get("ok"):
        return None
      result = response.get("result")
      return dict(result) if isinstance(result, dict) else None
    except (asyncio.TimeoutError, OSError):
      return None
    finally:
      await subscription.close()


runtime_market_query_bridge = RuntimeMarketQueryBridge()
