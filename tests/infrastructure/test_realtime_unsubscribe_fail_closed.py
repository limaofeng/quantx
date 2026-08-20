from __future__ import annotations

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.core.data.adapter import DataSubscription
from quantx_infrastructure.core.data.adapter_manager import AdapterManager
from quantx_infrastructure.core.data.realtime import RealtimeDataAdapter
from quantx_infrastructure.core.data.unified_subscription_manager import (
  UnifiedDataSubscriptionManager,
  _Handle,
)
from quantx_infrastructure.core.data.whole_quote_hub import WholeQuoteHub


@pytest.mark.asyncio
async def test_realtime_unsubscribe_retains_local_record_until_manager_succeeds() -> None:
  adapter = RealtimeDataAdapter()
  manager = AsyncMock()
  manager.unsubscribe = AsyncMock(
    side_effect=[False, RuntimeError("temporary unsubscribe failure"), True]
  )
  adapter.subscription_manager = manager
  subscription_id = "local-subscription"
  adapter.subscriptions[subscription_id] = DataSubscription(
    instrument_code="600000.SH",
    data_type="tick",
    manager_handle="manager-handle",
  )

  with pytest.raises(RuntimeError, match="拒绝取消句柄"):
    await adapter.unsubscribe(subscription_id)
  assert subscription_id in adapter.subscriptions

  with pytest.raises(RuntimeError, match="temporary unsubscribe failure"):
    await adapter.unsubscribe(subscription_id)
  assert subscription_id in adapter.subscriptions

  assert await adapter.unsubscribe(subscription_id) is True
  assert subscription_id not in adapter.subscriptions
  assert await adapter.unsubscribe(subscription_id) is True
  assert manager.unsubscribe.await_count == 3


@pytest.mark.asyncio
async def test_disconnected_adapter_release_retains_ref_until_unsubscribe_succeeds(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  adapter = RealtimeDataAdapter()
  subscription_manager = AsyncMock()
  subscription_manager.unsubscribe = AsyncMock(side_effect=[False, True])
  subscription_manager.unsubscribe_all = AsyncMock(return_value=True)
  adapter.subscription_manager = subscription_manager
  adapter.is_connected = False
  adapter.subscriptions["local-subscription"] = DataSubscription(
    instrument_code="600000.SH",
    data_type="tick",
    manager_handle="manager-handle",
  )

  manager = AdapterManager()
  ref_counts = defaultdict(int, {"realtime": 1})
  monkeypatch.setattr(manager, "_realtime_adapter", adapter)
  monkeypatch.setattr(manager, "_ref_counts", ref_counts)
  monkeypatch.setattr(manager, "_lifecycle_locks", defaultdict(asyncio.Lock))

  with pytest.raises(RuntimeError, match="拒绝取消句柄"):
    await manager.release_adapter_for_mode("paper")
  assert ref_counts["realtime"] == 1
  assert "local-subscription" in adapter.subscriptions

  await manager.release_adapter_for_mode("paper")
  assert ref_counts["realtime"] == 0
  assert adapter.subscriptions == {}


@pytest.mark.asyncio
async def test_unified_unsubscribe_retains_owner_maps_until_hub_confirms(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  manager = UnifiedDataSubscriptionManager()
  handle = "manager-handle"
  owner = "runtime-owner"
  record = _Handle(
    owner=owner,
    stock_code="600000.SH",
    period="tick",
    callback=lambda _data: None,
    hub_handle="hub-handle",
  )
  hub = AsyncMock()
  hub.unsubscribe = AsyncMock(side_effect=[False, True])
  monkeypatch.setattr(manager, "hub", hub)
  monkeypatch.setattr(manager, "_handles", {handle: record})
  monkeypatch.setattr(manager, "_owner_handles", {owner: {handle}})
  monkeypatch.setattr(manager, "_period_subscriptions", {})

  assert await manager.unsubscribe(handle) is False
  assert manager._handles == {handle: record}
  assert manager._owner_handles == {owner: {handle}}

  assert await manager.unsubscribe(handle) is True
  assert manager._handles == {}
  assert manager._owner_handles == {}
  assert await manager.unsubscribe(handle) is True


@pytest.mark.asyncio
async def test_whole_quote_unsubscribe_timeout_retains_retryable_consumer() -> None:
  hub = WholeQuoteHub()
  handle = await hub.subscribe_tick("600000.SH", lambda _data: None)
  consumer = hub._consumers[handle]
  consumer.task.cancel()
  await asyncio.gather(consumer.task, return_exceptions=True)

  release = asyncio.Event()
  entered = asyncio.Event()

  async def cancellation_delayed() -> None:
    entered.set()
    try:
      await asyncio.Event().wait()
    except asyncio.CancelledError:
      await release.wait()

  consumer.task = asyncio.create_task(cancellation_delayed())
  await entered.wait()
  hub._CONSUMER_CANCEL_TIMEOUT_SECONDS = 0.01

  assert await hub.unsubscribe(handle) is False
  assert hub._consumers[handle] is consumer
  assert hub._tick_consumers_by_code["600000.SH"][handle] is consumer

  release.set()
  await asyncio.wait_for(consumer.task, timeout=1.0)
  assert await hub.unsubscribe(handle) is True
  assert handle not in hub._consumers
  assert "600000.SH" not in hub._tick_consumers_by_code
  assert await hub.unsubscribe(handle) is True
