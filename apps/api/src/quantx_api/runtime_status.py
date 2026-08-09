"""Read-only health aggregation for independently supervised components."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import httpx
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  RuntimeComponentHeartbeat,
)
from sqlalchemy import select, text

from quantx_api.auth.tokens import utcnow

HEARTBEAT_TTL = timedelta(seconds=90)


async def _database_status() -> dict[str, Any]:
  try:
    async with AsyncSessionLocal() as db:
      version = (
        await db.execute(text("SELECT current_setting('server_version')"))
      ).scalar_one()
    return {"status": "ready", "version": str(version)}
  except Exception as exc:
    return {"status": "unavailable", "error": exc.__class__.__name__}


async def _component_heartbeats() -> dict[str, dict[str, Any]]:
  now = utcnow()
  try:
    async with AsyncSessionLocal() as db:
      result = await db.execute(select(RuntimeComponentHeartbeat))
      heartbeats = result.scalars().all()
      agent_result = await db.execute(
        select(AgentDevice).where(AgentDevice.revoked_at.is_(None))
      )
      agents = agent_result.scalars().all()
  except Exception as exc:
    unavailable = {
      "status": "unavailable",
      "error": exc.__class__.__name__,
    }
    return {
      "qmt-agent": {**unavailable, "connectedDevices": 0},
      "market-data": {**unavailable, "connectedDevices": 0},
    }

  components: dict[str, dict[str, Any]] = {}
  heartbeat_by_component = {
    heartbeat.component: heartbeat for heartbeat in heartbeats
  }
  for heartbeat in heartbeats:
    age = max(0.0, (now - heartbeat.updated_at).total_seconds())
    components[heartbeat.component] = {
      "status": (
        str(heartbeat.status or "").lower()
        if age <= HEARTBEAT_TTL.total_seconds()
        else "stale"
      ),
      "instanceId": heartbeat.instance_id,
      "ageSeconds": round(age, 3),
      "details": heartbeat.details or {},
    }

  online_agents = [
    agent
    for agent in agents
    if agent.last_seen_at is not None and now - agent.last_seen_at <= HEARTBEAT_TTL
  ]
  connected_agents = []
  for agent in online_agents:
    heartbeat = heartbeat_by_component.get(f"qmt-agent:{agent.id}")
    if heartbeat is None:
      continue
    heartbeat_age = max(0.0, (now - heartbeat.updated_at).total_seconds())
    if (
      heartbeat_age <= HEARTBEAT_TTL.total_seconds()
      and str(heartbeat.status or "").upper() == "READY"
    ):
      connected_agents.append(agent)
  components["qmt-agent"] = {
    "status": "ready" if connected_agents else "offline",
    "connectedDevices": len(connected_agents),
    "onlineDevices": len(online_agents),
    "reconcilingDevices": len(online_agents) - len(connected_agents),
    "registeredDevices": len(agents),
  }
  market_data_agents = [
    agent
    for agent in connected_agents
    if "market-data" in list(agent.capabilities or [])
  ]
  components["market-data"] = {
    "status": "ready" if market_data_agents else "offline",
    "connectedDevices": len(market_data_agents),
  }
  return components


async def _prefect_status() -> dict[str, Any]:
  if not settings.prefect_enabled:
    return {"status": "disabled"}
  api_url = settings.prefect_api_url.rstrip("/")
  if not api_url.endswith("/api"):
    api_url += "/api"
  health_url = f"{api_url}/health"
  worker_pool = settings.prefect_worker_pool.strip() or "quantx-pool"
  workers_url = (
    f"{api_url}/work_pools/{worker_pool}/workers/filter"
  )
  try:
    async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
      response = await client.get(health_url)
      workers_response = await client.post(
        workers_url,
        json={},
      )
    workers = workers_response.json() if workers_response.is_success else []
    online_workers = [
      worker
      for worker in workers
      if str(worker.get("status", "")).upper() == "ONLINE"
    ]
    return {
      "status": "ready" if response.is_success else "unavailable",
      "statusCode": response.status_code,
      "workersStatusCode": workers_response.status_code,
      "workerStatus": "ready" if online_workers else "offline",
      "onlineWorkers": len(online_workers),
      "registeredWorkers": len(workers),
      "offlineWorkers": len(workers) - len(online_workers),
      "workers": [
        {
          "name": worker.get("name"),
          "status": worker.get("status"),
          "lastHeartbeatTime": worker.get("last_heartbeat_time"),
        }
        for worker in online_workers
      ],
    }
  except Exception as exc:
    return {"status": "unavailable", "error": exc.__class__.__name__}


async def component_status() -> dict[str, dict[str, Any]]:
  database, heartbeats, prefect = await asyncio.gather(
    _database_status(),
    _component_heartbeats(),
    _prefect_status(),
  )
  return {
    "api": {"status": "ready"},
    "database": database,
    "engine": heartbeats.get("engine", {"status": "offline"}),
    "worker": {
      "status": prefect.get("workerStatus", "offline"),
      "onlineWorkers": prefect.get("onlineWorkers", 0),
      "registeredWorkers": prefect.get("registeredWorkers", 0),
      "offlineWorkers": prefect.get("offlineWorkers", 0),
      "workers": prefect.get("workers", []),
    },
    "qmtAgent": heartbeats["qmt-agent"],
    "marketData": heartbeats.get("market-data", {"status": "offline"}),
    "prefect": prefect,
  }


def required_components() -> tuple[str, ...]:
  profile = getattr(settings, "runtime_profile", "web").lower()
  if profile == "full":
    return (
      "api",
      "database",
      "engine",
      "prefect",
      "worker",
      "qmtAgent",
      "marketData",
    )
  return ("api", "database", "engine")


async def readiness_status() -> tuple[bool, dict[str, Any]]:
  components = await component_status()
  required = required_components()
  ready = all(components[name]["status"] == "ready" for name in required)
  return ready, {
    "status": "ready" if ready else "not_ready",
    "profile": getattr(settings, "runtime_profile", "web"),
    "requiredComponents": list(required),
    "components": components,
  }
