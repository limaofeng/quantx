from datetime import datetime, timezone

import pytest
from quantx_contracts import MarketBatchKind, MarketStreamBatch
from quantx_infrastructure.core.data import market_stream_transport
from quantx_infrastructure.core.data.market_stream_transport import (
  MARKET_STREAM_BATCH_CHANNEL,
  MARKET_STREAM_FRESHNESS_KEY,
  MARKET_STREAM_FRESHNESS_TTL_MILLISECONDS,
  MARKET_STREAM_LATEST_KEY,
  MARKET_STREAM_SNAPSHOT_CHUNK_SIZE,
  MARKET_STREAM_STAGING_PREFIX,
  MarketStreamFreshnessLease,
  MarketStreamState,
  MarketStreamStore,
)


class FakePipeline:
  def __init__(self, redis, *, transaction, fail=False):
    self.redis = redis
    self.operations = []
    self.transaction = transaction
    self.fail = fail

  async def __aenter__(self):
    return self

  async def __aexit__(self, *_args):
    return None

  def delete(self, key):
    self.operations.append(("delete", key))
    return self

  def set(self, key, value):
    self.operations.append(("set", key, value))
    return self

  def hset(self, key, *, mapping):
    self.operations.append(("hset", key, mapping))
    return self

  def expire(self, key, seconds):
    self.operations.append(("expire", key, seconds))
    return self

  def rename(self, source, destination):
    self.operations.append(("rename", source, destination))
    return self

  def publish(self, channel, payload):
    self.operations.append(("publish", channel, payload))
    return self

  async def execute(self):
    if self.fail:
      raise ConnectionError("redis unavailable")
    self.redis.pipeline_modes.append(self.transaction)
    for operation in self.operations:
      if operation[0] == "delete":
        self.redis.hashes.pop(operation[1], None)
        self.redis.expirations.pop(operation[1], None)
      elif operation[0] == "set":
        self.redis.values[operation[1]] = operation[2]
      elif operation[0] == "hset":
        self.redis.hashes.setdefault(operation[1], {}).update(operation[2])
      elif operation[0] == "expire":
        self.redis.expirations[operation[1]] = operation[2]
      elif operation[0] == "rename":
        self.redis.hashes[operation[2]] = self.redis.hashes.pop(operation[1])
      elif operation[0] == "publish":
        self.redis.published.append((operation[1], operation[2]))
    return [True] * len(self.operations)


