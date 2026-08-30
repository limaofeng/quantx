import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from quantx_api import agent_api, market_gateway
from quantx_contracts.market_health import MarketGatewayHealth, MarketHealthReason
from quantx_infrastructure.core.data.market_stream_transport import (
  MarketStreamFreshnessLease,
  MarketStreamState,
)
from quantx_infrastructure.services.market_stream_readiness import (
  MarketStreamReadinessStatus,
  classify_authoritative_market_stream_readiness,
)


@pytest.fixture
def supply(monkeypatch):
  state = MarketStreamState(
    status="READY",
    stream_id="current",
    sequence=4,
    instrument_count=100,
    universe_count=100,
    updated_at=datetime.now(timezone.utc),
  )
  lease = MarketStreamFreshnessLease(stream_id="current", sequence=4)
  reader = AsyncMock(return_value=(state, lease))
  monkeypatch.setattr(market_gateway, "active_market_stream_id", lambda: "current")
  monkeypatch.setattr(
    market_gateway.market_stream_store, "state_with_freshness", reader
  )
  monkeypatch.setattr(
    market_gateway._trading_time,
    "is_trading_hours",
    AsyncMock(return_value=True),
  )
  monkeypatch.setattr(
    market_gateway.market_stream_store,
    "engine_state",
    AsyncMock(side_effect=AssertionError("supply must not query consumers")),
  )
  return state, lease, reader


async def response_health():
  response = await market_gateway.health_ready()
  payload = json.loads(response.body)
  health = MarketGatewayHealth.model_validate(payload)
  assert response.headers["cache-control"] == "no-store"
  assert response.status_code == (200 if health.status == "ready" else 503)
  assert "engineSequence" not in payload
  assert "streamId" not in payload
  assert "error" not in payload
  return health


async def test_ready_supply_needs_neither_engine_nor_trading_account(supply):
  # Authenticated market sockets also accept data-only / TRADING_UNAVAILABLE.
  health = await response_health()
  assert health.status == "ready"
  assert health.connected_devices == 1
  assert health.sequence == 4
  market_gateway.market_stream_store.engine_state.assert_not_awaited()
  # The separate trading gate MUST still reject missing/lagging Engine state.
  state, lease, _ = supply
  for engine in (None, replace(state, sequence=3), replace(state, stream_id="old")):
    readiness = classify_authoritative_market_stream_readiness(
      stream_state=state,
      freshness_lease=lease,
      engine_state=engine,
      trading_session=True,
    )
    assert readiness.status is MarketStreamReadinessStatus.FAILED


async def test_offline_socket_cannot_reuse_redis_ready(supply, monkeypatch):
  monkeypatch.setattr(market_gateway, "active_market_stream_id", lambda: "")
  health = await response_health()
  assert health.reason_code is MarketHealthReason.STREAM_OFFLINE
  supply[2].assert_not_awaited()


@pytest.mark.parametrize(
  ("changes", "reason"),
  [
    ({"stream_id": "old"}, MarketHealthReason.STREAM_OFFLINE),
    ({"status": "OFFLINE"}, MarketHealthReason.STREAM_OFFLINE),
    ({"status": "SYNCING"}, MarketHealthReason.STREAM_SYNCING),
    ({"sequence": 2}, MarketHealthReason.STREAM_SYNCING),
    ({"commit_phase": "PREPARING"}, MarketHealthReason.STREAM_SYNCING),
    ({"instrument_count": 98}, MarketHealthReason.SNAPSHOT_INCOMPLETE),
    ({"universe_count": 0}, MarketHealthReason.SNAPSHOT_INCOMPLETE),
    ({"updated_at": None}, MarketHealthReason.SNAPSHOT_INCOMPLETE),
  ],
)
async def test_supply_rejects_unready_stream(supply, changes, reason):
  state, lease, reader = supply
  reader.return_value = (replace(state, **changes), lease)
  assert (await response_health()).reason_code is reason


@pytest.mark.parametrize(
  "lease",
  [
    None,
    MarketStreamFreshnessLease("old", 4),
    MarketStreamFreshnessLease("current", 3),
  ],
)
async def test_trading_session_requires_current_freshness(supply, lease):
  supply[2].return_value = (supply[0], lease)
  assert (await response_health()).reason_code is MarketHealthReason.STREAM_STALE


async def test_closed_market_keeps_connected_snapshot_healthy(supply, monkeypatch):
  supply[2].return_value = (supply[0], None)
  monkeypatch.setattr(
    market_gateway._trading_time, "is_trading_hours", AsyncMock(return_value=False)
  )
  health = await response_health()
  assert health.status == "ready"
  assert health.trading_session is False


async def test_connection_replaced_during_probe_cannot_report_ready(
  supply, monkeypatch
):
  async def replacement():
    monkeypatch.setattr(
      market_gateway, "active_market_stream_id", lambda: "replacement"
    )
    return supply[0], supply[1]

  supply[2].side_effect = replacement
  assert (await response_health()).reason_code is MarketHealthReason.STREAM_OFFLINE


@pytest.mark.parametrize("timeout", [False, True])
async def test_redis_failure_is_bounded_and_sanitized(supply, monkeypatch, timeout):
  async def fail():
    if timeout:
      await asyncio.Event().wait()
    raise ConnectionError("redis://private:secret@host")

  supply[2].side_effect = fail
  monkeypatch.setattr(market_gateway, "MARKET_GATEWAY_READINESS_TIMEOUT_SECONDS", 0.01)
  assert (await response_health()).reason_code is MarketHealthReason.REDIS_UNAVAILABLE


async def test_calendar_failure_does_not_assume_market_closed(supply, monkeypatch):
  monkeypatch.setattr(
    market_gateway._trading_time,
    "is_trading_hours",
    AsyncMock(side_effect=TimeoutError()),
  )
  assert (
    await response_health()
  ).reason_code is MarketHealthReason.CALENDAR_UNAVAILABLE


async def test_liveness_does_not_check_dependencies(monkeypatch):
  monkeypatch.setattr(
    market_gateway, "market_supply_health", AsyncMock(side_effect=AssertionError())
  )
  assert await market_gateway.health_live() == {
    "status": "alive",
    "component": "market-gateway",
  }


async def test_connection_registry_exposes_only_current_stream():
  registry = agent_api._MarketConnectionRegistry()
  first = await registry.register()
  assert first and registry.active_stream_id == first
  assert await registry.register() is None
  await registry.unregister("other")
  assert registry.active_stream_id == first
  await registry.unregister(first)
  assert registry.active_stream_id == ""
