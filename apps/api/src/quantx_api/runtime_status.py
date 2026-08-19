"""Read-only health aggregation for independently supervised components."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.core.data.market_stream_transport import (
  market_stream_store,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  RuntimeComponentHeartbeat,
)
from quantx_infrastructure.services.trading_time_service import TradingTimeService
from sqlalchemy import select, text

from quantx_api.auth.tokens import utcnow

HEARTBEAT_TTL = timedelta(seconds=90)
RECONCILING_AGENT_STATUSES = frozenset(
  {"RECONCILING", "RECONCILE_REQUIRED"}
)
CONNECTED_AGENT_STATUSES = frozenset(
  {
    "READY",
    *RECONCILING_AGENT_STATUSES,
    "TRADING_UNAVAILABLE",
    "XTDATA_UNAVAILABLE",
    "EMERGENCY_STOP",
  }
)


def _snapshot_age_seconds(value: Any, now: datetime) -> float | None:
  if not value:
    return None
  try:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
      parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return max(0.0, (now - parsed).total_seconds())
  except (TypeError, ValueError):
    return None


def _aware_state_age(value: datetime | None, now: datetime) -> float | None:
  if value is None:
    return None
  normalized = value.astimezone(timezone.utc).replace(tzinfo=None)
  return max(0.0, (now - normalized).total_seconds())


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
  connected_agents: list[tuple[AgentDevice, str]] = []
  for agent in online_agents:
    heartbeat = heartbeat_by_component.get(f"qmt-agent:{agent.id}")
    if heartbeat is None:
      continue
    heartbeat_age = max(0.0, (now - heartbeat.updated_at).total_seconds())
    heartbeat_status = str(heartbeat.status or "").upper()
    if (
      heartbeat_age <= HEARTBEAT_TTL.total_seconds()
      and heartbeat_status in CONNECTED_AGENT_STATUSES
    ):
      connected_agents.append((agent, heartbeat_status))
  ready_agents = [
    agent for agent, status in connected_agents if status == "READY"
  ]
  agent_modes: set[str] = set()
  protocol_versions: set[str] = set()
  account_ids: set[str] = set()
  snapshot_ages: list[float] = []
  for agent, _ in connected_agents:
    heartbeat = heartbeat_by_component.get(f"qmt-agent:{agent.id}")
    details = dict(heartbeat.details or {}) if heartbeat else {}
    capabilities = {
      str(value).lower()
      for value in [
        *list(agent.capabilities or []),
        *list(details.get("capabilities") or []),
      ]
    }
    agent_modes.update(
      value for value in ("live", "paper", "data-only") if value in capabilities
    )
    protocol_version = str(details.get("protocolVersion") or "").strip()
    if protocol_version:
      protocol_versions.add(protocol_version)
    account_ids.update(
      str(value) for value in list(agent.authorized_account_ids or [])
    )
    direct_age = _snapshot_age_seconds(details.get("snapshotAt"), now)
    if direct_age is not None:
      snapshot_ages.append(direct_age)
    for summary in dict(details.get("accountReconciliation") or {}).values():
      age = _snapshot_age_seconds(dict(summary or {}).get("snapshotAt"), now)
      if age is not None:
        snapshot_ages.append(age)
  reconciling_agents = [
    agent
    for agent, status in connected_agents
    if status in RECONCILING_AGENT_STATUSES
  ]
  components["qmt-agent"] = {
    "status": "ready" if connected_agents else "offline",
    "connectedDevices": len(connected_agents),
    "readyDevices": len(ready_agents),
    "onlineDevices": len(online_agents),
    "reconcilingDevices": len(reconciling_agents),
    "degradedDevices": len(connected_agents)
    - len(ready_agents)
    - len(reconciling_agents),
    "registeredDevices": len(agents),
    "modes": sorted(agent_modes),
    "protocolVersions": sorted(protocol_versions),
    "accountIds": sorted(account_ids),
    "latestSnapshotAgeSeconds": (
      round(min(snapshot_ages), 3) if snapshot_ages else None
    ),
  }
  market_data_agents = [
    agent
    for agent, status in connected_agents
    if "market-data" in list(agent.capabilities or [])
    and status != "XTDATA_UNAVAILABLE"
  ]
  market_stream_agents = []
  ready_market_stream_agents = []
  for agent in market_data_agents:
    heartbeat = heartbeat_by_component.get(f"qmt-agent:{agent.id}")
    details = dict(heartbeat.details or {}) if heartbeat else {}
    market_stream_status = str(
      details.get("marketStreamStatus") or "OFFLINE"
    ).upper()
    if market_stream_status != "OFFLINE":
      market_stream_agents.append(agent)
    if market_stream_status == "READY":
      ready_market_stream_agents.append(agent)
  try:
    stream_authority, engine_state = await asyncio.gather(
      market_stream_store.state_with_freshness(),
      market_stream_store.engine_state(),
    )
    stream_state, freshness_lease = stream_authority
    trading_session = await TradingTimeService().is_trading_hours(
      "SH",
      time_utils.now(),
    )
    stream_age = _aware_state_age(
      stream_state.updated_at if stream_state is not None else None,
      now,
    )
    engine_age = _aware_state_age(
      engine_state.updated_at if engine_state is not None else None,
      now,
    )
    watermarks_match = bool(
      stream_state is not None
      and engine_state is not None
      and stream_state.stream_id == engine_state.stream_id
      and stream_state.sequence == engine_state.sequence
    )
    fresh = bool(
      not trading_session
      or (
        stream_state is not None
        and freshness_lease is not None
        and freshness_lease.stream_id == stream_state.stream_id
        and freshness_lease.sequence == stream_state.sequence
      )
    )
    ready = bool(
      ready_market_stream_agents
      and stream_state is not None
      and stream_state.status == "READY"
      and engine_state is not None
      and engine_state.status == "READY"
      and watermarks_match
      and fresh
    )
    if ready:
      effective_status = "ready"
    elif not market_stream_agents:
      effective_status = "offline"
    elif stream_state is None or engine_state is None:
      effective_status = "syncing"
    elif stream_state.status != "READY":
      effective_status = str(stream_state.status).lower()
    elif engine_state.status != "READY" or not fresh:
      effective_status = "stale"
    elif not watermarks_match:
      effective_status = "syncing"
    else:
      effective_status = "syncing"
    components["market-data"] = {
      "status": effective_status,
      "connectedDevices": (
        len(market_stream_agents)
        if stream_state is not None and stream_state.status != "OFFLINE"
        else 0
      ),
      "protocol": "quantx.market.v1",
      "streamId": stream_state.stream_id if stream_state is not None else "",
      "sequence": stream_state.sequence if stream_state is not None else 0,
      "engineSequence": engine_state.sequence if engine_state is not None else 0,
      "instrumentCount": (
        engine_state.instrument_count if engine_state is not None else 0
      ),
      "streamAgeSeconds": round(stream_age, 3) if stream_age is not None else None,
      "engineAgeSeconds": round(engine_age, 3) if engine_age is not None else None,
      "tradingSession": trading_session,
    }
  except Exception as exc:
    components["market-data"] = {
      "status": "unavailable",
      "connectedDevices": 0,
      "protocol": "quantx.market.v1",
      "error": exc.__class__.__name__,
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
  raw_ai_runtime = heartbeats.get("ai-runtime", {"status": "offline"})
  ai_runtime = {
    "status": raw_ai_runtime.get("status", "offline"),
    "ageSeconds": raw_ai_runtime.get("ageSeconds"),
  }
  details = dict(raw_ai_runtime.get("details") or {})
  if details.get("configVersion") is not None:
    ai_runtime["configVersion"] = details.get("configVersion")
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
    "aiRuntime": ai_runtime,
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