class FakeRedis:
  def __init__(self):
    self.values = {}
    self.hashes = {}
    self.expirations = {}
    self.published = []
    self.pipeline_modes = []
    self.fail_pipeline = False
    self.before_eval = None

  async def get(self, key):
    return self.values.get(key)

  async def set(self, key, value):
    self.values[key] = value

  async def hgetall(self, key):
    return dict(self.hashes.get(key, {}))

  async def eval(self, script, numkeys, *args):
    if self.before_eval is not None:
      callback = self.before_eval
      self.before_eval = None
      await callback()

    if "quantx_market_allocate_generation_v1" in script:
      assert numkeys == 2
      generation_key, state_key = args
      raw_counter = self.values.get(generation_key, 0)
      if isinstance(raw_counter, bytes):
        raw_counter = raw_counter.decode("utf-8")
      counter = int(raw_counter)
      current = MarketStreamState.from_bytes(self.values.get(state_key))
      current_generation = current.generation if current is not None else 0
      generation = max(counter, current_generation) + 1
      self.values[generation_key] = str(generation).encode("utf-8")
      return generation

    if "quantx_market_read_state_freshness_v1" in script:
      assert numkeys == 2
      state_key, freshness_key = args
      return [self.values.get(state_key), self.values.get(freshness_key)]

    if "quantx_market_mark_syncing_v1" in script:
      assert numkeys == 3
      (
        state_key,
        target_staging_key,
        freshness_key,
        stream_id,
        generation,
        state_payload,
        staging_prefix,
      ) = args
      current = MarketStreamState.from_bytes(self.values.get(state_key))
      if current is not None and int(generation) <= current.generation:
        return b"GENERATION_STALE"
      self.hashes.pop(target_staging_key, None)
      self.expirations.pop(target_staging_key, None)
      self.values.pop(freshness_key, None)
      self.expirations.pop(freshness_key, None)
      if current is not None and current.stream_id != stream_id:
        current_staging_key = f"{staging_prefix}{current.stream_id}"
        self.hashes.pop(current_staging_key, None)
        self.expirations.pop(current_staging_key, None)
      self.values[state_key] = state_payload
      return b"OK"

    if "quantx_market_commit_snapshot_v1" in script:
      assert numkeys == 5
      (
        state_key,
        staging_key,
        latest_key,
        channel,
        freshness_key,
        stream_id,
        previous_sequence,
        state_payload,
        payload,
        freshness_payload,
        freshness_ttl,
      ) = args
      current = MarketStreamState.from_bytes(self.values.get(state_key))
      if current is None:
        self.hashes.pop(staging_key, None)
        self.expirations.pop(staging_key, None)
        return b"MISSING_STATE"
      if current.stream_id != stream_id:
        self.hashes.pop(staging_key, None)
        self.expirations.pop(staging_key, None)
        return b"STREAM_MISMATCH"
      if current.status != "SYNCING":
        self.hashes.pop(staging_key, None)
        self.expirations.pop(staging_key, None)
        return b"STATUS_MISMATCH"
      if current.sequence != int(previous_sequence):
        self.hashes.pop(staging_key, None)
        self.expirations.pop(staging_key, None)
        return b"SEQUENCE_MISMATCH"
      if staging_key not in self.hashes:
        return b"STAGING_MISSING"
      self.hashes[latest_key] = self.hashes.pop(staging_key)
      self.expirations.pop(staging_key, None)
      self.expirations.pop(latest_key, None)
      self.values[state_key] = state_payload
      self.published.append((channel, payload))
      self.values[freshness_key] = freshness_payload
      self.expirations[freshness_key] = int(freshness_ttl)
      return b"OK"

    if "quantx_market_commit_delta_v1" in script:
      assert numkeys == 4
      state_key, latest_key, channel, freshness_key = args[:4]
      (
        stream_id,
        previous_sequence,
        state_payload,
        payload,
        freshness_payload,
        freshness_ttl,
      ) = args[4:10]
      tick_arguments = args[10:]
      current = MarketStreamState.from_bytes(self.values.get(state_key))
      if current is None:
        return b"MISSING_STATE"
      if current.stream_id != stream_id:
        return b"STREAM_MISMATCH"
      if current.status == "SYNCING" and int(previous_sequence) != 1:
        return b"BARRIER_MISMATCH"
      if current.status not in {"SYNCING", "READY"}:
        return b"STATUS_MISMATCH"
      if current.sequence != int(previous_sequence):
        return b"SEQUENCE_MISMATCH"
      assert len(tick_arguments) % 2 == 0
      latest = self.hashes.setdefault(latest_key, {})
      for index in range(0, len(tick_arguments), 2):
        latest[tick_arguments[index]] = tick_arguments[index + 1]
      self.values[state_key] = state_payload
      self.published.append((channel, payload))
      self.values[freshness_key] = freshness_payload
      self.expirations[freshness_key] = int(freshness_ttl)
      return b"OK"

    assert "quantx_market_mark_offline_v1" in script
    assert numkeys == 3
    (
      state_key,
      staging_key,
      freshness_key,
      stream_id,
      updated_at,
      reason,
    ) = args
    current = MarketStreamState.from_bytes(self.values.get(state_key))
    if current is None or current.stream_id != stream_id:
      return 0
    self.hashes.pop(staging_key, None)
    self.expirations.pop(staging_key, None)
    self.values.pop(freshness_key, None)
    self.expirations.pop(freshness_key, None)
    self.values[state_key] = MarketStreamState(
      status="OFFLINE",
      stream_id=current.stream_id,
      generation=current.generation,
      sequence=current.sequence,
      captured_at=current.captured_at,
      updated_at=datetime.fromisoformat(updated_at),
      instrument_count=current.instrument_count,
      reason=reason,
    ).to_bytes()
    return 1

  def pipeline(self, *, transaction):
    return FakePipeline(
      self,
      transaction=transaction,
      fail=self.fail_pipeline,
    )


def make_batch(sequence, kind, data, *, stream_id="stream-1"):
  return MarketStreamBatch(
    stream_id=stream_id,
    sequence=sequence,
    kind=kind,
    captured_at=datetime.now(timezone.utc),
    instrument_count=len(data),
    data=data,
  )


