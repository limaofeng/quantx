from datetime import timedelta

import pytest
from quantx_api import runtime_status
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


@pytest.mark.asyncio
async def test_qmt_agent_is_ready_only_after_complete_reconciliation(
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
        status="RECONCILING",
        details={},
        updated_at=now,
      )
    )
    await db.commit()

  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"] == {
    "status": "offline",
    "connectedDevices": 0,
    "onlineDevices": 1,
    "reconcilingDevices": 1,
    "registeredDevices": 1,
  }
  assert components["market-data"]["status"] == "offline"

  async with session_factory() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "qmt-agent:device-1")
    heartbeat.status = "READY"
    await db.commit()

  components = await runtime_status._component_heartbeats()
  assert components["qmt-agent"]["status"] == "ready"
  assert components["qmt-agent"]["connectedDevices"] == 1
  assert components["market-data"]["status"] == "ready"
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
