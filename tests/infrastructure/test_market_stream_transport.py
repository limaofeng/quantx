from datetime import datetime, timezone

import pytest
from quantx_contracts import MarketBatchKind, MarketStreamBatch
from quantx_infrastructure.core.data.market_stream_transport import (
  MARKET_STREAM_BATCH_CHANNEL,
  MARKET_STREAM_LATEST_KEY,
  MarketStreamStore,
)


class FakePipeline:
  def __init__(self, redis, *, fail=False):
    self.redis = redis
    self.operations = []
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

  def publish(self, channel, payload):
    self.operations.append(("publish", channel, payload))
    return self

  async def execute(self):
    if self.fail:
      raise ConnectionError("redis unavailable")
    for operation in self.operations:
      if operation[0] == "delete":
        self.redis.hashes.pop(operation[1], None)
      elif operation[0] == "set":
        self.redis.values[operation[1]] = operation[2]
      elif operation[0] == "hset":
        self.redis.hashes.setdefault(operation[1], {}).update(operation[2])
      elif operation[0] == "publish":
        self.redis.published.append((operation[1], operation[2]))
    return [True] * len(self.operations)


class FakeRedis:
  def __init__(self):
    self.values = {}
    self.hashes = {}
    self.published = []
    self.fail_pipeline = False

  async def get(self, key):
    return self.values.get(key)

  async def set(self, key, value):
    self.values[key] = value

  async def hgetall(self, key):
    return dict(self.hashes.get(key, {}))

  def pipeline(self, *, transaction):
    assert transaction is True
    return FakePipeline(self, fail=self.fail_pipeline)


def make_batch(sequence, kind, data):
  return MarketStreamBatch(
    stream_id="stream-1",
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

  state, latest = await store.load_snapshot()
  assert state.sequence == 1
  assert latest["600000.SH"]["lastPrice"] == 10.0
  assert redis.published == [
    (MARKET_STREAM_BATCH_CHANNEL, snapshot.to_bytes())
  ]
  assert len(redis.hashes[MARKET_STREAM_LATEST_KEY]) == 1

  gap = make_batch(3, MarketBatchKind.DELTA, {"600000.SH": {}})
  with pytest.raises(ValueError, match="sequence gap"):
    await store.write_batch(gap, gap.to_bytes())


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