@pytest.mark.asyncio
async def test_store_requires_snapshot_then_commits_before_binary_publish() -> None:
  redis = FakeRedis()
  store = MarketStreamStore(redis)
  await store.mark_syncing("stream-1")

  delta = make_batch(1, MarketBatchKind.DELTA, {"600000.SH": {}})
  with pytest.raises(ValueError, match="first.*SNAPSHOT"):
    await store.write_batch(delta, delta.to_bytes())

  snapshot = make_batch(
    1,
    MarketBatchKind.SNAPSHOT,
    {"600000.SH": {"lastPrice": 10.0}},
  )
  await store.write_batch(snapshot, snapshot.to_bytes())

  snapshot_state, snapshot_freshness = await store.state_with_freshness()
  assert snapshot_state.status == "SYNCING"
  assert snapshot_state.sequence == 1
  assert snapshot_freshness == MarketStreamFreshnessLease(
    stream_id="stream-1",
    sequence=1,
  )
  assert await store.load_snapshot() is None
  assert redis.published == [
    (MARKET_STREAM_BATCH_CHANNEL, snapshot.to_bytes())
  ]
  assert len(redis.hashes[MARKET_STREAM_LATEST_KEY]) == 1
  assert (
    redis.expirations[MARKET_STREAM_FRESHNESS_KEY]
    == MARKET_STREAM_FRESHNESS_TTL_MILLISECONDS
  )

  gap = make_batch(3, MarketBatchKind.DELTA, {"600000.SH": {}})
  with pytest.raises(ValueError, match="sequence gap"):
    await store.write_batch(gap, gap.to_bytes())

  barrier = make_batch(2, MarketBatchKind.DELTA, {})
  barrier_state = await store.write_batch(barrier, barrier.to_bytes())
  assert barrier_state.status == "READY"
  assert barrier_state.sequence == 2

  update = make_batch(
    3,
    MarketBatchKind.DELTA,
    {"600000.SH": {"lastPrice": 10.2}},
  )
  await store.write_batch(update, update.to_bytes())
  loaded = await store.load_snapshot()
  assert loaded is not None
  state, latest = loaded
  assert state.status == "READY"
  assert state.sequence == 3
  assert latest["600000.SH"]["lastPrice"] == 10.2
  _, freshness = await store.state_with_freshness()
  assert freshness == MarketStreamFreshnessLease(
    stream_id="stream-1",
    sequence=3,
  )
  assert redis.published == [
    (MARKET_STREAM_BATCH_CHANNEL, snapshot.to_bytes()),
    (MARKET_STREAM_BATCH_CHANNEL, barrier.to_bytes()),
    (MARKET_STREAM_BATCH_CHANNEL, update.to_bytes()),
  ]


@pytest.mark.asyncio
async def test_snapshot_disconnect_never_becomes_ready() -> None:
  redis = FakeRedis()
  store = MarketStreamStore(redis)
  await store.mark_syncing("stream-1")
  snapshot = make_batch(
    1,
    MarketBatchKind.SNAPSHOT,
    {"600000.SH": {"lastPrice": 10.0}},
  )
  committed = await store.write_batch(snapshot, snapshot.to_bytes())
  assert committed.status == "SYNCING"
  assert committed.sequence == 1

  assert await store.mark_offline(
    "stream-1",
    reason="disconnected before continuity barrier",
  )

  state, freshness = await store.state_with_freshness()
  assert state.status == "OFFLINE"
  assert state.sequence == 1
  assert freshness is None
  assert MARKET_STREAM_FRESHNESS_KEY not in redis.expirations
  assert await store.load_snapshot() is None


@pytest.mark.asyncio
async def test_snapshot_is_chunked_in_staging_then_atomically_replaced() -> None:
  redis = FakeRedis()
  redis.hashes[MARKET_STREAM_LATEST_KEY] = {
    b"old.SH": b'{"lastPrice":1.0}'
  }
  store = MarketStreamStore(redis)
  await store.mark_syncing("stream-1")

  # SYNCING invalidates the watermark but keeps the last committed hash until
  # the replacement is complete.
  assert b"old.SH" in redis.hashes[MARKET_STREAM_LATEST_KEY]
  data = {
    f"{index:06d}.SZ": {"lastPrice": float(index)}
    for index in range(MARKET_STREAM_SNAPSHOT_CHUNK_SIZE + 1)
  }
  snapshot = make_batch(1, MarketBatchKind.SNAPSHOT, data)
  await store.write_batch(snapshot, snapshot.to_bytes())

  assert len(redis.hashes[MARKET_STREAM_LATEST_KEY]) == len(data)
  assert not any(
    str(key).startswith(MARKET_STREAM_STAGING_PREFIX)
    for key in redis.hashes
  )
  assert redis.pipeline_modes[-2:] == [False, False]


