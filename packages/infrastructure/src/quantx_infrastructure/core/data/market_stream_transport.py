"""Redis cache and binary fan-out for the authoritative whole-quote stream."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator

import orjson
import redis.asyncio as aioredis
from quantx_contracts import MarketBatchKind, MarketStreamBatch

from quantx_infrastructure.config.settings import settings

logger = logging.getLogger(__name__)

MARKET_STREAM_BATCH_CHANNEL = "market-data:whole:v1:batches"
MARKET_STREAM_LATEST_KEY = "market-data:whole:v1:latest"
MARKET_STREAM_STATE_KEY = "market-data:whole:v1:state"
MARKET_STREAM_ENGINE_STATE_KEY = "market-data:whole:v1:engine-state"
LEGACY_ACTIVE_SUBSCRIPTIONS_KEY = "market-data:active-subscriptions"


def _utcnow() -> datetime:
  return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MarketStreamState:
  status: str
  stream_id: str = ""
  sequence: int = 0
  captured_at: datetime | None = None
  updated_at: datetime | None = None
  instrument_count: int = 0
  reason: str = ""

  def to_bytes(self) -> bytes:
    return orjson.dumps(
      {
        "status": self.status,
        "stream_id": self.stream_id,
        "sequence": self.sequence,
        "captured_at": self.captured_at,
        "updated_at": self.updated_at,
        "instrument_count": self.instrument_count,
        "reason": self.reason,
      }
    )

  @classmethod
  def from_bytes(cls, payload: bytes | None) -> "MarketStreamState | None":
    if not payload:
      return None
    raw = orjson.loads(payload)

    def parse_time(value: object) -> datetime | None:
      if not value:
        return None
      parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
      return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)

    return cls(
      status=str(raw.get("status") or "OFFLINE").upper(),
      stream_id=str(raw.get("stream_id") or ""),
      sequence=int(raw.get("sequence") or 0),
      captured_at=parse_time(raw.get("captured_at")),
      updated_at=parse_time(raw.get("updated_at")),
      instrument_count=int(raw.get("instrument_count") or 0),
      reason=str(raw.get("reason") or ""),
    )


class BinaryMarketSubscription:
  def __init__(self, redis: aioredis.Redis, pubsub) -> None:
    self._redis = redis
    self._pubsub = pubsub

  async def messages(self) -> AsyncIterator[bytes]:
    async for message in self._pubsub.listen():
      if message["type"] == "message" and isinstance(message["data"], bytes):
        yield message["data"]

  async def wait_for_message(self, timeout: float = 0.0) -> bytes | None:
    message = await self._pubsub.get_message(
      ignore_subscribe_messages=True,
      timeout=timeout,
    )
    if message is None or not isinstance(message.get("data"), bytes):
      return None
    return message["data"]

  async def close(self) -> None:
    await self._pubsub.unsubscribe(MARKET_STREAM_BATCH_CHANNEL)
    await self._pubsub.aclose()
    await self._redis.aclose()


class MarketStreamStore:
  """Atomic latest-state cache plus lossy notification transport."""

  def __init__(self, redis_client: aioredis.Redis | None = None) -> None:
    self._redis = redis_client

  async def redis(self) -> aioredis.Redis:
    if self._redis is None:
      self._redis = aioredis.Redis.from_url(
        settings.redis_url,
        password=settings.redis_password or None,
        decode_responses=False,
        socket_timeout=settings.redis_socket_timeout,
        socket_connect_timeout=settings.redis_socket_connect_timeout,
        max_connections=settings.redis_max_connections,
      )
    return self._redis

  async def open_subscription(self) -> BinaryMarketSubscription:
    subscriber = aioredis.Redis.from_url(
      settings.redis_url,
      password=settings.redis_password or None,
      decode_responses=False,
      socket_timeout=settings.redis_socket_timeout,
      socket_connect_timeout=settings.redis_socket_connect_timeout,
      max_connections=settings.redis_max_connections,
    )
    pubsub = subscriber.pubsub()
    await pubsub.subscribe(MARKET_STREAM_BATCH_CHANNEL)
    return BinaryMarketSubscription(subscriber, pubsub)

  async def state(self) -> MarketStreamState | None:
    redis = await self.redis()
    return MarketStreamState.from_bytes(await redis.get(MARKET_STREAM_STATE_KEY))

  async def engine_state(self) -> MarketStreamState | None:
    redis = await self.redis()
    return MarketStreamState.from_bytes(
      await redis.get(MARKET_STREAM_ENGINE_STATE_KEY)
    )

  async def write_engine_state(
    self,
    *,
    status: str,
    stream_id: str,
    sequence: int,
    captured_at: datetime | None,
    instrument_count: int,
    reason: str = "",
  ) -> None:
    state = MarketStreamState(
      status=status,
      stream_id=stream_id,
      sequence=sequence,
      captured_at=captured_at,
      updated_at=_utcnow(),
      instrument_count=instrument_count,
      reason=reason,
    )
    redis = await self.redis()
    await redis.set(MARKET_STREAM_ENGINE_STATE_KEY, state.to_bytes())

  async def mark_syncing(self, stream_id: str, *, reason: str = "") -> None:
    state = MarketStreamState(
      status="SYNCING",
      stream_id=stream_id,
      updated_at=_utcnow(),
      reason=reason,
    )
    redis = await self.redis()
    async with redis.pipeline(transaction=True) as pipeline:
      pipeline.delete(MARKET_STREAM_LATEST_KEY)
      pipeline.set(MARKET_STREAM_STATE_KEY, state.to_bytes())
      await pipeline.execute()

  async def write_batch(
    self,
    batch: MarketStreamBatch,
    payload: bytes,
  ) -> MarketStreamState:
    current = await self.state()
    if current is None or current.stream_id != batch.stream_id:
      raise ValueError("market stream is not active")
    expected = 1 if current.sequence == 0 else current.sequence + 1
    if batch.sequence != expected:
      raise ValueError(
        f"market stream sequence gap: expected={expected} actual={batch.sequence}"
      )
    if current.sequence == 0 and batch.kind is not MarketBatchKind.SNAPSHOT:
      raise ValueError("first market stream batch must be SNAPSHOT")

    state = MarketStreamState(
      status="READY",
      stream_id=batch.stream_id,
      sequence=batch.sequence,
      captured_at=batch.captured_at,
      updated_at=_utcnow(),
      instrument_count=(
        batch.instrument_count
        if batch.kind is MarketBatchKind.SNAPSHOT
        else max(current.instrument_count, batch.instrument_count)
      ),
    )
    encoded_ticks = {
      code.encode("utf-8"): orjson.dumps(tick)
      for code, tick in batch.data.items()
    }
    redis = await self.redis()
    async with redis.pipeline(transaction=True) as pipeline:
      if batch.kind is MarketBatchKind.SNAPSHOT:
        pipeline.delete(MARKET_STREAM_LATEST_KEY)
      if encoded_ticks:
        pipeline.hset(MARKET_STREAM_LATEST_KEY, mapping=encoded_ticks)
      pipeline.set(MARKET_STREAM_STATE_KEY, state.to_bytes())
      pipeline.publish(MARKET_STREAM_BATCH_CHANNEL, payload)
      await pipeline.execute()
    return state

  async def mark_offline(self, stream_id: str, *, reason: str) -> bool:
    current = await self.state()
    if current is None or current.stream_id != stream_id:
      return False
    offline = MarketStreamState(
      status="OFFLINE",
      stream_id=stream_id,
      sequence=current.sequence,
      captured_at=current.captured_at,
      updated_at=_utcnow(),
      instrument_count=current.instrument_count,
      reason=reason,
    )
    redis = await self.redis()
    await redis.set(MARKET_STREAM_STATE_KEY, offline.to_bytes())
    return True

  async def load_snapshot(
    self,
    *,
    attempts: int = 3,
  ) -> tuple[MarketStreamState, dict[str, dict]] | None:
    redis = await self.redis()
    for _ in range(max(1, attempts)):
      before = await self.state()
      if before is None or before.status != "READY":
        return None
      raw_ticks = await redis.hgetall(MARKET_STREAM_LATEST_KEY)
      after = await self.state()
      if (
        after is None
        or after.status != "READY"
        or before.stream_id != after.stream_id
        or before.sequence != after.sequence
      ):
        continue
      ticks = {
        code.decode("utf-8"): orjson.loads(tick)
        for code, tick in raw_ticks.items()
      }
      if after.instrument_count and len(ticks) != after.instrument_count:
        continue
      return after, ticks
    return None

  async def cleanup_legacy_whole_controls(self) -> int:
    redis = await self.redis()
    controls = await redis.hgetall(LEGACY_ACTIVE_SUBSCRIPTIONS_KEY)
    stale_fields: list[bytes] = []
    for field, payload in controls.items():
      try:
        control = orjson.loads(payload)
      except orjson.JSONDecodeError:
        continue
      if str(control.get("kind") or "").lower() == "whole":
        stale_fields.append(field)
    if stale_fields:
      await redis.hdel(LEGACY_ACTIVE_SUBSCRIPTIONS_KEY, *stale_fields)
    return len(stale_fields)

  async def close(self) -> None:
    if self._redis is not None:
      await self._redis.aclose()
      self._redis = None


market_stream_store = MarketStreamStore()


__all__ = [
  "MARKET_STREAM_BATCH_CHANNEL",
  "MARKET_STREAM_LATEST_KEY",
  "MARKET_STREAM_ENGINE_STATE_KEY",
  "MARKET_STREAM_STATE_KEY",
  "BinaryMarketSubscription",
  "MarketStreamState",
  "MarketStreamStore",
  "market_stream_store",
]
