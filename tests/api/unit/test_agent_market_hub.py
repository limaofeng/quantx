import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from quantx_api import agent_api, agent_hub
from quantx_contracts import AgentMessageType


class FakeRedis:
  def __init__(self, active=None):
    self.active = active or {}
    self.values = {}

  async def hgetall(self, _key):
    return {key: json.dumps(value) for key, value in self.active.items()}

  async def set(self, key, value, **_kwargs):
    self.values[key] = value

  async def get(self, key):
    return self.values.get(key)

  async def delete(self, key):
    self.values.pop(key, None)

  async def eval(
    self,
    _script,
    _numkeys,
    key,
    operation,
    api_instance_id,
    api_started_at,
    payload,
    _ttl,
  ):
    current_raw = self.values.get(key)
    current = json.loads(current_raw) if current_raw else None
    if operation == "delete":
      if current and current.get("api_instance_id") == api_instance_id:
        self.values.pop(key, None)
        return 1
      return 0
    if (
      current
      and current.get("api_instance_id") != api_instance_id
      and int(current.get("api_started_at_micros") or 0) >= int(api_started_at)
    ):
      return 0
    self.values[key] = payload
    return 1


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
  first = await hub.register(
    "device-1",
    {"market-data"},
    authorized_account_ids={"account-1"},
    connected_at=datetime.now(timezone.utc),
    remote_address_summary="10.0.0.*",
  )
  standby = await hub.register(
    "device-2",
    {"market-data"},
    authorized_account_ids={"account-1"},
    connected_at=datetime.now(timezone.utc),
    remote_address_summary="10.0.0.*",
  )

  first_messages = [first.queue.get_nowait(), first.queue.get_nowait()]
  assert [item.message_type for item in first_messages] == [
    AgentMessageType.MARKET_RESET,
    AgentMessageType.MARKET_SUBSCRIBE,
  ]
  assert standby.queue.get_nowait().message_type is AgentMessageType.MARKET_RESET
  assert await hub.is_market_device("device-1")
  assert not await hub.is_market_device("device-2")

  await hub.unregister(first)
  failover = [standby.queue.get_nowait(), standby.queue.get_nowait()]
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

  async def is_market_session(lease):
    return lease.device_id == "device-1"

  async def publish(channel, payload):
    published.append((channel, payload))
    return 1

  async def ensure_device_active(_device_id, *, lease=None):
    assert lease is not None

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "is_market_session",
    is_market_session,
  )
  monkeypatch.setattr(agent_api, "_ensure_device_active", ensure_device_active)
  monkeypatch.setattr(agent_api.redis_pubsub, "publish", publish)

  active_session = agent_hub.AgentControlSession(
    device_id="device-1",
    capabilities={"market-data"},
    authorized_account_ids=frozenset({"account-1"}),
    queue=asyncio.Queue(),
    api_instance_id="api-1",
    agent_session_id="session-1",
    server_connected_at=datetime.now(timezone.utc),
    remote_address_summary="10.0.0.*",
    revoked=asyncio.Event(),
  )
  await agent_api._publish_market_event(
    active_session,
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
      agent_hub.AgentControlSession(
        device_id="device-2",
        capabilities={"market-data"},
        authorized_account_ids=frozenset({"account-1"}),
        queue=asyncio.Queue(),
        api_instance_id="api-1",
        agent_session_id="session-2",
        server_connected_at=datetime.now(timezone.utc),
        remote_address_summary="10.0.0.*",
        revoked=asyncio.Event(),
      ),
      {
        "kind": "whole",
        "data": {},
      },
    )

  with pytest.raises(ValueError, match="只允许单标的 K 线"):
    await agent_api._publish_market_event(
      active_session,
      {"kind": "whole", "data": {}},
    )


@pytest.mark.asyncio
async def test_market_stream_revalidation_uses_cross_process_lease(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  checked: list[str] = []
  now = agent_api.utcnow()
  api_heartbeat = SimpleNamespace(
    instance_id="api-instance-1",
    status="READY",
    updated_at=now,
  )
  lease = agent_api.MarketSessionLease(
    device_id="device-1",
    api_instance_id="api-instance-1",
    agent_session_id="agent-session-1",
  )

  async def market_lease(device_id: str):
    checked.append(device_id)
    return lease if device_id == "device-1" else None

  async def is_market_session(value) -> bool:
    return value == lease

  class Session:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return False

    async def get(self, model, _key):
      if model is agent_api.AgentDevice:
        return SimpleNamespace(revoked_at=None)
      return api_heartbeat

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "market_lease",
    market_lease,
  )
  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "is_market_session",
    is_market_session,
  )
  monkeypatch.setattr(agent_api, "AsyncSessionLocal", Session)

  await agent_api._ensure_device_active("device-1")
  with pytest.raises(Exception, match="控制会话已断开"):
    await agent_api._ensure_device_active("device-2")
  api_heartbeat.instance_id = "api-instance-2"
  with pytest.raises(Exception, match="控制会话已断开"):
    await agent_api._ensure_device_active("device-1")

  assert checked == ["device-1", "device-2", "device-1"]