@pytest.mark.asyncio
async def test_delta_rejects_code_outside_committed_snapshot() -> None:
  redis = FakeRedis()
  store = MarketStreamStore(redis)
  await store.mark_syncing("stream-1")
  snapshot = make_batch(
    1,
    MarketBatchKind.SNAPSHOT,
    {"600000.SH": {"lastPrice": 10.0}},
  )
  await store.write_batch(snapshot, snapshot.to_bytes())
  published_before = list(redis.published)

  unknown = make_batch(
    2,
    MarketBatchKind.DELTA,
    {"000001.SZ": {"lastPrice": 12.0}},
  )
  with pytest.raises(ValueError, match="outside SNAPSHOT"):
    await store.write_batch(unknown, unknown.to_bytes())

  assert redis.published == published_before
  assert (await store.state()).sequence == 1
  assert b"000001.SZ" not in redis.hashes[MARKET_STREAM_LATEST_KEY]


@pytest.mark.asyncio
async def test_snapshot_plus_known_delta_can_be_loaded_after_store_restart() -> None:
  redis = FakeRedis()
  store = MarketStreamStore(redis)
  await store.mark_syncing("stream-1")
  snapshot = make_batch(
    1,
    MarketBatchKind.SNAPSHOT,
    {
      "000001.SZ": {"lastPrice": 11.0},
      "600000.SH": {"lastPrice": 10.0},
    },
  )
  await store.write_batch(snapshot, snapshot.to_bytes())
  delta = make_batch(
    2,
    MarketBatchKind.DELTA,
    {"600000.SH": {"lastPrice": 10.2}},
  )
  await store.write_batch(delta, delta.to_bytes())

  restarted_store = MarketStreamStore(redis)
  state, latest = await restarted_store.load_snapshot()
  assert state.sequence == 2
  assert state.instrument_count == 2
  assert len(latest) == 2
  assert latest["600000.SH"]["lastPrice"] == 10.2


def test_store_source_time_matches_hub_time_timetag_and_capture_fallback() -> None:
  captured_at = datetime(2026, 8, 19, 2, 0, tzinfo=timezone.utc)
  assert market_stream_transport._tick_source_time(
    {"time": 1_700_000_000_000},
    captured_at,
  ) == 1_700_000_000
  assert market_stream_transport._tick_source_time(
    {"timetag": "20260819 09:30:00"},
    captured_at,
  ) == datetime(2026, 8, 19, 1, 30, tzinfo=timezone.utc).timestamp()
  assert market_stream_transport._tick_source_time({}, captured_at) == (
    captured_at.timestamp()
  )


@pytest.mark.asyncio
async def test_older_delta_advances_sequence_without_regressing_cache() -> None:
  redis = FakeRedis()
  store = MarketStreamStore(redis)
  await store.mark_syncing("stream-1")
  snapshot = make_batch(
    1,
    MarketBatchKind.SNAPSHOT,
    {"600000.SH": {"lastPrice": 10.0, "time": 100}},
  )
  await store.write_batch(snapshot, snapshot.to_bytes())
  older = make_batch(
    2,
    MarketBatchKind.DELTA,
    {"600000.SH": {"lastPrice": 9.0, "time": 90}},
  )
  await store.write_batch(older, older.to_bytes())

  restarted_store = MarketStreamStore(redis)
  state, latest = await restarted_store.load_snapshot()
  assert state.sequence == 2
  assert latest["600000.SH"] == {"lastPrice": 10.0, "time": 100}
  assert redis.published[-1] == (MARKET_STREAM_BATCH_CHANNEL, older.to_bytes())


