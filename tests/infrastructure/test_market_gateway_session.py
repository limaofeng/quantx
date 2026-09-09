"""Gateway connection authority is independent of control-session leases."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.auth.errors import AuthError
from quantx_infrastructure.models.agent_runtime import AgentDevice
from quantx_infrastructure.services import market_lease_reader
from quantx_market_data import agent_stream

from tests.infrastructure.test_market_gateway_auth import gateway_auth  # noqa: F401


@pytest.fixture
async def stream_identity(gateway_auth, monkeypatch):  # noqa: F811
  _, sessions = gateway_auth
  registry = agent_stream._MarketConnectionRegistry()
  monkeypatch.setattr(agent_stream, "_market_connections", registry)
  stream_id = await registry.register()
  identity = agent_stream.MarketStreamSession("device", "user", stream_id)
  return registry, identity, sessions


async def test_revalidation_and_events_do_not_read_control_lease(
  stream_identity, monkeypatch
):
  _, identity, _ = stream_identity
  monkeypatch.setattr(
    market_lease_reader.market_lease_reader,
    "market_lease",
    AsyncMock(side_effect=AssertionError("control dependency")),
  )
  publish = AsyncMock()
  monkeypatch.setattr(agent_stream.redis_pubsub, "publish", publish)
  await agent_stream._ensure_device_active("device", session=identity)
  await agent_stream._publish_market_event(
    identity,
    {"kind": "quote", "stock_code": "000001.SZ", "period": "1m", "data": {"close": 10}},
  )
  publish.assert_awaited_once_with("market-data:000001.SZ:1m", {"close": 10})


async def test_replaced_connection_cannot_publish_late_events(
  stream_identity, monkeypatch
):
  registry, identity, _ = stream_identity
  await registry.unregister(identity.stream_id)
  assert await registry.register() != identity.stream_id
  publish = AsyncMock()
  monkeypatch.setattr(agent_stream.redis_pubsub, "publish", publish)
  with pytest.raises(AuthError, match="已失效"):
    await agent_stream._publish_market_event(
      identity, {"kind": "quote", "stock_code": "000001.SZ"}
    )
  publish.assert_not_awaited()


@pytest.mark.parametrize("mutation", ["revoked", "reassigned", "deleted"])
async def test_persisted_device_changes_invalidate_the_stream(
  stream_identity, mutation
):
  _, identity, sessions = stream_identity
  async with sessions() as db:
    device = await db.get(AgentDevice, "device")
    if mutation == "revoked":
      device.revoked_at = datetime.now()
    elif mutation == "reassigned":
      device.user_id = "another-user"
    else:
      await db.delete(device)
    await db.commit()
  with pytest.raises(AuthError, match="设备已撤销或身份已改变"):
    await agent_stream._ensure_device_active("device", session=identity)


async def test_missing_or_wrong_connection_identity_is_rejected(stream_identity):
  _, identity, _ = stream_identity
  with pytest.raises(AuthError):
    await agent_stream._ensure_device_active("device")
  with pytest.raises(AuthError):
    await agent_stream._ensure_device_active("other-device", session=identity)


async def test_whole_market_payload_cannot_use_single_symbol_event_path(
  stream_identity, monkeypatch
):
  _, identity, _ = stream_identity
  publish = AsyncMock()
  monkeypatch.setattr(agent_stream.redis_pubsub, "publish", publish)
  with pytest.raises(ValueError, match="只允许单标的 K 线"):
    await agent_stream._publish_market_event(identity, {"kind": "whole", "data": {}})
  publish.assert_not_awaited()