@pytest.mark.asyncio
async def test_hub_revocation_wakes_control_guard_and_fails_over_market_agent(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  fake_redis = FakeRedis()

  async def get_redis():
    return fake_redis

  monkeypatch.setattr(agent_hub.redis_pubsub, "get_redis", get_redis)
  hub = agent_hub.AgentConnectionHub()
  first = await hub.register(
    "device-1",
    {"market-data"},
    authorized_account_ids={"account-1"},
    connected_at=datetime.now(timezone.utc),
    remote_address_summary="10.0.0.*",
  )
  standby = await hub.register(
    "device-2",
    {"market-data"},
    authorized_account_ids={"account-1"},
    connected_at=datetime.now(timezone.utc),
    remote_address_summary="10.0.0.*",
  )

  waiter = asyncio.create_task(hub.wait_until_revoked(first, timeout_seconds=1))
  assert await hub.revoke("device-1")

  assert await waiter
  assert await hub.is_market_device("device-2")
  assert standby.queue.qsize() >= 2


@pytest.mark.asyncio
async def test_duplicate_device_connection_replaces_exact_session_generation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  fake_redis = FakeRedis()

  async def get_redis():
    return fake_redis

  monkeypatch.setattr(agent_hub.redis_pubsub, "get_redis", get_redis)
  hub = agent_hub.AgentConnectionHub(api_instance_id="api-instance-1")
  first = await hub.register(
    "device-1",
    {"market-data", "live"},
    authorized_account_ids={"account-1"},
    connected_at=datetime.now(timezone.utc),
    remote_address_summary="10.0.0.*",
  )
  replacement = await hub.register(
    "device-1",
    {" MARKET-DATA ", "LIVE"},
    authorized_account_ids={"account-1"},
    connected_at=datetime.now(timezone.utc),
    remote_address_summary="10.0.0.*",
  )

  assert first.revoked.is_set()
  assert not replacement.revoked.is_set()
  assert replacement.capabilities == {"market-data", "live"}
  assert await hub.wait_until_revoked(first, timeout_seconds=0)
  assert not await hub.unregister(first)
  assert await hub.current_session("device-1") is replacement
  assert await hub.is_connected(
    "device-1",
    agent_session_id=replacement.agent_session_id,
  )
  assert not await hub.is_connected(
    "device-1",
    agent_session_id=first.agent_session_id,
  )
  assert await hub.market_lease("device-1") is None
  assert await hub.authorize_market_after_reconciliation(replacement)
  assert await hub.market_lease("device-1") == agent_hub.MarketSessionLease(
    device_id="device-1",
    api_instance_id="api-instance-1",
    agent_session_id=replacement.agent_session_id,
  )


@pytest.mark.asyncio
async def test_superseded_api_cannot_overwrite_or_delete_new_market_lease(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  fake_redis = FakeRedis()

  async def get_redis():
    return fake_redis

  monkeypatch.setattr(agent_hub.redis_pubsub, "get_redis", get_redis)
  started_at = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
  old_hub = agent_hub.AgentConnectionHub(
    api_instance_id="api-old",
    api_started_at=started_at,
  )
  new_hub = agent_hub.AgentConnectionHub(
    api_instance_id="api-new",
    api_started_at=started_at + timedelta(seconds=1),
  )
  equal_generation_hub = agent_hub.AgentConnectionHub(
    api_instance_id="api-peer",
    api_started_at=started_at + timedelta(seconds=1),
  )
  old_session = await old_hub.register(
    "device-old",
    {"market-data"},
    authorized_account_ids={"account-1"},
    connected_at=started_at,
    remote_address_summary="10.0.0.*",
  )
  new_session = await new_hub.register(
    "device-new",
    {"market-data"},
    authorized_account_ids={"account-1"},
    connected_at=started_at + timedelta(seconds=1),
    remote_address_summary="10.0.0.*",
  )
  await equal_generation_hub.register(
    "device-peer",
    {"market-data"},
    authorized_account_ids={"account-1"},
    connected_at=started_at + timedelta(seconds=1),
    remote_address_summary="10.0.0.*",
  )

  assert (await new_hub.market_lease("device-new")).agent_session_id == (
    new_session.agent_session_id
  )
  assert await equal_generation_hub.market_lease("device-peer") is None

  await old_hub.refresh_market_device(old_session)
  await old_hub.unregister(old_session)

  lease = await new_hub.market_lease("device-new")
  assert lease is not None
  assert lease.api_instance_id == "api-new"
  assert lease.agent_session_id == new_session.agent_session_id