@pytest.mark.asyncio
async def test_mixed_delta_only_updates_ticks_with_non_regressing_source_time() -> None:
  redis = FakeRedis()
  store = MarketStreamStore(redis)
  await store.mark_syncing("stream-1")
  snapshot = make_batch(
    1,
    MarketBatchKind.SNAPSHOT,
    {
      "000001.SZ": {"lastPrice": 11.0, "time": 100},
      "600000.SH": {
        "lastPrice": 10.0,
        "timetag": "20260819 09:30:00",
      },
    },
  )
  await store.write_batch(snapshot, snapshot.to_bytes())
  mixed = make_batch(
    2,
    MarketBatchKind.DELTA,
    {
      "000001.SZ": {"lastPrice": 9.0, "time": 90},
      "600000.SH": {
        "lastPrice": 10.2,
        "timetag": "20260819 09:31:00",
      },
    },
  )
  await store.write_batch(mixed, mixed.to_bytes())

  state, latest = await MarketStreamStore(redis).load_snapshot()
  assert state.sequence == 2
  assert latest["000001.SZ"]["lastPrice"] == 11.0
  assert latest["600000.SH"]["lastPrice"] == 10.2


@pytest.mark.asyncio
async def test_old_stream_offline_cannot_overwrite_new_stream_syncing() -> None:
  redis = FakeRedis()
  store = MarketStreamStore(redis)
  await store.mark_syncing("old-stream")
  await store.mark_syncing("new-stream")

  assert await store.mark_offline("old-stream", reason="old disconnected") is False
  state = await store.state()
  assert state.status == "SYNCING"
  assert state.stream_id == "new-stream"


@pytest.mark.asyncio
async def test_late_old_mark_syncing_cannot_overwrite_new_ready_generation() -> None:
  redis = FakeRedis()
  store = MarketStreamStore(redis)
  old_generation = await store.allocate_generation()
  new_generation = await store.allocate_generation()
  assert new_generation > old_generation

  await store.mark_syncing("new-stream", generation=new_generation)
  snapshot = make_batch(
    1,
    MarketBatchKind.SNAPSHOT,
    {"600000.SH": {"lastPrice": 10.0, "time": 100}},
    stream_id="new-stream",
  )
  await store.write_batch(snapshot, snapshot.to_bytes())
  barrier = make_batch(
    2,
    MarketBatchKind.DELTA,
    {},
    stream_id="new-stream",
  )
  await store.write_batch(barrier, barrier.to_bytes())
  new_staging = f"{MARKET_STREAM_STAGING_PREFIX}:new-stream"
  old_staging = f"{MARKET_STREAM_STAGING_PREFIX}:old-stream"
  redis.hashes[new_staging] = {b"new": b"staging"}
  redis.hashes[old_staging] = {b"old": b"staging"}
  redis.expirations[new_staging] = 60
  redis.expirations[old_staging] = 60
  latest_before = dict(redis.hashes[MARKET_STREAM_LATEST_KEY])
  published_before = list(redis.published)

  with pytest.raises(ValueError, match="SYNCING CAS rejected: GENERATION_STALE"):
    await store.mark_syncing("old-stream", generation=old_generation)

  state = await store.state()
  assert state.status == "READY"
  assert state.stream_id == "new-stream"
  assert state.generation == new_generation
  assert state.sequence == 2
  assert redis.hashes[MARKET_STREAM_LATEST_KEY] == latest_before
  assert redis.hashes[new_staging] == {b"new": b"staging"}
  assert redis.hashes[old_staging] == {b"old": b"staging"}
  assert redis.published == published_before
  assert store._active_stream_id == "new-stream"


@pytest.mark.asyncio
async def test_stale_snapshot_writer_cannot_replace_new_stream_state_or_hash() -> None:
  redis = FakeRedis()
  redis.hashes[MARKET_STREAM_LATEST_KEY] = {
    b"seed.SH": b'{"lastPrice":1.0}'
  }
  store = MarketStreamStore(redis)
  await store.mark_syncing("old-stream")

  async def activate_new_stream() -> None:
    await store.mark_syncing("new-stream")

  redis.before_eval = activate_new_stream
  stale_snapshot = make_batch(
    1,
    MarketBatchKind.SNAPSHOT,
    {"600000.SH": {"lastPrice": 10.0, "time": 100}},
    stream_id="old-stream",
  )
  with pytest.raises(ValueError, match="CAS rejected: STREAM_MISMATCH"):
    await store.write_batch(stale_snapshot, stale_snapshot.to_bytes())

  state = await store.state()
  assert state.status == "SYNCING"
  assert state.stream_id == "new-stream"
  assert redis.hashes[MARKET_STREAM_LATEST_KEY] == {
    b"seed.SH": b'{"lastPrice":1.0}'
  }
  old_staging = f"{MARKET_STREAM_STAGING_PREFIX}:old-stream"
  assert old_staging not in redis.hashes
  assert old_staging not in redis.expirations
  assert redis.published == []
  assert store._active_stream_id == "new-stream"


