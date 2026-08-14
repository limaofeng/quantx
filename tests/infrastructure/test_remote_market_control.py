import json
from datetime import date

import pytest
from quantx_infrastructure.core.data import remote_market_data


class FakeRedis:
  def __init__(self):
    self.values = {}

  async def hset(self, _name, key, value):
    self.values[key] = value

  async def hdel(self, _name, key):
    self.values.pop(key, None)


@pytest.mark.asyncio
async def test_market_control_is_cached_before_pubsub_wakeup(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  fake_redis = FakeRedis()
  published = []

  async def get_redis():
    return fake_redis

  async def publish(channel, payload):
    published.append((channel, payload))
    return 1

  monkeypatch.setattr(
    remote_market_data.redis_pubsub,
    "get_redis",
    get_redis,
  )
  monkeypatch.setattr(
    remote_market_data.redis_pubsub,
    "publish",
    publish,
  )
  subscribe = {
    "action": "SUBSCRIBE",
    "subscription_id": "sub-1",
    "kind": "quote",
    "stock_code": "600000.SH",
    "period": "tick",
  }
  await remote_market_data.RemoteMarketDataManager._publish_control(subscribe)

  assert json.loads(fake_redis.values["sub-1"]) == subscribe
  assert published == [
    (remote_market_data.MARKET_DATA_CONTROL_CHANNEL, subscribe)
  ]

  unsubscribe = {**subscribe, "action": "UNSUBSCRIBE"}
  await remote_market_data.RemoteMarketDataManager._publish_control(
    unsubscribe
  )
  assert fake_redis.values == {}


def test_one_minute_subscription_is_bounded_to_current_shanghai_day(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  manager = remote_market_data.RemoteMarketDataManager()
  captured = {}

  def capture(channel, callback, control):
    captured.update(
      {
        "channel": channel,
        "callback": callback,
        "control": control,
      }
    )
    return 7

  monkeypatch.setattr(manager, "_subscribe", capture)
  monkeypatch.setattr(
    remote_market_data.time_utils,
    "today",
    lambda: date(2026, 8, 13),
  )

  callback = lambda _data: None
  subscription_id = manager.subscribe_quote(
    "000001.SH",
    period="1m",
    count=-1,
    callback=callback,
  )

  assert subscription_id == 7
  assert captured == {
    "channel": "market-data:000001.SH:1m",
    "callback": callback,
    "control": {
      "kind": "quote",
      "stock_code": "000001.SH",
      "period": "1m",
      "count": -1,
      "start_time": "20260813000000",
      "end_time": "20260813235959",
    },
  }
