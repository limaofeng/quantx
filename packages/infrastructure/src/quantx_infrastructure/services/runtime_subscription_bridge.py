"""Redis wake/broadcast bridge between API subscriptions and the Engine."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Optional

from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.models.market_depth import MarketDepth, MarketDepthLevel
from quantx_infrastructure.models.realtime_price import RealTimePrice
from quantx_infrastructure.models.tick import Tick

RUNTIME_SUBSCRIPTION_CONTROL = "runtime-subscription:control"
TRADING_EVENT_CHANNEL = "trading-event:orders"


class RuntimeSubscriptionBridge:
  @staticmethod
  def _model_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    if isinstance(data.get("time"), str):
      data["time"] = datetime.fromisoformat(data["time"].replace("Z", "+00:00"))
    return data

  async def subscribe_price(self, symbol: str) -> AsyncIterator[RealTimePrice]:
    async for payload in self.stream("market-price", symbol=symbol):
      yield RealTimePrice(**self._model_payload(payload))

  async def subscribe_kline(
    self,
    symbol: str,
    period: str,
  ) -> AsyncIterator[KLine]:
    async for payload in self.stream(
      "market-kline",
      symbol=symbol,
      period=period,
    ):
      yield KLine(**self._model_payload(payload))

  async def subscribe_tick(self, symbol: str) -> AsyncIterator[Tick]:
    async for payload in self.stream("market-tick", symbol=symbol):
      yield Tick(**self._model_payload(payload))

  async def subscribe_depth(self, symbol: str) -> AsyncIterator[MarketDepth]:
    async for payload in self.stream("market-depth", symbol=symbol):
      data = self._model_payload(payload)
      data["bid_levels"] = [
        MarketDepthLevel(**item) for item in data.get("bid_levels") or []
      ]
      data["ask_levels"] = [
        MarketDepthLevel(**item) for item in data.get("ask_levels") or []
      ]
      yield MarketDepth(**data)

  async def subscribe_trading_events(self) -> AsyncIterator[dict[str, Any]]:
    """Stream order-event wake-ups produced after Engine DB convergence."""
    subscription = await redis_pubsub.open_subscription(TRADING_EVENT_CHANNEL)
    try:
      async for message in subscription.messages():
        yield message
    finally:
      await subscription.close()

  async def stream(
    self,
    stream_type: str,
    *,
    symbol: Optional[str] = None,
    period: Optional[str] = None,
    run_id: Optional[str] = None,
    include_history: bool = False,
  ) -> AsyncIterator[dict[str, Any]]:
    subscription_id = str(uuid.uuid4())
    channel = f"runtime-subscription:data:{subscription_id}"
    subscription = await redis_pubsub.open_subscription(channel)
    await redis_pubsub.publish(
      RUNTIME_SUBSCRIPTION_CONTROL,
      {
        "action": "SUBSCRIBE",
        "subscription_id": subscription_id,
        "channel": channel,
        "stream_type": stream_type,
        "symbol": symbol,
        "period": period,
        "run_id": run_id,
        "include_history": include_history,
      },
    )
    try:
      async for message in subscription.messages():
        if message.get("subscription_id") != subscription_id:
          continue
        if message.get("event") == "ERROR":
          raise RuntimeError(str(message.get("error") or "Engine subscription failed"))
        yield dict(message.get("payload") or {})
    finally:
      await redis_pubsub.publish(
        RUNTIME_SUBSCRIPTION_CONTROL,
        {
          "action": "UNSUBSCRIBE",
          "subscription_id": subscription_id,
        },
      )
      await subscription.close()


runtime_subscription_bridge = RuntimeSubscriptionBridge()
