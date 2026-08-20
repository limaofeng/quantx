"""Redis cache and binary fan-out for the authoritative whole-quote stream."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from typing import AsyncIterator

import orjson
import redis.asyncio as aioredis
from quantx_contracts import (
  MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS,
  MarketBatchKind,
  MarketStreamBatch,
  market_tick_source_time,
  validate_market_stream_capture_time,
)

from quantx_infrastructure.config.settings import settings

logger = logging.getLogger(__name__)

MARKET_STREAM_BATCH_CHANNEL = "market-data:whole:v1:batches"
MARKET_STREAM_LATEST_KEY = "market-data:whole:v1:latest"
MARKET_STREAM_STATE_KEY = "market-data:whole:v1:state"
MARKET_STREAM_ENGINE_STATE_KEY = "market-data:whole:v1:engine-state"
MARKET_STREAM_GENERATION_KEY = "market-data:whole:v1:generation"
MARKET_STREAM_FRESHNESS_KEY = "market-data:whole:v1:freshness"
MARKET_STREAM_STAGING_PREFIX = "market-data:whole:v1:staging"
MARKET_STREAM_SNAPSHOT_CHUNK_SIZE = 512
MARKET_STREAM_STAGING_TTL_SECONDS = 60
MARKET_STREAM_FRESHNESS_TTL_MILLISECONDS = int(
  MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS * 1000
)
LEGACY_ACTIVE_SUBSCRIPTIONS_KEY = "market-data:active-subscriptions"

_MARKET_STREAM_MARK_OFFLINE_SCRIPT = """
-- quantx_market_mark_offline_v1
local raw = redis.call('GET', KEYS[1])
if not raw then
  return 0
end
local state = cjson.decode(raw)
if tostring(state['stream_id'] or '') ~= ARGV[1] then
  return 0
end
state['status'] = 'OFFLINE'
state['updated_at'] = ARGV[2]
state['reason'] = ARGV[3]
redis.call('DEL', KEYS[2])
redis.call('DEL', KEYS[3])
redis.call('SET', KEYS[1], cjson.encode(state))
return 1
"""

_MARKET_STREAM_ALLOCATE_GENERATION_SCRIPT = """
-- quantx_market_allocate_generation_v1
local counter = tonumber(redis.call('GET', KEYS[1]) or 0)
local raw = redis.call('GET', KEYS[2])
local current_generation = 0
if raw then
  local current = cjson.decode(raw)
  current_generation = tonumber(current['generation'] or 0)
end
local next_generation = math.max(counter, current_generation) + 1
redis.call('SET', KEYS[1], next_generation)
return next_generation
"""

_MARKET_STREAM_MARK_SYNCING_SCRIPT = """
-- quantx_market_mark_syncing_v1
local raw = redis.call('GET', KEYS[1])
local current_generation = 0
local current_stream_id = ''
if raw then
  local current = cjson.decode(raw)
  current_generation = tonumber(current['generation'] or 0)
  current_stream_id = tostring(current['stream_id'] or '')
end
local incoming_generation = tonumber(ARGV[2])
if incoming_generation <= current_generation then
  return 'GENERATION_STALE'
end
redis.call('DEL', KEYS[2])
redis.call('DEL', KEYS[3])
if current_stream_id ~= '' and current_stream_id ~= ARGV[1] then
  redis.call('DEL', ARGV[4] .. current_stream_id)
end
redis.call('SET', KEYS[1], ARGV[3])
return 'OK'
"""

_MARKET_STREAM_COMMIT_SNAPSHOT_SCRIPT = """
-- quantx_market_commit_snapshot_v1
local function reject(reason)
  redis.call('DEL', KEYS[2])
  return reason
end
local raw = redis.call('GET', KEYS[1])
if not raw then
  return reject('MISSING_STATE')
end
local current = cjson.decode(raw)
if tostring(current['stream_id'] or '') ~= ARGV[1] then
  return reject('STREAM_MISMATCH')
