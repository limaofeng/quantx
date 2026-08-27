import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from quantx_api import agent_api, runtime_status
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
  REMOTE_AGENT_ACCOUNT_MISMATCH,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


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
      return {"status": "ready", "dependencies": {"redis": "ready"}}

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

  assert calls["client_kwargs"] == {"timeout": 1.0, "trust_env": False}
  assert calls["url"] == "http://127.0.0.1:18082/health/ready"
  assert status == {
    "status": "ready",
    "statusCode": 200,
    "dependencies": {"redis": "ready"},
  }


@pytest.mark.asyncio
async def test_market_gateway_status_rejects_non_ready_payload(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class FakeResponse:
    status_code = 503
    is_success = False

    @staticmethod
    def json():
      return {
        "status": "not_ready",
        "dependencies": {"redis": "unavailable"},
      }

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
    "status": "unavailable",
    "statusCode": 503,
    "dependencies": {"redis": "unavailable"},
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
    "http://192.168.101.4:30420",
  )
  monkeypatch.setattr(
    runtime_status.settings,
    "prefect_worker_pool",
    "quantx-pool",
  )
  monkeypatch.setattr(runtime_status.httpx, "AsyncClient", FakeClient)

  status = await runtime_status._prefect_status()

  assert calls["client_kwargs"] == {"timeout": 5.0, "trust_env": False}
  assert calls["health_url"] == "http://192.168.101.4:30420/api/health"
  assert calls["workers_url"] == (
    "http://192.168.101.4:30420/api/work_pools/quantx-pool/workers/filter"
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
async def test_component_status_does_not_consume_local_launch_environment(
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

  components = await runtime_status.component_status()

  assert components["qmtAgent"]["status"] == "ready"
  assert components["qmtAgent"]["connectedDevices"] == 1
  assert components["marketData"]["status"] == "ready"


@pytest.mark.asyncio
async def test_market_data_runtime_status_uses_only_heartbeat_snapshot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  snapshot = {
    "market-data": {
      "status": "ready",
      "sequence": 42,
      "engineSequence": 42,
      "engineAgeSeconds": 0.125,
    }
  }

  async def fake_component_heartbeats():
    return snapshot

  monkeypatch.setattr(
    runtime_status,
    "_component_heartbeats",
    fake_component_heartbeats,
  )
  assert await runtime_status.market_data_runtime_status() == snapshot["market-data"]


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

  async def state_with_freshness():
    return stream_state, MarketStreamFreshnessLease(
      stream_id=stream_state.stream_id,
      sequence=stream_state.sequence,
    )

  async def engine_state():
    return stream_state

  async def inside_session(*_args):
    return True

  async def is_connected(*_args, **_kwargs):
    return True

  monkeypatch.setattr(
    runtime_status.market_stream_store,
    "state_with_freshness",
    state_with_freshness,
  )
  monkeypatch.setattr(
    runtime_status.market_stream_store,
    "engine_state",
    engine_state,
  )
  monkeypatch.setattr(
    runtime_status.TradingTimeService,
    "is_trading_hours",
    inside_session,
  )
  monkeypatch.setattr(
    runtime_status.agent_connection_hub,
    "is_connected",
    is_connected,
  )
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
          "protocolVersion": "1.1",
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
    "protocolVersions": ["1.1"],
    "accountIds": ["***nt-1"],
    "latestSnapshotAgeSeconds": None,
    "latestReadyHeartbeatAt": None,
    "apiInstanceId": "api-instance-1",
    "remoteAddressSummaries": ["10.0.0.*"],
    "reasonCode": "REMOTE_AGENT_NOT_RECONCILED",
  }
  assert "qmt-agent:device-1" not in components
  assert components["market-data"]["status"] == "ready"
  assert components["market-data"]["streamAgeSeconds"] >= 25
  assert components["market-data"]["engineAgeSeconds"] >= 25

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
  assert components["market-data"]["status"] == "ready"

  async with session_factory() as db:
    api_heartbeat = await db.get(RuntimeComponentHeartbeat, "api")
    api_heartbeat.instance_id = "api-instance-2"
    api_heartbeat.updated_at = now + timedelta(seconds=1)
    await db.commit()

  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"]["status"] == "offline"
  assert components["qmt-agent"]["connectedDevices"] == 0
  assert components["qmt-agent"]["readyDevices"] == 0
  assert components["qmt-agent"]["latestReadyHeartbeatAt"] is None

  current_heartbeat_at = now + timedelta(seconds=2)
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

  async def state_without_freshness():
    return stream_state, None

  monkeypatch.setattr(
    runtime_status.market_stream_store,
    "state_with_freshness",
    state_without_freshness,
  )
  components = await runtime_status._component_heartbeats()
  assert components["market-data"]["status"] == "stale"

  async with session_factory() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "qmt-agent:device-1")
    heartbeat.status = "XTDATA_UNAVAILABLE"
    await db.commit()

  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"]["status"] == "degraded"
  assert components["qmt-agent"]["degradedDevices"] == 1
  assert components["market-data"]["status"] == "offline"
  await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "server_status",
  ["RECONCILE_REQUIRED", REMOTE_AGENT_ACCOUNT_MISMATCH],
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
  with pytest.raises(agent_api.AuthError, match="已被替换"):
    await agent_api._record_heartbeat(
      stale_session,
      {"status": "READY", "capabilities": ["live"]},
      sent_at=now,
    )
  await agent_api._record_heartbeat(
    control_session,
    {
      "status": "READY",
      "capabilities": ["paper"],
      "agent_version": "test",
      "protocol_version": "1.1",
    },
    sent_at=now,
  )

  async with session_factory() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "qmt-agent:device-1")
    device = await db.get(AgentDevice, "device-1")
    assert heartbeat.status == server_status
    assert heartbeat.details["protocolVersion"] == "1.1"
    assert heartbeat.details["reasonCode"] == server_status
    assert heartbeat.details["capabilities"] == ["live", "market-data"]
    assert device.capabilities == ["live", "market-data"]

  await agent_api._mark_session_offline(control_session)
  async with session_factory() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "qmt-agent:device-1")
    assert heartbeat.status == "OFFLINE"
    assert heartbeat.details["sessionActive"] is False
    assert heartbeat.details["reasonCode"] == "REMOTE_AGENT_OFFLINE"
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