@pytest.mark.asyncio
async def test_stale_delta_writer_cannot_modify_new_stream_state_hash_or_publish() -> None:
  redis = FakeRedis()
  store = MarketStreamStore(redis)
  await store.mark_syncing("old-stream")
  snapshot = make_batch(
    1,
    MarketBatchKind.SNAPSHOT,
    {"600000.SH": {"lastPrice": 10.0, "time": 100}},
    stream_id="old-stream",
  )
  await store.write_batch(snapshot, snapshot.to_bytes())
  latest_before = dict(redis.hashes[MARKET_STREAM_LATEST_KEY])
  published_before = list(redis.published)

  async def activate_new_stream() -> None:
    await store.mark_syncing("new-stream")

  redis.before_eval = activate_new_stream
  stale_delta = make_batch(
    2,
    MarketBatchKind.DELTA,
    {"600000.SH": {"lastPrice": 10.2, "time": 110}},
    stream_id="old-stream",
  )
  with pytest.raises(ValueError, match="CAS rejected: STREAM_MISMATCH"):
    await store.write_batch(stale_delta, stale_delta.to_bytes())

  state = await store.state()
  assert state.status == "SYNCING"
  assert state.stream_id == "new-stream"
  assert redis.hashes[MARKET_STREAM_LATEST_KEY] == latest_before
  assert redis.published == published_before
  assert store._active_stream_id == "new-stream"
  assert store._active_codes == frozenset()
  assert store._source_times == {}


@pytest.mark.asyncio
async def test_delta_cas_rejects_writer_with_stale_previous_sequence() -> None:
  redis = FakeRedis()
  store = MarketStreamStore(redis)
  await store.mark_syncing("stream-1")
  snapshot = make_batch(
    1,
    MarketBatchKind.SNAPSHOT,
    {"600000.SH": {"lastPrice": 10.0, "time": 100}},
  )
  await store.write_batch(snapshot, snapshot.to_bytes())

  competing = make_batch(
    2,
    MarketBatchKind.DELTA,
    {"600000.SH": {"lastPrice": 10.1, "time": 105}},
  )

  async def commit_competing_delta() -> None:
    await store.write_batch(competing, competing.to_bytes())

  redis.before_eval = commit_competing_delta
  stale = make_batch(
    2,
    MarketBatchKind.DELTA,
    {"600000.SH": {"lastPrice": 10.2, "time": 110}},
  )
  with pytest.raises(ValueError, match="CAS rejected: SEQUENCE_MISMATCH"):
    await store.write_batch(stale, stale.to_bytes())

  state, latest = await MarketStreamStore(redis).load_snapshot()
  assert state.sequence == 2
  assert latest["600000.SH"]["lastPrice"] == 10.1
  assert redis.published[-1] == (MARKET_STREAM_BATCH_CHANNEL, competing.to_bytes())


@pytest.mark.asyncio
async def test_store_redis_failure_prevents_publish_and_ready_state() -> None:
  redis = FakeRedis()
  store = MarketStreamStore(redis)
  await store.mark_syncing("stream-1")
  redis.fail_pipeline = True
  snapshot = make_batch(
    1,
    MarketBatchKind.SNAPSHOT,
    {"600000.SH": {"lastPrice": 10.0}},
  )

  with pytest.raises(ConnectionError, match="redis unavailable"):
    await store.write_batch(snapshot, snapshot.to_bytes())

  assert redis.published == []
  assert (await store.state()).status == "SYNCING"


@pytest.mark.asyncio
async def test_store_url_connection_preserves_separate_redis_password(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  captured = {}
  expected_client = object()

  def from_url(url, **kwargs):
    captured["url"] = url
    captured.update(kwargs)
    return expected_client

  monkeypatch.setattr(
    "quantx_infrastructure.core.data.market_stream_transport.settings.redis_url",
    "redis://192.168.101.4:30179/0",
  )
  monkeypatch.setattr(
    "quantx_infrastructure.core.data.market_stream_transport.settings.redis_password",
    "configured-secret",
  )
  monkeypatch.setattr(
    "quantx_infrastructure.core.data.market_stream_transport.aioredis.Redis.from_url",
    from_url,
  )

  client = await MarketStreamStore().redis()

  assert client is expected_client
  assert captured["url"] == "redis://192.168.101.4:30179/0"
  assert captured["password"] == "configured-secret"