end
if tostring(current['status'] or '') ~= 'SYNCING' then
  return reject('STATUS_MISMATCH')
end
if tonumber(current['sequence'] or 0) ~= tonumber(ARGV[2]) then
  return reject('SEQUENCE_MISMATCH')
end
if redis.call('EXISTS', KEYS[2]) ~= 1 then
  return 'STAGING_MISSING'
end
redis.call('RENAME', KEYS[2], KEYS[3])
redis.call('PERSIST', KEYS[3])
redis.call('SET', KEYS[1], ARGV[3])
redis.call('PUBLISH', KEYS[4], ARGV[4])
redis.call('SET', KEYS[5], ARGV[5], 'PX', ARGV[6])
return 'OK'
"""

_MARKET_STREAM_COMMIT_DELTA_SCRIPT = """
-- quantx_market_commit_delta_v1
local raw = redis.call('GET', KEYS[1])
if not raw then
  return 'MISSING_STATE'
end
local current = cjson.decode(raw)
if tostring(current['stream_id'] or '') ~= ARGV[1] then
  return 'STREAM_MISMATCH'
end
local current_status = tostring(current['status'] or '')
local previous_sequence = tonumber(ARGV[2])
local incoming = cjson.decode(ARGV[3])
local incoming_status = tostring(incoming['status'] or '')
if current_status == 'SYNCING'
  and previous_sequence ~= 1
  and previous_sequence ~= 2 then
  return 'BARRIER_MISMATCH'
end
if current_status ~= 'SYNCING' and current_status ~= 'READY' then
  return 'STATUS_MISMATCH'
end
if tonumber(current['sequence'] or 0) ~= previous_sequence then
  return 'SEQUENCE_MISMATCH'
end
if current_status == 'SYNCING'
  and previous_sequence == 1
  and incoming_status ~= 'SYNCING' then
  return 'PHASE_MISMATCH'
end
if current_status == 'SYNCING'
  and previous_sequence == 2
  and incoming_status ~= 'READY' then
  return 'PHASE_MISMATCH'
end
if current_status == 'READY' and incoming_status ~= 'READY' then
  return 'PHASE_MISMATCH'
end
for index = 7, #ARGV, 2 do
  redis.call('HSET', KEYS[2], ARGV[index], ARGV[index + 1])
