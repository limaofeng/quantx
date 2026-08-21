import json

import pytest
from quantx_api import agent_api, agent_hub
from quantx_contracts import AgentMessageType


class FakeRedis:
  def __init__(self, active=None):
    self.active = active or {}
    self.values = {}

  async def hgetall(self, _key):
    return {
      key: json.dumps(value)
      for key, value in self.active.items()
    }

  async def set(self, key, value, **_kwargs):
    self.values[key] = value

  async def get(self, key):
    return self.values.get(key)

  async def delete(self, key):
    self.values.pop(key, None)


@pytest.mark.asyncio
async def test_hub_replays_active_subscriptions_to_one_market_agent(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  active = {
    "sub-1": {
      "action": "SUBSCRIBE",
      "subscription_id": "sub-1",
      "kind": "quote",
      "stock_code": "600000.SH",
      "period": "tick",
    },
    "obsolete-whole": {
      "action": "SUBSCRIBE",
      "subscription_id": "obsolete-whole",
      "kind": "whole",
      "stock_codes": ["SH", "SZ"],
      "period": "tick",
    },
  }
  fake_redis = FakeRedis(active)

  async def get_redis():
    return fake_redis

  monkeypatch.setattr(agent_hub.redis_pubsub, "get_redis", get_redis)
  hub = agent_hub.AgentConnectionHub()
  first = await hub.register("device-1", {"market-data"})
  standby = await hub.register("device-2", {"market-data"})

  first_messages = [first.get_nowait(), first.get_nowait()]
  assert [item.message_type for item in first_messages] == [
    AgentMessageType.MARKET_RESET,
    AgentMessageType.MARKET_SUBSCRIBE,
  ]
  assert standby.get_nowait().message_type is AgentMessageType.MARKET_RESET
  assert await hub.is_market_device("device-1")
  assert not await hub.is_market_device("device-2")

  await hub.unregister("device-1", first)
  failover = [standby.get_nowait(), standby.get_nowait()]
  assert [item.message_type for item in failover] == [
    AgentMessageType.MARKET_RESET,
    AgentMessageType.MARKET_SUBSCRIBE,
  ]
  assert await hub.is_market_device("device-2")


@pytest.mark.asyncio
async def test_market_event_only_publishes_from_assigned_agent(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  published = []

  async def is_market_device(device_id):
    return device_id == "device-1"

  async def publish(channel, payload):
    published.append((channel, payload))
    return 1

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "is_market_device",
    is_market_device,
  )
  monkeypatch.setattr(agent_api.redis_pubsub, "publish", publish)

  await agent_api._publish_market_event(
    "device-1",
    {
      "kind": "quote",
      "stock_code": "600000.SH",
      "period": "1m",
      "data": {"600000.SH": [{"close": 10.5}]},
    },
  )
  assert published == [
    (
      "market-data:600000.SH:1m",
      {"600000.SH": [{"close": 10.5}]},
    )
  ]

  with pytest.raises(Exception, match="活动行情 Agent"):
    await agent_api._publish_market_event(
      "device-2",
      {
        "kind": "whole",
        "data": {},
      },
    )

  with pytest.raises(ValueError, match="只允许单标的 K 线"):
    await agent_api._publish_market_event(
      "device-1",
      {"kind": "whole", "data": {}},
    )
