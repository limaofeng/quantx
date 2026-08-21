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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


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
    "http://192.168.101.4:30420/api/work_pools/"
    "quantx-pool/workers/filter"
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
async def test_component_status_overrides_stale_ready_agent_when_launch_is_blocked(
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

  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "BLOCKED")
  monkeypatch.setenv("QMT_AGENT_LAUNCH_REASON", "QMT_ENROLLMENT_REQUIRED")
  monkeypatch.setattr(runtime_status.settings, "runtime_profile", "full")
  monkeypatch.setattr(runtime_status, "_database_status", fake_database_status)
  monkeypatch.setattr(
    runtime_status,
    "_component_heartbeats",
    fake_component_heartbeats,
  )
  monkeypatch.setattr(runtime_status, "_prefect_status", fake_prefect_status)

  components = await runtime_status.component_status()

  assert components["qmtAgent"] == {
    "status": "blocked",
    "launchState": "BLOCKED",
    "reasonCode": "QMT_ENROLLMENT_REQUIRED",
    "liveTradingEnabled": False,
    "connectedDevices": 0,
    "readyDevices": 0,
    "onlineDevices": 0,
    "reconcilingDevices": 0,
    "degradedDevices": 0,
    "registeredDevices": 1,
    "modes": [],
    "protocolVersions": [],
    "accountIds": [],
    "latestSnapshotAgeSeconds": None,
    "latestReadyHeartbeatAt": None,
  }
  assert components["marketData"] == {
    "status": "blocked",
    "launchState": "BLOCKED",
    "reasonCode": "QMT_ENROLLMENT_REQUIRED",
    "liveTradingEnabled": False,
    "connectedDevices": 0,
    "protocol": "quantx.market.v2",
  }

  ready, payload = await runtime_status.readiness_status()

  assert ready is False
  assert payload["status"] == "not_ready"
  assert payload["components"]["qmtAgent"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_qmt_agent_component_stays_ready_during_trade_reconciliation(
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
  now = runtime_status.utcnow()

  async with session_factory() as db:
    db.add(
      AgentDevice(
        id="device-1",
        user_id="user-1",
        name="paper",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["paper", "market-data"],
        last_seen_at=now,
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
        },
        updated_at=now,
      )
    )
    await db.commit()

  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"] == {
    "status": "ready",
    "connectedDevices": 1,
    "readyDevices": 0,
    "onlineDevices": 1,
    "reconcilingDevices": 1,
    "degradedDevices": 0,
    "registeredDevices": 1,
    "modes": ["paper"],
    "protocolVersions": ["1.1"],
    "accountIds": ["account-1"],
    "latestSnapshotAgeSeconds": None,
    "latestReadyHeartbeatAt": None,
  }
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
  assert components["qmt-agent"]["latestReadyHeartbeatAt"] == (
    now.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
  )
  assert components["market-data"]["status"] == "ready"

  launch_started_at = now + timedelta(seconds=1)
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "LAUNCH_ALLOWED")
  monkeypatch.setenv(
    "QMT_AGENT_LAUNCH_STARTED_AT",
    launch_started_at.replace(tzinfo=timezone.utc).isoformat(),
  )

  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"]["status"] == "offline"
  assert components["qmt-agent"]["connectedDevices"] == 0
  assert components["qmt-agent"]["readyDevices"] == 0
  assert components["qmt-agent"]["latestReadyHeartbeatAt"] is None

  current_heartbeat_at = launch_started_at + timedelta(seconds=1)
  async with session_factory() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "qmt-agent:device-1")
    heartbeat.updated_at = current_heartbeat_at
    device = await db.get(AgentDevice, "device-1")
    device.last_seen_at = current_heartbeat_at
    await db.commit()

  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"]["status"] == "ready"
  assert components["qmt-agent"]["readyDevices"] == 1
  assert components["qmt-agent"]["latestReadyHeartbeatAt"] == (
    current_heartbeat_at.replace(tzinfo=timezone.utc)
    .isoformat()
    .replace("+00:00", "Z")
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
  assert components["qmt-agent"]["status"] == "ready"
  assert components["qmt-agent"]["degradedDevices"] == 1
  assert components["market-data"]["status"] == "offline"
  await engine.dispose()


@pytest.mark.asyncio
async def test_ready_heartbeat_cannot_clear_engine_reconciliation_requirement(
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
        status="RECONCILE_REQUIRED",
        details={},
        updated_at=now,
      )
    )
    await db.commit()

  await agent_api._record_heartbeat(
    "device-1",
    {
      "status": "READY",
      "capabilities": ["live", "market-data"],
      "agent_version": "test",
      "protocol_version": "1.1",
    },
  )

  async with session_factory() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "qmt-agent:device-1")
    assert heartbeat.status == "RECONCILE_REQUIRED"
    assert heartbeat.details["protocolVersion"] == "1.1"
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