end
redis.call('SET', KEYS[1], ARGV[3])
redis.call('PUBLISH', KEYS[3], ARGV[4])
redis.call('SET', KEYS[4], ARGV[5], 'PX', ARGV[6])
return 'OK'
"""

_MARKET_STREAM_READ_STATE_FRESHNESS_SCRIPT = """
-- quantx_market_read_state_freshness_v1
local state = redis.call('GET', KEYS[1])
local freshness = redis.call('GET', KEYS[2])
if not state then state = false end
if not freshness then freshness = false end
return {state, freshness}
"""


def _utcnow() -> datetime:
  return datetime.now(timezone.utc)


def _staging_key(stream_id: str) -> str:
  return f"{MARKET_STREAM_STAGING_PREFIX}:{stream_id}"


def _tick_source_time(
  tick: dict,
  *,
  received_at: datetime | None = None,
) -> float:
  """Return a comparable broker source time or reject the untrusted tick."""
  return market_tick_source_time(tick, reference_at=received_at)


def _batch_source_times(
  batch: MarketStreamBatch,
  *,
  received_at: datetime | None = None,
) -> dict[str, float]:
  source_times: dict[str, float] = {}
  invalid_codes: list[str] = []
  invalid_reasons: list[str] = []
  for code, tick in batch.data.items():
    try:
      source_times[code] = _tick_source_time(
        tick,
        received_at=received_at,
      )
    except ValueError as exc:
      invalid_codes.append(code)
      invalid_reasons.append(str(exc))
  if invalid_codes:
    raise ValueError(
      "market stream batch contains tick without a valid source time: "
      f"stream_id={batch.stream_id} sequence={batch.sequence} "
      f"kind={batch.kind.value} invalid={len(invalid_codes)} "
      f"samples={','.join(invalid_codes[:5])} "
      f"reason={invalid_reasons[0]}"
    )
  return source_times


def _require_commit_success(result: object, *, kind: MarketBatchKind) -> None:
  decoded = (
    result.decode("utf-8", errors="replace")
    if isinstance(result, bytes)
    else str(result)
  )
  if decoded != "OK":
    raise ValueError(
      f"market stream Redis {kind.value} CAS rejected: {decoded}"
    )


def _result_text(result: object) -> str:
  return (
    result.decode("utf-8", errors="replace")
    if isinstance(result, bytes)
    else str(result)
  )


@dataclass(frozen=True)
class MarketStreamState:
  status: str
  stream_id: str = ""
  generation: int = 0
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
        "generation": self.generation,
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
      generation=int(raw.get("generation") or 0),
      sequence=int(raw.get("sequence") or 0),
      captured_at=parse_time(raw.get("captured_at")),
      updated_at=parse_time(raw.get("updated_at")),
      instrument_count=int(raw.get("instrument_count") or 0),
      reason=str(raw.get("reason") or ""),
    )


@dataclass(frozen=True)
class MarketStreamFreshnessLease:
  stream_id: str
  sequence: int

  def to_bytes(self) -> bytes:
    return orjson.dumps(
      {"stream_id": self.stream_id, "sequence": self.sequence}
    )

  @classmethod
  def from_bytes(
    cls,
    payload: bytes | None,
  ) -> "MarketStreamFreshnessLease | None":
    if not payload:
      return None
    raw = orjson.loads(payload)
    return cls(
      stream_id=str(raw.get("stream_id") or ""),
      sequence=int(raw.get("sequence") or 0),
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
    self._active_stream_id = ""
    self._active_generation = 0
    self._active_codes: frozenset[str] = frozenset()
    self._source_times: dict[str, float] = {}

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

  async def allocate_generation(self) -> int:
    redis = await self.redis()
    return int(
      await redis.eval(
        _MARKET_STREAM_ALLOCATE_GENERATION_SCRIPT,
        2,
        MARKET_STREAM_GENERATION_KEY,
        MARKET_STREAM_STATE_KEY,
      )
    )

  async def mark_syncing(
    self,
    stream_id: str,
    *,
    generation: int | None = None,
    reason: str = "",
  ) -> None:
    incoming_generation = (
      await self.allocate_generation() if generation is None else generation
    )
    if incoming_generation <= 0:
      raise ValueError("market stream generation must be positive")
    state = MarketStreamState(
      status="SYNCING",
      stream_id=stream_id,
      generation=incoming_generation,
      updated_at=_utcnow(),
      reason=reason,
    )
    redis = await self.redis()
    result = await redis.eval(
      _MARKET_STREAM_MARK_SYNCING_SCRIPT,
      3,
      MARKET_STREAM_STATE_KEY,
      _staging_key(stream_id),
      MARKET_STREAM_FRESHNESS_KEY,
      stream_id,
      str(incoming_generation),
      state.to_bytes(),
      f"{MARKET_STREAM_STAGING_PREFIX}:",
    )
    decoded = _result_text(result)
    if decoded != "OK":
      raise ValueError(f"market stream SYNCING CAS rejected: {decoded}")
    if incoming_generation > self._active_generation:
      self._active_stream_id = stream_id
      self._active_generation = incoming_generation
      self._active_codes = frozenset()
      self._source_times = {}

  async def write_batch(
    self,
    batch: MarketStreamBatch,
    payload: bytes,
    *,
    received_at: datetime | None = None,
  ) -> MarketStreamState:
    observed_at = received_at or _utcnow()
    # Future skew is judged against the instant the API received the frame;
    # queueing must not turn an invalid future timestamp into a valid one.
    validate_market_stream_capture_time(
      batch.captured_at,
      received_at=observed_at,
      max_age_seconds=None,
    )
    # Freshness is judged again at commit time so an ingress/commit backlog
    # cannot refresh Redis READY and its lease with an already-stale capture.
    validate_market_stream_capture_time(
      batch.captured_at,
      received_at=_utcnow(),
    )
    current = await self.state()
    if (
      current is None
      or current.stream_id != batch.stream_id
      or current.status not in {"SYNCING", "READY"}
    ):
      raise ValueError("market stream is not active")
    expected = 1 if current.sequence == 0 else current.sequence + 1
    if batch.sequence != expected:
      raise ValueError(
        f"market stream sequence gap: expected={expected} actual={batch.sequence}"
      )
    if current.sequence == 0 and batch.kind is not MarketBatchKind.SNAPSHOT:
      raise ValueError("first market stream batch must be SNAPSHOT")
    if current.sequence > 0 and batch.kind is MarketBatchKind.SNAPSHOT:
      raise ValueError("market stream SNAPSHOT is only valid as the first batch")
    if (
      batch.kind is MarketBatchKind.DELTA
      and not batch.data.keys() <= self._active_codes
    ):
      unknown = sorted(batch.data.keys() - self._active_codes)
      raise ValueError(
        "market stream DELTA contains instruments outside SNAPSHOT: "
        + ",".join(unknown[:5])
      )

    if batch.kind is MarketBatchKind.SNAPSHOT:
      next_status = "SYNCING"
    elif current.status == "SYNCING" and current.sequence == 1:
      # Sequence 2 is the pre-cut convergence barrier.  Committing and ACKing
      # it cannot make the API/Engine READY because the Agent has not yet
      # observed that ACK or switched to ordered callback capture.
      next_status = "SYNCING"
    elif current.status == "SYNCING" and current.sequence == 2:
      # Mandatory sequence 3 is the Agent's post-ACK readiness confirmation.
      # Its CAS is the one and only SYNCING -> READY transition.
      next_status = "READY"
    elif current.status == "READY" and current.sequence >= 3:
      next_status = "READY"
    else:
      raise ValueError(
        "market stream DELTA is invalid for current phase: "
        f"status={current.status} sequence={current.sequence}"
      )

    state = MarketStreamState(
      status=next_status,
      stream_id=batch.stream_id,
      generation=current.generation,
      sequence=batch.sequence,
      captured_at=batch.captured_at,
      updated_at=_utcnow(),
      instrument_count=(
        batch.instrument_count
        if batch.kind is MarketBatchKind.SNAPSHOT
        else current.instrument_count
      ),
    )
    state_payload = state.to_bytes()
    freshness_payload = MarketStreamFreshnessLease(
      stream_id=batch.stream_id,
      sequence=batch.sequence,
    ).to_bytes()
    redis = await self.redis()
    batch_source_times = _batch_source_times(
      batch,
      received_at=observed_at,
    )
    if batch.kind is MarketBatchKind.SNAPSHOT:
      staging_key = _staging_key(batch.stream_id)
      entries = iter(batch.data.items())
      while chunk := list(islice(entries, MARKET_STREAM_SNAPSHOT_CHUNK_SIZE)):
        encoded_ticks = {
          code.encode("utf-8"): orjson.dumps(tick) for code, tick in chunk
        }
        # Chunking bounds command size and event-loop stalls. Partial staging
        # data is never visible, and the authoritative state remains SYNCING
        # after RENAME until the sequence-2 continuity barrier commits.
        async with redis.pipeline(transaction=False) as pipeline:
          pipeline.hset(staging_key, mapping=encoded_ticks)
          pipeline.expire(staging_key, MARKET_STREAM_STAGING_TTL_SECONDS)
          await pipeline.execute()
      result = await redis.eval(
        _MARKET_STREAM_COMMIT_SNAPSHOT_SCRIPT,
        5,
        MARKET_STREAM_STATE_KEY,
        staging_key,
        MARKET_STREAM_LATEST_KEY,
        MARKET_STREAM_BATCH_CHANNEL,
        MARKET_STREAM_FRESHNESS_KEY,
        batch.stream_id,
        str(batch.sequence - 1),
        state_payload,
        payload,
        freshness_payload,
        str(MARKET_STREAM_FRESHNESS_TTL_MILLISECONDS),
      )
      _require_commit_success(result, kind=batch.kind)
      if self._active_stream_id == batch.stream_id:
        self._active_codes = frozenset(batch.data)
        self._source_times = batch_source_times
    else:
      accepted: dict[str, dict] = {}
      accepted_source_times: dict[str, float] = {}
      for code, tick in batch.data.items():
        source_time = batch_source_times[code]
        previous = self._source_times.get(code)
        if previous is not None and source_time < previous:
          continue
        accepted[code] = tick
        accepted_source_times[code] = source_time
      encoded_ticks = {
        code.encode("utf-8"): orjson.dumps(tick)
        for code, tick in accepted.items()
      }
      tick_arguments: list[bytes] = []
      for code, tick in encoded_ticks.items():
        tick_arguments.extend((code, tick))
      result = await redis.eval(
        _MARKET_STREAM_COMMIT_DELTA_SCRIPT,
        4,
        MARKET_STREAM_STATE_KEY,
        MARKET_STREAM_LATEST_KEY,
        MARKET_STREAM_BATCH_CHANNEL,
        MARKET_STREAM_FRESHNESS_KEY,
        batch.stream_id,
        str(batch.sequence - 1),
        state_payload,
        payload,
        freshness_payload,
        str(MARKET_STREAM_FRESHNESS_TTL_MILLISECONDS),
        *tick_arguments,
      )
      _require_commit_success(result, kind=batch.kind)
      if self._active_stream_id == batch.stream_id:
        self._source_times.update(accepted_source_times)
    return state

  async def mark_offline(self, stream_id: str, *, reason: str) -> bool:
    redis = await self.redis()
    changed = bool(
      await redis.eval(
        _MARKET_STREAM_MARK_OFFLINE_SCRIPT,
        3,
        MARKET_STREAM_STATE_KEY,
        _staging_key(stream_id),
        MARKET_STREAM_FRESHNESS_KEY,
        stream_id,
        _utcnow().isoformat(),
        reason,
      )
    )
    # The old handler releases the process connection lease before this
    # best-effort cleanup. A newer stream can therefore become active between
    # the atomic Redis CAS and this coroutine resuming; never clear that newer
    # stream's in-process code/source watermarks.
    if changed and self._active_stream_id == stream_id:
      self._active_stream_id = ""
      self._active_generation = 0
      self._active_codes = frozenset()
      self._source_times = {}
    return changed

  async def state_with_freshness(
    self,
  ) -> tuple[MarketStreamState | None, MarketStreamFreshnessLease | None]:
    redis = await self.redis()
    result = await redis.eval(
      _MARKET_STREAM_READ_STATE_FRESHNESS_SCRIPT,
      2,
      MARKET_STREAM_STATE_KEY,
      MARKET_STREAM_FRESHNESS_KEY,
    )
    if not isinstance(result, (list, tuple)) or len(result) != 2:
      raise ValueError("invalid market stream state/freshness Redis result")
    return (
      MarketStreamState.from_bytes(result[0]),
      MarketStreamFreshnessLease.from_bytes(result[1]),
    )

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
  "MARKET_STREAM_FRESHNESS_KEY",
  "MARKET_STREAM_FRESHNESS_TTL_MILLISECONDS",
  "MARKET_STREAM_LATEST_KEY",
  "MARKET_STREAM_ENGINE_STATE_KEY",
  "MARKET_STREAM_STATE_KEY",
  "MARKET_STREAM_STAGING_PREFIX",
  "BinaryMarketSubscription",
  "MarketStreamFreshnessLease",
  "MarketStreamState",
  "MarketStreamStore",
  "market_stream_store",
]
