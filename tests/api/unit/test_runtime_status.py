import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from quantx_api import agent_api, runtime_status
from quantx_contracts import PROTOCOL_VERSION
from quantx_contracts.market_health import MARKET_GATEWAY_HTTP_TIMEOUT_SECONDS
from quantx_infrastructure.core.data.market_stream_transport import (
  MarketStreamFreshnessLease,
  MarketStreamState,
)
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  RuntimeComponentHeartbeat,
)
from quantx_infrastructure.services.agent_session_guard import (
  QMT_ACCOUNT_MISMATCH,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def gateway_payload(status="ready"):
  return {
    "component": "market-gateway",
    "protocol": "quantx.market.v2",
    "status": status,
    "reasonCode": None if status == "ready" else "MARKET_STREAM_OFFLINE",
    "connectedDevices": 1 if status == "ready" else 0,
    "sequence": 42,
    "instrumentCount": 100,
    "universeCount": 100,
    "streamAgeSeconds": 0.125,
    "tradingSession": True,
  }


@pytest.mark.asyncio
async def test_market_gateway_status_uses_readiness_endpoint(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls: dict[str, object] = {}

  class FakeResponse:
    status_code = 200
    is_success = True

    @staticmethod
    def json():
      return gateway_payload()

  class FakeClient:
    def __init__(self, **kwargs):
      calls["client_kwargs"] = kwargs

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    async def get(self, url):
      calls["url"] = url
      return FakeResponse()

  monkeypatch.setattr(
    runtime_status.settings,
    "market_gateway_url",
    "http://127.0.0.1:18082",
  )
  monkeypatch.setattr(runtime_status.httpx, "AsyncClient", FakeClient)

  status = await runtime_status._market_gateway_status()

  assert calls["client_kwargs"] == {
    "timeout": MARKET_GATEWAY_HTTP_TIMEOUT_SECONDS,
    "trust_env": False,
  }
  assert calls["url"] == "http://127.0.0.1:18082/health/ready"
  assert status == {**gateway_payload(), "statusCode": 200}


@pytest.mark.asyncio
async def test_market_gateway_status_rejects_non_ready_payload(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class FakeResponse:
    status_code = 503
    is_success = False

    @staticmethod
    def json():
      return gateway_payload("not_ready")

  class FakeClient:
    def __init__(self, **_kwargs):
      pass

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    async def get(self, _url):
      return FakeResponse()

  monkeypatch.setattr(runtime_status.httpx, "AsyncClient", FakeClient)

  status = await runtime_status._market_gateway_status()

  assert status == {
    **gateway_payload("not_ready"),
    "status": "unavailable",
    "statusCode": 503,
  }


@pytest.mark.parametrize("ready", [True, False])
@pytest.mark.parametrize("missing", ["component", "protocol"])
async def test_market_gateway_projection_does_not_fill_missing_identity(
  monkeypatch, ready, missing
):
  payload = gateway_payload("ready" if ready else "not_ready")
  del payload[missing]
  client = httpx.AsyncClient(
    transport=httpx.MockTransport(
      lambda request: httpx.Response(200 if ready else 503, json=payload)
    )
  )
  monkeypatch.setattr(runtime_status.httpx, "AsyncClient", lambda **kwargs: client)
  assert await runtime_status._market_gateway_status() == {
    "status": "unavailable",
    "reasonCode": "MARKET_GATEWAY_UNAVAILABLE",
    "error": "ValidationError",
  }


@pytest.mark.asyncio
async def test_prefect_worker_health_uses_canonical_api_and_ignores_proxies(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls: dict[str, object] = {}

  class FakeResponse:
    status_code = 200
    is_success = True

    def __init__(self, payload):
      self.payload = payload

    def json(self):
      return self.payload

  class FakeClient:
    def __init__(self, **kwargs):
      calls["client_kwargs"] = kwargs

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    async def get(self, url):
      calls["health_url"] = url
      return FakeResponse({})

    async def post(self, url, json):
      calls["workers_url"] = url
      calls["workers_body"] = json
      return FakeResponse(
        [
          {"name": "worker-1", "status": "ONLINE"},
          {"name": "worker-old", "status": "OFFLINE"},
        ]
      )

  monkeypatch.setattr(runtime_status.settings, "prefect_enabled", True)
  monkeypatch.setattr(
    runtime_status.settings,
    "prefect_api_url",
    "http://192.168.5.6:30420",
  )
  monkeypatch.setattr(
    runtime_status.settings,
    "prefect_worker_pool",
    "quantx-pool",
  )
  monkeypatch.setattr(runtime_status.httpx, "AsyncClient", FakeClient)

  status = await runtime_status._prefect_status()

  assert calls["client_kwargs"] == {"timeout": 5.0, "trust_env": False}
  assert calls["health_url"] == "http://192.168.5.6:30420/api/health"
  assert calls["workers_url"] == (
    "http://192.168.5.6:30420/api/work_pools/quantx-pool/workers/filter"
  )
  assert calls["workers_body"] == {}
  assert status["status"] == "ready"
  assert status["workerStatus"] == "ready"
  assert status["onlineWorkers"] == 1
  assert status["registeredWorkers"] == 2
  assert status["offlineWorkers"] == 1
  assert status["workers"] == [
    {
      "name": "worker-1",
      "status": "ONLINE",
      "lastHeartbeatTime": None,
    }
  ]
  assert status["workersStatusCode"] == 200


@pytest.mark.asyncio
async def test_component_status_exposes_worker_registration_counts(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  async def fake_database_status():
    return {"status": "ready"}

  async def fake_component_heartbeats():
    return {
      "qmt-agent": {"status": "ready"},
      "engine": {"status": "ready"},
      "market-data": {"status": "ready"},
      "ai-runtime": {
        "status": "ready",
        "ageSeconds": 2.5,
        "details": {
          "configVersion": 3,
          "host": "must-not-be-public",
          "pid": 123,
        },
      },
    }

  async def fake_prefect_status():
    return {
      "status": "ready",
      "workerStatus": "ready",
      "onlineWorkers": 1,
      "registeredWorkers": 3,
      "offlineWorkers": 2,
      "workers": [{"name": "worker-1", "status": "ONLINE"}],
    }

  monkeypatch.setattr(runtime_status, "_database_status", fake_database_status)
  monkeypatch.setattr(
    runtime_status,
    "_component_heartbeats",
    fake_component_heartbeats,
  )
  monkeypatch.setattr(runtime_status, "_prefect_status", fake_prefect_status)
  monkeypatch.setattr(
    runtime_status, "_market_gateway_status", AsyncMock(return_value=gateway_payload())
  )

  components = await runtime_status.component_status()

  assert components["worker"] == {
    "status": "ready",
    "onlineWorkers": 1,
    "registeredWorkers": 3,
    "offlineWorkers": 2,
    "workers": [{"name": "worker-1", "status": "ONLINE"}],
  }
  assert components["aiRuntime"] == {
    "status": "ready",
    "ageSeconds": 2.5,
    "configVersion": 3,
  }
  assert "host" not in components["aiRuntime"]
  assert "aiRuntime" not in runtime_status.required_components()


@pytest.mark.asyncio
async def test_component_status_uses_aggregated_runtime_snapshot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  async def fake_database_status():
    return {"status": "ready"}

  async def fake_component_heartbeats():
    return {
      "qmt-agent": {
        "status": "ready",
        "connectedDevices": 1,
        "readyDevices": 1,
        "onlineDevices": 1,
        "registeredDevices": 1,
        "modes": ["live"],
        "accountIds": ["ACCOUNT-1"],
      },
      "engine": {"status": "ready"},
      "market-data": {
        "status": "ready",
        "connectedDevices": 1,
        "protocol": "quantx.market.v2",
      },
    }

  async def fake_prefect_status():
    return {"status": "ready", "workerStatus": "ready"}

  monkeypatch.setattr(runtime_status.settings, "runtime_profile", "full")
  monkeypatch.setattr(runtime_status, "_database_status", fake_database_status)
  monkeypatch.setattr(
    runtime_status,
    "_component_heartbeats",
    fake_component_heartbeats,
  )
  monkeypatch.setattr(runtime_status, "_prefect_status", fake_prefect_status)
  monkeypatch.setattr(
    runtime_status, "_market_gateway_status", AsyncMock(return_value=gateway_payload())
  )

  components = await runtime_status.component_status()

  assert components["qmtAgent"]["status"] == "ready"
  assert components["qmtAgent"]["connectedDevices"] == 1
  assert components["marketData"]["status"] == "ready"


@pytest.mark.asyncio
async def test_market_data_runtime_status_uses_only_gateway_supply(monkeypatch):
  gateway = AsyncMock(return_value=gateway_payload())
  monkeypatch.setattr(runtime_status, "_market_gateway_status", gateway)
  monkeypatch.setattr(
    runtime_status,
    "_component_heartbeats",
    AsyncMock(side_effect=AssertionError("must not read Engine or account")),
  )
  assert await runtime_status.market_data_runtime_status() == gateway_payload()
  gateway.assert_awaited_once()


@pytest.mark.asyncio
async def test_qmt_agent_component_is_degraded_until_trade_reconciliation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(runtime_status.settings, "enable_real_trading", True)
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          AgentDevice.__table__,
          RuntimeComponentHeartbeat.__table__,
        ],
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(runtime_status, "AsyncSessionLocal", session_factory)
  stream_state = MarketStreamState(
    status="READY",
    stream_id="stream-1",
    sequence=5,
    captured_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    updated_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    instrument_count=5_000,
  )

  current_freshness = MarketStreamFreshnessLease(
    stream_id=stream_state.stream_id,
    sequence=stream_state.sequence,
  )
  current_engine_state = stream_state

  async def readiness_snapshot():
    return stream_state, current_freshness, current_engine_state

  async def inside_session(*_args):
    return True

  control_identity = {
    "api_instance_id": "api-instance-1",
    "agent_session_id": "agent-session-1",
  }

  async def health_snapshots():
    return [
      SimpleNamespace(
        device_id="device-1",
        api_instance_id=control_identity["api_instance_id"],
        agent_session_id=control_identity["agent_session_id"],
        heartbeat_age_seconds=0.0,
        dependency_ready=True,
      )
    ]

  monkeypatch.setattr(
    runtime_status.market_stream_store,
    "readiness_snapshot",
    readiness_snapshot,
  )
  monkeypatch.setattr(
    runtime_status.TradingTimeService,
    "is_trading_hours",
    inside_session,
  )
  monkeypatch.setattr(
    runtime_status.agent_connection_hub,
    "health_snapshots",
    health_snapshots,
  )
  now = runtime_status.utcnow()

  async with session_factory() as db:
    db.add(
      RuntimeComponentHeartbeat(
        component="engine", instance_id="engine-1", status="READY", updated_at=now
      )
    )
    db.add(
      AgentDevice(
        id="device-1",
        user_id="user-1",
        name="live",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["live", "market-data"],
        last_seen_at=now,
      )
    )
    db.add(
      RuntimeComponentHeartbeat(
        component="api",
        instance_id="api-instance-1",
        status="READY",
        details={"apiInstanceId": "api-instance-1"},
        updated_at=now,
      )
    )
    db.add(
      RuntimeComponentHeartbeat(
        component="qmt-agent:device-1",
        instance_id="instance-1",
        status="RECONCILE_REQUIRED",
        details={
          "protocolVersion": PROTOCOL_VERSION,
          "marketStreamStatus": "READY",
          "apiInstanceId": "api-instance-1",
          "agentSessionId": "agent-session-1",
          "serverConnectedAt": now.isoformat(),
          "serverReceivedAt": now.isoformat(),
          "agentSentAt": now.isoformat(),
          "remoteAddressSummary": "10.0.0.*",
          "sessionActive": True,
        },
        updated_at=now,
      )
    )
    await db.commit()

  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"] == {
    "status": "degraded",
    "connectedDevices": 1,
    "readyDevices": 0,
    "onlineDevices": 1,
    "reconcilingDevices": 1,
    "degradedDevices": 0,
    "registeredDevices": 1,
    "modes": ["live"],
    "protocolVersions": [PROTOCOL_VERSION],
    "accountIds": ["***nt-1"],
    "latestSnapshotAgeSeconds": None,
    "latestReadyHeartbeatAt": None,
    "reasonCode": "QMT_AGENT_NOT_RECONCILED",
  }
  assert "qmt-agent:device-1" not in components
  assert components["engine"]["marketConsumption"]["status"] == "ready"
  assert components["engine"]["marketConsumption"]["streamAgeSeconds"] >= 25
  assert components["engine"]["marketConsumption"]["engineAgeSeconds"] >= 25
  assert components["engine"]["status"] == "ready"
  assert "market-data" not in components

  current_engine_state = replace(stream_state, sequence=4)
  components = await runtime_status._component_heartbeats()
  assert components["engine"]["status"] == "degraded"
  assert components["engine"]["reasonCode"] == "ENGINE_MARKET_NOT_READY"
  current_engine_state = stream_state

  recent = datetime.now(timezone.utc)
  stream_state = replace(
    stream_state,
    sequence=8,
    captured_at=recent,
    updated_at=recent,
  )
  current_freshness = MarketStreamFreshnessLease(
    stream_id=stream_state.stream_id,
    sequence=stream_state.sequence,
  )
  current_engine_state = replace(
    stream_state,
    sequence=6,
    updated_at=recent - timedelta(milliseconds=200),
  )
  components = await runtime_status._component_heartbeats()
  consumption = components["engine"]["marketConsumption"]
  assert components["engine"]["status"] == "ready"
  assert consumption["status"] == "ready"
  assert consumption["readinessStatus"] == "passed"
  assert consumption["readinessMessage"] == ""
  assert consumption["sequence"] == 8
  assert consumption["engineSequence"] == 6

  current_engine_state = replace(stream_state, updated_at=recent)
  stream_state = replace(
    stream_state,
    commit_phase="APPLYING",
    pending_sequence=9,
    updated_at=recent,
  )
  components = await runtime_status._component_heartbeats()
  consumption = components["engine"]["marketConsumption"]
  assert components["engine"]["status"] == "ready"
  assert consumption["status"] == "ready"
  assert consumption["readinessStatus"] == "passed"
  assert consumption["commitPhase"] == "APPLYING"

  stream_state = replace(stream_state, commit_phase="IDLE", pending_sequence=0)
  current_engine_state = stream_state

  async with session_factory() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "qmt-agent:device-1")
    heartbeat.status = "READY"
    await db.commit()

  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"]["status"] == "ready"
  assert components["qmt-agent"]["connectedDevices"] == 1
  assert components["qmt-agent"]["readyDevices"] == 1
  assert components["qmt-agent"]["reconcilingDevices"] == 0
  assert components["qmt-agent"]["reasonCode"] == ""
  assert components["qmt-agent"]["latestReadyHeartbeatAt"] == (
    now.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
  )
  assert components["engine"]["marketConsumption"]["status"] == "ready"

  async with session_factory() as db:
    api_heartbeat = await db.get(RuntimeComponentHeartbeat, "api")
    api_heartbeat.instance_id = "api-instance-2"
    api_heartbeat.updated_at = now + timedelta(seconds=1)
    await db.commit()

  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"]["status"] == "ready"
  assert components["qmt-agent"]["connectedDevices"] == 1
  assert components["qmt-agent"]["readyDevices"] == 1
  assert components["qmt-agent"]["latestReadyHeartbeatAt"] is not None

  current_heartbeat_at = now + timedelta(seconds=2)
  control_identity.update(
    api_instance_id="api-instance-2",
    agent_session_id="agent-session-2",
  )
  async with session_factory() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "qmt-agent:device-1")
    heartbeat.updated_at = current_heartbeat_at
    heartbeat.details = {
      **dict(heartbeat.details or {}),
      "apiInstanceId": "api-instance-2",
      "agentSessionId": "agent-session-2",
      "serverReceivedAt": current_heartbeat_at.isoformat(),
      "agentSentAt": current_heartbeat_at.isoformat(),
      "sessionActive": True,
    }
    device = await db.get(AgentDevice, "device-1")
    device.last_seen_at = current_heartbeat_at
    await db.commit()

  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"]["status"] == "ready"
  assert components["qmt-agent"]["readyDevices"] == 1
  assert components["qmt-agent"]["latestReadyHeartbeatAt"] == (
    current_heartbeat_at.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
  )

  async with session_factory() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "qmt-agent:device-1")
    heartbeat.status = "TRADING_UNAVAILABLE"
    heartbeat.details = {
      **dict(heartbeat.details or {}),
      "xttradingStatus": "DISCONNECTED",
      "xttradingReason": "XTTRADING_UNAVAILABLE",
    }
    await db.commit()

  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"]["status"] == "degraded"
  assert components["qmt-agent"]["reasonCode"] == "XTTRADING_UNAVAILABLE"
  assert components["engine"]["marketConsumption"]["status"] == "ready"

  async with session_factory() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "qmt-agent:device-1")
    heartbeat.status = "READY"
    await db.commit()

  current_freshness = None
  components = await runtime_status._component_heartbeats()
  assert components["engine"]["marketConsumption"]["status"] == "stale"
  assert components["engine"]["marketConsumption"]["readinessStatus"] == "failed"

  async def outside_session(*_args):
    return False

  monkeypatch.setattr(
    runtime_status.TradingTimeService,
    "is_trading_hours",
    outside_session,
  )
  components = await runtime_status._component_heartbeats()
  assert components["engine"]["marketConsumption"]["status"] == "ready"
  assert components["engine"]["marketConsumption"]["readinessStatus"] == "standby"
  assert "休市" in components["engine"]["marketConsumption"]["readinessMessage"]

  async with session_factory() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "qmt-agent:device-1")
    heartbeat.status = "XTDATA_UNAVAILABLE"
    await db.commit()

  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"]["status"] == "degraded"
  assert components["qmt-agent"]["reasonCode"] == "XTDATA_UNAVAILABLE"
  assert components["qmt-agent"]["degradedDevices"] == 1
  assert components["engine"]["marketConsumption"]["status"] == "offline"

  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "BLOCKED")
  monkeypatch.setenv("QMT_AGENT_LAUNCH_REASON", "QMT_RUNTIME_UNAVAILABLE")
  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"]["status"] == "blocked"
  assert components["qmt-agent"]["connectedDevices"] == 1
  assert components["qmt-agent"]["readyDevices"] == 0
  assert components["qmt-agent"]["reasonCode"] == "QMT_RUNTIME_UNAVAILABLE"
  await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "server_status",
  ["RECONCILE_REQUIRED", QMT_ACCOUNT_MISMATCH],
)
async def test_ready_heartbeat_cannot_clear_engine_reconciliation_requirement(
  monkeypatch: pytest.MonkeyPatch,
  server_status: str,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          AgentDevice.__table__,
          RuntimeComponentHeartbeat.__table__,
        ],
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(agent_api, "AsyncSessionLocal", session_factory)
  now = runtime_status.utcnow()

  async with session_factory() as db:
    db.add(
      AgentDevice(
        id="device-1",
        user_id="user-1",
        name="live",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["live", "market-data"],
        last_seen_at=now,
      )
    )
    db.add(
      RuntimeComponentHeartbeat(
        component="qmt-agent:device-1",
        instance_id="instance-1",
        status=server_status,
        details={
          "apiInstanceId": "api-instance-1",
          "agentSessionId": "agent-session-1",
          "serverConnectedAt": now.isoformat(),
          "serverReceivedAt": now.isoformat(),
          "sessionActive": True,
          "reasonCode": server_status,
        },
        updated_at=now,
      )
    )
    await db.commit()

  control_session = agent_api.AgentControlSession(
    device_id="device-1",
    capabilities={"live", "market-data"},
    authorized_account_ids=frozenset({"account-1"}),
    queue=asyncio.Queue(),
    api_instance_id="api-instance-1",
    agent_session_id="agent-session-1",
    server_connected_at=now,
    remote_address_summary="10.0.0.*",
    revoked=asyncio.Event(),
  )
  stale_session = agent_api.AgentControlSession(
    device_id="device-1",
    capabilities={"live", "market-data"},
    authorized_account_ids=frozenset({"account-1"}),
    queue=asyncio.Queue(),
    api_instance_id="api-instance-1",
    agent_session_id="agent-session-old",
    server_connected_at=now - timedelta(seconds=1),
    remote_address_summary="10.0.0.*",
    revoked=asyncio.Event(),
  )

  async def stale_is_still_in_hub(_device_id):
    return stale_session

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "current_session",
    stale_is_still_in_hub,
  )
  with pytest.raises(agent_api.AuthError, match="更新连接替换"):
    await agent_api._record_heartbeat(
      stale_session,
      {"status": "READY", "capabilities": ["live"]},
      sent_at=now,
      establish=True,
    )
  with pytest.raises(agent_api.AuthError, match="更新连接替换"):
    await agent_api._record_heartbeat(
      stale_session,
      {"status": "READY", "capabilities": ["live"]},
      sent_at=now,
    )

  async def control_is_current(_device_id):
    return control_session

  monkeypatch.setattr(
    agent_api.agent_connection_hub,
    "current_session",
    control_is_current,
  )
  await agent_api._record_heartbeat(
    control_session,
    {
      "status": "READY",
      "capabilities": ["paper"],
      "agent_version": "test",
      "protocol_version": PROTOCOL_VERSION,
    },
    sent_at=now,
  )

  async with session_factory() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "qmt-agent:device-1")
    device = await db.get(AgentDevice, "device-1")
    assert heartbeat.status == server_status
    assert heartbeat.details["protocolVersion"] == PROTOCOL_VERSION
    assert heartbeat.details["reasonCode"] == server_status
    assert heartbeat.details["capabilities"] == ["live", "market-data"]
    assert device.capabilities == ["live", "market-data"]

  await agent_api._mark_session_offline(control_session)
  async with session_factory() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "qmt-agent:device-1")
    assert heartbeat.status == "OFFLINE"
    assert heartbeat.details["sessionActive"] is False
    assert heartbeat.details["reasonCode"] == "QMT_AGENT_OFFLINE"
  await engine.dispose()


@pytest.mark.asyncio
async def test_stale_engine_heartbeat_is_not_ready(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          AgentDevice.__table__,
          RuntimeComponentHeartbeat.__table__,
        ],
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(runtime_status, "AsyncSessionLocal", session_factory)
  async with session_factory() as db:
    db.add(
      RuntimeComponentHeartbeat(
        component="engine",
        instance_id="engine-1",
        status="ready",
        details={},
        updated_at=runtime_status.utcnow()
        - runtime_status.HEARTBEAT_TTL
        - timedelta(seconds=1),
      )
    )
    await db.commit()

  components = await runtime_status._component_heartbeats()
  assert components["engine"]["status"] == "stale"
  await engine.dispose()
