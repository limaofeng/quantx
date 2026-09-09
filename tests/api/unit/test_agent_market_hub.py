import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from quantx_api import agent_hub
from quantx_contracts import AgentMessageType
from quantx_infrastructure.services.market_lease_reader import MarketSessionLease


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

  assert await waiter == agent_hub.QMT_DEVICE_REVOKED
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
  assert (
    await hub.wait_until_revoked(first, timeout_seconds=0)
    == agent_hub.QMT_CONTROL_SESSION_REPLACED
  )
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
  assert await hub.market_lease("device-1") == MarketSessionLease(
    device_id="device-1",
    api_instance_id="api-instance-1",
    agent_session_id=replacement.agent_session_id,
  )


@pytest.mark.asyncio
async def test_live_market_lease_is_independent_from_trading_reconciliation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  fake_redis = FakeRedis()

  async def get_redis():
    return fake_redis

  monkeypatch.setattr(agent_hub.redis_pubsub, "get_redis", get_redis)
  hub = agent_hub.AgentConnectionHub(api_instance_id="api-instance-1")
  await hub.register(
    "device-live",
    {"market-data", "live"},
    authorized_account_ids={"account-1"},
    connected_at=datetime.now(timezone.utc),
    remote_address_summary="10.0.0.*",
  )

  ready = await hub.market_lease_diagnostic("device-live")
  assert ready["reasonCode"] == "MARKET_LEASE_READY"
  assert ready["controlSessionRegistered"] is True
  assert ready["marketDataCapability"] is True
  assert ready["redisLeasePresent"] is True
  assert ready["redisLeaseDeviceId"] == "device-live"


@pytest.mark.asyncio
async def test_market_gateway_diagnostic_does_not_infer_missing_control_session(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  fake_redis = FakeRedis()

  async def get_redis():
    return fake_redis

  monkeypatch.setattr(agent_hub.redis_pubsub, "get_redis", get_redis)
  gateway_hub = agent_hub.AgentConnectionHub(api_instance_id="gateway-process")

  diagnostic = await gateway_hub.market_lease_diagnostic("device-live")

  assert diagnostic["controlSessionRegistered"] is False
  assert diagnostic["reasonCode"] == "MARKET_LEASE_NOT_PUBLISHED"


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
